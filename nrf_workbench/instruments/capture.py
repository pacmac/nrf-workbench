"""Single wakes kept at the unit's full sample rate (docs/specs/ppk2-stages.md, stage 1).

The session's envelope is 0.2 s a point; a wake's stages (a CPU wake, then a radio burst) are milliseconds. So as the profiler's blocks go by, a wake is noticed and its raw
words are kept: from sleep before it to the first block back near sleep after (a wake is the whole awake period, a radio burst and the receive window after it). `Capturer` decides and holds; `decode` and `window` turn a capture's words into amps and into an exact
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
WAKE_FACTOR = 2                              # a block is awake when its mean is over twice the sleep level (noise on a steady load is a few per cent, so 2 is far from it, and a radio is a thousand times)
BINS_PER_DECADE = 16                         # a bin is 10^(1/16) = 1.155 wide: the sleep level is known to +-7.5 %
LOW_EXP, HIGH_EXP = -9, 1                    # 1 nA to 10 A: 10 decades, 160 bins, constant memory however long a session runs
BINS = (HIGH_EXP - LOW_EXP) * BINS_PER_DECADE
SLEEP_QUANTILE = 0.10                        # the sleep level is the lowest tenth of the block means: the median would fail for a device awake over half the time, this fails only over nine tenths
MIN_SLEEP_BLOCKS = 20                        # 4 s of blocks before a sleep level is claimed: at 20 the tenth is the second lowest, so one stray low block cannot set it
BLOCK_S = 0.2                                # a profiler block: 20,000 samples at 100,000 a second, the finest grain at which a wake starts or ends
HOLD_S_DEFAULT = BLOCK_S                     # a wake ends at the first block back asleep: the finest the capture can see; longer is a choice (a device that dips to sleep inside one wake)
LEAD_S_DEFAULT = BLOCK_S                     # one block of sleep before the wake, so it starts from sleep
MAX_S_DEFAULT, MAX_S_MIN, MAX_S_MAX = 40.0, 1.0, 120.0       # the longest wake kept whole: the bench RAK is awake 21.6 s plus a lead-in, so 40 s is 1.8 times that; 120 s is 48 MB raw, 21 MB held, 167 MB for the newest 8, 4 % of a 4 GB Pi
KEEP = 8                                     # the newest 8, enough to compare wake with wake; held compressed (2.3 times smaller on the RAK), so 56 MB at 40 s, on a Pi with 3.8 GB
POINTS_DEFAULT, POINTS_MAX = 1000, 2000      # a wide chart's pixels; the envelope's own bound (about twice that)
KEEP_DISK = 20                               # wakes kept on disk per recording: the stage table needs the spread across wakes and 20 shows it; worst case 20 x 7 MB compressed (24 GB free on the Pi)
MAX_STAGES = 12                              # a wake has a handful of stages: more would not fit a table a person reads
MAX_EVERY = 8                                # a device that alternates between kinds of wake has two or three; 8 is room for more
NAME_MAX = 40
ZLIB_LEVEL = 1                               # a wake is mostly flat, so the fastest level already shrinks it a lot, and the session thread must not stall (the kernel buffers 160 ms): each block is
                                             # compressed as it arrives, 2.9 ms on the Pi (0.063 s for the 22 blocks of a 4.4 s wake, measured), where a 24 s wake compressed at its close takes 0.35 s


class ProfileError(ValueError):
    def __init__(self, message, field):
        ValueError.__init__(self, message)
        self.field = field


def _number(value, field, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProfileError("%s is a number (%s)" % (field, what), field)
    return float(value)


class WakeProfile(object):
    """What a wake is, for the device on the bench (docs/specs/ppk2-stages.md, "The wake profile"). Every field but the three times can be left to the data:
    `sleep_a` (amps; None = found as the lowest tenth of the block means), `awake_a` (amps; None = twice the sleep level), `hold_s` (a wake ends after this long back at or under the awake level;
    a shorter dip is part of the wake), `lead_s` (sleep kept before it), `max_s` (the longest wake kept whole)."""

    def __init__(self, sleep_a=None, awake_a=None, hold_s=None, lead_s=None, max_s=None):
        self.sleep_a = None if sleep_a is None else _number(sleep_a, "wake_sleep_a", "amps, above 0")
        self.awake_a = None if awake_a is None else _number(awake_a, "wake_awake_a", "amps, above 0")
        for value, field in ((self.sleep_a, "wake_sleep_a"), (self.awake_a, "wake_awake_a")):
            if value is not None and not value > 0:
                raise ProfileError("%s is above 0 amps" % field, field)
        if self.sleep_a is not None and self.awake_a is not None and not self.awake_a > self.sleep_a:
            raise ProfileError("wake_awake_a is above wake_sleep_a: a block cannot be awake at or under the sleep level", "wake_awake_a")
        self.max_s = MAX_S_DEFAULT if max_s is None else _number(max_s, "wake_max_s", "seconds")
        if not MAX_S_MIN <= self.max_s <= MAX_S_MAX:
            raise ProfileError("wake_max_s is %g to %g seconds" % (MAX_S_MIN, MAX_S_MAX), "wake_max_s")
        self.hold_s = HOLD_S_DEFAULT if hold_s is None else _number(hold_s, "wake_hold_s", "seconds")
        if not BLOCK_S - 1e-9 <= self.hold_s <= self.max_s:
            raise ProfileError("wake_hold_s is %g seconds (one block) up to the longest wake, %g" % (BLOCK_S, self.max_s), "wake_hold_s")
        self.lead_s = LEAD_S_DEFAULT if lead_s is None else _number(lead_s, "wake_lead_s", "seconds")
        if not 0 <= self.lead_s <= self.max_s:
            raise ProfileError("wake_lead_s is 0 up to the longest wake, %g seconds" % self.max_s, "wake_lead_s")

    @staticmethod
    def blocks(seconds):
        """Whole blocks that cover `seconds` (a hair of float error is not a block)."""
        return int(math.ceil(seconds / BLOCK_S - 1e-9))

    @property
    def hold_blocks(self):
        return max(1, self.blocks(self.hold_s))

    @property
    def lead_blocks(self):
        return self.blocks(self.lead_s)

    @property
    def max_blocks(self):
        return int(math.floor(self.max_s / BLOCK_S + 1e-9))

    def to_dict(self):
        return {"sleep_a": self.sleep_a, "awake_a": self.awake_a, "hold_s": self.hold_s, "lead_s": self.lead_s, "max_s": self.max_s}


class Level(object):
    """The sleep level from each block's mean: the lowest tenth of the blocks that read above zero (a histogram, so memory is constant). None until MIN_SLEEP_BLOCKS blocks have."""

    def __init__(self):
        self.counts = [0] * BINS
        self.n = 0

    def add(self, mean):
        if mean > 0:                                    # a block that reads zero or less says nothing about a level
            b = int((math.log10(mean) - LOW_EXP) * BINS_PER_DECADE)
            self.counts[0 if b < 0 else BINS - 1 if b >= BINS else b] += 1
            self.n += 1

    def sleep(self):
        """The centre in amps of the bin that holds the SLEEP_QUANTILE-th block, or None before MIN_SLEEP_BLOCKS blocks (or if that bin is the floor)."""
        if self.n < MIN_SLEEP_BLOCKS:
            return None
        want, seen = int(math.ceil(self.n * SLEEP_QUANTILE)), 0
        for b, c in enumerate(self.counts):
            seen += c
            if seen >= want:
                return None if b == 0 else 10 ** ((b + 0.5) / BINS_PER_DECADE + LOW_EXP)
        return None


