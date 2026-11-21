#!/usr/bin/env python3
"""The console's lines are kept on disk, bounded, and read back by host time, across a restart (docs/specs/power-workbench.md, "Console persistence").

`LineLog` is the store (nothing is held in memory but the counters); `console.history` is the command over it. The bound has no default:
with `console_log.max_mb` unset the log is absent and the command says which setting is missing.
Run:  python3 tests/console/test_history.py
"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.console.history import LineLog  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.events import Bus  # noqa: E402
from nrf_workbench.core.registry import UsageError  # noqa: E402
from test_console_commands import Base, Rig  # noqa: E402

T0 = 1795300000.0                      # a fixed host time: 2026-11-21 22:26:40 UTC (`date -u -d @1795300000`)
MB = 1024 * 1024


def fill(log, link="dbg", n=10, start=T0, step=1.0, text="line %d"):
    for i in range(n):
        log.append(link, start + i * step, i * 10, text % i if "%d" in text else text)


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def log(self, max_bytes=1 * MB):
        return LineLog(self.dir, max_bytes)

    def files(self, link="dbg"):
        folder = os.path.join(self.dir, link)
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def size(self, link="dbg"):
        return sum(os.path.getsize(os.path.join(self.dir, link, f)) for f in self.files(link))


class WhatIsKept(Folder):
    def test_a_line_is_on_disk_as_it_arrives_and_a_new_log_reads_it_back(self):
        a = self.log()
        fill(a, n=5)
        b = self.log()                                              # the restart: nothing but the folder is shared
        got = b.between("dbg", T0, T0 + 100)
        self.assertEqual([(l["wall"], l["offset"], l["text"]) for l in got["lines"]], [(T0 + i, i * 10, "line %d" % i) for i in range(5)])

    def test_the_layout_is_a_folder_per_link_and_a_utc_named_file_of_json_lines(self):
        fill(self.log(), n=2)
        self.assertEqual(self.files(), ["20261121-222640.jsonl"])
        with open(os.path.join(self.dir, "dbg", self.files()[0])) as fh:
            rows = [json.loads(x) for x in fh]
        self.assertEqual(rows[0], {"wall": T0, "offset": 0, "text": "line 0"})

    def test_a_line_the_broker_cut_says_so(self):
        log = self.log()
        log.append("dbg", T0, 0, "x" * 1024, partial=True)
        log.append("dbg", T0 + 1, 1024, "whole")
        got = log.between("dbg", T0, T0 + 5)["lines"]
        self.assertEqual([l.get("partial", False) for l in got], [True, False])

    def test_a_long_line_with_non_ascii_text_is_kept_whole(self):
        log = self.log()
        text = "SLEEPNOW awake=21600 µs " + "é" * 400
        log.append("dbg", T0, 0, text)
        self.assertEqual(self.log().between("dbg", T0, T0 + 1)["lines"][0]["text"], text)

    def test_links_do_not_mix(self):
        log = self.log()
        fill(log, "dbg", 3)
        fill(log, "other", 2, text="other %d")
        self.assertEqual([l["text"] for l in log.between("other", T0, T0 + 9)["lines"]], ["other 0", "other 1"])
        self.assertEqual(len(log.between("dbg", T0, T0 + 9)["lines"]), 3)

    def test_a_link_name_that_is_a_path_is_refused_and_writes_nothing(self):
        log = self.log()
        for bad in ("../x", "a/b", ".hidden", "", "x" * 65):
            with self.assertRaises(ValueError):
                log.append(bad, T0, 0, "no")
        self.assertEqual(os.listdir(self.dir), [])


class WhatIsRead(Folder):
    def test_the_range_is_from_inclusive_to_exclusive_and_in_order(self):
        log = self.log()
        fill(log, n=10)
        got = log.between("dbg", T0 + 3, T0 + 6)["lines"]
        self.assertEqual([l["text"] for l in got], ["line 3", "line 4", "line 5"])

    def test_an_empty_range_and_an_unknown_link_are_empty_not_errors(self):
        log = self.log()
        fill(log, n=3)
        self.assertEqual(log.between("dbg", T0 + 50, T0 + 60)["lines"], [])
        self.assertEqual(log.between("never", T0, T0 + 60), {"lines": [], "more": False, "next_from_wall": None, "kept_from": None})

    def test_a_range_over_several_segments_is_whole_and_ordered(self):
        log = self.log(max_bytes=4000)                              # a segment rolls at 1000 bytes: about ten lines
        fill(log, n=25, text="a line of text %d")
        got = log.between("dbg", T0 + 2, T0 + 24)["lines"]
        self.assertGreater(len(self.files()), 1, "the test must span segments")
        self.assertEqual([l["wall"] for l in got], [T0 + i for i in range(2, 24)], "none missing, none twice, in order")

    def test_a_cut_range_says_there_is_more_and_where_to_carry_on(self):
        log = self.log()
        fill(log, n=10)
        page = log.between("dbg", T0, T0 + 100, limit=4)
        self.assertEqual([l["text"] for l in page["lines"]], ["line 0", "line 1", "line 2", "line 3"])
        self.assertEqual((page["more"], page["next_from_wall"]), (True, T0 + 4))
        rest = log.between("dbg", page["next_from_wall"], T0 + 100, limit=100)
        self.assertEqual((len(rest["lines"]), rest["more"], rest["next_from_wall"]), (6, False, None))

    def test_a_line_torn_by_a_crash_is_skipped_and_the_rest_are_read(self):
        fill(self.log(), n=3)
        path = os.path.join(self.dir, "dbg", self.files()[0])
        with open(path, "a") as fh:
            fh.write('{"wall": 1795300003.0, "offs')                 # the process died mid-write
        got = self.log().between("dbg", T0, T0 + 100)["lines"]
        self.assertEqual([l["text"] for l in got], ["line 0", "line 1", "line 2"])

    def test_after_a_torn_line_the_next_append_starts_a_fresh_line(self):
        fill(self.log(), n=2)
        path = os.path.join(self.dir, "dbg", self.files()[0])
        with open(path, "a") as fh:
            fh.write('{"wall": 17953')
        again = self.log()
        again.append("dbg", T0 + 0.5, 99, "after the restart")        # the same second as the torn file's name, so the same file
        texts = [l["text"] for l in again.between("dbg", T0, T0 + 100)["lines"]]
        self.assertEqual(texts, ["line 0", "line 1", "after the restart"])


class TheBound(Folder):
    def test_the_folder_never_exceeds_the_bound_by_more_than_the_line_being_written(self):
        bound = 4000
        log = self.log(max_bytes=bound)
        longest = 0
        for i in range(400):
            text = "a line of console text, number %d" % i
            log.append("dbg", T0 + i, i, text)
            longest = max(longest, len(json.dumps({"wall": T0 + i, "offset": i, "text": text})) + 1)
            self.assertLessEqual(self.size(), bound + longest, "after line %d" % i)

    def test_the_oldest_goes_first_and_the_newest_line_is_always_kept(self):
        log = self.log(max_bytes=4000)
        fill(log, n=400, text="a line of console text, number %d")
        got = log.between("dbg", T0, T0 + 1000, limit=100000)
        self.assertEqual(got["lines"][-1]["text"], "a line of console text, number 399")
        self.assertGreater(got["lines"][0]["wall"], T0, "the first lines were pruned")
        self.assertEqual(got["kept_from"], got["lines"][0]["wall"])
        self.assertGreater(len(got["lines"]), 20, "most of the bound is still history, not one segment")

    def test_the_line_just_written_is_kept_even_when_the_host_clock_went_back(self):
        log = self.log(max_bytes=4000)
        fill(log, n=60, start=T0 + 5000, text="a line of console text, number %d")
        back = self.log(max_bytes=2000)                             # a restart after the clock was stepped back (its segment sorts first), with less room than is on disk
        back.append("dbg", T0, 0, "written on the earlier clock")
        self.assertEqual(back.between("dbg", T0, T0 + 1)["lines"][0]["text"], "written on the earlier clock")

    def test_a_file_rolls_at_a_quarter_of_the_bound(self):
        log = self.log(max_bytes=4000)
        fill(log, n=60, text="a line of console text, number %d")
        sizes = [os.path.getsize(os.path.join(self.dir, "dbg", f)) for f in self.files()]
        self.assertGreater(len(sizes), 2)
        for s in sizes[:-1]:
            self.assertTrue(1000 <= s < 1000 + 100, sizes)          # rolled as soon as it reached 1000 bytes: over by at most one line

    def test_the_bound_is_per_link(self):
        log = self.log(max_bytes=4000)
        fill(log, "a", 300, text="a line of console text, number %d")
        fill(log, "b", 3)
        self.assertEqual(len(log.between("b", T0, T0 + 100)["lines"]), 3, "a busy link does not prune a quiet one")

    def test_a_bound_of_nothing_is_refused(self):
        for bad in (0, -1, None, True, "4"):
            with self.assertRaises(ValueError):
                LineLog(self.dir, bad)


class AFailingWriteDoesNotStopTheConsole(Folder):
    def test_a_write_that_fails_is_counted_and_never_raised(self):
        blocker = os.path.join(self.dir, "not-a-folder")
        with open(blocker, "w") as fh:
            fh.write("x")
        log = LineLog(blocker, 1 * MB)                              # a file where the folder should be
        log.append("dbg", T0, 0, "lost")
        st = log.status()
        self.assertEqual((st["written"], st["write_errors"]), (0, 1))
        self.assertTrue(st["last_error"])

    def test_the_counters_say_what_was_written(self):
        log = self.log()
        fill(log, n=7)
        self.assertEqual(log.status(), {"written": 7, "write_errors": 0, "last_error": None})


class FromTheBus(Folder):
    def test_a_console_line_event_is_written_with_the_wall_the_bus_gave_it(self):
        bus = Bus(100, 3600, wall=lambda: T0 + 5)
        log = self.log()
        log.attach(bus)
        bus.publish("console.line", "console", {"link": "dbg", "offset": 42, "bytes": 6, "text": "hello", "reply": False})
        bus.publish("console.link", "console", {"link": "dbg", "state": "open"})
        got = log.between("dbg", T0, T0 + 100)["lines"]
        self.assertEqual([(l["wall"], l["offset"], l["text"]) for l in got], [(T0 + 5, 42, "hello")])
        self.assertEqual(log.status(), {"written": 1, "write_errors": 0, "last_error": None}, "an event that is not a line is not an error")

    def test_a_cut_line_on_the_bus_is_kept_as_cut(self):
        bus = Bus(100, 3600, wall=lambda: T0)
        log = self.log()
        log.attach(bus)
        bus.publish("console.line", "console", {"link": "dbg", "offset": 0, "bytes": 1024, "text": "x" * 1024, "reply": False, "partial": True})
        self.assertTrue(log.between("dbg", T0 - 1, T0 + 1)["lines"][0]["partial"])

    def test_a_failing_log_does_not_become_a_fault_of_the_bus(self):
        blocker = os.path.join(self.dir, "not-a-folder")
        with open(blocker, "w") as fh:
            fh.write("x")
        bus = Bus(100, 3600)
        LineLog(blocker, 1 * MB).attach(bus)
        bus.publish("console.line", "console", {"link": "dbg", "offset": 0, "bytes": 2, "text": "a", "reply": False})
        self.assertEqual(list(bus.faults), [])


class TheSettingHasNoDefault(unittest.TestCase):
    def test_console_log_max_mb_is_unset_by_default_and_must_be_a_positive_number(self):
        cfg, problems = cfgmod.load(env={})
        self.assertEqual((problems, cfg["console_log"]), ([], {"max_mb": None}))
        for bad in (0, -5, True, "big", float("inf")):
            _, problems = cfgmod.load(env={}, overrides={"console_log": {"max_mb": bad}})
            self.assertTrue(any("console_log.max_mb" in p for p in problems), (bad, problems))
        _, problems = cfgmod.load(env={}, overrides={"console_log": {"max_mb": 5}})
        self.assertEqual(problems, [])


class ThroughTheService(Base):
    def lines(self, rig, n):
        for i in range(n):
            rig.say(("boot line %d\n" % i).encode())
        rig.wait_lines(n)

    def test_without_a_bound_the_command_says_which_setting_is_missing(self):
        r = self.rig()
        self.assertIsNone(r.host.console_log)
        res = r.call("console.history", {"link": "dbg", "from_wall": 0, "to_wall": time.time() + 10})
        self.assertEqual(res.code, "no_bound")
        self.assertIn("console_log.max_mb", res.message)
        self.assertIn("console_log.max_mb", r.host.streams.absent["console_log"])

    def test_lines_the_broker_read_are_returned_by_history(self):
        r = self.rig(log_mb=5)
        t0 = time.time() - 1
        self.lines(r, 3)
        res = r.call("console.history", {"link": "dbg", "from_wall": t0, "to_wall": time.time() + 10})
        self.assertTrue(res.ok, getattr(res, "message", res))
        self.assertEqual([l["text"] for l in res.data["lines"]], ["boot line 0", "boot line 1", "boot line 2"])
        self.assertFalse(res.data["more"])
        self.assertEqual(res.data["write_errors"], 0)

    def test_lines_received_before_a_restart_are_returned_after_it(self):
        t0 = time.time() - 1
        first = Rig(self.tmp.name, log_mb=5)
        try:
            self.lines(first, 4)
        finally:
            first.close()
        second = self.rig(log_mb=5)                                 # a new service over the same folder: the memory is empty
        self.assertEqual(second.host.streams.console.since(0, None, types=("console.line",)).events, [])
        res = second.call("console.history", {"link": "dbg", "from_wall": t0, "to_wall": time.time() + 10})
        self.assertEqual([l["text"] for l in res.data["lines"]], ["boot line %d" % i for i in range(4)])

    def test_a_range_that_ends_before_it_starts_is_a_usage_error_naming_to_wall(self):
        r = self.rig(log_mb=5)
        with self.assertRaises(UsageError) as cm:
            r.call("console.history", {"link": "dbg", "from_wall": 100, "to_wall": 50})
        self.assertEqual(cm.exception.arg, "to_wall")
        with self.assertRaises(UsageError, msg="an empty range is a mistake too"):
            r.call("console.history", {"link": "dbg", "from_wall": 100, "to_wall": 100})

    def test_an_undeclared_link_is_refused_by_name(self):
        r = self.rig(log_mb=5)
        res = r.call("console.history", {"link": "nope", "from_wall": 0, "to_wall": 1})
        self.assertEqual(res.code, "not_a_declared_link")

    def test_a_range_over_the_live_feeds_own_ceiling_is_cut_at_it(self):
        r = self.rig(log_mb=5)
        retain = r.cfg["streams"]["console"]["retain"]
        self.assertEqual(retain, 1000)
        r.host.console_log.append("dbg", T0, 0, "first")
        for i in range(1, retain + 5):
            r.host.console_log.append("dbg", T0 + i, i, "x")
        res = r.call("console.history", {"link": "dbg", "from_wall": T0, "to_wall": T0 + 100000})
        self.assertEqual((len(res.data["lines"]), res.data["more"], res.data["next_from_wall"]), (retain, True, T0 + retain))


if __name__ == "__main__":
    unittest.main()
