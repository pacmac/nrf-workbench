#!/usr/bin/env python3
"""Single wakes kept at full rate (docs/specs/ppk2-stages.md, stage 1): the level rule, the window, the bounds, the decoding, and the whole path over the fake unit.
Run:  python3 tests/instruments/test_capture.py
"""
import os
import random
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.instruments import capture, ppk2  # noqa: E402
from test_ppk2 import REAL_META, Base, reference_amps, until  # noqa: E402

SLEEP, WAKE = 10e-6, 20e-3
WAKE_ADC = 12000                                                        # a 14-bit ADC: 16383 at most


def feed_blocks(c, plan):
    """plan: a list of (mean, high) per block; each block's data is 8 bytes naming it, so a capture's content says which blocks it holds."""
    for k, (mean, high) in enumerate(plan):
        c.feed(block(k), float(k), mean, high, 0)


def block(k):
    return k.to_bytes(4, "little") * 2


def quiet(n):
    return [(SLEEP, SLEEP * 1.01)] * n


def hot(n):
    return [(WAKE / 2, WAKE)] * n


class TheLevel(unittest.TestCase):
    def test_the_sleep_level_is_the_middle_block_to_within_7_5_percent(self):
        lv = capture.Level()
        for _ in range(100):
            lv.add(SLEEP, SLEEP * 1.01)
        self.assertAlmostEqual(lv.sleep(), SLEEP, delta=SLEEP * 0.075)
        self.assertIsNone(lv.level(), "a peak under twice the sleep is no wake")
        lv.add(WAKE / 2, WAKE)
        self.assertAlmostEqual(lv.level(), lv.sleep() + (WAKE - lv.sleep()) / 2, delta=1e-12)

    def test_the_middle_block_not_the_mean_is_the_sleep_level(self):
        lv = capture.Level()
        for _ in range(90):
            lv.add(SLEEP, SLEEP)
        for _ in range(10):
            lv.add(WAKE, WAKE)                                          # a tenth of the blocks awake would drag a mean to 2 mA
        self.assertLess(lv.sleep(), SLEEP * 1.1)

    def test_a_block_that_reads_zero_or_less_gives_no_sleep_level(self):
        lv = capture.Level()
        for _ in range(10):
            lv.add(-1e-6, 1e-6)
        lv.add(0.0, 0.0)                                                # exactly zero has no logarithm: it must not raise
        self.assertIsNone(lv.sleep())
        self.assertIsNone(lv.level())

    def test_the_median_is_a_constant_size_histogram(self):
        lv = capture.Level()
        for i in range(5000):
            lv.add(10 ** (-8 + (i % 70) / 10.0), 1.0)
        self.assertEqual(len(lv.counts), 160)
        self.assertEqual(sum(lv.counts), 5000)


class WhenAWakeIsKept(unittest.TestCase):
    def test_a_steady_load_and_noise_never_trigger(self):
        c = capture.Capturer()
        rng = random.Random(4)
        feed_blocks(c, [(1e-3 * (1 + rng.uniform(-0.03, 0.03)), 1e-3 * 1.1) for _ in range(500)])
        self.assertEqual(c.count, 0)

    def test_the_window_runs_from_the_block_before_to_the_first_quiet_block_after(self):
        c = capture.Capturer()
        feed_blocks(c, quiet(5) + hot(2) + quiet(3))                    # blocks 0-4 asleep, 5 and 6 awake, 7-9 asleep
        self.assertEqual(c.count, 1)
        cap = c.get(1)
        self.assertEqual(cap.data, b"".join(block(k) for k in (4, 5, 6, 7)))
        self.assertEqual(cap.t0, 4.0, "it begins where the block before the wake began")
        self.assertFalse(cap.truncated)
        self.assertEqual(cap.peak, WAKE)

    def test_the_first_block_has_no_block_before_it(self):
        c = capture.Capturer()
        feed_blocks(c, [(WAKE / 2, WAKE)] + quiet(3))
        self.assertEqual(c.count, 0, "with nothing to be asleep against there is no level yet")

    def test_a_wake_longer_than_the_ceiling_is_truncated_and_the_next_is_found(self):
        c = capture.Capturer()
        feed_blocks(c, quiet(100) + hot(30) + quiet(4) + hot(2) + quiet(2))      # a long history: the sleep level is the middle block, so a wake early in a session would pull it up
        first = c.get(1)
        self.assertTrue(first.truncated)
        self.assertEqual(first.blocks, capture.MAX_CAPTURE_BLOCKS)
        self.assertGreaterEqual(c.count, 2)
        self.assertFalse(c.get(c.count).truncated)
        self.assertEqual(c.get(c.count).peak, WAKE)

    def test_only_the_newest_are_kept_and_numbers_keep_rising(self):
        c = capture.Capturer()
        plan = quiet(4)
        for _ in range(12):
            plan += hot(1) + quiet(3)
        feed_blocks(c, plan)
        self.assertEqual(c.count, 12)
        self.assertEqual(c.numbers(), [5, 6, 7, 8, 9, 10, 11, 12])
        self.assertIsNone(c.get(4))

    def test_lost_samples_are_counted_per_capture(self):
        c = capture.Capturer()
        for k, (mean, high) in enumerate(quiet(4) + hot(2) + quiet(2)):
            c.feed(block(k), float(k), mean, high, 3 if k in (4, 5) else 0)
        self.assertEqual(c.get(1).lost, 6, "the block before the wake and the wake block both lost 3")

    def test_consecutive_awake_blocks_are_one_capture(self):
        c = capture.Capturer()
        feed_blocks(c, quiet(100) + hot(6) + quiet(2))
        self.assertEqual(c.count, 1)
        self.assertEqual(c.get(1).blocks, 1 + 6 + 1)


