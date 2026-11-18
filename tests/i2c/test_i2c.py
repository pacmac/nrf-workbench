#!/usr/bin/env python3
"""Tests for the I2C commands (docs/specs/i2c.md): the bus layer against a patched ioctl (the struct the kernel is handed, the
errors it answers with), and the commands against a fake bus and a real pin-map file, driven through Registry.call.

No I2C hardware is needed. What the tests cannot show is a real transfer: that is step 2 of the task, on the bench Pi.
Run:  python3 tests/i2c/test_i2c.py
"""
import ctypes
import errno
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.gpio import commands as gpio_commands  # noqa: E402
from nrf_workbench.i2c import bus, commands  # noqa: E402


class Ioctl(object):
    """Stands in for fcntl.ioctl: records each request, and for I2C_RDWR fills the read buffer like a device would."""

    def __init__(self, reply=b"", error=None):
        self.calls, self.reply, self.error = [], reply, error
        self.seen = None

    def __call__(self, fd, request, arg=0):
        self.calls.append((request, arg if isinstance(arg, int) else None))
        if request != bus.I2C_RDWR:
            return 0
        if self.error:
            raise self.error
        msgs = [arg.msgs[i] for i in range(arg.nmsgs)]
        self.seen = [(m.addr, m.flags, m.len, ctypes.string_at(m.buf, m.len) if not m.flags & bus.I2C_M_RD else None) for m in msgs]
        for m in msgs:
            if m.flags & bus.I2C_M_RD:
                ctypes.memmove(m.buf, self.reply, min(len(self.reply), m.len))
        return 0


def open_fds():
    return len(os.listdir("/proc/self/fd"))


class ParseHex(unittest.TestCase):
    def test_the_ways_a_person_writes_bytes(self):
        for text in ("0a ff", "0x0a,0xff", "0AFF", "0a:ff", " 0a\tff "):
            self.assertEqual(bus.parse_hex(text), b"\x0a\xff", text)
        self.assertEqual(bus.parse_hex(""), b"")

    def test_odd_length_and_not_hex_and_too_long_are_refused(self):
        for text in ("abc", "zz", "0a g1", "0x"):
            if text == "0x":
                self.assertEqual(bus.parse_hex(text), b"")
                continue
            with self.assertRaises(ValueError, msg=text):
                bus.parse_hex(text)
        self.assertEqual(len(bus.parse_hex("00" * bus.MAX_BYTES)), bus.MAX_BYTES)
        with self.assertRaises(ValueError):
            bus.parse_hex("00" * (bus.MAX_BYTES + 1))


