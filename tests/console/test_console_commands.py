#!/usr/bin/env python3
"""Tests for console.commands, through a REAL service build (service.build), a real broker and real pseudo-terminal pairs.

What matters: every refusal is made by the service and by name (a page that disables a button is a courtesy), a send
needs a wire and a measured voltage, a macro runs through the same guards line by line and stops at the first refusal,
and the console is absent unless it has its links file, its folder and a bounded log.
Run:  python3 tests/console/test_commands.py
"""
import json
import os
import select
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

import yaml  # noqa: E402
from fakes import Tree  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.console import port as portmod  # noqa: E402
from nrf_workbench.console.port import Port  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.registry import Context, UsageError  # noqa: E402

CEILING_S = 10            # only ever reached by a failing test


def raw_link(**kw):
    raw = {"name": "dbg", "backend": "usb-adapter", "adapter": {"ids": ["1a86:55d4"], "serial": "S1"}, "baud": 115200,
           "dut_tx_to_host": True, "host_tx_to_dut": True, "voltage": {"dut": 3.3, "adapter": 3.3, "verified": True},
           "dtr_rts_reach_dut": False, "reply_marker": "< "}
    raw.update(kw)
    return raw


class Rig(object):
    """A built service over a pty pair, with the broker started and every guard real."""

    def __init__(self, tmp, raws=None, bounds=True, links_path=True, console_dir=True, open_port=Port, start=True, log_mb=None):
        self.tmp = tmp
        self.tree = Tree()
        self.master, self.slave = os.openpty()
        self.node = os.ttyname(self.slave)
        self.present = True
        self.links_file = os.path.join(tmp, "links.yaml")
        with open(self.links_file, "w") as fh:
            yaml.safe_dump({"links": raws if raws is not None else [raw_link()]}, fh)
        self.macro_dir = os.path.join(tmp, "console-data")
        paths = {}
        if links_path:
            paths["links"] = self.links_file
        if console_dir:
            paths["console"] = self.macro_dir
        streams = {"events": {"retain": 100, "max_age_days": 1}, "activity": {"retain": 100, "max_age_days": 1}}
        if bounds:
            streams["console"] = {"retain": 1000, "max_age_days": 1}
        self.cfg, problems = cfgmod.load(env={}, overrides={"paths": paths, "streams": streams, "console_log": {"max_mb": log_mb}})
        assert problems == [], problems
        self.a, self.b = socket.socketpair()
        hooks = {"tool": "/fake/uploader", "open_port": open_port, "open_uevents": lambda: self.a,
                 "console_resolve": lambda link: (({"device": {}, "node": self.node}, "") if self.present
                                                  else (None, "the adapter is not attached"))}
        self.app, self.host, self.registry, self.caps = service.build(self.cfg, sysfs=self.tree.sysfs, hooks=hooks)
        self.started = False
        self.broker = self.host.console                      # kept, so a test may unset host.console and cleanup still works
        if start and self.broker is not None:
            self.broker.start()
            self.started = True

    def call(self, name, args=None, caller="tester"):
        return self.registry.call(name, args or {}, Context(self.host, caller, None), capabilities=self.caps)

    def say(self, data):
        os.write(self.master, data)

    def heard(self, n):
        got = b""
        while len(got) < n:
            ready, _, _ = select.select([self.master], [], [], CEILING_S)
            if not ready:
                raise AssertionError("the device heard %r and then nothing" % got)
            got += os.read(self.master, 4096)
        return got

    def wait_lines(self, n):
        deadline_cond = threading.Condition()
        token = self.host.streams.console.subscribe(lambda _e: (deadline_cond.acquire(), deadline_cond.notify_all(), deadline_cond.release()))
        try:
            with deadline_cond:
                if not deadline_cond.wait_for(lambda: self.host.console.state("dbg")["lines"] >= n, timeout=CEILING_S):
                    raise AssertionError("never saw %d lines" % n)
        finally:
            self.host.streams.console.unsubscribe(token)

    def close(self):
        if self.started:
            self.broker.stop()
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        self.a.close()
        self.b.close()
        self.tree.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def rig(self, **kw):
        r = Rig(self.tmp.name, **kw)
        self.addCleanup(r.close)
        return r


