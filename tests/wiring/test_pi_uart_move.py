#!/usr/bin/env python3
"""Tests for moving the Pi's UART off pins 8 and 10 and giving Bluetooth back (docs/specs/pi-uart-move.md): the PL011 table read from hardware,
the pins the bench reserves by configuration, and the Bluetooth status. Over the fake sysfs, /dev and /proc of test_pi_uart.
Run:  python3 tests/wiring/test_pi_uart_move.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from nrf_workbench.bench import bluetooth, overview  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.gpio import header, pinmap  # noqa: E402
from nrf_workbench.wiring import detect, piuart  # noqa: E402
from test_pi_uart import Base  # noqa: E402


class Table(Base):
    def test_each_pl011_is_found_by_its_physical_address(self):
        expected = {"fe201000": ("uart0", 14, 15, "a0"), "fe201400": ("uart2", 0, 1, "a4"), "fe201600": ("uart3", 4, 5, "a4"),
                    "fe201800": ("uart4", 8, 9, "a4"), "fe201a00": ("uart5", 12, 13, "a4")}
        self.assertEqual(piuart.UARTS, expected)
        for n, (base, (name, tx, rx, alt)) in enumerate(sorted(expected.items())):
            port = "ttyAMA%d" % n
            self.t.node(port, 204, 64 + n, base=base)
            self.assertEqual(piuart.uart_of(self.t.sysfs, port), {"name": name, "tx": tx, "rx": rx, "alt": alt, "port": port}, base)
            self.assertEqual(piuart.pins_of(self.t.sysfs, port), (tx, rx))

    def test_the_port_name_decides_nothing(self):
        self.t.node("ttyAMA0", 204, 64, base="fe201a00")                 # ttyAMA0 can be uart5 when Bluetooth has uart0
        self.assertEqual(piuart.pins_of(self.t.sysfs, "ttyAMA0"), (12, 13))

    def test_an_unknown_device_or_no_device_is_not_a_uart(self):
        self.t.node("ttyAMA1", 204, 65, base="deadbeef")
        self.assertIsNone(piuart.uart_of(self.t.sysfs, "ttyAMA1"))
        self.assertEqual(piuart.pins_of(self.t.sysfs, "ttyAMA1"), ())
        self.assertIsNone(piuart.uart_of(self.t.sysfs, "ttyAMA9"))
        self.assertIsNone(piuart.uart_of(os.path.join(self.t.root, "nowhere"), "ttyAMA0"))

    def test_a_plain_file_named_like_a_uart_is_not_a_node(self):
        self.t.node("ttyAMA0", 204, 64)
        os.unlink(os.path.join(self.t.dev, "ttyAMA0"))
        open(os.path.join(self.t.dev, "ttyAMA0"), "w").close()           # sysfs still names fe201000, but this is not a character device
        self.assertEqual(detect.pi_ports(self.t.sysfs, self.t.dev), [])

    def test_the_header_pins_come_from_the_headers_own_table(self):
        self.assertEqual([piuart.header_text(tx, rx) for tx, rx in ((14, 15), (12, 13), (4, 5), (8, 9), (0, 1))],
                         ["header pins 8 and 10", "header pins 32 and 33", "header pins 7 and 29", "header pins 24 and 21", "header pins 27 and 28"])
        self.assertEqual((header.physical(14), header.physical(15), header.physical(12), header.physical(13)), (8, 10, 32, 33))
        self.assertIsNone(header.physical(99))


class Reserved(Base):
    def reserve(self, *pins):
        self.host.config["pins"]["reserved"] = list(pins)

    def test_a_reserved_pin_is_refused_by_the_pin_map_and_says_why(self):
        self.reserve(14, 15)
        res = self.assign(14)
        self.assertEqual((res.cls, res.code, res.evidence), ("refused", "pin_reserved", {"gpio": 14, "reserved_by": "configuration"}))
        self.assertIn("reserved on this bench", res.message)
        self.assertEqual(self.assign(15).code, "pin_reserved")
        self.assertTrue(self.assign(16).ok, "an ordinary pin is still free")

    def test_by_default_nothing_is_reserved_but_the_hat_pins(self):
        self.assertEqual(cfgmod.DEFAULTS["pins"], {"reserved": []})
        self.assertTrue(self.assign(14).ok)
        res = self.assign(0, name="hat")
        self.assertEqual((res.code, "HAT" in res.message), ("pin_reserved", True))

    def test_the_header_shows_a_reserved_pin_as_reserved(self):
        self.reserve(14, 15)
        rows = dict((r["pin"], r) for r in self.call("gpio.header").data["header"])
        self.assertEqual((rows[8]["class"], rows[10]["class"], rows[32]["class"]), ("reserved", "reserved", "free"))
        self.reserve()
        rows = dict((r["pin"], r) for r in self.call("gpio.header").data["header"])
        self.assertEqual((rows[8]["class"], rows[10]["class"]), ("bus", "bus"))

    def test_the_overview_header_shows_it_too(self):
        self.reserve(14, 15)
        rows = dict((r["pin"], r) for r in overview._header(self.host))
        self.assertEqual((rows[8]["class"], rows[10]["class"]), ("reserved", "reserved"))

    def test_a_pin_already_held_by_a_connection_stays_held_and_the_link_keeps_working(self):
        self.uart_on()
        self.assertTrue(self.connect(name="rak-uart0").ok)
        self.reserve(14, 15)
        rows = dict((r["pin"], r) for r in overview._header(self.host))
        self.assertEqual((rows[8]["class"], rows[8]["holder"]), ("reserved", "rak-uart0"))
        self.assertEqual(self.call("wiring.show").data["connections"][0]["pins"], [14, 15])

    def test_the_build_function_takes_the_list_and_the_class_follows_it(self):
        fields = {"name": "p", "gpio": 14, "direction": "input", "service": "s", "action": "a", "purpose": "x", "active_low": False,
                  "voltage_v": 3.3, "voltage_verified": False, "pull": "none"}
        self.assertEqual(pinmap.build(fields, {})["class"], "bus")
        with self.assertRaises(pinmap.Refused) as cm:
            pinmap.build(fields, {}, [14])
        self.assertEqual(cm.exception.code, "pin_reserved")
        self.assertEqual(pinmap.build(dict(fields, gpio=16, name="q"), {}, [14])["class"], "free")

    def test_the_configuration_is_checked(self):
        for bad in ("14", [14, 14], [28], [-1], [True], ["14"], [1.5]):
            _, problems = cfgmod.load(env={}, overrides={"pins": {"reserved": bad}})
            self.assertTrue(any("pins.reserved" in p for p in problems), (bad, problems))
        for good in ([], [14, 15], [0, 27]):
            self.assertEqual(cfgmod.load(env={}, overrides={"pins": {"reserved": good}})[1], [], good)

    def test_an_unknown_key_under_pins_is_reported(self):
        _, problems = cfgmod.load(env={}, overrides={"pins": {"reservd": [14]}})
        self.assertTrue(any("reservd" in p for p in problems), problems)


class Bluetooth(Base):
    def adapter(self, name="hci0"):
        os.makedirs(os.path.join(self.t.sysfs, "class", "bluetooth", name))

    def rfkill(self, name, kind, soft="0", hard="0"):
        d = os.path.join(self.t.sysfs, "class", "rfkill", name)
        os.makedirs(d)
        for fname, value in (("type", kind), ("soft", soft), ("hard", hard)):
            with open(os.path.join(d, fname), "w") as fh:
                fh.write(value + "\n")

    def test_no_adapter_says_why_in_the_words_of_the_boot_overlay(self):
        st = bluetooth.status(self.host)
        self.assertEqual((st["present"], st["adapters"], st["blocked"]), (False, [], None))
        self.assertIn("disable-bt", st["why"])
        self.assertIn("wiring.uart_status", st["why"])

    def test_an_adapter_is_present(self):
        self.adapter()
        st = bluetooth.status(self.host)
        self.assertEqual((st["present"], st["adapters"], st["why"]), (True, ["hci0"], "adapter hci0"))

    def test_only_hci_names_count(self):
        self.adapter("hci0")
        os.makedirs(os.path.join(self.t.sysfs, "class", "bluetooth", "hci0:64"))     # a connection handle: not an adapter
        os.makedirs(os.path.join(self.t.sysfs, "class", "bluetooth", "hcix"))
        self.assertEqual(bluetooth.status(self.host)["adapters"], ["hci0"])

    def test_a_blocked_bluetooth_radio_is_said_and_a_blocked_wifi_radio_is_not_taken_for_it(self):
        self.adapter()
        self.rfkill("rfkill0", "wlan", soft="1")
        self.assertIsNone(bluetooth.status(self.host)["blocked"])
        self.rfkill("rfkill1", "bluetooth", soft="1")
        st = bluetooth.status(self.host)
        self.assertEqual((st["blocked"], st["why"]), (True, "blocked by rfkill"))

    def test_a_hard_block_counts_and_an_unblocked_radio_is_false(self):
        self.rfkill("rfkill1", "bluetooth", hard="1")
        self.assertTrue(bluetooth.status(self.host)["blocked"])

    def test_the_overview_carries_it(self):
        self.adapter()
        self.host.capabilities = {"wiring", "gpio"}
        self.assertEqual(overview.overview(self.host)["bluetooth"]["adapters"], ["hci0"])


if __name__ == "__main__":
    unittest.main()
