#!/usr/bin/env python3
"""Tests for the Pi's own UART as a debug link (docs/specs/pi-uart.md): the adapter row, connecting a device to it, the pins a
connection holds, and the setup report. A fake /dev, /proc and boot folder; no hardware.
Run:  python3 tests/wiring/test_pi_uart.py
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
from nrf_workbench.core.registry import Context, Registry  # noqa: E402
from nrf_workbench.devices import commands as device_commands  # noqa: E402
from nrf_workbench.devices import profile  # noqa: E402
from nrf_workbench.gpio import commands as gpio_commands  # noqa: E402
from nrf_workbench.wiring import commands as wiring_commands  # noqa: E402
from nrf_workbench.wiring import detect  # noqa: E402

PROFILE = profile.load(os.path.join(ROOT, "profiles", "rak4631.yaml"))
PI = "uart:ttyAMA0"
CAPS = {"sysfs", "wiring", "gpio"}


class Host(object):
    def __init__(self, tree):
        self.sysfs, self.devroot, self.proc = tree.sysfs, tree.dev, tree.proc
        self.config = {"paths": {"wiring": os.path.join(tree.root, "wiring"), "gpio": os.path.join(tree.root, "gpio")}}
        self.profiles, self.units, self.hooks = [PROFILE], {}, {}
        self.console, self.console_links = None, []
        self.bootdir = os.path.join(tree.root, "boot")
        os.makedirs(self.bootdir)
        self.services = {}

    def service(self, name, factory):
        if name not in self.services:
            self.services[name] = factory()
        return self.services[name]


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.t.app_board()
        self.t.node("ttyACM0", 166, 0)
        self.host = Host(self.t)
        self.reg = Registry()
        wiring_commands.register(self.reg, self.host)
        gpio_commands.register(self.reg, self.host)
        device_commands.register(self.reg, self.host)

    def uart_on(self):
        return self.t.node("ttyAMA0", 204, 64)

    def call(self, name, args=None):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities=set(CAPS))

    def connect(self, **kw):
        args = {"device": SERIAL, "intent": "log", "adapter": PI, "baud": 115200, "send": False}
        args.update(kw)
        return self.call("wiring.connect", args)

    def assign(self, gpio, service="power", name=None):
        return self.call("gpio.assign", {"name": name or "pin-%d" % gpio, "gpio": gpio, "direction": "input", "service": service,
                                         "action": "x", "purpose": "p", "active_low": False, "voltage_v": 3.3, "voltage_verified": False,
                                         "pull": "none"})

    def write_boot(self, config="", cmdline="console=tty1 root=/dev/mmcblk0p2"):
        for name, text in (("config.txt", config), ("cmdline.txt", cmdline)):
            with open(os.path.join(self.host.bootdir, name), "w") as fh:
                fh.write(text)


class AdapterRow(Base):
    def rows(self):
        return detect.pi_uarts(self.t.dev, self.t.proc, {})

    def test_with_no_uart_node_the_row_says_it_is_not_enabled_and_where_to_look(self):
        (row,) = self.rows()
        self.assertEqual((row["serial"], row["ids"], row["node"], row["problem_code"]), (PI, "pi-uart", None, "adapter_absent"))
        self.assertIn("not enabled", row["problem"])
        self.assertIn("wiring.uart_status", row["problem"])

    def test_with_the_node_it_is_a_clean_row_that_names_the_header_pins(self):
        node = self.uart_on()
        (row,) = self.rows()
        self.assertEqual((row["node"], row["problem"], row["problem_code"]), (node, "", None))
        self.assertIn("header pins 8 and 10", row["name"])

    def test_something_holding_the_node_is_a_port_held_problem_naming_the_process(self):
        node = self.uart_on()
        self.t.hold(777, node, "/sbin/agetty ttyAMA0")
        (row,) = self.rows()
        self.assertEqual(row["problem_code"], "port_held")
        self.assertIn("pid 777", row["problem"])

    def test_pins_given_to_another_service_are_a_pin_taken_problem_and_the_console_service_is_not(self):
        self.uart_on()
        (row,) = detect.pi_uarts(self.t.dev, self.t.proc, {14: "power"})
        self.assertEqual((row["problem_code"], "power" in row["problem"]), ("pin_taken", True))
        (row,) = detect.pi_uarts(self.t.dev, self.t.proc, {14: "console", 15: "console"})
        self.assertEqual(row["problem"], "")

    def test_an_esp32s_built_in_usb_is_never_offered_as_a_debug_adapter(self):
        self.t.usb("1-4", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8", tty="ttyACM2", majmin="166:2")
        self.t.node("ttyACM2", 166, 2)
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="5B1F007437", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        serials = [a["serial"] for a in self.call("wiring.show").data["adapters"]]
        self.assertIn("5B1F007437", serials)
        self.assertNotIn("64:E8", serials)

    def test_the_wiring_page_offers_it_with_the_usb_adapters(self):
        self.uart_on()
        self.t.usb("1-4", ("1a86", "55d4"), "USB Single Serial", serial="5B1F007437", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        serials = [a["serial"] for a in self.call("wiring.show").data["adapters"]]
        self.assertEqual(serials, ["5B1F007437", PI])


class Connecting(Base):
    def setUp(self):
        Base.setUp(self)
        self.uart_on()

    def test_a_device_is_connected_to_the_pi_uart_and_the_console_gets_a_pi_uart_link(self):
        res = self.connect(name="rak-uart")
        self.assertTrue(res.ok, res.message)
        self.assertEqual((res.data["adapter"], res.data["pins"], res.data["device"]), ({"ids": ["pi-uart"], "serial": PI}, [14, 15], SERIAL))
        (link,) = self.host.console_links
        self.assertEqual((link.name, link.backend, link.port, link.device, link.usable, link.baud),
                         ("rak-uart", "pi-uart", "ttyAMA0", SERIAL, True, 115200))
        self.assertEqual(link.can_send()[1], "link_rx_only", "receive-only by default")

    def test_sending_on_it_asks_for_no_measured_voltage_because_the_pi_is_3v3_by_design(self):
        res = self.connect(name="rak-uart", send=True)
        self.assertTrue(res.ok, res.message)
        (link,) = self.host.console_links
        self.assertEqual(link.can_send(), (True, "ok", ""))
        self.assertIsNone(res.data["voltage_measured"])

    def test_a_voltage_the_caller_passes_for_the_pi_uart_is_not_recorded_as_a_measurement(self):
        res = self.connect(name="rak-uart", send=True, voltage_measured=True)
        self.assertTrue(res.ok, res.message)
        self.assertIsNone(res.data["voltage_measured"], "nobody measured it: it is 3.3 V by design, which is a different fact")

    def test_a_usb_adapter_still_needs_its_voltage_measured_to_send(self):
        self.t.usb("1-4", ("1a86", "55d4"), "USB Single Serial", serial="5B1F007437", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        from nrf_workbench.core.registry import UsageError
        with self.assertRaises(UsageError):
            self.connect(adapter="5B1F007437", send=True)

    def test_the_pi_uart_belongs_to_one_connection(self):
        self.connect(name="one")
        res = self.connect(name="two")
        self.assertEqual((res.cls, res.code, res.evidence["held_by"]), ("refused", "adapter_taken", "one"))

    def test_without_the_node_it_is_refused_and_says_how_to_enable_it(self):
        os.unlink(os.path.join(self.t.dev, "ttyAMA0"))
        res = self.connect()
        self.assertEqual((res.cls, res.code), ("refused", "adapter_absent"))
        self.assertIn("wiring.uart_status", res.message)

    def test_a_process_holding_the_node_is_a_port_held_refusal(self):
        self.t.hold(777, os.path.join(self.t.dev, "ttyAMA0"), "/sbin/agetty")
        self.assertEqual(self.connect().code, "port_held")

    def test_pins_the_pin_map_gave_to_another_service_are_a_pin_taken_refusal(self):
        self.assertTrue(self.assign(14, service="power").ok)
        res = self.connect()
        self.assertEqual((res.cls, res.code), ("refused", "pin_taken"))
        self.assertEqual(self.host.console_links, [])

    def test_pins_assigned_to_the_console_service_do_not_block_it(self):
        self.assertTrue(self.assign(14, service="console").ok)
        self.assertTrue(self.connect().ok)

    def test_removing_the_connection_removes_the_link_and_frees_the_pins(self):
        self.connect(name="rak-uart")
        self.assertEqual(self.assign(15).code, "pin_taken")
        self.assertTrue(self.call("wiring.disconnect", {"name": "rak-uart"}).ok)
        self.assertEqual(self.host.console_links, [])
        self.assertTrue(self.assign(15).ok)

    def test_changing_the_baud_keeps_the_pi_uart(self):
        self.connect(name="rak-uart")
        res = self.call("wiring.change", {"name": "rak-uart", "baud": 9600})
        self.assertTrue(res.ok, res.message)
        self.assertEqual((self.host.console_links[0].baud, self.host.console_links[0].port), (9600, "ttyAMA0"))


class HeldPins(Base):
    def test_a_connection_holds_both_pins_and_names_itself_in_the_refusal(self):
        self.uart_on()
        self.connect(name="rak-uart")
        for pin in (14, 15):
            res = self.assign(pin)
            self.assertEqual((res.cls, res.code, res.evidence["held_by"]), ("refused", "pin_taken", "rak-uart"), pin)
        self.assertTrue(self.assign(16).ok, "a pin it does not hold is still free")

    def test_with_no_wiring_folder_nothing_is_held_and_nothing_breaks(self):
        self.host.config["paths"]["wiring"] = None
        self.assertEqual(wiring_commands.pins_held(self.host), {})


class UartStatus(Base):
    def status(self):
        res = self.call("wiring.uart_status")
        self.assertTrue(res.ok, res.message)
        return res

    def done(self, res):
        return [s["done"] for s in res.data["steps"]]

    def test_a_pi_as_it_ships_has_nothing_done_and_needs_a_reboot(self):
        self.write_boot(config="# dtparam=i2c_arm=on\n", cmdline="console=serial0,115200 console=tty1 root=/dev/mmcblk0p2")
        res = self.status()
        self.assertEqual((res.data["enabled"], res.data["on_header_pins"], res.data["kernel_console_on_it"], res.data["reboot_needed"]),
                         (False, False, True, True))
        self.assertEqual(self.done(res), [False, False, False, True])
        self.assertIn("not ready", res.message)
        self.assertEqual(res.data["pins"], {"tx_bcm": 14, "rx_bcm": 15, "tx_header": 8, "rx_header": 10})

    def test_with_no_serial0_at_all_it_says_none_and_not_a_path(self):
        self.write_boot()
        self.assertIsNone(self.status().data["serial0_is"])

    def test_a_commented_line_is_not_a_done_step(self):
        self.write_boot(config="#enable_uart=1\n#dtoverlay=disable-bt\n")
        self.assertEqual(self.done(self.status())[:2], [False, False])

    def test_a_pi_set_up_for_it_is_ready_and_needs_no_reboot(self):
        self.uart_on()
        os.symlink("ttyAMA0", os.path.join(self.t.dev, "serial0"))
        self.write_boot(config="enable_uart=1\ndtoverlay=disable-bt\n")
        res = self.status()
        self.assertEqual((res.data["enabled"], res.data["on_header_pins"], res.data["serial0_is"], res.data["reboot_needed"]),
                         (True, True, "ttyAMA0", False))
        self.assertEqual(self.done(res), [True, True, True, True])
        self.assertIn("is ready", res.message)

    def test_the_good_uart_on_the_pins_by_another_overlay_counts_as_the_bluetooth_step_done(self):
        """serial0 pointing at ttyAMA0 is what matters, however it got there (disable-bt, miniuart-bt)."""
        self.uart_on()
        os.symlink("ttyAMA0", os.path.join(self.t.dev, "serial0"))
        self.write_boot(config="enable_uart=1\ndtoverlay=miniuart-bt\n")
        self.assertEqual(self.done(self.status())[:2], [True, True])

    def test_serial0_on_the_mini_uart_means_the_good_uart_is_bluetooths(self):
        self.uart_on()
        self.t.node("ttyS0", 4, 64)
        os.symlink("ttyS0", os.path.join(self.t.dev, "serial0"))
        self.write_boot(config="enable_uart=1\n")
        res = self.status()
        self.assertEqual((res.data["serial0_is"], res.data["on_header_pins"]), ("ttyS0", False))
        self.assertFalse(self.done(res)[1])
        self.assertIn("not ready", res.message)

    def test_a_getty_holding_the_node_is_named_and_is_not_done(self):
        node = self.uart_on()
        os.symlink("ttyAMA0", os.path.join(self.t.dev, "serial0"))
        self.write_boot(config="enable_uart=1\ndtoverlay=disable-bt\n")
        self.t.hold(777, node, "/sbin/agetty -o -p -- \\u --keep-baud 115200 ttyAMA0")
        res = self.status()
        self.assertEqual([h["pid"] for h in res.data["holders"]], [777])
        self.assertEqual(self.done(res), [True, True, True, False])
        self.assertIn("not ready", res.message)

    def test_it_reports_the_pin_map_and_the_connection_that_holds_each_pin(self):
        self.uart_on()
        self.connect(name="rak-uart")
        res = self.status()
        self.assertEqual(res.data["held_by_connection"], {"14": "rak-uart", "15": "rak-uart"})

    def test_it_changes_nothing_on_disk(self):
        self.write_boot(config="a\n", cmdline="console=serial0,115200\n")
        before = [open(os.path.join(self.host.bootdir, n)).read() for n in ("config.txt", "cmdline.txt")]
        self.status()
        self.assertEqual([open(os.path.join(self.host.bootdir, n)).read() for n in ("config.txt", "cmdline.txt")], before)

    def test_the_command_needs_the_wiring_folder_and_a_pi(self):
        names = lambda caps: [c.name for c in self.reg.commands(caps) if c.name == "wiring.uart_status"]  # noqa: E731
        self.assertEqual((names({"wiring"}), names({"gpio"}), names({"wiring", "gpio"})), ([], [], ["wiring.uart_status"]))


if __name__ == "__main__":
    unittest.main()