class Buses(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nrfwb-i2c-")
        self.dev, self.sys = os.path.join(self.root, "dev"), os.path.join(self.root, "sys")
        os.makedirs(self.dev)

    def adapter(self, n, name="bcm2835 (i2c@7e804000)", clock=100000):
        open(os.path.join(self.dev, "i2c-%d" % n), "w").close()
        d = os.path.join(self.sys, "class", "i2c-dev", "i2c-%d" % n)          # /sys/class/i2c-dev, as the Pi has it (not i2c-adapter)
        os.makedirs(os.path.join(d, "device", "of_node"))
        with open(os.path.join(d, "name"), "w") as fh:
            fh.write(name + "\n")
        if clock is not None:
            with open(os.path.join(d, "device", "of_node", "clock-frequency"), "wb") as fh:
                fh.write(struct.pack(">I", clock))

    def test_no_bus_is_an_empty_list(self):
        self.assertEqual(bus.buses(self.dev, self.sys), [])

    def test_a_bus_is_listed_with_its_name_and_the_clock_the_device_tree_says(self):
        self.adapter(1, clock=400000)
        self.assertEqual(bus.buses(self.dev, self.sys), [{"bus": 1, "node": os.path.join(self.dev, "i2c-1"),
                                                          "name": "bcm2835 (i2c@7e804000)", "clock_hz": 400000}])

    def test_a_clock_the_tree_does_not_say_is_none_not_a_guess_and_odd_names_are_not_buses(self):
        self.adapter(1, clock=None)
        open(os.path.join(self.dev, "i2c-x"), "w").close()
        open(os.path.join(self.dev, "i2c-dev"), "w").close()
        (b,) = bus.buses(self.dev, self.sys)
        self.assertEqual((b["bus"], b["clock_hz"]), (1, None))

    def test_buses_are_in_number_order(self):
        for n in (10, 2, 1):
            self.adapter(n)
        self.assertEqual([b["bus"] for b in bus.buses(self.dev, self.sys)], [1, 2, 10])


class Transfer(unittest.TestCase):
    def run_transfer(self, ioctl, *a, **kw):
        with mock.patch.object(bus.fcntl, "ioctl", ioctl):
            return bus.transfer("/dev/null", *a, **kw)

    def test_a_write_then_a_read_is_one_transaction_with_the_read_flagged(self):
        io = Ioctl(reply=b"\x01\x02\x03")
        got, secs = self.run_transfer(io, 0x42, b"\x0a\xff", 3)
        self.assertEqual(got, b"\x01\x02\x03")
        self.assertEqual(io.seen, [(0x42, 0, 2, b"\x0a\xff"), (0x42, bus.I2C_M_RD, 3, None)])
        self.assertEqual([c[0] for c in io.calls], [bus.I2C_TIMEOUT, bus.I2C_RDWR], "one RDWR, so one transaction")
        self.assertEqual(io.calls[0][1], bus.TIMEOUT_TICKS)
        self.assertGreaterEqual(secs, 0)

    def test_a_write_alone_and_a_read_alone_are_one_message_each(self):
        io = Ioctl()
        self.assertEqual(self.run_transfer(io, 0x10, b"\x01", 0)[0], b"")
        self.assertEqual(io.seen, [(0x10, 0, 1, b"\x01")])
        io = Ioctl(reply=b"\xaa")
        self.assertEqual(self.run_transfer(io, 0x10, b"", 1)[0], b"\xaa")
        self.assertEqual(io.seen, [(0x10, bus.I2C_M_RD, 1, None)])

    def test_nothing_to_do_is_a_value_error_and_opens_nothing(self):
        before = open_fds()
        with self.assertRaises(ValueError):
            bus.transfer("/dev/null", 0x10, b"", 0)
        self.assertEqual(open_fds(), before)

    def test_a_device_that_does_not_answer_is_a_nak_that_says_which_part_when_it_can(self):
        # EIO is in the list because it was MEASURED on the bench Pi (kernel 6.18, bcm2835): 111 absent addresses of 112 answered EIO.
        for err in (errno.ENXIO, errno.EREMOTEIO, errno.EIO):
            for write, read, part in ((b"\x01", 0, "write"), (b"", 1, "read"), (b"\x01", 1, "transaction")):
                with self.assertRaises(bus.Nak, msg=(err, part)) as cm:
                    self.run_transfer(Ioctl(error=OSError(err, "x")), 0x42, write, read)
                self.assertEqual((cm.exception.address, cm.exception.part), (0x42, part))

    def test_any_other_kernel_error_is_not_a_nak(self):
        with self.assertRaises(OSError) as cm:
            self.run_transfer(Ioctl(error=OSError(errno.ETIMEDOUT, "transfer timed out")), 0x42, b"\x01", 0)
        self.assertNotIsInstance(cm.exception, bus.Nak)

    def test_the_node_is_closed_on_success_a_nak_and_an_error(self):
        before = open_fds()
        self.run_transfer(Ioctl(reply=b"\x01"), 0x42, b"", 1)
        for err in (errno.ENXIO, errno.ETIMEDOUT):
            with self.assertRaises((bus.Nak, OSError)):
                self.run_transfer(Ioctl(error=OSError(err, "x")), 0x42, b"\x01", 0)
        self.assertEqual(open_fds(), before)

    def test_an_adapter_that_will_not_take_a_timeout_still_transfers(self):
        class Strict(Ioctl):
            def __call__(self, fd, request, arg=0):
                if request == bus.I2C_TIMEOUT:
                    raise OSError(errno.EINVAL, "no")
                return Ioctl.__call__(self, fd, request, arg)
        self.assertEqual(self.run_transfer(Strict(reply=b"\x07"), 0x42, b"", 1)[0], b"\x07")


class Host(object):
    def __init__(self, root, with_bus=True):
        self.devroot, self.sysfs, self.proc = os.path.join(root, "dev"), os.path.join(root, "sys"), os.path.join(root, "proc")
        os.makedirs(self.devroot)
        if with_bus:
            open(os.path.join(self.devroot, "i2c-1"), "w").close()
        self.config = {"paths": {"gpio": os.path.join(root, "gpio")}}
        self.hooks, self.units, self.profiles = {}, {}, []
        self.services = {}
        self.calls = []

    def service(self, name, factory):
        if name not in self.services:
            self.services[name] = factory()
        return self.services[name]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="nrfwb-i2c-")
        self.host = Host(self.root)
        self.reg = Registry()
        commands.register(self.reg, self.host)
        gpio_commands.register(self.reg, self.host)
        self.reply = b"\x11\x22"
        self.nak = set()

        def fake(node, address, write, read):
            self.host.calls.append((node, address, write, read))
            if address in self.nak:
                raise bus.Nak(address, "transaction" if write and read else "write" if write else "read")
            return self.reply[:read], 0.0012
        self.host.hooks["i2c_transfer"] = fake

    def call(self, name, args=None, caps=("gpio",)):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities=set(caps))

    def claim(self, gpio, service="i2c", verified=True, name=None):
        res = self.call("gpio.assign", {"name": name or "i2c-%d" % gpio, "gpio": gpio, "direction": "input", "service": service,
                                        "action": "bus", "purpose": "i2c bus 1", "active_low": False, "voltage_v": 3.3,
                                        "voltage_verified": verified, "pull": "up"})
        self.assertTrue(res.ok, res.message)

    def claim_bus(self):
        self.claim(2)
        self.claim(3)

    def transfer(self, **kw):
        return self.call("i2c.transfer", dict({"bus": 1, "address": 0x42}, **kw))


