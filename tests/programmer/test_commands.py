#!/usr/bin/env python3
"""Tests for the registered commands (programmer/commands.py, devices/commands.py), called through
Registry.call exactly as a front end will, with a fake host over the fake sysfs tree.

What matters here: the upload is stored BEFORE the board is touched, a refused flash keeps the upload,
a unit's declared way back cannot be deleted, and an unmet requirement makes a command absent.
Run:  python3 tests/programmer/test_commands.py
"""
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL, Tree  # noqa: E402
from nrf_workbench.core.registry import Context, DuplicateCommand, Registry, UsageError  # noqa: E402
from nrf_workbench.devices import commands as device_commands  # noqa: E402
from nrf_workbench.devices import profile  # noqa: E402
from nrf_workbench.guards.lock import HostLock  # noqa: E402
from nrf_workbench.programmer import commands  # noqa: E402
from test_firmware_store import pkg, sha  # noqa: E402
from test_flash import FakeWatcher, Runner  # noqa: E402
from test_flash_firmware import Touch  # noqa: E402

PROFILE = profile.load(os.path.join(ROOT, "profiles", "rak4631.yaml"))
ALL = {"sysfs", "firmware_store", "uploader"}


class Body(io.BytesIO):
    """What a front end hands a raw-body command: a read(n) and the declared length."""

    def __init__(self, data, length="auto"):
        io.BytesIO.__init__(self, data)
        self.length = len(data) if length == "auto" else length


class Host(object):
    def __init__(self, tree, hooks=None, units=None):
        self.config = {"paths": {"firmware": os.path.join(tree.root, "store"), "lock": None, "profiles": None,
                                 "units": None}}
        self.sysfs, self.devroot, self.proc = tree.sysfs, tree.dev, tree.proc
        self.lock_path = os.path.join(tree.root, "lock")
        self.profiles, self.units, self.hooks = [PROFILE], units or {}, hooks or {}
        self.services = {}

    def service(self, name, factory):
        if name not in self.services:
            self.services[name] = factory()
        return self.services[name]


