"""The console's lines kept on disk (docs/specs/power-workbench.md, "Console persistence").

A bus subscriber appends every `console.line` to `<folder>/<link>/<YYYYMMDD-HHMMSS>.jsonl` (UTC, the first line's host time), one JSON
object per line: `{wall, offset, text}` and `partial: true` where the broker cut a long line. Nothing but counters is held in memory,
so a restarted service reads the same lines back.

The bound is required and has no default. A segment file rolls when it reaches a quarter of it (`SEGMENTS`: dropping the oldest then
removes at most a quarter of what is kept), and after each append the oldest segments go while the link's folder is over the bound; the
segment being written never goes, so the folder is over the bound by at most the one line being written.

Each line is flushed to the OS, not synced: a restart or a crash of the process loses nothing, a power cut can lose the last seconds
(an fsync per line would wear the Pi's card for a stream of a few lines a second). A write that fails is counted and never raised, so a
full disk does not stop the console. A line torn by a crash is skipped on reading, and the next append starts on a fresh line.
"""
import calendar
import json
import math
import os
import threading
import time

from .links import SLUG

SEGMENTS = 4                                  # a segment is a quarter of the bound: see the module note
NAME_FORMAT = "%Y%m%d-%H%M%S"
SUFFIX = ".jsonl"


def _segment_time(name):
    """The host time (UTC seconds) a segment file's name gives, or None for a file that is not one."""
    if not name.endswith(SUFFIX):
        return None
    try:
        return calendar.timegm(time.strptime(name[:-len(SUFFIX)], NAME_FORMAT))
    except ValueError:
        return None


def _row(raw):
    """The row a stored line holds, or None for a torn or foreign line."""
    try:
        row = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(row, dict) or not all(k in row for k in ("wall", "offset", "text")):
        return None
    wall = row["wall"]
    if isinstance(wall, bool) or not isinstance(wall, (int, float)):
        return None
    return row


class LineLog(object):
    def __init__(self, folder, max_bytes):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, (int, float)) or not math.isfinite(max_bytes) or max_bytes <= 0:
            raise ValueError("the bound is a number of bytes above 0 and has no default")
        self._folder = folder
        self._max = int(max_bytes)
        self._seg = max(1, self._max // SEGMENTS)
        self._lock = threading.Lock()
        self._links = {}                      # link -> {"dir", "segs": [[name, size]], "fh", "cur"}
        self.written, self.write_errors, self.last_error = 0, 0, None

    # -- writing ------------------------------------------------------------------------------------
    def attach(self, bus):
        """Subscribe to a console bus; returns the subscription token. The callback never raises."""
        return bus.subscribe(self._on_event)

    def _on_event(self, event):
        if event.type != "console.line":
            return
        p = event.payload
        try:
            self.append(p["link"], event.wall, p["offset"], p["text"], bool(p.get("partial")))
        except (ValueError, KeyError) as exc:                      # not a line this log can keep: counted, never raised
            self._fail(exc)

    def _fail(self, exc):
        with self._lock:
            self.write_errors += 1
            self.last_error = "%s: %s" % (type(exc).__name__, exc)

    def append(self, link, wall, offset, text, partial=False):
        """Keep one line. ValueError for a link name that is not a plain name (it would be a path); a write that fails is counted."""
        if not isinstance(link, str) or not SLUG.match(link):
            raise ValueError("a link name is 1 to 64 characters from a-z 0-9 . _ -, starting with a letter or a digit")
        row = {"wall": wall, "offset": offset, "text": text}
        if partial:
            row["partial"] = True
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8", "backslashreplace")
        with self._lock:
            try:
                self._write(link, wall, data)
                self.written += 1
            except OSError as exc:
                self.write_errors += 1
                self.last_error = "%s: %s" % (type(exc).__name__, exc)

    def _state(self, link):
        st = self._links.get(link)
        if st is None:
            d = os.path.join(self._folder, link)
            os.makedirs(d, exist_ok=True)
            segs = [[n, os.path.getsize(os.path.join(d, n))] for n in sorted(os.listdir(d)) if _segment_time(n) is not None]
            st = self._links[link] = {"dir": d, "segs": segs, "fh": None, "cur": None}
        return st

    def _open_segment(self, st, wall):
        name = time.strftime(NAME_FORMAT, time.gmtime(wall)) + SUFFIX
        path = os.path.join(st["dir"], name)
        fh = open(path, "ab")
        seg = next((s for s in st["segs"] if s[0] == name), None)
        if seg is None:
            seg = [name, 0]
            st["segs"].append(seg)
            st["segs"].sort()
        if seg[1] > 0:                                       # a segment a crash left may end in half a line: start on a fresh one
            with open(path, "rb") as rd:
                rd.seek(-1, os.SEEK_END)
                if rd.read(1) != b"\n":
                    fh.write(b"\n")
                    seg[1] += 1
        st["fh"], st["cur"] = fh, seg

    def _write(self, link, wall, data):
        st = self._state(link)
        if st["fh"] is None:
            self._open_segment(st, wall)
        st["fh"].write(data)
        st["fh"].flush()
        st["cur"][1] += len(data)
        if st["cur"][1] >= self._seg:
            st["fh"].close()
            st["fh"] = None
        self._prune(st)

    def _prune(self, st):
        while sum(s[1] for s in st["segs"]) > self._max:
            oldest = next((s for s in st["segs"] if s is not st["cur"]), None)
            if oldest is None:
                return
            try:
                os.remove(os.path.join(st["dir"], oldest[0]))
            except OSError as exc:
                self.write_errors += 1
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
                return
            st["segs"].remove(oldest)

    def status(self):
        with self._lock:
            return {"written": self.written, "write_errors": self.write_errors, "last_error": self.last_error}

    # -- reading ------------------------------------------------------------------------------------
    def _segments(self, link):
        if not isinstance(link, str) or not SLUG.match(link):
            return []
        d = os.path.join(self._folder, link)
        try:
            names = os.listdir(d)
        except OSError:
            return []
        return [(_segment_time(n), os.path.join(d, n)) for n in sorted(names) if _segment_time(n) is not None]

    def between(self, link, from_wall, to_wall, limit=None):
        """{lines, more, next_from_wall, kept_from}: the lines with from_wall <= wall < to_wall, in order. With `limit`, at most that
        many, and `more` with the host time to carry on from; `kept_from` is the oldest host time still on disk (None with none).
        Segment names only decide which files are skipped: they assume the host clock does not run backwards."""
        segs = self._segments(link)
        out = {"lines": [], "more": False, "next_from_wall": None, "kept_from": None}
        if not segs:
            return out
        out["kept_from"] = self._first_wall(segs[0][1])
        for i, (start, path) in enumerate(segs):
            if start >= to_wall:
                break
            if i + 1 < len(segs) and segs[i + 1][0] <= from_wall:
                continue
            with open(path, "rb") as fh:
                for raw in fh:
                    row = _row(raw)
                    if row is None or not from_wall <= row["wall"] < to_wall:
                        continue
                    if limit is not None and len(out["lines"]) >= limit:
                        out["more"], out["next_from_wall"] = True, row["wall"]
                        return out
                    out["lines"].append(row)
        return out

    def _first_wall(self, path):
        with open(path, "rb") as fh:
            for raw in fh:
                row = _row(raw)
                if row is not None:
                    return row["wall"]
        return None
