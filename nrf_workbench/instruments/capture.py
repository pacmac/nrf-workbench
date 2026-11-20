"""Single wakes kept at the unit's full sample rate (docs/specs/ppk2-stages.md, stage 1).

The session's envelope is 0.2 s a point; a wake's stages (a CPU wake, then a radio burst) are milliseconds. So as the profiler's blocks go by, a wake is noticed and its raw
words are kept: from the block before it to the first quiet block after. `Capturer` decides and holds; `decode` and `window` turn a capture's words into amps and into an exact
lowest/highest/mean envelope of any stretch of it, at any resolution.
"""
import collections
import math
import sys
from array import array

SAMPLE_RATE_HZ = 100000
WORD_MASK = 0x1FFFF                          # bits 0..13 the ADC, 14..16 the range (the rest are the counter and the logic inputs)
WAKE_FACTOR = 2                              # a wake must at least double the sleep current (the page's rule: noise is a few per cent, a radio burst a thousand times)
BINS_PER_DECADE = 16                         # a bin is 10^(1/16) = 1.155 wide: the sleep level is known to +-7.5 %, which moves the level by under 4 % of sleep-to-peak
LOW_EXP, HIGH_EXP = -9, 1                    # 1 nA to 10 A: 10 decades, 160 bins, constant memory however long a session runs
BINS = (HIGH_EXP - LOW_EXP) * BINS_PER_DECADE
MAX_CAPTURE_BLOCKS = 25                      # 25 blocks x 20,000 words x 4 bytes = 2 MB a capture, 5 s
KEEP = 8                                     # the newest 8: 16 MB at most, enough to compare wake with wake
POINTS_DEFAULT, POINTS_MAX = 1000, 2000      # a wide chart's pixels; the envelope's own bound (about twice that)


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
    def __init__(self, n, t0, level):
        self.n, self.t0, self.level = n, t0, level            # the number, the sample clock of the first word, the level in force when it began
        self.chunks = []
        self.lost = 0
        self.truncated = False
        self.peak = 0.0
        self.blocks = 0
        self.stats = None                                     # wake_at_s, mean and peak of the decoded samples, worked out the first time they are asked for

    @property
    def data(self):
        return b"".join(self.chunks)

    @property
    def samples(self):
        return sum(len(c) for c in self.chunks) // 4


class Capturer(object):
    """Fed every block as the session reads it: `feed(data, t0, mean, high, lost)`. Holds the last block, and the newest KEEP captures."""

    def __init__(self, keep=KEEP, max_blocks=MAX_CAPTURE_BLOCKS):
        self.keep, self.max_blocks = keep, max_blocks
        self.level = Level()
        self.items = collections.deque(maxlen=keep)
        self.count = 0                                        # captures taken so far, which is also the number of the last
        self._prev = None                                     # (data, t0, lost) of the block before the current one
        self._open = None

    def feed(self, data, t0, mean, high, lost=0):
        self.level.add(mean, high)
        level = self.level.level()
        hot = level is not None and high > level
        o = self._open
        if o is None:
            if hot:
                self.count += 1
                o = self._open = Capture(self.count, self._prev[1] if self._prev else t0, level)
                if self._prev:
                    self._add(o, self._prev[0], self._prev[2], high=None)
                self._add(o, data, lost, high)
        else:
            self._add(o, data, lost, high)
            if not hot:
                self._close()                                  # this was the first quiet block: the tail
            elif o.blocks >= self.max_blocks:
                o.truncated = True
                self._close()
        self._prev = (data, t0, lost)

    def _add(self, o, data, lost, high):
        o.chunks.append(data)
        o.lost += lost
        o.blocks += 1
        if high is not None and high > o.peak:
            o.peak = high

    def _close(self):
        self.items.append(self._open)
        self._open = None

    def get(self, n):
        for c in self.items:
            if c.n == n:
                return c
        return None

    def numbers(self):
        return [c.n for c in self.items]


# -- decoding -----------------------------------------------------------------------------------
def decode(data, amps):
    """The amps of each word in `data` (a multiple of 4 bytes), by the calibration's `amps(key)` where key is the word's range and ADC bits."""
    words = array("I")
    words.frombytes(data)
    if sys.byteorder == "big":
        words.byteswap()
    return array("d", [amps(w & WORD_MASK) for w in words])


def wake_start(values, level):
    """The index of the first sample over `level`, or 0 if none is (a capture is taken because one is, so that is not expected)."""
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
