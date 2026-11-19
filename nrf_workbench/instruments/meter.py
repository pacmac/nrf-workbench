"""The LAN multimeter (docs/specs/meter.md): meter.show, meter.read, meter.sample.

Every line sent to the meter is a QUERY. The service writes no setting to it (no function, range, NPLC or trigger): changing the function while
the meter is in series would open the circuit and cut the supply to the board under test. The function is where the leads are plugged, which
software cannot see, so the person sets it and these commands report it.
"""
import threading
import time

from ..core import config
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result, SUSPECT
from . import scpi

MAINS_CYCLE_S = 0.02        # one cycle of the SLOWER mains frequency (50 Hz): NPLC x this is a reading's time, never under-estimated at 60 Hz
READ_OVERHEAD_S = 0.014     # what one READ? costs beyond the conversion on the SDM3045X (the first read answered in 14 ms, 2026-11-19)
NPLC_ASSUMED = 10           # the one NPLC measured on the meter: used when a function does not report its own
MAX_SPAN_S = 30             # a sample holds the service as long as a flash does (about 30 s on this bench)
MAX_COUNT = 1000            # memory: 1000 points of two floats is a small body
OVERLOAD = 9.9e37           # SCPI's overload value

# SCPI function name (what FUNC? answers, without its quotes) -> (expect_function word, unit, query prefix for range, auto-range and NPLC).
# Only CURR (DC current) and VOLT (DC volts) are measured on the real meter; the rest follow the same naming and are reported with an empty unit if wrong.
FUNCTIONS = {
    "VOLT": ("dcv", "V", "VOLT:DC"), "VOLT:AC": ("acv", "V", "VOLT:AC"),
    "CURR": ("dci", "A", "CURR:DC"), "CURR:AC": ("aci", "A", "CURR:AC"),
    "RES": ("res", "ohm", "RES"), "FRES": ("fres", "ohm", "FRES"),
    "FREQ": ("freq", "Hz", "FREQ"), "CAP": ("cap", "F", "CAP"),
}
EXPECT = sorted(v[0] for v in FUNCTIONS.values())
CURRENT = ("CURR", "CURR:AC")


def parse_address(text):
    """(host, port) from 'host' or 'host:port'. ValueError says what is wrong."""
    host, port = config.meter_address(text)
    return host, port if port is not None else scpi.SCPI_PORT


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def period_s(nplc):
    """Seconds one reading takes: its conversion (NPLC mains cycles) or the per-call overhead, whichever is longer."""
    return max((nplc if nplc else NPLC_ASSUMED) * MAINS_CYCLE_S, READ_OVERHEAD_S)


def reading_ceiling_s(nplc):
    """Give-up time for one READ?: twice the period (jitter, one slow conversion) and the query ceiling for the call itself."""
    return 2 * period_s(nplc) + scpi.CEILING_S


class Setting(object):
    """What the meter is set to now, asked (never changed)."""

    def __init__(self, conn):
        raw = (conn.ask("FUNC?") or "").strip().strip('"')
        self.function = raw
        spec = FUNCTIONS.get(raw.upper())
        self.word, self.unit, prefix = spec if spec else (None, "", None)
        self.range = self.autorange = self.nplc = None
        if prefix:
            self.range = _number(conn.ask(prefix + ":RANG?", silent_ok=True))
            auto = conn.ask(prefix + ":RANG:AUTO?", silent_ok=True)
            self.autorange = None if auto is None else auto.strip().upper() in ("1", "ON")
            self.nplc = _number(conn.ask(prefix + ":NPLC?", silent_ok=True))

    @property
    def in_series(self):
        return self.function.upper() in CURRENT

    def to_dict(self):
        return {"function": self.function, "word": self.word, "unit": self.unit, "range": self.range, "autorange": self.autorange,
                "nplc": self.nplc, "period_s": round(period_s(self.nplc), 4), "in_series": self.in_series}


