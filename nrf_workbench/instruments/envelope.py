"""A whole session's readings in bounded memory (docs/specs/meter-page.md, "The envelope"), and the statistics a power measurement needs.

The envelope is a list of buckets {t0, t1, min, max, sum, n}. It starts with one reading per bucket. When it holds MAX_BUCKETS, neighbours are merged in pairs and the
bucket width doubles, so memory never grows while nothing it reports is lost: a merge keeps the lowest, the highest, the count and the sum exactly (the mean is sum / n). A
short spike therefore survives as the edge of its band however long the session runs.
"""

# About twice the pixels of a wide chart (a 1,280 px window draws one bucket per pixel at most), so a chart is never drawn from fewer buckets than it has room for.
MAX_BUCKETS = 2000


class Envelope(object):
    def __init__(self, max_buckets=MAX_BUCKETS):
        if not isinstance(max_buckets, int) or max_buckets < 2:
            raise ValueError("an envelope needs room for at least 2 buckets")
        self.max, self.width = max_buckets, 1             # `width` is how many readings a bucket takes before the next one starts
        self.buckets = []
        self._cur = None

    def add(self, t, value, lo=None, hi=None, n=1):
        """One point. A multimeter reading is a point (n 1). A power profiler block stands for `n` samples whose mean is `value`, lowest `lo`, highest `hi`: the bucket
        keeps them exactly (its sum takes value * n), and `width` counts the points added (`k`), not the samples they stand for."""
        lo = value if lo is None else lo
        hi = value if hi is None else hi
        c = self._cur
        if c is None:
            self._cur = {"t0": t, "t1": t, "min": lo, "max": hi, "sum": value * n, "n": n, "k": 1}
        else:
            c["t1"] = t
            c["min"] = min(c["min"], lo)
            c["max"] = max(c["max"], hi)
            c["sum"] += value * n
            c["n"] += n
            c["k"] += 1
        if self._cur["k"] >= self.width:
            self.buckets.append(self._cur)
            self._cur = None
            if len(self.buckets) >= self.max:
                self._merge()

    def _merge(self):
        merged, b = [], self.buckets
        for i in range(0, len(b) - 1, 2):
            x, y = b[i], b[i + 1]
            merged.append({"t0": x["t0"], "t1": y["t1"], "min": min(x["min"], y["min"]), "max": max(x["max"], y["max"]),
                           "sum": x["sum"] + y["sum"], "n": x["n"] + y["n"], "k": x["k"] + y["k"]})
        if len(b) % 2:
            merged.append(b[-1])                           # an odd one out stays as it is: it is merged on the next round
        self.buckets = merged
        self.width *= 2

    def snapshot(self):
        """The buckets so far, with the one being filled, each as {t0, t1, min, max, mean, n, k}: `n` the readings or samples it stands for, `k` the points added to it (a page
        that carries on from a snapshot needs the width, which is the most points a bucket holds). Never more than max_buckets."""
        every = list(self.buckets) + ([self._cur] if self._cur is not None else [])
        return [{"t0": b["t0"], "t1": b["t1"], "min": b["min"], "max": b["max"], "mean": b["sum"] / b["n"], "n": b["n"], "k": b["k"]} for b in every]


class Stats(object):
    """readings, min, max, mean and, for a current, the charge: coulombs = amp-seconds, by the trapezoid rule over consecutive valid readings."""

    def __init__(self):
        self.n, self.min, self.max, self.sum, self.charge = 0, None, None, 0.0, 0.0
        self.overloads = 0
        self._last = None

    def add(self, t, value, lo=None, hi=None, n=1, dt=None):
        """A point (n 1, the charge by the trapezoid rule between points) or a block of `n` samples (`dt` seconds long, mean `value`, lowest `lo`, highest `hi`): then the
        charge is the block's mean times its duration, which is exact for the samples taken, and `n` counts samples."""
        lo = value if lo is None else lo
        hi = value if hi is None else hi
        self.n += n
        self.min = lo if self.min is None else min(self.min, lo)
        self.max = hi if self.max is None else max(self.max, hi)
        self.sum += value * n
        if dt is not None:
            self.charge += value * dt
        elif self._last is not None:
            lt, lv = self._last
            self.charge += (value + lv) / 2.0 * (t - lt)
        self._last = (t, value)

    def overload(self):
        self.overloads += 1

    def to_dict(self, current=False):
        out = {"readings": self.n, "overloads": self.overloads, "min": self.min, "max": self.max,
               "mean": (self.sum / self.n) if self.n else None}
        if current:
            out["charge_c"] = self.charge
            out["charge_mah"] = self.charge / 3.6              # 1 mAh = 3.6 C
        return out