class Base(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.t.node("ttyACM0", 166, 0)
        self.touch = Touch(on_touch=self.to_bootloader)
        self.runner = Runner(on_run=self.to_app)
        self.host = Host(self.t, hooks={"touch": self.touch, "runner": self.runner, "tool": "/fake/adafruit-nrfutil",
                                        "watcher": FakeWatcher()})
        self.reg = Registry()
        commands.register(self.reg, self.host)
        device_commands.register(self.reg, self.host)
        self.data = pkg()
        self.id = sha(self.data)

    def to_bootloader(self):
        self.t.unplug("1-3")
        self.t.bootloader_board()

    def to_app(self):
        self.t.unplug("1-3")
        self.t.app_board()

    def call(self, name, args=None, body=None, caller="tester", from_text=False, caps=ALL):
        return self.reg.call(name, args or {}, Context(self.host, caller, body), capabilities=caps, from_text=from_text)

    def put(self, data=None, **args):
        data = self.data if data is None else data
        return self.call("firmware.put", args, body=Body(data))

    def flash_args(self, **kw):
        a = {"serial": SERIAL, "firmware": self.id, "enter_bound_s": 5.0, "flash_bound_s": 60.0}
        a.update(kw)
        return a


class Registration(Base):
    def test_every_command_is_registered_with_its_effect_and_body(self):
        rows = dict((c.name, c) for c in self.reg.commands())
        self.assertEqual(sorted(rows), ["devices.list", "firmware.delete", "firmware.list", "firmware.move",
                                        "firmware.project_delete", "firmware.project_new", "firmware.put",
                                        "firmware.show", "programmer.flash", "programmer.flash_stream"])
        self.assertEqual([rows[n].body for n in ("firmware.put", "programmer.flash_stream")], ["raw", "raw"])
        self.assertEqual([rows[n].body for n in ("firmware.list", "programmer.flash", "devices.list")], [None] * 3)
        self.assertEqual([rows[n].effect for n in ("devices.list", "firmware.list", "firmware.show")], ["read"] * 3)
        self.assertEqual([rows[n].effect for n in ("firmware.put", "firmware.delete", "firmware.move",
                                                   "firmware.project_new", "firmware.project_delete",
                                                   "programmer.flash", "programmer.flash_stream")], ["write"] * 7)
        for n in ("firmware.put", "programmer.flash_stream"):          # the project is optional on an upload
            project = dict((a.name, a) for a in rows[n].args)["project"]
            self.assertFalse(project.required, n)
        for n in ("firmware.move", "firmware.project_new", "firmware.project_delete"):
            self.assertTrue(dict((a.name, a) for a in rows[n].args)["project"].required, n)

    def test_both_bounds_are_required_arguments_with_a_unit(self):
        for name in ("programmer.flash", "programmer.flash_stream"):
            args = dict((a.name, a) for a in self.reg.get(name).args)
            for bound in ("enter_bound_s", "flash_bound_s"):
                self.assertTrue(args[bound].required, (name, bound))
                self.assertEqual((args[bound].type, args[bound].unit), ("float", "s"))

    def test_an_unmet_requirement_makes_commands_absent(self):
        self.assertEqual([c.name for c in self.reg.commands({"sysfs"})], ["devices.list"])
        self.assertEqual([c.name for c in self.reg.commands({"firmware_store"})],
                         ["firmware.delete", "firmware.list", "firmware.move", "firmware.project_delete",
                          "firmware.project_new", "firmware.put", "firmware.show"])
        self.assertEqual(len(self.reg.commands(ALL)), 10)
        self.assertEqual(self.reg.commands(set()), [])

    def test_registering_twice_names_the_module(self):
        with self.assertRaises(DuplicateCommand) as caught:
            commands.register(self.reg, self.host)
        self.assertIn("nrf_workbench.programmer.commands", str(caught.exception))


class Store(Base):
    def test_put_list_show_delete_through_the_registry(self):
        r = self.put(name="parking")
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual((r.data["sha256"], r.data["new"], r.data["name"]), (self.id, True, "parking"))
        self.assertEqual(r.data["uploaded_by"], "tester")
        self.assertEqual([i["sha256"] for i in self.call("firmware.list").data["items"]], [self.id])
        self.assertEqual(self.call("firmware.show", {"id": self.id[:8]}).data["used_by"], [])
        self.assertTrue(self.call("firmware.delete", {"id": self.id}).ok)
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_the_store_is_built_once_and_shared(self):
        self.put()
        self.call("firmware.list")
        self.assertEqual(list(self.host.services), ["firmware_store"])

    def test_a_wrong_client_checksum_is_refused_and_nothing_is_stored(self):
        r = self.put(sha256="0" * 64)
        self.assertEqual((r.cls, r.code), ("refused", "checksum_mismatch"))
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_a_malformed_checksum_is_a_usage_error_before_anything_is_read(self):
        body = Body(self.data)
        with self.assertRaises(UsageError):
            self.call("firmware.put", {"sha256": "abc"}, body=body)
        self.assertEqual(body.tell(), 0)

    def test_an_upload_with_no_length_is_length_required(self):
        r = self.call("firmware.put", {}, body=Body(self.data, length=None))
        self.assertEqual((r.cls, r.code), ("refused", "length_required"))

    def test_a_raw_command_with_no_body_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.call("firmware.put", {}, body=None)

    def test_not_a_package_is_bad_package(self):
        r = self.put(b"not a zip")
        self.assertEqual((r.cls, r.code), ("refused", "bad_package"))

    def test_a_declared_return_package_cannot_be_deleted_and_is_shown(self):
        self.put()
        image = os.path.join(self.host.config["paths"]["firmware"], "unsorted", self.id + ".zip")   # the default project
        self.host.units = {"UNIT-A": {"return_path": {"method": "flash", "package": image}},
                           "UNIT-B": {"return_path": {"method": "flash", "package": "/some/other.zip"}},
                           "UNIT-C": {"protected": True}}
        self.assertEqual(self.call("firmware.show", {"id": self.id}).data["used_by"], ["UNIT-A"])
        r = self.call("firmware.delete", {"id": self.id})
        self.assertEqual((r.cls, r.code), ("refused", "in_use"))
        self.assertEqual(r.evidence["units"], ["UNIT-A"])
        self.host.units = {}
        self.assertTrue(self.call("firmware.delete", {"id": self.id}).ok)

    def test_an_unknown_id(self):
        self.assertEqual(self.call("firmware.show", {"id": "deadbeef"}).code, "firmware_unknown")
        self.assertEqual(self.call("firmware.delete", {"id": "deadbeef"}).code, "firmware_unknown")


class Projects(Base):
    """The project folders through the registry, the way the CLI, the HTTP API and the MCP call them."""

    def test_a_put_into_a_project_shows_in_the_list_with_its_project(self):
        r = self.put(project="mt-alarm", name="v3")
        self.assertEqual((r.cls, r.data["project"]), ("ok", "mt-alarm"))
        data = self.call("firmware.list").data
        self.assertEqual(data["projects"], ["mt-alarm"])
        self.assertEqual([(i["sha256"], i["project"]) for i in data["items"]], [(self.id, "mt-alarm")])
        self.assertEqual(self.call("firmware.show", {"id": self.id}).data["project"], "mt-alarm")

    def test_a_bad_project_is_a_refusal_with_the_status_a_client_expects(self):
        from nrf_workbench.core import status
        for name in ("../x", "A", "", "a/b"):
            r = self.put(project=name)
            self.assertEqual((r.cls, r.code), ("refused", "bad_project"), repr(name))
            self.assertEqual(status.http_status(r), 422)
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_new_move_and_delete_a_project(self):
        self.put(project="alpha")
        self.assertEqual(self.call("firmware.project_new", {"project": "beta"}).data, {"project": "beta", "new": True})
        r = self.call("firmware.move", {"id": self.id[:8], "project": "beta"})
        self.assertEqual((r.cls, r.data["from"], r.data["project"]), ("ok", "alpha", "beta"))
        self.assertEqual(self.call("firmware.list").data["projects"], ["alpha", "beta"])
        gone = self.call("firmware.project_delete", {"project": "alpha"})
        self.assertEqual(gone.cls, "ok", gone.to_dict())
        refused = self.call("firmware.project_delete", {"project": "beta"})
        self.assertEqual((refused.cls, refused.code), ("refused", "project_not_empty"))
        self.assertEqual(self.call("firmware.list").data["projects"], ["beta"])

    def test_the_unknown_project_codes_have_their_http_statuses(self):
        from nrf_workbench.core import status
        r = self.call("firmware.project_delete", {"project": "nothere"})
        self.assertEqual((r.code, status.http_status(r)), ("project_unknown", 404))
        r = self.call("firmware.project_delete", {"project": "x"})
        self.assertEqual((r.code, status.http_status(r)), ("project_unknown", 404))
        self.put(project="alpha")
        r = self.call("firmware.project_delete", {"project": "alpha"})
        self.assertEqual((r.code, status.http_status(r)), ("project_not_empty", 409))

    def test_a_unit_return_path_follows_the_project_folder_and_blocks_a_move(self):
        self.put(project="alpha")
        image = os.path.join(self.host.config["paths"]["firmware"], "alpha", self.id + ".zip")
        self.host.units = {"UNIT-A": {"return_path": {"method": "flash", "package": image}}}
        self.assertEqual(self.call("firmware.show", {"id": self.id}).data["used_by"], ["UNIT-A"])
        r = self.call("firmware.move", {"id": self.id, "project": "beta"})
        self.assertEqual((r.cls, r.code), ("refused", "in_use"))


class Flash(Base):
    def setUp(self):
        Base.setUp(self)
        self.t.app_board()
        self.put()

    def test_flash_by_id_enters_once_and_flashes_once(self):
        r = self.call("programmer.flash", self.flash_args())
        self.assertEqual(r.cls, "ok", r.to_dict())
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))
        self.assertEqual(r.data["firmware"]["id"], self.id)
        self.assertIs(r.data["entered_bootloader"], True)

    def test_the_bounds_arrive_as_text_from_a_query_string_and_are_parsed(self):
        a = {"serial": SERIAL, "firmware": self.id, "enter_bound_s": "5", "flash_bound_s": "60"}
        r = self.call("programmer.flash", a, from_text=True)
        self.assertEqual(r.cls, "ok", r.to_dict())

    def test_a_missing_bound_is_a_usage_error_and_nothing_happens(self):
        a = self.flash_args()
        del a["flash_bound_s"]
        with self.assertRaises(UsageError):
            self.call("programmer.flash", a)
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_the_expected_product_reaches_the_check(self):
        r = self.call("programmer.flash", self.flash_args(expect_product="nrfwb-parking 2"))
        self.assertEqual((r.cls, r.code), ("suspect", "flash_suspect"), r.to_dict())   # the fake returns the default

    def test_a_serial_no_board_has_is_node_absent_and_nothing_is_touched(self):
        r = self.call("programmer.flash", self.flash_args(serial="NOT-ATTACHED"))
        self.assertEqual((r.cls, r.code), ("refused", "node_absent"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_an_unknown_firmware_id(self):
        r = self.call("programmer.flash", self.flash_args(firmware="deadbeef"))
        self.assertEqual((r.cls, r.code), ("refused", "firmware_unknown"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_a_protected_unit_in_the_host_units_is_refused(self):
        self.host.units = {SERIAL: {"protected": True}}
        r = self.call("programmer.flash", self.flash_args())
        self.assertEqual((r.cls, r.code), ("refused", "protected"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))


class Stream(Base):
    def setUp(self):
        Base.setUp(self)
        self.t.app_board()

    def stream(self, data=None, **kw):
        a = {"serial": SERIAL, "enter_bound_s": 5.0, "flash_bound_s": 60.0}
        a.update(kw)
        return self.call("programmer.flash_stream", a, body=Body(self.data if data is None else data))

    def test_upload_then_flash_in_one_call_and_the_upload_stays_in_the_store(self):
        r = self.stream(name="parking")
        self.assertEqual(r.cls, "ok", r.to_dict())
        self.assertEqual(r.data["upload"], {"id": self.id, "new": True, "bytes": len(self.data),
                                            "project": "unsorted"})
        self.assertEqual(r.data["firmware"]["name"], "parking")
        self.assertEqual([i["sha256"] for i in self.call("firmware.list").data["items"]], [self.id])

    def test_the_upload_goes_into_the_named_project_and_the_flash_uses_it_from_there(self):
        r = self.stream(name="parking", project="testpartner")
        self.assertEqual(r.cls, "ok", r.to_dict())
        self.assertEqual(r.data["upload"]["project"], "testpartner")
        (item,) = self.call("firmware.list").data["items"]
        self.assertEqual(item["project"], "testpartner")
        cmd = self.runner.calls[-1][0]
        pkg_arg = cmd[cmd.index("-pkg") + 1]
        self.assertEqual(pkg_arg, os.path.join(self.host.config["paths"]["firmware"], "testpartner", self.id + ".zip"),
                         "the board was flashed from the stored file in its project folder")

    def test_a_bad_project_is_refused_before_the_board_is_touched(self):
        r = self.stream(project="../etc")
        self.assertEqual((r.cls, r.code), ("refused", "bad_project"))
        self.assertEqual(self.touch.calls, [], "the board was touched for an upload that was refused")
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_the_file_is_stored_complete_BEFORE_the_board_is_touched(self):
        seen = []

        def at_touch():
            seen.append([i["sha256"] for i in self.call("firmware.list").data["items"]])
            self.to_bootloader()

        self.host.hooks["touch"] = Touch(on_touch=at_touch)
        r = self.stream()
        self.assertEqual(r.cls, "ok", r.to_dict())
        self.assertEqual(seen, [[self.id]], "the whole file must be in the store before the touch")

    def test_the_same_bytes_again_are_not_new(self):
        self.stream()
        self.t.unplug("1-3")
        self.t.app_board()
        r = self.stream()
        self.assertEqual(r.cls, "ok", r.to_dict())
        self.assertIs(r.data["upload"]["new"], False)
        self.assertEqual(len(self.call("firmware.list").data["items"]), 1)

    def test_a_refused_flash_keeps_the_upload_so_the_retry_needs_no_second_upload(self):
        r = self.stream(serial="NOT-ATTACHED")
        self.assertEqual((r.cls, r.code), ("refused", "node_absent"))
        self.assertEqual(r.evidence["upload"]["id"], self.id)
        self.assertEqual([i["sha256"] for i in self.call("firmware.list").data["items"]], [self.id])
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_a_held_lock_refuses_after_the_upload_and_the_file_is_kept(self):
        other = HostLock(self.host.lock_path)
        other.acquire("someone else")
        try:
            r = self.stream()
        finally:
            other.release()
        self.assertEqual((r.cls, r.code), ("refused", "lock_held"))
        self.assertEqual([i["sha256"] for i in self.call("firmware.list").data["items"]], [self.id])
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_an_invalid_upload_is_refused_and_nothing_is_touched(self):
        r = self.stream(data=b"not a zip")
        self.assertEqual((r.cls, r.code), ("refused", "bad_package"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_a_wrong_checksum_is_refused_before_the_board_is_touched(self):
        r = self.stream(sha256="0" * 64)
        self.assertEqual((r.cls, r.code), ("refused", "checksum_mismatch"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_no_length_is_refused_before_the_board_is_touched(self):
        a = {"serial": SERIAL, "enter_bound_s": 5.0, "flash_bound_s": 60.0}
        r = self.call("programmer.flash_stream", a, body=Body(self.data, length=None))
        self.assertEqual((r.cls, r.code), ("refused", "length_required"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_a_flash_that_fails_after_the_entry_still_reports_the_upload(self):
        self.host.hooks["runner"] = Runner(rc=1, out="Failed.\n")
        r = self.stream()
        self.assertEqual((r.cls, r.code), ("failed", "flash_failed"))
        self.assertEqual(r.evidence["upload"]["id"], self.id)
        self.assertIs(r.evidence["entered_bootloader"], True)


class Profiles(Base):
    """More than one profile is loaded: the board's own must be the one that is used."""

    def other_profile(self):
        import copy
        other = copy.deepcopy(PROFILE)
        other["name"] = "other-board"
        other["modes"]["app"]["ids"] = ["1234:0001"]
        other["modes"]["bootloader"]["ids"] = ["1234:0002"]
        return other

    def test_the_profile_that_claims_the_board_is_used_not_the_first_one(self):
        self.t.app_board()
        self.put()
        self.host.profiles = [self.other_profile(), PROFILE]          # the wrong one comes first
        r = self.call("programmer.flash", self.flash_args())
        self.assertEqual(r.cls, "ok", r.to_dict())
        self.assertEqual(len(self.touch.calls), 1)

    def test_devices_list_names_each_boards_own_profile(self):
        self.t.app_board()
        self.host.profiles = [self.other_profile(), PROFILE]
        (b,) = self.call("devices.list").data["boards"]
        self.assertEqual(b["profile"], "rak4631")


class Devices(Base):
    def test_boards_are_listed_in_a_stable_order_by_serial(self):
        self.t.usb("1-3", ("239a", "8029"), "WisCore RAK4631 Board", serial="ZZZ", tty="ttyACM0", majmin="166:0")
        self.t.usb("1-4", ("239a", "8029"), "WisCore RAK4631 Board", serial="AAA", tty="ttyACM1", majmin="166:1")
        serials = [b["serial"] for b in self.call("devices.list").data["boards"]]
        self.assertEqual(serials, ["AAA", "ZZZ"])

    def test_state_is_the_boards_state_not_just_its_mode(self):
        # The same serial in BOTH modes at once: each listed device is in its own mode, but the board's
        # state is "two".
        self.t.app_board()
        self.t.bootloader_board(name="1-4", tty="ttyACM1", majmin="166:1")
        boards = self.call("devices.list").data["boards"]
        self.assertEqual(sorted(b["mode"] for b in boards), ["app", "bootloader"])
        self.assertEqual([b["state"] for b in boards], ["two", "two"])

    def test_lists_the_attached_board_by_identity(self):
        self.t.app_board()
        r = self.call("devices.list")
        self.assertEqual(r.cls, "ok", r.to_dict())
        (b,) = r.data["boards"]
        self.assertEqual((b["profile"], b["mode"], b["state"], b["id"], b["serial"]),
                         ("rak4631", "app", "app", "239a:8029", SERIAL))
        self.assertEqual(b["product"], "WisCore RAK4631 Board")

    def test_a_board_in_its_bootloader_is_listed_as_such(self):
        self.t.bootloader_board()
        (b,) = self.call("devices.list").data["boards"]
        self.assertEqual((b["mode"], b["state"], b["id"]), ("bootloader", "bootloader", "239a:0029"))

    def test_no_board_is_an_empty_list_not_an_error(self):
        r = self.call("devices.list")
        self.assertEqual((r.cls, r.data["boards"]), ("ok", []))

    def test_it_reads_sysfs_only(self):
        self.t.app_board()
        self.call("devices.list")
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
