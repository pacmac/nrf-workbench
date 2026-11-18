#!/usr/bin/env python3
"""Tests for releasing and engaging a Pi UART link's pins (docs/specs/line-release.md): wiring.release, wiring.engage and the pin state in
wiring.show and bench.overview, over a fake `pinctrl` that keeps the pin registers and a fake console that records the order of calls.
Run:  python3 tests/wiring/test_release.py
"""
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.bench import overview  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.wiring import pinctrl  # noqa: E402
from test_pi_uart import PI, Base  # noqa: E402

REAL_OUTPUT = """14: a0    pn | hi // GPIO14 = TXD0
15: a0    pu | hi // GPIO15 = RXD0
"""
CAPS = {"sysfs", "wiring", "gpio", "pinctrl"}


class FakePinctrl(object):
    """The Pi's two UART pins as `pinctrl` reports them. `log` is shared with the fake console so the ORDER of calls can be asserted."""

    def __init__(self, log):
        self.log = log
        self.pins = {14: ["a0", "pn", "hi"], 15: ["a0", "pu", "hi"]}
        self.ignore_set = set()           # a pin that will not change (the register write is lost)
        self.rc_for_set = 0               # nonzero: `pinctrl set` fails
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "get":
            wanted = [int(g) for g in args[1].split(",")]
            self.log.append(("get", wanted))
            return 0, "".join("%d: %s    %s | %s // GPIO%d = x\n" % (g, self.pins[g][0], self.pins[g][1], self.pins[g][2], g) for g in wanted)
        gpio = int(args[1])
        self.log.append(("set", gpio, args[2:]))
        if self.rc_for_set:
            return self.rc_for_set, "pinctrl: not permitted"
        if gpio not in self.ignore_set:
            self.pins[gpio][0], self.pins[gpio][1] = args[2], args[3] if len(args) > 3 else self.pins[gpio][1]
        return 0, ""


class FakeConsole(object):
    def __init__(self, log):
        self.log, self.held = log, set()

    def suspend(self, name):
        self.log.append(("suspend", name))
        self.held.add(name)
        return True

    def resume(self, name):
        self.log.append(("resume", name))
        self.held.discard(name)

    def state(self, name):
        return {"state": "held" if name in self.held else "open", "reason": "", "node": None, "lines": 0, "bytes": 0, "last_byte_at": None}

    def set_links(self, links):
        pass


