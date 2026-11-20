#!/usr/bin/env python3
"""Tests for the Nordic PPK2 (docs/specs/ppk2.md): the port chosen by interface number, the metadata, the conversion, the reduction of the stream, the faults, the gate on
the supply, and a session through the sampler, all against a fake unit that speaks the protocol read from the real one (2026-11-20).
Run:  python3 tests/instruments/test_ppk2.py
"""
import json
import os
import random
import struct
import sys
import tempfile
import threading
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.events import Bus  # noqa: E402
from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.instruments import ppk2, sampler  # noqa: E402
from fakes import Tree  # noqa: E402

# What the real unit answered to the metadata query on 2026-11-20 (Calibrated 0, source mode, VDD 4000).
REAL_META = """Calibrated: 0
R0: 996.95562744140620000000
R1: 100.87370300292969000000
R2: 10.15465068817138700000
R3: 0.95032548904418950000
R4: 0.05614935234189033500
GS0: 0.00000000000000000010
GS1: 80.94953918457031000000
GS2: 14.14565277099609400000
GS3: 1.93789243698120120000
GS4: 0.05972966551780701000
GI0: 1.00000000000000000000
GI1: 0.97737044095993040000
GI2: 0.96965962648391720000
GI3: 0.96151167154312130000
GI4: 0.96700888872146610000
O0: 90.37355804443360000000
O1: 61.18122100830078000000
O2: 51.47786712646484400000
O3: 40.48199462890625000000
O4: 68.66588592529297000000
VDD: 4000
HW: 31219
mode: 2
S0: 0.00000006718058642718
S1: 0.00000082157708902741
S2: 0.00000827728854346788
S3: 0.00009466845949646086
S4: 0.00382176507264375700
I0: -0.00000011541752087396
I1: -0.00000228791668632766
I2: 0.00001781900573405437
I3: -0.00043550017289817333
I4: -0.01230763550847768800
UG0: 1.00000000000000000000
UG1: 1.00000000000000000000
UG2: 1.00000000000000000000
UG3: 1.00000000000000000000
UG4: 1.00000000000000000000
IA: 59
END
"""


def until(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.002)
    return False


def word(adc, rng, counter, logic=0):
    return (adc & 0x3FFF) | (rng << 14) | ((counter & 63) << 18) | (logic << 24)


def pack(words):
    return b"".join(struct.pack("<I", w) for w in words)


def reference_amps(cal, rng, adc):
    """The formula of the reference library, written out on its own: what the unit's calibration says a (range, ADC) word is."""
    x = (adc * 4 - cal.O[rng]) * (1.8 / 163840 / cal.R[rng])
    return cal.UG[rng] * (x * (cal.GS[rng] * x + cal.GI[rng]) + (cal.S[rng] * (cal.vdd_mv / 1000.0) + cal.I[rng]))