class Gate(Base):
    def test_nothing_claimed_is_refused_naming_the_pins_and_nothing_is_sent(self):
        res = self.transfer(read=2)
        self.assertEqual((res.cls, res.code), ("refused", "i2c_pins_unclaimed"))
        self.assertEqual(res.evidence["unclaimed"], [2, 3])
        self.assertIn("gpio.assign", res.message)
        self.assertEqual(self.host.calls, [])

    def test_one_pin_claimed_and_the_wrong_service_are_each_refused(self):
        self.claim(2)
        self.assertEqual(self.transfer(read=1).evidence["unclaimed"], [3])
        self.claim(3, service="power", name="three")
        self.assertEqual(self.transfer(read=1).evidence["unclaimed"], [3], "a pin claimed for another service is not the bus's")
        self.assertEqual(self.host.calls, [])

    def test_both_claimed_lets_it_through_without_a_measured_voltage(self):
        """The Pi's pins are 3.3 V by design (Peter, 2026-11-18): a measurement is asked of an adapter with a 3.3/5 V jumper, not of this."""
        self.claim(2, verified=False)
        self.claim(3, verified=False)
        self.assertTrue(self.transfer(read=2).ok)

    def test_a_signal_above_3v3_cannot_be_assigned_at_all_so_it_can_never_be_a_claim(self):
        res = self.call("gpio.assign", {"name": "x", "gpio": 2, "direction": "input", "service": "i2c", "action": "bus",
                                        "purpose": "p", "active_low": False, "voltage_v": 5.0, "voltage_verified": True, "pull": "up"})
        self.assertEqual((res.cls, res.code), ("refused", "voltage_unsafe"))
        self.assertEqual(self.transfer(read=1).code, "i2c_pins_unclaimed")

    def test_a_bus_with_no_known_pins_is_refused(self):
        open(os.path.join(self.host.devroot, "i2c-5"), "w").close()
        res = self.transfer(bus=5, read=1)
        self.assertEqual((res.code, res.evidence["bus"]), ("i2c_pins_unclaimed", 5))

    def test_the_scan_is_gated_the_same_way(self):
        self.assertEqual(self.call("i2c.scan", {"bus": 1, "first": 8, "last": 12}).code, "i2c_pins_unclaimed")
        self.assertEqual(self.host.calls, [])