class Release(Base):
    def setUp(self):
        Base.setUp(self)
        self.uart_on()
        self.log = []
        self.fake = FakePinctrl(self.log)
        self.host.hooks["pinctrl"] = self.fake
        self.host.console = FakeConsole(self.log)
        self.host.capabilities = set(CAPS)
        from nrf_workbench.core.registry import Context
        self.ctx = Context
        self.reg.get("wiring.release")                          # registered by the base's register(): the hook makes pinctrl available

    def call(self, name, args=None):
        return self.reg.call(name, args or {}, self.ctx(self.host, "tester", None), capabilities=set(CAPS))

    def connected(self, name="rak-uart0"):
        res = self.connect(name=name)
        self.assertTrue(res.ok, res.message)
        self.log[:] = []
        self.fake.calls[:] = []

    def sets(self):
        return [e for e in self.log if e[0] == "set"]

    def test_release_closes_the_port_first_floats_each_pin_and_reports_what_they_read_back_as(self):
        self.connected()
        res = self.call("wiring.release", {"name": "rak-uart0"})
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.log[0], ("suspend", "rak-uart0"), "the port is closed before any pin is touched")
        self.assertEqual(self.sets(), [("set", 14, ["ip", "pn"]), ("set", 15, ["ip", "pn"])])
        self.assertEqual(res.data["pin_state"]["state"], "floating")
        self.assertEqual([(p["gpio"], p["function"], p["pull"]) for p in res.data["pin_state"]["pins"]], [(14, "ip", "pn"), (15, "ip", "pn")])
        self.assertEqual(res.data["link"]["state"], "held")
        self.assertIn("GPIO 14, 15 are inputs with no pull", res.message)

    def test_engage_gives_the_pins_back_with_the_pull_the_pi_booted_with_and_opens_the_link_only_after_the_read_back(self):
        self.connected()
        self.call("wiring.release", {"name": "rak-uart0"})
        self.log[:] = []
        res = self.call("wiring.engage", {"name": "rak-uart0"})
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.sets(), [("set", 14, ["a0", "pn"]), ("set", 15, ["a0", "pu"])])
        kinds = [e[0] for e in self.log]
        self.assertLess(max(i for i, k in enumerate(kinds) if k == "get"), kinds.index("resume"), "the link opens after the pins were read back")
        self.assertEqual((res.data["pin_state"]["state"], res.data["link"]["state"]), ("driven", "open"))

    def test_a_release_that_cannot_float_a_pin_says_so_and_the_link_stays_held(self):
        self.connected()
        self.fake.ignore_set = {14}
        res = self.call("wiring.release", {"name": "rak-uart0"})
        self.assertEqual((res.cls, res.code), ("failed", "pins_not_set"))
        self.assertEqual(res.evidence["pin_state"]["state"], "mixed")
        self.assertIn("read back as mixed, not floating", res.message)
        self.assertEqual(self.host.console.state("rak-uart0")["state"], "held")

    def test_an_engage_that_cannot_restore_a_pin_leaves_the_link_held(self):
        self.connected()
        self.call("wiring.release", {"name": "rak-uart0"})
        self.fake.ignore_set = {15}
        res = self.call("wiring.engage", {"name": "rak-uart0"})
        self.assertEqual((res.cls, res.code), ("failed", "pins_not_set"))
        self.assertNotIn(("resume", "rak-uart0"), self.log)
        self.assertEqual(self.host.console.state("rak-uart0")["state"], "held")

    def test_pinctrl_refusing_is_a_failure_with_its_words_not_a_crash(self):
        self.connected()
        self.fake.rc_for_set = 1
        for name in ("wiring.release", "wiring.engage"):
            res = self.call(name, {"name": "rak-uart0"})
            self.assertEqual((res.cls, res.code), ("failed", "pins_not_set"), name)
            self.assertIn("not permitted", res.message)

    def test_both_are_idempotent(self):
        self.connected()
        for _ in range(2):
            self.assertEqual(self.call("wiring.release", {"name": "rak-uart0"}).data["pin_state"]["state"], "floating")
        for _ in range(2):
            self.assertEqual(self.call("wiring.engage", {"name": "rak-uart0"}).data["pin_state"]["state"], "driven")

    def test_a_usb_adapter_connection_is_refused_and_nothing_is_touched(self):
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="DONGLE1", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        res = self.connect(adapter="DONGLE1", name="usb-log")
        self.assertTrue(res.ok, res.message)
        self.log[:] = []
        for name in ("wiring.release", "wiring.engage"):
            res = self.call(name, {"name": "usb-log"})
            self.assertEqual((res.cls, res.code), ("refused", "pins_not_controllable"), name)
            self.assertIn("a USB adapter's lines are driven by its chip", res.message)
        self.assertEqual(self.log, [], "no pin was read or set and the link was not held")

    def test_an_unknown_name_is_connection_unknown(self):
        for name in ("wiring.release", "wiring.engage"):
            self.assertEqual(self.call(name, {"name": "nope"}).code, "connection_unknown")
        self.assertEqual(self.log, [])

    def test_the_pins_stay_reserved_by_the_connection_while_released(self):
        self.connected()
        self.call("wiring.release", {"name": "rak-uart0"})
        res = self.assign(14, name="mine")
        self.assertEqual(res.code, "pin_taken", "a released link still owns its pins: floating them is not freeing them")

    def test_the_command_does_not_touch_the_connection_file(self):
        self.connected()
        before = self.call("wiring.show").data["connections"][0]
        self.call("wiring.release", {"name": "rak-uart0"})
        after = self.call("wiring.show").data["connections"][0]
        self.assertEqual(dict((k, v) for k, v in before.items() if k not in ("pin_state", "status")),
                         dict((k, v) for k, v in after.items() if k not in ("pin_state", "status")))

    def test_wiring_show_reads_the_pin_state_live_and_a_hand_run_pinctrl_is_seen(self):
        self.connected()
        self.assertEqual(self.call("wiring.show").data["connections"][0]["pin_state"]["state"], "driven")
        self.fake.pins[14][:2] = ["ip", "pn"]                              # someone ran pinctrl by hand: the page must not say "driven"
        self.assertEqual(self.call("wiring.show").data["connections"][0]["pin_state"]["state"], "mixed")
        self.fake.pins[15][:2] = ["ip", "pn"]
        self.assertEqual(self.call("wiring.show").data["connections"][0]["pin_state"]["state"], "floating")

    def test_wiring_show_says_unknown_when_pinctrl_cannot_be_read_and_has_no_key_for_a_usb_adapter(self):
        self.connected()
        real = self.host.hooks["pinctrl"]
        self.host.hooks["pinctrl"] = lambda args: (1, "no access")
        pin_state = self.call("wiring.show").data["connections"][0]["pin_state"]
        self.assertEqual((pin_state["state"], pin_state["pins"]), ("unknown", []))
        self.assertIn("no access", pin_state["why"])
        self.host.hooks["pinctrl"] = real
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="DONGLE1", tty="ttyACM1", majmin="166:1")
        self.t.node("ttyACM1", 166, 1)
        self.connect(adapter="DONGLE1", name="usb-log")
        by_name = dict((c["name"], c) for c in self.call("wiring.show").data["connections"])
        self.assertNotIn("pin_state", by_name["usb-log"])

    def test_a_host_without_pinctrl_has_no_pin_state_and_no_release_command(self):
        self.connected()
        del self.host.hooks["pinctrl"]
        real_find, pinctrl.find = pinctrl.find, lambda: None
        try:
            self.assertNotIn("pin_state", self.call("wiring.show").data["connections"][0])
            names = [c.name for c in self.reg.commands(set(CAPS) - {"pinctrl"})]
            self.assertNotIn("wiring.release", names)
            self.assertNotIn("wiring.engage", names)
            self.assertIn("wiring.release", [c.name for c in self.reg.commands(set(CAPS))])
        finally:
            pinctrl.find = real_find

    def test_the_overview_marks_a_half_released_link_as_needing_attention_too(self):
        self.connected()
        self.fake.pins[14][:2] = ["ip", "pn"]
        row = overview._connections(self.host, {})[0][0]
        self.assertEqual((row["state"], row["pin_state"]), ("attention", "mixed"))

    def test_the_overview_marks_a_released_link_as_needing_attention_and_says_why(self):
        self.connected()
        row = overview._connections(self.host, {})[0][0]
        self.assertEqual((row["state"], row["pin_state"]), ("listening", "driven"))
        self.call("wiring.release", {"name": "rak-uart0"})
        row = overview._connections(self.host, {})[0][0]
        self.assertEqual((row["state"], row["pin_state"]), ("attention", "floating"))
        self.assertIn("released", row["why"])
        self.assertIn("engage", row["why"])
        self.call("wiring.engage", {"name": "rak-uart0"})
        row = overview._connections(self.host, {})[0][0]
        self.assertEqual((row["pin_state"], row["state"] != "attention"), ("driven", True))


