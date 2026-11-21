#!/usr/bin/env python3
"""The scope's axes and window maths (ui/src/meter/axes.js, docs/specs/meter-page.md "Live and replay") under Node. Run:  python3 tests/dashboard/test_meter_axes.py"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
AXES = os.path.join(ROOT, "ui", "src", "meter", "axes.js")
NODE = shutil.which("node")


def run(script, data=None):
    code = 'import * as a from %s;\nconst data = %s;\nconst out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + AXES), json.dumps(data), script)
    p = subprocess.run([NODE, "--input-type=module", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class TheTicks(unittest.TestCase):
    def test_a_step_is_one_two_or_five_times_a_power_of_ten(self):
        got = run("return data.map(([s, c]) => a.niceStep(s, c));", [[10, 5], [7, 5], [0.9, 4], [33, 4], [1e-3, 5], [100, 4], [0, 5], [5, 0]])
        self.assertEqual(got, [2, 2, 0.5, 10, 0.0002, 50, 0, 0])

    def test_ticks_are_round_values_inside_the_range_and_none_for_an_empty_one(self):
        t = run("return a.niceTicks(data[0], data[1], 5);", [0, 10])
        self.assertEqual(t["values"], [0, 2, 4, 6, 8, 10])
        t = run("return a.niceTicks(data[0], data[1], 6);", [0.0003, 0.0121])
        self.assertEqual(t["values"], [0.002, 0.004, 0.006, 0.008, 0.01, 0.012])
        t = run("return a.niceTicks(data[0], data[1], 4);", [0.0003, 0.0121])
        self.assertEqual(t["values"], [0.005, 0.01], "four intervals over 0.0118 want 0.003 or so: the next round step is 0.005")
        self.assertEqual(run("return a.niceTicks(1, 1);")["values"], [])
        t = run("return a.niceTicks(-5, 5, 5);")
        self.assertEqual(t["values"], [-4, -2, 0, 2, 4], "a range across zero has a tick at zero")

    def test_a_linear_axis_labels_its_ticks_in_the_unit_with_a_prefix(self):
        got = run("return a.yTicks(0, 0.12, (v) => 100 - v * 100, 'A');")
        self.assertEqual([t["label"] for t in got], ["0 A", "50 mA", "100 mA"])
        self.assertAlmostEqual(got[1]["y"], 100 - 0.05 * 100, places=9)
        got = run("return a.yTicks(-0.001, 0.003, (v) => v, 'A');")
        self.assertEqual([t["label"] for t in got], ["-1 mA", "0 A", "1 mA", "2 mA", "3 mA"])

    def test_a_log_axis_has_a_tick_at_each_power_of_ten_in_range(self):
        got = run("return a.yTicks(1e-5, 0.1, (v) => Math.log10(v), 'A', { log: true });")
        self.assertEqual([t["label"] for t in got], ["10 µA", "100 µA", "1 mA", "10 mA", "100 mA"])

    def test_a_log_axis_over_one_decade_adds_two_and_five_times(self):
        got = run("return a.yTicks(3e-5, 4e-4, (v) => v, 'A', { log: true });")
        self.assertEqual([t["label"] for t in got], ["50 µA", "100 µA", "200 µA"], "2e-5 and 5e-5 and 1e-4... only those inside the range")

    def test_a_log_axis_with_no_floor_above_zero_falls_back_to_linear_and_an_empty_one_has_none(self):
        self.assertTrue(run("return a.yTicks(0, 10, (v) => v, 'A', { log: true });"))
        self.assertEqual(run("return a.yTicks(5, 5, (v) => v, 'A');"), [])


@unittest.skipUnless(NODE, "node is not installed")
class TheLabels(unittest.TestCase):
    def test_time_reads_in_the_unit_the_span_needs(self):
        got = run("return data.map(([t, s]) => a.clockLabel(t, s));", [[0.0123, 0.05], [-0.2, 0.6], [1.5, 5], [12.34, 30], [75, 300], [3725, 7200], [0, 10], [-3, 9]])
        self.assertEqual(got, ["12.3 ms", "-200 ms", "1.5 s", "12.3 s", "1:15", "1:02:05", "0 s", "-3 s"])

    def test_a_duration_is_in_words(self):
        got = run("return data.map((s) => a.durationText(s));", [0.0015, 0.32, 4.2, 59.9, 100, 3600, 7500, 21.4])
        self.assertEqual(got, ["1.5 ms", "320 ms", "4.2 s", "59.9 s", "1 min 40 s", "1 h 00 min", "2 h 05 min", "21.4 s"])
        self.assertEqual(run("return a.durationText(NaN);"), "")


@unittest.skipUnless(NODE, "node is not installed")
class TheWindow(unittest.TestCase):
    B = {"t0": 0, "t1": 3600}

    def win(self, expr, **extra):
        return run("const b = data.b; const w = data.w; return %s;" % expr, dict({"b": self.B, "w": {"t0": 1000, "t1": 1100}}, **extra))

    def test_zooming_in_keeps_the_point_under_the_pointer_where_it_was(self):
        for anchor in (0.0, 0.25, 0.5, 1.0):
            w = self.win("a.zoomWindow(w, b, 4, %s)" % anchor)
            before = 1000 + 100 * anchor
            after = w["t0"] + (w["t1"] - w["t0"]) * anchor
            self.assertAlmostEqual(before, after, places=9)
            self.assertAlmostEqual(w["t1"] - w["t0"], 25, places=9)

    def test_zooming_out_is_held_inside_the_recording(self):
        w = self.win("a.zoomWindow(w, b, 0.001, 0.5)")
        self.assertEqual(w, self.B)
        w = self.win("a.zoomWindow({t0: 10, t1: 110}, b, 0.5, 0.0)")
        self.assertEqual((w["t0"], w["t1"]), (10, 210))
        w = self.win("a.zoomWindow({t0: 3500, t1: 3600}, b, 0.5, 1.0)")
        self.assertEqual((w["t0"], w["t1"]), (3400, 3600))

    def test_zooming_in_stops_at_one_sample(self):
        w = self.win("a.zoomWindow({t0: 5, t1: 5.00002}, b, 100, 0.5)")
        self.assertAlmostEqual(w["t1"] - w["t0"], 1e-5, places=12)

    def test_a_given_floor_is_respected(self):
        w = self.win("a.zoomWindow(w, b, 1000, 0.5, 0.2)")
        self.assertAlmostEqual(w["t1"] - w["t0"], 0.2, places=12)

    def test_panning_moves_by_a_fraction_of_the_window_and_stops_at_the_ends(self):
        self.assertEqual(self.win("a.panWindow(w, b, 0.5)"), {"t0": 1050, "t1": 1150})
        self.assertEqual(self.win("a.panWindow(w, b, -1)"), {"t0": 900, "t1": 1000})
        self.assertEqual(self.win("a.panWindow(w, b, -100)"), {"t0": 0, "t1": 100})
        self.assertEqual(self.win("a.panWindow(w, b, 100)"), {"t0": 3500, "t1": 3600})

    def test_the_whole_recording_cannot_be_panned_or_zoomed_out(self):
        self.assertEqual(self.win("a.panWindow(b, b, 1)"), self.B)
        self.assertEqual(self.win("a.zoomWindow(b, b, 0.5)"), self.B)
        self.assertTrue(self.win("a.isWhole(b, b)"))
        self.assertFalse(self.win("a.isWhole(w, b)"))

    def test_a_window_longer_than_the_recording_is_the_recording(self):
        self.assertEqual(self.win("a.clampWindow({t0: -5, t1: 9000}, b)"), self.B)

    def test_a_window_keeps_its_length_when_it_is_pushed_past_an_end(self):
        w = self.win("a.clampWindow({t0: 3550, t1: 3650}, b)")
        self.assertEqual((w["t0"], w["t1"]), (3500, 3600))
        w = self.win("a.clampWindow({t0: -50, t1: 50}, b)")
        self.assertEqual((w["t0"], w["t1"]), (0, 100))


@unittest.skipUnless(NODE, "node is not installed")
class TheFiguresOfAStretch(unittest.TestCase):
    B = [{"t0": 0, "t1": 1, "min": 1, "max": 2, "mean": 1.5, "n": 10}, {"t0": 1, "t1": 2, "min": 3, "max": 9, "mean": 4, "n": 10}, {"t0": 2, "t1": 3, "min": 0.5, "max": 1, "mean": 0.7, "n": 10}]

    def test_each_bucket_counts_for_the_part_of_it_inside_the_stretch(self):
        s = run("return a.windowStats(data, {t0: 0.5, t1: 2.5});", self.B)
        self.assertAlmostEqual(s["seconds"], 2.0, places=12)
        self.assertAlmostEqual(s["charge"], 1.5 * 0.5 + 4 * 1 + 0.7 * 0.5, places=12)
        self.assertAlmostEqual(s["mean"], s["charge"] / 2.0, places=12)
        self.assertEqual((s["lowest"], s["highest"]), (0.5, 9), "a bucket part-way inside still says what its extremes were")

    def test_the_whole_is_the_sum_and_a_stretch_past_the_data_is_only_what_is_there(self):
        s = run("return a.windowStats(data, {t0: -5, t1: 50});", self.B)
        self.assertAlmostEqual(s["seconds"], 3.0, places=12)
        self.assertAlmostEqual(s["charge"], 1.5 + 4 + 0.7, places=12)

    def test_no_bucket_in_the_stretch_is_none(self):
        self.assertIsNone(run("return a.windowStats(data, {t0: 10, t1: 11});", self.B))
        self.assertIsNone(run("return a.windowStats([], {t0: 0, t1: 1});"))
        self.assertIsNone(run("return a.windowStats(data, {t0: 1, t1: 1});", self.B))


@unittest.skipUnless(NODE, "node is not installed")
class UnderThePointer(unittest.TestCase):
    BUCKETS = [{"t0": i * 0.2, "t1": (i + 1) * 0.2, "min": 1e-5 * (i + 1), "max": 1e-3 * (i + 1), "mean": 1e-4 * (i + 1), "n": 20000} for i in range(10)]

    def test_the_bucket_under_a_time_is_the_one_that_contains_it(self):
        got = run("return [0, 0.0999, 0.2, 0.39, 1.99, 2.0].map((t) => { const b = a.bucketAt(data, t); return b && Math.round(b.t0 * 10); });", self.BUCKETS)
        self.assertEqual(got, [0, 0, 2, 2, 18, 18], "bucket starts in tenths of a second: a time on a boundary belongs to the later bucket, and the very end to the last")

    def test_outside_the_buckets_there_is_none(self):
        got = run("return [-0.001, 2.001].map((t) => a.bucketAt(data, t));", self.BUCKETS)
        self.assertEqual(got, [None, None])
        self.assertIsNone(run("return a.bucketAt([], 1);"))

    def test_the_readout_gives_the_time_and_the_three_values_in_words(self):
        r = run("return a.readout(data, 0.45, 'A', 2);", self.BUCKETS)
        self.assertEqual(r, {"t": "0.45 s", "lowest": "30 µA", "mean": "300 µA", "highest": "3 mA", "width": "200 ms"})

    def test_no_readout_outside_the_trace(self):
        self.assertIsNone(run("return a.readout(data, 9, 'A', 2);", self.BUCKETS))


if __name__ == "__main__":
    unittest.main()
