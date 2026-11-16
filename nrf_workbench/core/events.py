"""The event bus (docs/specs/core.md, "the event bus and the activity log").

Areas PUBLISH typed events; subscribers are CALLBACKS invoked on publish. Nothing waits on a thread and
nothing polls, and core imports no async runtime: an async consumer registers a callback that hands the
event to its own queue thread-safely. Callbacks from different threads can arrive out of order, so a
consumer treats a callback as "something new" and RESUMES BY CURSOR (`since`), which is exact.

Every retained stream is bounded by a REQUIRED `retain` (a count) and `max_age_s`, with **no default**:
an unbounded log is retained for ever by oversight. A bus without a bound refuses to exist (`no_bound`).
The values are the caller's, derived from the measured rate of the thing being logged.

Resume is exact: `since(cursor)` returns every retained event after the cursor, and says HOW MANY were
lost if the cursor is older than what is retained, never silently skipping. A service restart restarts
the cursors, so every bus has an `epoch`; a client holding another epoch is told it must reset, which is
different from loss and is reported as such.
"""
import collections
import math
import threading
import time
import uuid


class BoundError(ValueError):
    """A stream was asked for with no usable bound (the `no_bound` refusal of core.md)."""
    code = "no_bound"


class Event(object):
    __slots__ = ("cursor", "type", "area", "payload", "mono", "wall")

    def __init__(self, cursor, type, area, payload, mono, wall):
        self.cursor, self.type, self.area, self.payload, self.mono, self.wall = cursor, type, area, payload, mono, wall

    def to_dict(self):
        return {"cursor": self.cursor, "type": self.type, "area": self.area, "payload": self.payload,
                "mono": self.mono, "wall": self.wall}


class Page(object):
    """What `since` returns. `lost` is how many events after the cursor were pruned before they could be
    read (0 if none), or None when the cursor belongs to another epoch (`reset` is then True)."""
    __slots__ = ("events", "lost", "reset", "latest", "epoch")

    def __init__(self, events, lost, reset, latest, epoch):
        self.events, self.lost, self.reset, self.latest, self.epoch = events, lost, reset, latest, epoch

    def to_dict(self):
        return {"events": [e.to_dict() for e in self.events], "lost": self.lost, "reset": self.reset,
                "latest": self.latest, "epoch": self.epoch}


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


class Bus(object):
    def __init__(self, retain, max_age_s, mono=time.monotonic, wall=time.time):
        if not isinstance(retain, int) or isinstance(retain, bool) or retain < 1:
            raise BoundError("a retained stream needs `retain`, a count of at least 1, and has no default")
        if not _positive_number(max_age_s):
            raise BoundError("a retained stream needs `max_age_s`, a positive number of seconds, and has no default")
        self.retain, self.max_age_s = retain, max_age_s
        self.epoch = uuid.uuid4().hex
        self._mono, self._wall = mono, wall
        self._events = collections.deque()
        self._next = 1
        self._lock = threading.Lock()
        self._subscribers = {}
        self._next_token = 1
        # What a subscriber's failure leaves behind is kept (never swallowed), bounded by the stream's own bound.
        self.faults = collections.deque(maxlen=retain)

    # -- publishing ---------------------------------------------------------------------------------
    def _prune(self, now):
        while len(self._events) > self.retain:
            self._events.popleft()
        while self._events and now - self._events[0].mono > self.max_age_s:
            self._events.popleft()

    def publish(self, type, area, payload=None):
        if not isinstance(type, str) or not type:
            raise ValueError("an event has a type")
        if not isinstance(area, str) or not area:
            raise ValueError("an event names the area that raised it")
        if payload is not None and not isinstance(payload, dict):
            raise TypeError("an event payload is a dict")
        now = self._mono()
        with self._lock:
            event = Event(self._next, type, area, dict(payload or {}), now, self._wall())
            self._next += 1
            self._events.append(event)
            self._prune(now)
            subscribers = list(self._subscribers.values())
        for callback in subscribers:                 # outside the lock: a slow subscriber never blocks publishing
            try:
                callback(event)
            except Exception as exc:                 # noqa: BLE001 - recorded, not swallowed
                self.faults.append({"subscriber": getattr(callback, "__name__", repr(callback)),
                                    "error": repr(exc), "cursor": event.cursor})
        return event

    # -- subscribing ----------------------------------------------------------------------------------
    def subscribe(self, callback):
        if not callable(callback):
            raise TypeError("a subscriber is a callable")
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._subscribers[token] = callback
        return token

    def unsubscribe(self, token):
        with self._lock:
            return self._subscribers.pop(token, None) is not None

    # -- reading --------------------------------------------------------------------------------------
    def __len__(self):
        """How many events are held right now (not pruned by a read): the bound holds with no reader."""
        with self._lock:
            return len(self._events)

    @property
    def latest(self):
        with self._lock:
            return self._next - 1

    def since(self, cursor=0, epoch=None, types=None):
        """Every retained event after `cursor`, as a Page. A client resuming passes the epoch it last saw."""
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise ValueError("a cursor is an integer, 0 or more")
        with self._lock:
            self._prune(self._mono())
            latest = self._next - 1
            if (epoch is not None and epoch != self.epoch) or cursor > latest:
                kept = [e for e in self._events if types is None or e.type in types]
                return Page(kept, None, True, latest, self.epoch)
            oldest = self._events[0].cursor if self._events else self._next
            lost = max(0, oldest - cursor - 1)
            kept = [e for e in self._events if e.cursor > cursor and (types is None or e.type in types)]
            return Page(kept, lost, False, latest, self.epoch)
