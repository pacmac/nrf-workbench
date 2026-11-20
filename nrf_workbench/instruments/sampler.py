"""Watch and record sessions on an instrument (docs/specs/meter-page.md, docs/specs/ppk2.md).

One session at a time, whichever instrument it reads: a thread takes a reading, publishes it to the bounded `meter` stream (the browser listens, nothing polls) and, for a
recording, appends it to a CSV file. The multimeter's reading is its own short connection under its one-caller lock, so other meter commands can run between two readings;
the power profiler holds its port for the whole session. A fault ends the session, never papers over it.
"""
import json
import os
import re
import shutil
import threading
import time

from . import capture
from . import meter as meterlib
from . import scpi
from .envelope import Envelope, Stats
from .source import Fault, Reading

MIN_BOUND_S, MAX_BOUND_S = 1, 43200          # 12 h: about 216,000 readings and 13 MB of CSV at NPLC 10 (docs/specs/meter-page.md)
KEEP_RECORDINGS = 10                         # newest files kept: 10 x 13 MB = 130 MB at most
SETTING_EVERY = 10                           # re-ask function, range and NPLC every 10 readings: about 2 s at NPLC 10, not 4 extra queries on every reading
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# The first eight columns are the multimeter's; `min`, `max` and `n` were added for the power profiler's blocks (docs/specs/ppk2.md) and come last, so an older file still reads.
HEADER = "t_s,wall,value,unit,function,range,nplc,overload,min,max,n\n"


class Busy(Exception):
    def __init__(self, session):
        Exception.__init__(self, "a %s session is already running" % session["mode"])
        self.session = session


class NameTaken(Exception):
    pass


class Missing(Exception):
    """The instrument a session was asked to read is not configured on this bench."""


class RecordingUnknown(Exception):
    """No recording has that name."""


class Session(object):
    def __init__(self, number, mode, bound_s, name, wall, mono, source):
        self.number, self.mode, self.bound_s, self.name = number, mode, bound_s, name
        self.source = source
        self.started, self.t0 = wall, mono
        self.state, self.reason = mode_state(mode), ""
        self.stats, self.envelope = Stats(), Envelope()
        self.function = self.unit = self.range = self.nplc = None
        self.facts = {}                                      # what the source knows about the session so far (the profiler's lost and filtered samples)
        self.stop = threading.Event()
        self.thread = None
        self.file = None
        self.ended = None
        self.wakes_dir = None                                # where a recording keeps its wakes (the profiler's source writes them there)


def mode_state(mode):
    return "recording" if mode == "record" else "watching"


class MeterSource(object):
    """The multimeter as a source: one READ? per reading, each on its own connection; the setting is re-asked every SETTING_EVERY readings."""
    name = "meter"

    def __init__(self, meter):
        self.meter = meter
        self._setting = None

    def begin(self, s):
        self._setting = None

    def end(self):
        pass

    def ceiling_s(self, s):
        """How long to wait for the session's thread after a stop: one reading's give-up time and a second."""
        return meterlib.reading_ceiling_s(s.nplc) + 1.0

    def _read(self, conn, k):
        if self._setting is None or k % SETTING_EVERY == 0:
            self._setting = meterlib.Setting(conn)
        raw = conn.ask("READ?", ceiling=meterlib.reading_ceiling_s(self._setting.nplc))
        return self._setting, raw

    def take(self, s, k):
        try:
            setting, raw = self.meter.exchange(lambda conn: self._read(conn, k))
        except scpi.ScpiError as exc:
            raise Fault(str(exc))
        value = meterlib._number(raw)
        if value is None:
            raise Fault("the meter answered %r, which is not a number" % raw)
        return Reading(None, value, setting.function, setting.unit, setting.range, setting.nplc, overload=abs(value) >= meterlib.OVERLOAD)


