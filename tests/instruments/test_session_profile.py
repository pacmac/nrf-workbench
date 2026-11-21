#!/usr/bin/env python3
"""A session started with no `wake_*` uses the power profile in force (docs/specs/project-power-profile.md, decisions 4 and 5): the trigger as the default,
an explicit argument winning field by field, the profile's events written into a recording at its start, and the sidecar saying where the profile came from.
`meter.project_profile` is faked here with the answer the project area would give: the instruments never import the project area (architecture.md), they ask the registry.
Run:  python3 tests/instruments/test_session_profile.py
"""
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, HERE)

from nrf_workbench.core.registry import Command, Context, UsageError  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from test_capture import PathBase  # noqa: E402
from test_ppk2 import until  # noqa: E402


def in_force(trigger=None, events=None, beat_s=None, project="demo", role="alarm"):
    return {"project": project, "role": role, "kind": "nrf", "key": "SER1", "beat_s": beat_s, "trigger": trigger or {}, "events": events or []}


BOOT = {"name": "boot", "from": "wake", "start_s": 0.0, "end_s": 1.2, "every": 1, "offset": 0}
RADIO = {"name": "radio on", "from": "start", "start_s": 2.0, "end_s": 14.0, "every": 1, "offset": 0}


class FromTheProject(PathBase):
    def setUp(self):
        PathBase.setUp(self)
        self.answer = {"in_force": None, "candidates": [], "under_test": None}
        self.asked = []

        def profile(ctx):
            self.asked.append(ctx.caller)
            return Result.success(self.answer, "profile")
        self.reg.add(Command("meter.project_profile", "faked: what the project area answers", profile, requires=(), result="in_force, candidates, under_test"))
        self.host.registry = self.reg
        self.host.capabilities = {"ppk2", "meter_stream", "meter_store"}

    def force(self, **kw):
        self.answer = {"in_force": in_force(**kw), "candidates": [], "under_test": None}

    def start(self, command="meter.watch", **args):
        res = self.call(command, dict({"bound_s": 60, "source": "ppk2"}, **args))
        self.assertTrue(res.ok, getattr(res, "message", res))
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= self.BLOCKS * 20000, 30))
        self.sampler.stop()

    def sidecar(self, name):
        with open(os.path.join(self.folder, name + ".json")) as fh:
            return json.load(fh)


class TheTriggerIsTheDefault(FromTheProject):
    def test_with_nothing_in_force_the_session_is_exactly_as_before(self):
        self.start()
        self.assertEqual(self.sampler.session()["wake"], {"sleep_a": None, "awake_a": None, "hold_s": 0.2, "lead_s": 0.2, "max_s": 40.0})
        self.assertEqual(self.sampler.session()["profile"], {"project": None, "role": None, "source": "found", "overridden": []})

    def test_with_no_project_area_at_all_nothing_is_in_force_and_nothing_fails(self):
        del self.host.registry
        self.start()
        self.assertEqual(self.sampler.session()["profile"]["source"], "found")

    def test_a_watch_with_no_wake_arguments_takes_the_trigger_of_the_profile_in_force(self):
        self.force(trigger={"sleep_a": self.sleep_a, "hold_s": 0.6, "max_s": 30.0})
        self.start()
        s = self.sampler.session()
        self.assertEqual(s["wake"], {"sleep_a": self.sleep_a, "awake_a": None, "hold_s": 0.6, "lead_s": 0.2, "max_s": 30.0})
        self.assertEqual(self.sampler.sources["ppk2"].capturer.max_blocks, 150, "30 s, in the capture itself")
        self.assertEqual(s["profile"], {"project": "demo", "role": "alarm", "source": "project", "overridden": []})

    def test_an_explicit_argument_wins_for_its_field_and_the_profile_supplies_the_rest(self):
        self.force(trigger={"sleep_a": self.sleep_a, "hold_s": 0.6, "max_s": 30.0})
        self.start(wake_hold_s=0.4)
        s = self.sampler.session()
        self.assertEqual(s["wake"], {"sleep_a": self.sleep_a, "awake_a": None, "hold_s": 0.4, "lead_s": 0.2, "max_s": 30.0})
        self.assertEqual(s["profile"], {"project": "demo", "role": "alarm", "source": "project", "overridden": ["wake_hold_s"]})

    def test_explicit_arguments_with_no_profile_in_force_say_explicit(self):
        self.start(wake_hold_s=0.4)
        self.assertEqual(self.sampler.session()["profile"], {"project": None, "role": None, "source": "explicit", "overridden": ["wake_hold_s"]})

    def test_a_profile_of_events_alone_leaves_the_trigger_to_the_data(self):
        self.force(events=[RADIO])
        self.start()
        s = self.sampler.session()
        self.assertEqual((s["wake"]["sleep_a"], s["wake"]["max_s"], s["profile"]["source"]), (None, 40.0, "project"))

    def test_a_bad_explicit_value_is_still_a_usage_error_naming_its_field(self):
        self.force(trigger={"sleep_a": self.sleep_a})
        with self.assertRaises(UsageError) as cm:
            self.call("meter.watch", {"bound_s": 60, "source": "ppk2", "wake_awake_a": self.sleep_a / 2})
        self.assertEqual(cm.exception.arg, "wake_awake_a", "awake at or under the sleep level the profile gave")
        self.assertEqual(self.sampler.session()["state"], "idle")

    def test_a_multimeter_session_ignores_the_profile_and_is_not_refused(self):
        self.sampler.sources["meter"] = types.SimpleNamespace(name="meter")
        seen = []
        self.sampler.start = lambda mode, bound_s, name=None, source=None, wake=None, profile=None: seen.append((mode, source, wake, profile)) or {"bound_s": float(bound_s)}
        self.force(trigger={"sleep_a": self.sleep_a}, events=[BOOT, RADIO])
        res = self.call("meter.watch", {"bound_s": 60, "source": "meter"})
        self.assertTrue(res.ok, getattr(res, "message", res))
        self.assertEqual(seen, [("watch", "meter", None, None)])