class Transferring(Base):
    def setUp(self):
        Base.setUp(self)
        self.claim_bus()

    def test_a_register_read_is_one_call_with_the_bytes_the_device_returned(self):
        res = self.transfer(write="0a", read=2)
        self.assertEqual((res.ok, res.data["written"], res.data["read"], res.data["bytes_written"], res.data["bytes_read"]),
                         (True, "0a", "1122", 1, 2))
        self.assertEqual(self.host.calls, [(os.path.join(self.host.devroot, "i2c-1"), 0x42, b"\x0a", 2)])
        self.assertEqual(res.data["duration_s"], 0.0012)

    def test_a_write_only_and_a_read_only_transfer(self):
        self.assertEqual(self.transfer(write="01 02").data["bytes_read"], 0)
        self.assertEqual(self.transfer(read=1).data["bytes_written"], 0)

    def test_a_device_that_does_not_answer_is_a_failed_result_naming_the_address_and_the_part(self):
        self.nak.add(0x42)
        res = self.transfer(write="0a", read=2)
        self.assertEqual((res.cls, res.code), ("failed", "no_reply"))
        self.assertEqual((res.evidence["address"], res.evidence["part"]), (0x42, "transaction"))
        self.assertEqual(len(self.host.calls), 1, "one attempt, never retried")

    def test_a_bus_error_is_a_failed_result_with_the_reason(self):
        def boom(node, address, write, read):
            raise OSError(errno.ETIMEDOUT, "bus is wedged")
        self.host.hooks["i2c_transfer"] = boom
        res = self.transfer(read=1)
        self.assertEqual((res.cls, res.code, res.evidence["errno"]), ("failed", "write_failed", errno.ETIMEDOUT))
        self.assertIn("wedged", res.message)

    def test_no_answer_says_it_is_one_attempt_and_that_a_sleeping_device_needs_a_second_try(self):
        self.nak.add(0x42)
        res = self.transfer(read=1)
        self.assertEqual(res.code, "no_reply")
        self.assertIn("not retried", res.message)
        self.assertIn("sleeps", res.message)

    def test_bad_arguments_are_usage_errors_and_nothing_is_sent(self):
        bad = [dict(address=0x07, read=1), dict(address=0x78, read=1), dict(address=-1, read=1), dict(read=257), dict(read=-1),
               dict(write="zz"), dict(write="abc"), dict(write="00" * 257), dict()]
        for kw in bad:
            with self.assertRaises(UsageError, msg=str(kw)):
                self.transfer(**kw)
        self.assertEqual(self.host.calls, [])
        self.assertTrue(self.transfer(address=0x08, read=1).ok and self.transfer(address=0x77, read=1).ok, "the bounds are inclusive")
        self.assertTrue(self.transfer(read=256).ok)

    def test_a_bus_that_is_not_there_is_a_node_refusal_that_says_how_to_turn_i2c_on(self):
        res = self.transfer(bus=3, read=1)
        self.assertEqual((res.cls, res.code), ("refused", "node_absent"))
        self.assertIn("dtparam=i2c_arm=on", res.message)


