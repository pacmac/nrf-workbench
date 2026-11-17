#!/usr/bin/env python3
"""Tests for ui/src/consoleLog.js, the Console page's pure logic (docs/specs/dashboard.md, "The Console page").

The real module is run under Node, so these are not a copy of the logic in Python. They are skipped where Node is
absent (the built UI is committed, so the Pi never needs it). The page itself is proved in a real browser by
scripts/verify_dashboard.py; these pin the cases a browser run cannot force, such as a reply that lands in the same
instant as the send that caused it.
Run:  python3 tests/dashboard/test_console_log.py
"""
import json
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.abspath(os.path.join(HERE, "..", "..", "ui", "src", "consoleLog.js"))
NODE = shutil.which("node")


def run(script):
    """Run `script` with the module's exports in scope; it prints one JSON value, which is returned."""
    code = 'import * as m from %s;\nconst out = (() => { %s })();\nconsole.log(JSON.stringify(out));' % (json.dumps("file://" + MODULE), script)
    p = subprocess.run([NODE, "--input-type=module", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=30)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout)


@unittest.skipUnless(NODE, "node is not installed")
class ConsoleLog(unittest.TestCase):
    def test_a_send_is_dated_when_it_started_so_a_fast_reply_is_drawn_after_it(self):
        out = run("""
          const rows = [{cursor: 1, wall: 10.0, payload: {command: "console.send", args: {link: "a", text: "ping"}, class: "ok", code: "ok", duration_s: 0.5, caller: "x"}}];
          const sends = m.sendsOf(rows, "a");
          const lines = [{key: "l", wall: 9.8, text: "< pong", reply: true, partial: false}];
          return {tx: sends[0].wall, order: m.conversation(lines, sends).map((x) => x.kind)};
        """)
        self.assertEqual(out, {"tx": 9.5, "order": ["tx", "rx"]})

    def test_only_sends_on_this_link_with_a_text_count_and_a_refused_one_is_kept(self):
        out = run("""
          const row = (c, link, text, cls) => ({cursor: c, wall: c, payload: {command: "console.send", args: {link, text}, class: cls, code: cls, duration_s: 0, caller: "x"}});
          const rows = [row(1, "a", "one", "ok"), row(2, "b", "other", "ok"), row(3, "a", "two", "refused"),
                        {cursor: 4, wall: 4, payload: {command: "devices.list", args: {link: "a", text: "no"}, class: "ok", code: "ok"}},
                        {cursor: 5, wall: 5, payload: {command: "console.send", args: {link: "a"}, class: "usage", code: "usage"}}];
          return m.sendsOf(rows, "a").map((s) => [s.text, s.cls]);
        """)
        self.assertEqual(out, [["one", "ok"], ["two", "refused"]])

    def test_history_is_newest_first_collapses_repeats_and_skips_empty_lines(self):
        out = run("""
          const s = (t) => ({text: t});
          return m.historyOf([s("a"), s("b"), s("b"), s(""), s("a"), s("a")]);
        """)
        self.assertEqual(out, ["a", "b", "a"])

    def test_walking_stops_at_both_ends(self):
        out = run("""
          const h = ["new", "old"];
          return [m.walk(h, -1, "ArrowUp"), m.walk(h, 0, "ArrowUp"), m.walk(h, 1, "ArrowUp"), m.walk(h, 1, "ArrowDown"),
                  m.walk(h, 0, "ArrowDown"), m.walk(h, -1, "ArrowDown"), m.walk([], -1, "ArrowUp"), m.walk(h, 0, "x")];
        """)
        self.assertEqual(out, [0, 1, None, 0, -1, None, None, None])

    def test_the_log_is_cut_to_the_newest_lines_and_a_restart_clears_it(self):
        out = run("""
          const ev = (i) => ({type: "console.line", cursor: i, wall: i, payload: {link: "a", text: "t" + i, reply: false}});
          const many = Array.from({length: m.KEEP_LINES + 5}, (_, i) => ev(i));
          const kept = m.addLines({}, many, "e", false).a;
          const after = m.addLines({a: [{key: "old"}]}, [ev(1)], "e2", true).a;
          const other = m.addLines({a: [{key: "x"}]}, [{type: "console.link", cursor: 9, wall: 9, payload: {link: "a"}}], "e", false);
          return {n: kept.length, first: kept[0].text, last: kept[kept.length - 1].text, after: after.map((l) => l.key), other: other.a.length};
        """)
        self.assertEqual(out, {"n": 2000, "first": "t5", "last": "t2004", "after": ["e2-1"], "other": 1})

    def test_the_reasons_an_input_is_off_are_named_the_way_the_service_names_them(self):
        out = run("""
          const ok = {state: "open", rx_only: false, voltage: {verified: true}};
          return [m.cannotSend(null), m.cannotSend({...ok, state: "absent", reason: "gone"}), m.cannotSend({...ok, state: "unusable", reason: "baud"}),
                  m.cannotSend({...ok, rx_only: true}), m.cannotSend({...ok, voltage: {verified: false}}), m.cannotSend(ok)];
        """)
        self.assertIn("No link", out[0])
        self.assertIn("absent: gone", out[1])
        self.assertIn("cannot be used: baud", out[2])
        self.assertIn("link_rx_only", out[3])
        self.assertIn("voltage_unverified", out[4])
        self.assertEqual(out[5], "")


if __name__ == "__main__":
    unittest.main(verbosity=1)
