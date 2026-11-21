#!/usr/bin/env python3
"""Named events on a recording (nrf_workbench/instruments/events.py, docs/specs/meter-page.md "Events"): the rules, the file, and through the sampler and the registry.
Run:  python3 tests/instruments/test_recording_events.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.core.registry import UsageError  # noqa: E402
from nrf_workbench.instruments import events  # noqa: E402
from test_capture import PathBase  # noqa: E402


class TheRules(unittest.TestCase):
    def test_an_event_is_added_in_order_of_its_start(self):
        ev = events.set_event([], "tx", 5.0, 6.0)
        ev = events.set_event(ev, "boot", 1.0, 2.0)
        ev = events.set_event(ev, "rx", 6.0, 26.0)
        self.assertEqual([e["name"] for e in ev], ["boot", "tx", "rx"])
        self.assertEqual(ev[0], {"name": "boot", "start_s": 1.0, "end_s": 2.0})

    def test_the_same_name_in_any_case_changes_that_event_and_the_others_are_untouched(self):
        ev = events.set_event(events.set_event([], "TX", 5.0, 6.0), "rx", 6.0, 9.0)
        ev = events.set_event(ev, "tx", 5.5, 6.5)
        self.assertEqual([(e["name"], e["start_s"]) for e in ev], [("tx", 5.5), ("rx", 6.0)])

    def test_events_may_overlap_and_nest(self):
        ev = events.set_event([], "wake", 0.0, 24.0)
        ev = events.set_event(ev, "tx 1", 1.0, 1.7)
        ev = events.set_event(ev, "whole thing", 0.0, 24.0)
        self.assertEqual(len(ev), 3, "a burst is inside the wake it belongs to: stages could not, events can")

    def test_was_renames_or_moves_in_one_change(self):
        ev = events.set_event(events.set_event([], "a", 1.0, 2.0), "b", 3.0, 4.0)
        renamed = events.set_event(ev, "c", 1.0, 2.0, was="a")
        self.assertEqual([e["name"] for e in renamed], ["c", "b"])
        moved = events.set_event(ev, "a", 1.5, 2.5, was="A")
        self.assertEqual([(e["name"], e["start_s"]) for e in moved], [("a", 1.5), ("b", 3.0)])
        with self.assertRaises(events.EventError) as cm:
            events.set_event(ev, "b", 1.0, 2.0, was="a")
        self.assertEqual(cm.exception.field, "name", "renaming onto another event's name would lose it")
        with self.assertRaises(events.EventError) as cm:
            events.set_event(ev, "z", 1.0, 2.0, was="nope")
        self.assertEqual(cm.exception.field, "was")

    def test_each_bad_value_names_its_field(self):
        for args, field in ((("", 1.0, 2.0), "name"), (("x" * 41, 1.0, 2.0), "name"), ((None, 1.0, 2.0), "name"), (("a", -0.1, 2.0), "start_s"), (("a", 2.0, 2.0), "end_s"),
                            (("a", 2.0, 1.0), "end_s"), (("a", 1.0, 1.000001), "end_s"), (("a", float("nan"), 2.0), "start_s"), (("a", 1.0, float("inf")), "end_s"),
                            (("a", "1", 2.0), "start_s"), (("a", True, 2.0), "start_s")):
            with self.assertRaises(events.EventError, msg=str(args)) as cm:
                events.set_event([], *args)
            self.assertEqual(cm.exception.field, field, args)

    def test_one_sample_is_the_shortest_event(self):
        ev = events.set_event([], "edge", 1.0, 1.00001)
        self.assertEqual(len(ev), 1)

    def test_a_name_is_trimmed_and_forty_characters_fit(self):
        ev = events.set_event([], "  boot  ", 0.0, 1.0)
        self.assertEqual(ev[0]["name"], "boot")
        events.set_event([], "x" * 40, 0.0, 1.0)

    def test_there_is_a_limit_and_replacing_one_at_the_limit_is_allowed(self):
        ev = []
        for k in range(events.MAX_EVENTS):
            ev = events.set_event(ev, "e%d" % k, float(k), k + 0.5)
        with self.assertRaises(events.EventError):
            events.set_event(ev, "one more", 1000.0, 1001.0)
        self.assertEqual(len(events.set_event(ev, "e5", 5.0, 5.9)), events.MAX_EVENTS)

    def test_remove(self):
        ev = events.set_event(events.set_event([], "a", 1.0, 2.0), "b", 3.0, 4.0)
        self.assertEqual([e["name"] for e in events.remove_event(ev, "A")], ["b"])
        self.assertIsNone(events.remove_event(ev, "nope"))
        self.assertIsNone(events.remove_event([], None))


class TheFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nrfwb-events-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "r.events.json")

    def test_what_is_saved_is_what_is_loaded_and_no_partial_file_is_left(self):
        ev = events.set_event(events.set_event([], "b", 3.0, 4.0), "a", 1.0, 2.0)
        events.save_events(self.path, ev)
        self.assertEqual(events.load_events(self.path), ev)
        self.assertEqual(os.listdir(self.dir), ["r.events.json"])

    def test_a_missing_or_damaged_file_is_no_events(self):
        self.assertEqual(events.load_events(self.path), [])
        for text in ("", "not json", "[]", '{"events": 5}', '{"events": [{"name": "a"}]}', '{"events": [{"name": "a", "start_s": "x", "end_s": 1}]}'):
            with open(self.path, "w") as fh:
                fh.write(text)
            self.assertEqual(events.load_events(self.path), [], text)

    def test_a_file_edited_by_hand_out_of_order_loads_in_order(self):
        with open(self.path, "w") as fh:
            json.dump({"events": [{"name": "b", "start_s": 5, "end_s": 6}, {"name": "a", "start_s": 1, "end_s": 2}]}, fh)
        self.assertEqual([e["name"] for e in events.load_events(self.path)], ["a", "b"])


class ThroughTheSampler(PathBase):
    """The recording the fake unit makes has four wakes; events are named on it."""

    def setUp(self):
        PathBase.setUp(self)
        self.record_session("rak-1")

    def set(self, **args):
        return self.call("meter.event_set", dict({"recording": "rak-1", "name": "boot", "start_s": 1.0, "end_s": 2.0}, **args))

    def test_an_event_is_kept_with_the_recording_and_listed_with_what_it_measured(self):
        res = self.set()
        self.assertTrue(res.ok)
        self.assertEqual(res.data["events"][0]["name"], "boot")
        self.assertTrue(os.path.exists(os.path.join(self.folder, "rak-1.events.json")))
        data = self.call("meter.events", {"recording": "rak-1"}).data
        e = data["events"][0]
        self.assertEqual((e["name"], e["start_s"], e["end_s"]), ("boot", 1.0, 2.0))
        self.assertAlmostEqual(e["duration_s"], 1.0, places=9)
        self.assertAlmostEqual(e["mean"], self.sleep_a, delta=self.sleep_a * 1e-6, msg="1 s to 2 s of the fake unit's recording is asleep")
        self.assertAlmostEqual(e["charge_c"], self.sleep_a * 1.0, delta=self.sleep_a * 1e-6)

    def test_an_event_over_a_wake_measures_its_peak_and_charge_exactly_from_the_samples(self):
        span = self.call("meter.replay", {"recording": "rak-1"}).data["wakes"][0]
        burst0 = span["t0"] + 0.2
        self.set(name="burst", start_s=burst0 + 0.01, end_s=burst0 + 0.09)
        e = self.call("meter.events", {"recording": "rak-1"}).data["events"][0]
        self.assertEqual(e["source"], "wake", "inside a saved wake it is measured from the wake's own samples")
        self.assertAlmostEqual(e["peak"], self.wake_a, delta=self.wake_a * 1e-9)
        self.assertAlmostEqual(e["lowest"], self.wake_a, delta=self.wake_a * 1e-9)
        self.assertAlmostEqual(e["charge_c"], self.wake_a * 0.08, delta=self.wake_a * 0.08 * 1e-3)

    def test_a_long_event_inside_a_wake_is_still_measured_from_the_samples(self):
        span = self.call("meter.replay", {"recording": "rak-1"}).data["wakes"][0]
        self.set(name="most of it", start_s=span["t0"] + 0.1, end_s=span["t0"] + 0.5)          # 0.4 s over the default 1000 points is 0.4 ms a point: the samples answer; 2 points would not need them
        e = self.call("meter.events", {"recording": "rak-1"}).data["events"][0]
        self.assertEqual((e["source"], e["wake"]), ("wake", 1), "an event is measured from the samples whenever it lies wholly inside a saved wake, however wide")
        self.assertAlmostEqual(e["charge_c"], 0.2 * self.wake_a + 0.2 * self.sleep_a, delta=0.2 * self.wake_a * 1e-3)

    def test_an_event_that_runs_past_the_end_says_how_much_of_it_was_measured(self):
        end = self.call("meter.replay", {"recording": "rak-1"}).data["duration_s"]
        self.set(name="past", start_s=end - 1.0, end_s=end + 4.0)
        e = self.call("meter.events", {"recording": "rak-1"}).data["events"][0]
        self.assertAlmostEqual(e["duration_s"], 5.0, places=9)
        self.assertAlmostEqual(e["covered_s"], 1.0, places=6, msg="only the second inside the recording is measured")
        self.assertAlmostEqual(e["mean"], self.sleep_a, delta=self.sleep_a * 1e-6)

    def test_deleting_an_event_that_is_not_there_leaves_the_others_alone(self):
        self.set(name="a", start_s=1.0, end_s=2.0)
        self.set(name="b", start_s=3.0, end_s=4.0)
        self.assertEqual(self.call("meter.event_delete", {"recording": "rak-1", "name": "nope"}).code, "event_unknown")
        self.assertEqual([e["name"] for e in self.call("meter.events", {"recording": "rak-1"}).data["events"]], ["a", "b"])

    def test_an_event_outside_the_saved_wakes_is_measured_from_the_readings_clipped_to_its_edges(self):
        self.set(name="part", start_s=1.05, end_s=1.15)
        e = self.call("meter.events", {"recording": "rak-1"}).data["events"][0]
        self.assertEqual(e["source"], "csv")
        self.assertAlmostEqual(e["charge_c"], self.sleep_a * 0.1, delta=self.sleep_a * 0.1 * 1e-6, msg="0.1 s of a 0.2 s row counts 0.1 s, not 0.2")

    def test_energy_is_there_only_when_the_volts_are_given(self):
        self.set()
        e = self.call("meter.events", {"recording": "rak-1"}).data["events"][0]
        self.assertIsNone(e["energy_j"])
        e = self.call("meter.events", {"recording": "rak-1", "volts": 4.0}).data["events"][0]
        self.assertAlmostEqual(e["energy_j"], e["charge_c"] * 4.0, places=15)

    def test_a_rename_and_a_delete(self):
        self.set()
        res = self.set(name="start up", was="boot")
        self.assertEqual([e["name"] for e in res.data["events"]], ["start up"])
        res = self.call("meter.event_delete", {"recording": "rak-1", "name": "START UP"})
        self.assertEqual(res.data["events"], [])
        res = self.call("meter.event_delete", {"recording": "rak-1", "name": "nope"})
        self.assertEqual((res.cls, res.code), ("refused", "event_unknown"))

    def test_a_bad_value_is_a_usage_error_naming_the_field(self):
        for args, field in (({"name": ""}, "name"), ({"start_s": -1.0}, "start_s"), ({"end_s": 0.5}, "end_s"), ({"was": "nope"}, "was")):
            with self.assertRaises(UsageError, msg=str(args)) as cm:
                self.set(**args)
            self.assertEqual(cm.exception.arg, field, args)

    def test_an_unknown_recording_is_recording_unknown_for_every_event_command(self):
        for name, args in (("meter.events", {"recording": "nope"}), ("meter.event_set", {"recording": "nope", "name": "a", "start_s": 0.0, "end_s": 1.0}),
                           ("meter.event_delete", {"recording": "nope", "name": "a"}), ("meter.events", {"recording": "../x"})):
            self.assertEqual(self.call(name, args).code, "recording_unknown", name)

    def test_events_beyond_the_recording_are_kept_and_measured_where_there_is_data(self):
        self.set(name="past the end", start_s=1000.0, end_s=1001.0)
        e = self.call("meter.events", {"recording": "rak-1"}).data["events"][0]
        self.assertIsNone(e["mean"], "no readings there: nothing is measured, and it is not an error")
        self.assertEqual(e["source"], None)


if __name__ == "__main__":
    unittest.main()
