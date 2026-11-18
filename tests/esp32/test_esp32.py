#!/usr/bin/env python3
"""Tests for the ESP32 slice 1 (docs/specs/esp32.md): the two package kinds in the store, declared targets, and the
flash with the same guards as the nRF flash, over a fake sysfs and a fake esptool.

The tool is injected as a recorder: ZERO runs for every refusal, ONE for a success and ONE (never two) for a failure.
Run:  python3 tests/esp32/test_esp32.py
"""
import hashlib
import io
import json
import os
import sys
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))

from fakes import Tree  # noqa: E402
from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.esp32 import commands, flash, package  # noqa: E402
from nrf_workbench.guards.lock import HostLock  # noqa: E402
from nrf_workbench.programmer import commands as programmer_commands  # noqa: E402
from nrf_workbench.programmer.firmware_store import Store  # noqa: E402
from test_firmware_store import pkg as dfu_pkg  # noqa: E402

BRIDGE = ("10c4", "ea60")                       # a CP210x: the commonest bridge on an ESP32 dev board
NATIVE = ("303a", "1001")
SERIAL = "0001"                                 # what a CP2102 commonly reports on EVERY unit
GOOD = ("esptool v5.4.0\nChip type:          ESP32-D0WD-V3 (revision v3.1)\nMAC: 24:6f:28:aa:bb:cc\n"
        "Wrote 4096 bytes\nHash of data verified.\nHard resetting via RTS pin...\n")


def merged(extra=0, seed=0):
    """A whole-flash image: the partition table magic AA 50 at 0x8000."""
    data = bytearray(b"\xff" * (0x8000 + 64 + extra))
    data[0x1000] = 0xE9
    data[0x8000:0x8002] = b"\xaa\x50"
    data[0x8004] = seed
    return bytes(data)


def bundle(files=None, args=None, elf="app.elf", extra=None):
    files = files if files is not None else {"0x1000": "bootloader.bin", "0x8000": "partitions.bin", "0x10000": "app.bin"}
    body = {"flash_files": files, "extra_esptool_args": {"chip": "esp32"}}
    if elf:
        body["app_elf"] = elf
    body.update(args or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("flasher_args.json", (2020, 1, 1, 0, 0, 0)), json.dumps(body))
        for name in set(files.values()):
            z.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), b"\x01" * 64)
        if elf:
            z.writestr(zipfile.ZipInfo(elf, (2020, 1, 1, 0, 0, 0)), b"ELF")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Body(io.BytesIO):
    def __init__(self, data):
        io.BytesIO.__init__(self, data)
        self.length = len(data)


class Tool(object):
    """Stands in for esptool. Records every run."""

    def __init__(self, rc=0, out=GOOD, secs=2.0, timed_out=False):
        self.calls, self.rc, self.out, self.secs, self.timed_out = [], rc, out, secs, timed_out
        self.seen = []

    def __call__(self, cmd, timeout_s):
        self.calls.append((cmd, timeout_s))
        self.seen.append([open(a, "rb").read() for a in cmd if os.path.isfile(a) and a.startswith(os.sep)])
        return self.rc, self.out, self.secs, self.timed_out


class Host(object):
    def __init__(self, tree, hooks=None, units=None):
        self.config = {"paths": {"firmware": os.path.join(tree.root, "store"), "wiring": os.path.join(tree.root, "wiring"),
                                 "lock": None, "profiles": None, "units": None}}
        self.sysfs, self.devroot, self.proc = tree.sysfs, tree.dev, tree.proc
        self.lock_path = os.path.join(tree.root, "lock")
        self.profiles, self.units, self.hooks = [], units or {}, hooks or {}
        self.services = {}
        self.console, self.console_links = None, []

    def service(self, name, factory):
        if name not in self.services:
            self.services[name] = factory()
        return self.services[name]


