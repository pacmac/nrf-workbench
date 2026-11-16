#!/usr/bin/env python3
"""Tests for programmer.flash_file against a fake sysfs and a fake uploader.

The uploader is injected as a recorder, so each test can state exactly how many times it was run:
ZERO for every refusal (nothing is written), ONE for a success, and ONE, never two, for a failure.
Run:  python3 tests/programmer/test_flash.py
"""
import json
import os
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench.devices import profile  # noqa: E402
from nrf_workbench.devices.events import WaitResult  # noqa: E402
from nrf_workbench.guards import checks  # noqa: E402
from nrf_workbench.guards.lock import HostLock  # noqa: E402
from nrf_workbench.programmer import flash_file  # noqa: E402

PROFILE = profile.load(os.path.join(ROOT, "profiles", "rak4631.yaml"))
IMAGE_BYTES = 1152            # three 512-byte packets, so a complete transfer prints exactly 3 marks
GOOD_OUTPUT = ("Upgrading target on <node> with DFU package <package>. Flow control is disabled, Single bank, "
               "Touch disabled\n###\nActivating new firmware\nDevice programmed.\n")


def make_package(path, image_bytes=IMAGE_BYTES, softdevice=(182,), manifest=True, image_name="firmware.bin"):
    with zipfile.ZipFile(path, "w") as z:
        if manifest:
            z.writestr("manifest.json", json.dumps({"manifest": {"application": {
                "bin_file": image_name, "dat_file": "firmware.dat",
                "init_packet_data": {"softdevice_req": list(softdevice), "device_type": 82}}}}))
        z.writestr(image_name, b"\0" * image_bytes)
        z.writestr("firmware.dat", b"\0" * 14)
    return path


class Runner(object):
    """Stands in for adafruit-nrfutil. Records every run."""

    def __init__(self, rc=0, out=GOOD_OUTPUT, secs=0.5, timed_out=False, on_run=None):
        self.calls, self.rc, self.out, self.secs, self.timed_out, self.on_run = [], rc, out, secs, timed_out, on_run

    def __call__(self, cmd, timeout_s):
        self.calls.append((cmd, timeout_s))
        if self.on_run:
            self.on_run()
        return self.rc, self.out, self.secs, self.timed_out


class FakeWatcher(object):
    def __init__(self, reachable=True):
        self.reachable, self.opened, self.closed = reachable, False, False

    def open(self):
        self.opened = True

    def wait(self, predicate, bound_s):
        ok = predicate() if self.reachable else False
        return WaitResult(ok, 0.2, "fake", "" if ok else "bound reached")

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.lock = os.path.join(self.t.root, "lock")
        self.pkg = make_package(os.path.join(self.t.root, "app.zip"))
        self.t.node("ttyACM0", 166, 0)
        self.t.bootloader_board()

    def app_returns(self):
        self.t.unplug("1-3")
        self.t.app_board()

    def go(self, runner=None, watcher=None, **kw):
        runner = runner or Runner(on_run=self.app_returns)
        args = dict(units={}, sysfs=self.t.sysfs, devroot=self.t.dev, proc=self.t.proc, lock_path=self.lock,
                    runner=runner, tool="/fake/adafruit-nrfutil", watcher=watcher or FakeWatcher())
        args.update(kw)
        serial, package, bound = args.pop("serial", SERIAL), args.pop("package", self.pkg), args.pop("bound", 60.0)
        return runner, flash_file.flash_file(PROFILE, serial, package, bound, **args)


