#!/usr/bin/env python3
"""Tests for the session envelope and statistics (docs/specs/meter-page.md): bounded memory with nothing it reports lost.
Run:  python3 tests/instruments/test_envelope.py
"""
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.instruments.envelope import MAX_BUCKETS, Envelope, Stats  # noqa: E402


def fill(env, values, step=0.2):
    for i, v in enumerate(values):
        env.add(i * step, v)
    return env


class Bounded(unittest.TestCase):
    def test_it_never_holds_more_than_its_bound_however_many_readings_arrive(self):
        env = Envelope(max_buckets=8)
        for i in range(10000):
            env.add(i * 0.2, float(i % 7))
            self.assertLessEqual(len(env.snapshot()), 8)

    def test_the_real_bound_is_2000_and_a_session_of_12_hours_fits(self):
        self.assertEqual(MAX_BUCKETS, 2000)
        env = Envelope()
        for i in range(216000):                                   # 12 h at 0.2 s
            env.add(i * 0.2, 0.001)
        snap = env.snapshot()
        self.assertLessEqual(len(snap), 2000)
        self.assertEqual(sum(b["n"] for b in snap), 216000)

    def test_a_room_for_fewer_than_two_buckets_is_refused(self):
        for bad in (0, 1, -3, 2.5, "8", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                Envelope(max_buckets=bad)


class Exact(unittest.TestCase):
    """A merge keeps the lowest, the highest, the count and the sum: nothing the envelope reports is approximate."""

    def test_count_sum_min_and_max_survive_every_merge(self):
        rng = random.Random(7)
        values = [rng.uniform(-1, 1) for _ in range(5000)]
        env = fill(Envelope(max_buckets=16), values)
        snap = env.snapshot()
        self.assertEqual(sum(b["n"] for b in snap), 5000)
        self.assertAlmostEqual(sum(b["mean"] * b["n"] for b in snap), sum(values), places=9)
        self.assertEqual((min(b["min"] for b in snap), max(b["max"] for b in snap)), (min(values), max(values)))

    def test_every_bucket_is_exactly_what_its_own_readings_say(self):
        """Not just the totals: each bucket's lowest, highest, count and mean equal those of the readings it covers."""
        rng = random.Random(11)
        values = [rng.uniform(-1, 1) for _ in range(3000)]
        snap = fill(Envelope(max_buckets=16), values).snapshot()
        at = 0
        for b in snap:
            mine = values[at:at + b["n"]]
            self.assertEqual((b["min"], b["max"]), (min(mine), max(mine)), b)
            self.assertAlmostEqual(b["mean"], sum(mine) / len(mine), places=12)
            self.assertEqual((b["t0"], b["t1"]), (at * 0.2, (at + b["n"] - 1) * 0.2))
            at += b["n"]
        self.assertEqual(at, 3000)

    def test_buckets_are_in_time_order_and_do_not_overlap(self):
        snap = fill(Envelope(max_buckets=16), [float(i) for i in range(3000)]).snapshot()
        for a, b in zip(snap, snap[1:]):
            self.assertLessEqual(a["t0"], a["t1"])
            self.assertLessEqual(a["t1"], b["t0"])

    def test_the_first_reading_and_the_last_are_the_ends(self):
        snap = fill(Envelope(max_buckets=16), [1.0] * 1234, step=0.5).snapshot()
        self.assertEqual((snap[0]["t0"], snap[-1]["t1"]), (0.0, 1233 * 0.5))

    def test_a_short_spike_survives_as_the_edge_of_its_band(self):
        values = [0.0001] * 20000
        values[12345] = 0.050
        snap = fill(Envelope(max_buckets=64), values).snapshot()
        self.assertEqual(max(b["max"] for b in snap), 0.050)
        self.assertEqual([b["max"] for b in snap].count(0.050), 1, "in exactly one bucket")
        self.assertEqual(min(b["min"] for b in snap), 0.0001)

    def test_before_the_first_merge_every_reading_is_its_own_bucket(self):
        snap = fill(Envelope(max_buckets=64), [1.0, 2.0, 3.0]).snapshot()
        self.assertEqual([(b["min"], b["max"], b["mean"], b["n"]) for b in snap], [(1.0, 1.0, 1.0, 1), (2.0, 2.0, 2.0, 1), (3.0, 3.0, 3.0, 1)])

    def test_the_width_doubles_at_each_merge(self):
        env = Envelope(max_buckets=4)
        fill(env, [1.0] * 4)                                      # 4 buckets reach the bound: merged to 2 of width 2
        self.assertEqual((len(env.snapshot()), env.width), (2, 2))
        fill(env, [1.0] * 8)                                      # 4 + 8 readings in all, none lost
        self.assertEqual(sum(b["n"] for b in env.snapshot()), 12)

    def test_an_odd_one_out_is_kept_not_dropped(self):
        env = Envelope(max_buckets=5)
        fill(env, [1.0, 2.0, 3.0, 4.0, 5.0])                      # 5 buckets reach the bound: pairs (1,2), (3,4) and 5 alone
        snap = env.snapshot()
        self.assertEqual([b["n"] for b in snap], [2, 2, 1])
        self.assertEqual(sum(b["mean"] * b["n"] for b in snap), 15.0)

    def test_a_snapshot_includes_the_bucket_still_filling(self):
        env = Envelope(max_buckets=4)
        fill(env, [1.0] * 4)                                      # width is now 2
        env.add(99.0, 7.0)                                        # one reading of a bucket that wants two
        last = env.snapshot()[-1]
        self.assertEqual((last["n"], last["max"], last["t0"]), (1, 7.0, 99.0))


class Statistics(unittest.TestCase):
    def test_min_max_mean_and_count(self):
        s = Stats()
        for t, v in enumerate([3.0, 1.0, 2.0]):
            s.add(t, v)
        d = s.to_dict()
        self.assertEqual((d["readings"], d["min"], d["max"], d["mean"]), (3, 1.0, 3.0, 2.0))

    def test_an_empty_session_has_no_numbers_and_does_not_divide_by_zero(self):
        d = Stats().to_dict(current=True)
        self.assertEqual((d["readings"], d["min"], d["max"], d["mean"]), (0, None, None, None))
        self.assertEqual((d["charge_c"], d["charge_mah"]), (0.0, 0.0))

    def test_charge_is_the_trapezoid_integral_in_coulombs_and_mah(self):
        s = Stats()
        for t in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0):
            s.add(t, 0.001)                                       # 1 mA for 10 s
        d = s.to_dict(current=True)
        self.assertAlmostEqual(d["charge_c"], 0.010)
        self.assertAlmostEqual(d["charge_mah"], 0.010 / 3.6)
        t = Stats()
        t.add(0.0, 0.0)
        t.add(2.0, 0.004)                                         # a ramp from 0 to 4 mA over 2 s: the area is half of 4 mA x 2 s
        self.assertAlmostEqual(t.to_dict(current=True)["charge_c"], 0.004)

    def test_uneven_spacing_is_weighted_by_time_not_by_count(self):
        s = Stats()
        s.add(0.0, 0.010)
        s.add(0.1, 0.010)
        s.add(10.0, 0.000)                                        # a long quiet stretch
        self.assertAlmostEqual(s.to_dict(current=True)["charge_c"], 0.010 * 0.1 + (0.010 + 0.0) / 2 * 9.9)

    def test_charge_is_only_reported_for_a_current(self):
        s = Stats()
        s.add(0.0, 3.3)
        s.add(1.0, 3.3)
        self.assertNotIn("charge_c", s.to_dict())
        self.assertIn("charge_c", s.to_dict(current=True))

    def test_an_overload_is_counted_and_is_not_a_reading(self):
        s = Stats()
        s.add(0.0, 1.0)
        s.overload()
        s.add(2.0, 3.0)
        d = s.to_dict()
        self.assertEqual((d["readings"], d["overloads"], d["mean"]), (2, 1, 2.0))


if __name__ == "__main__":
    unittest.main()
