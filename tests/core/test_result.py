#!/usr/bin/env python3
"""Tests for the Result shape (docs/specs/core.md). Run:  python3 tests/core/test_result.py"""
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core.result import EXIT, FAILED, OK, REFUSED, SUSPECT, Result  # noqa: E402


class ResultShape(unittest.TestCase):
    def test_classes_and_exit_codes_are_the_specified_ones(self):
        self.assertEqual(EXIT, {OK: 0, REFUSED: 1, FAILED: 3, SUSPECT: 4})
        self.assertEqual(Result.success().exit_code(), 0)
        self.assertEqual(Result.refused("protected", "x").exit_code(), 1)
        self.assertEqual(Result.failed("flash_failed", "x").exit_code(), 3)

    def test_a_refusal_is_not_ok_and_carries_its_evidence(self):
        r = Result.refused("port_held", "held", {"holders": [{"pid": 7}]})
        self.assertFalse(r.ok)
        self.assertEqual(r.to_dict()["evidence"]["holders"][0]["pid"], 7)
        self.assertEqual(r.to_dict()["class"], "refused")

    def test_json_round_trips(self):
        d = json.loads(Result.success({"a": 1}, "done").to_json())
        self.assertEqual((d["ok"], d["code"], d["data"]), (True, "ok", {"a": 1}))

    def test_an_unknown_class_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            Result("maybe", "x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
