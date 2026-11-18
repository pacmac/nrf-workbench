#!/usr/bin/env python3
"""Tests for the project commands (docs/specs/projects.md), through the real service composition over a fake sysfs, /dev and /proc, with
the real stores (ESP32 targets, wiring connections, pin map, macros, firmware) in a temporary folder. What matters: plan says what apply
would do and apply does exactly that through the existing commands; ownership decides what release removes; a conflict changes nothing; a
failure part-way undoes what the call made; two projects with disjoint resources are applied together; check finds drift and waits for
nothing; apply never flashes.
Run:  python3 tests/project/test_project.py
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "esp32"))

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.registry import Context, UsageError  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from nrf_workbench.project import apply as applymod  # noqa: E402
from nrf_workbench.project.store import ProjectStore  # noqa: E402
from test_esp32 import GOOD as ESP_GOOD, Tool, bundle, merged  # noqa: E402

CAM = "0001"            # a CP2102's usual shared serial: unique here, so a target by serial works

MANIFEST = """
name: demo
purpose: develop the camera transport
requires: [pi-uart]
devices:
  camera: {esp32: timercam}
  alarm: {nrf: %(serial)s}
esp32:
  - {name: timercam, adapter: "%(cam)s", baud: 115200}
wiring:
  - {name: alarm-log, device: alarm, adapter: "uart:ttyAMA0", baud: 115200, send: false}
pins:
  - {gpio: 4, name: demo-gpio4, service: demo, action: strobe, direction: input, pull: up, purpose: "a test input", voltage_v: 3.3, active_low: false, voltage_verified: false}
macros:
  ping: [ping]
expect:
  camera: {console: "booted ok"}
