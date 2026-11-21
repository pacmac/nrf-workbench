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
        self.assertEqual(m["expect"], {"console": {"camera": "i2c slave 0x62"}, "i2c": {"bus": 1, "address": 0x62}, "meter": None})
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

    def test_expect_meter_names_a_function_and_a_range(self):
        base = "name: demo\nexpect:\n  %s\n"
        m, errors = check(base % "meter: {function: dcv, min: 3.1, max: 3.5}")
        self.assertEqual((errors, m["expect"]["meter"]), ([], {"function": "dcv", "min": 3.1, "max": 3.5}))
        self.assertEqual(check(base % "meter: {function: dci, min: 0, max: 0.002}")[1], [])
        self.assertEqual(check(base % "meter: {function: res, min: 5, max: 5}")[1], [], "a range of one value is allowed")
        self.assertIsNone(check("name: demo\n")[0]["expect"]["meter"])
        for item in ("meter: {function: volts, min: 1, max: 2}", "meter: {function: dcv, min: 3.5, max: 3.1}", "meter: {function: dcv, min: 1}",
                     "meter: {function: dcv, max: 2}", "meter: {min: 1, max: 2}", "meter: {function: dcv, min: '1', max: 2}",
                     "meter: {function: dcv, min: true, max: 2}", "meter: {function: dcv, min: .nan, max: 2}", "meter: {function: dcv, min: 1, max: .inf}",
                     "meter: {function: dcv, min: 1, max: 2, extra: 3}", "meter: 3.3", "meter: {}"):
            self.assertTrue(bad(base % item, "expect.meter"), item)

    def test_meter_cannot_be_a_role_name(self):
        self.assertTrue(bad("name: demo\ndevices:\n  meter: {esp32: a}\n", "reserved"))

    def test_requires_is_a_list_of_names(self):
        self.assertTrue(bad("name: demo\nrequires: i2c\n", "requires"))
        self.assertTrue(bad("name: demo\nrequires: [Bad Name]\n", "requires"))

    def test_the_manifest_reports_every_error_at_once_not_just_the_first(self):
        text = "name: wrong\nrequires: 5\nwiring: nope\npins: nope\nmacros: nope\n"
        self.assertGreaterEqual(len(check(text)[1]), 5)


