#!/usr/bin/env python3
"""Tests for core.activity: the record of what every caller did to the bench.

What matters: a refusal is recorded exactly like a success (and so is a bad argument), secrets are never
recorded, an entry is a summary and not the whole tool output, and the log's own bounds are required.
Run:  python3 tests/core/test_activity.py
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core.activity import REDACTED, ActivityLog, redact  # noqa: E402
from nrf_workbench.core.events import BoundError, Bus  # noqa: E402
from nrf_workbench.core.result import SUSPECT, Result  # noqa: E402


class Recording(unittest.TestCase):
    def setUp(self):
        self.log = ActivityLog(100, 3600)

    def entries(self):
        return [e.payload for e in self.log.since(0).events]

    def test_a_success_is_recorded_with_who_what_class_code_and_duration(self):
        self.log.record("mcp", "programmer.flash", {"serial": "S", "bound_s": 12.0}, Result.success({"x": 1}, "done"), 4.65)
        (p,) = self.entries()
        self.assertEqual((p["caller"], p["command"], p["args"], p["ok"], p["class"], p["code"], p["message"], p["duration_s"]),
                         ("mcp", "programmer.flash", {"serial": "S", "bound_s": 12.0}, True, "ok", "ok", "done", 4.65))

    def test_a_refusal_is_recorded_exactly_like_a_success(self):
        self.log.record("cli", "programmer.flash", {}, Result.refused("lock_held", "another instance"), 0.01)
        (p,) = self.entries()
        self.assertEqual((p["ok"], p["class"], p["code"], p["message"]), (False, "refused", "lock_held", "another instance"))

    def test_failed_and_suspect_are_recorded_with_their_classes(self):
        self.log.record("api", "c", {}, Result.failed("flash_failed", "x"), 1)
        self.log.record("api", "c", {}, Result(SUSPECT, "flash_suspect", "y"), 1)
        self.assertEqual([(p["class"], p["code"]) for p in self.entries()], [("failed", "flash_failed"), ("suspect", "flash_suspect")])

    def test_a_bad_argument_is_recorded_as_usage_because_it_is_still_something_a_caller_did(self):
        self.log.record_usage("dashboard", "programmer.flash", {"serial": "S"}, "needs the argument firmware", 0.0)
        (p,) = self.entries()
        self.assertEqual((p["class"], p["code"], p["ok"], p["caller"], p["message"]),
                         ("usage", "usage", False, "dashboard", "needs the argument firmware"))

    def test_an_entry_is_a_summary_never_the_whole_result(self):
        huge = "line of tool output\n" * 5000
        self.log.record("api", "programmer.flash", {}, Result.failed("flash_failed", "m", {"output": huge}, {"d": 1}), 1)
        (p,) = self.entries()
        self.assertNotIn("evidence", p)
        self.assertNotIn("data", p)
        self.assertLess(len(json.dumps(p)), 1000)

    def test_entries_are_in_order_and_the_page_is_json(self):
        for i in range(3):
            self.log.record("api", "c%d" % i, {}, Result.success(), 0)
        self.assertEqual([p["command"] for p in self.entries()], ["c0", "c1", "c2"])
        json.dumps(self.log.since(0).to_dict())

    def test_only_command_results_come_back_from_since(self):
        self.log.bus.publish("devices.changed", "devices", {})
        self.log.record("api", "c", {}, Result.success(), 0)
        self.assertEqual([e.type for e in self.log.since(0).events], ["command.result"])

    def test_the_epoch_is_honoured(self):
        self.log.record("api", "c", {}, Result.success(), 0)
        page = self.log.since(1, epoch="another")
        self.assertTrue(page.reset)


class Redaction(unittest.TestCase):
    def test_a_secret_named_argument_is_never_recorded(self):
        args = {"token": "abc", "API_KEY": "k", "Authorization": "Bearer x", "password": "p", "token_file": "/f",
                "client_secret": "s", "my_credential": "c", "serial": "S", "bound_s": 5.0}
        out = redact(args)
        for name in ("token", "API_KEY", "Authorization", "password", "token_file", "client_secret", "my_credential"):
            self.assertEqual(out[name], REDACTED, name)
        self.assertEqual((out["serial"], out["bound_s"]), ("S", 5.0))

    def test_the_original_is_not_changed(self):
        args = {"token": "abc"}
        redact(args)
        self.assertEqual(args, {"token": "abc"})

    def test_no_secret_value_survives_into_the_log(self):
        log = ActivityLog(10, 100)
        log.record("api", "c", {"token": "SECRET-VALUE", "serial": "S"}, Result.success(), 0)
        log.record_usage("api", "c", {"password": "SECRET-VALUE"}, "bad", 0)
        self.assertNotIn("SECRET-VALUE", json.dumps(log.since(0).to_dict()))

    def test_none_is_no_arguments(self):
        self.assertEqual(redact(None), {})


class Bounds(unittest.TestCase):
    def test_the_log_refuses_to_exist_without_both_bounds(self):
        for args in ((None, 10), (10, None), (0, 10), (10, 0), (True, 10)):
            with self.assertRaises(BoundError, msg=args):
                ActivityLog(*args)
        with self.assertRaises(TypeError):
            ActivityLog()

    def test_the_bounds_are_honoured(self):
        log = ActivityLog(3, 3600)
        for i in range(5):
            log.record("api", "c%d" % i, {}, Result.success(), 0)
        page = log.since(0)
        self.assertEqual(([e.payload["command"] for e in page.events], page.lost), (["c2", "c3", "c4"], 2))

    def test_it_is_its_own_stream_with_its_own_cursors(self):
        other = Bus(10, 100)
        other.publish("devices.changed", "devices")
        other.publish("devices.changed", "devices")
        log = ActivityLog(10, 100)
        self.assertEqual(log.record("api", "c", {}, Result.success(), 0).cursor, 1)

    def test_the_clock_can_be_injected_for_age(self):
        t = [0.0]
        log = ActivityLog(10, 5, mono=lambda: t[0], wall=lambda: 1.0)
        log.record("api", "c", {}, Result.success(), 0)
        t[0] = 6
        self.assertEqual(log.since(0).events, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
