#!/usr/bin/env python3
"""A kept wake reports its awake period apart from its capture length (docs/specs/power-workbench.md, "Commands", `meter.wake_table`, first slice).

A capture is the lead-in block, the awake blocks and one quiet block as its tail, so its length is the awake period plus 0.4 s. `awake_s` is the first block over the awake level to the last;
`capture_s` is the whole capture. Both are in `meter.captures`, and a wake kept on disk says them again when it is read back.
Run:  python3 tests/instruments/test_awake_period.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.instruments import capture  # noqa: E402
from test_capture import PathBase, WAKE_ADC, feed_blocks, given, hot, quiet  # noqa: E402

BLOCK = 0.2


class OnTheCapturer(unittest.TestCase):
    def kept(self, plan, **kw):
        c = capture.Capturer(given(**kw))
        feed_blocks(c, plan)
        return c.items

    def test_a_wake_of_known_length_reports_it_and_the_capture_is_longer_by_the_lead_in_and_tail(self):
        (w,) = self.kept(quiet(5) + hot(7) + quiet(5))
        self.assertEqual((w.lead_blocks, w.tail_blocks, w.blocks), (1, 1, 9))
        self.assertAlmostEqual(w.awake_s, 7 * BLOCK, places=9)
        self.assertAlmostEqual(w.capture_s, 9 * BLOCK, places=9)

    def test_a_dip_shorter_than_the_hold_off_is_inside_the_awake_period(self):
        (w,) = self.kept(quiet(5) + hot(3) + quiet(1) + hot(3) + quiet(5), hold_s=0.4)
        self.assertAlmostEqual(w.awake_s, 7 * BLOCK, places=9, msg="3 awake, 1 quiet inside it, 3 awake")

    def test_a_wake_cut_at_the_longest_wake_has_no_tail(self):
        (w, *_) = self.kept(quiet(5) + hot(30), max_s=1.0)
        self.assertTrue(w.truncated)
        self.assertEqual((w.lead_blocks, w.tail_blocks), (1, 0))
        self.assertAlmostEqual(w.awake_s, 4 * BLOCK, places=9)

    def test_a_wake_with_no_lead_in_has_none_to_take_off(self):
        (w,) = self.kept(hot(3) + quiet(5))
        self.assertEqual((w.lead_blocks, w.tail_blocks), (0, 1))
        self.assertAlmostEqual(w.awake_s, 3 * BLOCK, places=9)


class ThroughTheSession(PathBase):
    def test_meter_captures_says_the_awake_period_and_the_capture_length(self):
        self.BLOCKS = 30
        awake = dict((b, WAKE_ADC) for b in range(10, 17))
        self.unit.profile = lambda i: (awake.get(i // 20000, 1000) if i < 30 * 20000 else 1000, 0)
        self.record_session("known")
        (row,) = self.call("meter.captures", {"recording": "known"}).data["captures"]
        self.assertAlmostEqual(row["awake_s"], 7 * BLOCK, places=6)
        self.assertAlmostEqual(row["capture_s"], 9 * BLOCK, places=6)
        self.assertAlmostEqual(row["duration_s"], row["capture_s"], places=6, msg="duration_s stays: it is the capture's length")

    def test_a_wake_read_back_from_disk_says_it_again(self):
        self.BLOCKS = 30
        awake = dict((b, WAKE_ADC) for b in range(10, 17))
        self.unit.profile = lambda i: (awake.get(i // 20000, 1000) if i < 30 * 20000 else 1000, 0)
        self.record_session("known")
        live = self.call("meter.captures", {"recording": "known"}).data["captures"][0]
        again = self.call("meter.captures", {"recording": "known"}).data["captures"][0]       # from the recording's folder, not the session's memory
        self.assertEqual((again["awake_s"], again["capture_s"]), (live["awake_s"], live["capture_s"]))
        self.assertAlmostEqual(again["awake_s"], 1.4, places=6)


if __name__ == "__main__":
    unittest.main()