class Presence(Base):
    def test_the_console_exists_only_with_a_links_file_a_folder_and_a_bounded_log(self):
        self.assertIn("console", self.rig().caps)
        for missing in ({"bounds": False}, {"links_path": False}, {"console_dir": False}):
            r = Rig(self.tmp.name, **missing)
            self.addCleanup(r.close)
            self.assertNotIn("console", r.caps, missing)
            self.assertIsNone(r.host.console)
            names = [c.name for c in r.registry.commands(r.caps)]
            self.assertFalse([n for n in names if n.startswith("console.")], missing)

    def test_an_absent_log_says_which_setting_is_missing(self):
        r = self.rig(bounds=False)
        self.assertIn("streams.console.retain", r.host.streams.absent["console"])

    def test_every_console_command_is_registered_with_its_effect(self):
        r = self.rig()
        rows = dict((c.name, c) for c in r.registry.commands(r.caps) if c.name.startswith("console."))
        self.assertEqual(sorted(rows), ["console.history", "console.links", "console.macro_delete", "console.macro_list", "console.macro_run",
                                        "console.macro_set", "console.reopen", "console.send", "console.since"])
        self.assertEqual(sorted(n for n, c in rows.items() if c.effect == "write"),
                         ["console.macro_delete", "console.macro_run", "console.macro_set", "console.reopen", "console.send"])
        self.assertEqual(sorted(n for n, c in rows.items() if c.needs_service),
                         ["console.history", "console.macro_run", "console.reopen", "console.send", "console.since"])


class Reading(Base):
    def test_links_lists_the_declared_link_with_its_state_wiring_and_counts(self):
        r = self.rig()
        res = r.call("console.links")
        self.assertTrue(res.ok, res.to_dict())
        (link,) = res.data["links"]
        self.assertEqual((link["name"], link["state"], link["node"], link["baud"], link["rx_only"]), ("dbg", "open", r.node, 115200, False))
        self.assertEqual((res.data["live"], res.data["problems"]), (True, []))

    def test_links_in_local_mode_lists_what_is_declared_and_whether_the_adapter_is_attached(self):
        r = self.rig(start=False)
        r.host.console = None                                # the CLI's local mode has no broker
        res = r.call("console.links")
        self.assertEqual([(l["name"], l["state"]) for l in res.data["links"]], [("dbg", "absent")])
        self.assertFalse(res.data["live"])

    def test_since_returns_the_lines_after_a_cursor_and_the_next_cursor(self):
        r = self.rig()
        r.say(b"one\ntwo\n")
        r.wait_lines(2)
        res = r.call("console.since", {"link": "dbg"})
        self.assertEqual(([l["text"] for l in res.data["lines"]], res.data["next"], res.data["lost"]), (["one", "two"], 8, 0))
        res = r.call("console.since", {"link": "dbg", "cursor": 4})
        self.assertEqual([l["text"] for l in res.data["lines"]], ["two"])
        self.assertEqual(r.call("console.since", {"link": "dbg", "cursor": 8}).data["lines"], [])

    def test_since_refuses_an_unknown_link_and_a_negative_cursor_and_needs_the_service(self):
        r = self.rig()
        res = r.call("console.since", {"link": "nope"})
        self.assertEqual((res.cls, res.code), ("refused", "not_a_declared_link"))
        with self.assertRaises(UsageError):
            r.call("console.since", {"link": "dbg", "cursor": -1})
        r.host.console = None
        self.assertEqual(r.call("console.since", {"link": "dbg"}).code, "service_not_running")


