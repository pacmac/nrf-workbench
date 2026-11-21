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
from . import events as eventlib
from . import meter as meterlib
from . import replay
from . import scpi
from . import waketable
from .envelope import Envelope, Stats
from .source import Fault, Reading

MIN_BOUND_S, MAX_BOUND_S = 1, 43200          # 12 h: about 216,000 readings and 13 MB of CSV at NPLC 10 (docs/specs/meter-page.md)
MAX_WAKES = int(MAX_BOUND_S / capture.BLOCK_S)      # a wake is at least one 0.2 s block, so 12 h holds at most 43200 / 0.2 = 216,000 of them (docs/specs/power-workbench.md, "Capture")
KEEP_RECORDINGS = 10                         # newest files kept: 10 x 13 MB = 130 MB at most
RECORDING_SUFFIXES = (".csv", ".json", ".stages.json", ".events.json")      # what a recording keeps beside its saved wakes folder
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


class NotAProfilerRecording(Exception):
    """The recording is the multimeter's: it has no wakes to tabulate."""


class ProfileNotWanted(Exception):
    """A wake profile was given to a session of an instrument that keeps no wakes (the multimeter)."""


class BadEvents(ValueError):
    """An event of the power profile in force that the recording's own rules refuse (the manifest was judged by the same rules, so this means one of them changed)."""


class NotRunning(Exception):
    """There is no running session to change."""


