#!/usr/bin/env python3
"""`meter.wake_table`: one row per wake of a recording, found by the same rule the live session used (docs/specs/power-workbench.md, "Commands").

The recordings here are made through the whole path (the fake unit, the profiler's source, the sampler, the CSV and the saved wakes), so a table that disagreed with `meter.captures`
about what a wake is would fail. `awake_s` (the first block over the awake level to the last) and `capture_s` (the lead-in and tail included) are kept apart.
Run:  python3 tests/instruments/test_wake_table.py
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.instruments import capture, waketable  # noqa: E402
from test_capture import PathBase, WAKE_ADC  # noqa: E402
from test_ppk2 import reference_amps  # noqa: E402

BLOCK = 0.2


class Recorded(PathBase):
    """A recording whose unit sleeps (ADC 1000) except in the blocks `awake` names (block number -> ADC)."""

    def make(self, name, blocks, awake):
        self.BLOCKS = blocks
        self.unit.profile = lambda i: (awake.get(i // 20000, 1000) if i < blocks * 20000 else 1000, 0)
        self.record_session(name)
        res = self.call("meter.wake_table", {"recording": name})
        self.assertTrue(res.ok, getattr(res, "message", res))
        return res.data

    @staticmethod
    def run_of(first, count, adc=WAKE_ADC):
        return dict((b, adc) for b in range(first, first + count))

    def runs(self, *runs):
        """Several (first, count) runs of awake blocks as one plan."""
        out = {}
        for first, count in runs:
            out.update(self.run_of(first, count))
        return out


class AWakeOfKnownLength(Recorded):
    def test_the_awake_period_and_the_capture_length_are_kept_apart(self):
        data = self.make("known", 30, self.run_of(10, 7))                     # awake for blocks 10 to 16: 1.4 s
        (row,) = data["wakes"]
        self.assertAlmostEqual(row["awake_s"], 7 * BLOCK, places=6)
        self.assertAlmostEqual(row["capture_s"], 9 * BLOCK, places=6, msg="one block of sleep before it and one quiet block after it as its tail")
        self.assertAlmostEqual(row["start_s"], 10 * BLOCK, places=3, msg="the first awake block begins at 2.0 s on the recording's clock")
        self.assertFalse(row["partial"] or row["truncated"])

    def test_it_agrees_with_what_the_capture_itself_kept(self):
        self.make("known", 30, self.run_of(10, 7))
        (cap,) = self.call("meter.captures", {"recording": "known"}).data["captures"]
        (row,) = self.call("meter.wake_table", {"recording": "known"}).data["wakes"]
        self.assertAlmostEqual(cap["duration_s"], row["capture_s"], places=6, msg="the same wake, the same length")
        self.assertAlmostEqual(cap["awake_s"], row["awake_s"], places=6, msg="meter.captures now says the awake period too")
        self.assertEqual((row["resolution"], row["capture"]), ("full", cap["n"]))

    def test_the_charge_mean_and_peak_are_those_of_the_awake_blocks(self):
        data = self.make("known", 30, self.run_of(10, 7))
        row = data["wakes"][0]
        amps = reference_amps(self.cal, 0, WAKE_ADC)
        self.assertAlmostEqual(row["mean_a"], amps, delta=amps * 0.02)
        self.assertGreaterEqual(row["peak_a"], row["mean_a"])
        self.assertAlmostEqual(row["charge_mah"], amps * 7 * BLOCK / 3.6, delta=amps * 7 * BLOCK / 3.6 * 0.02, msg="1 mAh is 3.6 coulombs")

    def test_a_spike_in_the_tail_block_is_not_the_wakes_peak(self):
        amps = reference_amps(self.cal, 0, WAKE_ADC)
        awake = self.run_of(10, 7)
        self.BLOCKS = 30
        spike = 17 * 20000 + 500                                            # one sample in the tail block (block 17), far above the wake's own current
        self.unit.profile = lambda i: (16300 if i == spike else awake.get(i // 20000, 1000) if i < 30 * 20000 else 1000, 0)
        self.record_session("spike")
        row = self.call("meter.wake_table", {"recording": "spike"}).data["wakes"][0]
        self.assertAlmostEqual(row["awake_s"], 7 * BLOCK, places=6, msg="the tail block's mean is still asleep")
        self.assertAlmostEqual(row["peak_a"], amps, delta=amps * 0.02, msg="the peak is the awake blocks'")

    def test_a_wall_start_is_given_on_the_recordings_own_clock(self):
        self.make("known", 30, self.run_of(10, 7))
        row = self.call("meter.wake_table", {"recording": "known"}).data["wakes"][0]
        started = self.call("meter.replay", {"recording": "known"}).data["started"]
        self.assertAlmostEqual(row["wall"], started + row["start_s"], places=6)

    def test_a_wake_the_longest_wake_cut_has_no_tail_and_says_it_was_cut(self):
        self.wake = lambda: capture.WakeProfile(sleep_a=self.sleep_a, max_s=1.0)
        data = self.make("cut", 40, self.run_of(10, 12))
        first = data["wakes"][0]
        self.assertTrue(first["truncated"])
        self.assertAlmostEqual(first["capture_s"], 1.0, places=6)
        self.assertAlmostEqual(first["awake_s"], 1.0 - BLOCK, places=6, msg="a lead block and no tail: the rest is awake")
        self.assertTrue(data["wakes"][1]["partial"], "the rest of the wake is a continuation, not a whole wake")


class EveryWake(Recorded):
    def test_every_wake_gets_a_row_though_only_the_newest_twenty_were_kept_at_full_rate(self):
        awake = dict((3 + 4 * k, WAKE_ADC) for k in range(25))               # 25 single-block wakes, four blocks apart
        data = self.make("many", 101, awake)
        rows = data["wakes"]
        self.assertEqual(len(rows), 25)
        self.assertEqual([r["n"] for r in rows], list(range(1, 26)))
        self.assertEqual([r["resolution"] for r in rows], ["0.2s"] * 5 + ["full"] * 20)
        self.assertEqual([r["capture"] for r in rows[:5]], [None] * 5)
        kept = [c["n"] for c in self.call("meter.captures", {"recording": "many"}).data["captures"]]
        self.assertEqual([r["capture"] for r in rows[5:]], kept)
        self.assertEqual(data["summary"]["wakes"], 25)

    def test_a_recording_with_no_wake_has_no_rows_and_says_so(self):
        data = self.make("steady", 30, {})
        self.assertEqual((data["wakes"], data["summary"]["wakes"]), ([], 0))

    def test_a_wake_with_an_unfound_sleep_level_is_found_as_the_live_session_found_it(self):
        self.wake = lambda: capture.WakeProfile()                           # nothing given: the level is found from the data, 20 blocks in
        data = self.make("auto", 50, self.runs((30, 3), (40, 3)))
        live = self.call("meter.captures", {"recording": "auto"}).data["captures"]
        self.assertEqual([r["capture"] for r in data["wakes"]], [c["n"] for c in live])
        self.assertEqual([round(r["awake_s"], 6) for r in data["wakes"]], [0.6, 0.6])

    def test_a_wake_that_opens_the_recording_is_partial(self):
        data = self.make("opens", 30, self.run_of(0, 4))
        self.assertTrue(data["wakes"][0]["partial"], "its start was not seen")

    def test_the_profile_the_table_used_is_reported(self):
        data = self.make("prof", 30, self.run_of(10, 3))
        self.assertAlmostEqual(data["profile"]["sleep_a"], self.sleep_a, delta=self.sleep_a * 0.001)
        self.assertEqual((data["profile"]["hold_s"], data["profile"]["lead_s"]), (0.2, 0.2))


class AnOddWakeIsFound(Recorded):
    def wakes(self, odd_at, odd, n=9, length=5, gap=6):
        awake = {}
        for k in range(n):
            first = 3 + k * (length + gap)
            if k == odd_at:
                awake.update(odd(first))
            else:
                awake.update(self.run_of(first, length))
        return awake

    def test_a_wake_with_a_burst_in_the_middle_is_flagged_by_its_charge(self):
        def burst(first):
            run = self.run_of(first, 5)
            run.update(self.run_of(first + 1, 3, 16000))                    # the same length, a third more current for three blocks
            return run
        data = self.make("burst", 3 + 9 * 11 + 3, self.wakes(4, burst))
        flags = [r["outlier"] for r in data["wakes"]]
        self.assertEqual(flags, [False] * 4 + [True] + [False] * 4)
        self.assertAlmostEqual(data["wakes"][4]["awake_s"], 1.0, places=6, msg="its length is the same as the rest: only its charge is odd")

    def test_a_wake_longer_than_the_rest_is_flagged_by_its_length(self):
        data = self.make("long", 3 + 9 * 11 + 8, self.wakes(8, lambda first: self.run_of(first, 12)))
        self.assertEqual([r["outlier"] for r in data["wakes"]], [False] * 8 + [True])

    def test_a_wake_twice_as_long_with_the_same_charge_is_flagged_by_its_length_alone(self):
        want = 5 * reference_amps(self.cal, 0, WAKE_ADC)                    # the charge of a normal wake, in amp-blocks
        adc = min(range(1100, WAKE_ADC), key=lambda a: abs(10 * reference_amps(self.cal, 0, a) - want))
        awake = {}
        for k in range(9):
            first = 3 + k * 11
            awake.update(self.run_of(first, 10, adc) if k == 8 else self.run_of(first, 5))
        data = self.make("slow", 3 + 9 * 11 + 8, awake)
        odd, rest = data["wakes"][8], data["wakes"][:8]
        self.assertTrue(odd["outlier"] and not any(r["outlier"] for r in rest))
        self.assertLess(abs(odd["z"]["charge_mah"]), 3.5, "its charge is the same as the others': only its length is odd")
        self.assertGreater(abs(odd["z"]["awake_s"]), 3.5)

    def test_a_wake_cut_at_the_start_is_not_judged_and_does_not_change_how_the_others_are(self):
        awake = self.runs((0, 12), *[(14 + k * 8, 5) for k in range(6)])
        data = self.make("cutfirst", 14 + 6 * 8 + 3, awake)
        first, rest = data["wakes"][0], data["wakes"][1:]
        self.assertTrue(first["partial"])
        self.assertIsNone(first["outlier"], "a wake whose start was not seen says nothing about how long a whole one is")
        self.assertEqual([r["outlier"] for r in rest], [False] * 6)

    def test_with_fewer_than_four_wakes_there_is_no_spread_to_judge(self):
        data = self.make("three", 40, self.runs((3, 5), (14, 5), (25, 9)))
        self.assertEqual(len(data["wakes"]), 3)
        self.assertEqual([r["outlier"] for r in data["wakes"]], [None, None, None])


class TheSpread(unittest.TestCase):
    def test_identical_values_have_a_spread_of_nothing_and_flag_nothing(self):
        self.assertEqual(waketable.robust_z([5.0] * 6), [0.0] * 6)

    def test_one_far_value_is_flagged_and_the_rest_are_not(self):
        z = waketable.robust_z([5.0] * 8 + [12.0])
        self.assertTrue(abs(z[-1]) > waketable.Z_FLAG and all(abs(v) <= waketable.Z_FLAG for v in z[:-1]))

    def test_noise_far_below_one_percent_of_the_median_is_not_an_outlier(self):
        z = waketable.robust_z([100.0] * 4 + [100.4])
        self.assertTrue(all(abs(v) <= waketable.Z_FLAG for v in z), z)

    def test_fewer_than_four_values_are_not_judged(self):
        self.assertIsNone(waketable.robust_z([1.0, 2.0, 30.0]))

    def test_the_threshold_is_the_published_one(self):
        self.assertEqual(waketable.Z_FLAG, 3.5)


class Refusals(Recorded):
    def test_a_recording_that_is_not_there_is_refused_by_name(self):
        self.assertEqual(self.call("meter.wake_table", {"recording": "nothing"}).code, "recording_unknown")

    def test_a_sidecar_whose_wake_profile_the_capture_refuses_fails_by_name(self):
        self.make("badprof", 30, self.run_of(10, 3))
        path = os.path.join(self.folder, "badprof.json")
        with open(path) as fh:
            meta = json.load(fh)
        meta["wake"]["max_s"] = 0.01                                         # under the 1 s the capture will keep
        with open(path, "w") as fh:
            json.dump(meta, fh)
        res = self.call("meter.wake_table", {"recording": "badprof"})
        self.assertEqual(res.code, "bad_wake_profile")
        self.assertIn("wake_max_s", res.message)

    def test_a_multimeter_recording_has_no_wakes_and_is_refused(self):
        with open(os.path.join(self.folder, "dmm.csv"), "w") as fh:
            fh.write("t_s,wall,value,unit,function,range,nplc,overload,min,max,n\n1.000,1795260000.0,0.001,A,CURR,,,0,0.001,0.001,1\n")
        with open(os.path.join(self.folder, "dmm.json"), "w") as fh:
            json.dump({"name": "dmm", "source": "meter"}, fh)
        res = self.call("meter.wake_table", {"recording": "dmm"})
        self.assertEqual(res.code, "not_a_profiler_recording")


if __name__ == "__main__":
    unittest.main()
