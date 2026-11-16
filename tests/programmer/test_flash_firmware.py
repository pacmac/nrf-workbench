#!/usr/bin/env python3
"""Tests for programmer.flash_firmware (flash by id) on the fake sysfs tree, with a fake touch and a
fake uploader, so each test can state exactly how many touches and uploads happened.

The two that matter most: a package for the WRONG SoftDevice is refused with ZERO touches (the board
stays in its application), and ONE host lock is held across the touch and the upload.
Run:  python3 tests/programmer/test_flash_firmware.py
"""
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench.devices import profile  # noqa: E402
from nrf_workbench.guards.lock import HostLock, LockHeld  # noqa: E402
from nrf_workbench.programmer import flash_file, flash_firmware  # noqa: E402
from nrf_workbench.programmer.firmware_store import Store  # noqa: E402
from test_firmware_store import pkg  # noqa: E402
from test_flash import GOOD_OUTPUT, FakeWatcher, Runner  # noqa: E402

PROFILE = profile.load(os.path.join(ROOT, "profiles", "rak4631.yaml"))
PARKING = "nrfwb-parking 2"


class Touch(object):
    def __init__(self, on_touch=None, raises=None):
        self.calls, self.on_touch, self.raises = [], on_touch, raises

    def __call__(self, node):
        self.calls.append(node)
        if self.raises:
            raise self.raises
        if self.on_touch:
            self.on_touch()


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.lock = os.path.join(self.t.root, "lock")
        self.store = Store(os.path.join(self.t.root, "store"))
        self.data = pkg()
        self.id = self.store.put(io.BytesIO(self.data), len(self.data), name="parking").data["sha256"]
        self.t.node("ttyACM0", 166, 0)

    # the two re-enumerations a real board makes
    def to_bootloader(self):
        self.t.unplug("1-3")
        self.t.bootloader_board()

    def to_app(self, product="WisCore RAK4631 Board"):
        def back():
            self.t.unplug("1-3")
            self.t.usb("1-3", ("239a", "8029"), product, tty="ttyACM0", majmin="166:0")
        return back

    def in_app(self):
        self.t.app_board()

    def in_bootloader(self):
        self.t.bootloader_board()

    def go(self, touch=None, runner=None, ident=None, **kw):
        touch = touch if touch is not None else Touch(on_touch=self.to_bootloader)
        runner = runner or Runner(on_run=self.to_app())
        args = dict(units={}, sysfs=self.t.sysfs, devroot=self.t.dev, proc=self.t.proc, lock_path=self.lock,
                    runner=runner, tool="/fake/adafruit-nrfutil", touch=touch, watcher=FakeWatcher(),
                    enter_bound_s=5.0, flash_bound_s=60.0)
        args.update(kw)
        enter_bound_s, flash_bound_s = args.pop("enter_bound_s"), args.pop("flash_bound_s")
        serial = args.pop("serial", SERIAL)
        r = flash_firmware.flash_firmware(PROFILE, self.store, ident or self.id, serial, enter_bound_s,
                                          flash_bound_s, **args)
        return touch, runner, r

    def assert_untouched(self, touch, runner):
        self.assertEqual(touch.calls, [], "the board must not have been touched")
        self.assertEqual(runner.calls, [], "nothing must have been written")


