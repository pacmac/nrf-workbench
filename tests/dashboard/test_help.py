#!/usr/bin/env python3
"""Tests for the Help topics (ui/src/help.js; docs/specs/dashboard-redesign.md, "Help").

The module is run under Node, so these check the real data. What must hold: every page of the dashboard has a topic, every `related` link
resolves, every refusal code a topic names is a real code of the service (so the help cannot drift from it), and no topic is longer than a
phone screen can hold (a bounded number of points, steps and problems, and a short summary), because the rule is short and structured
help, not text everywhere.
Run:  python3 tests/dashboard/test_help.py
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core import status  # noqa: E402

MODULE = os.path.join(ROOT, "ui", "src", "help.js")
NODE = shutil.which("node")
# the pages the nav offers, apart from Help itself
PAGES = ["home", "devices", "wiring", "console", "firmware", "gpio", "activity", "commands"]
# One phone screen at 15 px with the structure around it: a summary of two lines and at most this many of each block.
MAX_SUMMARY, MAX_POINTS, MAX_STEPS, MAX_PROBLEMS, MAX_CODES = 160, 6, 6, 6, 6


def run(script):
    code = 'import * as m from %s;\nconst out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + MODULE), script)
    p = subprocess.run([NODE, "--input-type=module", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=30)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class Topics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.topics = run("return m.TOPICS;")
        cls.by = dict((t["id"], t) for t in cls.topics)

    def test_the_ids_are_unique_and_the_first_is_start_here(self):
        ids = [t["id"] for t in self.topics]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "start")

    def test_every_page_has_a_topic_and_the_page_opens_it(self):
        for page in PAGES:
            owners = [t["id"] for t in self.topics if page in t["pages"]]
            self.assertEqual(len(owners), 1, "%s must be explained by exactly one topic, found %s" % (page, owners))
            self.assertEqual(run("return m.topicForPage(%s);" % json.dumps(page)), owners[0])
        self.assertEqual(run("return m.topicForPage('nope');"), "start")

    def test_a_topic_names_no_page_the_dashboard_does_not_have(self):
        for t in self.topics:
            for page in t["pages"]:
                self.assertIn(page, PAGES, t["id"])

    def test_every_related_link_resolves_and_none_points_at_itself(self):
        for t in self.topics:
            for rel in t.get("related", []):
                self.assertIn(rel, self.by, "%s -> %s" % (t["id"], rel))
                self.assertNotEqual(rel, t["id"])

    def test_every_refusal_code_a_topic_names_is_a_real_code_with_a_fix(self):
        for t in self.topics:
            for c in t.get("codes", []):
                self.assertIn(c["code"], status.CODES, "%s names %s, which the service does not have" % (t["id"], c["code"]))
                self.assertTrue(c["fix"].strip(), c["code"])
                self.assertLess(len(c["fix"]), 140, c["code"])

    def test_a_code_is_explained_in_one_place_only(self):
        seen = {}
        for t in self.topics:
            for c in t.get("codes", []):
                self.assertNotIn(c["code"], seen, "%s is explained in both %s and %s" % (c["code"], seen.get(c["code"]), t["id"]))
                seen[c["code"]] = t["id"]

    def test_every_topic_is_short_and_structured(self):
        for t in self.topics:
            self.assertTrue(t["title"] and t["summary"], t["id"])
            self.assertLessEqual(len(t["summary"]), MAX_SUMMARY, t["id"])
            self.assertLessEqual(len(t.get("points", [])), MAX_POINTS, t["id"])
            self.assertLessEqual(len(t.get("steps", [])), MAX_STEPS, t["id"])
            self.assertLessEqual(len(t.get("problems", [])), MAX_PROBLEMS, t["id"])
            self.assertLessEqual(len(t.get("codes", [])), MAX_CODES, t["id"])
            self.assertTrue(t.get("steps") or t.get("points") or t.get("problems"), "%s has nothing to do or read" % t["id"])

    def test_every_problem_says_what_it_is_why_and_what_to_do(self):
        for t in self.topics:
            for p in t.get("problems", []):
                for key in ("symptom", "cause", "fix"):
                    self.assertTrue(p[key].strip(), "%s: %s" % (t["id"], p))

    def test_a_legend_uses_only_states_the_page_draws(self):
        for t in self.topics:
            for l in t.get("legend", []):
                self.assertIn(l["state"], ("connected", "listening", "absent", "attention", "fault"), t["id"])

    def test_search_finds_a_topic_by_its_words_and_by_a_code(self):
        self.assertIn("flashing", run("return m.TOPICS.filter((t) => m.topicText(t).includes('wrong_softdevice')).map((t) => t.id);"))
        self.assertIn("pins", run("return m.TOPICS.filter((t) => m.topicText(t).includes('i2c')).map((t) => t.id);"))
        self.assertEqual(run("return m.TOPICS.filter((t) => m.topicText(t).includes('zzzqq')).length;"), 0)

    def test_the_help_page_is_the_only_page_with_paragraphs_of_explanation(self):
        """The rule is 'no prose on a working page'; this keeps the biggest offenders from coming back."""
        src = os.path.join(ROOT, "ui", "src")
        banned = ["Built so far", "The Pi's 40-pin header as it is on the board", "Every command this service has on this host",
                  "Connections that use Pi pins", "A macro is a name and some lines", "Declare the adapter that is wired",
                  "in the Attached box at the top"]
        for name in sorted(os.listdir(src)):
            if name.endswith((".jsx", ".js")) and name != "help.js":
                text = open(os.path.join(src, name)).read()
                for phrase in banned:
                    self.assertNotIn(phrase, text, "%s still carries explanatory text: %s" % (name, phrase))


if __name__ == "__main__":
    unittest.main()
