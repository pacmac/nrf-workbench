"""Named events on a recording (docs/specs/meter-page.md, "Events").

An event is a stretch of a recording, `start_s` to `end_s` in seconds since the recording began, with a name. A person makes one on the scope with two clicks, the first for where it starts
and the second for where it ends, so nothing has to be known before the capture: what a board did is read off the trace afterwards. Events are kept in `<recording>.events.json`. They may
overlap (a radio burst is inside the wake it belongs to), unlike stages, which share out a wake's charge and so may not.
"""
import json
import math
import os

NAME_MAX = 40
MAX_EVENTS = 200                              # a person names a few dozen: 200 keeps the file and the table a person reads small
MIN_LENGTH_S = 1e-5                           # one sample of the profiler (100,000 a second): nothing shorter is a stretch


class EventError(ValueError):
    def __init__(self, message, field):
        ValueError.__init__(self, message)
        self.field = field


def _seconds(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EventError("%s is a number of seconds since the recording began" % field, field)
    return float(value)


def set_event(events, name, start_s, end_s, was=None):
    """`events` with the event `name` added, or changed if one of that name (ignoring case) is there, in order of start. `was` is the name of the event this one replaces, so a rename or a
    move is one change: with it, `name` may be the same or new, but must not be another event's. EventError for a rule broken."""
    name = (name if isinstance(name, str) else "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise EventError("an event name is 1 to %d characters" % NAME_MAX, "name")
    start_s, end_s = _seconds(start_s, "start_s"), _seconds(end_s, "end_s")
    if start_s < 0:
        raise EventError("start_s is 0 or more: the recording began at 0", "start_s")
    if end_s - start_s < MIN_LENGTH_S:
        raise EventError("end_s is at least %g s after start_s: a stretch shorter than one sample holds nothing" % MIN_LENGTH_S, "end_s")
    key, old = name.lower(), (was or "").strip().lower()
    if was is not None and not any(e["name"].lower() == old for e in events):
        raise EventError("there is no event named %s to replace" % was, "was")
    kept = [e for e in events if e["name"].lower() not in ((old,) if was is not None else (key,))]
    if any(e["name"].lower() == key for e in kept):
        raise EventError("there is already an event named %s" % name, "name")
    if len(kept) + 1 > MAX_EVENTS:
        raise EventError("at most %d events: delete one first" % MAX_EVENTS, "name")
    kept.append({"name": name, "start_s": start_s, "end_s": end_s})
    kept.sort(key=lambda e: (e["start_s"], e["end_s"], e["name"].lower()))
    return kept


def remove_event(events, name):
    """`events` without `name` (ignoring case), or None if there is no such event."""
    kept = [e for e in events if e["name"].lower() != (name or "").lower()]
    return kept if len(kept) != len(events) else None


def load_events(path):
    try:
        with open(path) as fh:
            rows = json.load(fh)["events"]
        out = [{"name": str(r["name"]), "start_s": float(r["start_s"]), "end_s": float(r["end_s"])} for r in rows]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out.sort(key=lambda e: (e["start_s"], e["end_s"], e["name"].lower()))
    return out


def save_events(path, events):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"events": events}, fh)
    os.replace(tmp, path)