class TheEventsAreWrittenAtTheStart(FromTheProject):
    def test_a_recording_starts_with_the_profiles_wake_events_as_stages_and_its_start_events_as_events(self):
        self.force(trigger={"sleep_a": self.sleep_a}, events=[BOOT, RADIO])
        self.start("meter.record", name="rak-1")
        stages = self.sampler.stage_list("rak-1")
        self.assertEqual([(s["name"], s["start_s"], s["end_s"], s["every"], s["offset"]) for s in stages], [("boot", 0.0, 1.2, 1, 0)])
        events = self.call("meter.events", {"recording": "rak-1"}).data["events"]
        self.assertEqual([(e["name"], e["start_s"], e["end_s"]) for e in events], [("radio on", 2.0, 14.0)], "the from-start event is an event, never a stage")

    def test_the_sidecar_says_where_the_profile_came_from(self):
        self.force(trigger={"sleep_a": self.sleep_a}, events=[BOOT], project="demo", role="alarm")
        self.start("meter.record", name="rak-1", wake_hold_s=0.4)
        self.assertEqual(self.sidecar("rak-1")["profile"], {"project": "demo", "role": "alarm", "source": "project", "overridden": ["wake_hold_s"]})
        self.assertEqual(self.sidecar("rak-1")["wake"]["hold_s"], 0.4)

    def test_the_sidecar_of_a_session_with_no_profile_says_found(self):
        self.start("meter.record", name="rak-2")
        self.assertEqual(self.sidecar("rak-2")["profile"], {"project": None, "role": None, "source": "found", "overridden": []})
        self.assertEqual(self.sampler.stage_list("rak-2"), [])

    def test_a_watch_applies_only_the_trigger_and_keeps_no_files(self):
        self.force(trigger={"sleep_a": self.sleep_a}, events=[BOOT, RADIO])
        self.start()
        self.assertEqual(os.listdir(self.folder), [])

    def test_stages_and_events_a_person_adds_later_join_the_profiles_and_a_same_named_one_replaces_it(self):
        self.force(trigger={"sleep_a": self.sleep_a}, events=[BOOT, RADIO])
        self.start("meter.record", name="rak-1")
        self.assertTrue(self.call("meter.stage_set", {"recording": "rak-1", "name": "radio join", "start_s": 4.0, "end_s": 12.0}).ok)
        self.assertTrue(self.call("meter.stage_set", {"recording": "rak-1", "name": "BOOT", "start_s": 0.0, "end_s": 1.0}).ok)
        self.assertEqual([(s["name"], s["end_s"]) for s in self.sampler.stage_list("rak-1")], [("BOOT", 1.0), ("radio join", 12.0)])


if __name__ == "__main__":
    unittest.main()