class Capture(object):
    """A wake: in memory or on disk (`loader` gives the words back, `samples` is what was saved). In memory its words are compressed as each block joins (`add`), so a wake of 24 s
    costs about 3 ms a block instead of a stall at its close, and a third of the memory; `finish` closes the stream and `blob` is then the same bytes the store writes."""

    def __init__(self, n, t0, level, loader=None, samples=0):
        self.n, self.t0, self.level = n, t0, level            # the number, the sample clock of the first word, the level in force when it began
        self._z, self._pieces, self._words, self._blob = zlib.compressobj(ZLIB_LEVEL), [], 0, None
        self.lost = 0
        self.truncated = False
        self.partial = False                                  # its rising edge was not seen: the block before it was not judged asleep (a recording that opened on it, no level yet, or the rest of a wake the longest-wake cut ended)
        self.peak = 0.0
        self.blocks = 0
        self.stats = None                                     # wake_at_s, mean and peak of the decoded samples, worked out the first time they are asked for
        self._loader, self._samples = loader, samples

    def add(self, data):
        """One block's raw words, compressed now."""
        self._pieces.append(self._z.compress(data))
        self._words += len(data) // 4

    def finish(self):
        """Close the stream. Idempotent."""
        if self._blob is None and self._loader is None:
            self._pieces.append(self._z.flush())
            self._blob, self._pieces, self._z = b"".join(self._pieces), [], None

    @property
    def blob(self):
        """The wake as one zlib stream (what the store writes), or None for a wake read back from disk or one not finished."""
        return self._blob

    @property
    def data(self):
        if self._loader is not None:
            return self._loader()
        self.finish()
        return zlib.decompress(self._blob)

    @property
    def samples(self):
        return self._samples if self._loader is not None else self._words


