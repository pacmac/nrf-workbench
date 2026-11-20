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


def given(**kw):
    """A profile with the sleep level given, so a short plan need not first feed the 20 blocks the data needs to find it."""
    return capture.WakeProfile(sleep_a=SLEEP, **kw)


class TheLevel(unittest.TestCase):
    def test_the_sleep_level_is_the_lowest_tenth_of_the_blocks_to_within_7_5_percent(self):
        lv = capture.Level()
        rng = random.Random(7)
        for _ in range(100):
            lv.add(SLEEP * (1 + rng.uniform(-0.03, 0.03)))
        self.assertAlmostEqual(lv.sleep(), SLEEP, delta=SLEEP * 0.075)

    def test_there_is_no_sleep_level_until_twenty_blocks_have_been_seen(self):
        lv = capture.Level()
        for _ in range(19):
            lv.add(SLEEP)
        self.assertIsNone(lv.sleep(), "at 19 the lowest tenth is the single lowest block, which one stray block could set")
        lv.add(SLEEP)
        self.assertAlmostEqual(lv.sleep(), SLEEP, delta=SLEEP * 0.075)

    def test_one_stray_low_block_does_not_set_the_level(self):
        lv = capture.Level()
        lv.add(SLEEP / 100)
        for _ in range(30):
            lv.add(SLEEP)
        self.assertAlmostEqual(lv.sleep(), SLEEP, delta=SLEEP * 0.075, msg="at 31 blocks the lowest tenth is the third lowest")

    def test_a_device_awake_most_of_the_time_still_has_its_sleep_level_where_the_median_would_not(self):
        lv = capture.Level()
        for _ in range(20):
            lv.add(SLEEP)
        for _ in range(80):
            lv.add(WAKE)                                                # awake four fifths of the time: the median block is awake
        self.assertAlmostEqual(lv.sleep(), SLEEP, delta=SLEEP * 0.075)
        lv2 = capture.Level()
        for _ in range(5):
            lv2.add(SLEEP)
        for _ in range(95):
            lv2.add(WAKE)
        self.assertAlmostEqual(lv2.sleep(), WAKE, delta=WAKE * 0.075, msg="over nine tenths awake there is no sleep to find: the level is what it mostly does")

    def test_blocks_that_read_zero_or_less_say_nothing_about_a_level(self):
        lv = capture.Level()
        for _ in range(40):
            lv.add(-1e-6)
        lv.add(0.0)                                                     # exactly zero has no logarithm: it must not raise
        self.assertEqual(lv.n, 0)
        self.assertIsNone(lv.sleep())
        for _ in range(25):
            lv.add(SLEEP)
        self.assertAlmostEqual(lv.sleep(), SLEEP, delta=SLEEP * 0.075, msg="the blocks that read above zero give it")

    def test_the_histogram_is_a_constant_size(self):
        lv = capture.Level()
        for i in range(5000):
            lv.add(10 ** (-8 + (i % 70) / 10.0))
        self.assertEqual(len(lv.counts), 160)
        self.assertEqual(sum(lv.counts), 5000)


class TheWakeProfile(unittest.TestCase):
    def test_nothing_given_is_everything_found_and_the_defaults_are_derived_ones(self):
        p = capture.WakeProfile()
        self.assertEqual(p.to_dict(), {"sleep_a": None, "awake_a": None, "hold_s": 0.2, "lead_s": 0.2, "max_s": 40.0})
        self.assertEqual((p.hold_blocks, p.lead_blocks, p.max_blocks), (1, 1, 200))

    def test_seconds_become_whole_blocks_without_float_error(self):
        p = capture.WakeProfile(hold_s=0.6, lead_s=0.4, max_s=120)
        self.assertEqual((p.hold_blocks, p.lead_blocks, p.max_blocks), (3, 2, 600), "0.6 / 0.2 is 2.9999999999999996 in floats")
        self.assertEqual(capture.WakeProfile(lead_s=0).lead_blocks, 0)
        self.assertEqual(capture.WakeProfile(hold_s=0.3).hold_blocks, 2, "a part of a block is a whole one: a wake ends no sooner than asked")
        self.assertEqual(3 * 0.2, 0.6000000000000001)
        self.assertEqual(capture.WakeProfile(hold_s=3 * 0.2).hold_blocks, 3, "0.6000000000000001 / 0.2 is 3.0000000000000004: not four blocks")
        self.assertEqual(capture.WakeProfile(max_s=1.4).max_blocks, 7, "1.4 / 0.2 is 6.999999999999999: not six blocks")

    def test_each_bad_value_names_its_field(self):
        for kw, field in (({"sleep_a": 0}, "wake_sleep_a"), ({"sleep_a": -1e-6}, "wake_sleep_a"), ({"awake_a": 0}, "wake_awake_a"), ({"sleep_a": 1e-3, "awake_a": 1e-3}, "wake_awake_a"),
                          ({"max_s": 0.9}, "wake_max_s"), ({"max_s": 121}, "wake_max_s"), ({"hold_s": 0.1}, "wake_hold_s"), ({"hold_s": 41}, "wake_hold_s"),
                          ({"lead_s": -0.1}, "wake_lead_s"), ({"lead_s": 41}, "wake_lead_s"), ({"sleep_a": True}, "wake_sleep_a"), ({"max_s": "40"}, "wake_max_s"),
                          ({"awake_a": float("nan")}, "wake_awake_a"), ({"hold_s": float("inf")}, "wake_hold_s")):
            with self.assertRaises(capture.ProfileError, msg=str(kw)) as cm:
                capture.WakeProfile(**kw)
            self.assertEqual(cm.exception.field, field, kw)

    def test_the_hold_and_the_lead_may_be_as_long_as_the_longest_wake_and_no_longer(self):
        capture.WakeProfile(hold_s=10, lead_s=10, max_s=10)
        with self.assertRaises(capture.ProfileError):
            capture.WakeProfile(hold_s=10.2, max_s=10)
        with self.assertRaises(capture.ProfileError):
            capture.WakeProfile(lead_s=10.2, max_s=10)

    def test_the_memory_a_wake_may_take_is_bounded_at_the_longest_setting(self):
        p = capture.WakeProfile(max_s=capture.MAX_S_MAX)
        raw = p.max_blocks * 20000 * 4
        self.assertEqual(raw, 48000000, "120 s is 48 MB raw")
        self.assertLess(raw / 2.3 * capture.KEEP, 0.05 * 3.8e9, "held compressed (2.3 times, measured), the newest 8 stay under 5 % of the bench Pi's 3.8 GB")


