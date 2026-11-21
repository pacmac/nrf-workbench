"""Any stretch of a recording as an exact envelope (docs/specs/meter-page.md, "Live and replay").

A recording's CSV has one row per reading: for the power profiler one per 0.2 s block with the block's exact lowest, highest and mean (`min`, `max`, `value`) and its sample count `n`;
for the multimeter one reading with `min == max == value`. `Trace` parses a file once into arrays; `window` answers any stretch of it in at most `points` buckets of equal width, each
bucket's lowest and highest being those of the rows in it and its mean time-weighted, so zooming in asks again and nothing is thinned. A row is the finest the file holds: a window
narrower than a row per point is answered at one row per point and says so (`resolution_s`); inside a saved wake the caller can do better from the wake's own samples (capture.py).
"""
import csv
import math
import os
from array import array

POINTS_DEFAULT, POINTS_MAX = 1000, 2000          # a wide chart's pixels; the bound of the other envelopes in this tool (capture.POINTS_MAX)
COLUMNS = {"t_s": 0, "wall": 1, "value": 2, "unit": 3, "function": 4, "overload": 7, "min": 8, "max": 9, "n": 10}


class Trace(object):
    """The rows of one recording. `ends[i]` is the time (seconds since the session began) a row's reading ended, `widths[i]` how long it covered: the gap to the row before it, and for the
    first row the whole time before it (the session began at 0)."""

    def __init__(self, ends, widths, lows, highs, means, counts, unit, function, wall0):
        self.ends, self.widths, self.lows, self.highs, self.means, self.counts = ends, widths, lows, highs, means, counts
        self.unit, self.function, self.wall0 = unit, function, wall0

    @property
    def rows(self):
        return len(self.ends)

    @property
    def start_s(self):
        return self.ends[0] - self.widths[0] if self.rows else 0.0

    @property
    def end_s(self):
        return self.ends[-1] if self.rows else 0.0

    @property
    def finest_s(self):
        """The narrowest a row is: how fine a window can be drawn from this file."""
        return min(self.widths) if self.rows else 0.0

    @classmethod
    def read(cls, path):
        """The trace of the file at `path`. OSError if it cannot be read; a row that does not parse, or that is an overload (it has no value), is left out."""
        ends, lows, highs, means, counts = array("d"), array("d"), array("d"), array("d"), array("d")
        unit = function = None
        wall0 = None
        with open(path, newline="") as fh:
            rows = csv.reader(fh)
            next(rows, None)                                  # the header
            for r in rows:
                try:
                    if len(r) > COLUMNS["overload"] and r[COLUMNS["overload"]] not in ("", "0"):
                        continue
                    t, v = float(r[COLUMNS["t_s"]]), float(r[COLUMNS["value"]])
                    lo = float(r[COLUMNS["min"]]) if len(r) > COLUMNS["min"] and r[COLUMNS["min"]] != "" else v
                    hi = float(r[COLUMNS["max"]]) if len(r) > COLUMNS["max"] and r[COLUMNS["max"]] != "" else v
                    n = float(r[COLUMNS["n"]]) if len(r) > COLUMNS["n"] and r[COLUMNS["n"]] != "" else 1.0
                    wall = float(r[COLUMNS["wall"]])
                except (ValueError, IndexError):
                    continue
                if not (math.isfinite(t) and math.isfinite(v) and math.isfinite(lo) and math.isfinite(hi)):
                    continue
                if ends and t <= ends[-1]:                    # time runs forward: a row that does not is damage
                    continue
                if unit is None:
                    unit, function = r[COLUMNS["unit"]], r[COLUMNS["function"]]
                    wall0 = wall - t
                ends.append(t)
                lows.append(lo)
                highs.append(hi)
                means.append(v)
                counts.append(n)
        widths = array("d", (e - (ends[i - 1] if i else 0.0) for i, e in enumerate(ends)))
        return cls(ends, widths, lows, highs, means, counts, unit, function, wall0)

    def window(self, t0=None, t1=None, points=POINTS_DEFAULT):
        """(buckets, stats, width_s) for the stretch [t0, t1) in seconds since the session began (default: the whole recording), or None if it holds no row. A bucket is
        {t0, t1, min, max, mean, n}; a row belongs to the bucket its middle falls in. `stats` is the stretch's lowest, highest, time-weighted mean, charge (mean x seconds), samples and
        seconds, from every row that overlaps it (each for the part inside the stretch), so it does not depend on the number of points. ValueError for points outside 2 to POINTS_MAX."""
        if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= POINTS_MAX:
            raise ValueError("points is 2 to %d" % POINTS_MAX)
        if not self.rows:
            return None
        lo_t = self.start_s if t0 is None else t0
        hi_t = self.end_s if t1 is None else t1
        first = _first_ending_after(self.ends, lo_t)
        last = _first_ending_after(self.ends, hi_t - 1e-12)         # rows ending at or before hi_t are in; the first that ends after it is the one that straddles it
        last = min(self.rows - 1, last)
        if first > last or hi_t <= lo_t:
            return None
        rows = range(first, last + 1)
        width = max((hi_t - lo_t) / points, self.finest_s)           # no finer than a row
        buckets = {}
        for i in rows:
            mid = self.ends[i] - self.widths[i] / 2.0
            k = int(math.floor((min(max(mid, lo_t), hi_t - 1e-12) - lo_t) / width))
            b = buckets.get(k)
            w = self.widths[i]
            if b is None:
                buckets[k] = [self.lows[i], self.highs[i], self.means[i] * w, w, self.counts[i]]
            else:
                if self.lows[i] < b[0]:
                    b[0] = self.lows[i]
                if self.highs[i] > b[1]:
                    b[1] = self.highs[i]
                b[2] += self.means[i] * w
                b[3] += w
                b[4] += self.counts[i]
        out = []
        for k in sorted(buckets):
            lo, hi, weighted, seconds, n = buckets[k]
            out.append({"t0": lo_t + k * width, "t1": lo_t + (k + 1) * width, "min": lo, "max": hi, "mean": weighted / seconds, "n": int(n)})
        # The figures count each row for the part of it inside the stretch, so a stretch that starts or ends part-way through a 0.2 s row does not count the whole of it.
        overlap = {i: min(self.ends[i], hi_t) - max(self.ends[i] - self.widths[i], lo_t) for i in rows}
        seconds = sum(overlap.values())
        charge = sum(self.means[i] * overlap[i] for i in rows)
        stats = {"lowest": min(self.lows[i] for i in rows), "highest": max(self.highs[i] for i in rows), "mean": charge / seconds, "charge": charge,
                 "samples": int(round(sum(self.counts[i] * overlap[i] / self.widths[i] for i in rows))), "seconds": seconds}
        return out, stats, width


def _first_ending_after(ends, t):
    """The index of the first row that ends after `t` (len(ends) if none): a binary search."""
    a, b = 0, len(ends)
    while a < b:
        m = (a + b) // 2
        if ends[m] > t:
            b = m
        else:
            a = m + 1
    return a


class TraceCache(object):
    """The newest trace parsed, kept while its file is unchanged (size and modification time): a person zooming asks the same file many times, and a 12 h recording is 216,000 rows."""

    def __init__(self):
        self._key, self._trace = None, None

    def get(self, path):
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime)
        if key != self._key:
            self._trace, self._key = Trace.read(path), key
        return self._trace

    def forget(self, path=None):
        if path is None or (self._key and self._key[0] == path):
            self._key, self._trace = None, None