class Capturer(object):
    """Fed every block as the session reads it: `feed(data, t0, mean, high, lost)`. A block is awake when its mean is over the awake level (`profile`, docs/specs/ppk2-stages.md): the first
    awake block opens a wake, with `lead_s` of sleep before it, and the wake ends at the first block that has been asleep for `hold_s` (one quiet block is kept as its tail), or when it
    reaches `max_s`. Holds the last `lead_s` of blocks, and the newest `keep` captures."""

    def __init__(self, profile=None, keep=KEEP):
        self.profile = profile or WakeProfile()
        self.keep = keep
        self.max_blocks, self.hold_blocks = self.profile.max_blocks, self.profile.hold_blocks
        self.level = Level()
        self.items = collections.deque(maxlen=keep)
        self.count = 0                                        # captures begun so far, which is also the number of the last
        self.kept = 0                                         # captures finished and kept: what the feed reports, so a page asks only once a wake can be read
        self.whole = 0                                        # captures closed that are whole at both ends: neither partial nor truncated (docs/specs/power-workbench.md, "Capture"). What stop_after_wakes counts
        self._prev_asleep = False                             # the block before the one being fed was judged asleep (a level was known and the block was under it): nothing before the first block
        self._ring = collections.deque(maxlen=self.profile.lead_blocks)      # (data, t0, lost, mean) of the last blocks, newest last: where a capture's lead-in comes from
        self._open = None
        self._pending = []                                    # (data, lost, high) of the quiet blocks of an open wake that have not yet lasted hold_s: part of it if it wakes again
        self._next = None                                     # a profile asked for from another thread: taken up at the start of the next block, so the session's thread is the only one that changes anything
        self.on_close = None                                  # called with each capture as it is kept (the profiler's source saves it to disk here)

    def request(self, profile):
        """Change the profile of a capture that is running (docs/specs/ppk2-stages.md, "The wake profile"): from the next block. Safe to call from any thread. A wake already open carries on
        under the new hold-off and longest wake; the levels found so far are kept, since they are the device's and not the profile's."""
        self._next = profile

    def _apply(self, profile):
        self.profile = profile
        self.max_blocks, self.hold_blocks = profile.max_blocks, profile.hold_blocks
        self._ring = collections.deque(list(self._ring)[-profile.lead_blocks:] if profile.lead_blocks else [], maxlen=profile.lead_blocks)

    def sleep_a(self):
        """The sleep level in force: the profile's, or the one found, or None while there is not enough data."""
        return self.profile.sleep_a if self.profile.sleep_a is not None else self.level.sleep()

    def awake_a(self):
        """The level a block's mean must pass to be awake: the profile's, or twice the sleep level, or None while there is no sleep level."""
        if self.profile.awake_a is not None:
            return self.profile.awake_a
        sleep = self.sleep_a()
        return None if sleep is None else WAKE_FACTOR * sleep

    def levels(self):
        """What the capture is using now, for the page to say in words: the two levels and whether each was set or found."""
        return {"sleep_a": self.sleep_a(), "sleep_auto": self.profile.sleep_a is None, "awake_a": self.awake_a(), "awake_auto": self.profile.awake_a is None}

    def feed(self, data, t0, mean, high, lost=0):
        nxt, self._next = self._next, None
        if nxt is not None:
            self._apply(nxt)
        self.level.add(mean)
        limit = self.awake_a()
        awake = limit is not None and mean > limit
        o = self._open
        if o is None:
            if awake:
                self.count += 1
                lead = list(self._ring)[-(self.max_blocks - 1):]          # the wake itself needs at least one block of the longest wake
                o = self._open = Capture(self.count, lead[0][1] if lead else t0, limit)
                o.partial = not self._prev_asleep
                self._pending = []
                for blk in lead:
                    self._add(o, blk[0], blk[2], high=None)
                self._add(o, data, lost, high)
                self._check_cap(o)
        elif awake:
            room = max(0, self.max_blocks - o.blocks)                 # a longest wake lowered under an open one leaves no room: it closes below
            for d, l, h in (self._pending + [(data, lost, high)])[:room]:         # a dip that did not last hold_s is part of the wake
                self._add(o, d, l, h)
            self._pending = []
            self._check_cap(o)
        else:
            self._pending.append((data, lost, high))
            if len(self._pending) >= self.hold_blocks:
                d, l, h = self._pending[0]
                self._add(o, d, l, h)                          # the first block back asleep is the tail; the rest of the quiet is not the wake's
                self._pending = []
                self._close()
        self._ring.append((data, t0, lost, mean))
        self._prev_asleep = limit is not None and not awake

    def _check_cap(self, o):
        if o.blocks >= self.max_blocks and self._open is o:
            o.truncated = True
            self._close()

    def _add(self, o, data, lost, high):
        o.add(data)
        o.lost += lost
        o.blocks += 1
        if high is not None and high > o.peak:
            o.peak = high

    def _close(self):
        done, self._open = self._open, None
        done.finish()
        self.items.append(done)
        if self.on_close is not None:
            self.on_close(done)                                # saved before it is counted: when the feed says it exists, it can be read
        self.kept += 1
        if not done.partial and not done.truncated:
            self.whole += 1

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
        c.finish()
        packed = c.blob if c.blob is not None else zlib.compress(c.data, ZLIB_LEVEL)
        with open(self._path(c.n, "bin"), "wb") as fh:
            fh.write(packed)
        meta = {"n": c.n, "t0": c.t0, "level": c.level, "lost": c.lost, "truncated": c.truncated, "partial": c.partial, "peak": c.peak, "samples": c.samples, "stats": c.stats}
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
            c.partial = bool(m.get("partial"))                 # a wake saved before this was recorded says nothing: it is read as whole
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

    def spans(self):
        """(n, t0, t1) for each wake on the session's clock: where its first sample falls and where its last does. Nothing is decoded."""
        rate = float(SAMPLE_RATE_HZ)
        return [(c.n, c.t0, c.t0 + c.samples / rate) for c in self._items()]

    def rows(self):
        out = []
        for c in self._items():
            if c.stats is None or "held" not in c.stats:
                self._values(c)
            out.append({"n": c.n, "t0": round(c.t0, 6), "wake_at_s": c.stats["wake_at_s"], "duration_s": c.samples / float(SAMPLE_RATE_HZ), "samples": c.samples,
                        "lost": c.lost, "truncated": c.truncated, "partial": c.partial, "peak": c.stats["peak"], "mean": c.stats["mean"], "held": c.stats["held"]})
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
        groups = {}                                          # (every, offset) -> the stages that share those wakes, and what each wake gave
        for k, s in enumerate(stages):
            groups.setdefault((s["every"], s["offset"]), {"stages": [], "numbers": [], "windows": [], "tagged": []})["stages"].append(k)
        per = [{"means": [], "peaks": [], "charges": [], "numbers": []} for _ in stages]
        items = self._items()
        for c in items:
            applicable = [key for key in groups if c.n % key[0] == key[1]]
            if not applicable:
                continue
            values, start = self._values(c)
            window = sum(values) / rate
            for key in applicable:
                g, tagged = groups[key], 0.0
                for k in g["stages"]:
                    s = stages[k]
                    i0 = max(0, start + int(round(s["start_s"] * rate)))
                    i1 = min(len(values), start + int(round(s["end_s"] * rate)))
                    if i1 <= i0:
                        continue
                    seg = values[i0:i1]
                    q = sum(seg) / rate
                    per[k]["means"].append(sum(seg) / len(seg))
                    per[k]["peaks"].append(max(seg))
                    per[k]["charges"].append(q)
                    per[k]["numbers"].append(c.n)
                    tagged += q
                g["numbers"].append(c.n)
                g["windows"].append(window)
                g["tagged"].append(tagged)
        totals = {}
        for key, g in groups.items():
            totals[key] = sum(sum(per[k]["charges"]) / len(per[k]["charges"]) for k in g["stages"] if per[k]["charges"])
        rows = []
        for s, p in zip(stages, per):
            n = len(p["charges"])
            charge = sum(p["charges"]) / n if n else None
            total = totals[(s["every"], s["offset"])]
            rows.append({"name": s["name"], "start_s": s["start_s"], "end_s": s["end_s"], "length_s": s["end_s"] - s["start_s"], "every": s["every"], "offset": s["offset"],
                         "wakes": n, "numbers": p["numbers"],
                         "mean": sum(p["means"]) / n if n else None, "lowest_mean": min(p["means"]) if n else None, "highest_mean": max(p["means"]) if n else None,
                         "peak": max(p["peaks"]) if n else None, "charge_c": charge,
                         "share": (charge / total) if n and total > 0 else None,
                         "energy_j": charge * volts if n and volts else None})
        out = []
        for key in sorted(groups):
            g = groups[key]
            rest = [w - t for w, t in zip(g["windows"], g["tagged"])]
            out.append({"every": key[0], "offset": key[1], "numbers": g["numbers"], "stages": [stages[k]["name"] for k in g["stages"]],
                        "tagged_charge_c": totals[key], "untagged_charge_c": sum(rest) / len(rest) if rest else None})
        return {"wakes": len(items), "stages": rows, "groups": out}