class WhenAWakeIsKept(unittest.TestCase):
    def test_a_steady_load_and_noise_never_trigger(self):
        c = capture.Capturer()
        rng = random.Random(4)
        feed_blocks(c, [(1e-3 * (1 + rng.uniform(-0.03, 0.03)), 1e-3 * 1.1) for _ in range(500)])
        self.assertEqual(c.count, 0)

    def test_the_window_runs_from_the_block_before_to_the_first_quiet_block_after(self):
        c = capture.Capturer(given())
        feed_blocks(c, quiet(5) + hot(2) + quiet(3))                    # blocks 0-4 asleep, 5 and 6 awake, 7-9 asleep
        self.assertEqual(c.count, 1)
        cap = c.get(1)
        self.assertEqual(cap.data, b"".join(block(k) for k in (4, 5, 6, 7)))
        self.assertEqual(cap.t0, 4.0, "it begins where the block before the wake began")
        self.assertFalse(cap.truncated)
        self.assertEqual(cap.peak, WAKE)

    def test_the_lead_in_is_lead_s_of_sleep_before_the_first_awake_block(self):
        c = capture.Capturer(given(lead_s=0.6))
        feed_blocks(c, quiet(10) + hot(2) + quiet(3))                   # 0-9 asleep, 10 and 11 awake, 12-14 asleep
        self.assertEqual(c.get(1).data, b"".join(block(k) for k in (7, 8, 9, 10, 11, 12)), "three blocks of lead-in (0.6 s), the wake, the first quiet block")
        self.assertEqual(c.get(1).t0, 7.0)
        c = capture.Capturer(given(lead_s=0))
        feed_blocks(c, quiet(10) + hot(2) + quiet(3))
        self.assertEqual(c.get(1).data, b"".join(block(k) for k in (10, 11, 12)), "no lead-in: it starts at the first awake block")

    def test_a_lead_in_longer_than_the_data_before_it_is_what_there_is(self):
        c = capture.Capturer(given(lead_s=1.0))
        feed_blocks(c, quiet(2) + hot(1) + quiet(2))
        self.assertEqual(c.get(1).data, b"".join(block(k) for k in (0, 1, 2, 3)))

    def test_a_wake_in_the_first_block_has_no_lead_in_when_the_sleep_level_is_given_and_is_not_seen_when_it_is_not(self):
        c = capture.Capturer(given())
        feed_blocks(c, [(WAKE / 2, WAKE)] + quiet(3))
        self.assertEqual((c.count, c.get(1).data), (1, block(0) + block(1)), "awake from its first block: it starts there, and ends at the first quiet one")
        c = capture.Capturer()
        feed_blocks(c, [(WAKE / 2, WAKE)] + quiet(3))
        self.assertEqual(c.count, 0, "with nothing to be asleep against there is no level to be awake over")

    def test_a_wake_longer_than_the_longest_is_truncated_and_the_next_is_found(self):
        c = capture.Capturer(given(max_s=1))
        self.assertEqual(c.max_blocks, 5)
        feed_blocks(c, quiet(30) + hot(12) + quiet(3) + hot(2) + quiet(2))
        first = c.get(1)
        self.assertTrue(first.truncated)
        self.assertEqual(first.blocks, 5)
        self.assertGreaterEqual(c.count, 3)
        self.assertFalse(c.get(c.count).truncated)
        self.assertEqual(c.get(c.count).peak, WAKE)

    def test_a_lead_in_as_long_as_the_longest_wake_still_leaves_room_for_the_wake(self):
        c = capture.Capturer(given(lead_s=1, max_s=1))
        feed_blocks(c, quiet(30) + hot(1) + quiet(3))
        self.assertEqual((c.get(1).blocks, c.get(1).truncated), (5, True), "four blocks of lead-in and the wake's first: five, the longest, not six")

    def test_a_block_exactly_at_the_awake_level_is_asleep(self):
        c = capture.Capturer(capture.WakeProfile(awake_a=5e-3))
        feed_blocks(c, quiet(3) + [(5e-3, 5e-3)] * 2 + quiet(2))
        self.assertEqual(c.count, 0, "over the level is awake, at it is not")
        feed_blocks(c, quiet(3) + [(5.0001e-3, 5.0001e-3)] + quiet(2))
        self.assertEqual(c.count, 1)

    def test_the_default_longest_wake_is_forty_seconds(self):
        c = capture.Capturer(given())
        feed_blocks(c, quiet(30) + hot(230) + quiet(3))
        self.assertEqual((c.get(1).blocks, c.get(1).truncated), (200, True))

    def test_a_dip_shorter_than_hold_s_does_not_end_a_wake_and_one_as_long_does(self):
        plan = quiet(30) + hot(2) + quiet(2) + hot(2) + quiet(5)
        c = capture.Capturer(given(hold_s=0.6))                        # three quiet blocks end it: the dip of two is part of it
        feed_blocks(c, plan)
        self.assertEqual((c.count, c.get(1).blocks), (1, 1 + 2 + 2 + 2 + 1), "a block of lead-in, the burst, the dip, the burst, the first block back asleep")
        self.assertEqual(c.get(1).data, b"".join(block(k) for k in range(29, 37)))
        c = capture.Capturer(given())                                  # one quiet block ends it: two wakes
        feed_blocks(c, plan)
        self.assertEqual(c.count, 2)

    def test_the_blocks_of_a_dip_that_ended_it_are_not_kept_beyond_the_first(self):
        c = capture.Capturer(given(hold_s=0.6))
        feed_blocks(c, quiet(30) + hot(1) + quiet(6))
        self.assertEqual(c.get(1).data, b"".join(block(k) for k in (29, 30, 31)), "the tail is one block, not the whole hold-off")

    def test_a_cpu_alone_is_a_wake_and_a_spike_early_on_does_not_hide_the_ones_after_it(self):
        cpu = [(SLEEP * 50, SLEEP * 60)]                                # 500 uA for 0.2 s: far over twice the sleep, far under any radio
        c = capture.Capturer()
        feed_blocks(c, quiet(30) + cpu * 3 + quiet(2) + [(1.0, 2.0)] + quiet(3) + hot(2) + quiet(2))      # then a one-block 1 A spike, then a 20 mA wake
        self.assertEqual(c.count, 3, "the CPU's, the spike's and the radio's: no running peak sets a level that hides the later ones")
        self.assertEqual(c.get(1).blocks, 1 + 3 + 1)
        self.assertEqual(c.get(3).peak, WAKE)

    def test_a_device_with_a_light_sleep_over_twice_its_deep_sleep_needs_the_awake_level_given(self):
        light = [(SLEEP * 5, SLEEP * 5.2)]                              # 50 uA between deep sleeps of 10 uA
        plan = quiet(30) + light * 20 + hot(2) + light * 20
        c = capture.Capturer()
        feed_blocks(c, plan)
        self.assertEqual((c.count, c.kept), (1, 0), "found from the data, a light sleep is awake: the first block of it opens a wake that never closes")
        c = capture.Capturer(capture.WakeProfile(awake_a=1e-4))        # 100 uA: over the light sleep, under any wake
        feed_blocks(c, plan)
        self.assertEqual((c.count, c.kept), (1, 1))
        self.assertEqual(c.get(1).blocks, 1 + 2 + 1)

    def test_the_levels_say_what_was_set_and_what_was_found(self):
        c = capture.Capturer()
        self.assertEqual(c.levels(), {"sleep_a": None, "sleep_auto": True, "awake_a": None, "awake_auto": True})
        feed_blocks(c, quiet(25))
        lv = c.levels()
        self.assertAlmostEqual(lv["sleep_a"], SLEEP, delta=SLEEP * 0.075)
        self.assertAlmostEqual(lv["awake_a"], 2 * lv["sleep_a"], delta=1e-15)
        self.assertEqual((lv["sleep_auto"], lv["awake_auto"]), (True, True))
        lv = capture.Capturer(capture.WakeProfile(sleep_a=1e-5, awake_a=3e-5)).levels()
        self.assertEqual(lv, {"sleep_a": 1e-5, "sleep_auto": False, "awake_a": 3e-5, "awake_auto": False})
        lv = capture.Capturer(capture.WakeProfile(sleep_a=1e-5)).levels()
        self.assertEqual((lv["awake_a"], lv["awake_auto"]), (2e-5, True), "the awake level follows a given sleep level")

    def test_an_awake_level_given_needs_no_sleep_level(self):
        c = capture.Capturer(capture.WakeProfile(awake_a=5e-3))
        feed_blocks(c, quiet(3) + hot(2) + quiet(2))
        self.assertEqual((c.count, c.get(1).blocks), (1, 1 + 2 + 1))

    def test_a_receive_window_between_two_bursts_is_part_of_the_one_wake(self):
        """The bench RAK: TX 1 (120 mA), a 20.8 s receive window at 6.5 mA, TX 2 (120 mA), then sleep. The window is under half the peak but far over twice the sleep."""
        c = capture.Capturer()
        window = [(1e-3, 1.1e-3)] * 100                                  # 20 s of 1 mA, 100 times the sleep and a twentieth of the burst's peak
        feed_blocks(c, quiet(1000) + hot(3) + window + hot(2) + quiet(3))
        self.assertEqual(c.count, 1, "two short wakes 21 s apart would be the old reading")
        cap = c.get(1)
        self.assertEqual(cap.blocks, 1 + 3 + 100 + 2 + 1, "one block of lead-in, the bursts, the window, and the first block back near sleep")
        self.assertFalse(cap.truncated)
        self.assertIsNotNone(cap.blob, "closing a wake closes its stream, so the store has the bytes to write without compressing anything")
        self.assertEqual(cap.data, b"".join(block(k) for k in range(999, 999 + 107)))

    def test_a_block_that_reads_zero_or_less_is_asleep_and_ends_a_wake(self):
        c = capture.Capturer(given())
        feed_blocks(c, quiet(30) + hot(1) + [(-1e-6, 1e-6)])              # the unit's noise at the floor can read below zero
        self.assertEqual((c.count, c.kept), (1, 1))

    def test_a_wake_closes_on_the_first_block_back_near_sleep_not_on_one_just_under_the_level(self):
        c = capture.Capturer()
        near = [(SLEEP * 1.5, SLEEP * 3)]                                # under twice the sleep: asleep, whatever its highest sample (the sleep level is good to 7.5 %, so not 1.9)
        over = [(SLEEP * 2.5, SLEEP * 3)]                                # over twice the sleep: still awake
        feed_blocks(c, quiet(100) + hot(1) + over * 4 + near + hot(1) + quiet(2))
        self.assertEqual(c.count, 2, "the block under twice the sleep ended the first wake, so the next burst is another")
        self.assertEqual(c.get(1).blocks, 1 + 1 + 4 + 1)

    def test_the_words_are_compressed_as_each_block_arrives_and_come_back_whole(self):
        import zlib
        cap = capture.Capture(1, 0.0, 1e-3)
        raw = [bytes([k % 7]) * 80000 for k in range(30)]
        for b in raw:
            cap.add(b)
        self.assertLess(sum(len(p) for p in cap._pieces), sum(len(b) for b in raw) / 20, "a flat wake holds a twentieth of its size while open")
        self.assertIsNone(cap.blob, "no stream until it is finished")
        cap.finish()
        cap.finish()
        self.assertEqual(zlib.decompress(cap.blob), b"".join(raw), "one ordinary zlib stream: the store writes it as it is, and older files read the same")
        self.assertEqual(cap.data, b"".join(raw))
        self.assertEqual(cap.samples, 30 * 20000)

    def test_only_the_newest_are_kept_and_numbers_keep_rising(self):
        c = capture.Capturer(given())
        plan = quiet(4)
        for _ in range(12):
            plan += hot(1) + quiet(3)
        feed_blocks(c, plan)
        self.assertEqual(c.count, 12)
        self.assertEqual(c.numbers(), [5, 6, 7, 8, 9, 10, 11, 12])
        self.assertIsNone(c.get(4))

    def test_lost_samples_are_counted_per_capture(self):
        c = capture.Capturer(given())
        for k, (mean, high) in enumerate(quiet(4) + hot(2) + quiet(2)):
            c.feed(block(k), float(k), mean, high, 3 if k in (4, 5) else 0)
        self.assertEqual(c.get(1).lost, 6, "both awake blocks lost 3")
        c = capture.Capturer(given(hold_s=0.6))
        for k, (mean, high) in enumerate(quiet(4) + hot(1) + quiet(1) + hot(1) + quiet(4)):
            c.feed(block(k), float(k), mean, high, 2 if k in (3, 5, 9) else 0)
        self.assertEqual(c.get(1).lost, 4, "block 3 (the lead-in) and block 5 (a dip the wake went on after) count; block 9 is in the hold-off after the tail, which is not the wake's")

    def test_a_wake_is_counted_as_kept_only_when_it_has_finished_and_after_it_was_handed_on(self):
        c = capture.Capturer()
        seen = []
        c.on_close = lambda cap: seen.append((cap.n, c.kept))
        feed_blocks(c, quiet(100) + hot(2))
        self.assertEqual((c.count, c.kept), (1, 0), "begun, not finished")
        for k, (mean, high) in enumerate(quiet(1)):
            c.feed(block(200), 200.0, mean, high, 0)
        self.assertEqual((c.count, c.kept), (1, 1))
        self.assertEqual(seen, [(1, 0)], "on_close runs before the count moves, so what the feed announces can already be read")

    def test_consecutive_awake_blocks_are_one_capture(self):
        c = capture.Capturer()
        feed_blocks(c, quiet(100) + hot(6) + quiet(2))
        self.assertEqual(c.count, 1)
        self.assertEqual(c.get(1).blocks, 1 + 6 + 1)


