"""What a session needs from an instrument (docs/specs/ppk2.md): the multimeter and the power profiler both answer to this and nothing else.

A source is begun once, asked for one `Reading` at a time, and ended. A fault it cannot carry on past is raised as `Fault` with the words the person will read; the
session ends with them (state `error`), never papers over them.
"""


class Fault(Exception):
    """The reason a session ends: the instrument went silent, was unreachable, or answered something that is not a measurement."""


class Reading(object):
    """One point of a session. A multimeter reading is a point (lo == hi == value, n == 1, dt None); a power profiler block is the exact lowest, highest, mean and
    count of the samples it covers, `dt` seconds long."""
    __slots__ = ("t", "value", "lo", "hi", "n", "dt", "function", "unit", "range", "nplc", "overload", "facts")

    def __init__(self, t, value, function, unit, range=None, nplc=None, lo=None, hi=None, n=1, dt=None, overload=False, facts=None):
        self.t, self.value, self.function, self.unit, self.range, self.nplc = t, value, function, unit, range, nplc
        self.lo = value if lo is None else lo
        self.hi = value if hi is None else hi
        self.n, self.dt, self.overload = n, dt, overload
        self.facts = facts or {}                    # what only this source knows about the session so far (the profiler's lost and filtered samples)