class FromApp(Base):
    def setUp(self):
        Base.setUp(self)
        self.in_app()

    def test_the_board_in_its_app_is_entered_once_then_flashed_once_from_the_store(self):
        touch, runner, r = self.go()
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertEqual(len(touch.calls), 1)
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0][0]
        self.assertEqual(cmd[cmd.index("-pkg") + 1], self.store.image_path(self.id), "the STORED file is flashed")
        self.assertIs(r.data["entered_bootloader"], True)
        self.assertEqual(r.data["firmware"], {"id": self.id, "name": "parking", "bytes": len(self.data)})
        self.assertIn("handover_s", r.data["entry"])
        self.assertEqual(r.data["app_after"]["id"], "239a:8029")
        self.assertEqual(r.evidence["firmware_id"], self.id)

    def test_a_short_id_prefix_works(self):
        touch, runner, r = self.go(ident=self.id[:8])
        self.assertEqual(r.cls, "ok", r.to_json())

    def test_this_package_is_the_declared_way_back_whatever_the_caller_declared(self):
        # A bogus declaration is replaced: the thing being flashed IS the way back.
        units = {SERIAL: {"return_path": {"method": "flash", "package": "/does/not/exist.zip"}}}
        touch, runner, r = self.go(units=units)
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertEqual(len(touch.calls), 1)

    def test_the_expected_product_is_checked_on_the_device(self):
        _, _, r = self.go(runner=Runner(on_run=self.to_app(PARKING)), expect_product=PARKING)
        self.assertEqual(r.cls, "ok", r.to_json())

    def test_another_product_after_the_flash_is_suspect_and_says_the_board_is_in_the_bootloader_path(self):
        _, _, r = self.go(runner=Runner(on_run=self.to_app("WisCore RAK4631 Board")), expect_product=PARKING)
        self.assertEqual((r.cls, r.code), ("suspect", "flash_suspect"), r.to_json())
        self.assertIs(r.evidence["entered_bootloader"], True)
        self.assertEqual(r.evidence["firmware_id"], self.id)

    def test_a_failed_flash_after_the_entry_says_the_board_is_still_in_its_bootloader(self):
        touch, runner, r = self.go(runner=Runner(rc=1, out="Failed.\n"))
        self.assertEqual((r.cls, r.code), ("failed", "flash_failed"), r.to_json())
        self.assertEqual((len(touch.calls), len(runner.calls)), (1, 1), "one touch, one attempt, no retry")
        self.assertIs(r.evidence["entered_bootloader"], True)
        self.assertIn("still there", r.message)

    def test_flashing_again_by_id_from_the_bootloader_makes_no_second_touch(self):
        touch, _, first = self.go(runner=Runner(rc=1, out="Failed.\n"))
        self.assertEqual(first.code, "flash_failed")
        # The board is now in its bootloader (the fake tree shows what the failed flash left):
        self.t.unplug("1-3")
        self.in_bootloader()
        _, runner, second = self.go(touch=touch)
        self.assertEqual(second.cls, "ok", second.to_json())
        self.assertEqual(len(touch.calls), 1, "the bootloader is never touched again")
        self.assertIs(second.data["entered_bootloader"], False)


class FromBootloader(Base):
    def setUp(self):
        Base.setUp(self)
        self.in_bootloader()

    def test_a_board_already_in_its_bootloader_is_flashed_with_no_touch(self):
        touch, runner, r = self.go()
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertEqual((len(touch.calls), len(runner.calls)), (0, 1))
        self.assertIs(r.data["entered_bootloader"], False)
        self.assertIsNone(r.data["entry"])