ALL = {"wiring", "esp32", "firmware_store"}


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.t.usb("1-2", BRIDGE, "CP2102 USB to UART Bridge Controller", serial=SERIAL, manufacturer="Silicon Labs",
                   tty="ttyUSB0", majmin="188:0")
        self.t.node("ttyUSB0", 188, 0)
        self.tool = Tool()
        self.host = Host(self.t, hooks={"esp_runner": self.tool, "esp_tool": "/fake/esptool"})
        self.reg = Registry()
        programmer_commands.register(self.reg, self.host)
        commands.register(self.reg, self.host)

    def call(self, name, args=None, body=None, caps=ALL):
        return self.reg.call(name, args or {}, Context(self.host, "tester", body), capabilities=caps)

    def put(self, data, **args):
        res = self.call("firmware.put", args, body=Body(data))
        self.assertTrue(res.ok, res.message)
        return res.data["sha256"]

    def declare(self, name="bench-esp", **kw):
        res = self.call("esp32.declare", dict({"serial": SERIAL}, name=name, **kw) if not kw else dict(name=name, **kw))
        self.assertTrue(res.ok, res.message)

    def flash_args(self, fid, **kw):
        a = {"target": "bench-esp", "firmware": fid, "flash_bound_s": 30.0}
        a.update(kw)
        return a


class Packages(unittest.TestCase):
    def tmp(self, data):
        import tempfile
        fd, path = tempfile.mkstemp(prefix="esp-pkg-")
        self.addCleanup(os.unlink, path)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path

    def test_a_whole_flash_image_is_recognised_by_its_partition_table(self):
        info = package.inspect(self.tmp(merged()))
        self.assertEqual((info["kind"], info["parts"][0]["offset"], info["image_bytes"]),
                         (package.KIND_IMAGE, 0, 0x8000 + 64))

    def test_an_app_only_image_has_no_offset_and_is_refused_with_the_reason(self):
        path = self.tmp(b"\xe9" + b"\0" * 2000)
        self.assertIsNone(package.kind_of(path))
        self.assertIn("flash offset", package.single_part_message(path))

    def test_a_file_that_is_neither_is_not_claimed(self):
        path = self.tmp(b"hello")
        self.assertIsNone(package.kind_of(path))
        self.assertIsNone(package.single_part_message(path))

    def test_an_image_one_byte_short_of_the_magic_is_not_a_whole_image(self):
        self.assertIsNone(package.kind_of(self.tmp(merged()[:0x8001])))

    def test_a_bundle_lists_its_parts_in_offset_order_and_keeps_the_chip_and_elf(self):
        info = package.inspect(self.tmp(bundle()))
        self.assertEqual([p["offset"] for p in info["parts"]], [0x1000, 0x8000, 0x10000])
        self.assertEqual((info["chip"], info["elf"], info["elf_name"], info["image_bytes"]), ("esp32", True, "app.elf", 192))

    def test_a_bundle_that_names_a_file_the_zip_lacks_is_refused(self):
        data = bundle(files={"0x0": "missing.bin"})
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(buf, "w") as zout:
            zout.writestr("flasher_args.json", zin.read("flasher_args.json"))
        with self.assertRaises(package.Bad) as cm:
            package.inspect(self.tmp(buf.getvalue()))
        self.assertIn("missing.bin", str(cm.exception))

    def test_parts_that_overlap_are_refused(self):
        with self.assertRaises(package.Bad) as cm:
            package.inspect(self.tmp(bundle(files={"0x1000": "a.bin", "0x1020": "b.bin"}, elf=None)))
        self.assertIn("runs into", str(cm.exception))

    def test_a_flash_offset_that_is_not_a_number_is_refused(self):
        with self.assertRaises(package.Bad):
            package.inspect(self.tmp(bundle(files={"soon": "a.bin"}, elf=None)))

    def test_an_elf_the_zip_does_not_hold_is_refused(self):
        data = bundle(args={"app_elf": "nope.elf"}, elf=None)
        with self.assertRaises(package.Bad):
            package.inspect(self.tmp(data))

    def test_a_dfu_zip_is_not_an_esp_bundle(self):
        self.assertIsNone(package.kind_of(self.tmp(dfu_pkg())))


