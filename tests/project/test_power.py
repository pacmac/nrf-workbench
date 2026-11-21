#!/usr/bin/env python3
"""Tests for the project power profile (nrf_workbench/project/power.py; docs/specs/project-power-profile.md): which role's trigger and events are in
force on the profiler, decided from the projects that are applied and the boards that are attached; the board under test named when it is not obvious;
and what project.show, project.plan and project.check say about it. Through the real service composition over a fake sysfs, as test_project.py.
Run:  python3 tests/project/test_power.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_project import Base, CAM, SERIAL  # noqa: E402

TRIGGER = "trigger: {awake_ua: 50, sleep_ua: 25, max_s: 40}"
EVENTS = "events:\n      - {name: boot, from: wake, start_s: 0.0, end_s: 1.2}\n      - {name: radio on, from: start, start_s: 2.0, end_s: 14.0}"


def manifest(name="demo", roles=None, power=None):
    """A project of roles: nrf roles by serial, esp32 roles by a target the manifest declares. `power` is {role: yaml body of its profile}."""
    roles = roles if roles is not None else {"alarm": SERIAL}
    text = "name: %s\ndevices:\n" % name
    for role, key in sorted(roles.items()):
        text += "  %s: {%s: %s}\n" % (role, "esp32" if key == "timercam" else "nrf", key)
    if "timercam" in roles.values():
        text += 'esp32:\n  - {name: timercam, adapter: "%s", baud: 115200}\n' % CAM
    profiles = power if power is not None else {"alarm": "beat_s: 900\n%s\n%s" % (TRIGGER, EVENTS)}
    if profiles:
        text += "power:\n"
        for role, body in sorted(profiles.items()):
            text += "  %s:\n" % role + "".join("    %s\n" % line for line in body.splitlines())
    return text


class PowerBase(Base):
    def setUp(self):
        Base.setUp(self)
        self.meter_dir = os.path.join(self.tmp, "meter")
        self.host.config["paths"]["meter"] = self.meter_dir
        self.caps = set(self.caps) | {"meter_stream", "meter_store"}

    def profile(self):
        res = self.call("meter.project_profile")
        self.assertTrue(res.ok, res.message)
        return res.data

    def use(self, project, role):
        return self.call("meter.project_use", {"project": project, "role": role})

    def applied(self, text=None, project="demo"):
        self.put(text if text is not None else manifest(project), project)
        res = self.apply(project)
        self.assertTrue(res.ok, res.message)

    def under_test_file(self):
        return os.path.join(self.meter_dir, "under-test.json")


class InForce(PowerBase):
    def test_nothing_is_in_force_with_no_project_applied_and_a_project_only_put_is_not_a_candidate(self):
        self.assertEqual(self.profile(), {"in_force": None, "candidates": [], "under_test": None})
        self.put(manifest())
        self.assertEqual(self.profile(), {"in_force": None, "candidates": [], "under_test": None})

    def test_an_applied_project_with_its_board_attached_puts_the_roles_profile_in_force(self):
        self.applied()
        d = self.profile()
        f = d["in_force"]
        self.assertEqual((f["project"], f["role"], f["kind"], f["beat_s"]), ("demo", "alarm", "nrf", 900))
        self.assertAlmostEqual(f["trigger"]["awake_a"], 50e-6, places=12)
        self.assertAlmostEqual(f["trigger"]["sleep_a"], 25e-6, places=12)
        self.assertEqual(f["trigger"]["max_s"], 40)
        self.assertEqual([(e["name"], e["from"]) for e in f["events"]], [("boot", "wake"), ("radio on", "start")])
        self.assertEqual([(c["project"], c["role"], c["attached"]) for c in d["candidates"]], [("demo", "alarm", True)])
        self.assertIsNone(d["under_test"])

    def test_a_board_that_is_not_attached_gives_no_profile_and_the_candidate_says_why(self):
        self.applied(manifest(roles={"alarm": "NOTPLUGGED"}))
        d = self.profile()
        self.assertIsNone(d["in_force"])
        self.assertEqual([(c["role"], c["attached"], c["why"]) for c in d["candidates"]], [("alarm", False, "not attached")])

    def test_unplugging_the_board_takes_the_profile_out_of_force_and_plugging_it_back_restores_it(self):
        self.applied(manifest(roles={"camera": "timercam"}, power={"camera": "beat_s: 60"}))
        self.assertEqual(self.profile()["in_force"]["role"], "camera")
        self.t.unplug("1-2")
        d = self.profile()
        self.assertEqual((d["in_force"], d["candidates"][0]["attached"]), (None, False))

    def test_a_role_with_no_profile_is_not_a_candidate_and_a_project_with_no_power_section_adds_none(self):
        self.applied(manifest(roles={"alarm": SERIAL}, power={}))
        self.assertEqual(self.profile(), {"in_force": None, "candidates": [], "under_test": None})

    def test_a_device_that_never_sleeps_has_a_profile_of_events_alone(self):
        self.applied(manifest(power={"alarm": "events:\n  - {name: radio on, from: start, start_s: 2, end_s: 14}"}))
        f = self.profile()["in_force"]
        self.assertEqual((f["trigger"], f["beat_s"], [e["name"] for e in f["events"]]), ({}, None, ["radio on"]))

    def test_a_release_takes_it_out_of_force(self):
        self.applied()
        self.assertTrue(self.call("project.release", {"project": "demo"}).ok)
        self.assertEqual(self.profile()["in_force"], None)


class UnderTest(PowerBase):
    def two_boards(self):
        self.applied(manifest(roles={"alarm": SERIAL, "camera": "timercam"}, power={"alarm": TRIGGER, "camera": "beat_s: 60"}))

    def test_two_attached_boards_with_profiles_put_none_in_force_until_one_is_named(self):
        self.two_boards()
        d = self.profile()
        self.assertIsNone(d["in_force"])
        self.assertEqual(sorted((c["role"], c["attached"]) for c in d["candidates"]), [("alarm", True), ("camera", True)])
        res = self.use("demo", "camera")
        self.assertTrue(res.ok, res.message)
        d = self.profile()
        self.assertEqual((d["in_force"]["role"], d["under_test"]), ("camera", {"project": "demo", "role": "camera"}))

    def test_naming_the_other_board_changes_it_and_an_empty_role_clears_it(self):
        self.two_boards()
        self.use("demo", "camera")
        self.use("demo", "alarm")
        self.assertEqual(self.profile()["in_force"]["role"], "alarm")
        self.assertTrue(self.use("demo", "").ok)
        d = self.profile()
        self.assertEqual((d["in_force"], d["under_test"]), (None, None))
        self.assertFalse(os.path.exists(self.under_test_file()))

    def test_the_choice_is_ignored_while_its_board_is_unplugged_and_the_one_left_is_in_force_on_its_own(self):
        self.two_boards()
        self.use("demo", "camera")
        self.t.unplug("1-2")
        d = self.profile()
        self.assertEqual((d["in_force"]["role"], d["under_test"]), ("alarm", None), "never carried: the one attached board is in force by the one-candidate rule")

    def test_a_release_removes_the_choice_so_a_later_apply_does_not_revive_it(self):
        self.two_boards()
        self.use("demo", "camera")
        self.assertTrue(os.path.exists(self.under_test_file()))
        self.assertTrue(self.call("project.release", {"project": "demo"}).ok)
        self.assertFalse(os.path.exists(self.under_test_file()))
        self.applied()
        self.assertIsNone(self.profile()["under_test"])

    def test_the_choice_records_the_board_so_a_different_board_in_that_role_does_not_inherit_it(self):
        self.two_boards()
        self.use("demo", "camera")
        with open(self.under_test_file()) as fh:
            self.assertEqual(json.load(fh), {"project": "demo", "role": "camera", "key": "timercam"})
        with open(self.under_test_file(), "w") as fh:
            json.dump({"project": "demo", "role": "camera", "key": "some-other-target"}, fh)
        d = self.profile()
        self.assertEqual((d["in_force"], d["under_test"]), (None, None))

    def test_two_applied_projects_with_one_attached_board_each_need_one_named(self):
        self.applied(manifest("one", {"alarm": SERIAL}, {"alarm": TRIGGER}), "one")
        self.t.app_board("1-4", "ttyACM3", "166:3", "SECOND")          # a second nRF board: an attached nrf role is a board a profile recognises
        self.t.node("ttyACM3", 166, 3)
        self.applied(manifest("two", {"alarm": "SECOND"}, {"alarm": "beat_s: 30"}), "two")
        self.assertIsNone(self.profile()["in_force"])
        self.assertTrue(self.use("two", "alarm").ok)
        self.assertEqual(self.profile()["in_force"]["project"], "two")

    def test_naming_what_is_not_a_candidate_is_refused_with_the_reason(self):
        self.applied(manifest(roles={"alarm": "NOTPLUGGED", "camera": "timercam"}, power={"alarm": TRIGGER}))
        for project, role, fragment in (("demo", "alarm", "not attached"), ("demo", "camera", "no profile"), ("demo", "nobody", "no role"),
                                        ("ghost", "alarm", "not applied")):
            res = self.use(project, role)
            self.assertEqual((res.cls, res.code), ("refused", "profile_not_candidate"), (project, role))
            self.assertIn(fragment, res.message)
        self.assertFalse(os.path.exists(self.under_test_file()), "a refusal writes nothing")
        self.put(manifest("loose"), "loose")
        self.assertIn("not applied", self.use("loose", "alarm").message)


class Surfaces(PowerBase):
    def test_show_carries_the_section_and_says_which_role_is_in_force(self):
        self.applied()
        d = self.call("project.show", {"project": "demo"}).data
        self.assertEqual(d["power"]["alarm"]["beat_s"], 900)
        self.assertEqual([(r["role"], r["power"]) for r in d["roles"]], [("alarm", {"profile": True, "in_force": True})])

    def test_show_for_a_role_without_a_profile_says_so(self):
        self.applied(manifest(roles={"alarm": SERIAL, "camera": "timercam"}, power={"alarm": TRIGGER}))
        d = self.call("project.show", {"project": "demo"}).data
        self.assertEqual(dict((r["role"], r["power"]) for r in d["roles"]),
                         {"alarm": {"profile": True, "in_force": True}, "camera": {"profile": False, "in_force": False}})

    def test_plan_has_one_non_blocking_item_per_profiled_role_have_when_attached_unresolved_when_not(self):
        self.put(manifest(roles={"alarm": SERIAL, "spare": "NOTPLUGGED"}, power={"alarm": TRIGGER, "spare": "beat_s: 60"}))
        plan = self.call("project.plan", {"project": "demo"}).data
        items = dict((i["name"], i) for i in plan["items"] if i["section"] == "power")
        self.assertEqual((items["alarm"]["action"], items["spare"]["action"]), ("have", "unresolved"))
        self.assertFalse(items["spare"]["blocking"] or items["alarm"]["blocking"])
        self.assertTrue(plan["ok"], "a board that is not plugged in never stops an apply")

    def test_check_does_not_count_an_unplugged_boards_profile_as_a_second_problem(self):
        self.applied(manifest(roles={"alarm": "NOTPLUGGED"}, power={"alarm": TRIGGER}))
        d = self.call("project.check", {"project": "demo"}).data
        by = dict(((f["section"], f["name"]), f) for f in d["findings"])
        self.assertEqual(by[("roles", "alarm")]["status"], "absent")
        self.assertEqual(by[("power", "alarm")]["status"], "not_applied")
        self.assertEqual(d["problems"], 1, "the role's absence is the one problem")

    def test_check_says_ok_for_a_profile_whose_board_is_attached(self):
        self.applied()
        d = self.call("project.check", {"project": "demo"}).data
        by = dict(((f["section"], f["name"]), f) for f in d["findings"])
        self.assertEqual((by[("power", "alarm")]["status"], d["problems"], d["ready"]), ("ok", 0, True))

    def test_applying_makes_nothing_for_the_profile_and_releasing_removes_nothing_for_it(self):
        self.applied()
        self.assertEqual(self.store.state("demo")["owns"], {"targets": [], "connections": [], "pins": [], "macros": []})
        self.assertEqual(self.call("project.release", {"project": "demo"}).data["removed"], [])


if __name__ == "__main__":
    unittest.main()