class Scanning(Base):
    def setUp(self):
        Base.setUp(self)
        self.claim_bus()

    def test_it_probes_each_address_in_the_range_once_with_a_one_byte_read_and_lists_the_answers(self):
        self.nak = set(range(0x08, 0x20)) - {0x10, 0x1a}
        res = self.call("i2c.scan", {"bus": 1, "first": 0x08, "last": 0x1f})
        self.assertEqual((res.data["found"], res.data["found_hex"]), ([0x10, 0x1a], ["0x10", "0x1a"]))
        self.assertEqual([(c[1], c[2], c[3]) for c in self.host.calls], [(a, b"", 1) for a in range(0x08, 0x20)])
        self.assertEqual(res.message, "2 of 24 addresses acknowledged")

    def test_there_is_no_default_range_and_a_bad_one_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.call("i2c.scan", {"bus": 1})
        for first, last in ((0x07, 0x20), (0x08, 0x78), (0x30, 0x20)):
            with self.assertRaises(UsageError, msg=(first, last)):
                self.call("i2c.scan", {"bus": 1, "first": first, "last": last})
        self.assertEqual(self.host.calls, [])

    def test_a_bus_error_at_one_address_does_not_end_the_scan_and_is_reported(self):
        """A kernel error other than no-answer (a timeout) at one address is recorded and the scan goes on."""
        def fake(node, address, write, read):
            if address == 0x12:
                raise OSError(errno.ETIMEDOUT, "timed out")
            if address in (0x0a, 0x20):
                return b"\x00", 0.0
            raise bus.Nak(address, "read")
        self.host.hooks["i2c_transfer"] = fake
        res = self.call("i2c.scan", {"bus": 1, "first": 0x08, "last": 0x20})
        self.assertEqual((res.ok, res.data["found"], res.data["bus_errors_hex"]), (True, [0x0a, 0x20], ["0x12"]))
        self.assertIn("1 addresses gave a bus error", res.message)

    def test_nothing_answering_is_an_ok_result_that_says_a_sleeping_device_misses_its_first_probe(self):
        self.nak = set(range(0x08, 0x0d))
        res = self.call("i2c.scan", {"bus": 1, "first": 0x08, "last": 0x0c})
        self.assertEqual((res.ok, res.data["found"], res.data["bus_errors"]), (True, [], []))
        self.assertIn("Nothing answered", res.message)

    def test_every_address_giving_a_bus_error_is_a_failure_saying_the_bus_is_stuck(self):
        def boom(node, address, write, read):
            raise OSError(errno.ETIMEDOUT, "stuck")
        self.host.hooks["i2c_transfer"] = boom
        res = self.call("i2c.scan", {"bus": 1, "first": 0x08, "last": 0x0c})
        self.assertEqual((res.cls, res.code, res.evidence["bus_errors"]), ("failed", "write_failed", [8, 9, 10, 11, 12]))
        self.assertIn("stuck", res.message)


class Buses(Base):
    def test_with_no_bus_it_says_i2c_is_off_and_what_to_add(self):
        os.unlink(os.path.join(self.host.devroot, "i2c-1"))
        res = self.call("i2c.buses")
        self.assertEqual((res.ok, res.data), (True, {"buses": [], "i2c_enabled": False, "to_enable": "dtparam=i2c_arm=on"}))
        self.assertIn("reboot", res.message)

    def test_with_a_bus_it_lists_it(self):
        res = self.call("i2c.buses")
        self.assertEqual((res.data["i2c_enabled"], [b["bus"] for b in res.data["buses"]], res.data["to_enable"]), (True, [1], None))


class Registration(Base):
    def test_the_commands_need_the_gpio_capability_and_a_probe_or_transfer_is_a_write(self):
        self.assertEqual([c.name for c in self.reg.commands(set()) if c.name.startswith("i2c.")], [])
        rows = dict((c.name, c) for c in self.reg.commands({"gpio"}) if c.name.startswith("i2c."))
        self.assertEqual(sorted(rows), ["i2c.buses", "i2c.scan", "i2c.transfer"])
        self.assertEqual([rows[n].effect for n in ("i2c.buses", "i2c.scan", "i2c.transfer")], ["read", "write", "write"])
        required = dict((a.name, a.required) for a in rows["i2c.scan"].args)
        self.assertEqual(required, {"bus": True, "first": True, "last": True})


if __name__ == "__main__":
    unittest.main()
