#!/usr/bin/env python3
"""Tests for the project manifest validator (nrf_workbench/project/manifest.py; docs/specs/projects.md, "The manifest").

Pure: text in, a normalised manifest or a list of errors out. What matters: every field a command needs is stated and none is defaulted, an
unknown key is an error (a typo is never silently ignored), and each error names the field.
Run:  python3 tests/project/test_manifest.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.project import manifest as mf  # noqa: E402

GOOD = """
name: demo
purpose: develop the camera transport
requires: [i2c, pi-uart]
devices:
  camera: {esp32: timercam}
  alarm: {nrf: D953162EF5806B4B}
esp32:
  - {name: timercam, adapter: "5D52ADF916", baud: 115200}
wiring:
  - {name: alarm-log, device: alarm, adapter: "uart:ttyAMA0", baud: 115200, send: false}
pins:
  - {gpio: 2, name: i2c-sda, service: i2c, action: sda, direction: input, pull: up, purpose: "Pi I2C bus 1", voltage_v: 3.3, active_low: false, voltage_verified: false}
macros:
  ping: [ping, status]
firmware:
  camera: {id: 761f7205}
expect:
  camera: {console: "i2c slave 0x62"}
  i2c: {bus: 1, address: 0x62}
notes: |
  probe twice
"""


def check(text, name="demo"):
    return mf.validate(text, name)


def bad(text, fragment, name="demo"):
    m, errors = mf.validate(text, name)
    assert m is None, "expected errors for: %s" % fragment
    return [e for e in errors if fragment in e]


class Good(unittest.TestCase):
    def test_a_full_manifest_is_normalised_with_every_section(self):
        m, errors = check(GOOD)
        self.assertEqual(errors, [])
        self.assertEqual(m["devices"], {"camera": {"kind": "esp32", "key": "timercam"}, "alarm": {"kind": "nrf", "key": "D953162EF5806B4B"}})
        self.assertEqual(m["esp32"], [{"name": "timercam", "baud": 115200, "serial": "5D52ADF916"}])
        self.assertEqual(m["wiring"][0]["send"], False)
        self.assertEqual(m["macros"], {"ping": "ping\nstatus"})
        self.assertEqual(m["expect"], {"console": {"camera": "i2c slave 0x62"}, "i2c": {"bus": 1, "address": 0x62}})
        self.assertEqual((m["name"], m["purpose"], m["requires"]), ("demo", "develop the camera transport", ["i2c", "pi-uart"]))
        self.assertEqual(m["pins"][0]["pull"], "up")

    def test_every_section_is_optional(self):
        m, errors = check("name: demo\n")
        self.assertEqual((errors, m["devices"], m["wiring"], m["pins"], m["macros"]), ([], {}, [], [], {}))


class Whole(unittest.TestCase):
    def test_not_yaml_not_a_mapping_and_too_big_are_refused(self):
        self.assertTrue(bad("a: [1, 2", "not valid YAML"))
        self.assertEqual(check("- a\n- b\n")[1], ["the manifest is a mapping of sections"])
        self.assertIn("at most", check("name: demo\nnotes: " + "x" * mf.MAX_BYTES)[1][0])

    def test_the_name_must_be_the_projects_own(self):
        self.assertTrue(bad("name: other\n", "must be 'demo'"))
        self.assertTrue(bad("purpose: x\n", "must be 'demo'"))

    def test_an_unknown_key_is_an_error_at_every_level_so_a_typo_is_never_ignored(self):
        self.assertTrue(bad("name: demo\nwirng: []\n", "'wirng' is not a field"))
        self.assertTrue(bad(GOOD.replace("baud: 115200, send", "baude: 1, baud: 115200, send"), "'baude'"))
        self.assertTrue(bad(GOOD.replace("action: sda,", "action: sda, colour: red,"), "'colour'"))
        self.assertTrue(bad(GOOD.replace("camera: {id: 761f7205}", "camera: {id: 761f7205, name: x}"), "firmware.camera"))

    def test_purpose_and_notes_are_bounded_and_a_purpose_is_one_line(self):
        self.assertTrue(bad("name: demo\npurpose: " + "x" * 201 + "\n", "purpose"))
        self.assertTrue(bad('name: demo\npurpose: "a\\nb"\n', "purpose"))
        self.assertTrue(bad("name: demo\nnotes: " + "x" * 4001 + "\n", "notes"))


class Devices(unittest.TestCase):
    def test_a_role_is_exactly_one_esp32_target_or_one_nrf_serial(self):
        for body in ("camera: {}", "camera: {esp32: a, nrf: b}", "camera: {ble: a}", "camera: a", "camera: {nrf: 'no spaces allowed'}",
                     "camera: {esp32: 'Bad Name'}"):
            self.assertTrue(bad("name: demo\ndevices:\n  %s\n" % body, "devices.camera"), body)

    def test_a_role_name_is_a_slug_and_i2c_is_reserved(self):
        self.assertTrue(bad("name: demo\ndevices:\n  Bad Role: {esp32: a}\n", "devices.Bad Role"))
        self.assertTrue(bad("name: demo\ndevices:\n  i2c: {esp32: a}\n", "reserved"))


class Esp32(unittest.TestCase):
    def test_by_adapter_or_by_slot_never_both_and_never_neither(self):
        base = "name: demo\nesp32:\n  - %s\n"
        self.assertEqual(check(base % "{name: a, slot: 1-1.3, baud: 115200}")[1], [])
        for item in ("{name: a, baud: 115200}", "{name: a, adapter: X, slot: 1-2, baud: 115200}"):
            self.assertTrue(bad(base % item, "exactly one of adapter"), item)

    def test_the_baud_is_stated_and_is_a_console_rate(self):
        for item in ("{name: a, adapter: X}", "{name: a, adapter: X, baud: 1200}", "{name: a, adapter: X, baud: fast}"):
            self.assertTrue(bad("name: demo\nesp32:\n  - %s\n" % item, "esp32[0].baud"), item)

    def test_a_target_name_is_used_once(self):
        two = "name: demo\nesp32:\n  - {name: a, adapter: X, baud: 115200}\n  - {name: a, adapter: Y, baud: 115200}\n"
        self.assertTrue(bad(two, "name 'a' is used twice"))


class Wiring(unittest.TestCase):
    ROLE = "name: demo\ndevices:\n  alarm: {nrf: SER1}\n  cam: {esp32: t}\nwiring:\n  - %s\n"

    def test_a_connection_is_wired_to_an_nrf_role_and_states_every_field(self):
        self.assertEqual(check(self.ROLE % "{name: w, device: alarm, adapter: 'uart:ttyAMA0', baud: 115200, send: false}")[1], [])
        for item, frag in (("{name: w, device: cam, adapter: X, baud: 115200, send: false}", "not an nrf role"),
                           ("{name: w, device: nobody, adapter: X, baud: 115200, send: false}", "not an nrf role"),
                           ("{name: w, device: alarm, adapter: X, baud: 115200}", "wiring[0].send"),
                           ("{name: w, device: alarm, adapter: X, baud: 7, send: false}", "wiring[0].baud"),
                           ("{name: w, device: alarm, send: false, baud: 115200}", "wiring[0].adapter"),
                           ("{device: alarm, adapter: X, baud: 115200, send: false}", "wiring[0].name")):
            self.assertTrue(bad(self.ROLE % item, frag), item)

    def test_sending_through_a_usb_adapter_needs_the_voltage_stated_and_the_pi_uart_does_not(self):
        usb = "{name: w, device: alarm, adapter: DONGLE, baud: 115200, send: true}"
        self.assertTrue(bad(self.ROLE % usb, "voltage_measured"))
        self.assertEqual(check(self.ROLE % usb.replace("send: true", "send: true, voltage_measured: true"))[1], [])
        pi = "{name: w, device: alarm, adapter: 'uart:ttyAMA0', baud: 115200, send: true}"
        self.assertEqual(check(self.ROLE % pi)[1], [])
        self.assertTrue(bad(self.ROLE % pi.replace("send: true", "send: true, voltage_measured: true"), "only for sending through a USB adapter"))
        self.assertTrue(bad(self.ROLE % usb.replace("send: true", "send: false, voltage_measured: true"), "only for sending"))


class Pins(unittest.TestCase):
    BASE = "gpio: 2, name: p, service: i2c, action: sda, purpose: x, voltage_v: 3.3, active_low: false, voltage_verified: false"

    def test_an_input_needs_pull_and_an_output_needs_its_three_fields_and_none_is_defaulted(self):
        self.assertEqual(check("name: demo\npins:\n  - {%s, direction: input, pull: up}\n" % self.BASE)[1], [])
        self.assertEqual(check("name: demo\npins:\n  - {%s, direction: output, initial: inactive, max_pulse_ms: 100, drives_dut: false}\n" % self.BASE)[1], [])
        self.assertTrue(bad("name: demo\npins:\n  - {%s, direction: input}\n" % self.BASE, "pins[0].pull"))
        self.assertTrue(bad("name: demo\npins:\n  - {%s, direction: output, initial: inactive}\n" % self.BASE, "max_pulse_ms"))
        self.assertTrue(bad("name: demo\npins:\n  - {%s, direction: sideways}\n" % self.BASE, "direction"))
        for key in ("gpio", "name", "service", "action", "purpose", "voltage_v", "active_low", "voltage_verified"):
            without = ", ".join(p for p in self.BASE.split(", ") if not p.startswith(key + ":"))
            self.assertTrue(bad("name: demo\npins:\n  - {%s, direction: input, pull: up}\n" % without, "pins[0].%s" % key), key)

    def test_a_pin_name_and_a_gpio_are_each_used_once(self):
        two = "name: demo\npins:\n  - {%s, direction: input, pull: up}\n  - {%s, direction: input, pull: up}\n" % (self.BASE, self.BASE)
        errors = check(two)[1]
        self.assertTrue(any("name 'p' is used twice" in e for e in errors) and any("gpio 2 is used twice" in e for e in errors), errors)


class Others(unittest.TestCase):
    def test_a_macro_is_a_list_of_lines_and_its_stored_name_fits(self):
        self.assertTrue(bad("name: demo\nmacros:\n  ping: ping\n", "macros.ping"))
        self.assertTrue(bad("name: demo\nmacros:\n  ping: []\n", "macros.ping"))
        self.assertTrue(bad("name: demo\nmacros:\n  ping: [1]\n", "macros.ping"))
        self.assertTrue(bad("name: demo\nmacros:\n  Ping: [a]\n", "macros.Ping"))
        long_name = "p" * 60
        self.assertTrue(bad("name: %s\nmacros:\n  %s: [a]\n" % ("q" * 10, long_name), "more than 64", name="q" * 10))

    def test_firmware_names_a_role_and_a_package_id(self):
        roles = "name: demo\ndevices:\n  cam: {esp32: t}\nfirmware:\n  %s\n"
        self.assertEqual(check(roles % "cam: {id: 761f7205}")[1], [])
        for item, frag in (("nobody: {id: 761f7205}", "not a role"), ("cam: {id: short}", "firmware.cam"), ("cam: {id: ZZZZZZZZ}", "firmware.cam"),
                           ("cam: 761f7205", "firmware.cam")):
            self.assertTrue(bad(roles % item, frag), item)

    def test_expect_names_a_role_a_console_line_and_an_i2c_address_in_range(self):
        base = "name: demo\ndevices:\n  cam: {esp32: t}\nexpect:\n  %s\n"
        self.assertEqual(check(base % "cam: {console: ready}")[1], [])
        for item, frag in (("nobody: {console: x}", "not a role"), ("cam: {console: ''}", "expect.cam"), ("cam: {}", "expect.cam"),
                           ("i2c: {bus: 1, address: 7}", "expect.i2c"), ("i2c: {bus: 1, address: 120}", "expect.i2c"), ("i2c: {bus: 1}", "expect.i2c")):
            self.assertTrue(bad(base % item, frag), item)
        self.assertEqual(check(base % "i2c: {bus: 1, address: 8}")[1], [])
        self.assertEqual(check(base % "i2c: {bus: 1, address: 119}")[1], [])

    def test_requires_is_a_list_of_names(self):
        self.assertTrue(bad("name: demo\nrequires: i2c\n", "requires"))
        self.assertTrue(bad("name: demo\nrequires: [Bad Name]\n", "requires"))

    def test_the_manifest_reports_every_error_at_once_not_just_the_first(self):
        text = "name: wrong\nrequires: 5\nwiring: nope\npins: nope\nmacros: nope\n"
        self.assertGreaterEqual(len(check(text)[1]), 5)


if __name__ == "__main__":
    unittest.main()
