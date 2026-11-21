#!/usr/bin/env python3
"""Replaying a recording (docs/specs/meter-page.md, "Live and replay"): any stretch of its CSV as an exact envelope, stitched with a saved wake's samples where the stretch is inside one,
and deleting a recording with everything kept beside it.
Run:  python3 tests/instruments/test_replay.py
"""
import os
import random
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.core.registry import UsageError  # noqa: E402
from nrf_workbench.instruments import replay, sampler  # noqa: E402
from test_capture import PathBase, WAKE_ADC  # noqa: E402

HEADER = "t_s,wall,value,unit,function,range,nplc,overload,min,max,n\n"


def write_csv(path, rows, header=HEADER):
    """rows: (t_s, value, min, max, n, overload) per line."""
    with open(path, "w") as fh:
        fh.write(header)
        for t, v, lo, hi, n, ov in rows:
            fh.write("%.3f,%.3f,%s,A,CURR,0,,%d,%s,%s,%d\n" % (t, 1795157166.0 + t, "" if ov else repr(v), ov, "" if ov else repr(lo), "" if ov else repr(hi), n))


def blocks(count, seed=1, width=0.2):
    rng = random.Random(seed)
    out = []
    for i in range(count):
        v = 1e-5 * (1 + rng.random()) + (3e-3 if i % 37 == 5 else 0)
        out.append((round((i + 1) * width, 3), v, v * 0.9, v * 1.3 + (4e-3 if i % 37 == 5 else 0), 20000, 0))
    return out


class Folder(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nrfwb-replay-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "r.csv")


class TheTrace(Folder):
    def test_it_reads_the_rows_and_how_long_each_covers(self):
        write_csv(self.path, blocks(10))
        t = replay.Trace.read(self.path)
        self.assertEqual(t.rows, 10)
        self.assertAlmostEqual(t.start_s, 0.0, places=9)
        self.assertAlmostEqual(t.end_s, 2.0, places=9)
        self.assertAlmostEqual(t.finest_s, 0.2, places=9)
        self.assertEqual((t.unit, t.function), ("A", "CURR"))
        self.assertAlmostEqual(t.wall0, 1795157166.0, places=3)

    def test_a_row_that_does_not_parse_or_is_an_overload_or_runs_backwards_is_left_out(self):
        rows = blocks(6)
        rows[2] = (rows[2][0], 0, 0, 0, 0, 1)                           # an overload: no value
        write_csv(self.path, rows)
        with open(self.path, "a") as fh:
            fh.write("garbage,line\n1.0,1.0,not-a-number,A,CURR,0,,0,1,1,1\n0.1,1.0,1e-5,A,CURR,0,,0,1e-5,1e-5,1\n")
        t = replay.Trace.read(self.path)
        self.assertEqual(t.rows, 5, "the overload, two broken lines and the one that goes back in time are gone")
        self.assertAlmostEqual(t.widths[2], 0.4, places=9, msg="the row after an overload covers the gap")

    def test_an_older_file_without_min_max_and_n_reads_as_one_reading_each(self):
        with open(self.path, "w") as fh:
            fh.write("t_s,wall,value,unit,function,range,nplc,overload\n0.400,1795157166.0,0.001,A,CURR,,10,0\n0.800,1795157166.4,0.003,A,CURR,,10,0\n")
        t = replay.Trace.read(self.path)
        self.assertEqual((list(t.lows), list(t.highs), list(t.counts)), ([0.001, 0.003], [0.001, 0.003], [1.0, 1.0]))
        self.assertAlmostEqual(t.widths[0], 0.4, places=9, msg="the first reading covers the time before it")

    def test_a_row_with_the_same_time_as_the_one_before_is_left_out(self):
        rows = blocks(5)
        rows.insert(3, (rows[2][0], 1.0, 1.0, 1.0, 20000, 0))            # the same time again: it would cover no time at all
        write_csv(self.path, rows)
        t = replay.Trace.read(self.path)
        self.assertEqual(t.rows, 5)
        self.assertTrue(all(w > 0 for w in t.widths), list(t.widths))

    def test_an_empty_file_is_a_trace_with_no_rows_and_no_window(self):
        write_csv(self.path, [])
        t = replay.Trace.read(self.path)
        self.assertEqual((t.rows, t.start_s, t.end_s), (0, 0.0, 0.0))
        self.assertIsNone(t.window())