class PathBase(Base):
    """The fake unit sleeps (ADC 1000) and wakes (ADC 12000) for one block in ten, through the profiler's source, the sampler and the registry."""
    BLOCKS = 40

    def setUp(self):
        Base.setUp(self)
        from nrf_workbench.instruments import meter, sampler
        self.cal = ppk2.parse_metadata(REAL_META)
        self.unit.profile = lambda i: (WAKE_ADC if i < self.BLOCKS * 20000 and (i // 20000) % 10 == 3 else 1000, 0)      # the unit keeps streaming after the test stops waiting: no wake past block 40
        self.sampler = sampler.Sampler(None, self.host.streams.meter, self.folder, ppk2=self.ppk)
        self.addCleanup(self.sampler.stop)
        self.host._services["meter_sampler"] = self.sampler
        self.reg = Registry()
        meter.register(self.reg, self.host)
        self.sleep_a, self.wake_a = reference_amps(self.cal, 0, 1000), reference_amps(self.cal, 0, WAKE_ADC)

    def wake(self):
        """The wake profile of the session: the sleep level is given, so the first wake of a short test is not lost to the 4 s the data needs to find it (AWakeOfTwentySeconds finds it)."""
        return capture.WakeProfile(sleep_a=self.sleep_a)

    def run_session(self):
        self.sampler.start("watch", 60, source="ppk2", wake=self.wake())
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= self.BLOCKS * 20000, 30))
        self.sampler.stop()

    def record_session(self, name):
        self.sampler.start("record", 60, name=name, source="ppk2", wake=self.wake())
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= self.BLOCKS * 20000, 30))
        self.sampler.stop()

    def call(self, name, args=None):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities={"ppk2", "meter_stream", "meter_store"})