class Parsing(unittest.TestCase):
    def test_the_real_pinctrl_output_is_read_and_other_lines_are_ignored(self):
        pins = pinctrl.parse("warning: x\n" + REAL_OUTPUT + "\n")
        self.assertEqual(pins[14], {"gpio": 14, "function": "a0", "pull": "pn", "level": "hi"})
        self.assertEqual(pins[15], {"gpio": 15, "function": "a0", "pull": "pu", "level": "hi"})

    def test_the_state_word_is_derived_from_the_read_back_only(self):
        drv = [{"function": "a0", "pull": "pn"}, {"function": "a0", "pull": "pu"}]
        flo = [{"function": "ip", "pull": "pn"}, {"function": "ip", "pull": "pn"}]
        self.assertEqual((pinctrl.state_word(drv), pinctrl.state_word(flo)), ("driven", "floating"))
        self.assertEqual(pinctrl.state_word([{"function": "ip", "pull": "pu"}, {"function": "ip", "pull": "pn"}]), "mixed", "an input with a pull is not floating")
        self.assertEqual(pinctrl.state_word([flo[0], drv[0]]), "mixed")

    def test_the_defaults_are_what_was_measured_on_the_pi(self):
        self.assertEqual(pinctrl.PI_UART_DEFAULT, {14: "pn", 15: "pu"})

    def test_a_missing_pin_in_the_output_is_an_error_not_a_guess(self):
        class H(object):
            hooks = {"pinctrl": lambda args: (0, "14: a0    pn | hi // GPIO14 = TXD0\n")}
        with self.assertRaises(pinctrl.PinctrlError):
            pinctrl.get(H(), [14, 15])


