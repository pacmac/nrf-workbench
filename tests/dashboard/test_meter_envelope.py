#!/usr/bin/env python3
"""The page's envelope (ui/src/meter/envelope.js) against the server's (nrf_workbench/instruments/envelope.py): the same readings must give the same buckets, to the
last bit, so what a page draws from the live feed equals what the server holds (docs/specs/meter-page.md). Run under Node, like test_help.py.
Run:  python3 tests/dashboard/test_meter_envelope.py
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

from nrf_workbench.instruments.envelope import Envelope, Stats  # noqa: E402

MODULE = os.path.join(ROOT, "ui", "src", "meter", "envelope.js")
FORMAT = os.path.join(ROOT, "ui", "src", "meter", "format.js")
NODE = shutil.which("node")


def node(script, data):
    """Run a snippet with `m` (envelope.js), `f` (format.js) and `data` (read from stdin: 25,000 readings do not fit on a command line)."""
    code = ('import * as m from %s;\nimport * as f from %s;\nimport fs from "node:fs";\nconst data = JSON.parse(fs.readFileSync(0, "utf8"));\n'
            'const out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + MODULE), json.dumps("file://" + FORMAT), script))
    p = subprocess.run([NODE, "--input-type=module", "-e", code], input=json.dumps(data), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def python_snapshot(values, step, max_buckets):
    e = Envelope(max_buckets)
    for i, v in enumerate(values):
        e.add(i * step, v)
    return e.snapshot()


@unittest.skipUnless(NODE, "node is not installed")
class SameAlgorithm(unittest.TestCase):
    def test_the_same_readings_give_the_same_buckets_through_every_merge(self):
        rng = random.Random(5)
        for count, max_buckets in ((7, 4), (100, 8), (1000, 16), (5000, 64), (25000, 2000)):
            values = [rng.uniform(-2, 2) for _ in range(count)]
            js = node("const e = new m.Envelope(data.max); data.v.forEach((x, i) => e.add(i * data.step, x)); return e.snapshot();",
                      {"v": values, "step": 0.2, "max": max_buckets})
            py = python_snapshot(values, 0.2, max_buckets)
            self.assertEqual(len(js), len(py), (count, max_buckets))
            self.assertEqual(js, py, (count, max_buckets))

    def test_the_bound_is_the_same_2000(self):
        self.assertEqual(node("return m.MAX_BUCKETS;", None), 2000)

    def test_statistics_and_charge_agree(self):
        rng = random.Random(9)
        values = [rng.uniform(0, 0.01) for _ in range(500)]
        js = node("const s = new m.Stats(); data.v.forEach((x, i) => s.add(i * 0.2, x)); s.overload(); return s.toObject(true);", {"v": values})
        s = Stats()
        for i, v in enumerate(values):
            s.add(i * 0.2, v)
        s.overload()
        py = s.to_dict(current=True)
        for key in ("readings", "overloads", "min", "max"):
            self.assertEqual(js[key], py[key], key)
        for key in ("mean", "charge_c", "charge_mah"):
            self.assertAlmostEqual(js[key], py[key], places=12, msg=key)

    def test_a_page_that_starts_from_a_profile_keeps_every_reading_and_carries_on(self):
        """Envelope.from(snapshot) then more readings: the totals (count, lowest, highest, sum) stay exact."""
        rng = random.Random(3)
        first = [rng.uniform(0, 1) for _ in range(3000)]
        more = [rng.uniform(0, 1) for _ in range(700)]
        snap = python_snapshot(first, 0.2, 64)
        out = node("const e = m.Envelope.from(data.snap, 64); data.more.forEach((x, i) => e.add(1000 + i * 0.2, x)); return e.snapshot();",
                   {"snap": snap, "more": more})
        allv = first + more
        self.assertEqual(sum(b["n"] for b in out), len(allv))
        self.assertEqual((min(b["min"] for b in out), max(b["max"] for b in out)), (min(allv), max(allv)))
        self.assertAlmostEqual(sum(b["mean"] * b["n"] for b in out), sum(allv), places=9)
        self.assertLessEqual(len(out), 64)

    def test_stats_from_a_profile_carry_the_charge_on_from_the_last_reading(self):
        out = node("const s = m.Stats.from({readings: 2, min: 0.001, max: 0.001, mean: 0.001, charge_c: 0.001, overloads: 0}, [1.0, 0.001]);"
                   "s.add(3.0, 0.001); return s.toObject(true);", None)
        self.assertEqual(out["readings"], 3)
        self.assertAlmostEqual(out["charge_c"], 0.001 + 0.001 * 2.0)


@unittest.skipUnless(NODE, "node is not installed")
class Format(unittest.TestCase):
    def test_a_clock_for_a_session_and_words_for_a_choice(self):
        out = node("return [f.clock(0), f.clock(59), f.clock(252), f.clock(3725), f.clock(43200), f.words(60), f.words(90), f.words(3600), f.words(43200)];", None)
        self.assertEqual(out, ["0:00", "0:59", "4:12", "1:02:05", "12:00:00", "1 min", "1 min 30 s", "1 h", "12 h"])

    def test_battery_life_from_an_average_and_a_capacity(self):
        out = node("return [f.batteryLife(0.001, 1000), f.batteryLife(0.0001, 1000), f.batteryLife(0.00001, 2000), f.batteryLife(0, 1000), f.batteryLife(0.001, 0), f.batteryLife(0.001, NaN)];", None)
        self.assertEqual(out, ["41.7 days", "417 days", "22.8 years", "", "", ""])

    def test_the_chart_always_has_zero_on_its_axis_and_a_spike_is_the_top_of_the_band(self):
        buckets = [{"t0": i, "t1": i + 1, "min": 0.0001, "max": 0.0001 if i != 5 else 0.05, "mean": 0.0001, "n": 5} for i in range(10)]
        out = node("const c = f.bandChart(data, 480, 120, 'A'); return {axis: [c.axisLo, c.axisHi], lo: c.lo, hi: c.hi, empty: f.bandChart([], 480, 120)};", buckets)
        self.assertEqual((out["axis"], out["hi"], out["empty"]), (["0 A", "50 mA"], "50 mA", None))


if __name__ == "__main__":
    unittest.main()