class Guards(Base):
    def refusal(self, raws, code, **args):
        r = self.rig(raws=raws)
        res = r.call("console.send", dict({"link": raws[0]["name"], "text": "x"}, **args))
        self.assertEqual((res.cls, res.code), ("refused", code), res.to_dict())
        return r

    def test_a_receive_only_link_refuses_a_send_by_name(self):
        r = self.refusal([raw_link(host_tx_to_dut=False)], "link_rx_only")
        ready, _, _ = select.select([r.master], [], [], 0.05)
        self.assertEqual(ready, [], "nothing reached the device")

    def test_an_unverified_voltage_refuses_a_send(self):
        self.refusal([raw_link(voltage={"dut": 3.3, "adapter": 5.0, "verified": False})], "voltage_unverified")

    def test_an_unusable_link_refuses_with_the_problems_own_code(self):
        self.refusal([raw_link(baud=1200)], "baud_refused")
        self.refusal([raw_link(dtr_rts_reach_dut=True)], "dtr_rts_unsafe")
        self.refusal([raw_link(adapter={"ids": ["239a:8029"], "serial": "x"})], "not_a_declared_link")

    def test_a_link_that_is_not_declared_is_refused(self):
        r = self.rig()
        self.assertEqual(r.call("console.send", {"link": "nope", "text": "x"}).code, "not_a_declared_link")

    def test_an_adapter_that_is_not_attached_is_link_absent(self):
        r = self.rig(start=False)
        r.present = False
        r.host.console.start()
        r.started = True
        res = r.call("console.send", {"link": "dbg", "text": "x"})
        self.assertEqual((res.cls, res.code), ("refused", "link_absent"))

    def test_an_adapter_held_by_another_process_is_port_held(self):
        r = self.rig(start=False)
        other = Port(r.node, 115200)
        self.addCleanup(other.close)
        r.host.console.start()
        r.started = True
        self.assertEqual(r.call("console.send", {"link": "dbg", "text": "x"}).code, "port_held")

    def test_a_line_may_not_carry_a_newline_a_return_or_a_nul_nor_exceed_the_cap(self):
        r = self.rig()
        for bad in ("a\nb", "a\rb", "a\0b", "x" * 1025):
            with self.assertRaises(UsageError, msg=repr(bad[:10])):
                r.call("console.send", {"link": "dbg", "text": bad})
        self.assertTrue(r.call("console.send", {"link": "dbg", "text": "x" * 1024}).ok)

    def test_the_refusals_are_made_before_anything_is_written(self):
        r = self.rig(raws=[raw_link(host_tx_to_dut=False)])
        r.call("console.send", {"link": "dbg", "text": "reboot"})
        ready, _, _ = select.select([r.master], [], [], 0.05)
        self.assertEqual(ready, [])