class Sampler(object):
    def __init__(self, meter, bus, folder=None, wall=time.time, mono=time.monotonic, ppk2=None):
        """`meter` and `ppk2` are the instruments this bench has (None for one it has not): each becomes a source a session can read."""
        self.sources = {}
        if meter is not None:
            self.sources["meter"] = MeterSource(meter)
        if ppk2 is not None:
            self.sources["ppk2"] = ppk2.source()
        self.bus, self.folder = bus, folder
        self._wall, self._mono = wall, mono
        self._lock = threading.Lock()
        self._session = None
        self._count = 0

    # -- control --------------------------------------------------------------------------------------
    def _source(self, name):
        if name is None:
            name = "meter" if "meter" in self.sources else next(iter(sorted(self.sources)), None)
        if name not in self.sources:
            raise Missing("this bench has no %s configured" % (name or "instrument"))
        return self.sources[name]

    def start(self, mode, bound_s, name=None, source=None):
        """The session's summary dict. Raises Busy, NameTaken, Missing, or ValueError for a bad bound or name."""
        if mode not in ("watch", "record"):
            raise ValueError("mode is watch or record")
        if isinstance(bound_s, bool) or not isinstance(bound_s, (int, float)) or not MIN_BOUND_S <= bound_s <= MAX_BOUND_S:
            raise ValueError("bound_s is %d to %d seconds" % (MIN_BOUND_S, MAX_BOUND_S))
        with self._lock:
            if self._session is not None and self._session.thread is not None and self._session.thread.is_alive():
                raise Busy(self._summary(self._session))
            src = self._source(source)
            self._count += 1
            wall = self._wall()
            if mode == "record":
                if not self.folder:
                    raise ValueError("there is no recordings folder (paths.meter)")
                name = name or time.strftime("%Y%m%d-%H%M%S", time.localtime(wall))
                if not NAME.match(name):
                    raise ValueError("a recording name is 1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit")
                os.makedirs(self.folder, exist_ok=True)
                path = os.path.join(self.folder, name + ".csv")
                if os.path.exists(path):
                    raise NameTaken("a recording named %s already exists" % name)
                self._prune()
            s = Session(self._count, mode, float(bound_s), name if mode == "record" else None, wall, self._mono(), src)
            if mode == "record":
                s.wakes_dir = os.path.join(self.folder, name + ".wakes")
                s.file = open(os.path.join(self.folder, name + ".csv"), "w")
                s.file.write(HEADER)
                s.file.flush()
                self._sidecar(s, ended=None)
            self._session = s
            self._publish_state(s)
            s.thread = threading.Thread(target=self._run, args=(s,), name="meter-session-%d" % s.number, daemon=True)
            s.thread.start()
            return self._summary(s)

    def stop(self):
        """End the session now and wait for its thread (at most the source's give-up time). Already stopped is fine."""
        with self._lock:
            s = self._session
        if s is None or s.thread is None:
            return
        s.stop.set()
        s.thread.join(s.source.ceiling_s(s))

    def _prune(self):
        """Keep the newest KEEP_RECORDINGS - 1 recordings so that one more fits."""
        files = sorted((f for f in os.listdir(self.folder) if f.endswith(".csv")), key=lambda f: os.path.getmtime(os.path.join(self.folder, f)))
        for old in files[:max(0, len(files) - (KEEP_RECORDINGS - 1))]:
            for suffix in (".csv", ".json", ".stages.json"):
                try:
                    os.unlink(os.path.join(self.folder, old[:-4] + suffix))
                except OSError:
                    pass
            shutil.rmtree(os.path.join(self.folder, old[:-4] + ".wakes"), ignore_errors=True)

    # -- the thread -----------------------------------------------------------------------------------
    def _run(self, s):
        k, reason, state = 0, "", "idle"
        source = s.source
        try:
            try:
                source.begin(s)
                while not s.stop.is_set():
                    if self._mono() - s.t0 >= s.bound_s:
                        reason = "the time bound was reached"
                        break
                    r = source.take(s, k)
                    if r.t is None:
                        r.t = self._mono() - s.t0
                    if s.function is None:
                        s.function, s.unit = r.function, r.unit
                    elif r.function != s.function:
                        raise Fault("the meter's function changed from %s to %s during the session" % (s.function, r.function))
                    s.range, s.nplc = r.range, r.nplc
                    self._take(s, r)
                    k += 1
                if not reason:
                    reason = "stopped"                         # the loop ended because stop() was called
            except Fault as exc:
                reason, state = str(exc), "error"
        finally:
            try:
                source.end()
            except Exception as exc:                           # closing an instrument must not stop the session's own books being closed
                if state != "error":
                    reason, state = "closing the instrument failed: %s" % exc, "error"
            with self._lock:
                s.state, s.reason, s.ended = state, reason, self._wall()
                if s.file is not None:
                    try:
                        s.file.close()
                    except OSError:
                        pass
                    self._sidecar(s, ended=s.ended)
                self._publish_state(s)

    def _take(self, s, r):
        wall = self._wall()
        with self._lock:                                   # the stats, the envelope and the feed move together: a snapshot is one moment
            if r.overload:
                s.stats.overload()
            else:
                s.stats.add(r.t, r.value, r.lo, r.hi, r.n, r.dt)
                s.envelope.add(r.t, r.value, r.lo, r.hi, r.n)
            s.facts.update(r.facts)
            if s.file is not None:
                s.file.write("%.3f,%.3f,%s,%s,%s,%s,%s,%d,%s,%s,%d\n" % (
                    r.t, wall, "" if r.overload else repr(r.value), r.unit, r.function,
                    "" if r.range is None else r.range, "" if r.nplc is None else r.nplc, 1 if r.overload else 0,
                    "" if r.overload else repr(r.lo), "" if r.overload else repr(r.hi), 0 if r.overload else r.n))
                s.file.flush()
            self.bus.publish("meter.reading", "meter", {
                "session": s.number, "source": s.source.name, "t_s": round(r.t, 3), "wall": wall, "value": None if r.overload else r.value,
                "min": None if r.overload else r.lo, "max": None if r.overload else r.hi, "n": 0 if r.overload else r.n, "dt": r.dt, "overload": r.overload,
                "unit": r.unit, "function": r.function, "range": r.range, "nplc": r.nplc,
                "facts": dict(r.facts)})                 # what only the source knows so far (lost, filtered, rate_low): the page shows it while the session runs

    # -- reading state --------------------------------------------------------------------------------
    def _current(self, s):
        return (s.function or "").upper() in meterlib.CURRENT

    def _summary(self, s):
        elapsed = (s.ended - s.started) if s.ended else self._mono() - s.t0
        return dict({"session": s.number, "source": s.source.name, "state": s.state, "mode": s.mode, "name": s.name, "started": s.started,
                     "elapsed_s": round(elapsed, 3), "bound_s": s.bound_s, "reason": s.reason, "function": s.function, "unit": s.unit,
                     "stats": s.stats.to_dict(self._current(s))}, **s.facts)

    def _publish_state(self, s):
        self.bus.publish("meter.state", "meter", self._summary(s))

    def _sidecar(self, s, ended):
        """The facts about a recording the CSV does not carry, written when it starts and again when it ends: so a recording a killed service left is known to have been cut short."""
        try:
            with open(os.path.join(self.folder, s.name + ".json"), "w") as fh:
                json.dump(dict({"name": s.name, "source": s.source.name, "started": s.started, "ended": ended, "reason": s.reason, "bound_s": s.bound_s,
                                "function": s.function, "unit": s.unit, "stats": s.stats.to_dict(self._current(s))}, **s.facts), fh)
        except OSError:
            pass

    def session(self):
        with self._lock:
            return self._summary(self._session) if self._session is not None else {
                "session": 0, "source": None, "state": "idle", "mode": None, "name": None, "started": None, "elapsed_s": 0, "bound_s": None, "reason": "",
                "function": None, "unit": None, "stats": Stats().to_dict()}

    def profile(self):
        """The session so far as an envelope, and the feed cursor taken at the same instant: events after the cursor continue it with no gap and no repeat."""
        with self._lock:
            s = self._session
            return {"session": s.number if s else 0, "source": s.source.name if s else None, "cursor": self.bus.latest, "epoch": self.bus.epoch,
                    "unit": s.unit if s else None, "function": s.function if s else None,
                    "buckets": s.envelope.snapshot() if s else [], "stats": self._summary(s)["stats"] if s else Stats().to_dict(),
                    "state": s.state if s else "idle", "facts": dict(s.facts) if s else {},
                    "last": list(s.stats._last) if s and s.stats._last else None}      # [t_s, value]: where a page carries the charge on from

    # -- the wakes kept at full rate, and their stages (docs/specs/ppk2-stages.md) ----------------------
    def _wakes(self, recording=None):
        """The WakeSet to read: a finished (or running) recording's wakes from disk when `recording` is named, else the last session's from memory if its source keeps wakes (the
        profiler's does; the multimeter's does not). None when there are none. RecordingUnknown for a name that is not a recording."""
        if recording is None:
            s = self._session
            return getattr(s.source, "wakes", None) if s is not None else None
        self._recording_path(recording)
        from . import ppk2                                   # here, not at the top: ppk2 is only for a bench that has one
        return ppk2.stored_wakes(os.path.join(self.folder, recording + ".wakes"))

    def _recording_path(self, recording):
        if not NAME.match(recording or "") or not self.folder or not os.path.exists(os.path.join(self.folder, recording + ".csv")):
            raise RecordingUnknown("there is no recording named %s" % recording)
        return os.path.join(self.folder, recording + ".csv")

    def captures(self, recording=None):
        s, ws = self._session, self._wakes(recording)
        return {"session": s.number if s else 0, "source": s.source.name if s else None, "recording": recording, "captures": ws.rows() if ws is not None else []}

    def capture(self, n, t0=None, t1=None, points=None, recording=None):
        """One wake's exact envelope, or None when no wake n is held. ValueError for a bad window or points."""
        ws = self._wakes(recording)
        return ws.envelope(n, t0, t1, points if points is not None else capture.POINTS_DEFAULT) if ws is not None else None

    def held(self, recording=None):
        ws = self._wakes(recording)
        return ws.numbers() if ws is not None else []

    def _stages_path(self, recording):
        self._recording_path(recording)
        return os.path.join(self.folder, recording + ".stages.json")

    def stage_list(self, recording):
        return capture.load_stages(self._stages_path(recording))

    def stage_set(self, recording, name, start_s, end_s):
        """The recording's stages with this one added or changed (capture.StageError for a rule broken)."""
        path = self._stages_path(recording)
        stages = capture.set_stage(capture.load_stages(path), name, start_s, end_s)
        capture.save_stages(path, stages)
        return stages

    def stage_delete(self, recording, name):
        """The stages left, or None if there was no stage of that name."""
        path = self._stages_path(recording)
        stages = capture.remove_stage(capture.load_stages(path), name)
        if stages is not None:
            capture.save_stages(path, stages)
        return stages

    def stages(self, recording, volts=None):
        """The stage table over the recording's saved wakes."""
        path = self._stages_path(recording)
        stages = capture.load_stages(path)
        ws = self._wakes(recording)
        if ws is None:
            return {"recording": recording, "wakes": 0, "stages": [dict(s, length_s=s["end_s"] - s["start_s"], wakes=0, mean=None, lowest_mean=None, highest_mean=None, peak=None,
                                                                         charge_c=None, share=None, energy_j=None) for s in stages],
                    "tagged_charge_c": 0.0, "untagged_charge_c": None}
        return dict(ws.stage_table(stages, volts), recording=recording)

    def recordings(self):
        """The files in the folder, newest first, with what the sidecar says. A recording whose sidecar has no end was cut short."""
        if not self.folder or not os.path.isdir(self.folder):
            return []
        live = self._session.name if self._session is not None and self._session.state == "recording" else None
        rows = []
        for f in os.listdir(self.folder):
            if not f.endswith(".csv"):
                continue
            path = os.path.join(self.folder, f)
            meta = {}
            try:
                with open(path[:-4] + ".json") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                pass
            if f[:-4] == live:                                  # the sidecar of a running recording is as it was at the start: the session knows better
                s = self._session
                meta = dict(meta, started=s.started, function=s.function, unit=s.unit, stats=self._summary(s)["stats"], **s.facts)
            rows.append({"name": f[:-4], "source": meta.get("source", "meter"), "bytes": os.path.getsize(path),
                         "started": meta.get("started"), "ended": meta.get("ended"),
                         "reason": meta.get("reason", ""), "function": meta.get("function"), "unit": meta.get("unit"),
                         "stats": meta.get("stats"), "interrupted": meta.get("ended") is None and f[:-4] != live,
                         "recording": f[:-4] == live, "mtime": os.path.getmtime(path),
                         "lost": meta.get("lost"), "filtered": meta.get("filtered"), "rate_low": meta.get("rate_low")})
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return rows

    def recording_text(self, name):
        if not NAME.match(name or "") or not self.folder:
            return None
        path = os.path.join(self.folder, name + ".csv")
        try:
            with open(path) as fh:
                return fh.read()
        except OSError:
            return None
