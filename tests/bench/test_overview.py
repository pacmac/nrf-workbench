#!/usr/bin/env python3
"""Tests for bench.overview (docs/specs/dashboard-redesign.md): one read that says what is on the bench. Built through the real
service composition over a fake sysfs, /dev and /proc, with a fake console for the links' state. What matters: each device kind is one row
with the right state, a board the bench knows and cannot see is listed as absent (so connected versus not is readable), nothing is
probed, and a store that cannot be read costs its own section only.
Run:  python3 tests/bench/test_overview.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.registry import Context  # noqa: E402

OTHER = "OTHERBOARD0002"
CAM = "5D52ADF916"


class FakeConsole(object):
    def __init__(self):
        self.states = {}

    def state(self, name):
        return self.states.get(name)

    def set_links(self, links):
        pass


class Base(unittest.TestCase):
    pi = True

    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-ov-")
        paths = {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console"),
                 "firmware": os.path.join(self.tmp, "fw")}
        if self.pi:
            paths["gpio"] = os.path.join(self.tmp, "gpio")
            os.makedirs(os.path.join(self.t.proc, "device-tree"))
            with open(os.path.join(self.t.proc, "device-tree", "model"), "wb") as fh:
                fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")
        with open(os.path.join(self.t.proc, "uptime"), "w") as fh:
            fh.write("12345.67 99999.0\n")
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": paths, "streams": {"console": {"retain": 50, "max_age_days": 1}, "events": {"retain": 5, "max_age_days": 1},
                                        "activity": {"retain": 5, "max_age_days": 1}}})
        self.assertEqual(problems, [])
        self.host, self.reg, self.caps = service.build_host(cfg, self.t.sysfs, self.t.dev, self.t.proc,
                                                             {"tool": "/x", "esp_tool": "/x"})
        self.host.console = FakeConsole()

    def call(self, name, args=None):
        return self.reg.call(name, args or {}, Context(self.host, "t", None), capabilities=set(self.caps))

    def overview(self):
        res = self.call("bench.overview")
        self.assertTrue(res.ok, res.message)
        return res.data

    def rows(self, **kw):
        return [d for d in self.overview()["devices"] if all(d.get(k) == v for k, v in kw.items())]


class Devices(Base):
    def test_an_attached_nrf_board_is_connected_and_carries_its_name_and_connections(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        (d,) = self.rows(kind="nrf")
        self.assertEqual((d["state"], d["name"], d["label"], d["mode"], d["model"], d["serial"]),
                         ("connected", "left-rak", "left-rak", "app", "rak4631", SERIAL))

    def test_a_board_in_both_modes_is_one_row_needing_attention(self):
        self.t.app_board("1-3", "ttyACM0", "166:0")
        self.t.bootloader_board("1-4", "ttyACM1", "166:1")
        (d,) = self.rows(kind="nrf")
        self.assertEqual((d["state"], d["why"]), ("attention", "attached in both modes at once"))

    def test_a_named_board_that_is_unplugged_is_listed_as_absent_so_connected_versus_not_is_readable(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.t.unplug("1-3")
        (d,) = self.rows(kind="nrf")
        self.assertEqual((d["state"], d["label"], d["why"]), ("absent", "left-rak", "not attached"))

    def test_a_board_a_connection_names_is_known_even_if_it_was_never_named(self):
        wiring = self.host.config["paths"]["wiring"]
        os.makedirs(wiring)
        import json
        with open(os.path.join(wiring, "connections.json"), "w") as fh:
            json.dump({"connections": {"rak-log": {"name": "rak-log", "intent": "log", "device": "GHOST0001",
                                                    "adapter": {"ids": ["1a86:55d4"], "serial": "DONGLE1"}, "baud": 115200,
                                                    "send": False, "voltage_measured": None}}}, fh)
        (d,) = self.rows(kind="nrf")
        self.assertEqual((d["state"], d["label"], d["connections"]), ("absent", "GHOST0001", ["rak-log"]))

    def test_an_esp32_target_is_connected_absent_or_needs_attention(self):
        self.t.usb("1-2", ("0403", "6001"), "M5stack", serial=CAM, tty="ttyUSB0", majmin="188:0")
        self.t.node("ttyUSB0", 188, 0)
        self.assertTrue(self.call("esp32.declare", {"name": "cam", "serial": CAM}).ok)
        (d,) = self.rows(kind="esp32")
        self.assertEqual((d["state"], d["name"], d["node"], d["page"]), ("connected", "cam", os.path.join(self.t.dev, "ttyUSB0"), "firmware"))
        self.host.console.states["cam"] = {"state": "error", "reason": "cannot open", "lines": 0}
        self.assertEqual(self.rows(kind="esp32")[0]["state"], "attention")
        self.assertEqual(self.rows(kind="esp32")[0]["why"], "cannot open")
        self.t.unplug("1-2")
        self.assertEqual((self.rows(kind="esp32")[0]["state"], self.rows(kind="esp32")[0]["why"]), ("absent", "not attached"))

    def test_an_esp32_nobody_declared_needs_attention_and_says_what_to_do(self):
        self.t.usb("1-4", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8", tty="ttyACM0", majmin="166:0")
        self.t.node("ttyACM0", 166, 0)
        (d,) = self.rows(kind="esp32")
        self.assertEqual((d["state"], d["undeclared"]), ("attention", True))
        self.assertIn("Firmware page", d["why"])
        self.assertEqual(self.rows(kind="adapter"), [], "a built-in-USB ESP32 is not also listed as an adapter")

    def test_a_free_adapter_is_listed_and_one_a_target_uses_is_not(self):
        self.t.usb("1-2", ("0403", "6001"), "M5stack", serial=CAM, tty="ttyUSB0", majmin="188:0")
        self.t.node("ttyUSB0", 188, 0)
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="DONGLE1", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        self.call("esp32.declare", {"name": "cam", "serial": CAM})
        (a,) = self.rows(kind="adapter")
        self.assertEqual((a["serial"], a["state"], a["why"], a["page"]), ("DONGLE1", "connected", "free: not wired to a device yet", "wiring"))

    def test_an_adapter_a_connection_uses_is_that_connection_and_not_also_a_free_adapter(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="DONGLE1", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        self.assertEqual(len(self.rows(kind="adapter")), 1)
        self.assertTrue(self.call("wiring.connect", {"device": SERIAL, "intent": "log", "adapter": "DONGLE1", "baud": 115200,
                                                     "send": False, "name": "rak-log"}).ok)
        self.assertEqual(self.rows(kind="adapter"), [])
        self.assertEqual(self.overview()["connections"][0]["adapter_serial"], "DONGLE1")
        self.t.node("ttyAMA0", 204, 64)
        self.assertTrue(self.call("wiring.connect", {"device": OTHER, "intent": "log", "adapter": "uart:ttyAMA0", "baud": 9600,
                                                     "send": False, "name": "second"}).ok)
        self.assertEqual(self.rows(kind="adapter"), [], "the Pi UART a connection uses is not listed as free either")

    def test_the_pi_uart_is_a_free_adapter_when_enabled_and_absent_from_the_list_when_not(self):
        self.assertEqual(self.rows(kind="adapter"), [])
        self.t.node("ttyAMA0", 204, 64)
        (a,) = self.rows(kind="adapter")
        self.assertEqual((a["serial"], a["state"]), ("uart:ttyAMA0", "connected"))

    def test_rows_are_ordered_attention_first_then_connected_then_absent(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.t.usb("1-4", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        self.call("devices.name", {"serial": SERIAL, "name": "a"})
        wiring = self.host.config["paths"]["wiring"]
        os.makedirs(wiring, exist_ok=True)
        import json
        with open(os.path.join(wiring, "device-names.json"), "w") as fh:
            json.dump({"names": {SERIAL: "a", "GONE0001": "gone"}}, fh)
        self.assertEqual([d["state"] for d in self.overview()["devices"]], ["attention", "connected", "absent"])

    def test_the_counts_add_up_to_the_rows(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.t.usb("1-4", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        c = self.overview()["counts"]
        self.assertEqual((c["devices"], c["connected"], c["attention"], c["absent"]), (2, 1, 1, 0))
        self.assertEqual(c["connected"] + c["attention"] + c["absent"], c["devices"])


class Connections(Base):
    def setUp(self):
        Base.setUp(self)
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.t.node("ttyAMA0", 204, 64)

    def connect(self, name="rak-log"):
        res = self.call("wiring.connect", {"device": SERIAL, "intent": "log", "adapter": "uart:ttyAMA0", "baud": 115200, "send": False,
                                           "name": name})
        self.assertTrue(res.ok, res.message)

    def test_a_connection_reports_its_state_from_the_console_in_the_benchs_words(self):
        self.connect()
        for raw, state in (("open", "listening"), ("absent", "absent"), ("held", "attention"), ("error", "attention")):
            self.host.console.states["rak-log"] = {"state": raw, "reason": "r", "lines": 7, "last_byte_at": 5.0}
            (c,) = self.overview()["connections"]
            self.assertEqual(c["state"], state, raw)
        self.assertEqual((c["lines"], c["baud"], c["rx_only"], c["device"], c["page"]), (7, 115200, True, SERIAL, "wiring"))
        self.assertEqual((c["via"], c["pins"]), ("Pi UART (header pins 8 and 10)", [14, 15]))

    def test_the_device_row_names_the_connections_that_use_it_and_a_named_device_shows_its_name_there(self):
        self.connect()
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.host.console.states["rak-log"] = {"state": "open", "reason": "", "lines": 0, "last_byte_at": None}
        (d,) = self.rows(kind="nrf")
        self.assertEqual(d["connections"], ["rak-log"])
        self.assertEqual(self.overview()["connections"][0]["device_name"], "left-rak")

    def test_with_no_console_a_connection_says_so_and_is_absent_not_listening(self):
        self.connect()
        self.host.console = None
        (c,) = self.overview()["connections"]
        self.assertEqual((c["state"], c["why"]), ("absent", "the console is not running"))

    def test_the_counts_say_how_many_links_listen(self):
        self.connect()
        self.host.console.states["rak-log"] = {"state": "open", "reason": "", "lines": 0, "last_byte_at": None}
        c = self.overview()["counts"]
        self.assertEqual((c["links"], c["listening"], c["links_attention"]), (1, 1, 0))


class PiParts(Base):
    def test_the_header_marks_who_holds_each_pin(self):
        self.t.node("ttyAMA0", 204, 64)
        self.t.app_board()
        self.call("wiring.connect", {"device": SERIAL, "intent": "log", "adapter": "uart:ttyAMA0", "baud": 115200, "send": False,
                                     "name": "rak-log"})
        self.call("gpio.assign", {"name": "i2c-sda", "gpio": 2, "direction": "input", "service": "i2c", "action": "sda", "purpose": "p",
                                  "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"})
        h = dict((r["pin"], r) for r in self.overview()["header"])
        self.assertEqual(len(h), 40)
        self.assertEqual((h[8]["holder"], h[8]["holder_kind"], h[8]["service"]), ("rak-log", "connection", "console"))
        self.assertEqual((h[3]["holder"], h[3]["holder_kind"], h[3]["service"]), ("i2c-sda", "pin-map", "i2c"))
        self.assertEqual((h[5]["holder"], h[1]["kind"], h[6]["kind"]), (None, "power", "ground"))

    def test_i2c_says_whether_a_bus_exists_and_whether_its_pins_are_claimed(self):
        self.assertEqual(self.overview()["i2c"], {"enabled": False, "buses": [], "pins_claimed": False})
        open(os.path.join(self.t.dev, "i2c-1"), "w").close()
        for gpio in (2, 3):
            self.call("gpio.assign", {"name": "i2c-%d" % gpio, "gpio": gpio, "direction": "input", "service": "i2c", "action": "x",
                                      "purpose": "p", "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"})
        i2c = self.overview()["i2c"]
        self.assertEqual((i2c["enabled"], [b["bus"] for b in i2c["buses"]], i2c["pins_claimed"]), (True, [1], True))

    def test_the_host_facts_come_from_proc(self):
        h = self.overview()["host"]
        self.assertEqual((h["model"], h["uptime_s"]), ("Raspberry Pi 4 Model B Rev 1.4", 12345.67))
        self.assertIn("gpio", h["capabilities"])


class Projects(Base):
    MANIFEST = "name: demo\npurpose: try it\nmacros:\n  ping: [ping]\n"

    def put(self, text, name="demo"):
        import io

        class Body(io.BytesIO):
            def __init__(self, data):
                io.BytesIO.__init__(self, data)
                self.length = len(data)
        res = self.reg.call("project.put", {"project": name}, Context(self.host, "t", Body(text.encode())), capabilities=set(self.caps))
        self.assertTrue(res.ok, res.message)

    def test_a_project_with_a_manifest_is_listed_with_its_state_and_a_folder_with_none_is_not(self):
        self.host.registry = self.reg
        self.call("firmware.project_new", {"project": "fw-only"})
        self.assertEqual(self.overview()["projects"], [])
        self.put(self.MANIFEST)
        (p,) = self.overview()["projects"]
        self.assertEqual((p["name"], p["purpose"], p["state"], p["word"], p["applied"]), ("demo", "try it", "absent", "not applied", False))
        self.assertEqual(self.reg.call("project.apply", {"project": "demo"}, Context(self.host, "t", None), capabilities=set(self.caps)).ok, True)
        (p,) = self.overview()["projects"]
        self.assertEqual((p["state"], p["word"], p["applied"]), ("connected", "ready", True))
        c = self.overview()["counts"]
        self.assertEqual((c["projects"], c["projects_attention"]), (1, 0))

    def test_a_broken_manifest_is_attention_and_an_unreadable_state_costs_the_projects_section_only(self):
        self.host.registry = self.reg
        os.makedirs(os.path.join(self.host.config["paths"]["wiring"], "projects", "demo"))
        with open(os.path.join(self.host.config["paths"]["wiring"], "projects", "demo", "project.yaml"), "w") as fh:
            fh.write("name: demo\nwirng: []\n")
        (p,) = self.overview()["projects"]
        self.assertEqual((p["state"], self.overview()["counts"]["projects_attention"]), ("attention", 1))
        self.put(self.MANIFEST)
        with open(os.path.join(self.host.config["paths"]["wiring"], "projects", "state.json"), "w") as fh:
            fh.write("{ nope")
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        d = self.overview()
        self.assertEqual((d["projects"], len(d["devices"])), ([], 1), "the projects section is lost, the devices are not")
        self.assertIn("projects", d["errors"])

    def test_without_a_registry_or_a_wiring_folder_the_section_is_none(self):
        self.host.registry = None
        self.assertIsNone(self.overview()["projects"])


class NotAPi(Base):
    pi = False

    def test_without_a_pin_map_the_pi_sections_are_none_with_the_reason_and_the_rest_still_works(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        d = self.overview()
        self.assertEqual((d["i2c"], d["header"]), (None, None))
        self.assertIn("pin map", d["absent"]["header"])
        self.assertEqual(len(d["devices"]), 1)
        self.assertIsNone(d["host"]["model"])


class Resilience(Base):
    def test_an_unreadable_connections_file_costs_its_section_only(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        wiring = self.host.config["paths"]["wiring"]
        os.makedirs(wiring)
        with open(os.path.join(wiring, "connections.json"), "w") as fh:
            fh.write("{ not json")
        d = self.overview()
        self.assertEqual(d["connections"], [])
        self.assertIn("connections", d["errors"])
        self.assertEqual(len(d["devices"]), 1)

    def test_an_unreadable_targets_file_costs_the_esp32_rows_only(self):
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        wiring = self.host.config["paths"]["wiring"]
        os.makedirs(wiring)
        with open(os.path.join(wiring, "esp32.json"), "w") as fh:
            fh.write("[1")
        d = self.overview()
        self.assertIn("esp32", d["errors"])
        self.assertEqual([x["kind"] for x in d["devices"]], ["nrf"])

    def test_it_is_a_read_that_needs_only_sysfs_and_opens_nothing(self):
        row = [c for c in self.reg.commands(set(self.caps)) if c.name == "bench.overview"][0]
        self.assertEqual((row.effect, list(row.requires)), ("read", ["sysfs"]))
        src = open(os.path.join(ROOT, "nrf_workbench", "bench", "overview.py")).read()
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
        self.assertNotIn("os.open(", code)
        self.assertNotIn("Port(", code)
        self.assertNotIn("serial.", code)


if __name__ == "__main__":
    unittest.main()