NO_PROFILE = {"project": None, "role": None, "source": "found", "overridden": []}      # no project's profile was in force and no wake argument was given: everything is found from the data


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
        self.ended_by = None                                 # why it ended: wakes_done, time_reached, gave_up, stopped or error; None while it runs
        self.stop_after_wakes = None                         # end when this many whole wakes have closed (the profiler's sessions only)
        self.wakes_dir = None                               # where a recording keeps its wakes (the profiler's source writes them there)
        self.wake = None                                     # the capture.WakeProfile this session was started with (None: everything found from the data)
        self.profile = dict(NO_PROFILE)                      # where the wake profile came from (docs/specs/project-power-profile.md, decision 4)


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
        self._traces = replay.TraceCache()                   # the recording last asked for, parsed (a replay asks the same file many times)
        self._stored_key, self._stored = None, None          # and the saved wakes of the recording last asked for, with the one decoded last
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

    def resolve_source(self, name=None):
        """The name of the source a session started with `source=name` would read (the multimeter when there is one, else the profiler). Raises Missing."""
        return self._source(name).name

    @staticmethod
    def _profile_files(events):
        """(stages, events) a power profile's events make, by the rules a recording applies: a `from: wake` event is a stage, a `from: start` one an event. BadEvents for one that is refused."""
        stages, recorded = [], []
        try:
            for e in events:
                if e["from"] == "wake":
                    stages = capture.set_stage(stages, e["name"], e["start_s"], e["end_s"], e["every"], e["offset"])
                else:
                    recorded = eventlib.set_event(recorded, e["name"], e["start_s"], e["end_s"])
        except (capture.StageError, eventlib.EventError) as exc:
            raise BadEvents("%s: %s" % (e["name"], exc))
        return stages, recorded

    def start(self, mode, bound_s, name=None, source=None, wake=None, profile=None, stop_after_wakes=None):
        """The session's summary dict. Raises Busy, NameTaken, Missing, BadEvents, or ValueError for a bad bound, count or name. `wake` is a capture.WakeProfile (the profiler's sessions only).
        `stop_after_wakes` ends the session when that many whole wakes have closed (`bound_s` is then the give-up: None means the 12 h maximum); the profiler's sessions only.
        `profile` is where it came from, {project, role, source, overridden, events} (docs/specs/project-power-profile.md): it is reported with the session and in a recording's sidecar,
        and a recording starts with its events (a watch keeps no files, so it takes only the trigger, which is `wake`)."""
        if mode not in ("watch", "record"):
            raise ValueError("mode is watch or record")
        if stop_after_wakes is not None and (isinstance(stop_after_wakes, bool) or not isinstance(stop_after_wakes, int) or not 1 <= stop_after_wakes <= MAX_WAKES):
            raise ValueError("stop_after_wakes is a whole number from 1 to %d: a wake is at least one 0.2 s block and 12 h holds that many" % MAX_WAKES)
        if bound_s is None and stop_after_wakes is not None:
            bound_s = MAX_BOUND_S                            # no ceiling given: the give-up is the 12 h maximum, never a period guessed from the board
        if isinstance(bound_s, bool) or not isinstance(bound_s, (int, float)) or not MIN_BOUND_S <= bound_s <= MAX_BOUND_S:
            raise ValueError("bound_s is %d to %d seconds" % (MIN_BOUND_S, MAX_BOUND_S))
        with self._lock:
            if self._session is not None and self._session.thread is not None and self._session.thread.is_alive():
                raise Busy(self._summary(self._session))
            src = self._source(source)
            if wake is not None and src.name != "ppk2":
                raise ProfileNotWanted("a multimeter session keeps no wakes, so it has no wake profile")
            if stop_after_wakes is not None and src.name != "ppk2":
                raise ProfileNotWanted("a multimeter session keeps no wakes, so it cannot stop after them")
            profile_stages, profile_events = self._profile_files((profile or {}).get("events") or []) if mode == "record" else ([], [])
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
            s.wake = wake
            s.stop_after_wakes = stop_after_wakes
            if profile:
                s.profile = {"project": profile["project"], "role": profile["role"], "source": profile["source"], "overridden": list(profile["overridden"])}
            if mode == "record":
                s.wakes_dir = os.path.join(self.folder, name + ".wakes")
                s.file = open(os.path.join(self.folder, name + ".csv"), "w")
                s.file.write(HEADER)
                s.file.flush()
                try:
                    if profile_stages:
                        capture.save_stages(os.path.join(self.folder, name + ".stages.json"), profile_stages)
                    if profile_events:
                        eventlib.save_events(os.path.join(self.folder, name + ".events.json"), profile_events)
                except OSError:
                    pass                                         # as the sidecar: a disk that will not take a small file does not stop the recording; the person sees the stages are not there
                self._sidecar(s, ended=None)
            self._session = s
            self._publish_state(s)
            s.thread = threading.Thread(target=self._run, args=(s,), name="meter-session-%d" % s.number, daemon=True)
            s.thread.start()
            return self._summary(s)

    def wake_set(self, wake):
        """Change what a wake is for the running session, from its next block (docs/specs/ppk2-stages.md, "The wake profile"): the trigger can be changed during a capture. `wake` is a
        capture.WakeProfile; a field it leaves to the data is found again. Raises NotRunning with no session running and ProfileNotWanted for a multimeter's. The session's summary."""
        with self._lock:
            s = self._session
            if s is None or s.thread is None or not s.thread.is_alive():
                raise NotRunning("no session is running")
            if s.source.name != "ppk2":
                raise ProfileNotWanted("a multimeter session keeps no wakes, so it has no wake profile")
            s.wake = wake
            s.source.set_wake(s, wake)
            self._publish_state(s)
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
            self._remove_files(old[:-4])

    def _remove_files(self, name):
        """Every file of one recording: the CSV, its sidecar, its stages and events, and its saved wakes. What was removed is returned."""
        removed = []
        for suffix in RECORDING_SUFFIXES:
            try:
                os.unlink(os.path.join(self.folder, name + suffix))
                removed.append(name + suffix)
            except OSError:
                pass
        if os.path.isdir(os.path.join(self.folder, name + ".wakes")):
            shutil.rmtree(os.path.join(self.folder, name + ".wakes"), ignore_errors=True)
            removed.append(name + ".wakes")
        self._traces.forget(os.path.join(self.folder, name + ".csv"))
        self._stored_key, self._stored = None, None
        return removed

    def recording_delete(self, name):
        """Delete a recording and everything kept with it. Raises RecordingUnknown for a name that is not there and Busy while a session is writing it."""
        self._recording_path(name)
        s = self._session
        if s is not None and s.name == name and s.thread is not None and s.thread.is_alive():
            raise Busy(self._summary(s))
        return {"name": name, "removed": self._remove_files(name)}

    # -- the thread -----------------------------------------------------------------------------------
    def _run(self, s):
        k, reason, state, ended_by = 0, "", "idle", None
        source = s.source
        try:
            try:
                source.begin(s)
                if s.facts:                                     # what the source learned of its instrument when it opened it (the profiler's mode, voltage and output): announce it, as the
                    with self._lock:                            # state sent when the session was created could not have it
                        self._publish_state(s)
                while not s.stop.is_set():
                    if self._mono() - s.t0 >= s.bound_s:
                        reason = "the time bound was reached"
                        ended_by = "time_reached" if s.stop_after_wakes is None else "gave_up"      # with a count wanted, the bound is the give-up: the wakes did not come
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
                    if s.stop_after_wakes is not None and s.facts.get("wakes", 0) >= s.stop_after_wakes:      # the Nth whole wake has closed (its tail included): event-driven off the capture's own close
                        reason, ended_by = "%d whole wake%s recorded" % (s.stop_after_wakes, "" if s.stop_after_wakes == 1 else "s"), "wakes_done"
                        break
                if not reason:
                    reason, ended_by = "stopped", "stopped"    # the loop ended because stop() was called
            except Fault as exc:
                reason, state, ended_by = str(exc), "error", "error"
        finally:
            try:
                source.end()
            except Exception as exc:                           # closing an instrument must not stop the session's own books being closed
                if state != "error":
                    reason, state, ended_by = "closing the instrument failed: %s" % exc, "error", "error"
            with self._lock:
                s.state, s.reason, s.ended, s.ended_by = state, reason, self._wall(), ended_by
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
                     "elapsed_s": round(elapsed, 3), "bound_s": s.bound_s, "reason": s.reason, "ended_by": s.ended_by, "stop_after_wakes": s.stop_after_wakes,
                     "function": s.function, "unit": s.unit,
                     "profile": dict(s.profile), "stats": s.stats.to_dict(self._current(s))}, **s.facts)

    def _publish_state(self, s):
        self.bus.publish("meter.state", "meter", self._summary(s))

    def _sidecar(self, s, ended):
        """The facts about a recording the CSV does not carry, written when it starts and again when it ends: so a recording a killed service left is known to have been cut short."""
        try:
            with open(os.path.join(self.folder, s.name + ".json"), "w") as fh:
                json.dump(dict({"name": s.name, "source": s.source.name, "started": s.started, "ended": ended, "reason": s.reason, "ended_by": s.ended_by, "bound_s": s.bound_s,
                                "stop_after_wakes": s.stop_after_wakes, "function": s.function, "unit": s.unit, "profile": s.profile, "stats": s.stats.to_dict(self._current(s))}, **s.facts), fh)
        except OSError:
            pass

    def session(self):
        with self._lock:
            return self._summary(self._session) if self._session is not None else {
                "session": 0, "source": None, "state": "idle", "mode": None, "name": None, "started": None, "elapsed_s": 0, "bound_s": None, "reason": "", "ended_by": None, "stop_after_wakes": None,
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
        folder = os.path.join(self.folder, recording + ".wakes")
        try:
            key = (folder, os.stat(folder).st_mtime_ns, len(os.listdir(folder)))
        except OSError:
            key = None
        if key is not None and key == self._stored_key:      # the wakes of the recording last asked for: decoding one takes seconds, and a person zooming asks again and again
            return self._stored
        ws = ppk2.stored_wakes(folder)
        self._stored_key, self._stored = key, ws
        return ws

    def _recording_path(self, recording):
        if not NAME.match(recording or "") or not self.folder or not os.path.exists(os.path.join(self.folder, recording + ".csv")):
            raise RecordingUnknown("there is no recording named %s" % recording)
        return os.path.join(self.folder, recording + ".csv")

    def wake_table(self, recording):
        """One row per wake of a power-profiler recording, found by the rule the live session used (waketable.py). RecordingUnknown for a name that is not there;
        NotAProfilerRecording for the multimeter's; ValueError (a ProfileError) if the sidecar's wake profile is one the capture refuses."""
        path = self._recording_path(recording)
        meta = {}
        try:
            with open(path[:-4] + ".json") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            pass
        if meta.get("source", "meter") != "ppk2":
            raise NotAProfilerRecording("%s is a multimeter recording: it keeps no wakes" % recording)
        try:
            trace = self._traces.get(path)
        except OSError as exc:
            raise RecordingUnknown("the recording %s could not be read: %s" % (recording, exc))
        ws = self._wakes(recording)
        data = waketable.build(trace, meta.get("wake") or {}, ws.spans() if ws is not None else [])
        data["recording"] = recording
        return data

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

    def stage_set(self, recording, name, start_s, end_s, every=1, offset=0):
        """The recording's stages with this one added or changed (capture.StageError for a rule broken)."""
        path = self._stages_path(recording)
        stages = capture.set_stage(capture.load_stages(path), name, start_s, end_s, every, offset)
        capture.save_stages(path, stages)
        return stages

    def stage_delete(self, recording, name):
        """The stages left, or None if there was no stage of that name."""
        path = self._stages_path(recording)
        stages = capture.remove_stage(capture.load_stages(path), name)
        if stages is not None:
            capture.save_stages(path, stages)
        return stages

    # -- events: named stretches of a recording (docs/specs/meter-page.md, "Events") ------------------------
    def _events_path(self, recording):
        self._recording_path(recording)
        return os.path.join(self.folder, recording + ".events.json")

    def event_set(self, recording, name, start_s, end_s, was=None):
        """The recording's events with this one added or changed (eventlib.EventError for a rule broken)."""
        path = self._events_path(recording)
        evs = eventlib.set_event(eventlib.load_events(path), name, start_s, end_s, was)
        eventlib.save_events(path, evs)
        return evs

    def event_delete(self, recording, name):
        """The events left, or None if there was no event of that name."""
        path = self._events_path(recording)
        evs = eventlib.remove_event(eventlib.load_events(path), name)
        if evs is not None:
            eventlib.save_events(path, evs)
        return evs

    def events(self, recording, volts=None):
        """The recording's events, each with what it measured: its length, average, lowest and highest current, charge and (with `volts`) energy, exact from the saved wake's samples when the
        event is wholly inside one and from the readings, each counted for the part inside the event, when not. An event with no readings in it measures nothing (it is not an error)."""
        evs = eventlib.load_events(self._events_path(recording))
        ws = self._wakes(recording)
        rows = []
        for e in evs:
            row = dict(e, duration_s=e["end_s"] - e["start_s"], source=None, wake=None, covered_s=None, mean=None, lowest=None, peak=None, charge_c=None, energy_j=None)
            try:
                r = self._replay(recording, e["start_s"], e["end_s"], 2, ws=ws, exact=True)
            except ValueError:
                rows.append(row)
                continue
            st = r["stats"]
            row.update(source=r["source"], wake=r["wake"], covered_s=st["seconds"], mean=st["mean"], lowest=st["lowest"], peak=st["highest"], charge_c=st["charge"],
                       energy_j=st["charge"] * volts if volts else None)
            rows.append(row)
        return {"recording": recording, "events": rows}

    def stages(self, recording, volts=None):
        """The stage table over the recording's saved wakes."""
        path = self._stages_path(recording)
        stages = capture.load_stages(path)
        ws = self._wakes(recording)
        if ws is None:
            return {"recording": recording, "wakes": 0, "stages": [dict(s, length_s=s["end_s"] - s["start_s"], wakes=0, numbers=[], mean=None, lowest_mean=None, highest_mean=None,
                                                                         peak=None, charge_c=None, share=None, energy_j=None) for s in stages], "groups": []}
        return dict(ws.stage_table(stages, volts), recording=recording)

    def replay(self, recording, t0=None, t1=None, points=None):
        """Any stretch of a recording as an exact envelope, on the recording's own clock (seconds since its session began). RecordingUnknown for a name that is not there; ValueError for
        points out of range or a stretch that holds nothing (the message says where the recording runs). Where the stretch is narrower than a row per point and lies inside a wake the
        recording saved at full rate, it is drawn from that wake's samples (`source` "wake") instead of the file's 0.2 s rows (`source` "csv")."""
        return self._replay(recording, t0, t1, points)

    def _replay(self, recording, t0, t1, points, ws=False, exact=False):
        """`replay`, with the recording's WakeSet given (so many stretches can share one: `False` fetches it) and `exact`: a stretch inside a saved wake is drawn from the wake's samples
        however wide, which is what a measurement wants (a stretch drawn for a chart only needs them when it is finer than the readings)."""
        path = self._recording_path(recording)
        try:
            trace = self._traces.get(path)
        except OSError as exc:
            raise RecordingUnknown("the recording %s could not be read: %s" % (recording, exc))
        points = replay.POINTS_DEFAULT if points is None else points
        got = trace.window(t0, t1, points)
        if got is None:
            raise ValueError("the stretch holds no readings of %s (it runs from %g to %g s)" % (recording, trace.start_s, trace.end_s))
        buckets, stats, width = got
        lo = trace.start_s if t0 is None else t0
        hi = trace.end_s if t1 is None else t1
        if ws is False:
            ws = self._wakes(recording)
        spans = ws.spans() if ws is not None else []
        out = {"recording": recording, "source": "csv", "wake": None, "unit": trace.unit, "function": trace.function, "started": trace.wall0,
               "start_s": trace.start_s, "duration_s": trace.end_s, "t0": buckets[0]["t0"], "t1": buckets[-1]["t1"], "points": len(buckets), "width_s": width,
               "resolution_s": trace.finest_s, "buckets": buckets, "stats": stats, "wakes": [{"n": n, "t0": a, "t1": b} for n, a, b in spans]}
        if exact or (hi - lo) / points < trace.finest_s:       # finer than a row per point was asked for (or a measurement): a saved wake that holds the whole stretch can answer
            for n, a, b in spans:
                if a - 1e-9 <= lo and hi <= b + 1e-9:
                    row = next((r for r in ws.rows() if r["n"] == n), None)
                    if row is None:
                        break
                    base = row["t0"] + row["wake_at_s"]        # the wake's own time zero, on the recording's clock
                    env = ws.envelope(n, lo - base, hi - base, points)
                    out.update(source="wake", wake=n, t0=base + env["t0"], t1=base + env["t1"], points=env["points"], width_s=env["width_s"], resolution_s=1.0 / capture.SAMPLE_RATE_HZ,
                               buckets=[dict(b, t0=base + b["t0"], t1=base + b["t1"]) for b in env["buckets"]],
                               stats={"lowest": min(b["min"] for b in env["buckets"]), "highest": env["peak"], "mean": env["mean"], "charge": env["charge_c"],
                                      "samples": env["samples"], "seconds": env["samples"] / float(capture.SAMPLE_RATE_HZ)})
                    break
        return out

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