class StoreKinds(Base):
    def test_a_whole_image_is_stored_as_bin_and_its_record_says_what_it_is(self):
        data = merged()
        fid = self.put(data, project="esp")
        rec = self.call("firmware.show", {"id": fid}).data
        self.assertEqual((rec["kind"], rec["parts"], rec["softdevice_req"], rec["packets"], rec["elf"]),
                         (package.KIND_IMAGE, 1, None, None, False))
        self.assertTrue(os.path.isfile(os.path.join(self.host.config["paths"]["firmware"], "esp", fid + ".bin")))
        self.assertFalse(os.path.exists(os.path.join(self.host.config["paths"]["firmware"], "esp", fid + ".zip")))

    def test_a_bundle_is_stored_as_zip_with_its_chip_and_elf(self):
        rec = self.call("firmware.show", {"id": self.put(bundle())}).data
        self.assertEqual((rec["kind"], rec["parts"], rec["chip"], rec["elf"]), (package.KIND_BUNDLE, 3, "esp32", True))

    def test_an_nrf_package_record_is_unchanged_and_has_no_kind(self):
        rec = self.call("firmware.show", {"id": self.put(dfu_pkg())}).data
        self.assertNotIn("kind", rec)
        self.assertEqual(rec["softdevice_req"], [182])

    def test_an_app_only_bin_is_refused_by_the_store_and_nothing_is_kept(self):
        res = self.call("firmware.put", body=Body(b"\xe9" + b"\0" * 5000))
        self.assertEqual((res.cls, res.code), ("refused", "bad_package"))
        self.assertIn("flash offset", res.message)
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_move_and_delete_follow_the_extension(self):
        fid = self.put(merged(), project="a")
        root = self.host.config["paths"]["firmware"]
        self.assertTrue(self.call("firmware.move", {"id": fid, "project": "b"}).ok)
        self.assertTrue(os.path.isfile(os.path.join(root, "b", fid + ".bin")))
        self.assertFalse(os.path.exists(os.path.join(root, "a", fid + ".bin")))
        self.assertTrue(Store(root).verify(fid).ok)
        self.assertTrue(self.call("firmware.delete", {"id": fid}).ok)
        self.assertEqual(os.listdir(os.path.join(root, "b")), [])


class Targets(Base):
    def test_declare_by_serial_lists_where_it_is_attached(self):
        self.declare()
        t = self.call("esp32.targets").data["targets"][0]
        self.assertEqual((t["name"], t["key"], t["key_kind"], t["attached"], t["node"]),
                         ("bench-esp", SERIAL, "serial", 1, os.path.join(self.t.dev, "ttyUSB0")))

    def test_a_serial_two_devices_share_cannot_be_used_and_the_slots_are_named(self):
        self.t.usb("1-3", BRIDGE, "CP2102 USB to UART Bridge Controller", serial=SERIAL, tty="ttyUSB1", majmin="188:1")
        res = self.call("esp32.declare", {"name": "x", "serial": SERIAL})
        self.assertEqual((res.cls, res.code), ("refused", "serial_unverifiable"))
        self.assertEqual(res.evidence["slots"], ["1-2", "1-3"])
        res = self.call("esp32.declare", {"name": "x", "slot": "1-3"})
        self.assertEqual((res.ok, res.data["key_kind"], res.data["key"]), (True, "slot", "1-3"))

    def test_a_declared_slot_target_follows_the_port_not_the_board(self):
        self.t.usb("1-3", BRIDGE, "CH340", serial=None, tty="ttyUSB1", majmin="188:1")
        self.call("esp32.declare", {"name": "p", "slot": "1-3"})
        self.t.unplug("1-3")
        self.assertEqual(self.call("esp32.targets").data["targets"][0]["problem"], "not attached")
        self.t.usb("1-3", BRIDGE, "CH340", serial=None, tty="ttyUSB1", majmin="188:1")
        self.assertEqual(self.call("esp32.targets").data["targets"][0]["attached"], 1)

    def test_refusals(self):
        self.declare()
        cases = [({"name": "bench-esp", "serial": SERIAL}, "name_taken"),
                 ({"name": "other", "serial": SERIAL}, "adapter_taken"),
                 ({"name": "other", "serial": "NOPE"}, "adapter_absent"),
                 ({"name": "other", "slot": "9-9"}, "adapter_absent")]
        for args, code in cases:
            res = self.call("esp32.declare", args)
            self.assertEqual((res.cls, res.code), ("refused", code), args)

    def test_a_name_that_breaks_the_rule_or_no_adapter_named_is_a_usage_error(self):
        for args in ({"name": "Bad Name", "serial": SERIAL}, {"name": "ok"}, {"name": "ok", "serial": SERIAL, "slot": "1-2"}):
            with self.assertRaises(UsageError, msg=str(args)):
                self.call("esp32.declare", args)

    def test_forget_and_an_unknown_target(self):
        self.declare()
        self.assertTrue(self.call("esp32.forget", {"name": "bench-esp"}).ok)
        self.assertEqual(self.call("esp32.forget", {"name": "bench-esp"}).code, "target_unknown")
        self.assertEqual(self.call("esp32.targets").data["targets"], [])

    def test_a_built_in_usb_esp32_is_listed_until_it_is_declared(self):
        self.t.usb("1-4", NATIVE, "USB JTAG/serial debug unit", serial="24:6F:28:AA:BB:CC", tty="ttyACM0", majmin="166:0")
        self.assertEqual([d["serial"] for d in self.call("esp32.targets").data["undeclared_native"]], ["24:6F:28:AA:BB:CC"])
        self.declare("native", serial="24:6F:28:AA:BB:CC")
        self.assertEqual(self.call("esp32.targets").data["undeclared_native"], [])

    def test_the_bench_dongle_is_not_an_esp32_until_someone_says_so(self):
        self.t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="5B1F007437", tty="ttyACM1", majmin="166:1")
        self.assertEqual(self.call("esp32.targets").data["targets"], [])