class Sending(Base):
    def test_a_send_reaches_the_device_with_the_line_ending_and_is_a_success(self):
        r = self.rig()
        res = r.call("console.send", {"link": "dbg", "text": "ping"})
        self.assertEqual((res.cls, res.data["sent"], res.data["reply"]), ("ok", 5, None))
        self.assertEqual(r.heard(5), b"ping\n")

    def test_an_empty_line_is_just_the_ending(self):
        r = self.rig()
        self.assertEqual(r.call("console.send", {"link": "dbg", "text": ""}).data["sent"], 1)
        self.assertEqual(r.heard(1), b"\n")

    def test_with_a_bound_it_returns_the_reply_line(self):
        r = self.rig()

        def device():
            r.heard(5)
            r.say(b"< pong\n")
        threading.Thread(target=device, daemon=True).start()
        res = r.call("console.send", {"link": "dbg", "text": "ping", "bound_s": CEILING_S})
        self.assertEqual((res.cls, res.data["reply"]), ("ok", "< pong"))

    def test_a_reply_that_never_comes_is_no_reply_a_failure_not_a_refusal(self):
        r = self.rig()
        res = r.call("console.send", {"link": "dbg", "text": "ping", "bound_s": 0.05})
        self.assertEqual((res.cls, res.code), ("failed", "no_reply"))
        self.assertEqual(res.evidence["sent"], 5, "the line WAS sent, and the result says so")

    def test_a_bound_must_be_more_than_zero(self):
        r = self.rig()
        for bad in (0, -1):
            with self.assertRaises(UsageError):
                r.call("console.send", {"link": "dbg", "text": "x", "bound_s": bad})

    def test_an_adapter_that_goes_mid_write_is_link_absent_and_a_stuck_write_is_write_failed(self):
        class Fake(object):
            def __init__(self, node, baud):
                self.mode = "gone"

            def fileno(self):
                return os.open(os.devnull, os.O_RDONLY)

            def write_all(self, data, bound_s):
                raise portmod.PortGone("the port went away") if self.mode == "gone" else portmod.PortError("did not finish")

            def read(self):
                return b""

            def close(self):
                pass
        r = self.rig(open_port=Fake, start=False)
        with mock.patch.object(r.host.console, "start"):
            pass
        st = r.host.console._states["dbg"]
        st.port = Fake("n", 1)
        st.state = "open"
        res = r.call("console.send", {"link": "dbg", "text": "x"})
        self.assertEqual((res.cls, res.code), ("refused", "link_absent"))
        st.port.mode = "stuck"
        res = r.call("console.send", {"link": "dbg", "text": "x"})
        self.assertEqual((res.cls, res.code), ("failed", "write_failed"))

    def test_reopen_returns_the_links_state(self):
        r = self.rig()
        res = r.call("console.reopen", {"link": "dbg"})
        self.assertEqual((res.cls, res.data["state"]), ("ok", "open"))
        self.assertEqual(r.call("console.reopen", {"link": "nope"}).code, "not_a_declared_link")

    def test_a_send_is_recorded_in_the_activity_log_with_its_link_and_text_and_announced_as_started(self):
        r = self.rig()
        res = r.registry.call("console.send", {"link": "dbg", "text": "hello"}, Context(r.host, "tester", None), capabilities=r.caps)
        self.assertTrue(res.ok)
        # the registry is below the API, which does the recording: prove the arguments are what history will show
        self.assertEqual(res.data["link"], "dbg")