class TheWindow(Folder):
    def naive(self, t, lo_t, hi_t, points):
        """The same answer by the slowest honest route: row by row."""
        width = max((hi_t - lo_t) / points, t.finest_s)
        got = {}
        for i in range(t.rows):
            if not (t.ends[i] > lo_t and t.ends[i] - t.widths[i] < hi_t):
                continue
            mid = t.ends[i] - t.widths[i] / 2
            k = int((min(max(mid, lo_t), hi_t - 1e-12) - lo_t) // width)
            got.setdefault(k, []).append(i)
        return width, got

    def test_every_bucket_is_exact_at_any_zoom(self):
        write_csv(self.path, blocks(500))
        t = replay.Trace.read(self.path)
        for t0, t1, points in ((None, None, 1000), (None, None, 7), (10.0, 20.0, 50), (33.3, 34.1, 1000), (0.0, 100.0, 2000), (99.0, 100.0, 5), (40.05, 40.95, 3)):
            buckets, stats, width = t.window(t0, t1, points)
            lo_t, hi_t = (t.start_s if t0 is None else t0), (t.end_s if t1 is None else t1)
            w, got = self.naive(t, lo_t, hi_t, points)
            self.assertAlmostEqual(width, w, places=12)
            self.assertLessEqual(len(buckets), points)
            self.assertEqual(len(buckets), len(got), (t0, t1, points))
            for b, k in zip(buckets, sorted(got)):
                idx = got[k]
                self.assertAlmostEqual(b["min"], min(t.lows[i] for i in idx), delta=1e-18)
                self.assertAlmostEqual(b["max"], max(t.highs[i] for i in idx), delta=1e-18)
                self.assertAlmostEqual(b["mean"], sum(t.means[i] * t.widths[i] for i in idx) / sum(t.widths[i] for i in idx), delta=1e-15)
                self.assertEqual(b["n"], sum(t.counts[i] for i in idx))
                self.assertAlmostEqual(b["t0"], lo_t + k * width, places=9)

    def test_the_stretchs_figures_do_not_depend_on_how_many_points_are_drawn(self):
        write_csv(self.path, blocks(300))
        t = replay.Trace.read(self.path)
        a = t.window(10.0, 30.0, 2)[1]
        b = t.window(10.0, 30.0, 2000)[1]
        self.assertEqual(a, b)
        rows = [i for i in range(t.rows) if t.ends[i] > 10.0 and t.ends[i] - t.widths[i] < 30.0]
        self.assertAlmostEqual(a["lowest"], min(t.lows[i] for i in rows), delta=1e-18)
        self.assertAlmostEqual(a["highest"], max(t.highs[i] for i in rows), delta=1e-18)
        self.assertAlmostEqual(a["charge"], sum(t.means[i] * t.widths[i] for i in rows), delta=1e-15)
        self.assertAlmostEqual(a["mean"], a["charge"] / a["seconds"], delta=1e-18)
        self.assertEqual(a["samples"], len(rows) * 20000)

    def test_the_figures_count_a_row_for_the_part_of_it_inside_the_stretch(self):
        write_csv(self.path, [(0.2, 1e-3, 1e-3, 1e-3, 20000, 0), (0.4, 3e-3, 3e-3, 3e-3, 20000, 0), (0.6, 9e-3, 9e-3, 9e-3, 20000, 0)])
        t = replay.Trace.read(self.path)
        stats = t.window(0.1, 0.5, 4)[1]
        self.assertAlmostEqual(stats["seconds"], 0.4, places=12)
        self.assertAlmostEqual(stats["charge"], 1e-3 * 0.1 + 3e-3 * 0.2 + 9e-3 * 0.1, places=12, msg="half of the first row, all the second, half of the third")
        self.assertAlmostEqual(stats["mean"], stats["charge"] / 0.4, places=12)
        self.assertEqual(stats["samples"], 40000)
        self.assertEqual((stats["lowest"], stats["highest"]), (1e-3, 9e-3), "a row that is only partly inside still says what its extremes were")

    def test_a_window_narrower_than_a_row_a_point_is_drawn_at_one_row_a_point(self):
        write_csv(self.path, blocks(100))
        t = replay.Trace.read(self.path)
        buckets, stats, width = t.window(5.0, 5.8, 1000)
        self.assertAlmostEqual(width, 0.2, places=9, msg="0.8 s in 1000 points is 0.8 ms: not finer than the rows")
        self.assertEqual(len(buckets), 4)

    def test_a_window_that_holds_no_row_or_runs_backwards_is_none(self):
        write_csv(self.path, blocks(10))
        t = replay.Trace.read(self.path)
        self.assertIsNone(t.window(50.0, 60.0))
        self.assertIsNone(t.window(1.0, 1.0))
        self.assertIsNone(t.window(1.5, 1.0))

    def test_points_out_of_range_are_refused(self):
        write_csv(self.path, blocks(10))
        t = replay.Trace.read(self.path)
        for bad in (1, 2001, True, 5.5, "9"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                t.window(points=bad)

    def test_a_multimeter_recording_with_uneven_readings_is_time_weighted(self):
        with open(self.path, "w") as fh:
            fh.write(HEADER)
            fh.write("1.000,1795157166.0,0.001,A,CURR,,10,0,0.001,0.001,1\n4.000,1795157169.0,0.004,A,CURR,,10,0,0.004,0.004,1\n")
        t = replay.Trace.read(self.path)
        stats = t.window()[1]
        self.assertAlmostEqual(stats["mean"], (0.001 * 1.0 + 0.004 * 3.0) / 4.0, places=12, msg="the second reading covers three seconds, the first one")

    def test_the_cache_reparses_only_when_the_file_changes(self):
        write_csv(self.path, blocks(10))
        cache = replay.TraceCache()
        a = cache.get(self.path)
        self.assertIs(cache.get(self.path), a)
        time.sleep(0.01)
        write_csv(self.path, blocks(20))
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        b = cache.get(self.path)
        self.assertIsNot(b, a)
        self.assertEqual(b.rows, 20)
        cache.forget(self.path)
        self.assertIsNot(cache.get(self.path), b)


class ThroughTheSampler(PathBase):
    """The fake unit records four wakes (the sleep level given); replaying the recording asks the sampler, which reads the CSV and the saved wakes."""

    def setUp(self):
        PathBase.setUp(self)
        self.record_session("rak-1")

    def replay(self, **args):
        return self.call("meter.replay", dict({"recording": "rak-1"}, **args))

    def test_the_whole_recording_comes_from_its_readings_and_says_where_its_wakes_are(self):
        res = self.replay().data
        self.assertEqual((res["source"], res["wake"]), ("csv", None))
        self.assertGreaterEqual(res["duration_s"], 8.0, "40 blocks of 0.2 s at least: the unit is not paced here, so it ran on a little before the stop")
        self.assertEqual(len(res["wakes"]), 4)
        self.assertLess(res["wakes"][0]["t0"], res["wakes"][0]["t1"])
        self.assertAlmostEqual(res["resolution_s"], 0.2, places=9)
        self.assertEqual(res["unit"], "A")
        self.assertAlmostEqual(res["stats"]["highest"], self.wake_a, delta=self.wake_a * 1e-6)
        self.assertAlmostEqual(res["stats"]["lowest"], self.sleep_a, delta=self.sleep_a * 1e-6)

    def test_a_stretch_inside_a_saved_wake_is_drawn_from_its_samples_on_the_recordings_clock(self):
        span = self.replay().data["wakes"][0]
        burst0 = span["t0"] + 0.2                                       # the capture is 0.2 s of lead-in, the awake block, a tail
        res = self.replay(t0=burst0 - 0.001, t1=burst0 + 0.004, points=50).data
        self.assertEqual((res["source"], res["wake"]), ("wake", 1))
        self.assertAlmostEqual(res["resolution_s"], 1e-5, places=12)
        self.assertAlmostEqual(res["t0"], burst0 - 0.001, places=4)
        highs = [b["max"] for b in res["buckets"]]
        lows = [b["min"] for b in res["buckets"]]
        self.assertAlmostEqual(max(highs), self.wake_a, delta=self.wake_a * 1e-9)
        self.assertAlmostEqual(min(lows), self.sleep_a, delta=self.sleep_a * 1e-9)
        edge = next(b for b in res["buckets"] if b["max"] > (self.wake_a + self.sleep_a) / 2)
        self.assertAlmostEqual(edge["t0"], burst0, delta=2e-4, msg="the burst starts where the readings say the awake block starts")
        self.assertEqual(res["stats"]["samples"], sum(b["n"] for b in res["buckets"]))
        self.assertAlmostEqual(res["stats"]["highest"], self.wake_a, delta=self.wake_a * 1e-9)

    def test_a_stretch_that_is_not_inside_a_wake_stays_at_the_readings_however_narrow(self):
        res = self.replay(t0=1.0, t1=1.001, points=50).data
        self.assertEqual(res["source"], "csv")
        self.assertEqual(res["points"], 1)

    def test_a_wide_stretch_that_covers_a_wake_is_not_stitched(self):
        span = self.replay().data["wakes"][0]
        res = self.replay(t0=span["t0"] - 0.1, t1=span["t1"] + 0.1, points=2000).data
        self.assertEqual(res["source"], "csv", "only a stretch wholly inside a saved wake can be drawn from it")

    def test_the_saved_wakes_of_the_recording_asked_for_are_held_between_requests_until_they_change(self):
        a = self.sampler._wakes("rak-1")
        self.assertIs(self.sampler._wakes("rak-1"), a, "decoding a wake takes seconds: a person zooming asks again and again")
        with open(os.path.join(self.folder, "rak-1.wakes", "99.json"), "w") as fh:
            fh.write("{}")
        self.assertIsNot(self.sampler._wakes("rak-1"), a, "a wake added to the folder makes a new set")

    def test_a_replay_of_a_recording_that_kept_no_wakes_is_the_readings_alone(self):
        os.rename(os.path.join(self.folder, "rak-1.wakes"), os.path.join(self.folder, "gone"))
        res = self.replay().data
        self.assertEqual((res["source"], res["wakes"]), ("csv", []))

    def test_bad_arguments(self):
        with self.assertRaises(UsageError):
            self.replay(points=1)
        with self.assertRaises(UsageError):
            self.replay(t0=500.0, t1=600.0)
        res = self.call("meter.replay", {"recording": "nope"})
        self.assertEqual((res.cls, res.code), ("refused", "recording_unknown"))
        res = self.call("meter.replay", {"recording": "../x"})
        self.assertEqual(res.code, "recording_unknown")


class DeletingARecording(PathBase):
    def setUp(self):
        PathBase.setUp(self)
        self.record_session("rak-1")
        with open(os.path.join(self.folder, "rak-1.stages.json"), "w") as fh:
            fh.write('{"stages": []}')
        with open(os.path.join(self.folder, "rak-1.events.json"), "w") as fh:
            fh.write('{"events": []}')
        self.call("meter.replay", {"recording": "rak-1"})                # parsed and held

    def files(self):
        return sorted(f for f in os.listdir(self.folder) if f.startswith("rak-1"))

    def test_everything_kept_with_it_goes_and_the_answer_says_what(self):
        self.assertEqual(self.files(), ["rak-1.csv", "rak-1.events.json", "rak-1.json", "rak-1.stages.json", "rak-1.wakes"])
        res = self.call("meter.recording_delete", {"name": "rak-1"})
        self.assertTrue(res.ok)
        self.assertEqual(sorted(res.data["removed"]), ["rak-1.csv", "rak-1.events.json", "rak-1.json", "rak-1.stages.json", "rak-1.wakes"])
        self.assertEqual(self.files(), [])
        self.assertEqual([r["name"] for r in self.call("meter.recordings").data["recordings"]], [])
        self.assertIsNone(self.sampler._traces._key, "the parsed rows are let go, not held for a file that is gone")
        self.assertIsNone(self.sampler._stored_key)

    def test_a_deleted_recording_is_gone_from_the_replay_and_its_name_can_be_used_again(self):
        self.call("meter.recording_delete", {"name": "rak-1"})
        self.assertEqual(self.call("meter.replay", {"recording": "rak-1"}).code, "recording_unknown")
        self.assertEqual(self.call("meter.captures", {"recording": "rak-1"}).code, "recording_unknown")
        self.record_session("rak-1")
        self.assertTrue(self.call("meter.replay", {"recording": "rak-1"}).ok)

    def test_other_recordings_are_left_alone(self):
        self.record_session("rak-2")
        self.call("meter.recording_delete", {"name": "rak-1"})
        self.assertEqual(sorted(f for f in os.listdir(self.folder) if f.startswith("rak-2")), ["rak-2.csv", "rak-2.json", "rak-2.wakes"])

    def test_one_that_does_not_exist_is_recording_unknown(self):
        for name in ("nope", "../rak-1", "rak-1.csv", ""):
            res = self.call("meter.recording_delete", {"name": name})
            self.assertEqual((res.cls, res.code), ("refused", "recording_unknown"), name)
        self.assertEqual(self.files()[0], "rak-1.csv")

    def test_a_recording_that_is_being_written_is_refused_and_survives(self):
        self.unit.profile = lambda i: (1000, 0)
        self.sampler.start("record", 60, name="live-1", source="ppk2")
        self.assertTrue(self.until_reading())
        res = self.call("meter.recording_delete", {"name": "live-1"})
        self.assertEqual((res.cls, res.code), ("refused", "meter_busy"))
        self.assertEqual(res.evidence["name"], "live-1")
        self.assertTrue(os.path.exists(os.path.join(self.folder, "live-1.csv")))
        self.sampler.stop()
        self.assertTrue(self.call("meter.recording_delete", {"name": "live-1"}).ok, "once it has ended it can go")

    def until_reading(self):
        from test_ppk2 import until
        return until(lambda: self.sampler.session()["stats"]["readings"] >= 20000, 10)

    def test_the_newest_ten_rule_removes_the_same_files(self):
        for k in range(10):
            base = os.path.join(self.folder, "old-%d" % k)
            for suffix in sampler.RECORDING_SUFFIXES:
                open(base + suffix, "w").close()
            os.makedirs(base + ".wakes")
            os.utime(base + ".csv", (1000 + k, 1000 + k))
        self.sampler.start("record", 60, name="new", source="ppk2")
        self.sampler.stop()
        for gone in ("old-0.", "old-1."):                               # rak-1 and ten more make eleven: two go to leave room for the new one
            self.assertEqual([f for f in os.listdir(self.folder) if f.startswith(gone)], [], "the oldest went with its sidecar, stages, events and wakes")
        self.assertEqual(len([f for f in os.listdir(self.folder) if f.startswith("old-2.")]), 5, "the next oldest is kept whole")


if __name__ == "__main__":
    unittest.main()