class FakeUnit(object):
    """The PPK2's measuring port: answers 0x19 with a metadata block, streams words after 0x06 until 0x07, keeps 768 bytes in flight after a stop, and takes the
    mode, voltage and power commands. Every write is recorded as a tuple of ints."""

    def __init__(self, meta=REAL_META, profile=None):
        self.meta = {k: v for k, v in (line.split(": ", 1) for line in meta.strip().split("\n") if ": " in line)}
        self.writes, self.closed = [], False
        self.measuring, self.index, self.pending = False, 0, b""
        self.profile = profile or (lambda i: (1000 + (i % 7), 0))          # word index -> (adc, range)
        self.lost_at = {}                                                  # word index -> how many counter steps to skip before it
        self.silent = False
        self.ignore_voltage = False
        self.counter = 0
        self.power = None

    def write(self, data):
        frame = tuple(bytearray(data))
        self.writes.append(frame)
        if frame == (ppk2.META,):
            self.pending += ("".join("%s: %s\n" % kv for kv in self.meta.items()) + "END\n").encode()
        elif frame == (ppk2.START,):
            self.measuring = True
        elif frame == (ppk2.STOP,):
            if self.measuring:
                self.pending += bytes(768)
            self.measuring = False
        elif frame[0] == ppk2.MODE:
            self.meta["mode"] = str(frame[1])
        elif frame[0] == ppk2.POWER:
            self.power = bool(frame[1])
        elif frame[0] == ppk2.VOLTAGE and not self.ignore_voltage:
            self.meta["VDD"] = str((frame[1] - 3) * 256 + frame[2] + 800 - 32)

    def read(self, size):
        if self.pending:
            out, self.pending = self.pending[:size], self.pending[size:]
            return out
        if not self.measuring or self.silent:
            return b""
        n = max(1, size // 4)
        words = []
        for _ in range(n):
            self.counter += self.lost_at.pop(self.index, 0)
            adc, rng = self.profile(self.index)
            words.append(word(adc, rng, self.counter))
            self.counter += 1
            self.index += 1
        return pack(words)

    def close(self):
        self.closed = True


class Host(object):
    def __init__(self, tree, setting="auto", folder=None, unit=None):
        cfg, problems = cfgmod.load(env={}, overrides={"instruments": {"ppk2": setting}, "paths": {"meter": folder},
                                                       "streams": {"meter": {"retain": 5000, "max_age_days": 1}}})
        assert problems == [], problems
        self.config = cfg
        self.sysfs, self.devroot, self.proc = tree.sysfs, tree.dev, tree.proc
        self.unit = unit or FakeUnit()
        self.opened = []
        self.hooks = {"ppk2_open": self._open}
        self.streams = types.SimpleNamespace(meter=Bus(5000, 86400))
        self._services = {}
        self._guard = threading.Lock()

    def _open(self, node):
        self.opened.append(node)
        self.unit.closed = False
        return self.unit

    def service(self, name, factory):
        """As service.Host.service: one lock held while a factory runs, and not re-entrant. A factory that asks for another service deadlocks the real service (it did,
        on the bench, 2026-11-20): here it fails at once."""
        if not self._guard.acquire(timeout=2):
            raise RuntimeError("Host.service was asked for a service from inside a service factory: that deadlocks the real host")
        try:
            if name not in self._services:
                self._services[name] = factory()
            return self._services[name]
        finally:
            self._guard.release()

    def started(self, name):
        with self._guard:
            return self._services.get(name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.close)
        self.tree.node("ttyACM1", 166, 1)
        self.tree.node("ttyACM2", 166, 2)
        self.tree.ppk2()
        self.folder = tempfile.mkdtemp(prefix="nrfwb-ppk2-rec-")
        self.host = Host(self.tree, folder=self.folder)
        self.unit = self.host.unit
        self.ppk = ppk2.Ppk2(self.host)
        self.host._services["ppk2"] = self.ppk

    def ctx(self):
        return types.SimpleNamespace(host=self.host)

    def start_sampler(self, **kw):
        self.sampler = sampler.Sampler(None, self.host.streams.meter, self.folder, ppk2=self.ppk, **kw)
        self.addCleanup(self.sampler.stop)
        return self.sampler


class Choosing(Base):
    def test_the_measuring_port_is_chosen_by_its_interface_number_and_the_shell_is_never_opened(self):
        r = self.ppk.show(self.ctx())
        self.assertEqual(r.code, "ok", r.message)
        self.assertEqual(self.host.opened, [os.path.join(self.tree.dev, "ttyACM1")])
        self.assertEqual(r.data["port"], os.path.join(self.tree.dev, "ttyACM1"))

    def test_the_interface_not_the_ttyACM_number_decides(self):
        """With the two ports numbered the other way round, the measuring one is still interface 01."""
        tree = Tree()
        self.addCleanup(tree.close)
        tree.node("ttyACM5", 166, 5)
        tree.node("ttyACM3", 166, 3)
        tree.ppk2(measuring=("ttyACM5", "166:5"), shell=("ttyACM3", "166:3"))
        host = Host(tree)
        r = ppk2.Ppk2(host).show(types.SimpleNamespace(host=host))
        self.assertEqual(r.code, "ok", r.message)
        self.assertEqual(host.opened, [os.path.join(tree.dev, "ttyACM5")])

    def test_nothing_attached_is_ppk2_unreachable(self):
        self.tree.unplug("1-1.2")
        r = self.ppk.show(self.ctx())
        self.assertEqual((r.cls, r.code), ("failed", "ppk2_unreachable"))
        self.assertEqual(self.host.opened, [])

    def test_two_units_need_the_serial_and_the_serial_picks_one(self):
        self.tree.node("ttyACM7", 166, 7)
        self.tree.node("ttyACM8", 166, 8)
        self.tree.ppk2(name="1-1.4", serial="SECONDUNIT", measuring=("ttyACM7", "166:7"), shell=("ttyACM8", "166:8"))
        r = self.ppk.show(self.ctx())
        self.assertEqual((r.cls, r.code), ("refused", "two_candidates"))
        self.assertEqual(sorted(r.evidence["serials"]), ["E1C5F15B15A3", "SECONDUNIT"])
        self.host.config["instruments"]["ppk2"] = "SECONDUNIT"
        r = self.ppk.show(self.ctx())
        self.assertEqual((r.code, r.data["serial"]), ("ok", "SECONDUNIT"))
        self.assertEqual(self.host.opened[-1], os.path.join(self.tree.dev, "ttyACM7"))
        self.host.config["instruments"]["ppk2"] = "NOTTHIS"
        self.assertEqual(self.ppk.show(self.ctx()).code, "ppk2_unreachable")


class Metadata(unittest.TestCase):
    def test_the_real_block_parses_for_all_five_ranges(self):
        cal = ppk2.parse_metadata(REAL_META)
        for name in ("R", "GS", "GI", "O", "S", "I", "UG"):
            self.assertEqual(len(getattr(cal, name)), 5, name)
        self.assertEqual((cal.vdd_mv, cal.calibrated, cal.hw, cal.mode), (4000, False, 31219, "source"))
        self.assertAlmostEqual(cal.R[0], 996.9556274414062)
        self.assertAlmostEqual(cal.O[4], 68.66588592529297)

    def test_a_missing_key_or_a_zero_shunt_is_refused_not_guessed(self):
        with self.assertRaises(ppk2.Ppk2Error):
            ppk2.parse_metadata(REAL_META.replace("GI3:", "XX3:"))
        with self.assertRaises(ppk2.Ppk2Error):
            ppk2.parse_metadata(REAL_META.replace("R2: 10.15465068817138700000", "R2: 0"))

    def test_the_mode_numbers(self):
        self.assertEqual(ppk2.parse_metadata(REAL_META.replace("mode: 2", "mode: 1")).mode, "ampere")
        self.assertIsNone(ppk2.parse_metadata(REAL_META.replace("mode: 2", "mode: 9")).mode)

    def test_the_conversion_is_the_reference_formula_in_every_range(self):
        cal = ppk2.parse_metadata(REAL_META)
        rng = random.Random(3)
        for r in range(5):
            for _ in range(200):
                adc = rng.randrange(0, 16384)
                self.assertAlmostEqual(cal.amps((r << 14) | adc), reference_amps(cal, r, adc), places=15, msg=(r, adc))

    def test_a_dark_unit_reads_about_a_tenth_of_a_microamp_as_the_real_one_did(self):
        """Not a tautology: the first range's offset O0 is 90.37 of 65536, so a word of 22 or 23 (88 or 92 after x4) is the real unit's zero."""
        cal = ppk2.parse_metadata(REAL_META)
        v = cal.amps(23)
        self.assertLess(abs(v), 1e-6)


def naive_block(cal, words, prev_range, skip):
    """Every sample on its own, no counting trick: the held-out samples after a range change, then the figures. Returns (kept values, new prev_range, new skip)."""
    kept, hold = [], skip
    last = prev_range
    for w in words:
        r = (w >> 14) & 7
        if last is not None and r != last:
            hold = ppk2.SPIKE_SAMPLES
        last = r
        if hold > 0:
            hold -= 1
            continue
        kept.append(reference_amps(cal, min(r, 4), w & 0x3FFF))
    return kept, last, hold


class Reducing(unittest.TestCase):
    def setUp(self):
        self.cal = ppk2.parse_metadata(REAL_META)

    def test_the_counting_reduction_equals_the_straight_loop_over_random_words_across_blocks(self):
        rng = random.Random(5)
        red = ppk2.Reducer(self.cal)
        prev, skip, counter = None, 0, 0
        for block in range(6):
            n = 4000
            words = []
            r = rng.randrange(0, 5)
            for _ in range(n):
                if rng.random() < 0.01:                                     # an occasional range change, so a hold-out crosses a boundary sometimes
                    r = rng.randrange(0, 5)
                words.append(word(rng.randrange(0, 16384), r, counter))
                counter += 1
            kept, prev, skip = naive_block(self.cal, words, prev, skip)
            got = red.block(pack(words))
            if not kept:
                self.assertIsNone(got)
                continue
            self.assertEqual(got["n"], len(kept))
            self.assertAlmostEqual(got["lo"], min(kept), places=15)
            self.assertAlmostEqual(got["hi"], max(kept), places=15)
            self.assertAlmostEqual(got["mean"], sum(kept) / len(kept), places=13)

    def test_blocks_in_one_range_and_blocks_that_change_range_both_equal_the_straight_loop(self):
        """The fast path (a block in one range, counted in C) and the slow one (a change inside it) must give what looking at every sample gives, with losses in the counter too."""
        rng = random.Random(17)
        red = ppk2.Reducer(self.cal)
        prev, skip, counter, lost_total, last_counter = None, 0, 0, 0, None
        steady = mixed = 0
        for block in range(40):
            n = rng.choice((3, 5, 64, 1000, 4096))
            if rng.random() < 0.6:
                r = rng.randrange(0, 5)
                ranges = [r] * n
                steady += 1
            else:
                ranges = [rng.randrange(0, 5) if rng.random() < 0.02 else 0 for _ in range(n)]
                for i in range(1, n):
                    if rng.random() > 0.02:
                        ranges[i] = ranges[i - 1]
                mixed += 1
            words, counters = [], []
            for i in range(n):
                counter += rng.choice((0, 0, 0, 0, 0, 0, 0, 0, 0, 3)) if rng.random() < 0.05 else 0      # now and then samples never arrived
                words.append(word(rng.randrange(0, 16384), ranges[i], counter))
                counters.append(counter & 63)
                counter += 1
            kept, prev, skip = naive_block(self.cal, words, prev, skip)
            seq = ([last_counter] if last_counter is not None else []) + counters
            naive_lost = sum((b - a - 1) & 63 for a, b in zip(seq, seq[1:]))
            last_counter = counters[-1]
            before = red.lost
            got = red.block(pack(words))
            self.assertEqual(red.lost - before, naive_lost, "block %d" % block)
            lost_total += naive_lost
            if not kept:
                self.assertIsNone(got)
                continue
            self.assertEqual(got["n"], len(kept), "block %d" % block)
            self.assertAlmostEqual(got["lo"], min(kept), places=15)
            self.assertAlmostEqual(got["hi"], max(kept), places=15)
            self.assertAlmostEqual(got["mean"], sum(kept) / len(kept), places=13)
            self.assertEqual(got["range"], max(ranges))
        self.assertEqual(red.lost, lost_total)
        self.assertGreater(steady, 10)
        self.assertGreater(mixed, 10)

    def test_a_steady_block_after_a_change_holds_out_its_first_three_and_the_next_steady_block_none(self):
        red = ppk2.Reducer(self.cal)
        red.block(pack([word(100, 0, i) for i in range(50)]))
        got = red.block(pack([word(100, 2, 50 + i) for i in range(50)]))                 # the whole block is in the new range
        self.assertEqual((got["n"], red.filtered), (47, 3))
        got = red.block(pack([word(100, 2, 100 + i) for i in range(50)]))
        self.assertEqual((got["n"], red.filtered), (50, 3))

    def test_a_block_with_no_loss_is_the_counter_run_and_one_dropped_sample_is_found_anywhere_in_it(self):
        for gap_at in (0, 1, 31, 32, 63, 64, 100, 998, 999):
            red = ppk2.Reducer(self.cal)
            red.block(pack([word(100, 0, i) for i in range(5)]))
            c = 5
            words = []
            for i in range(1000):
                if i == gap_at:
                    c += 1                                                              # one sample the host never got
                words.append(word(100, 0, c))
                c += 1
            before = red.lost
            red.block(pack(words))
            self.assertEqual(red.lost - before, 1, gap_at)

    def test_a_burst_of_a_few_samples_is_the_blocks_highest_and_still_moves_the_mean_exactly(self):
        words = [word(100, 0, i) for i in range(2000)]
        for i in range(1000, 1004):
            words[i] = word(9000, 0, i)                                       # 4 samples of a short burst, same range
        got = ppk2.Reducer(self.cal).block(pack(words))
        low, high = reference_amps(self.cal, 0, 100), reference_amps(self.cal, 0, 9000)
        self.assertAlmostEqual(got["hi"], high, places=15)
        self.assertAlmostEqual(got["lo"], low, places=15)
        self.assertAlmostEqual(got["mean"], (1996 * low + 4 * high) / 2000, places=13)

    def test_the_three_samples_after_a_range_change_are_held_out_and_counted(self):
        words = [word(500, 0, i) for i in range(10)] + [word(500, 1, i) for i in range(10, 20)]
        red = ppk2.Reducer(self.cal)
        got = red.block(pack(words))
        self.assertEqual((got["n"], red.filtered), (17, 3))
        # the first samples of a stream are never held out for "changing" from nothing
        self.assertEqual(ppk2.Reducer(self.cal).block(pack([word(500, 2, i) for i in range(10)]))["n"], 10)

    def test_a_hold_out_that_runs_over_the_end_of_a_block_continues_in_the_next(self):
        red = ppk2.Reducer(self.cal)
        a = [word(500, 0, i) for i in range(9)] + [word(500, 1, 9)]                # the change is the last sample
        got = red.block(pack(a))
        self.assertEqual((got["n"], red.filtered), (9, 1))
        b = [word(500, 1, i) for i in range(10, 20)]
        got = red.block(pack(b))
        self.assertEqual((got["n"], red.filtered), (8, 3))                         # two more from the next block: three in all after the change

    def test_a_whole_block_held_out_is_none(self):
        red = ppk2.Reducer(self.cal)
        red.block(pack([word(500, 0, i) for i in range(5)]))
        self.assertIsNone(red.block(pack([word(500, 1, 5), word(500, 1, 6), word(500, 1, 7)])))

    def test_the_counter_counts_lost_samples_and_wraps_at_64(self):
        red = ppk2.Reducer(self.cal)
        words = [word(500, 0, c) for c in (0, 1, 2, 5, 6)]                        # 3 and 4 never arrived
        red.block(pack(words))
        self.assertEqual(red.lost, 2)
        red.block(pack([word(500, 0, c) for c in (7, 8)]))                        # across the boundary: no loss
        self.assertEqual(red.lost, 2)
        red.block(pack([word(500, 0, c) for c in (62, 63, 0, 1)]))                # 9 to 62 is 53 lost, then it wraps cleanly
        self.assertEqual(red.lost, 2 + 53)

    def test_time_is_the_sample_clock_and_counts_the_lost_samples(self):
        red = ppk2.Reducer(self.cal)
        got = red.block(pack([word(500, 0, i) for i in range(20000)]))
        self.assertAlmostEqual(got["t"], 0.2, places=9)
        self.assertAlmostEqual(got["dt"], 0.2, places=9)
        words = [word(500, 0, 20000 + i + (3 if i >= 10000 else 0)) for i in range(20000)]    # 3 lost mid-block
        got = red.block(pack(words))
        self.assertAlmostEqual(got["dt"], (20000 + 3) / 100000.0, places=9)
        self.assertAlmostEqual(got["t"], (40000 + 3) / 100000.0, places=9)

    def test_the_range_reported_is_the_highest_seen_and_never_above_four(self):
        got = ppk2.Reducer(self.cal).block(pack([word(500, 1, 0), word(500, 1, 1), word(500, 3, 2), word(500, 3, 3), word(500, 3, 4), word(500, 3, 5)]))
        self.assertEqual(got["range"], 3)

    def test_the_memo_is_bounded_by_the_number_of_different_words(self):
        red = ppk2.Reducer(self.cal)
        red.block(pack([word(i % 16384, i % 5, i) for i in range(30000)]))
        self.assertLessEqual(len(self.cal._memo), 5 * 16384)


class LinkOnAPty(unittest.TestCase):
    """The real port wrapper, on a pseudo-terminal: bytes in order and none lost, never more than asked for, the surplus kept, silence as an empty read, a hang-up as an error."""

    def setUp(self):
        try:
            self.master, slave = os.openpty()
        except OSError as exc:
            self.skipTest("no pty here (%s)" % exc)
        self.name = os.ttyname(slave)
        self.slave = slave                                   # held open so the pty outlives the link
        self.link = ppk2.Link(self.name)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            self.link.close()
        except OSError:
            pass

    def feed(self, data):
        def run():
            view = memoryview(data)
            while view:
                view = view[os.write(self.master, view[:4096]):]
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def test_every_byte_arrives_in_order_and_no_read_returns_more_than_it_asked_for(self):
        data = bytes((i * 7 + i // 251) & 0xFF for i in range(120000))
        self.feed(data)
        got, sizes = b"", []
        end = time.monotonic() + 10
        while len(got) < len(data) and time.monotonic() < end:
            chunk = self.link.read(7777)
            self.assertLessEqual(len(chunk), 7777)
            sizes.append(len(chunk))
            got += chunk
        self.assertEqual(got, data)

    def test_the_surplus_of_a_read_is_kept_for_the_next_one(self):
        os.write(self.master, bytes(range(200)))
        first = self.link.read(10)
        self.assertEqual(first, bytes(range(10)))
        self.assertEqual(self.link.read(1000), bytes(range(10, 200)))

    def test_silence_is_an_empty_read_after_about_the_port_timeout(self):
        t = time.monotonic()
        self.assertEqual(self.link.read(100), b"")
        took = time.monotonic() - t
        self.assertGreaterEqual(took, ppk2.PORT_TIMEOUT_S * 0.8)
        self.assertLess(took, ppk2.PORT_TIMEOUT_S + 0.2)

    def test_a_write_reaches_the_other_end(self):
        self.link.write(ppk2._frame(ppk2.META))
        self.assertEqual(os.read(self.master, 10), bytes([ppk2.META]))

    def test_a_hang_up_is_an_error_not_an_endless_empty_read(self):
        os.close(self.master)                                # the far end goes away: the link's reads are told so
        self.master = -1
        with self.assertRaises(OSError):
            for _ in range(5):
                self.link.read(10)


class Writing(Base):
    ALLOWED = ((ppk2.META,), (ppk2.START,), (ppk2.STOP,), (ppk2.MODE, 1), (ppk2.MODE, 2), (ppk2.POWER, 0), (ppk2.POWER, 1))

    def check_writes(self):
        for frame in self.unit.writes:
            ok = frame in self.ALLOWED or (len(frame) == 3 and frame[0] == ppk2.VOLTAGE and 3 <= frame[1] <= 19)
            self.assertTrue(ok, "%r is not one of the six shapes" % (frame,))

    def test_nothing_but_the_six_shapes_can_be_framed(self):
        for bad in ((0x20,), (0x05,), (0x11, 3), (0x0C, 2), (0x0D, 2, 0), (0x0D, 20, 0), (0x0D, 3, 256), (0x19, 0), ()):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ppk2._frame(*bad)

    def test_the_voltage_encoding_is_the_reference_libraries(self):
        self.assertEqual(tuple(bytearray(ppk2.voltage_frame(800))), (0x0D, 3, 32))
        self.assertEqual(tuple(bytearray(ppk2.voltage_frame(3300))), (0x0D, 3 + (3300 - 800 + 32) // 256, (3300 - 800 + 32) % 256))
        self.assertEqual(tuple(bytearray(ppk2.voltage_frame(5000)))[1], 19)

    def test_every_byte_written_across_a_whole_scenario_is_one_of_the_six_shapes(self):
        self.ppk.show(self.ctx())
        self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        self.ppk.set_mode(self.ctx(), "source")
        self.ppk.set_supply(self.ctx(), True, mv=3300, accept_supply=True)
        self.ppk.set_supply(self.ctx(), False)
        s = self.start_sampler()
        s.start("record", 60, name="all", source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] > 0))
        s.stop()
        self.check_writes()

    def test_show_changes_nothing_on_the_unit(self):
        self.ppk.show(self.ctx())
        self.assertEqual(set(self.unit.writes), {(ppk2.STOP,), (ppk2.META,)})
        self.assertTrue(self.unit.closed)


class Gate(Base):
    def test_leaving_source_mode_with_the_output_unknown_is_refused_and_writes_nothing(self):
        r = self.ppk.set_mode(self.ctx(), "ampere")
        self.assertEqual((r.cls, r.code), ("refused", "supply_unconfirmed"))
        self.assertEqual(r.evidence["supply"], "unknown")
        self.assertNotIn((ppk2.MODE, 1), self.unit.writes)

    def test_with_the_tick_it_goes_and_reads_back(self):
        r = self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        self.assertEqual((r.code, r.data["mode"], r.data["supply"]), ("ok", "ampere", "off"))
        self.assertIn((ppk2.MODE, 1), self.unit.writes)

    def test_entering_source_mode_needs_no_tick(self):
        self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        r = self.ppk.set_mode(self.ctx(), "source")
        self.assertEqual((r.code, r.data["mode"]), ("ok", "source"))

    def test_asking_for_the_mode_it_is_already_in_writes_nothing_and_needs_no_tick(self):
        r = self.ppk.set_mode(self.ctx(), "source")
        self.assertEqual((r.code, r.data["mode"]), ("ok", "source"))
        self.assertFalse([w for w in self.unit.writes if w[0] == ppk2.MODE])
        self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        n = len(self.unit.writes)
        self.assertEqual(self.ppk.set_mode(self.ctx(), "ampere").code, "ok")
        self.assertFalse([w for w in self.unit.writes[n:] if w[0] == ppk2.MODE])

    def test_coming_back_into_source_mode_the_output_is_unknown_again_so_leaving_needs_the_tick(self):
        self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        self.ppk.set_mode(self.ctx(), "source")
        self.assertEqual(self.ppk.show(self.ctx()).data["supply"], "unknown")
        r = self.ppk.set_mode(self.ctx(), "ampere")
        self.assertEqual((r.cls, r.code), ("refused", "supply_unconfirmed"))

    def test_leaving_source_mode_after_this_service_saw_the_output_off_needs_no_tick(self):
        self.ppk.set_supply(self.ctx(), False)
        r = self.ppk.set_mode(self.ctx(), "ampere")
        self.assertEqual(r.code, "ok", r.message)

    def test_leaving_source_mode_with_the_output_known_on_is_refused(self):
        self.ppk.set_supply(self.ctx(), True, mv=3300, accept_supply=True)
        r = self.ppk.set_mode(self.ctx(), "ampere")
        self.assertEqual((r.code, r.evidence["supply"]), ("supply_unconfirmed", "on"))

    def test_a_mode_that_did_not_take_is_a_failure_not_a_success(self):
        self.unit.write = lambda data, orig=self.unit.write: orig(data) if tuple(bytearray(data))[0] != ppk2.MODE else None
        r = self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        self.assertEqual((r.cls, r.code), ("failed", "no_reply"))

    def test_a_bad_mode_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.ppk.set_mode(self.ctx(), "volts")

    def test_switching_on_needs_source_mode_and_the_tick(self):
        self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        r = self.ppk.set_supply(self.ctx(), True, mv=3300, accept_supply=True)
        self.assertEqual((r.cls, r.code), ("refused", "ppk2_mode"))
        self.ppk.set_mode(self.ctx(), "source")
        r = self.ppk.set_supply(self.ctx(), True, mv=3300)
        self.assertEqual((r.cls, r.code), ("refused", "supply_unconfirmed"))
        self.assertFalse([w for w in self.unit.writes if w[0] in (ppk2.POWER, ppk2.VOLTAGE)], "a refusal wrote nothing")
        r = self.ppk.set_supply(self.ctx(), True, mv=3300, accept_supply=True)
        self.assertEqual((r.code, r.data["supply"], r.data["vdd_mv"]), ("ok", "on", 3300))
        order = [w[0] for w in self.unit.writes if w[0] in (ppk2.POWER, ppk2.VOLTAGE)]
        self.assertEqual(order, [ppk2.VOLTAGE, ppk2.POWER], "the voltage is set before the output is switched on")
        self.assertTrue(self.unit.power)

    def test_switching_off_needs_no_tick_and_no_mode(self):
        self.ppk.set_mode(self.ctx(), "ampere", accept_supply=True)
        r = self.ppk.set_supply(self.ctx(), False)
        self.assertEqual((r.code, r.data["supply"]), ("ok", "off"))

    def test_a_voltage_outside_800_to_5000_is_refused_before_anything_is_opened(self):
        for mv in (799, 5001, 0, -1, 100000):
            r = self.ppk.set_supply(self.ctx(), True, mv=mv, accept_supply=True)
            self.assertEqual((r.cls, r.code), ("refused", "voltage_unsafe"), mv)
        self.assertEqual(self.host.opened, [])
        for mv in (800, 5000):
            self.assertEqual(self.ppk.set_supply(self.ctx(), False, mv=mv).code, "ok", mv)

    def test_the_unit_does_not_report_its_voltage_back_so_what_was_set_is_remembered_and_said(self):
        """On the real unit the metadata's VDD stayed 4000 after a voltage command (2026-11-20): a read-back check could never pass, and it ran AFTER the switch."""
        self.unit.ignore_voltage = True
        r = self.ppk.set_supply(self.ctx(), False, mv=2500)
        self.assertEqual((r.code, r.data["vdd_mv"], r.data["vdd_from"]), ("ok", 2500, "set by this service"))
        self.assertIn("does not report its voltage back", r.message)
        shown = self.ppk.show(self.ctx()).data
        self.assertEqual((shown["vdd_mv"], shown["vdd_from"]), (2500, "set by this service"))

    def test_before_this_service_sets_a_voltage_the_units_own_figure_is_shown_and_labelled(self):
        shown = self.ppk.show(self.ctx()).data
        self.assertEqual((shown["vdd_mv"], shown["vdd_from"]), (4000, "the unit's own figure when it was read"))

    def test_the_voltage_is_written_before_the_switch_and_a_refusal_writes_neither(self):
        self.ppk.set_supply(self.ctx(), True, mv=2200, accept_supply=True)
        writes = [w for w in self.unit.writes if w[0] in (ppk2.VOLTAGE, ppk2.POWER)]
        self.assertEqual([w[0] for w in writes], [ppk2.VOLTAGE, ppk2.POWER])
        self.assertEqual(writes[0], (0x0D, 2200 >> 8, 2200 & 255), "the frame is the voltage in mV as two bytes, high then low")


class Session(Base):
    def test_a_watch_publishes_blocks_with_min_max_n_and_source_in_order(self):
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] >= 3 * 20000))
        s.stop()
        events = [e for e in self.host.streams.meter.since(0).events]
        types_ = [e.type for e in events]
        self.assertEqual(types_[0], "meter.state")
        self.assertEqual(types_[-1], "meter.state")
        reads = [e.payload for e in events if e.type == "meter.reading"]
        self.assertGreaterEqual(len(reads), 3)
        r = reads[0]
        self.assertEqual((r["source"], r["unit"], r["function"], r["n"], r["range"]), ("ppk2", "A", "CURR", 20000, 0))
        self.assertLessEqual(r["min"], r["value"])
        self.assertLessEqual(r["value"], r["max"])
        self.assertAlmostEqual(reads[0]["t_s"], 0.2, places=6)
        self.assertAlmostEqual(reads[2]["t_s"], 0.6, places=6)
        self.assertEqual(s.session()["source"], "ppk2")

    def test_the_session_statistics_are_exact_and_the_charge_is_mean_times_time(self):
        cal = ppk2.parse_metadata(REAL_META)
        self.unit.profile = lambda i: (1000 + (i % 7), 0)
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] >= 40000))
        s.stop()
        st = s.session()["stats"]
        n = st["readings"]
        self.assertEqual(n % 20000, 0)
        vals = [reference_amps(cal, 0, 1000 + (i % 7)) for i in range(n)]
        self.assertAlmostEqual(st["mean"], sum(vals) / n, places=13)
        self.assertAlmostEqual(st["min"], min(vals), places=15)
        self.assertAlmostEqual(st["max"], max(vals), places=15)
        self.assertAlmostEqual(st["charge_c"], sum(vals) / 100000.0, places=13)         # amps x seconds, each sample 10 us
        self.assertAlmostEqual(st["charge_mah"], st["charge_c"] / 3.6, places=15)

    def test_a_recording_writes_the_blocks_with_the_old_columns_in_place(self):
        s = self.start_sampler()
        s.start("record", 60, name="prof", source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] >= 40000))
        s.stop()
        with open(os.path.join(self.folder, "prof.csv")) as fh:
            lines = fh.read().strip().split("\n")
        self.assertEqual(lines[0], sampler.HEADER.strip())
        self.assertEqual(len(lines) - 1, s.session()["stats"]["readings"] // 20000)
        c = lines[1].split(",")
        self.assertEqual((c[3], c[4], c[5], c[6], c[7], c[10]), ("A", "CURR", "0", "", "0", "20000"))
        self.assertLessEqual(float(c[8]), float(c[2]))
        self.assertLessEqual(float(c[2]), float(c[9]))
        with open(os.path.join(self.folder, "prof.json")) as fh:
            side = json.load(fh)
        self.assertEqual((side["source"], side["lost"], side["filtered"]), ("ppk2", 0, 0))
        row = [r for r in s.recordings() if r["name"] == "prof"][0]
        self.assertEqual((row["source"], row["lost"], row["unit"]), ("ppk2", 0, "A"))

    def test_the_session_begins_by_stopping_a_dead_stream_then_reads_the_calibration_then_starts(self):
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] > 0))
        s.stop()
        w = self.unit.writes
        self.assertEqual(w[:3], [(ppk2.STOP,), (ppk2.META,), (ppk2.START,)])
        self.assertEqual(w[-1], (ppk2.STOP,))
        self.assertTrue(self.unit.closed)
        self.assertFalse(self.unit.measuring)

    def test_stopping_drains_what_was_in_flight(self):
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] > 0))
        s.stop()
        self.assertEqual(self.unit.pending, b"", "the 768 bytes a stop leaves in flight were read, not left for the next opener")

    def test_a_silent_unit_ends_the_session_with_the_reason_and_keeps_the_file(self):
        self.unit.silent = True
        clock = {"t": 0.0}

        def mono():
            clock["t"] += 0.05
            return clock["t"]
        self.host.config["instruments"]["ppk2"] = "auto"
        ppk = ppk2.Ppk2(self.host, monotonic=mono)
        s = sampler.Sampler(None, self.host.streams.meter, self.folder, ppk2=ppk)
        self.addCleanup(s.stop)
        s.start("record", 60, name="quiet", source="ppk2")
        self.assertTrue(until(lambda: s.session()["state"] == "error"))
        self.assertIn("gone silent", s.session()["reason"])
        self.assertTrue(os.path.exists(os.path.join(self.folder, "quiet.csv")))
        self.assertTrue(self.unit.closed)

    def test_a_unit_that_is_not_there_ends_the_session_with_error(self):
        self.tree.unplug("1-1.2")
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["state"] == "error"))
        self.assertIn("no PPK2 is attached", s.session()["reason"])

    def test_lost_samples_and_filtered_samples_are_on_the_session_and_the_feed(self):
        self.unit.lost_at = {5000: 7}
        self.unit.profile = lambda i: (800, 0 if i < 30000 else 1)
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] >= 60000))
        s.stop()
        sess = s.session()
        self.assertEqual((sess["lost"], sess["filtered"], sess["rate_low"]), (7, 3, False))

    def test_a_stream_slower_than_98000_a_second_is_flagged(self):
        clock = {"t": 0.0}

        def mono(step):
            def read():
                clock["t"] += step
                return clock["t"]
            return read
        # A block costs three reads of the clock (the deadline, one check, the elapsed time). At 0.1 s a read a block of 20,000 words takes 0.3 s of fake time: 66,000 a second.
        for step, low in ((0.1, True), (0.03, False)):                  # 0.03 s a read is 0.09 s a block: 222,000 a second, nothing wrong
            clock["t"] = 0.0
            ppk = ppk2.Ppk2(self.host, monotonic=mono(step))
            s = sampler.Sampler(None, Bus(5000, 86400), self.folder, ppk2=ppk)
            s.start("watch", 60, source="ppk2")
            self.assertTrue(until(lambda: s.session()["stats"]["readings"] >= 200000))
            s.stop()
            self.assertEqual(s.session()["rate_low"], low, step)
            self.unit.index = self.unit.counter = 0

    def test_a_second_session_is_busy_across_sources_and_show_waits_for_it(self):
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        with self.assertRaises(sampler.Busy):
            s.start("watch", 60, source="ppk2")
        r = self.ppk.show(self.ctx())
        self.assertEqual((r.cls, r.code), ("refused", "meter_busy"))
        s.stop()
        self.assertEqual(self.ppk.show(self.ctx()).code, "ok")

    def test_a_source_that_is_not_configured_is_missing(self):
        s = sampler.Sampler(None, self.host.streams.meter, self.folder, ppk2=None)
        with self.assertRaises(sampler.Missing):
            s.start("watch", 60, source="ppk2")
        with self.assertRaises(sampler.Missing):
            s.start("watch", 60)                              # nothing at all

    def test_the_default_source_is_the_one_that_is_there(self):
        s = self.start_sampler()
        self.assertEqual(s.start("watch", 60)["source"], "ppk2")

    def test_the_profile_joins_the_feed_and_carries_the_facts(self):
        s = self.start_sampler()
        s.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: s.session()["stats"]["readings"] >= 40000))
        s.stop()
        p = s.profile()
        self.assertEqual((p["source"], p["unit"], p["function"]), ("ppk2", "A", "CURR"))
        self.assertEqual(sum(b["n"] for b in p["buckets"]), s.session()["stats"]["readings"])
        self.assertIn("lost", p["facts"])