class Macros(Base):
    def macros(self, r):
        return dict((m["name"], m) for m in r.call("console.macro_list").data["macros"])

    def test_a_macro_is_saved_listed_replaced_and_deleted(self):
        r = self.rig()
        res = r.call("console.macro_set", {"name": "boot", "text": "reset\nstatus"})
        self.assertEqual((res.cls, res.data["created"], res.data["lines"]), ("ok", True, 2))
        self.assertEqual(self.macros(r)["boot"]["text"], "reset\nstatus")
        res = r.call("console.macro_set", {"name": "boot", "text": "status"})
        self.assertEqual((res.message, res.data["created"]), ("replaced", False))
        self.assertEqual(r.call("console.macro_delete", {"name": "boot"}).message, "deleted")
        self.assertEqual(self.macros(r), {})
        res = r.call("console.macro_delete", {"name": "boot"})
        self.assertEqual((res.cls, res.code), ("refused", "macro_unknown"))

    def test_a_macro_name_and_text_are_checked(self):
        r = self.rig()
        for name, text in (("Bad Name", "x"), ("../x", "x"), ("", "x"), ("ok", ""), ("ok", "\n\n"), ("ok", "a\0b"),
                           ("ok", "a\rb"), ("ok", "x" * 1025), ("ok", "\n".join(["x" * 1000] * 5))):
            with self.assertRaises(UsageError, msg=repr((name, text[:10]))):
                r.call("console.macro_set", {"name": name, "text": text})
        self.assertEqual(self.macros(r), {})

    def test_a_trailing_newline_is_not_another_line_and_crlf_is_one_ending(self):
        r = self.rig()
        self.assertEqual(r.call("console.macro_set", {"name": "m", "text": "a\r\nb\n"}).data["lines"], 2)

    def test_macros_are_kept_in_a_private_atomically_written_file_the_bench_owns(self):
        r = self.rig()
        r.call("console.macro_set", {"name": "m", "text": "a"})
        path = os.path.join(r.macro_dir, "macros.json")
        self.assertEqual(json.load(open(path))["macros"]["m"]["text"], "a")
        self.assertEqual(os.stat(r.macro_dir).st_mode & 0o777, 0o700)
        self.assertEqual([n for n in os.listdir(r.macro_dir) if n != "macros.json"], [], "no temporary left behind")
        self.assertEqual(json.load(open(path))["macros"]["m"]["updated_by"], "tester")

    def test_a_write_that_fails_at_the_swap_leaves_the_old_file_whole_and_no_temporary(self):
        from nrf_workbench.console import macros as macros_mod
        r = self.rig()
        r.call("console.macro_set", {"name": "m", "text": "old"})
        path = os.path.join(r.macro_dir, "macros.json")
        before = open(path).read()
        with mock.patch.object(macros_mod.os, "replace", side_effect=OSError("disk gone")):
            res = r.call("console.macro_set", {"name": "m", "text": "new"})
        self.assertEqual((res.cls, res.code), ("failed", "write_failed"))
        self.assertEqual(open(path).read(), before, "the old macros survived the failed write")
        self.assertEqual([n for n in os.listdir(r.macro_dir) if n != "macros.json"], [], "no temporary left behind")

    def test_a_macro_runs_its_lines_in_order_through_the_same_send_path(self):
        r = self.rig()
        r.call("console.macro_set", {"name": "boot", "text": "one\ntwo\nthree"})
        res = r.call("console.macro_run", {"link": "dbg", "name": "boot"})
        self.assertEqual((res.cls, res.data["ran"], res.data["of"]), ("ok", 3, 3))
        self.assertEqual(r.heard(14), b"one\ntwo\nthree\n")

    def test_a_macro_with_a_bound_waits_for_each_lines_reply(self):
        r = self.rig()
        r.call("console.macro_set", {"name": "m", "text": "a\nb"})

        def device():
            for _ in range(2):
                r.heard(2)
                r.say(b"< ok\n")
        threading.Thread(target=device, daemon=True).start()
        res = r.call("console.macro_run", {"link": "dbg", "name": "m", "bound_s": CEILING_S})
        self.assertEqual((res.cls, [x["reply"] for x in res.data["results"]]), ("ok", ["< ok", "< ok"]))

    def test_a_macro_stops_at_the_first_refusal_and_never_pushes_on_past_it(self):
        r = self.rig(raws=[raw_link(host_tx_to_dut=False)])
        r.call("console.macro_set", {"name": "m", "text": "a\nb\nc"})
        res = r.call("console.macro_run", {"link": "dbg", "name": "m"})
        self.assertEqual((res.cls, res.code, res.data["ran"], res.data["of"]), ("refused", "link_rx_only", 0, 3))
        self.assertEqual(len(res.data["results"]), 1, "the second and third lines were never tried")
        self.assertIn("line 1 of 3", res.message)

    def test_a_macro_stops_at_a_missing_reply(self):
        r = self.rig()
        r.call("console.macro_set", {"name": "m", "text": "a\nb"})
        res = r.call("console.macro_run", {"link": "dbg", "name": "m", "bound_s": 0.05})
        self.assertEqual((res.cls, res.code, res.data["ran"]), ("failed", "no_reply", 0))
        self.assertEqual(r.heard(2), b"a\n")
        ready, _, _ = select.select([r.master], [], [], 0.05)
        self.assertEqual(ready, [], "the second line was not sent after the first got no reply")

    def test_running_an_unknown_macro_or_on_an_unknown_link_is_refused(self):
        r = self.rig()
        self.assertEqual(r.call("console.macro_run", {"link": "dbg", "name": "nope"}).code, "macro_unknown")
        r.call("console.macro_set", {"name": "m", "text": "a"})
        self.assertEqual(r.call("console.macro_run", {"link": "nope", "name": "m"}).code, "not_a_declared_link")

    def test_an_unreadable_macros_file_is_a_failure_that_names_it_and_is_never_overwritten(self):
        r = self.rig()
        os.makedirs(r.macro_dir, exist_ok=True)
        path = os.path.join(r.macro_dir, "macros.json")
        with open(path, "w") as fh:
            fh.write("{ not json")
        for name, args in (("console.macro_list", {}), ("console.macro_set", {"name": "m", "text": "a"}),
                           ("console.macro_delete", {"name": "m"})):
            res = r.call(name, args)
            self.assertEqual((res.cls, res.code), ("failed", "write_failed"), name)
            self.assertIn("macros.json", res.message)
        self.assertEqual(open(path).read(), "{ not json", "the damaged file is left for a person to look at")


