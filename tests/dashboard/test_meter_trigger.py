#!/usr/bin/env python3
"""The trigger fields as typed, and the wake_* arguments they become (ui/src/meter/trigger.js, docs/specs/ppk2-stages.md "The wake profile") under Node. Run:  python3 tests/dashboard/test_meter_trigger.py"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
TRIGGER = os.path.join(ROOT, "ui", "src", "meter", "trigger.js")
NODE = shutil.which("node")


def run(script, data=None):
    code = 'import * as t from %s;\nconst data = %s;\nconst out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + TRIGGER), json.dumps(data), script)
    p = subprocess.run([NODE, "--input-type=module", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


def args(**fields):
    base = {"awake": "", "sleep": "", "hold": "", "lead": "", "max": ""}
    base.update(fields)
    return run("return t.triggerArgs(data);", base)


@unittest.skipUnless(NODE, "node is not installed")
class TheFields(unittest.TestCase):
    def test_blank_fields_are_left_out_so_the_data_decides(self):
        self.assertEqual(args(), {"args": {}, "problems": []})
        self.assertEqual(args(awake="  ", hold="\t"), {"args": {}, "problems": []})

    def test_levels_typed_in_microamps_are_sent_in_amps_and_times_as_they_are(self):
        got = args(awake="68", sleep="34", hold="0.6", lead="1", max="30")["args"]
        self.assertAlmostEqual(got["wake_awake_a"], 68e-6, places=15)
        self.assertAlmostEqual(got["wake_sleep_a"], 34e-6, places=15)
        self.assertEqual((got["wake_hold_s"], got["wake_lead_s"], got["wake_max_s"]), (0.6, 1, 30))

    def test_zero_is_a_value_not_a_blank(self):
        self.assertEqual(args(lead="0")["args"], {"wake_lead_s": 0}, "no lead-in is a choice: it must reach the server")

    def test_a_field_that_is_not_a_number_is_named_and_not_sent(self):
        got = args(awake="lots", hold="1", max="1e")
        self.assertEqual(got["args"], {"wake_hold_s": 1})
        self.assertEqual(got["problems"], ["Awake above is not a number", "Longest wake is not a number"])

    def test_one_field_changed_is_a_change_and_none_is_not(self):
        self.assertFalse(run("return t.isChanged(t.EMPTY_TRIGGER);"))
        self.assertTrue(run("return t.isChanged({...t.EMPTY_TRIGGER, max: '40'});"))
        self.assertFalse(run("return t.isChanged({...t.EMPTY_TRIGGER, max: '  '});"))


if __name__ == "__main__":
    unittest.main()