class Power(unittest.TestCase):
    """The `power` section (docs/specs/project-power-profile.md): a role's trigger and named events, judged by the code that judges them on a recording."""
    ROLES = "name: demo\ndevices:\n  alarm: {nrf: SER1}\n  sensor: {nrf: SER2}\npower:\n%s"
    FULL = """  alarm:
    trigger: {awake_ua: 50, sleep_ua: 25, hold_s: 0.4, lead_s: 0.2, max_s: 40}
    events:
      - {name: boot, from: wake, start_s: 0.0, end_s: 1.2}
      - {name: radio join, from: wake, start_s: 4.0, end_s: 12.1, every: 2, offset: 1}
      - {name: radio on, from: start, start_s: 2.0, end_s: 14.0}
"""

    def power(self, body, indent="  alarm:\n"):
        return self.ROLES % (indent + "".join("    %s\n" % line for line in body.splitlines()))

    def test_a_full_profile_is_normalised_with_the_trigger_in_amps_and_every_default_stated(self):
        m, errors = check(self.ROLES % self.FULL)
        self.assertEqual(errors, [])
        p = m["power"]["alarm"]
        self.assertNotIn("beat_s", p, "a wake is found from the current: no period is carried")
        for key, ua in (("awake_a", 50), ("sleep_a", 25)):
            self.assertAlmostEqual(p["trigger"][key], ua / 1e6, places=12, msg="microamps are handed on as amps")
        self.assertEqual((p["trigger"]["hold_s"], p["trigger"]["lead_s"], p["trigger"]["max_s"]), (0.4, 0.2, 40))
        self.assertEqual(p["events"], [{"name": "boot", "from": "wake", "start_s": 0.0, "end_s": 1.2, "every": 1, "offset": 0},
                                       {"name": "radio join", "from": "wake", "start_s": 4.0, "end_s": 12.1, "every": 2, "offset": 1},
                                       {"name": "radio on", "from": "start", "start_s": 2.0, "end_s": 14.0, "every": 1, "offset": 0}])

    def test_no_power_section_is_an_empty_mapping_and_each_part_alone_is_enough(self):
        self.assertEqual(check("name: demo\n")[0]["power"], {})
        only_events = check(self.power("events:\n  - {name: radio on, from: start, start_s: 2, end_s: 14}"))
        self.assertEqual(only_events[1], [])
        self.assertEqual(only_events[0]["power"]["alarm"]["trigger"], {})
        only_trigger = check(self.power("trigger: {max_s: 30}"))
        self.assertEqual((only_trigger[1], only_trigger[0]["power"]["alarm"]["trigger"], only_trigger[0]["power"]["alarm"]["events"]), ([], {"max_s": 30}, []))

    def test_a_profile_that_says_nothing_and_a_role_that_is_not_there_are_refused(self):
        self.assertTrue(bad(self.power("{}"), "power.alarm"))
        self.assertTrue(bad("name: demo\ndevices:\n  alarm: {nrf: SER1}\npower:\n  nobody:\n    trigger: {max_s: 30}\n", "power.nobody: 'nobody' is not a role"))
        self.assertTrue(bad("name: demo\npower:\n  meter:\n    trigger: {max_s: 30}\n", "power.meter"))
        self.assertTrue(bad("name: demo\npower: [a]\n", "power:"))

    def test_an_unknown_key_is_refused_at_each_level(self):
        self.assertTrue(bad(self.power("beat: 60"), "'beat' is not a field"))
        self.assertTrue(bad(self.power("trigger: {colour: red}"), "power.alarm.trigger: 'colour'"))
        self.assertTrue(bad(self.power("events:\n  - {name: a, from: start, start_s: 1, end_s: 2, colour: red}"), "power.alarm.events[0]: 'colour'"))

    def test_the_trigger_is_judged_by_the_wake_profile_and_names_the_field(self):
        for body, field in (("trigger: {sleep_ua: 50, awake_ua: 50}", "power.alarm.trigger.awake_ua"),
                            ("trigger: {sleep_ua: 0}", "power.alarm.trigger.sleep_ua"),
                            ("trigger: {awake_ua: -3}", "power.alarm.trigger.awake_ua"),
                            ("trigger: {max_s: 121}", "power.alarm.trigger.max_s"),
                            ("trigger: {max_s: 0.5}", "power.alarm.trigger.max_s"),
                            ("trigger: {hold_s: 0.1}", "power.alarm.trigger.hold_s"),
                            ("trigger: {hold_s: 41}", "power.alarm.trigger.hold_s"),
                            ("trigger: {lead_s: 41}", "power.alarm.trigger.lead_s"),
                            ("trigger: {max_s: fast}", "power.alarm.trigger.max_s"),
                            ("trigger: {max_s: true}", "power.alarm.trigger.max_s"),
                            ("trigger: 50", "power.alarm.trigger")):
            self.assertTrue(bad(self.power(body), field), body)

    def test_a_wake_period_is_not_a_field(self):
        """A wake is found from the current. Wakes need not be periodic (a button, a sensor, a radio frame) and a board may never sleep, so no period is carried or assumed."""
        for body in ("beat_s: 900", "beat_s: 900\ntrigger: {max_s: 30}", "period_s: 900", "every_s: 900"):
            self.assertTrue(bad(self.power(body), "is not a field"), body)

    def test_an_event_states_its_name_its_kind_and_its_times(self):
        for item, field in (("{from: start, start_s: 1, end_s: 2}", "events[0].name"),
                            ("{name: '', from: start, start_s: 1, end_s: 2}", "events[0].name"),
                            ("{name: %s, from: start, start_s: 1, end_s: 2}" % ("x" * 41), "events[0].name"),
                            ("{name: a, start_s: 1, end_s: 2}", "events[0].from"),
                            ("{name: a, from: sometime, start_s: 1, end_s: 2}", "events[0].from"),
                            ("{name: a, from: start, end_s: 2}", "events[0].start_s"),
                            ("{name: a, from: start, start_s: 1}", "events[0].end_s"),
                            ("{name: a, from: start, start_s: 2, end_s: 2}", "events[0].end_s"),
                            ("{name: a, from: start, start_s: -1, end_s: 2}", "events[0].start_s"),
                            ("{name: a, from: start, start_s: x, end_s: 2}", "events[0].start_s"),
                            ("a", "events[0]")):
            self.assertTrue(bad(self.power("events:\n  - %s" % item), "power.alarm.%s" % field), item)
        self.assertTrue(bad(self.power("events: nope"), "power.alarm.events"))

    def test_every_and_offset_are_for_wake_events_only_and_offset_is_below_every(self):
        wake = "events:\n  - {name: a, from: wake, start_s: 1, end_s: 2, %s}"
        self.assertEqual(check(self.power(wake % "every: 8, offset: 7"))[1], [])
        for extra in ("every: 9", "every: 0", "every: 2, offset: 2", "offset: 1", "every: 2, offset: -1"):
            self.assertTrue(bad(self.power(wake % extra), "power.alarm.events[0]"), extra)
        for extra in ("every: 2", "offset: 1"):
            self.assertTrue(bad(self.power("events:\n  - {name: a, from: start, start_s: 1, end_s: 2, %s}" % extra), "events[0].%s" % extra.split(":")[0]), extra)
        self.assertEqual(check(self.power("events:\n  - {name: a, from: wake, start_s: -0.2, end_s: 1}"))[1], [], "a stage may begin before the wake")

    def test_a_name_is_unique_ignoring_case_across_both_kinds(self):
        two = "events:\n  - {name: Radio, from: wake, start_s: 1, end_s: 2}\n  - {name: radio, from: start, start_s: 1, end_s: 2}"
        self.assertTrue(bad(self.power(two), "power.alarm.events[1].name"))

    def test_wake_events_may_not_overlap_in_wakes_they_share_but_may_where_they_do_not(self):
        base = "events:\n  - {name: a, from: wake, start_s: 1, end_s: 3, %s}\n  - {name: b, from: wake, start_s: 2, end_s: 4, %s}"
        self.assertTrue(bad(self.power(base % ("", "")), "power.alarm.events[1]"))
        self.assertTrue(bad(self.power(base % ("every: 2", "every: 2")), "power.alarm.events[1]"))
        self.assertEqual(check(self.power(base % ("every: 2, offset: 0", "every: 2, offset: 1")))[1], [], "alternate wakes do not share")

    def test_recording_events_may_overlap_and_nest_as_they_do_on_a_recording(self):
        two = "events:\n  - {name: awake, from: start, start_s: 0, end_s: 20}\n  - {name: radio, from: start, start_s: 4, end_s: 9}"
        self.assertEqual(check(self.power(two))[1], [])

    def test_at_most_twelve_events_counting_both_kinds(self):
        rows = "".join("  - {name: e%d, from: start, start_s: %d, end_s: %d}\n" % (i, i, i + 1) for i in range(12))
        self.assertEqual(check(self.power("events:\n" + rows))[1], [])
        more = rows + "  - {name: e12, from: wake, start_s: 1, end_s: 2}\n"
        self.assertTrue(bad(self.power("events:\n" + more), "at most 12"))


if __name__ == "__main__":
    unittest.main()
