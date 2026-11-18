#!/usr/bin/env python3
"""Tests for board names (devices.name / devices.unname, nrf_workbench/devices/names.py): a label kept against a USB serial in a
small JSON file, shown by devices.list and the live feed's board list, unique, and never able to take the board list down.
Run:  python3 tests/devices/test_names.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.devices import commands, names, profile  # noqa: E402

PROFILE = profile.load(os.path.join(ROOT, "profiles", "rak4631.yaml"))
OTHER = "OTHERSERIAL0002"
CAPS = {"sysfs", "wiring"}


class Host(object):
    def __init__(self, tree, folder="auto"):
        self.sysfs, self.devroot, self.proc = tree.sysfs, tree.dev, tree.proc
        self.profiles, self.units, self.hooks = [PROFILE], {}, {}
        self.folder = os.path.join(tree.root, "wiring") if folder == "auto" else folder
        self.config = {"paths": {"wiring": self.folder}}
        self.services = {}

    def service(self, name, factory):
        if name not in self.services:
            self.services[name] = factory()
        return self.services[name]


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.t.app_board("1-3", "ttyACM0", "166:0", serial=SERIAL)
        self.t.app_board("1-4", "ttyACM1", "166:1", serial=OTHER)
        self.host = Host(self.t)
        self.reg = Registry()
        commands.register(self.reg, self.host)

    def call(self, name, args=None, caps=CAPS):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities=caps)

    def named(self):
        return dict((b["serial"], b["name"]) for b in self.call("devices.list").data["boards"])

    @property
    def file(self):
        return os.path.join(self.host.folder, names.FILE)


class Naming(Base):
    def test_a_board_is_named_by_its_serial_and_the_name_is_listed_with_it(self):
        res = self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.assertEqual((res.ok, res.data), (True, {"serial": SERIAL, "name": "left-rak"}))
        self.assertEqual(self.named(), {SERIAL: "left-rak", OTHER: None})

    def test_the_names_are_a_plain_json_file_a_person_can_read_and_edit(self):
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        with open(self.file) as fh:
            self.assertEqual(json.load(fh), {"names": {SERIAL: "left-rak"}})
        with open(self.file, "w") as fh:
            json.dump({"names": {OTHER: "bench-two"}}, fh)                   # edited by hand: read again on the next call
        self.assertEqual(self.named(), {SERIAL: None, OTHER: "bench-two"})

    def test_a_name_outlives_the_service_and_the_board_being_unplugged(self):
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.t.unplug("1-3")
        self.t.app_board("1-5", "ttyACM2", "166:2", serial=SERIAL)           # the same board in another port
        self.host = Host(self.t)                                             # a new process, nothing cached
        self.reg = Registry()
        commands.register(self.reg, self.host)
        self.assertEqual(self.named()[SERIAL], "left-rak")

    def test_naming_again_replaces_the_name_and_the_same_name_again_is_fine(self):
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.assertTrue(self.call("devices.name", {"serial": SERIAL, "name": "left-rak"}).ok)
        self.assertTrue(self.call("devices.name", {"serial": SERIAL, "name": "right-rak"}).ok)
        self.assertEqual(self.named()[SERIAL], "right-rak")

    def test_two_boards_cannot_share_a_name(self):
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        res = self.call("devices.name", {"serial": OTHER, "name": "left-rak"})
        self.assertEqual((res.cls, res.code), ("refused", "name_taken"))
        self.assertEqual(self.named(), {SERIAL: "left-rak", OTHER: None})

    def test_a_serial_that_is_not_attached_is_refused_so_a_typo_is_never_stored(self):
        res = self.call("devices.name", {"serial": "TYPO", "name": "x"})
        self.assertEqual((res.cls, res.code), ("refused", "node_absent"))
        self.assertFalse(os.path.exists(self.file))

    def test_a_name_that_breaks_the_rule_is_a_usage_error(self):
        for bad in ("Left Rak", "", "-x", "a" * 65, "über"):
            with self.assertRaises(UsageError, msg=repr(bad)):
                self.call("devices.name", {"serial": SERIAL, "name": bad})

    def test_removing_a_name_leaves_the_board_and_a_serial_with_no_name_is_refused(self):
        self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.assertTrue(self.call("devices.unname", {"serial": SERIAL}).ok)
        self.assertEqual(self.named(), {SERIAL: None, OTHER: None})
        self.assertEqual(self.call("devices.unname", {"serial": SERIAL}).code, "target_unknown")

    def test_a_name_file_that_cannot_be_read_never_takes_the_board_list_down_and_is_never_overwritten(self):
        os.makedirs(self.host.folder)
        with open(self.file, "w") as fh:
            fh.write("{ not json")
        self.assertEqual(self.named(), {SERIAL: None, OTHER: None})
        res = self.call("devices.name", {"serial": SERIAL, "name": "left-rak"})
        self.assertEqual((res.cls, res.code), ("failed", "write_failed"))
        with open(self.file) as fh:
            self.assertEqual(fh.read(), "{ not json")

    def test_with_no_folder_there_are_no_names_and_the_commands_are_absent(self):
        self.host = Host(self.t, folder=None)
        self.assertEqual(self.named(), {SERIAL: None, OTHER: None})
        self.assertEqual([c.name for c in self.reg.commands({"sysfs"})], ["devices.list"])
        self.assertEqual(sorted(c.name for c in self.reg.commands(CAPS)), ["devices.list", "devices.name", "devices.unname"])


if __name__ == "__main__":
    unittest.main()