class TheWholePath(PathBase):
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


class AWakeOfTwentySeconds(PathBase):
    """The bench RAK's awake period: a radio burst, a 20 s receive window at a twentieth of the burst's current, a second burst, then sleep. It is one wake, kept whole and zoomable."""
    BLOCKS = 260
    WINDOW_ADC = 3000
    TX1, WINDOW, TX2 = 131, 134, 234                                        # the first block of each: 131-133 burst, 134-233 window (20 s), 234-235 burst

    def wake(self):
        return None                                                         # nothing given: the 125 quiet blocks before the wake are what the sleep level is found from

    def setUp(self):
        PathBase.setUp(self)
        t = self

        def profile(i):
            b = i // 20000
            if i >= t.BLOCKS * 20000:
                return 1000, 0
            if t.TX1 <= b < t.WINDOW or t.TX2 <= b < t.TX2 + 2:
                return WAKE_ADC, 0
            if t.WINDOW <= b < t.TX2:
                return t.WINDOW_ADC, 0
            return 1000, 0
        self.unit.profile = profile
        self.window_a = reference_amps(self.cal, 0, self.WINDOW_ADC)

    def test_it_is_one_capture_with_the_whole_receive_window_in_it(self):
        self.record_session("rak-1")
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual(len(rows), 1, [(r["n"], r["duration_s"]) for r in rows])
        r = rows[0]
        self.assertEqual((r["samples"], r["lost"], r["truncated"]), (107 * 20000, 0, False))
        self.assertAlmostEqual(r["duration_s"], 21.4, places=9)
        expected = (20000 * 2 * self.sleep_a + 20000 * 5 * self.wake_a + 20000 * 100 * self.window_a) / (107 * 20000.0)
        self.assertAlmostEqual(r["mean"], expected, delta=expected * 1e-9)

    def test_any_stretch_of_it_can_be_zoomed_into(self):
        self.record_session("rak-1")
        whole = self.call("meter.capture", {"n": 1, "points": 1000}).data
        self.assertAlmostEqual(whole["t1"] - whole["t0"], 21.4, places=9)
        self.assertEqual(sum(b["n"] for b in whole["buckets"]), 107 * 20000)
        mid = self.call("meter.capture", {"n": 1, "t0": 5.0, "t1": 6.0, "points": 100}).data
        self.assertEqual(sum(b["n"] for b in mid["buckets"]), 100000)
        for b in mid["buckets"]:
            self.assertAlmostEqual(b["min"], self.window_a, delta=self.window_a * 1e-9)
            self.assertAlmostEqual(b["max"], self.window_a, delta=self.window_a * 1e-9)
        edge = self.call("meter.capture", {"n": 1, "t0": 3.0 * 0.2 - 0.001, "t1": 3.0 * 0.2 + 0.001, "points": 20}).data       # the end of the first burst: the unit's step from burst to window
        highs = [b["max"] for b in edge["buckets"]]
        self.assertAlmostEqual(max(highs), self.wake_a, delta=self.wake_a * 1e-9)
        self.assertAlmostEqual(min(b["min"] for b in edge["buckets"]), self.window_a, delta=self.window_a * 1e-9)

    def test_it_is_saved_and_read_back_whole(self):
        self.record_session("rak-1")
        disk = self.call("meter.captures", {"recording": "rak-1"}).data["captures"]
        self.assertEqual([(r["n"], r["samples"]) for r in disk], [(1, 107 * 20000)])
        mem = self.call("meter.capture", {"n": 1, "points": 50}).data
        back = self.call("meter.capture", {"n": 1, "points": 50, "recording": "rak-1"}).data
        self.assertEqual(mem, back)
        self.assertLess(os.path.getsize(os.path.join(self.folder, "rak-1.wakes", "1.bin")), 107 * 20000 * 4 / 3)


class TheWakeProfileOfASession(PathBase):
    """The profile is chosen when a session starts (meter.watch and meter.record take wake_*), reaches the capture, and is reported with the session and in the recording's sidecar."""

    def start(self, command="meter.watch", **args):
        res = self.call(command, dict({"bound_s": 60, "source": "ppk2"}, **args))
        self.assertTrue(res.ok, res.message if hasattr(res, "message") else res)
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= self.BLOCKS * 20000, 30))
        self.sampler.stop()

    def test_nothing_given_says_everything_was_found_and_the_found_levels_are_reported(self):
        self.start()
        s = self.sampler.session()
        self.assertEqual(s["wake"], {"sleep_a": None, "awake_a": None, "hold_s": 0.2, "lead_s": 0.2, "max_s": 40.0})
        self.assertAlmostEqual(s["sleep_a"], self.sleep_a, delta=self.sleep_a * 0.075, msg="the sleep level the data gave")
        self.assertAlmostEqual(s["awake_a"], 2 * s["sleep_a"], delta=s["sleep_a"] * 1e-3)
        self.assertEqual(len(self.call("meter.captures").data["captures"]), 2, "the wakes of blocks 23 and 33: the first 20 blocks (4 s) are what the sleep level is found from")

    def test_what_is_given_reaches_the_capture_and_is_reported_as_given(self):
        self.start(wake_sleep_a=self.sleep_a, wake_hold_s=0.6, wake_lead_s=0.4, wake_max_s=30)
        s = self.sampler.session()
        self.assertEqual(s["wake"], {"sleep_a": self.sleep_a, "awake_a": None, "hold_s": 0.6, "lead_s": 0.4, "max_s": 30.0})
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["samples"], (2 + 1 + 1) * 20000, "two blocks of lead-in (0.4 s, though only 3 blocks came before the first wake), the wake, and one tail block: the rest of the hold-off is not kept")
        self.assertEqual(self.sampler.sources["ppk2"].capturer.max_blocks, 150, "30 s")

    def test_an_awake_level_given_finds_wakes_from_the_first_block(self):
        self.start(wake_awake_a=(self.sleep_a + self.wake_a) / 2)
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual(len(rows), 4, "no sleep level to wait for")

    def test_a_bad_value_is_a_usage_error_that_names_its_field(self):
        for args, field in (({"wake_max_s": 0.5}, "wake_max_s"), ({"wake_hold_s": 0.05}, "wake_hold_s"), ({"wake_sleep_a": -1.0}, "wake_sleep_a"),
                            ({"wake_sleep_a": 1e-3, "wake_awake_a": 1e-4}, "wake_awake_a"), ({"wake_lead_s": 99.0}, "wake_lead_s")):
            with self.assertRaises(UsageError, msg=str(args)) as cm:
                self.call("meter.watch", dict({"bound_s": 60, "source": "ppk2"}, **args))
            self.assertEqual(cm.exception.arg, field, args)
        self.assertEqual(self.sampler.session()["state"], "idle", "nothing started")

    def test_a_multimeter_session_has_no_wake_profile_to_set(self):
        self.sampler.sources["meter"] = types.SimpleNamespace(name="meter")
        with self.assertRaises(UsageError) as cm:
            self.call("meter.watch", {"bound_s": 60, "source": "meter", "wake_max_s": 30.0, "wake_hold_s": 1.0})
        self.assertEqual(cm.exception.arg, "wake_hold_s")
        self.assertEqual(self.sampler.session()["state"], "idle")

    def test_a_recording_keeps_the_profile_and_the_levels_found_in_its_sidecar(self):
        import json
        self.start("meter.record", name="rak-1", wake_hold_s=0.4)
        with open(os.path.join(self.folder, "rak-1.json")) as fh:
            side = json.load(fh)
        self.assertEqual(side["wake"]["hold_s"], 0.4)
        self.assertAlmostEqual(side["sleep_a"], self.sleep_a, delta=self.sleep_a * 0.075)
        self.assertIn("awake_a", side)

    def test_the_levels_ride_every_reading_to_four_figures(self):
        self.start()
        reads = [e.payload for e in self.host.streams.meter.since(0).events if e.type == "meter.reading"]
        with_levels = [r["facts"] for r in reads if "awake_a" in r["facts"]]
        self.assertTrue(with_levels)
        self.assertNotIn("awake_a", reads[0]["facts"], "none before the data can give a sleep level")
        for f in with_levels:
            self.assertEqual(float("%.4g" % f["awake_a"]), f["awake_a"])


