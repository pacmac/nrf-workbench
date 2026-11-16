#!/usr/bin/env python3
"""Tests for core.status: the ONE mapping from a Result to an exit code and an HTTP status.

The mapping is total, and the closed code list in the code equals the table in core.md in both
directions, and covers every code the package actually emits: a code added in one place only fails here.
Run:  python3 tests/core/test_status.py
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core import status  # noqa: E402
from nrf_workbench.core.result import FAILED, OK, REFUSED, SUSPECT, Result  # noqa: E402


def doc_codes():
    rows = {}
    with open(os.path.join(ROOT, "docs", "specs", "core.md")) as fh:
        for line in fh:
            m = re.match(r"^\| `([a-z_0-9]+)` \| (ok|refused|failed|suspect) \|", line)
            if m:
                rows[m.group(1)] = m.group(2)
    return rows


def emitted_codes():
    found = {}
    pattern = re.compile(r'Result\.(?:refused|failed)\(\s*"([a-z_0-9]+)"|Result\(\s*[A-Z_]+,\s*"([a-z_0-9]+)"', re.S)
    for base, _dirs, files in os.walk(os.path.join(ROOT, "nrf_workbench")):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name)) as fh:
                    for m in pattern.finditer(fh.read()):
                        found.setdefault(m.group(1) or m.group(2), os.path.join(base, name))
    return found


def result_for(code):
    return Result(status.CODES[code], code, "x")


class Closed(unittest.TestCase):
    def test_the_code_list_equals_the_table_in_core_md_both_ways(self):
        doc = doc_codes()
        self.assertGreater(len(doc), 30, "the table was not parsed")
        self.assertEqual(sorted(set(doc) - set(status.CODES)), [], "in core.md but not in status.CODES")
        self.assertEqual(sorted(set(status.CODES) - set(doc) - {"ok"}), [], "in status.CODES but not in core.md")

    def test_each_codes_class_agrees_with_core_md(self):
        for code, cls in doc_codes().items():
            self.assertEqual(status.CODES[code], cls, code)

    def test_every_code_the_package_emits_is_in_the_closed_list(self):
        emitted = emitted_codes()
        self.assertGreater(len(emitted), 20, "no codes were found in the source")
        for code, where in sorted(emitted.items()):
            self.assertIn(code, status.CODES, "%s is emitted in %s but is not in the closed list" % (code, where))

    def test_the_cli_only_codes_are_real_refusals(self):
        for code in status.CLI_ONLY:
            self.assertEqual(status.CODES[code], REFUSED, code)


class Mapping(unittest.TestCase):
    def test_exit_codes_come_from_the_class(self):
        self.assertEqual([status.exit_code(Result(c, "x" if c != OK else "ok")) for c in (OK, REFUSED, FAILED, SUSPECT)],
                         [0, 1, 3, 4])

    def test_http_for_each_kind(self):
        self.assertEqual(status.http_status(Result.success()), 200)
        self.assertEqual(status.http_status(Result.refused("lock_held", "x")), 409)
        self.assertEqual(status.http_status(Result.refused("protected", "x")), 409)
        self.assertEqual(status.http_status(Result.refused("in_use", "x")), 409)
        self.assertEqual(status.http_status(Result.refused("link_rx_only", "x")), 409)
        for code in ("bad_package", "wrong_softdevice", "no_bound", "baud_refused", "not_a_declared_link",
                     "checksum_mismatch"):
            self.assertEqual(status.http_status(Result.refused(code, "x")), 422, code)
        self.assertEqual(status.http_status(Result.refused("firmware_unknown", "x")), 404)
        self.assertEqual(status.http_status(Result.refused("length_required", "x")), 411)
        self.assertEqual(status.http_status(Result.refused("store_full", "x")), 507)
        self.assertEqual(status.http_status(Result.failed("flash_failed", "x")), 500)
        self.assertEqual(status.http_status(Result(SUSPECT, "flash_suspect", "x")), 500)

    def test_cli_only_codes_have_no_http_status(self):
        for code in status.CLI_ONLY:
            self.assertIsNone(status.http_status(Result.refused(code, "x")), code)

    def test_the_mapping_is_total(self):
        for code in status.CODES:
            result = result_for(code)
            self.assertIn(status.exit_code(result), (0, 1, 3, 4), code)
            http = status.http_status(result)
            if code in status.CLI_ONLY:
                self.assertIsNone(http, code)
            else:
                self.assertIsInstance(http, int, code)
                self.assertIn(http // 100, (2, 4, 5), code)

    def test_usage_errors_are_not_results(self):
        self.assertEqual((status.USAGE_EXIT, status.USAGE_HTTP), (2, 400))


if __name__ == "__main__":
    unittest.main(verbosity=1)
