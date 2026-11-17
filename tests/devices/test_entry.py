#!/usr/bin/env python3
"""Tests for identity, the guards and enter_bootloader(), against a fake sysfs.

The point of most of them: a REFUSAL opens nothing. The touch is injected as a recorder, so each
test can state exactly how many times the port was opened (zero for every refusal, one for a
success, and one, never two, for a touch that did not work). Run:  python3 tests/devices/test_entry.py
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fakes import SERIAL, Tree, make_dfu_zip  # noqa: E402
from nrf_workbench.devices import entry, identity, profile  # noqa: E402
from nrf_workbench.guards import checks  # noqa: E402
from nrf_workbench.guards.lock import HostLock, LockHeld  # noqa: E402

PROFILE = profile.load(os.path.join(ROOT, "profiles", "rak4631.yaml"))


class Touch(object):
    """Records every open of the port. The real one opens a serial port; this one only counts."""

    def __init__(self, on_touch=None, raises=None):
        self.calls = []
        self.on_touch = on_touch
        self.raises = raises

    def __call__(self, node):
        self.calls.append(node)
        if self.raises:
            raise self.raises
        if self.on_touch:
            self.on_touch()


class FakeWatcher(object):
    """Stands in for the uevent socket: waits by evaluating the level, as the real one does."""

    def __init__(self, reachable=True, elapsed=0.25):
        self.reachable, self.elapsed, self.opened, self.closed = reachable, elapsed, False, False

    def open(self):
        self.opened = True

    def wait(self, predicate, bound_s):
        from nrf_workbench.devices.events import WaitResult
        ok = predicate() if self.reachable else False
        return WaitResult(ok, self.elapsed, "fake", "" if ok else "bound reached")

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.lock = os.path.join(self.t.root, "lock")
        self.zip = make_dfu_zip(os.path.join(self.t.root, "app.zip"))
        self.units = {SERIAL: {"return_path": {"method": "flash", "package": self.zip}}}

    def attach_app(self):
        self.t.node("ttyACM0", 166, 0)
        self.t.app_board()

    def go(self, touch=None, watcher=None, **kw):
        touch = touch or Touch()
        args = dict(units=self.units, sysfs=self.t.sysfs, devroot=self.t.dev, proc=self.t.proc,
                    lock_path=self.lock, touch=touch, watcher=watcher or FakeWatcher())
        args.update(kw)
        serial = args.pop("serial", SERIAL)
        bound = args.pop("bound", 5.0)
        return touch, entry.enter_bootloader(PROFILE, serial, bound, **args)


class Identity(Base):
    def test_state_and_node_join(self):
        self.attach_app()
        inv = identity.inventory(self.t.sysfs)
        self.assertEqual(identity.state(PROFILE, SERIAL, inv), "app")
        self.assertEqual(identity.state(PROFILE, "OTHER", inv), "absent")
        node, why = identity.node_for(identity.board_devices(PROFILE, inv)[0], self.t.dev)
        self.assertEqual(node, os.path.join(self.t.dev, "ttyACM0"), why)

    def test_attached_but_no_node_is_not_absent(self):
        self.t.app_board()                    # sysfs lists it, /dev has no node
        inv = identity.inventory(self.t.sysfs)
        self.assertEqual(identity.state(PROFILE, SERIAL, inv), "app")
        node, why = identity.node_for(identity.board_devices(PROFILE, inv)[0], self.t.dev)
        self.assertIsNone(node)
        self.assertIn("no such node", why)

    def test_a_node_that_is_not_a_char_device_is_refused(self):
        self.t.app_board()
        open(os.path.join(self.t.dev, "ttyACM0"), "w").close()      # a regular file with the right name
        node, why = identity.node_for(identity.board_devices(PROFILE, identity.inventory(self.t.sysfs))[0], self.t.dev)
        self.assertIsNone(node)
        self.assertIn("not a character device", why)

    def test_a_wrong_device_number_is_refused(self):
        self.t.node("ttyACM0", 166, 9)
        self.t.app_board(majmin="166:0")
        node, why = identity.node_for(identity.board_devices(PROFILE, identity.inventory(self.t.sysfs))[0], self.t.dev)
        self.assertIsNone(node)
        self.assertIn("different device number", why)

    def test_two_modes_at_once(self):
        self.t.app_board(name="1-3")
        self.t.bootloader_board(name="1-4")
        self.assertEqual(identity.state(PROFILE, SERIAL, identity.inventory(self.t.sysfs)), "two")


class Refusals(Base):
    """Every refusal: nothing was opened."""

    def refused(self, code, **kw):
        touch, r = self.go(**kw)
        self.assertEqual((r.cls, r.code), ("refused", code), r.to_json())
        self.assertEqual(touch.calls, [], "a refusal must not open the port")
        return r

    def test_no_bound(self):
        self.attach_app()
        self.refused("no_bound", bound=None)
        self.refused("no_bound", bound=0)

    def test_board_absent(self):
        self.refused("node_absent")

    def test_attached_no_node(self):
        self.t.app_board()
        r = self.refused("node_absent")
        self.assertEqual(r.evidence["state"], "attached_no_node")

    def test_wrong_serial(self):
        self.attach_app()
        self.refused("node_absent", serial="SOMETHINGELSE")

    def test_no_serial_named(self):
        self.attach_app()
        self.refused("serial_unverifiable", serial="")

    def test_device_exposes_no_serial(self):
        self.t.node("ttyACM0", 166, 0)
        self.t.app_board(serial=None)
        self.refused("node_absent")                      # nothing can match a serial it does not have

    def test_protected(self):
        self.attach_app()
        self.units[SERIAL]["protected"] = True
        self.refused("protected")

    def test_no_return_path(self):
        self.attach_app()
        self.units = {SERIAL: {}}
        self.refused("no_return_path")

    def test_return_package_missing_or_not_a_package(self):
        self.attach_app()
        self.units[SERIAL]["return_path"]["package"] = os.path.join(self.t.root, "nope.zip")
        self.refused("no_return_path")
        self.units[SERIAL]["return_path"]["package"] = make_dfu_zip(os.path.join(self.t.root, "bad.zip"), with_manifest=False)
        self.refused("no_return_path")

    def test_port_held_by_another_process_even_through_another_alias(self):
        self.attach_app()
        alias = self.t.node("ttyACM5", 166, 0)             # a second node for the SAME device number
        self.t.hold(4242, alias, "python3 monitor.py")
        r = self.refused("port_held")
        self.assertEqual(r.evidence["holders"][0]["pid"], 4242)

    def test_lock_held_by_another_instance(self):
        self.attach_app()
        other = HostLock(self.lock).acquire("flashing")
        self.addCleanup(other.release)
        r = self.refused("lock_held")
        self.assertIn("flashing", r.evidence["holder"])

    def test_entry_method_not_implemented_for_the_profile(self):
        self.attach_app()
        other = dict(PROFILE, bootloader=dict(PROFILE["bootloader"], entry="gpio-on-reset"))
        touch = Touch()
        r = entry.enter_bootloader(other, SERIAL, 5.0, units=self.units, sysfs=self.t.sysfs, devroot=self.t.dev,
                                   proc=self.t.proc, lock_path=self.lock, touch=touch, watcher=FakeWatcher())
        self.assertEqual(r.code, "capability_missing")
        self.assertEqual(touch.calls, [])


class Touching(Base):
    def reenumerate(self):
        """What the board does after the touch: the app disappears, the bootloader appears."""
        self.t.unplug("1-3")
        self.t.bootloader_board()

    def test_a_lock_the_caller_holds_is_neither_taken_again_nor_released(self):
        self.attach_app()
        mine = HostLock(self.lock)
        mine.acquire("the caller")
        try:
            _, r = self.go(touch=Touch(on_touch=self.reenumerate), held=mine)
            self.assertEqual(r.cls, "ok", r.to_json())           # taking it again would be lock_held
            with self.assertRaises(LockHeld):                    # and it is STILL held afterwards
                HostLock(self.lock).acquire("someone else")
        finally:
            mine.release()

    def test_success_touches_exactly_once_and_measures(self):
        self.attach_app()
        touch = Touch(on_touch=self.reenumerate)
        _, r = self.go(touch=touch)
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertEqual(touch.calls, [os.path.join(self.t.dev, "ttyACM0")], "exactly one touch, on the node found by identity")
        self.assertEqual(r.data["before"]["id"], "239a:8029")
        self.assertEqual(r.data["after"]["id"], "239a:0029")
        self.assertIs(r.data["serial_same"], True)
        self.assertIn("handover_s", r.data)

    def test_serial_that_changes_across_the_mode_is_reported(self):
        self.attach_app()
        touch = Touch(on_touch=lambda: (self.t.unplug("1-3"), self.t.bootloader_board(serial="DIFFERENT")))
        _, r = self.go(touch=touch)
        self.assertEqual(r.cls, "ok", r.to_json())
        self.assertIs(r.data["serial_same"], False, "a changed serial must be reported, not hidden")

    def test_a_touch_that_does_not_reach_the_bootloader_is_not_retried(self):
        self.attach_app()
        touch = Touch()                                         # the board never re-enumerates
        w = FakeWatcher(reachable=False)
        _, r = self.go(touch=touch, watcher=w)
        self.assertEqual((r.cls, r.code), ("failed", "bootloader_not_reached"), r.to_json())
        self.assertEqual(len(touch.calls), 1, "never retry: a retry is another reset")
        self.assertTrue(w.opened and w.closed, "the event source is opened before the touch and closed after")

    def test_a_failing_touch_is_one_attempt_and_is_reported(self):
        self.attach_app()
        touch = Touch(raises=OSError("device busy"))
        _, r = self.go(touch=touch)
        self.assertEqual((r.cls, r.code), ("failed", "touch_failed"))
        self.assertEqual(len(touch.calls), 1)

    def test_already_in_the_bootloader_touches_nothing(self):
        self.t.node("ttyACM0", 166, 0)
        self.t.bootloader_board()
        touch, r = self.go()
        self.assertEqual(r.cls, "ok")
        self.assertTrue(r.data["already_in_bootloader"])
        self.assertEqual(touch.calls, [])

    def test_the_lock_is_released_so_a_second_call_can_run(self):
        self.attach_app()
        self.go(touch=Touch(on_touch=self.reenumerate))
        other = HostLock(self.lock).acquire("next")         # would raise LockHeld if entry kept the lock
        other.release()


class NeverTouchTwice(Base):
    """Peter, 2026-11-16: a second call must never touch a board already in DFU. The cases: after a
    success, after a timeout, after a failure, with no readable enumeration, with a broken ledger."""

    def ledger_path(self):
        return self.lock + ".touches.json"

    def reenumerate_to_bootloader(self):
        self.t.unplug("1-3")
        self.t.bootloader_board()

    def test_called_again_after_success_touches_nothing(self):
        self.attach_app()
        touch = Touch(on_touch=self.reenumerate_to_bootloader)
        _, first = self.go(touch=touch)
        self.assertEqual(first.cls, "ok")
        _, second = self.go(touch=touch)
        self.assertEqual((second.cls, second.code), ("ok", "ok"))
        self.assertTrue(second.data.get("already_in_bootloader"))
        self.assertEqual(len(touch.calls), 1, "the second call must not touch a board already in the bootloader")

    def test_a_second_call_after_a_timeout_is_refused_not_retried(self):
        self.attach_app()
        touch = Touch()                                           # the board never re-enumerates
        _, first = self.go(touch=touch, watcher=FakeWatcher(reachable=False))
        self.assertEqual(first.code, "bootloader_not_reached")
        _, second = self.go(touch=touch, watcher=FakeWatcher(reachable=False))
        self.assertEqual((second.cls, second.code), ("refused", "touch_pending"), second.to_json())
        self.assertEqual(len(touch.calls), 1, "a retry on the same enumeration is a second reset")

    def test_allowed_again_once_the_board_has_re_enumerated(self):
        self.attach_app()
        touch = Touch()
        self.go(touch=touch, watcher=FakeWatcher(reachable=False))
        self.t.unplug("1-3")
        self.t.app_board()                                        # a NEW enumeration: the earlier one resolved
        touch.on_touch = self.reenumerate_to_bootloader
        _, third = self.go(touch=touch)
        self.assertEqual(third.cls, "ok", third.to_json())
        self.assertEqual(len(touch.calls), 2)

    def test_a_failed_touch_blocks_the_next_one(self):
        self.attach_app()
        touch = Touch(raises=OSError("device busy"))
        _, first = self.go(touch=touch)
        self.assertEqual(first.code, "touch_failed")
        import json
        with open(self.ledger_path()) as fh:
            self.assertEqual(json.load(fh)[SERIAL]["outcome"], "touch_failed", "the ledger must say what happened")
        _, second = self.go(touch=touch)
        self.assertEqual((second.cls, second.code), ("refused", "touch_pending"))
        self.assertEqual(second.evidence["earlier"]["outcome"], "touch_failed")
        self.assertEqual(len(touch.calls), 1)

    def test_an_unreadable_enumeration_blocks_a_repeat(self):
        self.t.node("ttyACM0", 166, 0)
        self.t.app_board(devnum="")                               # the host exposes no devnum
        touch = Touch()
        self.go(touch=touch, watcher=FakeWatcher(reachable=False))
        _, second = self.go(touch=touch, watcher=FakeWatcher(reachable=False))
        self.assertEqual(second.code, "touch_pending")
        self.assertIn("cannot be read", second.message)
        self.assertEqual(len(touch.calls), 1)

    def test_a_corrupt_ledger_fails_closed(self):
        self.attach_app()
        with open(self.ledger_path(), "w") as fh:
            fh.write("{ not json")
        touch, r = self.go()
        self.assertEqual((r.cls, r.code), ("refused", "touch_pending"))
        self.assertEqual(touch.calls, [], "an untrustworthy ledger must block, never allow")

    def test_the_ledger_is_written_before_the_touch(self):
        self.attach_app()
        seen = []

        def at_touch():
            import json
            with open(self.ledger_path()) as fh:
                seen.append(json.load(fh)[SERIAL]["outcome"])
            self.reenumerate_to_bootloader()

        self.go(touch=Touch(on_touch=at_touch))
        self.assertEqual(seen, ["touching"], "write-ahead: the record must exist when the touch is made")

    def test_forgetting_a_touch_is_explicit_and_touches_nothing(self):
        self.attach_app()
        touch = Touch()
        self.go(touch=touch, watcher=FakeWatcher(reachable=False))
        self.assertEqual(self.go(touch=touch)[1].code, "touch_pending")
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = entry.main(["--profile", os.path.join(ROOT, "profiles", "rak4631.yaml"), "--serial", SERIAL,
                             "--bound-s", "1", "--lock", self.lock, "--forget-touch"])
        self.assertEqual(rc, 0)
        self.assertIn("nothing was touched", out.getvalue())
        self.assertEqual(len(touch.calls), 1, "forgetting must not touch")
        touch.on_touch = self.reenumerate_to_bootloader
        self.assertEqual(self.go(touch=touch)[1].cls, "ok", "after an explicit forget a touch is allowed again")


class OnlyEntryTouches(unittest.TestCase):
    def test_no_other_module_opens_a_port_at_the_touch_baud_or_imports_a_serial_library(self):
        # Fragments, so this file does not trip the host's port guard itself. Each pattern has the files allowed to
        # contain it, and the allowances are NAMED here, not hidden in a loose pattern: the touch is entry.py's; the
        # console's port module (docs/specs/console.md) is the one place a port is opened for reading and writing, and
        # its links module has to name 1200 to refuse it. Nothing else may.
        entry_py = os.path.join("devices", "entry.py")
        port_py = os.path.join("console", "port.py")
        links_py = os.path.join("console", "links.py")
        # port.py and links.py name 1200 only to REFUSE it (a link's baud is never 1200, and the port module has no
        # constant for it); entry.py is the touch itself.
        rules = [("serial" + r"\.Serial", (entry_py, port_py)), ("import " + "serial", (entry_py, port_py)),
                 ("term" + "ios", (entry_py, port_py)), ("TOUCH" + "_BAUD", (entry_py,)),
                 (r"\b1200\b", (entry_py, links_py, port_py))]
        base = os.path.join(ROOT, "nrf_workbench")
        offenders = []
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, base)
                with open(path) as fh:
                    text = fh.read()
                for pattern, allowed in rules:
                    if rel not in allowed and re.search(pattern, text):
                        offenders.append("%s: %s" % (rel, pattern))
        self.assertEqual(offenders, [], "a file outside the named allowances opens a port or names the touch baud")

    def test_the_authorised_file_carries_its_authorisation_record(self):
        with open(os.path.join(ROOT, "nrf_workbench", "devices", "entry.py")) as fh:
            head = fh.read(2500)
        self.assertIn("AUTHORISED BY PETER", head)
        self.assertIn("THIS FILE", head)

    def test_entry_takes_no_port_path_as_an_input(self):
        import inspect
        params = inspect.signature(entry.enter_bootloader).parameters
        self.assertFalse([p for p in params if "port" in p.lower() or p.lower() in ("path", "tty", "node")],
                         "the node must be found by identity, never passed in")


if __name__ == "__main__":
    unittest.main(verbosity=2)
