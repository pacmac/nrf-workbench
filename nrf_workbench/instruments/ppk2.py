"""The Nordic Power Profiler Kit II (docs/specs/ppk2.md): ppk2.show, ppk2.mode, ppk2.supply, and a source for meter sessions.

The unit enumerates two serial ports; interface 01 speaks the PPK2 protocol and interface 03 is a Zephyr shell that is never opened. Measuring sends only the metadata
query, start and stop. Mode, voltage and the supply switch are writes to the unit and sit behind a gate: in source mode the PPK2 powers the board, and the service
cannot see what is wired. Every byte written passes `_frame`, which knows exactly six shapes.
"""
import collections
import os
import select
import sys
import threading
import time
from array import array

from ..console import port as portlib
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from ..devices import identity
from . import scpi
from .source import Fault, Reading

VENDOR_PRODUCT = "1915:c00a"
PROTOCOL_INTERFACE = 1                      # interface 03 is a shell
SAMPLE_RATE_HZ = 100000                     # measured 2026-11-20: 99,466 words in the first 1.007 s
BLOCK_SAMPLES = 20000                       # 0.2 s: the multimeter's period at NPLC 10, so the feed rate, retention and file size are the ones meter-page.md derived
BLOCK_S = BLOCK_SAMPLES / float(SAMPLE_RATE_HZ)
META_CEILING_S = 0.5                        # the metadata answer took 0.2 s: 2.5 x
BLOCK_CEILING_S = 2 * BLOCK_S + scpi.CEILING_S      # two blocks of stream and the call ceiling the multimeter uses: a block later than this is a silent unit
DRAIN_QUIET_S = 0.05                        # the 768 bytes still in flight after a stop took under 10 ms: a 50 ms read that returns nothing means it is over
DRAIN_MAX_S = 1.0                           # a stream that never stops is given up on after this
SPIKE_SAMPLES = 3                           # samples held after a range change, as the reference library does (unproven on a real load: docs/specs/ppk2.md)
RATE_LOW_HZ = 98000                         # 2 % under the 100,000 the unit sends (measured 99,466)
RATE_AFTER_S = 1.0                          # a rate is judged only after a second of stream
PORT_TIMEOUT_S = 0.05                       # one wait for bytes: a quarter of a block, so a stop is seen within a block
NOMINAL_BAUD = 9600
# The line discipline's read buffer is 4,096 bytes and the stream is 400,000 bytes a second: it fills in 10.2 ms. Waiting 8 ms after the port turns readable leaves 3.2 kB,
# under the buffer with 2 ms to spare for the sleep to overshoot; anything left over stays in the kernel's larger flip buffer (64 kB, 160 ms), which a block never gets near.
READ_PACE_S = 0.008
READ_MAX = 65536
ADC_MULT = 1.8 / 163840
VOLT_MIN_MV, VOLT_MAX_MV = 800, 5000
VOLT_OFFSET, VOLT_BASE_HI = 32, 3           # the regulator's encoding in the reference library: the pair is (3 + n // 256, n % 256) for n = mV - 800 + 32
RANGES = 5
MODES = {1: "ampere", 2: "source"}          # metadata `mode`: 2 was read from the unit (source, VDD 4000); 1 is the reference library's ampere value, unproven here