class Commands(Base):
    def setUp(self):
        Base.setUp(self)
        self.registry = Registry()
        ppk2.register(self.registry, self.host)

    def test_the_commands_exist_only_with_the_capability(self):
        names = [c.name for c in self.registry.commands({"ppk2"})]
        self.assertEqual(names, ["ppk2.mode", "ppk2.show", "ppk2.supply"])
        self.assertEqual(self.registry.commands(set()), [])

    def test_the_write_commands_are_declared_writes_and_show_a_read(self):
        by = {c.name: c for c in self.registry.commands({"ppk2"})}
        self.assertEqual((by["ppk2.show"].effect, by["ppk2.mode"].effect, by["ppk2.supply"].effect), ("read", "write", "write"))

    def test_show_through_the_registry(self):
        r = self.registry.call("ppk2.show", {}, Context(self.host), {"ppk2"})
        self.assertEqual((r.code, r.data["mode"], r.data["vdd_mv"], r.data["supply"], r.data["calibrated"]), ("ok", "source", 4000, "unknown", False))
        self.assertIn("not calibrated by the user", r.message)


class SessionCommands(Base):
    """meter.* on a bench that has only the PPK2: the stream gate is the stream, and the source says which instrument."""
    CAPS = {"ppk2", "meter_stream", "meter_store"}

    def setUp(self):
        Base.setUp(self)
        from nrf_workbench.instruments import meter
        self.reg = Registry()
        meter.register(self.reg, self.host)
        self.addCleanup(lambda: self.host.started("meter_sampler") and self.host.started("meter_sampler").stop())

    def call(self, name, args=None):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities=set(self.CAPS))

    def test_the_session_commands_need_the_stream_not_the_multimeter(self):
        names = [c.name for c in self.reg.commands({"meter_stream"})]
        self.assertIn("meter.watch", names)
        self.assertNotIn("meter.show", names, "the multimeter's own commands still need the multimeter")
        self.assertNotIn("meter.record", names, "recording needs its folder")

    def test_a_watch_defaults_to_the_only_instrument_and_names_it(self):
        res = self.call("meter.watch", {"bound_s": 60})
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["source"], "ppk2")
        self.assertTrue(until(lambda: self.call("meter.session").data["stats"]["readings"] >= 20000))
        self.assertEqual(self.call("meter.stop").data["state"], "idle")

    def test_asking_for_an_instrument_that_is_not_configured_is_capability_missing(self):
        res = self.call("meter.watch", {"bound_s": 60, "source": "meter"})
        self.assertEqual((res.cls, res.code), ("refused", "capability_missing"))

    def test_an_unknown_source_is_a_usage_error_naming_it(self):
        with self.assertRaises(UsageError) as cm:
            self.call("meter.watch", {"bound_s": 60, "source": "scope"})
        self.assertEqual(cm.exception.arg, "source")

    def test_a_recording_through_the_command_lists_its_source_and_numbers(self):
        self.assertTrue(self.call("meter.record", {"bound_s": 60, "name": "via-cmd", "source": "ppk2"}).ok)
        self.assertTrue(until(lambda: self.call("meter.session").data["stats"]["readings"] >= 20000))
        self.call("meter.stop")
        row = self.call("meter.recordings").data["recordings"][0]
        self.assertEqual((row["name"], row["source"], row["unit"]), ("via-cmd", "ppk2", "A"))
        self.assertTrue(self.call("meter.recording", {"name": "via-cmd"}).data["csv"].startswith("t_s,wall,value,unit,function,range,nplc,overload,min,max,n"))


