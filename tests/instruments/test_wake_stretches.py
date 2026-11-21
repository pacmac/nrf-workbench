#!/usr/bin/env python3
"""`meter.wake_table level_ma`: the stretches of each wake over a current level (docs/specs/power-workbench.md, "Stretches above a level").

Through the whole path (the fake unit, the profiler's source, the sampler, the recording): a wake with an opening burst, a quiet receive plateau and a closing burst has two stretches, and
a burst that runs on from another is one longer stretch, which is what shows a wake with an extra closing burst. A stretch is a run of 0.2 s blocks whose HIGHEST sample is over the level.
Run:  python3 tests/instruments/test_wake_stretches.py
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
from test_capture import PathBase  # noqa: E402
from test_ppk2 import reference_amps  # noqa: E402

BLOCK = 0.2
HIGH, LOW = 12000, 4000                 # ADC values: a transmit burst and the receive plateau between bursts; both are awake (over twice the sleep level, ADC 1000)
SPIKE = 16300                           # one sample of this in an otherwise low block


class Stretches(PathBase):
    def setUp(self):
        PathBase.setUp(self)
        high, low = reference_amps(self.cal, 0, HIGH), reference_amps(self.cal, 0, LOW)
        self.level_ma = (high + low) / 2.0 * 1000.0         # halfway between the plateau and the burst

    def make(self, name, plan, blocks=None, spikes=()):
        """plan: block number -> ADC. `spikes`: sample numbers that read SPIKE."""
        blocks = blocks or (max(plan) + 6)
        self.BLOCKS = blocks
        spikes = set(spikes)
        self.unit.profile = lambda i: (SPIKE if i in spikes else plan.get(i // 20000, 1000) if i < blocks * 20000 else 1000, 0)
        self.record_session(name)

    def table(self, name, level_ma="default"):
        args = {"recording": name}
        if level_ma == "default":
            level_ma = self.level_ma
        if level_ma is not None:
            args["level_ma"] = level_ma
        res = self.call("meter.wake_table", args)
        self.assertTrue(res.ok, getattr(res, "message", res))
        return res.data["wakes"]

    @staticmethod
    def span(*groups):
        """A plan from (first block, count, adc) groups."""
        plan = {}
        for first, count, adc in groups:
            for b in range(first, first + count):
                plan[b] = adc
        return plan

    def test_an_opening_burst_a_receive_plateau_and_a_closing_burst_are_two_stretches(self):
        self.make("two", self.span((10, 3, HIGH), (13, 5, LOW), (18, 2, HIGH)))
        (row,) = self.table("two")
        self.assertAlmostEqual(row["awake_s"], 10 * BLOCK, places=6)
        got = [(round(s["start_s"], 6), round(s["end_s"], 6), round(s["length_s"], 6)) for s in row["stretches"]]
        self.assertEqual(got, [(0.0, 0.6, 0.6), (1.6, 2.0, 0.4)], "from the wake's first awake block, to the end of the last block over the level")
        self.assertAlmostEqual(row["above_s"], 1.0, places=6)

    def test_a_burst_that_runs_on_from_another_is_one_longer_stretch_not_a_third(self):
        normal = self.span((10, 3, HIGH), (13, 5, LOW), (18, 2, HIGH))
        extra = self.span((30, 3, HIGH), (33, 5, LOW), (38, 5, HIGH))        # the closing transmit is three blocks longer
        self.make("run", {**normal, **extra}, blocks=50)
        first, second = self.table("run")
        self.assertEqual((len(first["stretches"]), len(second["stretches"])), (2, 2), "the count is the same: two in each wake")
        self.assertAlmostEqual(first["stretches"][1]["length_s"], 2 * BLOCK, places=6)
        self.assertAlmostEqual(second["stretches"][1]["length_s"], 5 * BLOCK, places=6, msg="but its closing stretch is longer")

    def test_a_quiet_block_between_two_bursts_makes_them_two_stretches(self):
        self.make("gap", self.span((10, 2, HIGH), (12, 1, LOW), (13, 2, HIGH)))
        (row,) = self.table("gap")
        self.assertEqual([round(s["length_s"], 6) for s in row["stretches"]], [0.4, 0.4])

    def test_a_wake_that_ends_in_a_burst_has_it_as_its_last_stretch(self):
        self.make("end", self.span((10, 4, LOW), (14, 2, HIGH)))
        (row,) = self.table("end")
        self.assertEqual(len(row["stretches"]), 1)
        self.assertAlmostEqual(row["stretches"][0]["end_s"], row["awake_s"], places=6, msg="it runs to the end of the awake period")

    def test_a_burst_shorter_than_a_block_counts_by_its_highest_sample(self):
        self.make("spike", self.span((10, 6, LOW)), spikes=[12 * 20000 + 700])      # one sample in block 12: its mean is plateau, its highest is not
        (row,) = self.table("spike")
        self.assertEqual([round(s["length_s"], 6) for s in row["stretches"]], [BLOCK], "the block holding the spike is a stretch of one block")
        self.assertAlmostEqual(row["stretches"][0]["start_s"], 2 * BLOCK, places=6)

    def test_a_wake_wholly_under_the_level_has_no_stretches(self):
        self.make("quiet", self.span((10, 5, LOW)))
        (row,) = self.table("quiet")
        self.assertEqual((row["stretches"], row["above_s"]), ([], 0.0))

    def test_without_a_level_there_are_no_stretches_to_report(self):
        self.make("none", self.span((10, 3, HIGH), (13, 5, LOW)))
        (row,) = self.table("none", level_ma=None)
        self.assertEqual((row["stretches"], row["above_s"]), (None, None))

    def test_each_wake_of_a_recording_has_its_own_stretches_and_charge_is_still_the_awake_blocks(self):
        self.make("twowakes", {**self.span((10, 2, HIGH), (12, 2, LOW)), **self.span((30, 6, HIGH))}, blocks=45)
        a, b = self.table("twowakes")
        self.assertEqual([len(a["stretches"]), len(b["stretches"])], [1, 1])
        self.assertAlmostEqual(a["stretches"][0]["length_s"], 2 * BLOCK, places=6)
        self.assertAlmostEqual(b["stretches"][0]["length_s"], 6 * BLOCK, places=6)
        self.assertGreater(b["charge_mah"], a["charge_mah"])

    def test_a_level_that_is_not_above_zero_is_a_usage_error_naming_it(self):
        self.make("bad", self.span((10, 3, HIGH)))
        for bad in (0, -5):
            with self.assertRaises(UsageError) as cm:
                self.call("meter.wake_table", {"recording": "bad", "level_ma": bad})
            self.assertEqual(cm.exception.arg, "level_ma")


if __name__ == "__main__":
    unittest.main()