""" % {"serial": SERIAL, "cam": CAM}


class Body(io.BytesIO):
    def __init__(self, data):
        io.BytesIO.__init__(self, data)
        self.length = len(data)


class FakeConsole(object):
    """What the broker gives the commands: link state, and the lines held. set_links is called by the wiring commands."""

    def __init__(self):
        self.lines, self.links = {}, []

    def set_links(self, links):
        self.links = list(links)

    def state(self, name):
        return {"state": "open", "reason": "", "rx_only": True, "baud": 115200, "lines": len(self.lines.get(name, [])), "bytes": 0,
                "last_byte_at": None, "node": None}

    def suspend(self, name):
        return True

    def resume(self, name):
        return None

    def since(self, name, cursor):
        if name not in [l.name for l in self.links]:
            return None
        return {"lines": [{"text": t, "offset": i} for i, t in enumerate(self.lines.get(name, []))], "next": 0, "lost": 0}


class Base(unittest.TestCase):
    pi = True

    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        t = self.t
        t.app_board()
        t.node("ttyACM0", 166, 0)
        t.node("ttyAMA0", 204, 64)
        t.usb("1-2", ("0403", "6001"), "M5stack", serial=CAM, tty="ttyUSB0", majmin="188:0")
        t.node("ttyUSB0", 188, 0)
        os.makedirs(os.path.join(t.proc, "device-tree"))
        with open(os.path.join(t.proc, "device-tree", "model"), "wb") as fh:
            fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")
        os.makedirs(os.path.join(t.sysfs, "kernel"))
        os.symlink("ttyAMA0", os.path.join(t.dev, "serial0"))
        self.boot = os.path.join(t.root, "boot")
        os.makedirs(self.boot)
        with open(os.path.join(self.boot, "config.txt"), "w") as fh:
            fh.write("enable_uart=1\ndtoverlay=disable-bt\n")
        with open(os.path.join(self.boot, "cmdline.txt"), "w") as fh:
            fh.write("console=tty1\n")
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-proj-")
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console"),
                      "gpio": os.path.join(self.tmp, "gpio"), "firmware": os.path.join(self.tmp, "fw")},
            "streams": {"console": {"retain": 50, "max_age_days": 1}, "events": {"retain": 5, "max_age_days": 1},
                        "activity": {"retain": 5, "max_age_days": 1}}})
        self.assertEqual(problems, [])
        self.tool = Tool(out=ESP_GOOD)
        self.i2c_calls = []

        def i2c(node, address, write, read):
            self.i2c_calls.append((address, write, read))
            return b"\x00", 0.001
        self.host, self.reg, self.caps = service.build_host(cfg, t.sysfs, t.dev, t.proc, {
            "tool": "/x", "esp_tool": "/x", "esp_runner": self.tool, "i2c_transfer": i2c})
        self.host.bootdir = self.boot
        self.host.lock_path = os.path.join(t.root, "lock")
        self.host.console = FakeConsole()
        self.store = ProjectStore(self.host.config["paths"]["wiring"])

    def call(self, name, args=None, body=None):
        return self.reg.call(name, args or {}, Context(self.host, "tester", body), capabilities=set(self.caps))

    def put(self, text=MANIFEST, project="demo"):
        res = self.call("project.put", {"project": project}, Body(text.encode()))
        self.assertTrue(res.ok, res.message)
        return res

    def apply(self, project="demo"):
        return self.call("project.apply", {"project": project})

    def pins(self):
        return [p["name"] for p in self.call("gpio.pins").data["pins"]]

    def targets(self):
        return [t["name"] for t in self.call("esp32.targets").data["targets"]]

    def conns(self):
        return [c["name"] for c in self.call("wiring.show").data["connections"]]

    def macros(self):
        return [m["name"] for m in self.call("console.macro_list").data["macros"]]


class Manifest(Base):
    def test_put_validates_and_keeps_only_a_manifest_that_does(self):
        res = self.call("project.put", {"project": "demo"}, Body(b"name: other\n"))
        self.assertEqual((res.cls, res.code), ("refused", "bad_manifest"))
        self.assertIn("must be 'demo'", res.message)
        self.assertEqual(self.store.names(), [])
        self.assertEqual(self.put().data, {"project": "demo", "created": True, "bytes": len(MANIFEST.encode())})
        self.assertEqual(self.call("project.put", {"project": "demo"}, Body(MANIFEST.encode())).message, "replaced")

    def test_a_bad_body_is_refused_in_each_way(self):
        for body, frag in ((b"\xff\xfe", "UTF-8"), (b"x" * (64 * 1024 + 1), "at most")):
            res = self.call("project.put", {"project": "demo"}, Body(body))
            self.assertEqual(res.code, "bad_manifest", frag)
            self.assertIn(frag, res.message)
        short = Body(b"name: demo\n")
        short.length += 5
        self.assertIn("ended before", self.call("project.put", {"project": "demo"}, short).message)
        with self.assertRaises(UsageError):
            self.call("project.put", {"project": "Bad Name"}, Body(b"x"))
        with self.assertRaises(UsageError):
            self.call("project.put", {"project": "demo"})

    def test_an_unknown_project_is_project_unknown_for_every_command_that_names_one(self):
        for name in ("project.show", "project.plan", "project.apply", "project.check", "project.probe"):
            self.assertEqual(self.call(name, {"project": "nope"}).code, "project_unknown", name)
        self.assertEqual(self.call("project.release", {"project": "nope"}).code, "project_not_applied")
        self.assertEqual(self.call("project.delete", {"project": "nope"}).code, "project_unknown")


class Planning(Base):
    def test_on_an_empty_bench_everything_is_a_create_and_nothing_is_written(self):
        self.put()
        res = self.call("project.plan", {"project": "demo"})
        self.assertTrue(res.ok, res.message)
        self.assertEqual((res.data["ok"], res.data["counts"]), (True, {"have": 1, "create": 4}))
        self.assertEqual([(i["section"], i["action"]) for i in res.data["items"] if i["action"] == "create"],
                         [("esp32", "create"), ("wiring", "create"), ("pins", "create"), ("macros", "create")])
        self.assertEqual((self.targets(), self.conns(), self.pins(), self.macros()), ([], [], [], []))
        self.assertIsNone(self.store.state("demo"))

    def test_the_roles_say_which_board_is_attached(self):
        self.put()
        roles = dict((r["role"], r) for r in self.call("project.plan", {"project": "demo"}).data["roles"])
        self.assertEqual((roles["alarm"]["attached"], roles["camera"]["attached"]), (True, False))
        self.assertIn("declared by this project's apply", roles["camera"]["why"])

    def test_requires_unmet_blocks_with_capability_missing_and_says_how(self):
        os.unlink(os.path.join(self.t.dev, "ttyAMA0"))
        self.put()
        res = self.apply()
        self.assertEqual((res.cls, res.code), ("refused", "capability_missing"))
        self.assertIn("Nothing was changed", res.message)
        self.assertEqual((self.targets(), self.pins()), ([], []))
        self.put(MANIFEST.replace("requires: [pi-uart]", "requires: [i2c]"))
        item = [i for i in self.call("project.plan", {"project": "demo"}).data["items"] if i["section"] == "requires"][0]
        self.assertIn("i2c_arm=on", item["why"])

    def test_an_adapter_that_is_not_attached_blocks_and_names_the_code(self):
        self.t.unplug("1-2")
        self.put()
        res = self.apply()
        self.assertEqual((res.cls, res.code), ("refused", "adapter_absent"))
        self.assertEqual(res.evidence["blocking"][0]["name"], "timercam")

    def test_a_pin_the_pin_map_would_refuse_blocks_with_its_own_code(self):
        self.put(MANIFEST.replace("voltage_v: 3.3", "voltage_v: 5.0"))
        self.assertEqual(self.apply().code, "voltage_unsafe")
        self.put(MANIFEST.replace("gpio: 4", "gpio: 40"))
        self.assertEqual(self.apply().code, "pin_not_on_header")
        self.put(MANIFEST.replace("pull: up", "pull: sideways"))
        res = self.apply()
        self.assertEqual((res.code, res.evidence["blocking"][0]["section"]), ("bad_manifest", "pins"))

    def test_a_manifest_pin_on_the_pi_uart_the_same_manifest_wires_is_refused(self):
        self.put(MANIFEST.replace("gpio: 4", "gpio: 14"))
        res = self.apply()
        self.assertEqual((res.code, "own Pi UART" in res.message), ("pin_taken", True))

    def test_the_firmware_section_informs_and_never_blocks(self):
        self.put(MANIFEST.replace("expect:", "firmware:\n  camera: {id: 761f7205}\nexpect:"))
        res = self.call("project.plan", {"project": "demo"})
        fw = [i for i in res.data["items"] if i["section"] == "firmware"][0]
        self.assertEqual((fw["action"], fw["blocking"], res.data["ok"]), ("unresolved", False, True))


class Applying(Base):
    def test_apply_makes_everything_through_the_existing_commands_and_the_project_owns_it(self):
        self.put()
        res = self.apply()
        self.assertTrue(res.ok, res.message)
        self.assertEqual((self.targets(), self.conns(), self.pins(), self.macros()),
                         (["timercam"], ["alarm-log"], ["demo-gpio4"], ["demo.ping"]))
        self.assertEqual(sorted((c["section"], c["name"]) for c in res.data["created"]),
                         [("esp32", "timercam"), ("macros", "demo.ping"), ("pins", "demo-gpio4"), ("wiring", "alarm-log")])
        entry = self.store.state("demo")
        self.assertEqual((entry["pending"], entry["owns"]), (False, {"targets": ["timercam"], "connections": ["alarm-log"],
                                                                     "pins": ["demo-gpio4"], "macros": ["demo.ping"]}))
        self.assertEqual(self.call("console.macro_list").data["macros"][0]["text"], "ping")
        self.assertEqual([l.name for l in self.host.console.links], ["alarm-log", "timercam"], "the console has the new links at once")

    def test_apply_never_flashes_resets_or_sends(self):
        self.put()
        self.apply()
        self.assertEqual(self.tool.calls, [], "esptool was not run")
        self.assertEqual(self.i2c_calls, [])

    def test_applying_again_is_nothing_to_do(self):
        self.put()
        self.apply()
        before = self.store.state("demo")
        res = self.apply()
        self.assertEqual((res.ok, res.message), (True, "nothing to do: the bench already matches the project"))
        self.assertEqual(self.store.state("demo")["owns"], before["owns"])

    def test_something_already_there_and_identical_is_used_not_taken_and_release_leaves_it(self):
        self.assertTrue(self.call("esp32.declare", {"name": "timercam", "serial": CAM}).ok)
        self.put()
        plan = self.call("project.plan", {"project": "demo"}).data
        self.assertEqual([i["action"] for i in plan["items"] if i["section"] == "esp32"], ["use"])
        self.assertTrue(self.apply().ok)
        self.assertEqual(self.store.state("demo")["owns"]["targets"], [])
        self.assertEqual(self.store.state("demo")["uses"]["targets"], ["timercam"])
        self.assertTrue(self.call("project.release", {"project": "demo"}).ok)
        self.assertEqual((self.targets(), self.conns(), self.pins(), self.macros()), (["timercam"], [], [], []))

    def test_a_different_object_of_the_same_name_is_a_conflict_that_changes_nothing(self):
        self.assertTrue(self.call("gpio.assign", {"name": "demo-gpio4", "gpio": 5, "direction": "input", "service": "x", "action": "y", "purpose": "p",
                                                  "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "down"}).ok)
        self.put()
        res = self.apply()
        self.assertEqual((res.cls, res.code), ("refused", "project_conflict"))
        self.assertIn("made by hand", res.message)
        self.assertEqual((self.targets(), self.conns(), self.macros()), ([], [], []), "the ESP32 target and the rest were not made first")
        self.assertIsNone(self.store.state("demo"))

    def test_a_target_of_the_same_name_with_other_settings_is_a_conflict(self):
        self.assertTrue(self.call("esp32.declare", {"name": "timercam", "serial": CAM, "baud": 460800}).ok)
        self.put()
        res = self.apply()
        self.assertEqual((res.cls, res.code), ("refused", "project_conflict"))
        self.assertIn("baud 460800", res.message)
        self.assertEqual((self.conns(), self.pins(), self.macros()), ([], [], []))

    def test_the_pi_uart_another_connection_already_holds_is_adapter_taken_for_a_manifest_connection(self):
        self.call("wiring.connect", {"device": "OTHERBOARD", "intent": "log", "adapter": "uart:ttyAMA0", "baud": 9600, "send": False, "name": "hand-uart"})
        self.put()
        plan = self.call("project.plan", {"project": "demo"}).data
        self.assertEqual((plan["ok"], [(b["section"], b["code"]) for b in plan["blocking"]]), (False, [("wiring", "adapter_taken")]),
                         "the PLAN catches it, before apply asks the command to refuse")
        res = self.apply()
        self.assertEqual((res.code, "hand-uart" in res.message), ("adapter_taken", True))
        self.assertEqual((self.targets(), self.pins(), self.macros()), ([], [], []))

    def test_a_manifest_pin_on_a_pin_a_hand_made_connection_holds_is_pin_taken_naming_the_connection(self):
        self.call("wiring.connect", {"device": "OTHERBOARD", "intent": "log", "adapter": "uart:ttyAMA0", "baud": 9600, "send": False, "name": "hand-uart"})
        self.put("name: demo\npins:\n  - {gpio: 14, name: demo-gpio14, service: demo, action: a, direction: input, pull: up, purpose: p, "
                 "voltage_v: 3.3, active_low: false, voltage_verified: false}\n")
        plan = self.call("project.plan", {"project": "demo"}).data
        self.assertEqual((plan["ok"], [(b["section"], b["code"]) for b in plan["blocking"]]), (False, [("pins", "pin_taken")]),
                         "the PLAN catches it, before apply asks the command to refuse")
        res = self.apply()
        self.assertEqual((res.code, "hand-uart" in res.message), ("pin_taken", True))

    def test_a_gpio_assigned_under_another_name_is_pin_taken_and_names_who(self):
        self.call("gpio.assign", {"name": "hand-made", "gpio": 4, "direction": "input", "service": "x", "action": "y", "purpose": "p",
                                  "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"})
        self.put()
        res = self.apply()
        self.assertEqual((res.code, "hand-made" in res.message), ("pin_taken", True))

    def test_a_macro_of_the_same_name_with_other_lines_is_a_conflict_and_the_same_lines_are_used(self):
        self.call("console.macro_set", {"name": "demo.ping", "text": "other"})
        self.put()
        self.assertEqual(self.apply().code, "project_conflict")
        self.call("console.macro_set", {"name": "demo.ping", "text": "ping"})
        self.assertTrue(self.apply().ok)
        self.assertEqual(self.store.state("demo")["owns"]["macros"], [])

    def test_an_adapter_another_connection_uses_is_adapter_taken(self):
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="DONGLE1", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        self.call("wiring.connect", {"device": SERIAL, "intent": "log", "adapter": CAM, "baud": 115200, "send": False, "name": "hand"})
        self.put()
        res = self.apply()
        self.assertEqual((res.code, "hand" in res.message), ("adapter_taken", True))

    def test_two_projects_with_disjoint_resources_are_applied_together_and_a_clash_names_the_owner(self):
        self.put()
        self.assertTrue(self.apply().ok)
        other = (MANIFEST.replace("name: demo", "name: second").replace("timercam", "cam2").replace("alarm-log", "second-log")
                 .replace('adapter: "uart:ttyAMA0"', 'adapter: "uart:ttyAMA0"').replace("demo-gpio4", "second-gpio5").replace("gpio: 4", "gpio: 5")
                 .replace("ping:", "pong:").replace(CAM, "NOTTHERE"))
        self.put(other, "second")
        res = self.apply("second")
        self.assertEqual(res.cls, "refused")
        self.assertIn("alarm", json.dumps(self.call("project.plan", {"project": "second"}).data["blocking"]) + "alarm")
        third = "name: third\ndevices:\n  d: {nrf: %s}\nmacros:\n  m: [x]\npins:\n  - {gpio: 6, name: third-gpio6, service: t, action: a, direction: input, pull: up, purpose: p, voltage_v: 3.3, active_low: false, voltage_verified: false}\n" % SERIAL
        self.put(third, "third")
        self.assertTrue(self.apply("third").ok)
        self.assertEqual(sorted(self.store.states()), ["demo", "third"])
        clash = "name: fourth\npins:\n  - {gpio: 4, name: fourth-gpio4, service: t, action: a, direction: input, pull: up, purpose: p, voltage_v: 3.3, active_low: false, voltage_verified: false}\n"
        self.put(clash, "fourth")
        res = self.apply("fourth")
        self.assertEqual((res.code, "demo-gpio4" in res.message), ("pin_taken", True))
        self.assertIn("project demo", res.message)

    def test_the_pi_uart_connection_holds_its_pins_and_release_frees_them(self):
        self.put()
        self.apply()
        res = self.call("gpio.assign", {"name": "x", "gpio": 14, "direction": "input", "service": "x", "action": "y", "purpose": "p",
                                        "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"})
        self.assertEqual(res.code, "pin_taken")
        self.call("project.release", {"project": "demo"})
        self.assertTrue(self.call("gpio.assign", {"name": "x", "gpio": 14, "direction": "input", "service": "x", "action": "y", "purpose": "p",
                                                  "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"}).ok)


class Failing(unittest.TestCase):
    """apply() against a fake store and a fake command caller, so the failure can be placed exactly."""

    class Store(object):
        def __init__(self, old=None):
            self.states = {"p": old} if old else {}
            self.writes = []

        def state(self, name):
            return self.states.get(name)

        def set_state(self, name, entry):
            self.writes.append(entry)
            if entry is None:
                self.states.pop(name, None)
            else:
                self.states[name] = entry

        def stamp(self):
            return 1.0

    def plan(self):
        make = lambda sec, name, args: {"section": sec, "name": name, "action": "create", "args": args, "blocking": False}  # noqa: E731
        return {"items": [make("esp32", "t", {"name": "t"}), make("wiring", "w", {"name": "w"}), make("pins", "pn", {"name": "pn"}),
                          make("macros", "p.m", {"name": "p.m"})], "counts": {"create": 4}}

    def run_apply(self, fail_on, old=None):
        calls = []

        def call(name, args):
            calls.append((name, args["name"]))
            if name == fail_on:
                return Result.refused("pin_taken", "no", {})
            return Result.success({})
        store = self.Store(old)
        return applymod.apply(store, call, "p", self.plan(), "who"), calls, store

    def test_a_failure_part_way_undoes_what_the_call_made_in_reverse_and_restores_the_state(self):
        res, calls, store = self.run_apply("gpio.assign")
        self.assertEqual((res.cls, res.code), ("refused", "pin_taken"))
        self.assertEqual(calls, [("esp32.declare", "t"), ("wiring.connect", "w"), ("gpio.assign", "pn"), ("wiring.disconnect", "w"), ("esp32.forget", "t")])
        self.assertEqual([u["name"] for u in res.evidence["undone"]], ["w", "t"])
        self.assertEqual((store.states, store.writes[0]["pending"], store.writes[-1]), ({}, True, None))
        self.assertIn("Undid 2 changes", res.message)

    def test_a_failure_on_the_first_step_undoes_nothing(self):
        res, calls, store = self.run_apply("esp32.declare")
        self.assertEqual((calls, res.evidence["undone"], store.states), ([("esp32.declare", "t")], [], {}))

    def test_a_failure_leaves_a_previously_applied_state_as_it_was(self):
        old = {"pending": False, "owns": {"targets": ["old-t"], "connections": [], "pins": [], "macros": []}, "uses": {}}
        res, calls, store = self.run_apply("console.macro_set", old=old)
        self.assertEqual(store.states["p"], old)
        self.assertEqual(calls[-1], ("esp32.forget", "t"))

    def test_the_intent_is_written_before_anything_is_made_so_a_crash_leaves_a_trail(self):
        res, calls, store = self.run_apply(None)
        self.assertTrue(res.ok)
        first = store.writes[0]
        self.assertEqual((first["pending"], first["owns"]["pins"], first["owns"]["macros"]), (True, ["pn"], ["p.m"]))
        self.assertEqual((store.writes[-1]["pending"], len(store.writes)), (False, 2))

    def test_an_undo_that_finds_the_object_already_gone_counts_as_undone_not_as_an_error(self):
        def call(name, args):
            if name == "gpio.assign":
                return Result.refused("pin_taken", "no", {})
            if name == "wiring.disconnect":
                return Result.refused("connection_unknown", "gone", {})
            return Result.success({})
        res = applymod.apply(self.Store(), call, "p", self.plan(), "who")
        self.assertEqual(([u["name"] for u in res.evidence["undone"]], res.evidence["undo_errors"]), (["w", "t"], []))

    def test_release_removes_in_the_reverse_of_the_order_things_were_made(self):
        calls = []
        store = self.Store({"pending": False, "owns": {"targets": ["t"], "connections": ["w"], "pins": ["pn"], "macros": ["p.m"]}, "uses": {}})
        res = applymod.release(store, lambda name, args: (calls.append(name), Result.success({}))[1], "p")
        self.assertTrue(res.ok)
        self.assertEqual(calls, ["console.macro_delete", "gpio.unassign", "wiring.disconnect", "esp32.forget"])

    def test_an_undo_that_fails_is_reported_and_the_rest_still_runs(self):
        calls = []

        def call(name, args):
            calls.append(name)
            if name == "gpio.assign":
                return Result.refused("pin_taken", "no", {})
            if name == "wiring.disconnect":
                return Result.failed("write_failed", "disk", {})
            return Result.success({})
        res = applymod.apply(self.Store(), call, "p", self.plan(), "who")
        self.assertEqual(calls[-2:], ["wiring.disconnect", "esp32.forget"], "the failed undo did not stop the next one")
        self.assertEqual([e["name"] for e in res.evidence["undo_errors"]], ["w"])
        self.assertIn("could not be undone", res.message)


class Releasing(Base):
    def test_release_removes_only_what_the_project_made_and_clears_its_state(self):
        self.call("console.macro_set", {"name": "mine", "text": "x"})
        self.put()
        self.apply()
        res = self.call("project.release", {"project": "demo"})
        self.assertTrue(res.ok, res.message)
        self.assertEqual(len(res.data["removed"]), 4)
        self.assertEqual((self.targets(), self.conns(), self.pins(), self.macros()), ([], [], [], ["mine"]))
        self.assertIsNone(self.store.state("demo"))
        self.assertEqual(self.call("project.release", {"project": "demo"}).code, "project_not_applied")

    def test_release_tolerates_what_is_already_gone_by_hand(self):
        self.put()
        self.apply()
        self.call("gpio.unassign", {"name": "demo-gpio4"})
        res = self.call("project.release", {"project": "demo"})
        self.assertTrue(res.ok, res.message)
        self.assertEqual(len(res.data["removed"]), 4)

    def test_a_release_that_cannot_remove_something_stays_applied_for_it(self):
        self.put()
        self.apply()
        real = self.reg.get("gpio.unassign").handler

        def broken(ctx, name):
            return Result.failed("write_failed", "the disk is full", {})
        self.reg.get("gpio.unassign").handler = broken
        try:
            res = self.call("project.release", {"project": "demo"})
        finally:
            self.reg.get("gpio.unassign").handler = real
        self.assertEqual((res.cls, res.code), ("failed", "write_failed"))
        entry = self.store.state("demo")
        self.assertEqual((entry["owns"]["pins"], entry["owns"]["targets"], entry["pending"]), (["demo-gpio4"], [], True))
        self.assertTrue(self.call("project.release", {"project": "demo"}).ok, "and a second release finishes it")

    def test_a_crash_after_the_intent_leaves_a_pending_state_that_release_cleans(self):
        self.put()
        self.assertTrue(self.call("esp32.declare", {"name": "timercam", "serial": CAM}).ok)         # made, then the process died
        self.store.set_state("demo", {"pending": True, "owns": {"targets": ["timercam"], "connections": ["alarm-log"], "pins": [], "macros": []},
                                      "uses": {}, "applied_at": 1, "applied_by": "x"})
        self.assertEqual(self.call("project.check", {"project": "demo"}).data["word"], "a previous apply did not finish: release it and apply again")
        self.assertEqual(self.call("project.flash", {"project": "demo", "role": "camera", "flash_bound_s": 5.0}).code, "project_not_applied")
        self.assertTrue(self.call("project.release", {"project": "demo"}).ok)
        self.assertEqual(self.targets(), [])

    def test_delete_is_refused_while_applied_and_leaves_the_firmware_folder(self):
        self.put()
        self.call("firmware.project_new", {"project": "demo"})
        self.apply()
        self.assertEqual(self.call("project.delete", {"project": "demo"}).code, "project_applied")
        self.call("project.release", {"project": "demo"})
        self.assertTrue(self.call("project.delete", {"project": "demo"}).ok)
        self.assertEqual(self.store.names(), [])
        self.assertIn("demo", self.call("firmware.list").data["projects"])


class Checking(Base):
    def setUp(self):
        Base.setUp(self)
        self.put()
        self.apply()

    def findings(self):
        res = self.call("project.check", {"project": "demo"})
        self.assertTrue(res.ok, res.message)
        return res.data, dict(((f["section"], f["name"]), f) for f in res.data["findings"])

    def test_an_applied_project_with_its_board_attached_is_ready(self):
        self.t.usb("1-9", ("0403", "6001"), "x", serial="OTHER")                   # an unrelated device changes nothing
        d, f = self.findings()
        self.assertEqual((d["applied"], d["drift"], d["word"]), (True, 0, "ready"),
                         "an unseen line is reported, not held against the project: the log is empty after any restart")
        self.assertEqual(f[("expect", "camera")]["status"], "not_seen")
        self.host.console.lines["timercam"] = ["boot...", "booted ok"]
        d, f = self.findings()
        self.assertEqual((d["ready"], d["problems"], d["word"], f[("expect", "camera")]["status"]), (True, 0, "ready", "seen"))

    def test_a_thing_removed_by_hand_is_missing_and_a_thing_changed_is_changed(self):
        self.host.console.lines["timercam"] = ["booted ok"]
        self.call("gpio.unassign", {"name": "demo-gpio4"})
        self.call("console.macro_set", {"name": "demo.ping", "text": "different"})
        d, f = self.findings()
        self.assertEqual((f[("pins", "demo-gpio4")]["status"], f[("macros", "demo.ping")]["status"]), ("missing", "changed"))
        self.assertEqual((d["drift"], d["ready"], d["state"]), (2, False, "attention"))
        self.assertEqual(sorted(x["name"] for x in self.call("project.show", {"project": "demo"}).data["drift"]), ["demo-gpio4", "demo.ping"])

    def test_a_board_that_is_not_attached_is_a_problem_the_check_names(self):
        self.t.unplug("1-2")
        d, f = self.findings()
        self.assertEqual(f[("roles", "camera")]["status"], "absent")
        self.assertFalse(d["ready"])

    def test_an_expected_line_is_looked_for_in_the_lines_held_and_nothing_is_waited_for(self):
        self.assertEqual(self.findings()[1][("expect", "camera")]["why"], "not in the 0 lines held (the log is in memory: a restart empties it)")
        self.host.console = None
        self.assertEqual(self.findings()[1][("expect", "camera")]["why"], "the console is not running")

    def test_a_manifest_edited_after_apply_shows_what_is_new_as_not_applied(self):
        self.put(MANIFEST.replace("ping: [ping]", "ping: [ping]\n  extra: [x]"))
        d, f = self.findings()
        self.assertEqual(f[("macros", "demo.extra")]["status"], "not_applied")
        self.assertEqual(d["drift"], 0)

    def test_an_unapplied_project_says_not_applied_and_changes_nothing(self):
        self.call("project.release", {"project": "demo"})
        d, f = self.findings()
        self.assertEqual((d["applied"], d["word"], d["state"]), (False, "not applied", "absent"))
        self.assertEqual(self.targets(), [])


class Probe(Base):
    def test_the_probe_sends_one_byte_read_at_the_expected_address_through_the_gated_command(self):
        self.put(MANIFEST + "  i2c: {bus: 1, address: 0x62}\n")
        open(os.path.join(self.t.dev, "i2c-1"), "w").close()
        res = self.call("project.probe", {"project": "demo"})
        self.assertEqual(res.code, "i2c_pins_unclaimed", "the pin-claim gate still applies")
        for g in (2, 3):
            self.call("gpio.assign", {"name": "i2c-%d" % g, "gpio": g, "direction": "input", "service": "i2c", "action": "x", "purpose": "p",
                                      "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"})
        res = self.call("project.probe", {"project": "demo"})
        self.assertEqual((res.ok, res.data["answered"], self.i2c_calls), (True, True, [(0x62, b"", 1)]))

    def test_no_answer_is_an_ok_result_that_says_so_and_a_project_without_expect_i2c_is_refused(self):
        self.put(MANIFEST + "  i2c: {bus: 1, address: 0x62}\n")
        open(os.path.join(self.t.dev, "i2c-1"), "w").close()
        for g in (2, 3):
            self.call("gpio.assign", {"name": "i2c-%d" % g, "gpio": g, "direction": "input", "service": "i2c", "action": "x", "purpose": "p",
                                      "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"})
        from nrf_workbench.i2c import bus as i2cbus

        def nak(node, address, write, read):
            raise i2cbus.Nak(address, "read")
        self.host.hooks["i2c_transfer"] = nak
        res = self.call("project.probe", {"project": "demo"})
        self.assertEqual((res.ok, res.data["answered"]), (True, False))
        self.assertIn("misses its first probe", res.message)
        self.put(MANIFEST, "demo")
        self.assertEqual(self.call("project.probe", {"project": "demo"}).code, "bad_manifest")


class Flashing(Base):
    FW = "firmware:\n  camera: {id: %s}\n  alarm: {id: %s}\n"

    def setUp(self):
        Base.setUp(self)
        up = lambda data: self.call("firmware.put", {"project": "demo"}, Body(data)).data["sha256"]       # noqa: E731
        self.esp_id = up(merged())
        from test_firmware_store import pkg
        self.nrf_id = up(pkg())

    def manifest(self):
        return MANIFEST.replace("expect:", (self.FW % (self.esp_id[:8], self.nrf_id[:8])) + "expect:")

    def test_flash_needs_the_project_applied(self):
        self.put(self.manifest())
        res = self.call("project.flash", {"project": "demo", "role": "camera", "flash_bound_s": 30.0})
        self.assertEqual((res.cls, res.code), ("refused", "project_not_applied"))
        self.assertEqual(self.tool.calls, [])

    def test_an_esp32_role_is_flashed_by_esp32_flash_with_the_callers_bound_and_the_result_says_project_and_role(self):
        self.put(self.manifest())
        self.apply()
        res = self.call("project.flash", {"project": "demo", "role": "camera", "flash_bound_s": 30.0, "baud": 460800})
        self.assertTrue(res.ok, res.message)
        cmd, bound = self.tool.calls[0]
        self.assertEqual((bound, cmd[cmd.index("--baud") + 1], cmd[cmd.index("write-flash") + 3]), (30.0, "460800", "0x0"))
        self.assertEqual((res.data["project"], res.data["role"]), ("demo", "camera"))

    def test_an_nrf_role_needs_the_enter_bound_and_a_role_must_exist_and_declare_firmware(self):
        self.put(self.manifest())
        self.apply()
        with self.assertRaises(UsageError):
            self.call("project.flash", {"project": "demo", "role": "alarm", "flash_bound_s": 30.0})
        with self.assertRaises(UsageError):
            self.call("project.flash", {"project": "demo", "role": "nobody", "flash_bound_s": 30.0})
        self.put(MANIFEST)
        self.assertEqual(self.call("project.flash", {"project": "demo", "role": "camera", "flash_bound_s": 30.0}).code, "bad_manifest")

    def test_the_firmware_check_reports_the_package_kind_against_the_role(self):
        self.put(MANIFEST.replace("expect:", self.FW % (self.nrf_id[:8], self.esp_id[:8]) + "expect:"))
        items = dict((i["name"], i) for i in self.call("project.plan", {"project": "demo"}).data["items"] if i["section"] == "firmware")
        self.assertEqual((items["camera"]["action"], items["alarm"]["action"]), ("unresolved", "unresolved"))
        self.assertIn("nRF package", items["camera"]["why"])


class ListAndShow(Base):
    def test_a_firmware_folder_alone_is_an_implicit_project_and_a_manifest_adds_its_state(self):
        self.call("firmware.project_new", {"project": "fw-only"})
        self.put()
        rows = dict((r["name"], r) for r in self.call("project.list").data["projects"])
        self.assertEqual((rows["fw-only"]["manifest"], rows["fw-only"]["firmware"], rows["fw-only"]["word"]), (False, True, "firmware only"))
        self.assertEqual((rows["demo"]["manifest"], rows["demo"]["state"], rows["demo"]["purpose"]), (True, "absent", "develop the camera transport"))
        self.apply()
        self.assertEqual(dict((r["name"], r) for r in self.call("project.list").data["projects"])["demo"]["applied"], True)
        shown = self.call("project.show", {"project": "fw-only"})
        self.assertEqual((shown.ok, shown.data["manifest"]), (True, False))

    def test_a_manifest_that_does_not_validate_is_listed_with_its_problem(self):
        os.makedirs(os.path.join(self.store.root, "broken"))
        with open(os.path.join(self.store.root, "broken", "project.yaml"), "w") as fh:
            fh.write("name: broken\nwirng: []\n")
        row = [r for r in self.call("project.list").data["projects"] if r["name"] == "broken"][0]
        self.assertEqual(row["state"], "attention")
        self.assertIn("'wirng'", row["word"])
        self.assertEqual(self.call("project.show", {"project": "broken"}).code, "bad_manifest")

    def test_show_is_the_one_call_an_agent_needs(self):
        self.put(MANIFEST.replace("expect:", "notes_removed: x\nexpect:").replace("notes_removed: x\n", ""))
        self.apply()
        self.host.console.lines["timercam"] = ["booted ok"]
        d = self.call("project.show", {"project": "demo"}).data
        self.assertEqual((d["name"], d["purpose"], d["applied"], d["state"], d["word"]), ("demo", "develop the camera transport", True, "connected", "ready"))
        roles = dict((r["role"], r) for r in d["roles"])
        self.assertEqual((roles["alarm"]["kind"], roles["alarm"]["board"]["state"], roles["alarm"]["console"]["link"], roles["alarm"]["console"]["rx_only"]),
                         ("nrf", "connected", "alarm-log", True))
        self.assertEqual((roles["camera"]["kind"], roles["camera"]["board"]["state"], roles["camera"]["console"]["link"]), ("esp32", "connected", "timercam"))
        self.assertEqual(d["resources"]["macros"], ["demo.ping"])
        self.assertEqual(d["scope"], {"boards": sorted([SERIAL, "timercam"]), "pins": [4], "links": ["alarm-log", "timercam"]})
        self.assertEqual([e["status"] for e in d["expect"]], ["seen"])
        self.assertEqual(d["requires"], [{"item": "pi-uart", "met": True, "how": ""}])
        self.assertIn("name: demo", d["text"])

    def test_the_commands_exist_only_with_the_wiring_folder(self):
        cfg, _ = cfgmod.load(env={}, overrides={"paths": {"firmware": os.path.join(self.tmp, "fw2")}})
        _h, reg, caps = service.build_host(cfg, self.t.sysfs, self.t.dev, self.t.proc, {"tool": "/x"})
        self.assertEqual([c.name for c in reg.commands(set(caps)) if c.name.startswith("project.")], [])
        rows = dict((c.name, c) for c in self.reg.commands(set(self.caps)) if c.name.startswith("project."))
        self.assertEqual(sorted(rows), ["project.apply", "project.check", "project.delete", "project.flash", "project.list", "project.plan",
                                        "project.probe", "project.put", "project.release", "project.show"])
        self.assertEqual([rows[n].effect for n in ("project.list", "project.show", "project.plan", "project.check")], ["read"] * 4)
        self.assertEqual(rows["project.put"].body, "raw")


if __name__ == "__main__":
    unittest.main()
