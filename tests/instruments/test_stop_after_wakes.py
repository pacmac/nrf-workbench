#!/usr/bin/env python3
"""A recording that stops on the board's own wakes (docs/specs/power-workbench.md, "Capture: stop on the clock, or on the board"), and says how it ended.
`stop_after_wakes` ends the session when the Nth WHOLE wake has closed; a wake already in progress when the recording began, or one cut at the longest wake, is kept and not counted;
the give-up is derived from the beat; every session names why it ended (`ended_by`).
Run:  python3 tests/instruments/test_stop_after_wakes.py
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

from nrf_workbench.core.registry import Command, UsageError  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from nrf_workbench.instruments import capture  # noqa: E402
from test_capture import SLEEP, WAKE, WAKE_ADC, PathBase, feed_blocks, given, hot, quiet  # noqa: E402
from test_ppk2 import until  # noqa: E402

FLAT = lambda i: (1000, 0)                                              # a board that never wakes


class TheCapturerCountsWholeWakes(unittest.TestCase):
    def test_a_wake_counts_when_it_closes_and_not_when_it_opens(self):
        c = capture.Capturer(given())
        feed_blocks(c, quiet(2) + hot(2))
        self.assertEqual((c.count, c.whole), (1, 0), "open: not counted yet")
        feed_blocks(c, quiet(3))
        self.assertEqual((c.count, c.whole), (1, 1))

    def test_a_wake_in_progress_when_the_recording_began_is_kept_and_not_counted(self):
        c = capture.Capturer(given())
        feed_blocks(c, hot(2) + quiet(3) + hot(2) + quiet(3))
        self.assertEqual((c.count, c.whole), (2, 1))
        self.assertEqual((c.get(1).partial, c.get(2).partial), (True, False))

    def test_a_wake_that_opens_before_any_sleep_level_was_known_is_partial(self):
        c = capture.Capturer(capture.WakeProfile())                     # nothing given: the level is found from the first 20 blocks, and the block before the 20th judged nothing
        feed_blocks(c, quiet(19) + hot(1) + quiet(3))
        self.assertEqual((c.count, c.get(1).partial, c.whole), (1, True, 0))

    def test_a_wake_cut_at_the_longest_is_not_counted_and_nor_is_its_continuation(self):
        c = capture.Capturer(given(max_s=1.0))                          # 5 blocks
        feed_blocks(c, quiet(2) + hot(8) + quiet(3))
        self.assertEqual(c.whole, 0)
        self.assertTrue(c.get(1).truncated)
        self.assertTrue(c.get(2).partial, "it begins where the cut ended: no rising edge was seen")

class OnTheSession(PathBase):
    def start(self, command="meter.record", **args):
        a = dict({"source": "ppk2", "wake_sleep_a": self.sleep_a}, **args)
        if command == "meter.record":
            a.setdefault("name", "swk")
        res = self.call(command, a)
        self.assertTrue(res.ok, getattr(res, "message", res))
        return res

    def done(self):
        self.assertTrue(until(lambda: self.sampler.session()["state"] in ("idle", "error"), 30), "the session did not end")
        return self.sampler.session()

    def test_a_recording_ends_when_the_second_wake_has_closed_and_keeps_both_whole(self):
        self.start(stop_after_wakes=2)
        s = self.done()
        self.assertEqual((s["ended_by"], s["wakes"], s["stop_after_wakes"]), ("wakes_done", 2, 2))
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual([r["samples"] for r in rows], [60000, 60000], "each with its lead-in and its tail: closed, and no third")
        self.assertLess(s["stats"]["readings"], self.BLOCKS * 20000, "it did not run on to the end of the unit's stream")

    def test_a_wake_in_progress_at_the_start_does_not_count(self):
        self.unit.profile = lambda i: (WAKE_ADC if (i // 20000) % 10 == 0 else 1000, 0)
        self.start(stop_after_wakes=1)
        s = self.done()
        rows = self.call("meter.captures").data["captures"]
        self.assertEqual((s["ended_by"], s["wakes"], len(rows)), ("wakes_done", 1, 2))
        self.assertEqual([r["partial"] for r in rows], [True, False])

    def test_no_wake_ever_is_a_give_up_at_the_bound_given_and_says_how_many_were_seen(self):
        self.unit.profile = FLAT
        res = self.start(stop_after_wakes=1, bound_s=2.0)
        self.assertEqual(res.data["bound_s"], 2.0)
        s = self.done()
        self.assertEqual((s["ended_by"], s["wakes"], s["stop_after_wakes"]), ("gave_up", 0, 1))

    def test_a_wake_period_is_never_assumed_so_with_no_bound_the_give_up_is_the_twelve_hour_maximum(self):
        """Wakes need not be periodic (a button, a motion sensor, a radio frame), so no bound is derived from how often a board wakes."""
        self.unit.profile = FLAT
        res = self.start(stop_after_wakes=1)
        self.assertEqual(res.data["bound_s"], 43200.0)
        self.assertNotIn("beat_s", res.data)
        self.sampler.stop()
        self.assertEqual(self.done()["ended_by"], "stopped")

    def test_wakes_at_irregular_times_are_counted_all_the_same(self):
        self.unit.profile = lambda i: (WAKE_ADC if (i // 20000) in (3, 4, 9, 31, 32) else 1000, 0)          # three wakes, 1.2 s then 4.4 s apart
        self.start(stop_after_wakes=3)
        s = self.done()
        self.assertEqual((s["ended_by"], s["wakes"]), ("wakes_done", 3))

    def test_a_count_no_recording_could_reach_is_refused_naming_its_argument(self):
        with self.assertRaises(UsageError) as cm:
            self.call("meter.record", {"source": "ppk2", "stop_after_wakes": 216001})
        self.assertEqual(cm.exception.arg, "stop_after_wakes", "a wake is at least one 0.2 s block: 12 h holds 216,000")
        self.assertEqual(self.sampler.session()["state"], "idle")

    def test_n_is_a_whole_number_of_at_least_one(self):
        for bad in (0, -1):
            with self.assertRaises(UsageError) as cm:
                self.call("meter.watch", {"source": "ppk2", "stop_after_wakes": bad})
            self.assertEqual(cm.exception.arg, "stop_after_wakes")

    def test_with_neither_a_bound_nor_a_count_the_bound_is_asked_for(self):
        with self.assertRaises(UsageError) as cm:
            self.call("meter.watch", {"source": "ppk2"})
        self.assertEqual(cm.exception.arg, "bound_s")

    def test_a_multimeter_session_cannot_stop_on_wakes(self):
        import types
        self.sampler.sources["meter"] = types.SimpleNamespace(name="meter")
        with self.assertRaises(UsageError) as cm:
            self.call("meter.watch", {"source": "meter", "stop_after_wakes": 1})
        self.assertEqual(cm.exception.arg, "stop_after_wakes")


class HowASessionEnds(PathBase):
    def done(self):
        self.assertTrue(until(lambda: self.sampler.session()["state"] in ("idle", "error"), 30), "the session did not end")
        return self.sampler.session()

    def test_the_time_bound_is_time_reached(self):
        self.unit.profile = FLAT
        self.assertTrue(self.call("meter.record", {"bound_s": 1, "source": "ppk2", "name": "tb"}).ok)
        self.assertEqual(self.done()["ended_by"], "time_reached")
        with open(os.path.join(self.folder, "tb.json")) as fh:
            self.assertEqual(json.load(fh)["ended_by"], "time_reached", "the sidecar says it too")

    def test_a_stop_by_hand_is_stopped_and_a_running_session_has_no_cause_yet(self):
        self.unit.profile = FLAT
        self.assertTrue(self.call("meter.watch", {"bound_s": 60, "source": "ppk2"}).ok)
        self.assertIsNone(self.sampler.session()["ended_by"])
        self.sampler.stop()
        self.assertEqual(self.done()["ended_by"], "stopped")

    def test_a_unit_that_goes_silent_is_error_with_the_reason(self):
        self.assertTrue(self.call("meter.watch", {"bound_s": 60, "source": "ppk2"}).ok)
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] > 0, 10))
        self.unit.silent = True
        s = self.done()
        self.assertEqual(s["ended_by"], "error")
        self.assertIn("gone silent", s["reason"])


if __name__ == "__main__":
    unittest.main()
