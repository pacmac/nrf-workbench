#!/usr/bin/env python3
"""The tty node of a USB device is found for both sysfs layouts a Linux host uses (nrf_workbench/devices/identity.py).

A CDC-ACM device has <dev>/<dev>:1.0/tty/ttyACM0/dev; a usb-serial one (FTDI, CP210x, CH340) has
<dev>/<dev>:1.0/ttyUSB0/tty/ttyUSB0/dev, one level deeper. The second was never found until a TimerCam on an FTDI chip was
plugged into the bench Pi (2026-11-18) and was "not recognised": the walk stopped one level short. The fake tree here has both
layouts, written the way the Pi showed them.
Run:  python3 tests/devices/test_tty_layouts.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fakes import Tree  # noqa: E402
from nrf_workbench.devices import identity  # noqa: E402
from nrf_workbench.wiring import detect  # noqa: E402


class Layouts(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)

    def device(self, name):
        return next(d for d in identity.inventory(self.t.sysfs) if d["name"] == name)

    def test_a_usb_serial_device_with_its_class_entry_four_levels_down_is_found(self):
        self.t.usb("1-1.3", ("0403", "6001"), "M5stack", serial="5D52ADF916", manufacturer="Hades2001", tty="ttyUSB0", majmin="188:0")
        node = self.t.node("ttyUSB0", 188, 0)
        self.assertEqual(identity.node_for(self.device("1-1.3"), self.t.dev), (node, ""))

    def test_a_cdc_acm_device_is_still_found_at_three_levels(self):
        self.t.usb("1-1.4", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8", tty="ttyACM0", majmin="166:0")
        node = self.t.node("ttyACM0", 166, 0)
        self.assertEqual(identity.node_for(self.device("1-1.4"), self.t.dev), (node, ""))

    def test_a_hub_is_not_given_the_node_of_what_is_plugged_into_it(self):
        hub = self.t.usb("1-1", ("2109", "3431"), "USB2.0 Hub", serial=None, manufacturer="")
        child = os.path.join(hub, "1-1.3")                              # sysfs nests a child device inside its hub's directory
        os.makedirs(os.path.join(child, "1-1.3:1.0", "tty", "ttyACM0"))          # four levels below the HUB: inside the walk's reach
        for fname, val in (("idVendor", "303a"), ("idProduct", "1001")):
            with open(os.path.join(child, fname), "w") as fh:
                fh.write(val + "\n")
        with open(os.path.join(child, "1-1.3:1.0", "tty", "ttyACM0", "dev"), "w") as fh:
            fh.write("166:0\n")
        self.t.node("ttyACM0", 166, 0)
        node, why = identity.node_for(self.device("1-1"), self.t.dev)
        self.assertIsNone(node)
        self.assertIn("no tty interface", why)

    def test_a_usb_serial_adapter_is_offered_as_an_adapter_with_its_node_and_slot(self):
        self.t.usb("1-1.3", ("0403", "6001"), "M5stack", serial="5D52ADF916", manufacturer="Hades2001", tty="ttyUSB0", majmin="188:0")
        node = self.t.node("ttyUSB0", 188, 0)
        (a,) = detect.adapters(self.t.sysfs, self.t.dev, [])
        self.assertEqual((a["serial"], a["ids"], a["node"], a["slot"], a["problem"]), ("5D52ADF916", "0403:6001", node, "1-1.3", ""))


if __name__ == "__main__":
    unittest.main()
