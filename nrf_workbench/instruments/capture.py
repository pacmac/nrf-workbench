"""Single wakes kept at the unit's full sample rate (docs/specs/ppk2-stages.md, stage 1).

The session's envelope is 0.2 s a point; a wake's stages (a CPU wake, then a radio burst) are milliseconds. So as the profiler's blocks go by, a wake is noticed and its raw
words are kept: from the block before it to the first quiet block after. `Capturer` decides and holds; `decode` and `window` turn a capture's words into amps and into an exact
lowest/highest/mean envelope of any stretch of it, at any resolution.
"""
import collections
import json
import math
import os
import sys
import threading
import zlib
from array import array

SAMPLE_RATE_HZ = 100000
WORD_MASK = 0x1FFFF                          # bits 0..13 the ADC, 14..16 the range (the rest are the counter and the logic inputs)
WAKE_FACTOR = 2                              # a wake must at least double the sleep current (the page's rule: noise is a few per cent, a radio burst a thousand times)
BINS_PER_DECADE = 16                         # a bin is 10^(1/16) = 1.155 wide: the sleep level is known to +-7.5 %, which moves the level by under 4 % of sleep-to-peak
LOW_EXP, HIGH_EXP = -9, 1                    # 1 nA to 10 A: 10 decades, 160 bins, constant memory however long a session runs
BINS = (HIGH_EXP - LOW_EXP) * BINS_PER_DECADE
MAX_CAPTURE_BLOCKS = 25                      # 25 blocks x 20,000 words x 4 bytes = 2 MB a capture, 5 s
KEEP = 8                                     # the newest 8: 16 MB at most, enough to compare wake with wake
LEAD_BLOCKS_MAX = 5                          # the lead-in goes back at most 5 blocks (1 s, 400 KB of ring): a CPU awake for longer than that before its radio is a stage of its own, seen in the session's chart
POINTS_DEFAULT, POINTS_MAX = 1000, 2000      # a wide chart's pixels; the envelope's own bound (about twice that)
KEEP_DISK = 20                               # wakes kept on disk per recording: the stage table needs the spread across wakes and 20 shows it; worst case 20 x 2 MB before compression
MAX_STAGES = 12                              # a wake has a handful of stages: more would not fit a table a person reads
NAME_MAX = 40
ZLIB_LEVEL = 1                               # a wake is mostly flat, so the fastest level already shrinks it a lot, and the session thread must not stall (the kernel buffers 160 ms)


class Level(object):
    """The sleep level and the peak so far, from each block's mean and highest sample: sleep = the median of the means (a histogram, so memory is constant), peak = the highest."""

    def __init__(self):
        self.counts = [0] * BINS
        self.n = 0
        self.peak = 0.0

    def add(self, mean, high):
        if mean > 0:
            b = int((math.log10(mean) - LOW_EXP) * BINS_PER_DECADE)
            b = 0 if b < 0 else BINS - 1 if b >= BINS else b
        else:
            b = 0
        self.counts[b] += 1
        self.n += 1
        if high > self.peak:
            self.peak = high

    def sleep(self):
        """The middle bin's centre in amps, or None before anything was added or when the middle block read zero or less."""
        if not self.n:
            return None
        half, seen = (self.n + 1) // 2, 0
        for b, c in enumerate(self.counts):
            seen += c
            if seen >= half:
                return None if b == 0 else 10 ** ((b + 0.5) / BINS_PER_DECADE + LOW_EXP)
        return None

    def level(self):
        """Halfway from sleep to peak, or None when there is no wake yet (no sleep level, or a peak under twice it)."""
        s = self.sleep()
        if s is None or self.peak < WAKE_FACTOR * s:
            return None
        return s + (self.peak - s) / 2.0