class Flashing(Base):
    def setUp(self):
        Base.setUp(self)
        self.declare()

    def test_a_whole_image_is_written_at_zero_in_one_run_and_judged_by_esptool_verifying_it(self):
        fid = self.put(merged())
        res = self.call("esp32.flash", self.flash_args(fid, baud=460800))
        self.assertTrue(res.ok, res.message)
        cmd = self.tool.calls[0][0]
        self.assertEqual(len(self.tool.calls), 1)
        self.assertEqual(cmd[:5], ["/fake/esptool", "--port", os.path.join(self.t.dev, "ttyUSB0"), "--baud", "460800"])
        self.assertEqual(cmd[cmd.index("write-flash") + 3], "0x0")
        self.assertEqual((res.data["parts"], res.data["verified"], res.data["chip"], res.data["mac"]),
                         (1, 1, "ESP32-D0WD-V3 (revision v3.1)", "24:6f:28:aa:bb:cc"))
        self.assertEqual(res.data["output"], GOOD)

    def test_a_bundle_writes_every_part_at_its_offset_and_the_scratch_folder_is_gone_afterwards(self):
        fid = self.put(bundle())
        self.tool.out = GOOD + "Hash of data verified.\nHash of data verified.\n"
        res = self.call("esp32.flash", self.flash_args(fid))
        self.assertTrue(res.ok, res.message)
        cmd = self.tool.calls[0][0]
        self.assertEqual(cmd[cmd.index("--chip") + 1], "esp32")
        pairs = cmd[cmd.index("write-flash") + 3:]
        self.assertEqual(pairs[0::2], ["0x1000", "0x8000", "0x10000"])
        self.assertEqual(self.tool.seen[0], [b"\x01" * 64] * 3)
        root = self.host.config["paths"]["firmware"]
        self.assertEqual([n for n in os.listdir(root) if n.startswith(".esp-")], [])

    def test_a_part_esptool_did_not_verify_makes_the_verdict_suspect(self):
        fid = self.put(bundle())
        res = self.call("esp32.flash", self.flash_args(fid))               # GOOD verifies one part, the bundle has three
        self.assertEqual((res.cls, res.code), ("suspect", "flash_suspect"))
        self.assertIn("1 of 3", res.message)

    def test_a_failure_is_one_attempt_with_the_whole_output(self):
        fid = self.put(merged())
        self.tool.rc, self.tool.out = 2, "A fatal error occurred: Failed to connect to ESP32: No serial data received.\n"
        res = self.call("esp32.flash", self.flash_args(fid))
        self.assertEqual((res.cls, res.code, len(self.tool.calls)), ("failed", "flash_failed", 1))
        self.assertEqual(res.evidence["output"], self.tool.out)

    def test_a_run_that_outlasts_its_bound_is_a_failure_that_names_the_bound(self):
        fid = self.put(merged())
        self.tool.timed_out = True
        res = self.call("esp32.flash", self.flash_args(fid, flash_bound_s=12.5))
        self.assertEqual((res.code, res.evidence["bound_s"]), ("flash_failed", 12.5))
        self.assertEqual(self.tool.calls[0][1], 12.5)

    def assertRefusedWithoutRunning(self, res, code):
        self.assertEqual((res.cls, res.code), ("refused", code), res.message)
        self.assertEqual(self.tool.calls, [])

    def test_no_bound_a_bad_baud_and_an_unknown_firmware_or_target_touch_nothing(self):
        fid = self.put(merged())
        with self.assertRaises(UsageError):
            self.call("esp32.flash", {"target": "bench-esp", "firmware": fid})          # the bound is a required argument
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid, flash_bound_s=0.0)), "no_bound")
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid, baud=1200)), "baud_refused")
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args("0" * 64)), "firmware_unknown")
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid, target="nope")), "target_unknown")

    def test_an_nrf_package_is_refused_here(self):
        res = self.call("esp32.flash", self.flash_args(self.put(dfu_pkg())))
        self.assertRefusedWithoutRunning(res, "bad_package")
        self.assertIn("nRF", res.message)

    def test_the_wrong_tool_is_refused_when_esptool_is_not_there(self):
        fid = self.put(merged())
        del self.host.hooks["esp_tool"]
        with mock.patch.object(flash, "find_tool", return_value=None):
            res = self.call("esp32.flash", self.flash_args(fid))
        self.assertRefusedWithoutRunning(res, "capability_missing")

    def test_an_unplugged_adapter_two_matches_a_protected_unit_and_a_held_port_each_refuse(self):
        fid = self.put(merged())
        self.host.units = {SERIAL: {"protected": True}}
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid)), "protected")
        self.host.units = {}
        self.t.hold(4242, os.path.join(self.t.dev, "ttyUSB0"), "minicom")
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid)), "port_held")
        import shutil
        shutil.rmtree(os.path.join(self.t.proc, "4242"))
        self.t.usb("1-3", BRIDGE, "CP2102", serial=SERIAL, tty="ttyUSB1", majmin="188:1")
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid)), "serial_unverifiable")
        self.t.unplug("1-3")
        self.t.unplug("1-2")
        self.assertRefusedWithoutRunning(self.call("esp32.flash", self.flash_args(fid)), "node_absent")

    def test_a_slot_target_is_protected_by_its_slot_key(self):
        self.call("esp32.declare", {"name": "p", "slot": "1-2"})
        self.host.units = {"slot:1-2": {"protected": True}}
        res = self.call("esp32.flash", self.flash_args(self.put(merged()), target="p"))
        self.assertRefusedWithoutRunning(res, "protected")

    def test_the_host_lock_held_by_someone_else_refuses(self):
        fid = self.put(merged())
        other = HostLock(self.host.lock_path)
        other.acquire("somebody else")
        self.addCleanup(other.release)
        res = self.call("esp32.flash", self.flash_args(fid))
        self.assertRefusedWithoutRunning(res, "lock_held")

    def test_the_lock_is_released_after_a_flash(self):
        fid = self.put(merged())
        self.call("esp32.flash", self.flash_args(fid))
        again = HostLock(self.host.lock_path)
        again.acquire("proves it was released")
        again.release()