class RealBinary(unittest.TestCase):
    """The path with no hook: a real executable is run, and a hang is given up on at the ceiling."""

    def host(self):
        class H(object):
            hooks = {}
        return H()

    def script(self, body):
        d = tempfile.mkdtemp(prefix="nrfwb-pinctrl-")
        path = os.path.join(d, "pinctrl")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_it_runs_the_binary_with_the_arguments_and_reads_its_output(self):
        path = self.script('echo "$1 $2" > "$0.args"; printf "14: a0    pn | hi // x\\n15: a0    pu | lo // y\\n"')
        real, pinctrl.find = pinctrl.find, lambda: path
        try:
            pins = pinctrl.get(self.host(), [14, 15])
        finally:
            pinctrl.find = real
        self.assertEqual([(p["gpio"], p["level"]) for p in pins], [(14, "hi"), (15, "lo")])
        with open(path + ".args") as fh:
            self.assertEqual(fh.read().strip(), "get 14,15")

    def test_a_hang_is_given_up_on_at_the_ceiling_and_is_an_error(self):
        # it would answer correctly after 5 s: only the ceiling can make this an error
        path = self.script('sleep 5; printf "14: a0    pn | hi // x\\n"')
        real, pinctrl.find = pinctrl.find, lambda: path
        ceiling, pinctrl.CEILING_S = pinctrl.CEILING_S, 0.1
        try:
            with self.assertRaises(pinctrl.PinctrlError):
                pinctrl.get(self.host(), [14])
        finally:
            pinctrl.find, pinctrl.CEILING_S = real, ceiling

    def test_no_binary_is_an_error(self):
        real, pinctrl.find = pinctrl.find, lambda: None
        try:
            with self.assertRaises(pinctrl.PinctrlError):
                pinctrl.get(self.host(), [14])
        finally:
            pinctrl.find = real


class Capability(Base):
    def setUp(self):
        Base.setUp(self)
        self.uart_on()
        os.makedirs(os.path.join(self.t.proc, "device-tree"))
        with open(os.path.join(self.t.proc, "device-tree", "model"), "wb") as fh:
            fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")

    def caps(self, hook):
        tmp = tempfile.mkdtemp(prefix="nrfwb-cap-")
        cfg, problems = cfgmod.load(env={}, overrides={"paths": {"wiring": os.path.join(tmp, "w"), "gpio": os.path.join(tmp, "g")}})
        self.assertEqual(problems, [])
        host, reg, caps = service.build_host(cfg, self.t.sysfs, self.t.dev, self.t.proc, {"pinctrl": hook} if hook else {})
        return caps

    def test_the_capability_needs_a_pi_with_a_pin_map_and_the_tool(self):
        self.assertIn("pinctrl", self.caps(lambda a: (0, "")))
        real, pinctrl.find = pinctrl.find, lambda: None
        try:
            self.assertNotIn("pinctrl", self.caps(None))
        finally:
            pinctrl.find = real


if __name__ == "__main__":
    unittest.main()
