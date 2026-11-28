#!/usr/bin/env python3
"""`meter.life`: a recording's wake/sleep cycle and its battery life, found by the same wake rule as `meter.wake_table` (docs/specs/power-workbench.md, "Commands").

The recordings are made through the whole path (the fake unit, the profiler's source, the sampler, the CSV), the unit sleeping (ADC 1000) except in the blocks a test names, so the figures are
worked out here from the two currents the unit's calibration gives and not read back from the command. A device that sleeps, wakes and sleeps again is rated by the mean current over WHOLE
cycles, from the start of its first wake to the start of its last, not over a window that may hold half of one.
Run:  python3 tests/instruments/test_life.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.core.registry import UsageError  # noqa: E402
from test_capture import WAKE_ADC  # noqa: E402,F401
from test_ppk2 import reference_amps  # noqa: E402
from test_wake_table import BLOCK, Recorded  # noqa: E402

CYCLE_BLOCKS, AWAKE_BLOCKS = 10, 5                         # a wake of 1.0 s in every 2.0 s


class Cycled(Recorded):
    """Five wakes of 1.0 s, starting at 1.0 s, 3.0 s, 5.0 s, 7.0 s and 9.0 s: four whole cycles of 2.0 s between the first start and the last."""

    def make_cycle(self, name="cycle"):
        self.make(name, 60, self.runs(*[(5 + k * CYCLE_BLOCKS, AWAKE_BLOCKS) for k in range(5)]))

    def life(self, name="cycle", capacity=None):
        res = self.call("meter.life", dict({"recording": name}, **({} if capacity is None else {"capacity_mah": capacity})))
        self.assertTrue(res.ok, getattr(res, "message", res))
        return res.data


class TheCycleOfARecording(Cycled):
    def test_the_period_the_cycles_and_the_mean_over_whole_cycles(self):
        self.make_cycle()
        d = self.life()
        self.assertTrue(d["found"])
        self.assertEqual((d["wakes"], d["cycles"]), (5, 4))
        self.assertAlmostEqual(d["period_s"], CYCLE_BLOCKS * BLOCK, places=6)
        self.assertAlmostEqual(d["duration_s"], 4 * CYCLE_BLOCKS * BLOCK, places=6, msg="from the start of the first wake to the start of the last")
        want = (AWAKE_BLOCKS * self.wake_a + (CYCLE_BLOCKS - AWAKE_BLOCKS) * self.sleep_a) / CYCLE_BLOCKS
        self.assertAlmostEqual(d["mean_a"], want, delta=want * 0.005)
        self.assertEqual([round(s / BLOCK) for s in d["starts"]], [5, 15, 25, 35, 45], msg="the wakes' starts, in blocks of the recording")

    def test_the_wake_and_sleep_phases_add_up_to_the_cycle(self):
        self.make_cycle()
        p = self.life()["phases"]
        self.assertAlmostEqual(p["wake"]["time_s"], AWAKE_BLOCKS * BLOCK, places=6, msg="the awake period, as the wake table gives it")
        self.assertAlmostEqual(p["sleep"]["time_s"], (CYCLE_BLOCKS - AWAKE_BLOCKS) * BLOCK, places=6)
        self.assertAlmostEqual(p["wake"]["mean_a"], self.wake_a, delta=self.wake_a * 0.005)
        self.assertAlmostEqual(p["sleep"]["mean_a"], self.sleep_a, delta=self.sleep_a * 0.005)
        self.assertGreaterEqual(p["wake"]["peak_a"], p["wake"]["mean_a"])
        cycle = self.life()
        per_cycle = cycle["mean_a"] * cycle["period_s"] / 3.6                          # amps x seconds is coulombs, and 1 mAh is 3.6 coulombs
        self.assertAlmostEqual((p["wake"]["charge_mah"] + p["sleep"]["charge_mah"]) * 1.0, per_cycle, delta=per_cycle * 0.005, msg="the two phases are the whole cycle")

    def test_it_finds_the_same_wakes_as_the_wake_table(self):
        self.make_cycle()
        d = self.life()
        rows = self.call("meter.wake_table", {"recording": "cycle"}).data["wakes"]
        self.assertEqual(len(rows), d["wakes"])
        for r, start in zip(rows, d["starts"]):
            self.assertAlmostEqual(r["start_s"], start, places=6, msg="one rule decides when a wake starts")
        self.assertAlmostEqual(d["phases"]["wake"]["time_s"], sum(r["awake_s"] for r in rows[:-1]) / (len(rows) - 1), places=6, msg="and how long it is awake")
        self.assertAlmostEqual(d["phases"]["wake"]["time_s"], rows[0]["awake_s"], places=6)

    def test_the_wakes_are_as_regular_as_they_are(self):
        self.make_cycle()
        d = self.life()
        self.assertTrue(d["regular"])
        self.assertAlmostEqual(d["shortest_s"], d["longest_s"], places=6)


class ThePeak(Cycled):
    def test_the_peak_is_the_highest_of_the_wakes_not_the_first(self):
        tall = 14000                                                                                  # a 14-bit ADC: a taller wake in the third place
        plan = self.runs((5, 5), (15, 5), (35, 5), (45, 5))
        plan.update(self.run_of(25, 5, adc=tall))
        self.make("peaks", 60, plan)
        d = self.life("peaks")
        want = reference_amps(self.cal, 0, tall)
        self.assertGreater(want, self.wake_a)
        self.assertAlmostEqual(d["peak_a"], want, delta=want * 0.005)
        self.assertAlmostEqual(d["phases"]["wake"]["peak_a"], want, delta=want * 0.005, msg="the third wake is inside the span, so the cycle's wake phase has it")
        self.assertAlmostEqual(d["phases"]["wake"]["mean_a"], (3 * self.wake_a + want) / 4.0, delta=want * 0.005, msg="and its mean is the charge over the time of the four wakes inside the span (the fifth starts where it ends)")


class HowRegularTheWakesAre(Cycled):
    def test_a_drift_of_one_row_is_still_regular(self):
        self.make("one-row", 60, self.runs((5, 5), (15, 5), (26, 5), (36, 5), (46, 5)))              # gaps of 10, 11, 10 and 10 rows
        d = self.life("one-row")
        self.assertEqual(d["cycles"], 4)
        self.assertTrue(d["regular"])
        self.assertAlmostEqual(d["longest_s"] - d["shortest_s"], BLOCK, places=6)

    def test_wakes_that_drift_by_more_than_two_rows_are_not(self):
        self.make("drift", 60, self.runs((5, 5), (15, 5), (27, 5), (37, 5), (50, 5)))                # gaps of 10, 12, 10 and 13 rows
        d = self.life("drift")
        self.assertFalse(d["regular"])
        self.assertAlmostEqual(d["shortest_s"], 10 * BLOCK, places=6)
        self.assertAlmostEqual(d["longest_s"], 13 * BLOCK, places=6)
        self.assertAlmostEqual(d["period_s"], (45 * BLOCK) / 4, places=6, msg="the period is the mean gap: the span over the cycles")


class TheRuleOnItsOwn(unittest.TestCase):
    """life.life needs the trace only to add the charge between two starts: with no sleep level or fewer than two whole wakes it never looks at it."""

    def test_no_sleep_level_is_its_own_reason(self):
        from nrf_workbench.instruments import life
        d = life.life(None, {"wakes": [{"partial": False, "truncated": False, "start_s": 1.0}] * 3, "sleep_a": None, "awake_a": None}, 1000)
        self.assertEqual((d["found"], d["reason"], d["wakes"], d["life_h"]), (False, "no_sleep", 3, None))

    def test_a_partial_or_truncated_wake_is_not_a_wake_for_a_cycle(self):
        from nrf_workbench.instruments import life
        rows = [{"partial": True, "truncated": False, "start_s": 0.0}, {"partial": False, "truncated": False, "start_s": 3.0}, {"partial": False, "truncated": True, "start_s": 6.0}]
        d = life.life(None, {"wakes": rows, "sleep_a": 1e-5, "awake_a": 2e-5})
        self.assertEqual((d["found"], d["reason"], d["wakes"]), (False, "wakes", 1))


class TheLifeAtACapacity(Cycled):
    def test_a_capacity_gives_hours_and_days_at_the_mean_over_whole_cycles(self):
        self.make_cycle()
        d = self.life(capacity=1000)
        hours = 1000.0 / (d["mean_a"] * 1000.0)                                         # mAh over mA
        self.assertAlmostEqual(d["life_h"], hours, delta=hours * 1e-9)
        self.assertAlmostEqual(d["life_days"], hours / 24.0, delta=hours / 24.0 * 1e-9)
        self.assertEqual(d["capacity_mah"], 1000)

    def test_twice_the_capacity_lasts_twice_as_long(self):
        self.make_cycle()
        a, b = self.life(capacity=500), self.life(capacity=1000)
        self.assertAlmostEqual(b["life_h"] / a["life_h"], 2.0, places=9)

    def test_without_a_capacity_there_is_no_life_and_the_cycle_is_still_given(self):
        self.make_cycle()
        d = self.life()
        self.assertTrue(d["found"] and d["life_h"] is None and d["life_days"] is None and d["capacity_mah"] is None)

    def test_a_capacity_that_is_not_above_zero_is_refused(self):
        self.make_cycle()
        for bad in (0, -5):
            with self.assertRaises(UsageError, msg=str(bad)):
                self.call("meter.life", {"recording": "cycle", "capacity_mah": bad})


class WhenThereIsNoCycle(Cycled):
    def test_a_steady_load_has_no_wake_and_says_so(self):
        self.make("steady", 40, {})
        d = self.life("steady", capacity=1000)
        self.assertFalse(d["found"])
        self.assertEqual((d["reason"], d["wakes"]), ("wakes", 0))
        self.assertIsNone(d["life_h"], msg="no cycle, no battery life: the whole recording's mean is the page's Average, not this")

    def test_one_wake_is_not_a_cycle(self):
        self.make("single", 40, self.run_of(15, AWAKE_BLOCKS))
        d = self.life("single")
        self.assertFalse(d["found"])
        self.assertEqual((d["reason"], d["wakes"]), ("wakes", 1))

    def test_a_wake_cut_off_by_the_end_of_the_recording_is_not_counted(self):
        self.make("cut", 50, self.runs((5, 5), (15, 5), (25, 5), (45, 5)))                  # the last wake runs to the recording's last block
        rows = self.call("meter.wake_table", {"recording": "cut"}).data["wakes"]
        d = self.life("cut")
        whole = [r for r in rows if not r["partial"] and not r["truncated"]]
        self.assertEqual(d["wakes"], len(whole), msg="only whole wakes make a cycle")


class Refusals(Cycled):
    def test_a_recording_that_is_not_there_is_refused_by_name(self):
        self.assertEqual(self.call("meter.life", {"recording": "nothing"}).code, "recording_unknown")


if __name__ == "__main__":
    unittest.main()