class KeptWithTheRecording(PathBase):
    """Stage 2, wakes: a recording writes each wake to <name>.wakes/ as it closes and reads them back without the unit; a watch writes nothing."""

    def test_a_recording_saves_every_wake_with_the_calibration_and_a_watch_saves_none(self):
        self.record_session("rak-1")
        d = os.path.join(self.folder, "rak-1.wakes")
        names = sorted(os.listdir(d))
        self.assertIn("cal.json", names)
        self.assertEqual([n for n in names if n.endswith(".json") and n != "cal.json"], ["1.json", "2.json", "3.json", "4.json"])
        raw = 60000 * 4
        self.assertLess(os.path.getsize(os.path.join(d, "1.bin")), raw / 3, "it must shrink (about 3.5 times: the counter bits still change every word)")
        self.run_session()                                                                # a watch
        self.assertEqual([f for f in os.listdir(self.folder) if f.endswith(".wakes")], ["rak-1.wakes"])

    def test_the_saved_wakes_read_back_as_the_ones_in_memory_did(self):
        self.record_session("rak-1")
        mem = self.call("meter.captures").data["captures"]
        disk = self.call("meter.captures", {"recording": "rak-1"}).data["captures"]
        self.assertEqual(len(disk), len(mem))
        for a, b in zip(mem, disk):
            for key in ("n", "t0", "samples", "lost", "truncated", "wake_at_s", "duration_s"):
                self.assertEqual(a[key], b[key], key)
            self.assertAlmostEqual(a["peak"], b["peak"], delta=a["peak"] * 1e-12)
            self.assertAlmostEqual(a["mean"], b["mean"], delta=a["mean"] * 1e-12)
        for args in ({}, {"t0": -0.001, "t1": 0.004, "points": 5}):
            m = self.call("meter.capture", dict(args, n=2)).data
            k = self.call("meter.capture", dict(args, n=2, recording="rak-1")).data
            self.assertEqual(m, k)

    def test_listing_a_saved_wake_writes_its_figures_back_so_it_is_not_decoded_again(self):
        self.record_session("rak-1")
        import json
        path = os.path.join(self.folder, "rak-1.wakes", "1.json")
        def stats():
            with open(path) as fh:
                return json.load(fh)["stats"]
        self.assertIsNone(stats(), "the session thread must not decode: it saves the words and nothing else")
        self.call("meter.captures", {"recording": "rak-1"})
        self.assertAlmostEqual(stats()["wake_at_s"], 0.2, places=9)

    def test_only_the_newest_twenty_are_kept_on_disk(self):
        store = capture.WakeStore(os.path.join(self.folder, "x.wakes"))
        for n in range(1, 26):
            c = capture.Capture(n, float(n), 1e-3)
            c.add(n.to_bytes(4, "little") * 8)
            store.save(c)
        self.assertEqual(store.numbers(), list(range(6, 26)))
        loaded = store.load()
        self.assertEqual(loaded[0].data, (6).to_bytes(4, "little") * 8)
        self.assertEqual(loaded[0].samples, 8)

    def test_a_wake_with_a_json_always_has_its_words(self):
        store = capture.WakeStore(os.path.join(self.folder, "x.wakes"))
        c = capture.Capture(1, 0.0, 1e-3)
        c.add(b"abcd" * 4)
        store.save(c)
        os.unlink(os.path.join(store.dir, "1.bin"))
        with self.assertRaises(OSError):
            store.load()[0].data

    def test_a_disk_that_will_not_take_the_wakes_does_not_stop_the_session_and_says_so(self):
        open(os.path.join(self.folder, "rak-1.wakes"), "w").close()                      # a file where the folder should be
        self.record_session("rak-1")
        self.assertEqual(self.sampler.session()["state"], "idle")
        self.assertGreaterEqual(self.sampler.session()["wakes_unsaved"], 1)
        self.assertGreaterEqual(len(self.call("meter.captures").data["captures"]), 3, "the wakes are still held in memory")

    def test_deleting_an_old_recording_deletes_its_wakes_and_stages(self):
        for k in range(10):
            base = os.path.join(self.folder, "old-%d" % k)
            open(base + ".csv", "w").close()
            os.makedirs(base + ".wakes")
            with open(base + ".stages.json", "w") as fh:
                fh.write('{"stages": []}')
            os.utime(base + ".csv", (1000 + k, 1000 + k))
        self.sampler.start("record", 60, name="new", source="ppk2")
        self.sampler.stop()
        self.assertFalse(os.path.exists(os.path.join(self.folder, "old-0.csv")))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "old-0.wakes")))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "old-0.stages.json")))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "old-1.wakes")))

    def test_an_unknown_recording_is_recording_unknown_for_all_of_them(self):
        for name, args in (("meter.captures", {"recording": "nope"}), ("meter.capture", {"n": 1, "recording": "nope"}), ("meter.stages", {"recording": "nope"}),
                           ("meter.stage_set", {"recording": "nope", "name": "a", "start_s": 0.0, "end_s": 1.0}), ("meter.stage_delete", {"recording": "nope", "name": "a"}),
                           ("meter.captures", {"recording": "../x"})):
            self.assertEqual(self.call(name, args).code, "recording_unknown", name)


