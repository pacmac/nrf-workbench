#!/usr/bin/env python3
"""Tests for the GPIO pin map (docs/specs/gpio.md): the header table, the validation, the store, the commands through a
real service build, and a gate proving nothing in the area touches a pin.
Run:  python3 tests/gpio/test_gpio.py
"""
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import Tree  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.registry import Context, UsageError  # noqa: E402
from nrf_workbench.gpio import header, pinmap  # noqa: E402

# What `gpioinfo -c 0` printed for lines 0 to 27 on the bench Pi 4B (Raspberry Pi 4 Model B Rev 1.4), 2026-11-17.
PI_LINE_NAMES = ["ID_SDA", "ID_SCL"] + ["GPIO%d" % n for n in range(2, 28)]


def output(**kw):
    d = dict(name="dut-reset", gpio=17, direction="output", service="programmer", action="reset",
             purpose="Resets the RAK on the bench for a flash.", active_low=False, voltage_v=3.3, voltage_verified=False,
             initial="inactive", max_pulse_ms=100, drives_dut=True)
    d.update(kw)
    return d


def inp(**kw):
    d = dict(name="door", gpio=22, direction="input", service="manual", action="sense", purpose="Tells if the bench door is shut.",
             active_low=True, voltage_v=3.3, voltage_verified=True, pull="up")
    d.update(kw)
    return d


class Header(unittest.TestCase):
    def test_forty_pins_and_every_header_gpio_exactly_once(self):
        rows = header.table()
        self.assertEqual([r["pin"] for r in rows], list(range(1, 41)))
        gpios = sorted(r["gpio"] for r in rows if r["gpio"] is not None)
        self.assertEqual(gpios, list(range(0, 28)))

    def test_the_power_and_ground_pins(self):
        rows = header.table()
        self.assertEqual(sum(r["kind"] == "power" for r in rows), 4)
        self.assertEqual(sum(r["kind"] == "ground" for r in rows), 8)
        self.assertEqual(sum(r["kind"] == "gpio" for r in rows), 28)
        self.assertTrue(all(r["gpio"] is None and r["class"] is None for r in rows if r["kind"] != "gpio"))

    def test_the_labels_agree_with_the_line_names_the_real_pi_reports(self):
        by_gpio = dict((r["gpio"], r["label"]) for r in header.table() if r["gpio"] is not None)
        for offset, name in enumerate(PI_LINE_NAMES):
            self.assertEqual(by_gpio[offset], name, "gpiochip0 line %d" % offset)

    def test_classes(self):
        self.assertEqual([g for g in range(28) if header.gpio_class(g) == header.RESERVED], [0, 1])
        self.assertEqual([g for g in range(28) if header.gpio_class(g) == header.BUS], [2, 3, 7, 8, 9, 10, 11, 14, 15])
        self.assertEqual(header.gpio_class(17), header.FREE)
        self.assertIsNone(header.gpio_class(28))
        self.assertIsNone(header.gpio_class(-1))

    def test_a_few_physical_positions_the_wiring_depends_on(self):
        by_pin = dict((r["pin"], r) for r in header.table())
        self.assertEqual((by_pin[11]["gpio"], by_pin[12]["gpio"], by_pin[8]["gpio"], by_pin[10]["gpio"]), (17, 18, 14, 15))
        self.assertEqual((by_pin[27]["gpio"], by_pin[28]["gpio"]), (0, 1))


