#!/usr/bin/env python3
"""The scope trace's chart maths (ui/src/meter/scope.js, docs/specs/ppk2-stages.md "The scope view") under Node. Run:  python3 tests/dashboard/test_meter_scope.py"""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCOPE = os.path.join(ROOT, "ui", "src", "meter", "scope.js")
NODE = shutil.which("node")


def run(script, data=None):
    code = 'import * as s from %s;\nconst data = %s;\nconst out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + SCOPE), json.dumps(data), script)
    p = subprocess.run([NODE, "--input-type=module", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def buckets(values, t0=-0.2, step=0.001):
    return [{"t0": t0 + i * step, "t1": t0 + (i + 1) * step, "min": v, "max": v, "mean": v, "n": 100} for i, v in enumerate(values)]


@unittest.skipUnless(NODE, "node is not installed")
class TheChart(unittest.TestCase):
    def chart(self, values, **opts):
        return run("const c = s.scopeChart(data.b, 480, 160, data.o); return {...c, timeAt: c.timeAt(240)};", {"b": buckets(values), "o": opts})

    def ys(self, c):
        pts = [tuple(float(v) for v in p.split(",")) for p in c["mean"].split(" ")]
        return [p[1] for p in pts]

    def test_linear_puts_zero_at_the_bottom_and_the_peak_at_the_top(self):
        c = self.chart([10e-6] * 5 + [20e-3] * 5 + [10e-6] * 5)
        ys = self.ys(c)
        self.assertAlmostEqual(min(ys), 4.0, places=1)                    # the peak, one pad from the top
        self.assertGreater(max(ys), 155)                                  # 10 uA against 20 mA is almost at the bottom
        self.assertEqual(c["bottom"], "0 A")
        self.assertFalse(c["log"])

    def test_log_shows_the_sleep_level_well_above_the_floor(self):
        values = [10e-6] * 5 + [3e-3] * 5 + [20e-3] * 5 + [10e-6] * 5
        lin, log = self.ys(self.chart(values)), self.ys(self.chart(values, log=True))
        self.assertTrue(self.chart(values, log=True)["log"])
        self.assertAlmostEqual(min(log), 4.0, places=1)
        # the 3 mA stage, 15 % of the peak: it is a sliver on the linear axis and a good part of the way up on the log one
        self.assertGreater(lin[7], 130)
        self.assertLess(log[7], 80)
        # the lowest sample sits on the floor, the highest on the top
        self.assertAlmostEqual(max(log), 156.0, places=1)

    def test_the_band_runs_from_the_highest_sample_along_the_top_to_the_lowest_along_the_bottom(self):
        b = [{"t0": i * 0.001, "t1": (i + 1) * 0.001, "min": 1e-6, "max": 20e-3, "mean": 5e-3, "n": 100} for i in range(6)]
        pts = [tuple(float(v) for v in p.split(",")) for p in run("return s.scopeChart(data, 480, 160, {}).band;", b).split(" ")]
        self.assertEqual(len(pts), 12)
        top, bottom = pts[:6], pts[6:]
        self.assertTrue(all(abs(y - 4.0) < 0.1 for _, y in top), "the highest sample is the top edge")
        self.assertTrue(all(y > 150 for _, y in bottom), "the lowest sample is the bottom edge")
        self.assertEqual([x for x, _ in bottom], sorted((x for x, _ in top), reverse=True), "and it comes back the other way, so the polygon closes")

    def test_a_log_axis_with_nothing_above_zero_falls_back_to_linear(self):
        self.assertFalse(self.chart([0.0, -1e-6, 0.0], log=True)["log"])

    def test_the_wake_start_is_marked_only_when_it_is_in_view_and_where_time_zero_is(self):
        c = self.chart([1e-3] * 400)                                      # -0.2 s to 0.2 s
        self.assertAlmostEqual(c["zero"], 240.0, places=1)
        late = run("return s.scopeChart(data, 480, 160, {}).zero;", buckets([1e-3] * 10, t0=0.5))
        self.assertIsNone(late)

    def test_the_time_axis_is_in_milliseconds_under_a_second_and_seconds_above(self):
        self.assertEqual(run("return [s.timeLabel(0.0123, 0.3), s.timeLabel(-0.2, 0.4), s.timeLabel(0, 0.4), s.timeLabel(1.5, 3), s.timeLabel(-0.2, 5)];"),
                         ["12.3 ms", "-200 ms", "0 ms", "1.5 s", "-0.2 s"])

    def test_ticks_are_round_numbers_inside_the_range(self):
        for t0, t1 in ((-0.2, 0.4), (0.0, 0.0123), (-1.0, 4.0), (0.0030, 0.0042)):
            ts = run("return s.ticks(data[0], data[1]);", [t0, t1])
            self.assertGreaterEqual(len(ts), 3, (t0, t1))
            self.assertLessEqual(len(ts), 8, (t0, t1))
            self.assertTrue(all(t0 - 1e-9 <= t <= t1 + 1e-9 for t in ts), (t0, t1, ts))
            steps = {round(b - a, 9) for a, b in zip(ts, ts[1:])}
            self.assertEqual(len(steps), 1, "equal steps: %r" % ts)
            m = steps.pop()
            lead = f"{m:.12e}".split("e")[0]
            self.assertIn(lead[:3], ("1.0", "2.0", "5.0"), "a step of 1, 2 or 5 times a power of ten: %r" % m)

    def test_a_drag_is_turned_back_into_a_time_and_stays_inside_the_chart(self):
        c = run("const c = s.scopeChart(data, 480, 160, {}); return [c.timeAt(4), c.timeAt(240), c.timeAt(476), c.timeAt(-50), c.timeAt(9999)];", buckets([1e-3] * 400))
        self.assertAlmostEqual(c[0], -0.2, places=9)
        self.assertAlmostEqual(c[1], 0.0, places=9)
        self.assertAlmostEqual(c[2], 0.2, places=9)
        self.assertEqual((c[3], c[4]), (c[0], c[2]))

    def test_a_time_is_placed_inside_the_chart_so_a_stage_can_be_shaded(self):
        c = run("const c = s.scopeChart(data, 480, 160, {}); return [c.at(-0.2), c.at(0), c.at(0.2), c.at(-5), c.at(5)];", buckets([1e-3] * 400))
        self.assertEqual(c, [4.0, 240.0, 476.0, 4.0, 476.0])

    def test_nothing_to_draw_is_null(self):
        self.assertIsNone(run("return s.scopeChart([], 480, 160, {});"))


if __name__ == "__main__":
    unittest.main()
