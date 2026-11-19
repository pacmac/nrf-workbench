"""Watch and record sessions on the multimeter (docs/specs/meter-page.md).

One session at a time: a thread takes a reading, publishes it to the bounded `meter` stream (the browser listens, nothing polls) and, for a recording, appends it to a CSV file.
Each reading is its own short connection under the meter's one-caller lock, so other meter commands can run between two readings. A fault ends the session, never papers over it.
"""
import json
import os
import re
import threading
import time

from . import meter as meterlib
from . import scpi
from .envelope import Envelope, Stats

MIN_BOUND_S, MAX_BOUND_S = 1, 43200          # 12 h: about 216,000 readings and 13 MB of CSV at NPLC 10 (docs/specs/meter-page.md)
KEEP_RECORDINGS = 10                         # newest files kept: 10 x 13 MB = 130 MB at most
SETTING_EVERY = 10                           # re-ask function, range and NPLC every 10 readings: about 2 s at NPLC 10, not 4 extra queries on every reading
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
HEADER = "t_s,wall,value,unit,function,range,nplc,overload\n"


class Busy(Exception):
    def __init__(self, session):
        Exception.__init__(self, "a %s session is already running" % session["mode"])
        self.session = session


class NameTaken(Exception):
    pass


class Session(object):
    def __init__(self, number, mode, bound_s, name, wall, mono):
        self.number, self.mode, self.bound_s, self.name = number, mode, bound_s, name
        self.started, self.t0 = wall, mono
        self.state, self.reason = mode_state(mode), ""
        self.stats, self.envelope = Stats(), Envelope()
        self.function = self.unit = self.range = self.nplc = None
        self.stop = threading.Event()
        self.thread = None
        self.file = None
        self.ended = None


def mode_state(mode):
    return "recording" if mode == "record" else "watching"


