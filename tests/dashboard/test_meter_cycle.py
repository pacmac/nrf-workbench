#!/usr/bin/env python3
"""The wake/sleep cycle finder (ui/src/meter/cycle.js, docs/specs/meter-page.md "Battery life") run under Node on envelopes built by the server's own Envelope, against
means worked out here from the raw readings. Run:  python3 tests/dashboard/test_meter_cycle.py
"""
import json
import os
import random
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.instruments.envelope import Envelope  # noqa: E402

CYCLE = os.path.join(ROOT, "ui", "src", "meter", "cycle.js")
FORMAT = os.path.join(ROOT, "ui", "src", "meter", "format.js")
NODE = shutil.which("node")
STEP = 0.2                      # one reading every 0.2 s, as the multimeter at NPLC 10 and the profiler's blocks
SLEEP, WAKE = 10e-6, 20e-3      # 10 uA asleep, 20 mA in the radio burst


def run(script, data):
    code = ('import * as c from %s;\nimport * as f from %s;\nimport fs from "node:fs";\nconst data = JSON.parse(fs.readFileSync(0, "utf8"));\n'
            'const out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + CYCLE), json.dumps("file://" + FORMAT), script))
    p = subprocess.run([NODE, "--input-type=module", "-e", code], input=json.dumps(data), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def envelope(values, max_buckets=100000):
    e = Envelope(max_buckets)
    for i, v in enumerate(values):
        e.add(i * STEP, v)
    return e.snapshot()


def find(values, max_buckets=100000):
    return run("return c.findCycle(data);", envelope(values, max_buckets))


def pattern(period_s, wake_s, count, offset_s=0.0, level=WAKE):
    """`count` readings: asleep, with `wake_s` of `level` at the start of every `period_s`, the first wake starting `offset_s` in."""
    out = []
    for i in range(count):
        t = i * STEP - offset_s
        out.append(level if t >= 0 and (t % period_s) < wake_s - 1e-9 else SLEEP)
    return out


def starts(values):
    return [i for i, v in enumerate(values) if v > 1e-3 and (i == 0 or values[i - 1] <= 1e-3)]


@unittest.skipUnless(NODE, "node is not installed")
class FindingTheCycle(unittest.TestCase):
    def test_the_mean_is_exact_over_whole_cycles(self):
        values = pattern(60, 1, 1800, offset_s=5)                       # 6 minutes, wakes at 5 s, 65 s, ... 305 s
        s = starts(values)
        want = sum(values[s[0]:s[-1]]) / (s[-1] - s[0])
        r = find(values)
        self.assertTrue(r["found"])
        self.assertEqual(r["wakes"], len(s))
        self.assertEqual(r["cycles"], len(s) - 1)
        self.assertAlmostEqual(r["mean"], want, delta=want * 1e-9)
        self.assertAlmostEqual(r["period"], 60, places=6)
        self.assertTrue(r["regular"])

    def test_it_differs_from_the_whole_window_when_the_window_holds_part_of_a_cycle(self):
        values = pattern(60, 1, 650, offset_s=50)                        # 130 s: starts 50 s into a cycle, ends 20 s into the third wake
        window = sum(values) / len(values)
        one_cycle = ((60 - 1) * SLEEP + 1 * WAKE) / 60
        r = find(values)
        self.assertTrue(r["found"])
        self.assertAlmostEqual(r["mean"], one_cycle, delta=one_cycle * 1e-6)
        self.assertGreater(abs(window - one_cycle) / one_cycle, 0.05, "the window average must be the wrong one here, or this test proves nothing")

    def test_a_burst_shorter_than_a_bucket_is_found_by_the_highest_sample(self):
        values = pattern(60, 0.2, 6000, offset_s=7)                      # 0.2 s bursts, 20 minutes: buckets of 0.8 s or more after merging
        r = find(values, max_buckets=64)
        self.assertTrue(r["found"], r)
        self.assertEqual(r["cycles"], 19)
        self.assertAlmostEqual(r["period"], 60, delta=3.5, msg="the period is placed to one bucket at each end")

    def test_a_wake_of_two_bursts_is_one_wake(self):
        values = pattern(60, 0.4, 1800, offset_s=5)
        for i, v in enumerate(list(values)):                              # a second burst 1 s after each first one
            if v == WAKE and i + 5 < len(values):
                values[i + 5] = WAKE
        r = find(values)
        self.assertTrue(r["found"])
        self.assertEqual(r["wakes"], 6)
        self.assertAlmostEqual(r["period"], 60, places=6)

    def test_noise_on_a_steady_load_is_no_cycle(self):
        rng = random.Random(3)
        r = find([1e-3 * (1 + rng.uniform(-0.03, 0.03)) for _ in range(2000)])
        self.assertFalse(r["found"])
        self.assertEqual(r["reason"], "steady")

    def test_one_wake_is_not_a_cycle_and_says_how_many(self):
        r = find(pattern(1000, 1, 600, offset_s=20))
        self.assertFalse(r["found"])
        self.assertEqual((r["reason"], r["wakes"]), ("wakes", 1))

    def test_a_current_at_or_below_zero_has_no_sleep_level(self):
        r = find([-v for v in pattern(60, 1, 1800)])
        self.assertFalse(r["found"])
        self.assertEqual(r["reason"], "no_sleep")

    def test_a_device_awake_most_of_the_time_has_no_sleep_level_to_find(self):
        values = [WAKE] * 1500 + [SLEEP] * 20 + [WAKE] * 1500
        self.assertFalse(find(values)["found"])

    def test_gaps_that_differ_are_not_regular_and_the_estimate_still_holds(self):
        values = [SLEEP] * 1800
        for t in (10, 60, 130, 180, 250):                                # gaps 50, 70, 50, 70 s
            values[int(t / STEP)] = WAKE
        s = starts(values)
        want = sum(values[s[0]:s[-1]]) / (s[-1] - s[0])
        r = find(values)
        self.assertTrue(r["found"])
        self.assertFalse(r["regular"])
        self.assertAlmostEqual(r["shortest"], 50, places=6)
        self.assertAlmostEqual(r["longest"], 70, places=6)
        self.assertAlmostEqual(r["mean"], want, delta=want * 1e-9)

    def test_a_wake_in_progress_at_the_end_is_left_out(self):
        full = pattern(60, 5, 1800, offset_s=5)
        s = starts(full)
        ended_at_a_start = full[:s[-1] + 1]
        ended_mid_wake = full[:s[-1] + 12]
        a, b = find(ended_at_a_start), find(ended_mid_wake)
        self.assertTrue(a["found"] and b["found"])
        self.assertEqual(a["cycles"], b["cycles"])
        self.assertEqual(a["mean"], b["mean"])

    def test_a_partial_last_bucket_and_too_few_buckets_do_not_crash(self):
        self.assertEqual(find([SLEEP, SLEEP])["reason"], "short")
        self.assertEqual(run("return c.findCycle([]);", [])["reason"], "short")
        r = find(pattern(60, 1, 1803, offset_s=5), max_buckets=50)       # a snapshot with a bucket still filling
        self.assertTrue(r["found"])

    def test_the_mean_is_exact_through_merged_buckets_too(self):
        values = pattern(60, 1, 1800, offset_s=5)
        r = find(values, max_buckets=64)                                 # buckets of 32 readings (6.4 s) by the end
        self.assertTrue(r["found"], r)
        ia, ib = round(r["starts"][0] / STEP), round(r["starts"][-1] / STEP)
        self.assertEqual(ia % 32, 0)
        want = sum(values[ia:ib]) / (ib - ia)
        self.assertAlmostEqual(r["mean"], want, delta=want * 1e-9)

    def test_the_mean_is_weighted_by_time_when_readings_are_not_evenly_spaced(self):
        # a multimeter that stalls: the readings are 0.2 s apart except for a 3 s hole in the middle of a sleep
        values = pattern(60, 1, 1800, offset_s=5)
        times = [i * STEP + (3.0 if i > 700 else 0.0) for i in range(len(values))]
        e = Envelope(100000)
        for t, v in zip(times, values):
            e.add(t, v)
        buckets = e.snapshot()
        r = run("return c.findCycle(data);", buckets)
        s = [i for i in starts(values)]
        want = sum(values[i] * (times[i + 1] - times[i]) for i in range(s[0], s[-1])) / (times[s[-1]] - times[s[0]])
        self.assertTrue(r["found"])
        self.assertAlmostEqual(r["mean"], want, delta=want * 1e-9)
        self.assertGreater(abs(want - sum(values[s[0]:s[-1]]) / (s[-1] - s[0])), 0, "the hole must change the answer, or this proves nothing")

    def test_noise_on_the_sleep_level_does_not_make_wakes(self):
        rng = random.Random(11)
        values = [v * (1 + rng.uniform(-0.05, 0.05)) for v in pattern(60, 1, 1800, offset_s=5)]
        r = find(values)
        self.assertTrue(r["found"])
        self.assertEqual(r["wakes"], 6)

    def test_a_steady_draw_with_a_small_rise_is_not_a_wake_from_sleep(self):
        # 55 % of the time at 3 mA, the rest asleep, and a 4 mA blip each cycle: the median is the 3 mA, so the blip is less than twice it and is not a wake
        values = [10e-6 if (i % 50) >= 28 else 3e-3 for i in range(1800)]
        for i in range(0, 1800, 50):
            values[i] = 4e-3
        r = find(values)
        self.assertFalse(r["found"], r)
        self.assertEqual(r["reason"], "steady")

    def test_the_whole_recording_choice_is_the_average(self):
        # the page's default is capacity / mean over all the readings, unchanged by this feature: 1000 mAh at 1 mA is 1000 h
        self.assertEqual(run("return f.batteryLife(0.001, 1000);", {}), "41.7 days")
        self.assertEqual(run("return f.batteryLife(null, 1000);", {}), "")


@unittest.skipUnless(NODE, "node is not installed")
class TheTable(unittest.TestCase):
    def test_the_rows_add_up_to_the_cycle_exactly_and_match_the_raw_readings(self):
        values = pattern(60, 1, 1800, offset_s=5)                        # a 1 s wake of 20 mA every 60 s, 10 uA between
        r = find(values)
        w, s = r["phases"]["wake"], r["phases"]["sleep"]
        self.assertAlmostEqual(w["time"], 1.0, places=9)
        self.assertAlmostEqual(w["charge"], 1.0 * WAKE, delta=1e-12)
        self.assertAlmostEqual(w["mean"], WAKE, delta=1e-12)
        self.assertEqual(w["peak"], WAKE)
        self.assertAlmostEqual(s["time"], 59.0, places=9)
        self.assertAlmostEqual(s["charge"], 59.0 * SLEEP, delta=1e-12)
        self.assertAlmostEqual(s["mean"], SLEEP, delta=1e-15)
        self.assertAlmostEqual(w["time"] + s["time"], r["period"], places=9)
        self.assertAlmostEqual(w["charge"] + s["charge"], r["charge"] / r["cycles"], delta=1e-12)
        self.assertEqual(w["widest"], 5)

    def test_a_wake_of_one_reading_is_flagged_unresolved_but_its_charge_and_peak_are_exact(self):
        values = pattern(60, 0.2, 1800, offset_s=5)
        r = find(values)                                                  # one reading per bucket here
        w = r["phases"]["wake"]
        self.assertEqual(w["widest"], 1)
        self.assertAlmostEqual(w["charge"], 0.2 * WAKE, delta=1e-12)
        self.assertEqual(w["peak"], WAKE)
        merged = find(values, max_buckets=64)                             # buckets of 6.4 s: the wake is inside one, which also holds sleep
        wm = merged["phases"]["wake"]
        self.assertEqual(wm["widest"], 1)
        self.assertEqual(wm["peak"], WAKE, "the highest sample survives a merge")
        self.assertAlmostEqual(wm["time"] + merged["phases"]["sleep"]["time"], merged["period"], places=9)
        self.assertAlmostEqual(wm["charge"] + merged["phases"]["sleep"]["charge"], merged["charge"] / merged["cycles"], delta=1e-12)

    def test_wakes_of_different_lengths_give_the_mean_and_the_shortest_to_longest(self):
        values = [SLEEP] * 3000
        for t, n in ((10, 5), (70, 10), (130, 5), (190, 10), (250, 5)):   # 1 s and 2 s wakes
            for k in range(n):
                values[int(t / STEP) + k] = WAKE
        r = find(values)
        w = r["phases"]["wake"]
        self.assertEqual(r["cycles"], 4)
        self.assertAlmostEqual(w["shortest"], 1.0, places=9)
        self.assertAlmostEqual(w["longest"], 2.0, places=9)
        self.assertAlmostEqual(w["time"], 1.5, places=9)                  # the first four wakes: 1, 2, 1, 2 s
        self.assertEqual(w["widest"], 10)

    def test_a_stall_inside_a_wake_is_part_of_its_time_and_charge(self):
        values = pattern(60, 1, 1800, offset_s=5)
        times = [i * STEP + (3.0 if i > 28 else 0.0) for i in range(len(values))]     # the meter stalls 3 s after the third reading of the first wake (readings 25 to 29)
        e = Envelope(100000)
        for t, v in zip(times, values):
            e.add(t, v)
        r = run("return c.findCycle(data);", e.snapshot())
        first = [i for i in range(len(values)) if values[i] == WAKE][:5]
        want_time = times[first[-1] + 1] - times[first[0]]
        want_charge = sum(values[i] * (times[i + 1] - times[i]) for i in first)
        self.assertTrue(r["found"])
        self.assertAlmostEqual(r["phases"]["wake"]["longest"], want_time, places=9)
        self.assertAlmostEqual(r["phases"]["wake"]["longest"] - r["phases"]["wake"]["shortest"], 3.0, places=9)
        self.assertGreater(want_charge / (5 * STEP * WAKE), 1.5, "the stall must add charge, or this proves nothing")
        total = r["phases"]["wake"]["charge"] * r["cycles"]               # the first wake, stalled, and the four others (the fifth starts at the end of the span)
        self.assertAlmostEqual(total, want_charge + 4 * (5 * STEP * WAKE), delta=1e-12)

    def test_a_wake_of_two_bursts_takes_in_the_sleep_between_them(self):
        values = [SLEEP] * 1800
        for t in (10, 70, 130, 190):
            i = int(t / STEP)
            values[i] = WAKE
            values[i + 5] = WAKE                                          # a second burst 1 s after the first
        r = find(values)
        self.assertAlmostEqual(r["phases"]["wake"]["time"], 1.2, places=9)

    def test_the_spans_are_every_wake_and_start_where_the_marks_do(self):
        values = pattern(60, 1, 1800, offset_s=5)
        r = find(values)
        self.assertEqual(len(r["spans"]), r["wakes"])
        self.assertEqual([s["t0"] for s in r["spans"]], r["starts"])
        self.assertTrue(all(abs((s["t1"] - s["t0"]) - 1.0) < 1e-9 for s in r["spans"][:-1]))

    def test_the_text_helpers(self):
        t = run("return [f.chargeText(0.0036 * 7.3), f.chargeText(1.8), f.chargeText(0.0000036 * 5), f.chargeText(null), f.energyText(0.0036, 4), f.energyText(1.8, 3.3), f.energyText(1, null),"
                " f.lapse(0.2), f.lapse(0.05), f.lapse(12), f.lapse(90)];", {})
        self.assertEqual(t, ["7.3 µAh", "0.5 mAh", "5 nAh", "—", "4 µWh", "1.65 mWh", "—", "0.2 s", "0.05 s", "12 s", "1 min 30 s"])

    def test_the_chart_shades_the_wakes_with_a_visible_minimum_width(self):
        buckets = envelope(pattern(60, 0.2, 1800, offset_s=5))
        r = run("return c.findCycle(data);", buckets)
        chart = run("return f.bandChart(data.b, 480, 120, 'A', [], data.spans);", {"b": buckets, "spans": r["spans"] + [{"t0": -9, "t1": -8}]})
        self.assertEqual(len(chart["shades"]), r["wakes"])
        self.assertTrue(all(s["w"] >= 2 and 4 <= s["x"] <= 476 for s in chart["shades"]))
        self.assertEqual(run("return f.bandChart(data, 480, 120, 'A');", buckets)["shades"], [])


@unittest.skipUnless(NODE, "node is not installed")
class MarksOnTheChart(unittest.TestCase):
    def test_marks_are_x_positions_inside_the_chart_and_only_for_times_it_shows(self):
        buckets = envelope(pattern(60, 1, 1800, offset_s=5))
        found = run("return c.findCycle(data);", buckets)
        chart = run("return f.bandChart(data.b, 480, 120, 'A', data.marks);", {"b": buckets, "marks": found["starts"] + [-5, 99999]})
        self.assertEqual(len(chart["marks"]), len(found["starts"]))
        self.assertTrue(all(4 <= x <= 476 for x in chart["marks"]))
        self.assertEqual(chart["marks"], sorted(chart["marks"]))

    def test_no_marks_by_default(self):
        buckets = envelope([SLEEP, WAKE, SLEEP])
        self.assertEqual(run("return f.bandChart(data, 480, 120, 'A');", buckets)["marks"], [])


if __name__ == "__main__":
    unittest.main()