class RefusedBeforeAnyTouch(Base):
    def setUp(self):
        Base.setUp(self)
        self.in_app()

    def test_an_unknown_id(self):
        touch, runner, r = self.go(ident="deadbeef")
        self.assertEqual((r.cls, r.code), ("refused", "firmware_unknown"))
        self.assert_untouched(touch, runner)

    def test_a_stored_file_altered_after_upload(self):
        with open(self.store.image_path(self.id), "r+b") as f:
            f.seek(10)
            b = f.read(1)
            f.seek(10)
            f.write(bytes([b[0] ^ 0xFF]))
        touch, runner, r = self.go()
        self.assertEqual((r.cls, r.code), ("refused", "bad_package"), r.to_json())
        self.assert_untouched(touch, runner)

    def test_a_package_for_the_wrong_softdevice_is_refused_with_the_board_still_in_its_app(self):
        other = pkg(seed=9, softdevice=(140,))
        wrong = self.store.put(io.BytesIO(other), len(other)).data["sha256"]
        touch, runner, r = self.go(ident=wrong)
        self.assertEqual((r.cls, r.code), ("refused", "wrong_softdevice"), r.to_json())
        self.assertEqual(r.evidence["firmware_id"], wrong)
        self.assert_untouched(touch, runner)

    def test_each_bound_is_required(self):
        # flash_bound_s=0 matters most: without the early check the board would be touched and only
        # THEN would the flash step reject the bound, leaving it stranded in its bootloader.
        for kw in ({"enter_bound_s": None}, {"flash_bound_s": None}, {"enter_bound_s": 0}, {"flash_bound_s": 0},
                   {"flash_bound_s": -1}, {"enter_bound_s": True}, {"flash_bound_s": True}):
            touch, runner, r = self.go(**kw)
            self.assertEqual((r.cls, r.code), ("refused", "no_bound"), kw)
            self.assert_untouched(touch, runner)

    def test_no_serial(self):
        touch, runner, r = self.go(serial="")
        self.assertEqual(r.code, "serial_unverifiable")
        self.assert_untouched(touch, runner)

    def test_no_uploader_is_found_before_the_board_is_touched(self):
        with mock.patch.object(flash_file, "find_tool", lambda: None):
            touch, runner, r = self.go(tool=None)
        self.assertEqual((r.cls, r.code), ("refused", "capability_missing"))
        self.assert_untouched(touch, runner)

    def test_another_instance_holding_the_lock(self):
        other = HostLock(self.lock)
        other.acquire("someone else")
        try:
            touch, runner, r = self.go()
        finally:
            other.release()
        self.assertEqual((r.cls, r.code), ("refused", "lock_held"))
        self.assert_untouched(touch, runner)

    def test_a_protected_unit_is_refused_by_the_entry_with_nothing_touched(self):
        touch, runner, r = self.go(units={SERIAL: {"protected": True}})
        self.assertEqual((r.cls, r.code), ("refused", "protected"), r.to_json())
        self.assertEqual(r.evidence["firmware_id"], self.id)
        self.assertIs(r.evidence["entered_bootloader"], False)
        self.assert_untouched(touch, runner)

    def test_a_board_that_is_not_there(self):
        self.t.unplug("1-3")
        touch, runner, r = self.go()
        self.assertEqual(r.cls, "refused", r.to_json())
        self.assert_untouched(touch, runner)

    def test_a_failed_touch_is_reported_as_unknown_state_not_as_not_entered(self):
        touch = Touch(raises=OSError("could not open"))
        touch_, runner, r = self.go(touch=touch)
        self.assertEqual((r.cls, r.code), ("failed", "touch_failed"), r.to_json())
        self.assertEqual(len(touch.calls), 1)
        self.assertEqual(runner.calls, [])
        self.assertIsNone(r.evidence["entered_bootloader"])


class OneLock(Base):
    def setUp(self):
        Base.setUp(self)
        self.in_app()

    def other_can_take_the_lock(self):
        other = HostLock(self.lock)
        try:
            other.acquire("an intruder")
        except LockHeld:
            return False
        other.release()
        return True

    def test_the_lock_is_held_across_the_touch_and_the_upload(self):
        seen = {}

        def at_touch():
            seen["at_touch"] = self.other_can_take_the_lock()
            self.to_bootloader()

        def at_upload():
            seen["at_upload"] = self.other_can_take_the_lock()
            self.to_app()()

        _, _, r = self.go(touch=Touch(on_touch=at_touch), runner=Runner(on_run=at_upload))
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertEqual(seen, {"at_touch": False, "at_upload": False})
        self.assertTrue(self.other_can_take_the_lock(), "and it is released afterwards")

    def test_the_lock_is_released_after_a_refusal_and_after_a_failure(self):
        # Keep every lock object alive: a lock that is merely garbage-collected is released by accident,
        # which would hide a missing release().
        made = []
        real = flash_firmware.HostLock

        def keep(*a, **kw):
            made.append(real(*a, **kw))
            return made[-1]

        with mock.patch.object(flash_firmware, "HostLock", keep):
            self.go(ident="deadbeef")
            self.assertTrue(self.other_can_take_the_lock())
            self.go(runner=Runner(rc=1, out="Failed.\n"))
            self.assertTrue(self.other_can_take_the_lock())
            self.go(runner=Runner(on_run=self.to_app()))
        self.assertTrue(made)
        self.assertTrue(self.other_can_take_the_lock())


if __name__ == "__main__":
    unittest.main(verbosity=1)