META, START, STOP, MODE, POWER, VOLTAGE = 0x19, 0x06, 0x07, 0x11, 0x0C, 0x0D
AMPERE_BYTE, SOURCE_BYTE = 1, 2
WORD_MASK = 0x1FFFF                         # bits 0..13 the ADC, 14..16 the range
ADC_MASK = 0x3FFF
# Byte tables for bytes.translate, which does a whole block in C: the range is bits 14, 15 (byte 1, bits 6, 7) and 16 (byte 2, bit 0), the ADC's top bits are byte 1's low 6,
# the counter is byte 2's bits 2..7, and a counter run is the byte (i & 63) << 2 over and over.
_TOP2 = bytes(b >> 6 for b in range(256))
_LOW6 = bytes(b & 0x3F for b in range(256))
_BIT0 = bytes(b & 1 for b in range(256))
_NO_RANGE = bytes(b & 0xFC for b in range(256))
_COUNTER_RUN = bytes(range(0, 256, 4)) * (2 * BLOCK_SAMPLES // 64 + 2)


class Ppk2Error(Exception):
    pass


VOLTAGE_HI_MIN = VOLT_BASE_HI
VOLTAGE_HI_MAX = VOLT_BASE_HI + (VOLT_MAX_MV - VOLT_MIN_MV + VOLT_OFFSET) // 256


def _frame(*frame):
    """The bytes to write, only if they are one of the six shapes the module may send; anything else is a programming error and never reaches the port."""
    ok = (frame in ((META,), (START,), (STOP,), (MODE, AMPERE_BYTE), (MODE, SOURCE_BYTE), (POWER, 0), (POWER, 1))
          or (len(frame) == 3 and frame[0] == VOLTAGE and VOLTAGE_HI_MIN <= frame[1] <= VOLTAGE_HI_MAX and 0 <= frame[2] <= 255))
    if not ok:
        raise ValueError("%r is not a PPK2 command this module sends" % (frame,))
    return bytes(bytearray(frame))


def voltage_frame(mv):
    n = mv - VOLT_MIN_MV + VOLT_OFFSET
    return _frame(VOLTAGE, VOLT_BASE_HI + n // 256, n % 256)


# -- metadata and the conversion -------------------------------------------------------------------
class Calibration(object):
    def __init__(self, values):
        try:
            for name in ("R", "GS", "GI", "O", "S", "I", "UG"):
                setattr(self, name, [values["%s%d" % (name, i)] for i in range(RANGES)])
            self.vdd_mv = values["VDD"]
        except KeyError as exc:
            raise Ppk2Error("the metadata has no %s" % exc.args[0])
        if any(r == 0 for r in self.R):
            raise Ppk2Error("the metadata gives a shunt resistance of 0")
        self.calibrated = bool(values.get("Calibrated"))
        self.hw = values.get("HW")
        self.mode = MODES.get(int(values["mode"])) if "mode" in values else None
        self._memo = {}

    def amps(self, key):
        """Amps for a (range, ADC) word key, memoised: at most 5 x 16,384 different keys exist, so the memory is bounded and a steady load costs nothing."""
        v = self._memo.get(key)
        if v is None:
            r = min(key >> 14, RANGES - 1)
            x = ((key & ADC_MASK) * 4 - self.O[r]) * (ADC_MULT / self.R[r])
            v = self.UG[r] * (x * (self.GS[r] * x + self.GI[r]) + (self.S[r] * (self.vdd_mv / 1000.0) + self.I[r]))
            self._memo[key] = v
        return v


def parse_metadata(text):
    values = {}
    for line in text.split("\n"):
        key, sep, rest = line.partition(": ")
        if sep:
            try:
                values[key.strip()] = float(rest)
            except ValueError:
                pass
    return Calibration(values)


# -- the stream ----------------------------------------------------------------------------------
class Reducer(object):
    """Turns the stream, 0.2 s at a time, into the exact lowest, highest, mean and count of each block, in a way that costs about 17 ms a block on the bench Pi."""

    def __init__(self, cal):
        self.cal = cal
        self.words = self.lost = self.filtered = 0           # received, lost to the counter, left out after a range change
        self.prev_range, self.prev_counter, self.skip = None, None, 0

    def _lost(self, masked, n):
        """Samples the host never got in this block, from the counter (bits 18..23, one step a sample, wrapping at 64). `masked` is each word's third byte with the range and
        spare bits cleared. A block with no loss is the counter run on from its first value, and that is one comparison in C; only a block that differs is walked sample by sample."""
        first = masked[0] >> 2
        lost = 0
        if masked != _COUNTER_RUN[first:first + n]:
            cs = [b >> 2 for b in masked]
            lost = sum((q - p - 1) & 63 for p, q in zip(cs, cs[1:]))
        if self.prev_counter is not None:
            lost += (first - self.prev_counter - 1) & 63
        self.prev_counter = masked[-1] >> 2
        return lost

    def _blockwise(self, data, n):
        """A block in one range (a steady load: the common case): the ADC values are counted in C with no per-sample Python, and a range change can only be at the
        block's start. Returns (counts of (range, ADC) keys, the samples to hold out and the ADC values they had, the range), or None if the block has more than one range."""
        b1, b2 = data[1::4], data[2::4]
        low, high = b1.translate(_TOP2), b2.translate(_BIT0)
        if low.count(low[:1]) != n or high.count(high[:1]) != n:
            return None
        rng = low[0] | (high[0] << 2)
        until = self.skip
        if self.prev_range is not None and rng != self.prev_range:
            until = max(until, SPIKE_SAMPLES)
        self.skip = max(0, until - n)
        self.prev_range = rng
        buf = bytearray(2 * n)
        buf[0::2] = data[0::4]
        buf[1::2] = b1.translate(_LOW6)
        adc = array("H")
        adc.frombytes(bytes(buf))
        if sys.byteorder == "big":
            adc.byteswap()
        counts = collections.Counter(adc)
        dropped = min(until, n)
        for i in range(dropped):
            counts[adc[i]] -= 1
        offset = rng << 14
        return {offset | a: c for a, c in counts.items()}, dropped, rng

    def _samplewise(self, data, n):
        """A block whose range changes inside it: every sample is looked at, and the 3 after each change are held out, across a block boundary too."""
        words = array("I")
        words.frombytes(data)
        if sys.byteorder == "big":
            words.byteswap()
        keys = [w & WORD_MASK for w in words]
        rs = [k >> 14 for k in keys]
        drop = set()
        until = self.skip
        if until:
            drop.update(range(min(until, n)))
        last = self.prev_range
        for i, r in enumerate(rs):
            if r != last:
                if last is not None:
                    until = max(until, i + SPIKE_SAMPLES)
                    drop.update(range(i, min(i + SPIKE_SAMPLES, n)))
                last = r
        self.skip = max(0, until - n) if until > n else 0
        self.prev_range = rs[-1]
        counts = collections.Counter(keys)
        for i in drop:
            counts[keys[i]] -= 1
        return counts, len(drop), max(rs)

    def block(self, data):
        """The block's facts for `data`, a multiple of 4 bytes: t, mean, lo, hi, n, range, dt; or None if every sample in it was filtered."""
        n = len(data) // 4
        lost = self._lost(data[2::4].translate(_NO_RANGE), n)
        fast = self._blockwise(data, n)
        counts, held, top = fast if fast is not None else self._samplewise(data, n)
        kept = n - held
        drop = range(held)
        self.words += n
        self.lost += lost
        self.filtered += len(drop)
        total_words = self.words + self.lost
        t = total_words / float(SAMPLE_RATE_HZ)
        if kept <= 0:
            return None
        lo = hi = None
        total = 0.0
        amps = self.cal.amps
        for key, c in counts.items():
            if c > 0:
                v = amps(key)
                total += v * c
                if lo is None or v < lo:
                    lo = v
                if hi is None or v > hi:
                    hi = v
        return {"t": t, "mean": total / kept, "lo": lo, "hi": hi, "n": kept, "range": min(top, RANGES - 1), "dt": (n + lost) / float(SAMPLE_RATE_HZ)}


# -- the instrument -----------------------------------------------------------------------------
class Link(object):
    """The PPK2's measuring port, opened through the console's port module: the one place a port is opened, locked, made raw, and closed without dropping DTR. The baud is
    nominal on a USB CDC port. `read(size)` waits up to PORT_TIMEOUT_S for bytes and gives back at most `size`, keeping any surplus for the next call so a block never
    starts mid-word. Its failures are OSError, as a library's would be."""

    def __init__(self, node):
        try:
            self.port = portlib.Port(node, NOMINAL_BAUD)
        except portlib.PortError as exc:
            raise OSError(str(exc))
        self._extra = b""

    def read(self, size):
        if not self._extra:
            fd = self.port.fileno()
            if not select.select([fd], [], [], PORT_TIMEOUT_S)[0]:
                return b""
            # The kernel hands a USB serial port's bytes over in 128-byte pieces: 6,250 reads in 2 s on the Pi, 8 % of a core. Waiting READ_PACE_S lets about 3 kB gather so a
            # read returns that much (measured below).
            time.sleep(READ_PACE_S)
            try:
                self._extra = os.read(fd, READ_MAX)
            except BlockingIOError:
                return b""
            except OSError as exc:
                raise OSError("the port went away (%s)" % exc.strerror)
            if not self._extra:
                raise OSError("the port hung up")
        data, self._extra = self._extra[:size], self._extra[size:]
        return data

    def write(self, data):
        try:
            self.port.write_all(data, portlib.write_bound_s(len(data), NOMINAL_BAUD))
        except portlib.PortError as exc:
            raise OSError(str(exc))

    def close(self):
        self.port.close()


def default_opener(node):
    return Link(node)


class Ppk2(object):
    def __init__(self, host, monotonic=time.monotonic):
        self.host, self._mono = host, monotonic
        self._lock = threading.Lock()
        self._busy = False
        self._supply = None                                 # True / False once THIS service switched it; None is unknown (the unit does not report it)
        self._vdd = None                                    # the source voltage this service set, in mV; None until it does (the unit does not report it back)

    def _setting(self):
        return self.host.config["instruments"]["ppk2"]

    def _opener(self):
        return self.host.hooks.get("ppk2_open") or default_opener

    def locate(self):
        """(node, serial, None) or (None, None, the Result that says why not)."""
        setting = self._setting()
        found = [d for d in identity.inventory(self.host.sysfs) if d["id"] == VENDOR_PRODUCT]
        if setting != "auto":
            found = [d for d in found if d["serial"] == setting]
        if not found:
            return None, None, Result.failed("ppk2_unreachable", "no PPK2 is attached%s" % ("" if setting == "auto" else " with the serial %s" % setting),
                                             {"setting": setting})
        if len(found) > 1:
            return None, None, Result.refused("two_candidates", "%d PPK2s are attached: set instruments.ppk2 to the serial of the one to use" % len(found),
                                              {"serials": [d["serial"] for d in found]})
        dev = found[0]
        reasons = []
        for name in sorted(os.listdir(dev["path"])):
            if not name.startswith(dev["name"] + ":"):
                continue
            number = identity._read(os.path.join(dev["path"], name, "bInterfaceNumber"))
            try:
                if number is None or int(number, 16) != PROTOCOL_INTERFACE:
                    continue
            except ValueError:
                continue
            node, why = identity.node_for({"path": os.path.join(dev["path"], name)}, self.host.devroot)
            if node:
                return node, dev["serial"], None
            reasons.append(why)
        return None, None, Result.failed("ppk2_unreachable", "the PPK2 %s has no serial port for its measuring interface (%s)"
                                         % (dev["serial"], "; ".join(reasons) or "interface 01 not found"), {"serial": dev["serial"]})

    # -- one conversation: open, end any stream a dead process left, ask --------------------------------
    def _drain(self, port):
        end = self._mono() + DRAIN_MAX_S
        while self._mono() < end:
            if not port.read(65536):
                return

    def _meta(self, port):
        port.write(_frame(META))
        end = self._mono() + META_CEILING_S
        text = b""
        while b"END" not in text and self._mono() < end:
            text += port.read(4096)
        if b"END" not in text:
            raise Ppk2Error("the PPK2 did not answer the metadata query within %.1f s" % META_CEILING_S)
        return parse_metadata(text.decode("utf-8", "replace"))

    def _ready(self, port):
        """Stop any stream a dead process left, drain it, then read the calibration: the unit is quiet and known."""
        port.write(_frame(STOP))
        self._drain(port)
        return self._meta(port)

    def _converse(self, work):
        """Locate, open, run `work(port)`, close; under the one-caller lock and never during a session. A Result comes back for any failure."""
        with self._lock:
            if self._busy:
                return Result.refused("meter_busy", "a session is using the PPK2: its mode and supply are in meter.session until it ends", {})
            node, serial, problem = self.locate()
            if problem is not None:
                return problem
            try:
                port = self._opener()(node)
            except (OSError, ValueError) as exc:
                return Result.failed("ppk2_unreachable", "the PPK2's port %s would not open: %s" % (node, exc), {"port": node})
            try:
                return work(port, node, serial)
            except Ppk2Error as exc:
                return Result.failed("no_reply", str(exc), {"port": node})
            except OSError as exc:
                return Result.failed("ppk2_unreachable", "the PPK2's port %s failed: %s" % (node, exc), {"port": node})
            finally:
                try:
                    port.close()
                except OSError:
                    pass

    def _supply_word(self):
        return "unknown" if self._supply is None else ("on" if self._supply else "off")

    def _facts(self, cal, node, serial):
        return {"reachable": True, "serial": serial, "port": node, "mode": cal.mode or "unknown",
                "vdd_mv": self._vdd if self._vdd is not None else cal.vdd_mv, "vdd_from": "set by this service" if self._vdd is not None else "the unit's own figure when it was read",
                "calibrated": cal.calibrated, "hw": cal.hw, "supply": self._supply_word()}

    # -- commands -------------------------------------------------------------------------------------
    def show(self, ctx):
        def work(port, node, serial):
            cal = self._ready(port)
            data = self._facts(cal, node, serial)
            return Result.success(data, "PPK2 %s, %s mode, %g mV%s" % (serial, data["mode"], cal.vdd_mv, "" if cal.calibrated else ", not calibrated by the user"))
        return self._converse(work)

    def set_mode(self, ctx, mode, accept_supply=False):
        if mode not in ("ampere", "source"):
            raise UsageError("mode is ampere or source", "mode")

        def work(port, node, serial):
            cal = self._ready(port)
            if cal.mode == mode:
                return Result.success(self._facts(cal, node, serial), "already in %s mode: nothing was written" % mode)
            if mode == "ampere" and self._supply is not False and not accept_supply:
                return Result.refused("supply_unconfirmed", "the PPK2 is in %s mode and %s: leaving source mode can cut the power to the board it supplies. "
                                      "Say accept_supply when that is meant" % (cal.mode or "an unknown", "may be powering the board" if self._supply is None else "is powering the board"),
                                      {"mode": cal.mode or "unknown", "supply": self._supply_word()})
            port.write(_frame(MODE, AMPERE_BYTE if mode == "ampere" else SOURCE_BYTE))
            after = self._ready(port)
            if after.mode != mode:
                return Result.failed("no_reply", "the PPK2 was told %s mode and reports %s" % (mode, after.mode or "an unknown mode"), {"mode": after.mode})
            # Ampere mode has no output. Coming back into source mode the unit may resume an output it had on before and does not say so, so it is unknown again.
            self._supply = False if mode == "ampere" else None
            return Result.success(self._facts(after, node, serial), "%s mode" % mode)
        return self._converse(work)

    def set_supply(self, ctx, on, mv=None, accept_supply=False):
        if mv is not None and (isinstance(mv, bool) or not VOLT_MIN_MV <= mv <= VOLT_MAX_MV):
            return Result.refused("voltage_unsafe", "the PPK2 supplies %d to %d mV: %s mV is outside" % (VOLT_MIN_MV, VOLT_MAX_MV, mv), {"mv": mv})

        def work(port, node, serial):
            cal = self._ready(port)
            if on:
                if cal.mode != "source":
                    return Result.refused("ppk2_mode", "the PPK2 is in %s mode: only source mode supplies a board. Set it with ppk2.mode" % (cal.mode or "an unknown"),
                                          {"mode": cal.mode or "unknown"})
                if not accept_supply:
                    volts = mv if mv is not None else (self._vdd if self._vdd is not None else cal.vdd_mv)
                    return Result.refused("supply_unconfirmed", "switching on powers whatever is wired to the PPK2's output at %g mV: say accept_supply when that is meant" % volts,
                                          {"mv": volts})
            # The voltage goes first and the switch second, so the output never comes on at an older voltage. The unit does not report either back: its metadata's VDD is the
            # figure it started with and did not change after a voltage command (seen on the bench, 2026-11-20), so what was set is remembered here and said so.
            if mv is not None:
                port.write(voltage_frame(int(mv)))
                self._vdd = int(mv)
            port.write(_frame(POWER, 1 if on else 0))
            self._supply = bool(on)
            after = self._ready(port)
            data = self._facts(after, node, serial)
            return Result.success(data, "supply %s at %g mV (set by this service: the unit does not report its voltage back)" % ("on" if on else "off", data["vdd_mv"]))
        return self._converse(work)

    # -- the session ---------------------------------------------------------------------------------
    def source(self):
        return Ppk2Source(self)

    def _claim(self):
        with self._lock:
            if self._busy:
                raise Fault("the PPK2 is already in use")
            self._busy = True

    def _release(self):
        with self._lock:
            self._busy = False


class Ppk2Source(object):
    """The PPK2 as a source for a session: the port is held from `begin` to `end`, each `take` is one block of 0.2 s."""
    name = "ppk2"

    def __init__(self, ppk2):
        self.ppk2, self.port, self.reducer, self.started, self.claimed = ppk2, None, None, None, False

    def ceiling_s(self, s):
        return BLOCK_CEILING_S + DRAIN_MAX_S + 1.0

    def begin(self, s):
        p = self.ppk2
        p._claim()
        self.claimed = True
        try:
            node, serial, problem = p.locate()
            if problem is not None:
                raise Fault(problem.message)
            try:
                port = p._opener()(node)
            except (OSError, ValueError) as exc:
                raise Fault("the PPK2's port %s would not open: %s" % (node, exc))
            self.port = port
            try:
                cal = p._ready(port)
                port.write(_frame(START))
            except Ppk2Error as exc:
                raise Fault(str(exc))
            except OSError as exc:
                raise Fault("the PPK2's port %s failed: %s" % (node, exc))
            self.reducer, self.started = Reducer(cal), p._mono()
        except BaseException:
            self._close()
            raise

    def _block(self):
        """The next 20,000 words, or a Fault if they are not all there in BLOCK_CEILING_S."""
        p = self.ppk2
        need = BLOCK_SAMPLES * 4
        data = b""
        end = p._mono() + BLOCK_CEILING_S
        while len(data) < need:
            if p._mono() >= end:
                raise Fault("the PPK2 sent %d of the %d samples of a block in %.2f s: it has gone silent" % (len(data) // 4, BLOCK_SAMPLES, BLOCK_CEILING_S))
            try:
                data += self.port.read(need - len(data))
            except OSError as exc:
                raise Fault("the PPK2's port failed: %s" % exc)
        return data

    def take(self, s, k):
        p, r = self.ppk2, self.reducer
        b = None
        while b is None:                                    # a block whose every sample was held out after a range change says nothing: read the next
            b = r.block(self._block())
        elapsed = p._mono() - self.started
        facts = {"lost": r.lost, "filtered": r.filtered, "rate_low": bool(elapsed >= RATE_AFTER_S and r.words / elapsed < RATE_LOW_HZ)}
        return Reading(b["t"], b["mean"], "CURR", "A", range=b["range"], lo=b["lo"], hi=b["hi"], n=b["n"], dt=b["dt"], facts=facts)

    def _close(self):
        if not self.claimed:
            return
        port, self.port, self.claimed = self.port, None, False
        try:
            if port is not None:
                try:
                    port.write(_frame(STOP))
                    self.ppk2._drain(port)
                except OSError:
                    pass
                port.close()
        finally:
            self.ppk2._release()

    def end(self):
        self._close()


def register(registry, host):
    ppk2 = host.service("ppk2", lambda: Ppk2(host))

    def add(command):
        registry.add(command, module=__name__)

    add(Command("ppk2.show", "Ask the Nordic Power Profiler what it is set to: mode (ampere or source), supply voltage, calibration. Measures nothing; refused while a session is using it.",
                ppk2.show, requires=("ppk2",), result="reachable, serial, port, mode, vdd_mv, calibrated, hw, supply"))
    add(Command("ppk2.mode", "Set the PPK2 to ampere mode (a board with its own supply, measured in series) or source mode (the PPK2 can power the board). Leaving source mode "
                "can cut a board's power, so it needs accept_supply unless this service has seen the output off.",
                ppk2.set_mode, args=(Arg("mode", doc="ampere or source"), Arg("accept_supply", type="bool", required=False,
                                                                              doc="true when cutting the board's supply is meant")),
                requires=("ppk2",), effect="write", result="mode, vdd_mv, supply"))
    add(Command("ppk2.supply", "Set the PPK2's source voltage (800 to 5000 mV) and switch its output on or off. Switching ON powers whatever is wired to it: it needs source mode and accept_supply.",
                ppk2.set_supply, args=(Arg("on", type="bool", doc="true to power the output, false to switch it off"),
                                       Arg("mv", type="int", required=False, unit="mV", doc="the source voltage, 800 to 5000"),
                                       Arg("accept_supply", type="bool", required=False, doc="true when powering the board is meant")),
                requires=("ppk2",), effect="write", result="mode, vdd_mv, supply"))