class Capture(object):
    """A wake: in memory (chunks of raw words) or on disk (`loader` gives the words back, `samples` is what was saved)."""

    def __init__(self, n, t0, level, loader=None, samples=0):
        self.n, self.t0, self.level = n, t0, level            # the number, the sample clock of the first word, the level in force when it began
        self.chunks = []
        self.lost = 0
        self.truncated = False
        self.peak = 0.0
        self.blocks = 0
        self.stats = None                                     # wake_at_s, mean and peak of the decoded samples, worked out the first time they are asked for
        self._loader, self._samples = loader, samples

    @property
    def data(self):
        return self._loader() if self._loader is not None else b"".join(self.chunks)

    @property
    def samples(self):
        return self._samples if self._loader is not None else sum(len(c) for c in self.chunks) // 4


class Capturer(object):
    """Fed every block as the session reads it: `feed(data, t0, mean, high, lost)`. Holds the last block, and the newest KEEP captures."""

    def __init__(self, keep=KEEP, max_blocks=MAX_CAPTURE_BLOCKS):
        self.keep, self.max_blocks = keep, max_blocks
        self.level = Level()
        self.items = collections.deque(maxlen=keep)
        self.count = 0                                        # captures begun so far, which is also the number of the last
        self.kept = 0                                         # captures finished and kept: what the feed reports, so a page asks only once a wake can be read
        self._ring = collections.deque(maxlen=LEAD_BLOCKS_MAX)       # (data, t0, lost, mean) of the last blocks, newest last: where a capture's lead-in comes from
        self._open = None
        self.on_close = None                                  # called with each capture as it is kept (the profiler's source saves it to disk here)

    def feed(self, data, t0, mean, high, lost=0):
        self.level.add(mean, high)
        level = self.level.level()
        hot = level is not None and high > level
        o = self._open
        if o is None:
            if hot:
                self.count += 1
                lead = self._lead_in()
                o = self._open = Capture(self.count, lead[0][1] if lead else t0, level)
                for blk in lead:
                    self._add(o, blk[0], blk[2], high=None)
                self._add(o, data, lost, high)
        else:
            self._add(o, data, lost, high)
            if not hot:
                self._close()                                  # this was the first quiet block: the tail
            elif o.blocks >= self.max_blocks:
                o.truncated = True
                self._close()
        self._ring.append((data, t0, lost, mean))

    def _lead_in(self):
        """The blocks before the one that revealed the wake, oldest first: back through any that were already awake (a mean over twice the sleep level, like a CPU waking before its
        radio), to and including the first quiet one, and at most LEAD_BLOCKS_MAX. So a wake starts from sleep, not part-way through."""
        sleep = self.level.sleep()
        lead = []
        for blk in reversed(self._ring):
            lead.append(blk)
            if sleep is None or blk[3] <= WAKE_FACTOR * sleep:
                break
        lead.reverse()
        return lead

    def _add(self, o, data, lost, high):
        o.chunks.append(data)
        o.lost += lost
        o.blocks += 1
        if high is not None and high > o.peak:
            o.peak = high

    def _close(self):
        done, self._open = self._open, None
        self.items.append(done)
        if self.on_close is not None:
            self.on_close(done)                                # saved before it is counted: when the feed says it exists, it can be read
        self.kept += 1

    def get(self, n):
        for c in self.items:
            if c.n == n:
                return c
        return None

    def numbers(self):
        return [c.n for c in self.items]


# -- decoding -----------------------------------------------------------------------------------
def decode(data, amps, hold=0):
    """(the amps of each word in `data`, a multiple of 4 bytes, and how many were held). `amps(key)` is the calibration's conversion of the word's range and ADC bits.
    The unit's range switch puts a glitch in the first samples after it (measured on the RAK, 2026-11-20: 221 mA and 139 mA either side of a 120 mA edge, the plateau 120 mA), which
    the session's own reduction holds out (`hold` samples from the one where the range changes). Here the held samples take the value before them, so a wake keeps its length and
    its peak and charge are the session's."""
    words = array("I")
    words.frombytes(data)
    if sys.byteorder == "big":
        words.byteswap()
    if not hold:
        return array("d", [amps(w & WORD_MASK) for w in words]), 0
    out, last_range, skip, held, prev = [], None, 0, 0, None
    for w in words:
        key = w & WORD_MASK
        r = key >> 14
        if last_range is not None and r != last_range:
            skip = hold
        last_range = r
        if skip > 0:                                       # only ever set after the first sample, so there is a value before it to hold
            out.append(prev)
            skip -= 1
            held += 1
        else:
            prev = amps(key)
            out.append(prev)
    return array("d", out), held