class EveryWakeIsLinedUpOnTheSameEdge(PathBase):
    """On the RAK the first wake's CPU phase made the session's peak 9 mA, so its capture's level was 4.5 mA and its time origin the CPU's start; by the second the peak was the radio's. Every wake's
    origin is the first sample over half of its OWN highest sample, so the radio edge is t = 0 in both."""

    def setUp(self):
        PathBase.setUp(self)
        cpu, radio = 3000, 12000
        # wake A: block 3 is the CPU alone, block 4 the radio. Wake B, later, is the radio alone (block 13)
        self.unit.profile = lambda i: ((cpu, 0) if 3 * 20000 <= i < 4 * 20000 else (radio, 0) if (4 * 20000 <= i < 5 * 20000 or 13 * 20000 <= i < 14 * 20000) else (1000, 0))
        self.cpu_a, self.radio_a = reference_amps(self.cal, 0, cpu), reference_amps(self.cal, 0, radio)

    def test_the_radio_edge_is_time_zero_in_a_wake_that_began_with_the_cpu_and_in_one_that_did_not(self):
        self.run_session()
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual([r["n"] for r in rows[:2]], [1, 2])
        self.assertAlmostEqual(rows[0]["wake_at_s"], 0.4, places=9, msg="blocks 2 (asleep) and 3 (the CPU) come first, then the radio")
        self.assertAlmostEqual(rows[1]["wake_at_s"], 0.2, places=9)
        for n in (1, 2):
            env = self.call("meter.capture", {"n": n, "t0": -0.0002, "t1": 0.0002, "points": 4}).data
            before, after = env["buckets"][0], env["buckets"][-1]
            self.assertLess(before["max"], self.radio_a / 2, "before time zero it is not the radio")
            self.assertAlmostEqual(after["min"], self.radio_a, delta=self.radio_a * 1e-9)
        self.assertAlmostEqual(self.call("meter.capture", {"n": 1, "t0": -0.0002, "t1": -0.0001, "points": 2}).data["peak"], self.cpu_a, delta=self.cpu_a * 1e-9,
                               msg="in the first wake the CPU phase is just before the edge")


class HoldingOutTheRangeGlitch(unittest.TestCase):
    """The range switch puts a glitch in the first samples after it (a real RAK wake: 221 mA and 139 mA either side of a 120 mA edge). The session's reduction holds 3 out; so does a wake."""

    def setUp(self):
        self.cal = ppk2.parse_metadata(REAL_META)
        self.a0, self.a1 = reference_amps(self.cal, 0, 1000), reference_amps(self.cal, 1, 2000)

    def words(self, plan):
        return b"".join(__import__("struct").pack("<I", (adc & 0x3FFF) | (rng << 14) | ((i & 63) << 18)) for i, (adc, rng) in enumerate(plan))

    def test_the_three_samples_from_a_range_change_take_the_value_before_them(self):
        plan = [(1000, 0)] * 10 + [(16000, 1)] * 3 + [(2000, 1)] * 7
        vals, held = capture.decode(self.words(plan), self.cal.amps, hold=3)
        self.assertEqual(held, 3)
        self.assertEqual(len(vals), 20, "a wake keeps its length")
        self.assertEqual(list(vals[:13]), [self.a0] * 13, "the glitch is not in it")
        self.assertEqual(list(vals[13:]), [self.a1] * 7)
        raw, none = capture.decode(self.words(plan), self.cal.amps)
        self.assertEqual(none, 0)
        self.assertGreater(max(raw), 10e-3, "without the hold the glitch is the peak")

    def test_a_second_change_inside_the_hold_starts_it_again_and_the_first_sample_is_never_held(self):
        plan = [(1000, 0), (16000, 1), (16000, 2), (16000, 0), (1000, 0), (1000, 0), (1000, 0)]
        vals, held = capture.decode(self.words(plan), self.cal.amps, hold=3)
        self.assertEqual(vals[0], self.a0)
        self.assertEqual(list(vals[:6]), [self.a0] * 6, "1 changes to 2 changes to 0 changes: held until 3 samples after the last change")
        self.assertEqual(held, 5)

    def test_no_range_change_holds_nothing(self):
        vals, held = capture.decode(self.words([(1000, 0)] * 8), self.cal.amps, hold=3)
        self.assertEqual((held, list(vals)), (0, [self.a0] * 8))