class Verdict(Base):
    def test_success_runs_once_with_the_right_command_and_reports_the_return(self):
        runner, r = self.go()
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0][0]
        self.assertEqual(cmd[:4], ["/fake/adafruit-nrfutil", "dfu", "serial", "-pkg"])
        self.assertEqual(cmd[cmd.index("-b") + 1], "115200")
        self.assertIn("--singlebank", cmd)
        self.assertEqual(cmd[cmd.index("-p") + 1], os.path.join(self.t.dev, "ttyACM0"), "the node came from identity")
        self.assertEqual(r.data["app_after"]["id"], "239a:8029")
        self.assertIn("Device programmed.", r.data["output"])

    def test_expected_packets_is_the_image_over_the_packet_size_rounded_up(self):
        for size, want in ((1, 1), (512, 1), (513, 2), (1152, 3), (332092, 649)):
            self.assertEqual(flash_file.expected_packets(size), want, size)

    def test_a_short_duration_alone_is_not_a_veto(self):
        # The real case: 332 092 bytes took 21.554 s, completed, and an earlier draft wrongly called it suspect.
        runner, r = self.go(runner=Runner(secs=0.0001, on_run=self.app_returns))
        self.assertEqual(r.cls, "ok", "duration is recorded, not judged: " + r.to_json())
        self.assertIn("throughput_bytes_per_s", r.data)

    def test_fewer_marks_than_packets_is_a_partial_write_and_suspect(self):
        out = GOOD_OUTPUT.replace("###", "##")
        runner, r = self.go(runner=Runner(out=out, secs=9999.0, on_run=self.app_returns))
        self.assertEqual((r.cls, r.code), ("suspect", "flash_suspect"), "however long it took: " + r.to_json())
        self.assertEqual(r.exit_code(), 4)
        self.assertEqual((r.evidence["marks"], r.evidence["expected_packets"]), (2, 3))

    def test_more_marks_than_packets_is_also_suspect(self):
        _, r = self.go(runner=Runner(out=GOOD_OUTPUT.replace("###", "####"), on_run=self.app_returns))
        self.assertEqual(r.code, "flash_suspect")

    def test_no_marks_at_all_is_suspect(self):
        _, r = self.go(runner=Runner(out="Device programmed.\n", on_run=self.app_returns))
        self.assertEqual(r.code, "flash_suspect")

    def test_the_REAL_output_of_the_first_flash_is_judged_ok(self):
        with open(os.path.join(HERE, "fixtures", "real_flash_output.txt")) as fh:
            real = fh.read()
        self.assertEqual(real.count("#"), 649)
        pkg = make_package(os.path.join(self.t.root, "real-size.zip"), image_bytes=332092)
        runner, r = self.go(runner=Runner(out=real, secs=21.554, on_run=self.app_returns), package=pkg)
        self.assertEqual(r.cls, "ok", "the real 21.554 s flash must pass: " + r.to_json())
        self.assertEqual((r.data["packets"], r.data["marks"]), (649, 649))

    def test_exit_zero_without_the_success_line_is_a_failure(self):
        runner, r = self.go(runner=Runner(out="Upgrading target\nsomething else\n"))
        self.assertEqual((r.cls, r.code), ("failed", "flash_failed"))

    def test_the_success_line_with_a_failing_exit_status_is_still_a_failure(self):
        runner, r = self.go(runner=Runner(rc=2, out=GOOD_OUTPUT, on_run=self.app_returns))
        self.assertEqual((r.cls, r.code), ("failed", "flash_failed"), "both the output AND the exit status must agree")

    def test_a_failure_returns_the_WHOLE_output_and_is_not_retried(self):
        long_out = "".join("line %d\n" % i for i in range(500)) + "ERROR: bad\n"
        runner, r = self.go(runner=Runner(rc=1, out=long_out))
        self.assertEqual(r.code, "flash_failed")
        self.assertEqual(r.evidence["output"], long_out, "never a tail")
        self.assertEqual(len(runner.calls), 1, "a retry is a new, deliberate call")

    def test_a_timeout_is_a_failure_with_the_partial_output(self):
        runner, r = self.go(runner=Runner(rc=-1, out="####\n", timed_out=True))
        self.assertEqual((r.cls, r.code), ("failed", "flash_failed"))
        self.assertIn("bound", r.message)
        self.assertEqual(runner.calls[0][1], 60.0, "the caller's bound is the uploader's ceiling")

    def test_the_application_not_returning_is_reported(self):
        runner, r = self.go(runner=Runner(), watcher=FakeWatcher(reachable=False))
        self.assertEqual((r.cls, r.code), ("failed", "run_node_not_returned"), r.to_json())
        self.assertEqual(len(runner.calls), 1)

    def returns_as(self, product):
        def back():
            self.t.unplug("1-3")
            self.t.usb("1-3", ("239a", "8029"), product, tty="ttyACM0", majmin="166:0")
        return back

    def test_the_returned_device_must_report_the_expected_product(self):
        runner, r = self.go(runner=Runner(on_run=self.returns_as("nrfwb-parking 1")), expect_product="nrfwb-parking 1")
        self.assertEqual(r.cls, "ok", r.to_json())

    def test_a_different_product_after_the_flash_is_suspect(self):
        # The write reported success and the board is back, but it is not the image that was written.
        runner, r = self.go(runner=Runner(on_run=self.returns_as("WisCore RAK4631 Board")),
                            expect_product="nrfwb-parking 1")
        self.assertEqual((r.cls, r.code), ("suspect", "flash_suspect"), r.to_json())
        self.assertEqual(r.evidence["reported_product"], "WisCore RAK4631 Board")
        self.assertEqual(r.evidence["expected_product"], "nrfwb-parking 1")

    def test_with_no_expected_product_nothing_is_checked(self):
        _, r = self.go(runner=Runner(on_run=self.returns_as("anything at all")))
        self.assertEqual(r.cls, "ok")

    def test_the_event_source_is_opened_before_the_write_and_closed_after(self):
        w = FakeWatcher()
        self.go(watcher=w)
        self.assertTrue(w.opened and w.closed)