class Build(unittest.TestCase):
    def test_a_complete_output_and_input_are_accepted(self):
        out = pinmap.build(output(), {})
        self.assertEqual((out["direction"], out["initial"], out["max_pulse_ms"], out["drives_dut"], out["class"]),
                         ("output", "inactive", 100, True, "free"))
        self.assertEqual(out["voltage"], {"signal": 3.3, "verified": False})
        self.assertNotIn("pull", out)
        i = pinmap.build(inp(), {})
        self.assertEqual((i["pull"], i["active_low"]), ("up", True))
        self.assertNotIn("max_pulse_ms", i)

    def test_every_malformed_field_is_invalid_and_names_the_field(self):
        cases = [
            ("name", dict(name="Bad Name")), ("name", dict(name="")), ("name", dict(name=None)), ("name", dict(name="x" * 65)),
            ("gpio", dict(gpio="17")), ("gpio", dict(gpio=True)), ("gpio", dict(gpio=None)), ("gpio", dict(gpio=17.0)),
            ("direction", dict(direction="both")), ("direction", dict(direction=None)),
            ("service", dict(service="Not A Slug")), ("action", dict(action="")),
            ("purpose", dict(purpose="")), ("purpose", dict(purpose="  ")), ("purpose", dict(purpose="x" * 201)),
            ("purpose", dict(purpose="two\nlines")), ("purpose", dict(purpose=None)),
            ("active_low", dict(active_low=None)), ("active_low", dict(active_low="no")),
            ("voltage_v", dict(voltage_v=None)), ("voltage_v", dict(voltage_v=0)), ("voltage_v", dict(voltage_v=-3.3)),
            ("voltage_v", dict(voltage_v=float("nan"))), ("voltage_v", dict(voltage_v=float("inf"))), ("voltage_v", dict(voltage_v=True)),
            ("voltage_verified", dict(voltage_verified=None)), ("voltage_verified", dict(voltage_verified="yes")),
            ("initial", dict(initial=None)), ("initial", dict(initial="high")),
            ("max_pulse_ms", dict(max_pulse_ms=None)), ("max_pulse_ms", dict(max_pulse_ms=0)), ("max_pulse_ms", dict(max_pulse_ms=1.5)),
            ("max_pulse_ms", dict(max_pulse_ms=True)),
            ("drives_dut", dict(drives_dut=None)), ("drives_dut", dict(drives_dut="yes")),
            ("pull", dict(pull="up")),                                          # a pull on an output
        ]
        for arg, change in cases:
            with self.assertRaises(pinmap.Invalid, msg=str(change)) as cm:
                pinmap.build(output(**change), {})
            self.assertEqual(cm.exception.arg, arg, change)

    def test_the_input_side_of_the_same(self):
        for arg, change in (("pull", dict(pull=None)), ("pull", dict(pull="sideways")), ("initial", dict(initial="inactive")),
                            ("max_pulse_ms", dict(max_pulse_ms=10)), ("drives_dut", dict(drives_dut=False))):
            with self.assertRaises(pinmap.Invalid, msg=str(change)) as cm:
                pinmap.build(inp(**change), {})
            self.assertEqual(cm.exception.arg, arg)

    def test_the_boundaries_that_are_allowed(self):
        self.assertEqual(pinmap.build(output(name="a" * 64, purpose="x" * 200, max_pulse_ms=1, voltage_v=3.3), {})["max_pulse_ms"], 1)
        for pull in ("up", "down", "none"):
            self.assertEqual(pinmap.build(inp(pull=pull), {})["pull"], pull)

    def test_the_rules_each_fire_with_their_own_code(self):
        for change, code in ((dict(gpio=0), "pin_reserved"), (dict(gpio=1), "pin_reserved"), (dict(gpio=28), "pin_not_on_header"),
                             (dict(gpio=-1), "pin_not_on_header"), (dict(gpio=57), "pin_not_on_header"),
                             (dict(voltage_v=3.31), "voltage_unsafe"), (dict(voltage_v=5), "voltage_unsafe"),
                             (dict(voltage_v=5.0, voltage_verified=True), "voltage_unsafe")):
            with self.assertRaises(pinmap.Refused, msg=str(change)) as cm:
                pinmap.build(output(**change), {})
            self.assertEqual(cm.exception.code, code, change)

    def test_the_neighbours_of_each_rule_are_accepted(self):
        for gpio in (2, 27):
            self.assertEqual(pinmap.build(output(gpio=gpio), {})["gpio"], gpio)
        self.assertEqual(pinmap.build(output(gpio=14), {})["class"], "bus")

    def test_a_malformed_field_is_reported_before_a_rule(self):
        with self.assertRaises(pinmap.Invalid):
            pinmap.build(output(gpio=0, name="Bad Name"), {})
        with self.assertRaises(pinmap.Invalid):
            pinmap.build(output(voltage_v=5, purpose=""), {})
        for later in (dict(service="Bad"), dict(action=""), dict(initial="high"), dict(max_pulse_ms=0), dict(drives_dut=None)):
            with self.assertRaises(pinmap.Invalid, msg=str(later)):          # the rule would fire (reserved GPIO) if shape came second
                pinmap.build(output(gpio=0, **later), {})

    def test_a_gpio_held_by_another_name_is_taken_and_names_the_holder(self):
        other = pinmap.build(output(name="first"), {})
        with self.assertRaises(pinmap.Refused) as cm:
            pinmap.build(output(name="second"), {"first": other})
        self.assertEqual((cm.exception.code, cm.exception.evidence["held_by"]), ("pin_taken", "first"))
        self.assertEqual(pinmap.build(output(name="second", gpio=18), {"first": other})["gpio"], 18)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "gpio")
        self.store = pinmap.PinStore(self.dir, now=lambda: 5.0)

    def test_assign_list_replace_unassign(self):
        rec, created = self.store.assign(output(), by="tester")
        self.assertEqual((created, rec["updated_by"], rec["updated_at"]), (True, "tester", 5.0))
        self.store.assign(inp())
        self.assertEqual([p["gpio"] for p in self.store.list()], [17, 22], "listed in GPIO order")
        rec, created = self.store.assign(output(max_pulse_ms=50))
        self.assertEqual((created, rec["max_pulse_ms"]), (False, 50))
        self.assertEqual(len(self.store.list()), 2)
        self.assertTrue(self.store.unassign("door"))
        self.assertFalse(self.store.unassign("door"))
        self.assertEqual([p["name"] for p in self.store.list()], ["dut-reset"])

    def test_a_name_may_move_to_a_free_gpio_but_not_onto_another_names(self):
        self.store.assign(output())
        self.store.assign(inp())
        rec, created = self.store.assign(output(gpio=18))
        self.assertEqual((rec["gpio"], created), (18, False))
        with self.assertRaises(pinmap.Refused):
            self.store.assign(output(gpio=22))
        self.assertEqual([p["gpio"] for p in self.store.list()], [18, 22], "a refused assign changed nothing")

    def test_the_file_is_private_and_holds_what_was_declared(self):
        self.store.assign(output())
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)
        with open(os.path.join(self.dir, "pins.json")) as fh:
            self.assertEqual(json.load(fh)["pins"]["dut-reset"]["gpio"], 17)

    def test_a_failed_swap_leaves_the_old_file_whole_and_no_temporary(self):
        self.store.assign(output())
        path = os.path.join(self.dir, "pins.json")
        with open(path) as fh:
            before = fh.read()
        with mock.patch("nrf_workbench.core.jsonstore.os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                self.store.assign(output(max_pulse_ms=50))
        with open(path) as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(os.listdir(self.dir), ["pins.json"])

    def test_an_unreadable_file_is_an_error_that_names_it_and_is_left_alone(self):
        os.makedirs(self.dir)
        path = os.path.join(self.dir, "pins.json")
        with open(path, "w") as fh:
            fh.write("{ not json")
        for call in (self.store.list, lambda: self.store.assign(output()), lambda: self.store.unassign("x")):
            with self.assertRaises(pinmap.PinStoreError) as cm:
                call()
            self.assertIn("pins.json", str(cm.exception))
        with open(path) as fh:
            self.assertEqual(fh.read(), "{ not json")


class Rig(object):
    """A real service build over a fake /proc whose device tree says what the test wants."""

    def __init__(self, tmp, model=b"Raspberry Pi 4 Model B Rev 1.4\0", gpio_dir=True):
        self.tree = Tree()
        if model is not None:
            os.makedirs(os.path.join(self.tree.proc, "device-tree"), exist_ok=True)
            with open(os.path.join(self.tree.proc, "device-tree", "model"), "wb") as fh:
                fh.write(model)
        self.gpio_dir = os.path.join(tmp, "gpio")
        paths = {"gpio": self.gpio_dir} if gpio_dir else {}
        self.cfg, problems = cfgmod.load(env={}, overrides={"paths": paths,
                                                            "streams": {"events": {"retain": 10, "max_age_days": 1},
                                                                        "activity": {"retain": 10, "max_age_days": 1}}})
        assert problems == [], problems
        self.app, self.host, self.registry, self.caps = service.build(self.cfg, sysfs=self.tree.sysfs, proc=self.tree.proc,
                                                                      hooks={"tool": "/fake/uploader"})

    def call(self, name, args=None, caller="tester"):
        return self.registry.call(name, args or {}, Context(self.host, caller, None), capabilities=self.caps)

    def close(self):
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
    def gpio_names(self, r):
        return [c.name for c in r.registry.commands(r.caps) if c.name.startswith("gpio.")]

    def test_the_commands_exist_on_a_pi_with_a_folder(self):
        r = self.rig()
        self.assertIn("gpio", r.caps)
        self.assertEqual(self.gpio_names(r), ["gpio.assign", "gpio.header", "gpio.pins", "gpio.unassign"])

    def test_they_are_absent_where_the_device_tree_is_not_a_pi_or_missing_or_there_is_no_folder(self):
        for kw in ({"model": b"Some Other Board\0"}, {"model": None}, {"gpio_dir": False}):
            r = self.rig(**kw)
            self.assertNotIn("gpio", r.caps, kw)
            self.assertEqual(self.gpio_names(r), [], kw)

    def test_a_gpiochip_is_not_evidence_of_a_pi(self):
        r = self.rig(model=None)
        os.makedirs(os.path.join(r.tree.dev), exist_ok=True)
        open(os.path.join(r.tree.dev, "gpiochip0"), "w").close()
        self.assertFalse(service.is_raspberry_pi(r.tree.proc))

    def test_the_effects(self):
        r = self.rig()
        rows = dict((c.name, c) for c in r.registry.commands(r.caps) if c.name.startswith("gpio."))
        self.assertEqual(sorted(n for n, c in rows.items() if c.effect == "write"), ["gpio.assign", "gpio.unassign"])
        self.assertFalse([n for n, c in rows.items() if c.needs_service], "the map is a file: no service state is needed")


class Commands(Base):
    def test_assign_pins_header_and_unassign(self):
        r = self.rig()
        self.assertEqual(r.call("gpio.pins").data["pins"], [])
        res = r.call("gpio.assign", output())
        self.assertEqual((res.cls, res.message, res.data["created"]), ("ok", "assigned", True))
        self.assertEqual(r.call("gpio.assign", output(max_pulse_ms=20)).message, "replaced")
        rows = r.call("gpio.header").data["header"]
        self.assertEqual([x["assigned"] for x in rows if x["gpio"] == 17], ["dut-reset"])
        self.assertEqual(sum(x["assigned"] is not None for x in rows), 1)
        self.assertEqual(r.call("gpio.unassign", {"name": "dut-reset"}).message, "unassigned")
        refusal = r.call("gpio.unassign", {"name": "dut-reset"})
        self.assertEqual((refusal.cls, refusal.code), ("refused", "pin_unknown"))

    def test_a_refusal_is_a_result_and_a_malformed_field_is_a_usage_error(self):
        r = self.rig()
        res = r.call("gpio.assign", output(gpio=0))
        self.assertEqual((res.cls, res.code), ("refused", "pin_reserved"))
        with self.assertRaises(UsageError) as cm:
            r.call("gpio.assign", output(purpose=""))
        self.assertIn("purpose", str(cm.exception))
        self.assertEqual(r.call("gpio.pins").data["pins"], [], "nothing was recorded")

    def test_a_damaged_store_is_a_failure_that_names_the_file(self):
        r = self.rig()
        os.makedirs(r.gpio_dir)
        with open(os.path.join(r.gpio_dir, "pins.json"), "w") as fh:
            fh.write("nope")
        for name, args in (("gpio.pins", {}), ("gpio.header", {}), ("gpio.assign", output()), ("gpio.unassign", {"name": "x"})):
            res = r.call(name, args)
            self.assertEqual((res.cls, res.code), ("failed", "write_failed"), name)
            self.assertIn("pins.json", res.message)

    def test_the_caller_is_recorded_on_the_assignment(self):
        r = self.rig()
        r.call("gpio.assign", output(), caller="the-ai")
        self.assertEqual(r.call("gpio.pins").data["pins"][0]["updated_by"], "the-ai")


class OverHttp(Base):
    def client(self, r):
        from starlette.testclient import TestClient
        c = TestClient(r.app)
        self.addCleanup(c.close)
        return c

    def test_statuses(self):
        r = self.rig()
        c = self.client(r)
        self.assertEqual(c.post("/api/gpio/assign", json=output()).status_code, 200)
        self.assertEqual(c.post("/api/gpio/assign", json=output(name="other")).status_code, 409)           # pin_taken
        self.assertEqual(c.post("/api/gpio/assign", json=output(name="o2", gpio=0)).status_code, 422)      # pin_reserved
        self.assertEqual(c.post("/api/gpio/assign", json=output(name="o3", gpio=30)).status_code, 422)     # pin_not_on_header
        self.assertEqual(c.post("/api/gpio/assign", json=output(name="o4", gpio=18, voltage_v=5)).status_code, 422)
        self.assertEqual(c.post("/api/gpio/assign", json=output(name="o5", gpio=19, purpose="")).status_code, 400)
        self.assertEqual(c.post("/api/gpio/unassign", json={"name": "nope"}).status_code, 404)
        self.assertEqual(c.get("/api/gpio/pins").json()["data"]["pins"][0]["name"], "dut-reset")
        self.assertEqual(len(c.get("/api/gpio/header").json()["data"]["header"]), 40)

    def test_no_routes_exist_without_the_capability(self):
        c = self.client(self.rig(model=b"x\0"))
        self.assertEqual(c.get("/api/gpio/pins").status_code, 404)

    def test_every_assignment_is_in_the_activity_log_with_its_arguments(self):
        r = self.rig()
        c = self.client(r)
        c.post("/api/gpio/assign", json=output())
        rows = [e.payload for e in r.host.streams.activity.since(0).events if e.payload["command"] == "gpio.assign"]
        self.assertEqual([(p["args"]["gpio"], p["args"]["service"], p["class"]) for p in rows], [(17, "programmer", "ok")])


class Gate(unittest.TestCase):
    """Nothing in the gpio area may touch a pin: no gpiod, no gpiochip, no device open, no process (gpioset, pinctrl)."""
    TOUCH = re.compile(r"gpiod|gpiochip|/dev/gpio|/dev/mem|os\.open\(|subprocess|\bpinctrl\b|gpioset|gpioget|\bioctl\b|\bfcntl\b|\bmmap\b")

    @staticmethod
    def code_of(path):
        """The file's code as the parser sees it: comments are gone and so are docstrings (prose may say `gpiochip`), but
        every other string stays, so a real "/dev/gpiochip0" in code is still caught."""
        import ast
        with open(path) as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                    node.body = node.body[1:] or [ast.Pass()]
        return ast.unparse(tree)

    def scan(self, root):
        found = []
        for base, _dirs, names in os.walk(root):
            for name in names:
                if name.endswith(".py"):
                    for n, line in enumerate(self.code_of(os.path.join(base, name)).splitlines(), 1):
                        if self.TOUCH.search(line):
                            found.append("%s:%d: %s" % (name, n, line.strip()))
        return found

    def test_the_package_touches_nothing(self):
        files = [n for _b, _d, ns in os.walk(os.path.join(ROOT, "nrf_workbench", "gpio")) for n in ns if n.endswith(".py")]
        self.assertGreaterEqual(len(files), 4, "the scan found too few files to mean anything")
        self.assertEqual(self.scan(os.path.join(ROOT, "nrf_workbench", "gpio")), [])

    def test_the_gate_goes_red_on_an_offender(self):
        with tempfile.TemporaryDirectory() as d:
            for line in ("import gpiod", "x = open('/dev/gpiochip0')", "import subprocess", "os.open('x', 0)", "mmap.mmap(0, 1)"):
                with open(os.path.join(d, "bad.py"), "w") as fh:
                    fh.write(line + "\n")
                self.assertTrue(self.scan(d), line)
            with open(os.path.join(d, "bad.py"), "w") as fh:
                fh.write("x = 1  # a comment may say gpiod\n")
            self.assertEqual(self.scan(d), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