# -- stages: named stretches of a wake, kept with the recording -----------------------------------
class StageError(ValueError):
    def __init__(self, message, field):
        ValueError.__init__(self, message)
        self.field = field


def shares_a_wake(e1, o1, e2, o2):
    """Whether some wake number n has n % e1 == o1 and n % e2 == o2: when (o1 - o2) is a multiple of gcd(e1, e2) (the Chinese remainder theorem)."""
    return (o1 - o2) % math.gcd(e1, e2) == 0


def set_stage(stages, name, start_s, end_s, every=1, offset=0):
    """`stages` with the stage `name` added, or changed if one of that name (ignoring case) is there, in order of start. It applies to the wakes whose number n has n % every == offset.
    StageError for a rule broken."""
    name = (name if isinstance(name, str) else "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise StageError("a stage name is 1 to %d characters" % NAME_MAX, "name")
    for value, field in ((start_s, "start_s"), (end_s, "end_s")):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise StageError("%s is a number of seconds from the wake's start" % field, field)
    if not start_s < end_s:
        raise StageError("start_s must be before end_s", "end_s")
    if isinstance(every, bool) or not isinstance(every, int) or not 1 <= every <= MAX_EVERY:
        raise StageError("every is 1 to %d: the stage applies to every that-many wakes" % MAX_EVERY, "every")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset < every:
        raise StageError("offset is 0 to every - 1: which of the repeating wakes the stage is for", "offset")
    others = [s for s in stages if s["name"].lower() != name.lower()]
    for s in others:
        if start_s < s["end_s"] and s["start_s"] < end_s and shares_a_wake(every, offset, s["every"], s["offset"]):
            raise StageError("it overlaps the stage %s (%g to %g s) in the wakes they share" % (s["name"], s["start_s"], s["end_s"]), "start_s")
    if len(others) + 1 > MAX_STAGES:
        raise StageError("at most %d stages" % MAX_STAGES, "name")
    return sorted(others + [{"name": name, "start_s": float(start_s), "end_s": float(end_s), "every": every, "offset": offset}], key=lambda s: (s["start_s"], s["every"], s["offset"]))


def remove_stage(stages, name):
    """`stages` without `name` (ignoring case), or None if there is no such stage."""
    kept = [s for s in stages if s["name"].lower() != (name or "").lower()]
    return kept if len(kept) != len(stages) else None


def load_stages(path):
    try:
        with open(path) as fh:
            rows = json.load(fh)["stages"]
        return [{"name": str(r["name"]), "start_s": float(r["start_s"]), "end_s": float(r["end_s"]), "every": int(r.get("every", 1)), "offset": int(r.get("offset", 0))} for r in rows]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_stages(path, stages):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"stages": stages}, fh)
    os.replace(tmp, path)