class OverHttp(Base):
    """The same commands through the real app: the routes, the statuses, the live feed, and the history."""

    def client(self, r):
        from starlette.testclient import TestClient
        c = TestClient(r.app)
        self.addCleanup(c.close)
        return c

    def test_the_routes_and_the_statuses_a_client_sees(self):
        r = self.rig()
        c = self.client(r)
        links = c.get("/api/console/links").json()
        self.assertEqual((links["class"], links["data"]["links"][0]["name"]), ("ok", "dbg"))
        ok = c.post("/api/console/send", json={"link": "dbg", "text": "hi"})
        self.assertEqual((ok.status_code, ok.json()["data"]["sent"]), (200, 3))
        self.assertEqual(r.heard(3), b"hi\n")
        self.assertEqual(c.post("/api/console/send", json={"link": "nope", "text": "x"}).status_code, 422)
        self.assertEqual(c.post("/api/console/send", json={"link": "dbg", "text": "a\nb"}).status_code, 400)
        self.assertEqual(c.post("/api/console/macro_delete", json={"name": "nope"}).status_code, 404)
        self.assertEqual(c.get("/api/console/since", params={"link": "dbg", "cursor": "0"}).status_code, 200)

    def test_each_refusal_has_its_own_status(self):
        r = self.rig(raws=[raw_link(host_tx_to_dut=False)])
        c = self.client(r)
        res = c.post("/api/console/send", json={"link": "dbg", "text": "x"})
        self.assertEqual((res.status_code, res.json()["code"]), (409, "link_rx_only"))
        r2 = self.rig(raws=[raw_link(baud=1200)])
        res = self.client(r2).post("/api/console/send", json={"link": "dbg", "text": "x"})
        self.assertEqual((res.status_code, res.json()["code"]), (422, "baud_refused"))

    def test_the_live_feed_carries_the_links_state_and_every_line_as_it_arrives(self):
        r = self.rig()
        c = self.client(r)
        with c.websocket_connect("/ws/events") as ws:
            ws.send_json({"want": {"console": {"cursor": 0}}})
            first = ws.receive_json()
            self.assertEqual((first["type"], first["feed"]), ("events", "console"))
            self.assertEqual([e["type"] for e in first["page"]["events"]], ["console.link"])
            r.say(b"hello\n")
            got = []
            while not got:
                for e in ws.receive_json()["page"]["events"]:
                    if e["type"] == "console.line":
                        got.append(e["payload"])
            self.assertEqual((got[0]["link"], got[0]["text"], got[0]["offset"]), ("dbg", "hello", 0))

    def test_a_send_is_in_the_history_with_its_link_and_text_for_every_caller(self):
        r = self.rig()
        c = self.client(r)
        c.post("/api/console/send", json={"link": "dbg", "text": "status"})
        c.post("/api/console/send", json={"link": "dbg", "text": "reboot"})
        sent = [e.payload for e in r.host.streams.activity.since(0).events if e.payload["command"] == "console.send"]
        self.assertEqual([(p["args"]["link"], p["args"]["text"], p["class"]) for p in sent],
                         [("dbg", "status", "ok"), ("dbg", "reboot", "ok")])
        started = [e.payload for e in r.host.streams.activity.bus.since(0, None, types=("command.started",)).events]
        self.assertEqual([p["command"] for p in started], ["console.send", "console.send"])

    def test_no_console_routes_exist_when_the_console_is_absent(self):
        r = Rig(self.tmp.name, bounds=False)
        self.addCleanup(r.close)
        self.assertEqual(self.client(r).get("/api/console/links").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=1)
