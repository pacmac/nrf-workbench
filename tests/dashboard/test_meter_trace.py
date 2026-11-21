#!/usr/bin/env python3
"""The scope trace's geometry (ui/src/meter/trace.js, docs/specs/meter-page.md "Live and replay") under Node. Run:  python3 tests/dashboard/test_meter_trace.py"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TRACE = os.path.join(ROOT, "ui", "src", "meter", "trace.js")
NODE = shutil.which("node")


def run(script, data=None):
    code = 'import * as t from %s;\nconst data = %s;\nconst out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + TRACE), json.dumps(data), script)
    p = subprocess.run([NODE, "--input-type=module", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def buckets(values, t0=0.0, step=1.0):
    return [{"t0": t0 + i * step, "t1": t0 + (i + 1) * step, "min": v, "max": v, "mean": v, "n": 10} for i, v in enumerate(values)]


def points(text):
    return [tuple(float(v) for v in p.split(",")) for p in text.split(" ")]


@unittest.skipUnless(NODE, "node is not installed")
class ThePlot(unittest.TestCase):
    def plot(self, values, view=None, **opts):
        b = buckets(values)
        v = view or {"t0": 0, "t1": len(values)}
        return run("const p = t.plot(data.b, data.v, data.o); return {...p, at: [p.at(0), p.at(100)], timeAt: [p.timeAt(6), p.timeAt(954), p.timeAt(480)]};", {"b": b, "v": v, "o": opts})

    def test_linear_puts_zero_at_the_bottom_and_the_highest_at_the_top(self):
        p = self.plot([1e-5] * 5 + [0.02] * 5)
        ys = [pt[1] for pt in points(p["mean"])]
        self.assertAlmostEqual(min(ys), 6.0, places=1, msg="the peak, one pad from the top")
        self.assertGreater(max(ys), 250)
        self.assertEqual((p["bottomText"], p["topText"]), ("0 A", "20 mA"))
        self.assertFalse(p["log"])

    def test_the_y_axis_has_labelled_ticks_at_their_own_heights(self):
        p = self.plot([0.0] * 5 + [0.1] * 5)
        labels = {tk["label"]: tk["y"] for tk in p["yTicks"]}
        self.assertEqual(sorted(labels), ["0 A", "100 mA", "50 mA"])
        self.assertAlmostEqual(labels["0 A"], 254.0, places=1)
        self.assertAlmostEqual(labels["100 mA"], 6.0, places=1)
        self.assertAlmostEqual(labels["50 mA"], 130.0, places=1, msg="halfway up is halfway between")

    def test_the_x_axis_has_round_times_that_fall_where_the_time_does(self):
        p = self.plot([1.0] * 100, {"t0": 0, "t1": 100})
        by = {tk["label"]: tk["x"] for tk in p["xTicks"]}
        self.assertEqual(sorted(by, key=lambda s: by[s]), ["0:00", "0:20", "0:40", "1:00", "1:20", "1:40"], "a span of a minute or more is read as minutes and seconds")
        self.assertAlmostEqual(by["0:00"], 6.0, places=1)
        self.assertAlmostEqual(by["1:40"], 954.0, places=1)
        self.assertAlmostEqual(by["1:00"], 6 + 0.6 * 948, places=1)

    def test_a_pixel_maps_back_to_a_time_and_a_time_to_a_pixel(self):
        p = self.plot([1.0] * 10, {"t0": 20, "t1": 30})
        self.assertAlmostEqual(p["timeAt"][0], 20.0, places=9)
        self.assertAlmostEqual(p["timeAt"][1], 30.0, places=9)
        self.assertAlmostEqual(p["timeAt"][2], 25.0, places=9)
        self.assertEqual(p["at"], [6.0, 954.0], "a time before the window is held at its left edge, one after it at its right")

    def test_only_the_buckets_in_view_are_drawn_and_the_ones_at_the_edges_are_clipped_to_it(self):
        b = buckets([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        p = run("const p = t.plot(data.b, {t0: 2.5, t1: 5.5}, {}); return p.mean;", {"b": b})
        pts = points(p)
        self.assertEqual(len(pts), 4, "buckets 2 to 5 overlap [2.5, 5.5)")
        self.assertAlmostEqual(pts[0][0], 6 + ((2.5 + 3.0) / 2 - 2.5) / 3.0 * 948, places=1, msg="the first is drawn at the middle of what is in view")

    def test_log_shows_the_sleep_level_well_above_the_floor_with_decades_labelled(self):
        values = [10e-6] * 5 + [3e-3] * 5 + [20e-3] * 5
        p = self.plot(values, log=True)
        self.assertTrue(p["log"])
        self.assertEqual([tk["label"] for tk in p["yTicks"]], ["10 µA", "100 µA", "1 mA", "10 mA"])
        ys = [pt[1] for pt in points(p["mean"])]
        self.assertAlmostEqual(min(ys), 6.0, places=1)
        self.assertAlmostEqual(max(ys), 254.0, places=1)

    def test_log_with_nothing_above_zero_is_drawn_linear(self):
        p = self.plot([0.0, -1e-6, 0.0], log=True)
        self.assertFalse(p["log"])

    def test_nothing_to_draw_is_none(self):
        self.assertIsNone(run("return t.plot([], {t0: 0, t1: 1});"))
        self.assertIsNone(run("return t.plot(data, {t0: 1, t1: 1});", buckets([1.0])))

    def test_a_flat_trace_is_drawn_at_the_middle_and_a_single_bucket_fills_the_plot(self):
        p = self.plot([0.0] * 4)
        self.assertTrue(all(abs(pt[1] - 130.0) < 1e-6 for pt in points(p["mean"])))
        p = self.plot([2.0], {"t0": 0, "t1": 1})
        self.assertEqual(len(points(p["mean"])), 1)


if __name__ == "__main__":
    unittest.main()