class Sampler(object):
    def __init__(self, meter, bus, folder=None, wall=time.time, mono=time.monotonic):
        self.meter, self.bus, self.folder = meter, bus, folder
        self._wall, self._mono = wall, mono
        self._lock = threading.Lock()
        self._session = None
        self._count = 0

    # -- control --------------------------------------------------------------------------------------
    def start(self, mode, bound_s, name=None):
        """The session's summary dict. Raises Busy, NameTaken, or ValueError for a bad bound or name."""
        if mode not in ("watch", "record"):
            raise ValueError("mode is watch or record")
        if isinstance(bound_s, bool) or not isinstance(bound_s, (int, float)) or not MIN_BOUND_S <= bound_s <= MAX_BOUND_S:
            raise ValueError("bound_s is %d to %d seconds" % (MIN_BOUND_S, MAX_BOUND_S))
        with self._lock:
            if self._session is not None and self._session.thread is not None and self._session.thread.is_alive():
                raise Busy(self._summary(self._session))
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
            s = Session(self._count, mode, float(bound_s), name if mode == "record" else None, wall, self._mono())
            if mode == "record":
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
        """End the session now and wait for its thread (at most one reading's ceiling). Already stopped is fine."""
        with self._lock:
            s = self._session
        if s is None or s.thread is None:
            return
        s.stop.set()
        s.thread.join(meterlib.reading_ceiling_s(s.nplc) + 1.0)

    def _prune(self):
        """Keep the newest KEEP_RECORDINGS - 1 recordings so that one more fits."""
        files = sorted((f for f in os.listdir(self.folder) if f.endswith(".csv")), key=lambda f: os.path.getmtime(os.path.join(self.folder, f)))
        for old in files[:max(0, len(files) - (KEEP_RECORDINGS - 1))]:
            for suffix in (".csv", ".json"):
                try:
                    os.unlink(os.path.join(self.folder, old[:-4] + suffix))
                except OSError:
                    pass

    # -- the thread -----------------------------------------------------------------------------------
    def _read(self, conn, setting, k):
        if setting is None or k % SETTING_EVERY == 0:
            setting = meterlib.Setting(conn)
        raw = conn.ask("READ?", ceiling=meterlib.reading_ceiling_s(setting.nplc))
        return setting, raw

    def _run(self, s):
        setting, k, reason, state = None, 0, "", "idle"
        try:
            while not s.stop.is_set():
                if self._mono() - s.t0 >= s.bound_s:
                    reason = "the time bound was reached"
                    break
                try:
                    setting, raw = self.meter.exchange(lambda conn: self._read(conn, setting, k))
                except scpi.ScpiError as exc:
                    reason, state = str(exc), "error"
                    break
                now = self._mono() - s.t0
                value = meterlib._number(raw)
                if value is None:
                    reason, state = "the meter answered %r, which is not a number" % raw, "error"
                    break
                if s.function is None:
                    s.function, s.unit = setting.function, setting.unit
                elif setting.function != s.function:
                    reason, state = "the meter's function changed from %s to %s during the session" % (s.function, setting.function), "error"
                    break
                s.range, s.nplc = setting.range, setting.nplc
                self._take(s, now, value, setting)
                k += 1
            if state == "idle" and not reason:
                reason = "stopped"                         # the loop ended because stop() was called
        finally:
            with self._lock:
                s.state, s.reason, s.ended = state, reason, self._wall()
                if s.file is not None:
                    try:
                        s.file.close()
                    except OSError:
                        pass
                    self._sidecar(s, ended=s.ended)
                self._publish_state(s)

    def _take(self, s, t, value, setting):
        overload = abs(value) >= meterlib.OVERLOAD
        wall = self._wall()
        with self._lock:                                   # the stats, the envelope and the feed move together: a snapshot is one moment
            if overload:
                s.stats.overload()
            else:
                s.stats.add(t, value)
                s.envelope.add(t, value)
            if s.file is not None:
                s.file.write("%.3f,%.3f,%s,%s,%s,%s,%s,%d\n" % (
                    t, wall, "" if overload else repr(value), setting.unit, setting.function,
                    "" if setting.range is None else setting.range, "" if setting.nplc is None else setting.nplc, 1 if overload else 0))
                s.file.flush()
            self.bus.publish("meter.reading", "meter", {
                "session": s.number, "t_s": round(t, 3), "wall": wall, "value": None if overload else value, "overload": overload,
                "unit": setting.unit, "function": setting.function, "range": setting.range, "nplc": setting.nplc})

    # -- reading state --------------------------------------------------------------------------------
    def _current(self, s):
        return (s.function or "").upper() in meterlib.CURRENT

    def _summary(self, s):
        elapsed = (s.ended - s.started) if s.ended else self._mono() - s.t0
        return {"session": s.number, "state": s.state, "mode": s.mode, "name": s.name, "started": s.started, "elapsed_s": round(elapsed, 3),
                "bound_s": s.bound_s, "reason": s.reason, "function": s.function, "unit": s.unit,
                "stats": s.stats.to_dict(self._current(s))}

    def _publish_state(self, s):
        self.bus.publish("meter.state", "meter", self._summary(s))

    def _sidecar(self, s, ended):
        """The facts about a recording the CSV does not carry, written when it starts and again when it ends: so a recording a killed service left is known to have been cut short."""
        try:
            with open(os.path.join(self.folder, s.name + ".json"), "w") as fh:
                json.dump({"name": s.name, "started": s.started, "ended": ended, "reason": s.reason, "bound_s": s.bound_s,
                           "function": s.function, "unit": s.unit, "stats": s.stats.to_dict(self._current(s))}, fh)
        except OSError:
            pass

    def session(self):
        with self._lock:
            return self._summary(self._session) if self._session is not None else {
                "session": 0, "state": "idle", "mode": None, "name": None, "started": None, "elapsed_s": 0, "bound_s": None, "reason": "",
                "function": None, "unit": None, "stats": Stats().to_dict()}

    def profile(self):
        """The session so far as an envelope, and the feed cursor taken at the same instant: events after the cursor continue it with no gap and no repeat."""
        with self._lock:
            s = self._session
            return {"session": s.number if s else 0, "cursor": self.bus.latest, "epoch": self.bus.epoch,
                    "unit": s.unit if s else None, "function": s.function if s else None,
                    "buckets": s.envelope.snapshot() if s else [], "stats": self._summary(s)["stats"] if s else Stats().to_dict(),
                    "state": s.state if s else "idle",
                    "last": list(s.stats._last) if s and s.stats._last else None}      # [t_s, value]: where a page carries the charge on from

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
                meta = dict(meta, started=s.started, function=s.function, unit=s.unit, stats=self._summary(s)["stats"])
            rows.append({"name": f[:-4], "bytes": os.path.getsize(path), "started": meta.get("started"), "ended": meta.get("ended"),
                         "reason": meta.get("reason", ""), "function": meta.get("function"), "unit": meta.get("unit"),
                         "stats": meta.get("stats"), "interrupted": meta.get("ended") is None and f[:-4] != live,
                         "recording": f[:-4] == live, "mtime": os.path.getmtime(path)})
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
