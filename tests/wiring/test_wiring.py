#!/usr/bin/env python3
"""Tests for the wiring backend (docs/specs/wiring.md, "Backend slice 1"): detection, the store and its refusals, the
commands through a REAL service build, and the console following the connections with no restart.
Run:  python3 tests/wiring/test_wiring.py
"""
import json
import os
import select
import socket
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.registry import Context, UsageError  # noqa: E402
from nrf_workbench.wiring import detect, store  # noqa: E402

ADAPTER = "5B1F007437"
OTHER_ADAPTER = "8C2D1A9907"
BOARD_IDS = ["239a:8029", "239a:0029"]          # the app and bootloader ids of the RAK profile are in profiles/; read below


def fields(**kw):
    d = dict(device=SERIAL, name="rak-uart0", adapter=ADAPTER, baud=115200, send=False, voltage_measured=None)
    d.update(kw)
    return d


def adapter_row(serial=ADAPTER, ids="1a86:55d4", problem="", node="/dev/ttyACM1"):
    return {"serial": serial, "ids": ids, "name": "USB Single Serial", "node": node, "problem": problem}


class Detect(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)

    def test_a_usb_serial_adapter_is_found_and_a_board_and_a_hub_are_not(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.t.usb("1-4", ("1a86", "55d4"), "USB Single Serial", serial=ADAPTER, manufacturer="QinHeng", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        self.t.usb("1-5", ("05e3", "0610"), "USB Hub", serial="HUB1", manufacturer="Genesys")
        found = detect.adapters(self.t.sysfs, self.t.dev, ["239a:8029", "239a:0029"])
        self.assertEqual([(a["serial"], a["ids"], a["node"], a["problem"]) for a in found],
                         [(ADAPTER, "1a86:55d4", os.path.join(self.t.dev, "ttyACM1"), "")])
        self.assertEqual(found[0]["name"], "USB Single Serial")
        self.assertEqual(found[0]["slot"], "1-4", "where it is plugged, for a target that cannot be found by serial")

    def test_an_adapter_with_no_serial_is_listed_and_says_why_it_cannot_be_connected(self):
        self.t.usb("1-4", ("1a86", "55d4"), "USB Single Serial", serial=None, manufacturer="QinHeng", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        (a,) = detect.adapters(self.t.sysfs, self.t.dev, [])
        self.assertIsNone(a["serial"])
        self.assertIn("no serial number", a["problem"])

    def test_an_adapter_whose_node_is_missing_is_listed_without_a_node(self):
        self.t.usb("1-4", ("1a86", "55d4"), "USB Single Serial", serial=ADAPTER, manufacturer="QinHeng", tty="ttyACM1", majmin="166:1")
        (a,) = detect.adapters(self.t.sysfs, self.t.dev, [])
        self.assertIsNone(a["node"])

    def test_the_board_ids_come_from_every_profile_mode(self):
        profs = [{"modes": {"app": {"ids": ["a:1"]}, "bootloader": {"ids": ["b:2", "a:1"]}}}, {"modes": {"app": {"ids": []}}}]
        self.assertEqual(detect.board_ids(profs), ["a:1", "b:2"])


class Build(unittest.TestCase):
    def build(self, others=(), detected=None, existing=None, board_ids=(), **kw):
        return store.build_log(fields(**kw), [adapter_row()] if detected is None else detected, list(others), list(board_ids), existing)

    def test_a_complete_receive_only_connection_is_accepted(self):
        r = self.build()
        self.assertEqual((r["name"], r["intent"], r["device"], r["baud"], r["send"], r["voltage_measured"]),
                         ("rak-uart0", "log", SERIAL, 115200, False, None))
        self.assertEqual(r["adapter"], {"ids": ["1a86:55d4"], "serial": ADAPTER})

    def test_the_name_defaults_from_the_device(self):
        self.assertEqual(self.build(name=None)["name"], "debug-" + SERIAL.lower()[:4])

    def test_every_malformed_field_is_invalid_and_names_the_field(self):
        cases = [("device", dict(device=None)), ("device", dict(device="")), ("device", dict(device="a b")), ("name", dict(name="Bad Name")),
                 ("name", dict(name="x" * 65)), ("adapter", dict(adapter=None)), ("adapter", dict(adapter="")),
                 ("baud", dict(baud=None)), ("baud", dict(baud="115200")), ("baud", dict(baud=True)), ("baud", dict(baud=115200.0)),
                 ("send", dict(send=None)), ("send", dict(send="no")), ("voltage_measured", dict(send=True, voltage_measured=None)),
                 ("voltage_measured", dict(send=True, voltage_measured="yes")), ("voltage_measured", dict(send=False, voltage_measured=True))]
        for arg, change in cases:
            with self.assertRaises(store.Invalid, msg=str(change)) as cm:
                self.build(**change)
            self.assertEqual(cm.exception.arg, arg, change)

    def test_sending_needs_its_voltage_statement_and_receive_only_does_not(self):
        r = self.build(send=True, voltage_measured=False)
        self.assertEqual((r["send"], r["voltage_measured"]), (True, False))
        r = self.build(send=True, voltage_measured=True)
        self.assertEqual(r["voltage_measured"], True)

    def test_each_rule_has_its_own_code(self):
        taken = self.build(name="other")
        cases = [(dict(name="rak-uart0"), dict(others=[dict(taken, name="rak-uart0", adapter={"ids": ["x:y"], "serial": "Z"})]), "name_taken"),
                 (dict(adapter="NOPE"), {}, "adapter_absent"),
                 (dict(name="second"), dict(others=[taken]), "adapter_taken"),
                 (dict(), dict(board_ids=["1a86:55d4"]), "not_a_declared_link"),
                 (dict(baud=1200), {}, "baud_refused"), (dict(baud=1234), {}, "baud_refused")]
        for change, extra, code in cases:
            with self.assertRaises(store.Refused, msg=str(change)) as cm:
                self.build(**dict(extra, **change))
            self.assertEqual(cm.exception.code, code, change)

    def test_adapter_taken_names_the_holder(self):
        holder = self.build(name="first")
        with self.assertRaises(store.Refused) as cm:
            self.build(name="second", others=[holder])
        self.assertEqual(cm.exception.evidence["held_by"], "first")

    def test_an_adapter_with_a_problem_or_two_with_one_serial_is_refused_as_absent(self):
        for detected in ([adapter_row(problem="no serial number")], [adapter_row(), adapter_row(ids="0403:6001")]):
            with self.assertRaises(store.Refused) as cm:
                self.build(detected=detected)
            self.assertEqual(cm.exception.code, "adapter_absent")

    def test_an_edit_may_keep_an_adapter_that_is_unplugged_but_not_pick_a_missing_one(self):
        existing = self.build()
        self.assertEqual(self.build(detected=[], existing=existing, baud=9600)["baud"], 9600)
        with self.assertRaises(store.Refused):
            self.build(detected=[], existing=existing, adapter="OTHER")

    def test_a_malformed_field_is_reported_before_a_rule(self):
        with self.assertRaises(store.Invalid):
            self.build(adapter="NOPE", baud=None)

    def test_the_stored_link_is_what_the_console_validates(self):
        link = store.to_link(self.build(send=True, voltage_measured=True))
        self.assertTrue(link.usable)
        self.assertEqual((link.device, link.name, link.ids, link.serial, link.baud), (SERIAL, "rak-uart0", ["1a86:55d4"], ADAPTER, 115200))
        self.assertEqual(link.can_send(), (True, "ok", ""))
        self.assertEqual(store.to_link(self.build(send=True, voltage_measured=False)).can_send()[1], "voltage_unverified")
        self.assertEqual(store.to_link(self.build(send=False)).can_send()[1], "link_rx_only")
        self.assertFalse(store.to_link(self.build(), ["1a86:55d4"]).usable, "a board's id is never opened")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "wiring")
        self.s = store.WiringStore(self.dir, now=lambda: 7.0)

    def test_put_list_replace_delete(self):
        rec, created = self.s.put(store.build_log(fields(), [adapter_row()], [], []), by="tester")
        self.assertEqual((created, rec["updated_by"], rec["updated_at"]), (True, "tester", 7.0))
        rec, created = self.s.put(dict(rec, baud=9600))
        self.assertEqual((created, rec["baud"]), (False, 9600))
        self.assertEqual([c["name"] for c in self.s.list()], ["rak-uart0"])
        self.assertTrue(self.s.delete("rak-uart0"))
        self.assertFalse(self.s.delete("rak-uart0"))
        self.assertEqual(self.s.list(), [])

    def test_private_atomic_and_an_unreadable_file_is_left_alone(self):
        self.s.put(store.build_log(fields(), [adapter_row()], [], []))
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)
        path = os.path.join(self.dir, "connections.json")
        with mock.patch("nrf_workbench.core.jsonstore.os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                self.s.delete("rak-uart0")
        self.assertEqual(os.listdir(self.dir), ["connections.json"])
        with open(path, "w") as fh:
            fh.write("{ nope")
        with self.assertRaises(store.StoreError) as cm:
            self.s.list()
        self.assertIn("connections.json", str(cm.exception))
        with open(path) as fh:
            self.assertEqual(fh.read(), "{ nope")


class Rig(object):
    """A real service build over a fake sysfs with a board and a USB adapter, and a real pty as the adapter's port."""

    def __init__(self, tmp, wiring=True, links_path=None):
        self.tree = Tree()
        self.tree.app_board()
        self.tree.node("ttyACM0", 166, 0)
        self.tree.usb("1-4", ("1a86", "55d4"), "USB Single Serial", serial=ADAPTER, manufacturer="QinHeng", tty="ttyACM1", majmin="166:1")
        self.tree.node("ttyACM1", 166, 1)
        self.master, self.slave = os.openpty()
        self.node = os.ttyname(self.slave)
        paths = {"console": os.path.join(tmp, "console")}
        if wiring:
            paths["wiring"] = os.path.join(tmp, "wiring")
        if links_path:
            paths["links"] = links_path
        self.cfg, problems = cfgmod.load(env={}, overrides={"paths": paths, "streams": {
            "events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1}, "console": {"retain": 1000, "max_age_days": 1}}})
        assert problems == [], problems
        self.a, self.b = socket.socketpair()
        hooks = {"tool": "/fake/uploader", "open_uevents": lambda: self.a,
                 "console_resolve": lambda link: ({"device": {}, "node": self.node}, "")}
        self.app, self.host, self.registry, self.caps = service.build(self.cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, hooks=hooks)
        self.started = False
        if self.host.console is not None:
            self.host.console.start()
            self.started = True

    def call(self, name, args=None, caller="tester"):
        return self.registry.call(name, args or {}, Context(self.host, caller, None), capabilities=self.caps)

    def connect(self, **kw):
        a = dict(device=SERIAL, intent="log", adapter=ADAPTER, baud=115200, send=False, name="rak-uart0")
        a.update(kw)
        return self.call("wiring.connect", a)

    def say(self, data):
        os.write(self.master, data)

    def heard(self, n):
        got = b""
        while len(got) < n:
            ready, _, _ = select.select([self.master], [], [], 5)
            if not ready:
                raise AssertionError("the device heard %r and then nothing" % got)
            got += os.read(self.master, 4096)
        return got

    def wait_lines(self, name, n):
        import threading
        cond = threading.Condition()
        token = self.host.streams.console.subscribe(lambda _e: (cond.acquire(), cond.notify_all(), cond.release()))
        try:
            with cond:
                if not cond.wait_for(lambda: (self.host.console.state(name) or {"lines": 0})["lines"] >= n, timeout=5):
                    raise AssertionError("never saw %d lines" % n)
        finally:
            self.host.streams.console.unsubscribe(token)

    def close(self):
        if self.started:
            self.host.console.stop()
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


class Capability(Base):
    def test_the_wiring_commands_and_the_console_exist_with_a_wiring_folder_and_no_links_file(self):
        r = self.rig()
        self.assertIn("wiring", r.caps)
        self.assertIn("console", r.caps)
        names = [c.name for c in r.registry.commands(r.caps)]
        for n in ("wiring.show", "wiring.connect", "wiring.change", "wiring.disconnect", "console.links"):
            self.assertIn(n, names)

    def test_without_a_wiring_folder_there_are_no_wiring_commands_and_no_console_without_links(self):
        r = self.rig(wiring=False)
        self.assertNotIn("wiring", r.caps)
        self.assertNotIn("console", r.caps)
        self.assertFalse([c.name for c in r.registry.commands(r.caps) if c.name.startswith("wiring.")])

    def test_links_yaml_is_not_read_when_a_wiring_folder_is_set_and_the_start_says_so(self):
        path = os.path.join(self.tmp.name, "links.yaml")
        with open(path, "w") as fh:
            fh.write("links: []\n")
        r = self.rig(links_path=path)
        self.assertTrue(any("links.yaml is not read" in p for p in r.host.console_problems), r.host.console_problems)

    def test_the_effects(self):
        r = self.rig()
        rows = dict((c.name, c) for c in r.registry.commands(r.caps) if c.name.startswith("wiring."))
        self.assertEqual(sorted(n for n, c in rows.items() if c.effect == "write"), ["wiring.change", "wiring.connect", "wiring.disconnect"])


class Show(Base):
    def test_show_lists_the_board_the_adapter_the_intents_and_no_connections(self):
        r = self.rig()
        res = r.call("wiring.show")
        d = res.data
        self.assertEqual([(x["serial"], x["mode"]) for x in d["devices"]], [(SERIAL, "app")])
        self.assertEqual([(a["serial"], a["held_by"]) for a in d["adapters"]], [(ADAPTER, None)])
        self.assertEqual(d["connections"], [])
        self.assertEqual(d["intents"], {"log": "built", "control": "not built", "bus": "not built", "swd": "not built", "analog": "not built"})

    def test_show_says_who_holds_an_adapter_and_the_live_state_of_a_connection(self):
        r = self.rig()
        r.connect()
        d = r.call("wiring.show").data
        self.assertEqual(d["adapters"][0]["held_by"], "rak-uart0")
        (c,) = d["connections"]
        self.assertEqual((c["name"], c["device"], c["status"]["state"], c["can_send"]), ("rak-uart0", SERIAL, "open", False))


class Connect(Base):
    def test_a_connection_makes_the_console_link_at_once_with_its_device(self):
        r = self.rig()
        self.assertEqual(r.call("console.links").data["links"], [])
        res = r.connect()
        self.assertEqual((res.cls, res.message, res.data["created"]), ("ok", "connected", True))
        (link,) = r.call("console.links").data["links"]
        self.assertEqual((link["name"], link["device"], link["state"], link["baud"], link["rx_only"]), ("rak-uart0", SERIAL, "open", 115200, True))

    def test_what_the_adapter_prints_is_read_through_the_connection(self):
        r = self.rig()
        r.connect()
        r.say(b"HB up=5s\nHB up=10s\n")
        r.wait_lines("rak-uart0", 2)
        d = r.call("console.since", {"link": "rak-uart0"}).data
        self.assertEqual([l["text"] for l in d["lines"]], ["HB up=5s", "HB up=10s"])

    def test_the_default_name_and_the_second_connection_on_the_same_adapter(self):
        r = self.rig()
        res = r.connect(name=None)
        self.assertEqual(res.data["name"], "debug-" + SERIAL.lower()[:4])
        res = r.connect(name="second")
        self.assertEqual((res.cls, res.code, res.evidence["held_by"]), ("refused", "adapter_taken", "debug-" + SERIAL.lower()[:4]))

    def test_each_refusal_is_a_result_and_each_malformed_field_a_usage_error(self):
        r = self.rig()
        self.assertEqual(r.connect(adapter="NOPE").code, "adapter_absent")
        self.assertEqual(r.connect(baud=1200).code, "baud_refused")
        self.assertEqual(r.connect(intent="control").code, "intent_not_built")
        with self.assertRaises(UsageError):
            r.connect(intent="nonsense")
        with self.assertRaises(UsageError) as cm:
            r.connect(baud=None)
        self.assertIn("baud", str(cm.exception))
        r.connect()
        self.assertEqual(r.connect(adapter=ADAPTER, name="rak-uart0").code, "name_taken")
        self.assertEqual(r.connect(adapter=ADAPTER, name="another").code, "adapter_taken")
        self.assertEqual(r.call("wiring.show").data["connections"][0]["name"], "rak-uart0")
        self.assertEqual(len(r.call("wiring.show").data["connections"]), 1, "a refused connect changed nothing")

    def test_a_board_is_never_offered_or_opened_as_an_adapter(self):
        r = self.rig()
        self.assertNotIn(SERIAL, [a["serial"] for a in r.call("wiring.show").data["adapters"]])
        self.assertEqual(r.connect(adapter=SERIAL).code, "adapter_absent")

    def test_the_caller_is_recorded(self):
        r = self.rig()
        r.connect()
        self.assertEqual(r.call("wiring.show", caller="x").data["connections"][0]["updated_by"], "tester")


class Change(Base):
    def test_turning_on_sending_is_refused_until_the_voltage_is_measured_and_takes_effect_without_a_restart(self):
        r = self.rig()
        r.connect()
        self.assertEqual(r.call("console.send", {"link": "rak-uart0", "text": "x"}).code, "link_rx_only")
        res = r.call("wiring.change", {"name": "rak-uart0", "send": True, "voltage_measured": False})
        self.assertEqual((res.cls, res.message), ("ok", "changed"))
        self.assertEqual(r.call("console.send", {"link": "rak-uart0", "text": "x"}).code, "voltage_unverified")
        r.call("wiring.change", {"name": "rak-uart0", "voltage_measured": True})
        self.assertEqual(r.call("console.send", {"link": "rak-uart0", "text": "ping"}).cls, "ok")
        self.assertEqual(r.heard(5), b"ping\n")

    def test_turning_sending_off_drops_the_voltage_statement(self):
        r = self.rig()
        r.connect(send=True, voltage_measured=True)
        res = r.call("wiring.change", {"name": "rak-uart0", "send": False})
        self.assertEqual((res.data["send"], res.data["voltage_measured"]), (False, None))
        self.assertEqual(r.call("console.send", {"link": "rak-uart0", "text": "x"}).code, "link_rx_only")

    def test_a_permission_change_keeps_the_port_and_its_lines_but_a_baud_change_reopens(self):
        r = self.rig()
        r.connect()
        r.say(b"one\n")
        r.wait_lines("rak-uart0", 1)
        r.call("wiring.change", {"name": "rak-uart0", "send": True, "voltage_measured": True})
        self.assertEqual(r.call("console.links").data["links"][0]["lines"], 1, "same open port, counters kept")
        r.call("wiring.change", {"name": "rak-uart0", "baud": 9600})
        link = r.call("console.links").data["links"][0]
        self.assertEqual((link["baud"], link["state"]), (9600, "open"))
        self.assertEqual(link["lines"], 0, "a new wire is a new port")

    def test_unknown_names_and_bad_values(self):
        r = self.rig()
        self.assertEqual(r.call("wiring.change", {"name": "nope", "baud": 9600}).code, "connection_unknown")
        r.connect()
        self.assertEqual(r.call("wiring.change", {"name": "rak-uart0", "baud": 1200}).code, "baud_refused")
        with self.assertRaises(UsageError):
            r.call("wiring.change", {"name": "rak-uart0", "voltage_measured": True})        # not sending: no voltage question
        self.assertEqual(r.call("console.links").data["links"][0]["baud"], 115200, "a refused change changed nothing")


class Disconnect(Base):
    def test_the_console_stops_at_once_and_the_name_continues_its_offsets_if_made_again(self):
        r = self.rig()
        r.connect()
        r.say(b"abc\ndef\n")
        r.wait_lines("rak-uart0", 2)
        first = r.call("console.since", {"link": "rak-uart0"}).data
        self.assertEqual(first["next"], 8)
        res = r.call("wiring.disconnect", {"name": "rak-uart0"})
        self.assertEqual((res.cls, res.message), ("ok", "disconnected"))
        self.assertEqual(r.call("console.links").data["links"], [])
        self.assertEqual(r.call("console.since", {"link": "rak-uart0"}).code, "not_a_declared_link")
        self.assertEqual(r.call("wiring.show").data["adapters"][0]["held_by"], None, "the adapter is free again")
        r.connect()
        r.say(b"ghi\n")
        r.wait_lines("rak-uart0", 1)
        again = r.call("console.since", {"link": "rak-uart0", "cursor": first["next"]}).data
        self.assertEqual([(l["text"], l["offset"]) for l in again["lines"]], [("ghi", 8)],
                         "a cursor taken before the removal still points at the right place")

    def test_unknown_name(self):
        r = self.rig()
        self.assertEqual(r.call("wiring.disconnect", {"name": "nope"}).code, "connection_unknown")


class StartUp(Base):
    def test_connections_stored_before_the_service_started_are_links_when_it_starts(self):
        r = self.rig()
        r.connect()
        r2 = Rig(self.tmp.name)                          # a second service over the same folders: a restart
        self.addCleanup(r2.close)
        (link,) = r2.call("console.links").data["links"]
        self.assertEqual((link["name"], link["device"]), ("rak-uart0", SERIAL))

    def test_a_damaged_store_is_a_failure_that_names_the_file_and_does_not_stop_the_console(self):
        r = self.rig()
        os.makedirs(os.path.join(self.tmp.name, "wiring"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "wiring", "connections.json"), "w") as fh:
            fh.write("nope")
        for name, args in (("wiring.show", {}), ("wiring.connect", dict(device=SERIAL, intent="log", adapter=ADAPTER, baud=115200, send=False)),
                           ("wiring.disconnect", {"name": "x"})):
            res = r.call(name, args)
            self.assertEqual((res.cls, res.code), ("failed", "write_failed"), name)
            self.assertIn("connections.json", res.message)
        r2 = Rig(self.tmp.name)
        self.addCleanup(r2.close)
        self.assertEqual(r2.call("console.links").data["links"], [])
        self.assertTrue(any("connections.json" in p for p in r2.host.console_problems))


class OverHttp(Base):
    def test_statuses(self):
        from starlette.testclient import TestClient
        r = self.rig()
        c = TestClient(r.app)
        self.addCleanup(c.close)
        body = dict(device=SERIAL, intent="log", adapter=ADAPTER, baud=115200, send=False, name="rak-uart0")
        self.assertEqual(c.post("/api/wiring/connect", json=dict(body, intent="control")).status_code, 422)     # intent_not_built
        self.assertEqual(c.post("/api/wiring/connect", json=dict(body, baud=1200)).status_code, 422)            # baud_refused
        self.assertEqual(c.post("/api/wiring/connect", json=dict(body, name="Bad Name")).status_code, 400)      # usage
        self.assertEqual(c.post("/api/wiring/connect", json=dict(body, adapter="NOPE")).status_code, 409)       # adapter_absent
        self.assertEqual(c.post("/api/wiring/connect", json=body).status_code, 200)
        self.assertEqual(c.post("/api/wiring/connect", json=dict(body, name="two")).status_code, 409)           # adapter_taken
        self.assertEqual(c.post("/api/wiring/connect", json=body).status_code, 409)                             # name_taken
        self.assertEqual(c.post("/api/wiring/change", json={"name": "nope", "baud": 9600}).status_code, 404)
        self.assertEqual(c.post("/api/wiring/disconnect", json={"name": "nope"}).status_code, 404)
        self.assertEqual(c.get("/api/wiring/show").json()["data"]["connections"][0]["name"], "rak-uart0")
        self.assertEqual(c.get("/api/console/links").json()["data"]["links"][0]["device"], SERIAL)

    def test_every_write_is_in_the_activity_log(self):
        from starlette.testclient import TestClient
        r = self.rig()
        c = TestClient(r.app)
        self.addCleanup(c.close)
        c.post("/api/wiring/connect", json=dict(device=SERIAL, intent="log", adapter=ADAPTER, baud=115200, send=False, name="rak-uart0"))
        rows = [e.payload for e in r.host.streams.activity.since(0).events if e.payload["command"] == "wiring.connect"]
        self.assertEqual([(p["args"]["adapter"], p["class"]) for p in rows], [(ADAPTER, "ok")])


if __name__ == "__main__":
    unittest.main(verbosity=1)