class Refusals(Base):
    """Every refusal: nothing was written."""

    def refused(self, code, **kw):
        runner, r = self.go(**kw)
        self.assertEqual((r.cls, r.code), ("refused", code), r.to_json())
        self.assertEqual(runner.calls, [], "a refusal must not run the uploader")
        return r

    def test_no_bound(self):
        self.refused("no_bound", bound=None)
        self.refused("no_bound", bound=0)

    def test_the_board_is_running_its_application(self):
        self.t.unplug("1-3")
        self.t.app_board()
        r = self.refused("not_in_bootloader")
        self.assertEqual(r.evidence["state"], "app")

    def test_board_absent(self):
        self.t.unplug("1-3")
        self.refused("node_absent")

    def test_wrong_serial(self):
        self.refused("node_absent", serial="SOMETHINGELSE")

    def test_no_serial_named(self):
        self.refused("serial_unverifiable", serial="")

    def test_protected(self):
        self.refused("protected", units={SERIAL: {"protected": True}})

    def test_attached_but_no_node(self):
        os.remove(os.path.join(self.t.dev, "ttyACM0"))
        r = self.refused("node_absent")
        self.assertEqual(r.evidence["state"], "attached_no_node")

    def test_port_held_through_another_alias(self):
        alias = self.t.node("ttyACM5", 166, 0)
        self.t.hold(777, alias, "screen monitor")
        r = self.refused("port_held")
        self.assertEqual(r.evidence["holders"][0]["pid"], 777)

    def test_lock_held(self):
        other = HostLock(self.lock).acquire("entering the bootloader")
        self.addCleanup(other.release)
        self.refused("lock_held")

    def test_the_uploader_is_missing(self):
        from unittest import mock
        with mock.patch.object(flash_file, "find_tool", return_value=None):
            runner = Runner()
            r = flash_file.flash_file(PROFILE, SERIAL, self.pkg, 60.0, units={}, sysfs=self.t.sysfs,
                                      devroot=self.t.dev, proc=self.t.proc, lock_path=self.lock, runner=runner,
                                      tool=None, watcher=FakeWatcher())
        self.assertEqual((r.cls, r.code), ("refused", "capability_missing"))
        self.assertEqual(runner.calls, [])

    def test_package_not_a_dfu_zip(self):
        for name, make in (("missing.zip", None), ("hex.hex", "hex"), ("nomanifest.zip", "nomanifest"), ("trunc.zip", "trunc")):
            path = os.path.join(self.t.root, name)
            if make == "hex":
                with open(path, "w") as fh:
                    fh.write(":020000040000FA\n")
            elif make == "nomanifest":
                make_package(path, manifest=False)
            elif make == "trunc":
                make_package(path)
                with open(path, "r+b") as fh:
                    fh.truncate(60)
            self.refused("bad_package", package=path)

    def test_wrong_softdevice(self):
        path = make_package(os.path.join(self.t.root, "s132.zip"), softdevice=(101,))
        r = self.refused("wrong_softdevice", package=path)
        self.assertEqual(r.evidence["package_requires"], [101])
        self.assertEqual(r.evidence["device_has"], 182)


class Held(Base):
    """A caller that already holds the host lock (programmer.flash holds one across enter and flash)."""

    def test_a_lock_the_caller_holds_is_neither_taken_again_nor_released(self):
        mine = HostLock(self.lock)
        mine.acquire("the caller")
        try:
            _, r = self.go(held=mine)
            self.assertEqual(r.cls, "ok", r.to_json())          # taking it again would be lock_held
            with self.assertRaises(Exception) as caught:         # and it is STILL held afterwards
                HostLock(self.lock).acquire("someone else")
            self.assertEqual(type(caught.exception).__name__, "LockHeld")
        finally:
            mine.release()

    def test_without_held_the_lock_is_taken_and_released_as_before(self):
        _, r = self.go()
        self.assertEqual(r.cls, "ok")
        other = HostLock(self.lock)
        other.acquire("free again")
        other.release()


class PackageCheck(unittest.TestCase):
    def test_reads_the_image_size_the_verdict_is_derived_from(self):
        d = tempfile.mkdtemp()
        refusal, info = checks.dfu_package(PROFILE, make_package(os.path.join(d, "p.zip"), image_bytes=4000))
        self.assertIsNone(refusal)
        self.assertEqual(info["image_bytes"], 4000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