def wake_start(values, level):
    """The index of the first sample over `level`, or 0 if none is. A wake's time origin is the first sample over half of its own highest sample (held out of the glitch), so every wake of a
    device is lined up on the same edge: the level in force when a capture began is not used, because it rose as the session's peak did (on the RAK, the first wake's origin was 0.2 s
    before its radio edge and the second's on it)."""
    for i, v in enumerate(values):
        if v > level:
            return i
    return 0


def window(values, i0, i1, points):
    """An exact envelope of values[i0:i1] in at most `points` buckets of equal width in samples: each bucket's lowest, highest, sum and count. Returns (buckets, width) with times as
    sample indexes (t0 inclusive, t1 exclusive) for the caller to turn into seconds. Nothing is thinned: a bucket's min and max are those of its samples."""
    n = i1 - i0
    width = max(1, -(-n // points))
    out = []
    for a in range(i0, i1, width):
        seg = values[a:min(a + width, i1)]
        out.append({"a": a, "b": a + len(seg), "min": min(seg), "max": max(seg), "mean": sum(seg) / len(seg), "n": len(seg)})
    return out, width


# -- wakes on disk, with a recording --------------------------------------------------------------
class WakeStore(object):
    """`<recording>.wakes/`: for each wake `<n>.bin` (its raw words, zlib) and `<n>.json` (t0, level, lost, truncated, peak, samples, stats); the newest `keep` are kept."""

    def __init__(self, directory, keep=KEEP_DISK):
        self.dir, self.keep = directory, keep

    def _path(self, n, suffix):
        return os.path.join(self.dir, "%d.%s" % (n, suffix))

    def save(self, c):
        """Write a capture (OSError if the disk will not take it). The json goes last and by rename, so a wake with a json always has its words."""
        os.makedirs(self.dir, exist_ok=True)
        raw = c.data
        with open(self._path(c.n, "bin"), "wb") as fh:
            fh.write(zlib.compress(raw, ZLIB_LEVEL))
        meta = {"n": c.n, "t0": c.t0, "level": c.level, "lost": c.lost, "truncated": c.truncated, "peak": c.peak, "samples": len(raw) // 4, "stats": c.stats}
        tmp = self._path(c.n, "json.tmp")
        with open(tmp, "w") as fh:
            json.dump(meta, fh)
        os.replace(tmp, self._path(c.n, "json"))
        self._prune()

    def numbers(self):
        try:
            return sorted(int(f[:-5]) for f in os.listdir(self.dir) if f.endswith(".json") and f[:-5].isdigit())
        except OSError:
            return []

    def _prune(self):
        for n in self.numbers()[:-self.keep]:
            for suffix in ("bin", "json"):
                try:
                    os.unlink(self._path(n, suffix))
                except OSError:
                    pass

    def _loader(self, n):
        def load():
            with open(self._path(n, "bin"), "rb") as fh:
                return zlib.decompress(fh.read())
        return load

    def load(self):
        """The saved wakes, oldest first, each reading its words only when asked."""
        out = []
        for n in self.numbers():
            try:
                with open(self._path(n, "json")) as fh:
                    m = json.load(fh)
                c = Capture(m["n"], m["t0"], m["level"], loader=self._loader(n), samples=m["samples"])
            except (OSError, ValueError, KeyError):
                continue
            c.lost, c.truncated, c.peak, c.stats = m.get("lost", 0), bool(m.get("truncated")), m.get("peak", 0.0), m.get("stats")
            out.append(c)
        return out

    def remember(self, c):
        """Write a capture's decoded figures back to its json, so listing it again does not decode it again."""
        try:
            with open(self._path(c.n, "json")) as fh:
                m = json.load(fh)
            m["stats"] = c.stats
            tmp = self._path(c.n, "json.tmp")
            with open(tmp, "w") as fh:
                json.dump(m, fh)
            os.replace(tmp, self._path(c.n, "json"))
        except (OSError, ValueError):
            pass


class WakeSet(object):
    """The wakes of one session (in memory) or of one finished recording (on disk): the list, an exact envelope of any stretch of one, and the stage table across all of them.
    `items()` gives the captures, `amps` is the calibration's conversion of a (range, ADC) key. One wake is held decoded at a time (500,000 doubles are 4 MB, twenty would be 80)."""

    def __init__(self, items, amps, remember=None, hold=0):
        self._items, self._amps, self._remember, self._hold = items, amps, remember, hold
        self._decoded, self._lock = None, threading.Lock()

    def numbers(self):
        return [c.n for c in self._items()]

    def get(self, n):
        for c in self._items():
            if c.n == n:
                return c
        return None

    def _values(self, c):
        with self._lock:
            if self._decoded is not None and self._decoded[0] is c:
                return self._decoded[1], self._decoded[2]
            values, held = decode(c.data, self._amps, self._hold)
            start = wake_start(values, max(values) / 2.0)
            self._decoded = (c, values, start)
            if c.stats is None or "held" not in c.stats:
                c.stats = {"wake_at_s": start / float(SAMPLE_RATE_HZ), "mean": sum(values) / len(values), "peak": max(values), "held": held}
                if self._remember is not None:
                    self._remember(c)
            return values, start

    def rows(self):
        out = []
        for c in self._items():
            if c.stats is None or "held" not in c.stats:
                self._values(c)
            out.append({"n": c.n, "t0": round(c.t0, 6), "wake_at_s": c.stats["wake_at_s"], "duration_s": c.samples / float(SAMPLE_RATE_HZ), "samples": c.samples,
                        "lost": c.lost, "truncated": c.truncated, "peak": c.stats["peak"], "mean": c.stats["mean"], "held": c.stats["held"]})
        return out

    def envelope(self, n, t0=None, t1=None, points=POINTS_DEFAULT):
        """An exact envelope of one wake: times in seconds from the wake's start. None for a number that is not held; ValueError for an empty window or bad points."""
        c = self.get(n)
        if c is None:
            return None
        if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= POINTS_MAX:
            raise ValueError("points is 2 to %d" % POINTS_MAX)
        values, start = self._values(c)
        rate = float(SAMPLE_RATE_HZ)
        i0 = 0 if t0 is None else max(0, start + int(math.floor(t0 * rate)))
        i1 = len(values) if t1 is None else min(len(values), start + int(math.ceil(t1 * rate)))
        if i1 <= i0:
            raise ValueError("the window holds no samples of capture %d (it runs from %g to %g s)" % (n, -start / rate, (len(values) - start) / rate))
        buckets, width = window(values, i0, i1, points)
        seg = values[i0:i1]
        return {"n": c.n, "wake_at_s": start / rate, "t0": (i0 - start) / rate, "t1": (i1 - start) / rate, "points": len(buckets), "width_s": width / rate,
                "buckets": [{"t0": (b["a"] - start) / rate, "t1": (b["b"] - start) / rate, "min": b["min"], "max": b["max"], "mean": b["mean"], "n": b["n"]} for b in buckets],
                "charge_c": sum(seg) / rate, "mean": sum(seg) / len(seg), "peak": max(seg), "samples": len(seg), "lost": c.lost, "truncated": c.truncated,
                "held": c.stats["held"]}

    def stage_table(self, stages, volts=None):
        """For each stage, over every wake: the stretch's mean current, peak and charge in each wake, and from them the mean, the spread across wakes, the peak, the mean charge and the
        share of the tagged stages' charge. Exact: from the decoded samples, not an envelope. A stage outside a capture contributes nothing from it."""
        rate = float(SAMPLE_RATE_HZ)
        per = [{"means": [], "peaks": [], "charges": []} for _ in stages]
        windows, tagged_each, wakes = [], [], 0
        for c in self._items():
            values, start = self._values(c)
            wakes += 1
            tagged = 0.0
            for k, s in enumerate(stages):
                i0 = max(0, start + int(round(s["start_s"] * rate)))
                i1 = min(len(values), start + int(round(s["end_s"] * rate)))
                if i1 <= i0:
                    continue
                seg = values[i0:i1]
                q = sum(seg) / rate
                per[k]["means"].append(sum(seg) / len(seg))
                per[k]["peaks"].append(max(seg))
                per[k]["charges"].append(q)
                tagged += q
            windows.append(sum(values) / rate)
            tagged_each.append(tagged)
        rows, total = [], 0.0
        for s, p in zip(stages, per):
            if p["charges"]:
                total += sum(p["charges"]) / len(p["charges"])
        for s, p in zip(stages, per):
            n = len(p["charges"])
            charge = sum(p["charges"]) / n if n else None
            rows.append({"name": s["name"], "start_s": s["start_s"], "end_s": s["end_s"], "length_s": s["end_s"] - s["start_s"], "wakes": n,
                         "mean": sum(p["means"]) / n if n else None, "lowest_mean": min(p["means"]) if n else None, "highest_mean": max(p["means"]) if n else None,
                         "peak": max(p["peaks"]) if n else None, "charge_c": charge,
                         "share": (charge / total) if n and total > 0 else None,
                         "energy_j": charge * volts if n and volts else None})
        untagged = [w - t for w, t in zip(windows, tagged_each)]
        return {"wakes": wakes, "stages": rows, "tagged_charge_c": total, "untagged_charge_c": sum(untagged) / len(untagged) if untagged else None}


# -- stages: named stretches of a wake, kept with the recording -----------------------------------
class StageError(ValueError):
    def __init__(self, message, field):
        ValueError.__init__(self, message)
        self.field = field


def set_stage(stages, name, start_s, end_s):
    """`stages` with the stage `name` added, or changed if one of that name (ignoring case) is there, in order of start. StageError for a rule broken."""
    name = (name if isinstance(name, str) else "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise StageError("a stage name is 1 to %d characters" % NAME_MAX, "name")
    for value, field in ((start_s, "start_s"), (end_s, "end_s")):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise StageError("%s is a number of seconds from the wake's start" % field, field)
    if not start_s < end_s:
        raise StageError("start_s must be before end_s", "end_s")
    others = [s for s in stages if s["name"].lower() != name.lower()]
    for s in others:
        if start_s < s["end_s"] and s["start_s"] < end_s:
            raise StageError("it overlaps the stage %s (%g to %g s)" % (s["name"], s["start_s"], s["end_s"]), "start_s")
    if len(others) + 1 > MAX_STAGES:
        raise StageError("at most %d stages" % MAX_STAGES, "name")
    return sorted(others + [{"name": name, "start_s": float(start_s), "end_s": float(end_s)}], key=lambda s: s["start_s"])


def remove_stage(stages, name):
    """`stages` without `name` (ignoring case), or None if there is no such stage."""
    kept = [s for s in stages if s["name"].lower() != (name or "").lower()]
    return kept if len(kept) != len(stages) else None


def load_stages(path):
    try:
        with open(path) as fh:
            rows = json.load(fh)["stages"]
        return [{"name": str(r["name"]), "start_s": float(r["start_s"]), "end_s": float(r["end_s"])} for r in rows]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_stages(path, stages):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"stages": stages}, fh)
    os.replace(tmp, path)