class TheWholePath(Base):
    """The fake unit sleeps (ADC 1000) and wakes (ADC 12000) for one block in ten, through the profiler's source, the sampler and the registry."""
    BLOCKS = 40

    def setUp(self):
        Base.setUp(self)
        from nrf_workbench.instruments import meter, sampler
        self.cal = ppk2.parse_metadata(REAL_META)
        self.unit.profile = lambda i: (WAKE_ADC if (i // 20000) % 10 == 3 else 1000, 0)
        self.sampler = sampler.Sampler(None, self.host.streams.meter, self.folder, ppk2=self.ppk)
        self.addCleanup(self.sampler.stop)
        self.host._services["meter_sampler"] = self.sampler
        self.reg = Registry()
        meter.register(self.reg, self.host)
        self.sleep_a, self.wake_a = reference_amps(self.cal, 0, 1000), reference_amps(self.cal, 0, WAKE_ADC)

    def run_session(self):
        self.sampler.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= self.BLOCKS * 20000, 30))
        self.sampler.stop()

    def call(self, name, args=None):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities={"ppk2", "meter_stream", "meter_store"})

    def test_each_wake_is_kept_with_its_lead_in_and_tail_and_the_right_peak(self):
        self.run_session()
        rows = self.call("meter.captures").data["captures"]
        self.assertGreaterEqual(len(rows), 3)
        r = rows[0]
        self.assertEqual((r["n"], r["samples"], r["lost"], r["truncated"]), (1, 60000, 0, False))
        self.assertAlmostEqual(r["duration_s"], 0.6, places=9)
        self.assertAlmostEqual(r["wake_at_s"], 0.2, places=9, msg="the wake begins where the second block of the capture does")
        self.assertAlmostEqual(r["peak"], self.wake_a, delta=self.wake_a * 1e-9)
        self.assertAlmostEqual(r["mean"], (20000 * self.wake_a + 40000 * self.sleep_a) / 60000.0, delta=r["mean"] * 1e-9)
        self.assertAlmostEqual(r["t0"], 0.4, places=5, msg="the capture begins where the block before the wake began: block 2 of the unit's stream")
        self.assertAlmostEqual(rows[1]["t0"] - rows[0]["t0"], 2.0, places=5, msg="wakes are 2 s apart on the unit's clock")

    def test_lost_samples_are_counted_in_the_capture_they_fall_in_and_no_later_one(self):
        self.unit.lost_at = {70000: 5}                                            # inside the first wake's block (words 60,000 to 79,999)
        self.run_session()
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual([r["lost"] for r in rows[:3]], [5, 0, 0])

    def test_the_raw_words_are_stored_whole(self):
        self.run_session()
        src = self.sampler.sources["ppk2"]
        c = src.capturer.get(1)
        self.assertEqual(len(c.data), 60000 * 4)
        self.assertTrue(c.data[:4] != c.data[80000:84000][:4], "lead-in words are the sleeping ones, the wake's are not")

    def test_the_window_is_an_exact_envelope_at_any_zoom(self):
        self.run_session()
        naive = [self.sleep_a] * 20000 + [self.wake_a] * 20000 + [self.sleep_a] * 20000       # the capture, sample by sample
        for t0, t1, points in ((None, None, 1000), (None, None, 7), (-0.001, 0.004, 5), (-0.2, 0.4, 2000), (0.0, 0.0001, 10)):
            res = self.call("meter.capture", {"n": 1, **({} if t0 is None else {"t0": t0, "t1": t1}), "points": points}).data
            i0 = 0 if t0 is None else 20000 + int(t0 * 100000 // 1)
            i1 = 60000 if t1 is None else 20000 + int(-(-t1 * 100000 // 1))
            seg = naive[i0:i1]
            self.assertAlmostEqual(res["t0"], (i0 - 20000) / 100000.0, places=9)
            self.assertEqual(sum(b["n"] for b in res["buckets"]), len(seg), (t0, t1, points))
            self.assertLessEqual(len(res["buckets"]), points)
            a = i0
            for b in res["buckets"]:
                part = naive[a:a + b["n"]]
                self.assertAlmostEqual(b["min"], min(part), delta=1e-12)
                self.assertAlmostEqual(b["max"], max(part), delta=1e-12)
                self.assertAlmostEqual(b["mean"], sum(part) / len(part), delta=1e-12)
                self.assertAlmostEqual(b["t0"], (a - 20000) / 100000.0, places=9)
                a += b["n"]
            self.assertAlmostEqual(res["charge_c"], sum(seg) / 100000.0, delta=1e-12)
            self.assertAlmostEqual(res["peak"], max(seg), delta=1e-12)

    def test_a_bucket_that_straddles_the_wake_keeps_both_its_lowest_and_highest_sample(self):
        self.run_session()
        res = self.call("meter.capture", {"n": 1, "points": 7}).data                              # 60,000 samples in 7 buckets: 8,572 wide, so the edges at 20,000 and 40,000 fall inside buckets
        straddling = [b for b in res["buckets"] if b["min"] < self.sleep_a * 1.0000001 and b["max"] > self.wake_a * 0.9999999]
        self.assertTrue(straddling)

    def test_a_number_not_held_is_capture_unknown_and_says_what_is_held(self):
        self.run_session()
        res = self.call("meter.capture", {"n": 99})
        self.assertEqual((res.cls, res.code), ("refused", "capture_unknown"))
        self.assertEqual(res.evidence["held"], [r["n"] for r in self.call("meter.captures").data["captures"]])

    def test_an_empty_window_and_bad_points_are_usage_errors(self):
        self.run_session()
        with self.assertRaises(UsageError):
            self.call("meter.capture", {"n": 1, "t0": 5.0, "t1": 6.0})
        with self.assertRaises(UsageError):
            self.call("meter.capture", {"n": 1, "points": 1})
        with self.assertRaises(UsageError):
            self.call("meter.capture", {"n": 1, "points": 2001})

    def test_the_feed_says_how_many_were_taken_so_a_page_need_not_poll(self):
        self.run_session()
        reads = [e.payload for e in self.host.streams.meter.since(0).events if e.type == "meter.reading"]
        counts = [r["facts"]["captures"] for r in reads]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts[0], 0)
        self.assertEqual(counts[-1], len(self.call("meter.captures").data["captures"]))

    def test_the_captures_stay_readable_after_the_session_ends_until_the_next_begins(self):
        self.run_session()
        self.assertEqual(self.sampler.session()["state"], "idle")
        self.assertTrue(self.call("meter.capture", {"n": 1}).ok)
        self.unit.profile = lambda i: (1000, 0)
        self.sampler.start("watch", 60, source="ppk2")
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= 20000, 10))
        self.sampler.stop()
        self.assertEqual(self.call("meter.captures").data["captures"], [])

    def test_a_steady_load_takes_no_captures(self):
        self.unit.profile = lambda i: (1000 + (i % 7), 0)
        self.run_session()
        self.assertEqual(self.call("meter.captures").data["captures"], [])

    def test_a_session_that_is_not_the_profilers_holds_none(self):
        self.sampler._session = types.SimpleNamespace(number=3, thread=None, source=types.SimpleNamespace(name="meter"))
        data = self.call("meter.captures").data
        self.assertEqual((data["session"], data["source"], data["captures"]), (3, "meter", []))
        self.assertEqual(self.call("meter.capture", {"n": 1}).code, "capture_unknown")


if __name__ == "__main__":
    unittest.main()