class Identify(Base):
    def setUp(self):
        Base.setUp(self)
        self.declare()

    def test_it_reads_the_chip_and_the_mac(self):
        res = self.call("esp32.identify", {"target": "bench-esp", "bound_s": 10.0})
        self.assertTrue(res.ok, res.message)
        self.assertEqual((res.data["chip"], res.data["mac"]), ("ESP32-D0WD-V3 (revision v3.1)", "24:6f:28:aa:bb:cc"))
        self.assertEqual(self.tool.calls[0][0][-1], "chip-id")

    def test_an_adapter_with_no_esp32_behind_it_fails_and_says_what_to_check(self):
        self.tool.rc, self.tool.out = 2, "A fatal error occurred: Failed to connect\n"
        res = self.call("esp32.identify", {"target": "bench-esp", "bound_s": 10.0})
        self.assertEqual((res.cls, res.code), ("failed", "flash_failed"))
        self.assertIn("wired to reset", res.message)

    def test_listing_never_runs_the_tool(self):
        self.call("esp32.targets")
        self.assertEqual(self.tool.calls, [])


class Reset(Base):
    def setUp(self):
        Base.setUp(self)
        self.declare()

    def test_run_resets_to_the_application_and_bootloader_leaves_the_chip_waiting(self):
        for mode, after in (("run", "hard-reset"), ("bootloader", "no-reset")):
            self.tool.calls.clear()
            res = self.call("esp32.reset", {"target": "bench-esp", "mode": mode, "bound_s": 10.0})
            self.assertTrue(res.ok, res.message)
            cmd = self.tool.calls[0][0]
            self.assertEqual((cmd[cmd.index("--after") + 1], cmd[-1], res.data["mode"]), (after, "chip-id", mode))
        self.assertIn("waiting", res.message)

    def test_a_mode_that_is_not_one_is_a_usage_error_and_runs_nothing(self):
        with self.assertRaises(UsageError):
            self.call("esp32.reset", {"target": "bench-esp", "mode": "sideways", "bound_s": 10.0})
        self.assertEqual(self.tool.calls, [])

    def test_a_chip_that_does_not_answer_is_a_failure_not_a_reset(self):
        self.tool.rc, self.tool.out = 2, "Failed to connect\n"
        res = self.call("esp32.reset", {"target": "bench-esp", "mode": "run", "bound_s": 10.0})
        self.assertEqual((res.cls, res.code), ("failed", "flash_failed"))

    def test_an_unknown_target_a_held_port_and_a_held_lock_each_refuse_without_running(self):
        self.assertEqual(self.call("esp32.reset", {"target": "no", "mode": "run", "bound_s": 5.0}).code, "target_unknown")
        self.t.hold(4243, os.path.join(self.t.dev, "ttyUSB0"), "minicom")
        self.assertEqual(self.call("esp32.reset", {"target": "bench-esp", "mode": "run", "bound_s": 5.0}).code, "port_held")
        self.assertEqual(self.tool.calls, [])