class Wiring(unittest.TestCase):
    def cfg(self, **overrides):
        cfg, problems = cfgmod.load(env={}, overrides=overrides)
        self.assertEqual(problems, [])
        return cfg

    def test_the_capabilities_follow_the_instrument_the_stream_and_the_folder(self):
        from nrf_workbench import service
        host = lambda **o: service.Host(self.cfg(**o), {}, {})  # noqa: E731
        mine = {"meter", "ppk2", "meter_stream", "meter_store"}
        bound = {"meter": {"retain": 10, "max_age_days": 1}}
        self.assertEqual(service.capabilities(host()) & mine, set())
        self.assertEqual(service.capabilities(host(instruments={"ppk2": "auto"})) & mine, {"ppk2"})
        self.assertEqual(service.capabilities(host(instruments={"ppk2": "auto"}, streams=bound)) & mine, {"ppk2", "meter_stream"})
        self.assertEqual(service.capabilities(host(instruments={"ppk2": "auto"}, streams=bound, paths={"meter": "/x"})) & mine, {"ppk2", "meter_stream", "meter_store"})
        both = service.capabilities(host(instruments={"ppk2": "auto", "meter": "m"}, streams=bound))
        self.assertEqual(both & mine, {"meter", "ppk2", "meter_stream"})

    def test_a_bench_with_only_the_profiler_and_no_bound_is_told_the_stream_is_off(self):
        from nrf_workbench import service
        s = service.build_streams(self.cfg(instruments={"ppk2": "auto"}))
        self.assertIsNone(s.meter)
        self.assertIn("streams.meter.retain", s.absent["meter"])

    def test_the_ppk2_commands_are_registered_by_the_service(self):
        from nrf_workbench import service
        host = service.Host(self.cfg(instruments={"ppk2": "auto"}), {}, {})
        reg = Registry()
        ppk2.register(reg, host)
        self.assertEqual(sorted(c.name for c in reg.commands({"ppk2"})), ["ppk2.mode", "ppk2.show", "ppk2.supply"])
        self.assertIs(host.started("ppk2"), host.service("ppk2", lambda: None), "one instrument object, shared with the session source")


class Config(unittest.TestCase):
    def test_ppk2_is_auto_or_a_serial(self):
        for good in ("auto", "E1C5F15B15A3", "abc123"):
            _cfg, problems = cfgmod.load(env={}, overrides={"instruments": {"ppk2": good}})
            self.assertEqual(problems, [], good)
        for bad in ("", "a b", "x" * 65, "../etc", 5, True):
            _cfg, problems = cfgmod.load(env={}, overrides={"instruments": {"ppk2": bad}})
            self.assertTrue([p for p in problems if "instruments.ppk2" in p], repr(bad))

    def test_the_environment_sets_it(self):
        cfg, problems = cfgmod.load(env={"NRFWB_PPK2": "auto"}, overrides={})
        self.assertEqual((problems, cfg["instruments"]["ppk2"]), ([], "auto"))

    def test_it_is_unset_by_default(self):
        cfg, _ = cfgmod.load(env={}, overrides={})
        self.assertIsNone(cfg["instruments"]["ppk2"])


if __name__ == "__main__":
    unittest.main()