class AWakeInTheSessionHasNoGlitchInIt(PathBase):
    def setUp(self):
        PathBase.setUp(self)
        # the wake is in range 1 and its first two samples are the glitch; coming back to range 0 holds three more
        self.unit.profile = lambda i: ((16000, 1) if i in (60000, 60001) else (2000, 1) if i < self.BLOCKS * 20000 and (i // 20000) % 10 == 3 else (1000, 0))
        self.a1 = reference_amps(self.cal, 1, 2000)

    def test_the_peak_and_the_held_count_of_a_wake_are_the_sessions_not_the_glitchs(self):
        self.run_session()
        row = self.call("meter.captures").data["captures"][0]
        self.assertAlmostEqual(row["peak"], self.a1, delta=self.a1 * 1e-9)
        self.assertEqual(row["held"], 6, "3 from the change into range 1 (the two glitch samples and the one after) and 3 from the change back to range 0")

    def test_the_envelope_and_the_stage_table_carry_no_glitch_either(self):
        self.record_session("rak-1")
        env = self.call("meter.capture", {"n": 1, "points": 2000}).data
        self.assertAlmostEqual(env["peak"], self.a1, delta=self.a1 * 1e-9)
        self.assertAlmostEqual(max(b["max"] for b in env["buckets"]), self.a1, delta=self.a1 * 1e-9)
        self.call("meter.stage_set", {"recording": "rak-1", "name": "burst", "start_s": -0.01, "end_s": 0.2})
        s = self.call("meter.stages", {"recording": "rak-1"}).data["stages"][0]
        self.assertAlmostEqual(s["peak"], self.a1, delta=self.a1 * 1e-9)


class StageRules(unittest.TestCase):
    def test_a_stage_is_added_changed_and_kept_in_order_of_start(self):
        s = capture.set_stage([], "radio", 0.05, 0.1)
        s = capture.set_stage(s, "cpu", 0.0, 0.05)
        self.assertEqual([x["name"] for x in s], ["cpu", "radio"])
        s = capture.set_stage(s, "RADIO", 0.05, 0.12)                                  # the same name ignoring case changes it
        self.assertEqual([(x["name"], x["end_s"]) for x in s], [("cpu", 0.05), ("RADIO", 0.12)])

    def test_the_rules_each_refuse_naming_the_argument(self):
        base = capture.set_stage([], "a", 0.0, 1.0)
        for args, field in (((base, "", 2.0, 3.0), "name"), ((base, "x" * 41, 2.0, 3.0), "name"), ((base, "   ", 2.0, 3.0), "name"), ((base, None, 2.0, 3.0), "name"),
                            ((base, "b", 3.0, 3.0), "end_s"), ((base, "b", 3.0, 2.0), "end_s"), ((base, "b", "x", 2.0), "start_s"), ((base, "b", 2.0, float("inf")), "end_s"),
                            ((base, "b", True, 2.0), "start_s"), ((base, "b", 0.5, 1.5), "start_s"), ((base, "b", -1.0, 0.1), "start_s")):
            with self.assertRaises(capture.StageError) as cm:
                capture.set_stage(*args)
            self.assertEqual(cm.exception.field, field, args)
        capture.set_stage(base, "b", 1.0, 2.0)                                         # touching is not overlapping

    def test_no_more_than_twelve(self):
        s = []
        for k in range(12):
            s = capture.set_stage(s, "s%d" % k, k, k + 0.5)
        with self.assertRaises(capture.StageError):
            capture.set_stage(s, "s12", 20, 21)
        self.assertEqual(len(capture.set_stage(s, "s3", 3, 3.2)), 12, "changing one is not adding one")

    def test_removing_and_loading(self):
        s = capture.set_stage([], "a", 0.0, 1.0)
        self.assertIsNone(capture.remove_stage(s, "zz"))
        self.assertEqual(capture.remove_stage(s, "A"), [])
        d = tempfile_dir()
        path = os.path.join(d, "x.stages.json")
        self.assertEqual(capture.load_stages(path), [])
        with open(path, "w") as fh:
            fh.write("not json")
        self.assertEqual(capture.load_stages(path), [])
        capture.save_stages(path, s)
        self.assertEqual(capture.load_stages(path), s)


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp(prefix="nrfwb-stages-")


class TheStageTable(PathBase):
    def setUp(self):
        PathBase.setUp(self)
        self.record_session("rak-1")

    def add(self, name, a, b):
        return self.call("meter.stage_set", {"recording": "rak-1", "name": name, "start_s": a, "end_s": b})

    def test_each_stage_is_exact_against_the_raw_samples_and_the_shares_add_up(self):
        self.add("lead", -0.2, 0.0)
        self.add("burst", 0.0, 0.2)
        self.add("tail", 0.2, 0.4)
        t = self.call("meter.stages", {"recording": "rak-1"}).data
        by = {s["name"]: s for s in t["stages"]}
        self.assertEqual([s["name"] for s in t["stages"]], ["lead", "burst", "tail"])
        self.assertEqual(t["wakes"], 4)
        b = by["burst"]
        self.assertAlmostEqual(b["mean"], self.wake_a, delta=self.wake_a * 1e-9)
        self.assertAlmostEqual(b["charge_c"], 0.2 * self.wake_a, delta=1e-12)
        self.assertEqual(b["peak"], self.wake_a)
        self.assertEqual(b["wakes"], 4)
        self.assertAlmostEqual(b["length_s"], 0.2, places=12)
        self.assertAlmostEqual(by["lead"]["mean"], self.sleep_a, delta=self.sleep_a * 1e-9)
        self.assertAlmostEqual(sum(s["share"] for s in t["stages"]), 1.0, places=9)
        self.assertAlmostEqual(b["share"], 0.2 * self.wake_a / (0.2 * self.wake_a + 0.4 * self.sleep_a), places=9)
        self.assertEqual(len(t["groups"]), 1)
        self.assertAlmostEqual(t["groups"][0]["untagged_charge_c"], 0.0, delta=1e-12, msg="the three stages cover the whole capture")
        self.assertAlmostEqual(t["groups"][0]["tagged_charge_c"], 0.2 * self.wake_a + 0.4 * self.sleep_a, delta=1e-12)
        self.assertEqual(t["groups"][0]["numbers"], [1, 2, 3, 4])
        self.assertEqual(by["burst"]["numbers"], [1, 2, 3, 4])

    def test_a_stage_is_in_seconds_from_the_wake_start_so_a_narrower_one_sees_less_of_it(self):
        self.add("early", 0.0, 0.05)
        e = self.call("meter.stages", {"recording": "rak-1"}).data["stages"][0]
        self.assertAlmostEqual(e["charge_c"], 0.05 * self.wake_a, delta=1e-12)
        self.assertAlmostEqual(self.call("meter.stages", {"recording": "rak-1"}).data["groups"][0]["untagged_charge_c"], 0.15 * self.wake_a + 0.4 * self.sleep_a, delta=1e-12)

    def test_a_stage_outside_every_capture_contributes_nothing(self):
        self.add("later", 5.0, 6.0)
        s = self.call("meter.stages", {"recording": "rak-1"}).data["stages"][0]
        self.assertEqual((s["wakes"], s["mean"], s["charge_c"], s["share"], s["peak"]), (0, None, None, None, None))

    def test_energy_is_charge_times_volts_when_volts_are_given_and_absent_otherwise(self):
        self.add("burst", 0.0, 0.2)
        self.assertIsNone(self.call("meter.stages", {"recording": "rak-1"}).data["stages"][0]["energy_j"])
        j = self.call("meter.stages", {"recording": "rak-1", "volts": 4.0}).data["stages"][0]["energy_j"]
        self.assertAlmostEqual(j, 0.2 * self.wake_a * 4.0, delta=1e-12)

    def test_stages_are_stored_with_the_recording_and_survive_the_sampler(self):
        self.add("burst", 0.0, 0.2)
        from nrf_workbench.instruments import capture as c2
        self.assertEqual(c2.load_stages(os.path.join(self.folder, "rak-1.stages.json")), [{"name": "burst", "start_s": 0.0, "end_s": 0.2, "every": 1, "offset": 0}])

    def test_delete_removes_and_an_unknown_stage_is_stage_unknown(self):
        self.add("burst", 0.0, 0.2)
        res = self.call("meter.stage_delete", {"recording": "rak-1", "name": "nope"})
        self.assertEqual((res.cls, res.code), ("refused", "stage_unknown"))
        self.assertEqual(self.call("meter.stage_delete", {"recording": "rak-1", "name": "BURST"}).data["stages"], [])

    def test_a_bad_stage_is_a_usage_error_naming_the_argument(self):
        self.add("burst", 0.0, 0.2)
        with self.assertRaises(UsageError) as cm:
            self.add("other", 0.1, 0.3)
        self.assertEqual(cm.exception.arg, "start_s")
        with self.assertRaises(UsageError) as cm:
            self.add("", 1.0, 2.0)
        self.assertEqual(cm.exception.arg, "name")

    def test_a_recording_with_no_wakes_has_a_table_of_empty_stages(self):
        open(os.path.join(self.folder, "plain.csv"), "w").close()
        self.call("meter.stage_set", {"recording": "plain", "name": "x", "start_s": 0.0, "end_s": 0.1})
        t = self.call("meter.stages", {"recording": "plain"}).data
        self.assertEqual((t["wakes"], t["stages"][0]["wakes"], t["stages"][0]["mean"]), (0, 0, None))


class WakesOfDifferentKinds(PathBase):
    """The RAK alternates two shapes of wake: odd wakes are one kind, even wakes another. Stages for each, over only their own wakes."""
    HI_ADC, LO_ADC = 12000, 9000

    def setUp(self):
        PathBase.setUp(self)
        self.unit.profile = lambda i: ((self.HI_ADC if ((i // 20000) // 10) % 2 == 0 else self.LO_ADC) if i < self.BLOCKS * 20000 and (i // 20000) % 10 == 3 else 1000, 0)
        self.hi, self.lo = reference_amps(self.cal, 0, self.HI_ADC), reference_amps(self.cal, 0, self.LO_ADC)
        self.record_session("rak-1")

    def add(self, name, every, offset, a=0.0, b=0.2):
        return self.call("meter.stage_set", {"recording": "rak-1", "name": name, "start_s": a, "end_s": b, "every": every, "offset": offset})

    def test_each_stage_sees_only_its_own_wakes_and_the_same_times_may_be_used_twice(self):
        self.add("beacon", 2, 1)                                   # the same 0 to 0.2 s, for the odd wakes
        self.add("second", 2, 0)                                   # and for the even ones: not an overlap, they never share a wake
        t = self.call("meter.stages", {"recording": "rak-1"}).data
        by = {s["name"]: s for s in t["stages"]}
        self.assertEqual((by["beacon"]["numbers"], by["second"]["numbers"]), ([1, 3], [2, 4]))
        self.assertAlmostEqual(by["beacon"]["mean"], self.hi, delta=self.hi * 1e-9)
        self.assertAlmostEqual(by["second"]["mean"], self.lo, delta=self.lo * 1e-9)
        self.assertEqual(by["beacon"]["lowest_mean"], by["beacon"]["highest_mean"], "within a kind the wakes are alike")
        self.assertEqual([(g["every"], g["offset"], g["numbers"], g["stages"]) for g in t["groups"]], [(2, 0, [2, 4], ["second"]), (2, 1, [1, 3], ["beacon"])])
        self.assertAlmostEqual(by["beacon"]["share"], 1.0, places=9)
        self.assertAlmostEqual(by["second"]["share"], 1.0, places=9, msg="a share is of the stages that apply to the same wakes")
        for g, own in zip(t["groups"], (self.lo, self.hi)):
            self.assertAlmostEqual(g["tagged_charge_c"], 0.2 * own, delta=1e-12)
            self.assertAlmostEqual(g["untagged_charge_c"], 0.4 * self.sleep_a, delta=1e-12 + 0.0, msg="the rest of that kind's capture")

    def test_without_a_selector_both_kinds_are_averaged_and_the_spread_shows_it(self):
        self.add("both", 1, 0)
        s = self.call("meter.stages", {"recording": "rak-1"}).data["stages"][0]
        self.assertAlmostEqual(s["mean"], (2 * self.hi + 2 * self.lo) / 4, delta=self.hi * 1e-9)
        self.assertAlmostEqual(s["lowest_mean"], self.lo, delta=self.lo * 1e-9)
        self.assertAlmostEqual(s["highest_mean"], self.hi, delta=self.hi * 1e-9)

    def test_stages_that_can_share_a_wake_may_not_overlap_in_time(self):
        self.add("a", 2, 1, 0.0, 0.2)
        for every, offset in ((1, 0), (2, 1), (4, 1), (4, 3)):      # 1/0 holds for every wake; 4/1 and 4/3 are odd wakes; 2/1 is the same
            with self.assertRaises(UsageError, msg=(every, offset)) as cm:
                self.add("b", every, offset, 0.1, 0.3)
            self.assertEqual(cm.exception.arg, "start_s")
        for every, offset in ((2, 0), (4, 0), (4, 2)):              # even wakes: never an odd one
            self.add("b", every, offset, 0.1, 0.3)

    def test_the_selector_is_stored_and_a_bad_one_names_the_argument(self):
        self.add("a", 2, 1)
        import json
        with open(os.path.join(self.folder, "rak-1.stages.json")) as fh:
            self.assertEqual(json.load(fh)["stages"], [{"name": "a", "start_s": 0.0, "end_s": 0.2, "every": 2, "offset": 1}])
        for every, offset, arg in ((0, 0, "every"), (9, 0, "every"), (2, 2, "offset"), (2, -1, "offset"), (1, 1, "offset")):
            with self.assertRaises(UsageError) as cm:
                self.add("z", every, offset)
            self.assertEqual(cm.exception.arg, arg, (every, offset))


class TheSelectorRules(unittest.TestCase):
    def test_two_selectors_share_a_wake_exactly_when_the_offsets_agree_modulo_the_gcd(self):
        for e1 in range(1, 9):
            for o1 in range(e1):
                for e2 in range(1, 9):
                    for o2 in range(e2):
                        brute = any(n % e1 == o1 and n % e2 == o2 for n in range(1, 200))
                        self.assertEqual(capture.shares_a_wake(e1, o1, e2, o2), brute, (e1, o1, e2, o2))

    def test_an_old_stage_file_without_selectors_means_every_wake(self):
        d = tempfile_dir()
        path = os.path.join(d, "x.stages.json")
        with open(path, "w") as fh:
            fh.write('{"stages": [{"name": "a", "start_s": 0.0, "end_s": 1.0}]}')
        self.assertEqual(capture.load_stages(path), [{"name": "a", "start_s": 0.0, "end_s": 1.0, "every": 1, "offset": 0}])

    def test_a_non_integer_or_boolean_selector_is_refused(self):
        for every, offset in ((True, 0), (2.0, 0), ("2", 0), (2, True), (2, 0.0)):
            with self.assertRaises(capture.StageError):
                capture.set_stage([], "a", 0.0, 1.0, every, offset)


class TheSpreadAcrossWakes(PathBase):
    def setUp(self):
        PathBase.setUp(self)
        # each wake takes a little less than the last: ADC 12000, 11000, 10000, 9000 for the wakes in blocks 3, 13, 23, 33
        self.unit.profile = lambda i: ((WAKE_ADC - 1000 * ((i // 20000) // 10)) if i < self.BLOCKS * 20000 and (i // 20000) % 10 == 3 else 1000, 0)

    def test_the_stage_gives_the_mean_of_the_wakes_and_how_far_they_differ(self):
        self.record_session("rak-1")
        self.call("meter.stage_set", {"recording": "rak-1", "name": "burst", "start_s": 0.0, "end_s": 0.2})
        s = self.call("meter.stages", {"recording": "rak-1"}).data["stages"][0]
        per = [reference_amps(self.cal, 0, WAKE_ADC - 1000 * k) for k in range(4)]
        self.assertEqual(s["wakes"], 4)
        self.assertAlmostEqual(s["mean"], sum(per) / 4, delta=per[0] * 1e-9)
        self.assertAlmostEqual(s["lowest_mean"], min(per), delta=per[0] * 1e-9)
        self.assertAlmostEqual(s["highest_mean"], max(per), delta=per[0] * 1e-9)
        self.assertAlmostEqual(s["peak"], max(per), delta=per[0] * 1e-9)
        self.assertGreater(s["highest_mean"] - s["lowest_mean"], 0.2 * per[0], "the wakes must really differ, or this proves nothing")


if __name__ == "__main__":
    unittest.main()