class FakeBroker(object):
    """What the commands ask of the console: its links, and to release and retake a port."""

    def __init__(self, log):
        self.log, self.links = log, []

    def set_links(self, links):
        self.links = list(links)

    def suspend(self, name):
        self.log.append("suspend")
        return True

    def resume(self, name):
        self.log.append("resume")

    def state(self, name):
        return None


class Console(Base):
    def setUp(self):
        Base.setUp(self)
        self.log = []
        self.host.console = FakeBroker(self.log)
        self.tool.calls = self.log                                     # one list, so the ORDER of tool and console shows
        self.tool.seen = []

    def test_a_declared_target_is_a_receive_only_console_link_of_its_own_bridge(self):
        self.declare()
        (link,) = self.host.console.links
        self.assertEqual((link.name, link.device, link.own_bridge, link.dtr_rts_reach_dut, link.usable),
                         ("bench-esp", "bench-esp", True, True, True))
        self.assertEqual((link.serial, link.adapter_slot, link.baud, link.host_tx_to_dut), (SERIAL, None, 115200, False))
        self.assertEqual(link.can_send()[1], "link_rx_only")
        self.assertEqual(self.host.console_links, [link])

    def test_a_slot_target_is_found_by_its_slot_and_a_baud_can_be_given(self):
        self.call("esp32.declare", {"name": "p", "slot": "1-2", "baud": 921600})
        (link,) = self.host.console.links
        self.assertEqual((link.serial, link.adapter_slot, link.baud), (None, "1-2", 921600))

    def test_a_baud_that_is_not_a_console_rate_is_a_usage_error(self):
        for baud in (1200, 12345):
            with self.assertRaises(UsageError, msg=str(baud)):
                self.call("esp32.declare", {"name": "p", "slot": "1-2", "baud": baud})

    def test_forgetting_a_target_takes_its_link_away(self):
        self.declare()
        self.call("esp32.forget", {"name": "bench-esp"})
        self.assertEqual(self.host.console.links, [])

    def test_the_console_is_released_before_esptool_runs_and_retaken_after_it_for_every_command(self):
        self.declare()
        fid = self.put(merged())
        self.log[:] = []
        self.call("esp32.flash", self.flash_args(fid))
        self.assertEqual([x if isinstance(x, str) else "esptool" for x in self.log], ["suspend", "esptool", "resume"])
        self.log[:] = []
        self.call("esp32.identify", {"target": "bench-esp", "bound_s": 5.0})
        self.call("esp32.reset", {"target": "bench-esp", "mode": "run", "bound_s": 5.0})
        self.assertEqual([x if isinstance(x, str) else "esptool" for x in self.log],
                         ["suspend", "esptool", "resume"] * 2)

    def test_the_console_is_retaken_even_when_esptool_fails_or_the_flash_is_refused(self):
        self.declare()
        fid = self.put(merged())
        self.log[:] = []
        self.tool.rc, self.tool.out = 2, "Failed to connect\n"
        self.assertEqual(self.call("esp32.flash", self.flash_args(fid)).code, "flash_failed")
        self.assertEqual(self.call("esp32.flash", self.flash_args(fid, baud=1)).code, "baud_refused")
        self.assertEqual([x for x in self.log if isinstance(x, str)], ["suspend", "resume"] * 2)

    def test_a_target_that_does_not_exist_never_touches_the_console(self):
        self.call("esp32.flash", self.flash_args("0" * 64, target="nope"))
        self.assertEqual([x for x in self.log if isinstance(x, str)], [])

    def test_a_target_named_like_a_wiring_connection_is_listed_with_the_problem_and_never_opened(self):
        from nrf_workbench.wiring import commands as wiring_commands
        self.declare()
        record = {"name": "bench-esp", "intent": "log", "device": "RAK1", "adapter": {"ids": ["1a86:55d4"], "serial": "S9"},
                  "baud": 115200, "send": False, "voltage_measured": None}
        by_name = dict((l.name, l) for l in wiring_commands.links_for(self.host, [record]))
        both = wiring_commands.links_for(self.host, [record])
        self.assertEqual([l.name for l in both], ["bench-esp", "bench-esp"])
        self.assertTrue(both[0].usable)
        self.assertFalse(both[1].usable)
        self.assertIn("used by another link", both[1].problems[0].message)
        self.assertEqual(len(by_name), 1)

    def test_the_wiring_page_is_told_the_adapter_belongs_to_a_target(self):
        from nrf_workbench.wiring import commands as wiring_commands
        self.declare()
        self.assertEqual(wiring_commands._esp_holder(self.host, SERIAL), "bench-esp")
        self.assertIsNone(wiring_commands._esp_holder(self.host, "OTHER"))
        self.assertIsNone(wiring_commands._esp_holder(self.host, None))


class Registration(Base):
    def test_commands_appear_only_with_what_they_need(self):
        names = lambda caps: [c.name for c in self.reg.commands(caps) if c.name.startswith("esp32.")]  # noqa: E731
        self.assertEqual(names(set()), [])
        self.assertEqual(names({"wiring"}), ["esp32.declare", "esp32.forget", "esp32.targets"])
        self.assertEqual(names({"wiring", "esp32"}), ["esp32.declare", "esp32.forget", "esp32.identify", "esp32.reset",
                                                       "esp32.targets"])
        self.assertEqual(names(ALL), ["esp32.declare", "esp32.flash", "esp32.forget", "esp32.identify", "esp32.reset",
                                      "esp32.targets"])


if __name__ == "__main__":
    unittest.main()