class Meter(object):
    def __init__(self, host):
        self.host = host
        self._lock = threading.Lock()              # one caller on the meter at a time

    def _address(self):
        return parse_address(self.host.config["instruments"]["meter"])

    def _run(self, work):
        """Connect, run `work(conn)`, close. Maps the transport's failures onto results."""
        address, port = self._address()
        with self._lock:
            try:
                conn = scpi.Connection(address, port)
            except scpi.Unreachable as exc:
                return Result.failed("meter_unreachable", str(exc), {"meter": "%s:%d" % (address, port)})
            try:
                with conn:
                    return work(conn)
            except scpi.Silent as exc:
                return self._silent(address, port, exc)
            except scpi.Unreachable as exc:
                return Result.failed("meter_unreachable", str(exc), {"meter": "%s:%d" % (address, port)})

    def _silent(self, address, port, exc):
        """The meter was silent: ask a new connection what its error queue says, so the evidence is the meter's own words."""
        error = None
        try:
            with scpi.Connection(address, port) as fresh:
                error = fresh.ask("SYST:ERR?", silent_ok=True)
        except scpi.ScpiError:
            pass
        return Result.failed("no_reply", "%s. The meter took the line and did not answer (a query it does not know gets no reply)" % exc,
                             {"meter": "%s:%d" % (address, port), "query": exc.query, "ceiling_s": exc.ceiling, "error": error})

    # -- commands -----------------------------------------------------------------------------------
    def show(self, ctx):
        def work(conn):
            identity = conn.ask("*IDN?")
            setting = Setting(conn)
            error = conn.ask("SYST:ERR?", silent_ok=True)
            data = dict(setting.to_dict(), reachable=True, identity=identity, error=error)
            note = "; set to current: in series with the circuit if the leads are in the current jacks" if setting.in_series else ""
            return Result.success(data, "%s, set to %s%s" % (identity, setting.function or "an unknown function", note))
        return self._run(work)

    def _expect(self, setting, expect_function):
        """None, or the refusal: the meter is not set to the function the caller thinks it is reading."""
        if expect_function is None:
            return None
        if expect_function not in EXPECT:
            raise UsageError("expect_function is one of: %s" % ", ".join(EXPECT), "expect_function")
        if setting.word == expect_function:
            return None
        return Result.refused("meter_function", "the meter is set to %s, not %s: nothing was read. The function is where the leads are plugged, "
                              "so it is set at the meter's front panel" % (setting.function or "an unknown function", expect_function),
                              {"function": setting.function, "word": setting.word, "expected": expect_function})

    def _reading(self, conn, setting):
        raw = conn.ask("READ?", ceiling=reading_ceiling_s(setting.nplc))
        value = _number(raw)
        return raw, value

    def read(self, ctx, expect_function=None):
        def work(conn):
            setting = Setting(conn)
            refusal = self._expect(setting, expect_function)
            if refusal is not None:
                return refusal
            raw, value = self._reading(conn, setting)
            if value is None:
                return Result.failed("no_reply", "the meter answered %r, which is not a number" % raw, {"raw": raw})
            data = dict(setting.to_dict(), value=value, raw=raw, overload=abs(value) >= OVERLOAD)
            if data["overload"]:
                return Result(SUSPECT, "meter_overload", "the meter reads overload (%s) on its %s range: the range is too small"
                              % (raw, setting.range), data, {"raw": raw, "range": setting.range})
            return Result.success(data, "%g %s" % (value, setting.unit or "(unit not known for %s)" % setting.function))
        return self._run(work)

    def sample(self, ctx, count, expect_function=None):
        if isinstance(count, bool) or not 1 <= count <= MAX_COUNT:
            raise UsageError("count is 1 to %d" % MAX_COUNT, "count")

        def work(conn):
            setting = Setting(conn)
            refusal = self._expect(setting, expect_function)
            if refusal is not None:
                return refusal
            period = period_s(setting.nplc)
            if count * period > MAX_SPAN_S:
                raise UsageError("%d readings at %.3f s each is %.1f s: a sample is at most %d s, so at most %d readings with the meter set as it is"
                                 % (count, period, count * period, MAX_SPAN_S, int(MAX_SPAN_S / period)), "count")
            points, overloads, t0 = [], 0, None
            for _ in range(count):
                raw, value = self._reading(conn, setting)
                now = time.monotonic()
                t0 = now if t0 is None else t0
                if value is None:
                    return Result.failed("no_reply", "the meter answered %r, which is not a number (after %d readings)" % (raw, len(points)),
                                         {"raw": raw, "points": points})
                if abs(value) >= OVERLOAD:
                    overloads += 1
                points.append({"t_s": round(now - t0, 3), "value": value})
            good = [p["value"] for p in points if abs(p["value"]) < OVERLOAD]
            data = dict(setting.to_dict(), points=points, n=len(points), overloads=overloads,
                        min=min(good) if good else None, max=max(good) if good else None,
                        mean=(sum(good) / len(good)) if good else None, duration_s=points[-1]["t_s"],
                        clock="t_s is when each reading ARRIVED on this host, from the first; the meter stamps nothing")
            if overloads:
                return Result(SUSPECT, "meter_overload", "%d of %d readings were overload: the range is too small" % (overloads, count), data,
                              {"range": setting.range})
            return Result.success(data, "%d readings, mean %g %s" % (len(points), data["mean"], setting.unit))
        return self._run(work)


def register(registry, host):
    meter = Meter(host)

    def add(command):
        registry.add(command, module=__name__)

    function = Arg("expect_function", required=False,
                   doc="refuse unless the meter is set to this function: %s" % ", ".join(EXPECT))
    add(Command("meter.show", "Ask the multimeter what it is and how it is set (function, range, auto-range, NPLC). Measures nothing and changes nothing.",
                meter.show, requires=("meter",), result="reachable, identity, function, unit, range, autorange, nplc, period_s, in_series, error"))
    add(Command("meter.read", "One reading under the meter's present setting. The service never changes the setting: the function is where the leads are plugged.",
                meter.read, args=(function,), requires=("meter",), result="value, unit, function, range, nplc, raw, overload"))
    add(Command("meter.sample", "Several readings in a row, timestamped when each arrives, with min, max and mean. At most 1000, and at most 30 s of readings.",
                meter.sample, args=(Arg("count", type="int", doc="how many readings, 1 to 1000 (and at most 30 s at the meter's NPLC)"), function),
                requires=("meter",), result="points (t_s, value), n, min, max, mean, unit, period_s, duration_s"))
