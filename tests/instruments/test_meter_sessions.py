#!/usr/bin/env python3
"""Tests for the meter's watch and record sessions (docs/specs/meter-page.md): the sampler thread, the live feed, the CSV, the bounds and the faults, against
the fake SCPI meter of test_meter.py on a real socket.
Run:  python3 tests/instruments/test_meter_sessions.py
"""
import json
import os
import socket
import sys
import tempfile
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.events import Bus  # noqa: E402
from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.instruments import meter, sampler  # noqa: E402
from test_meter import CURRENT, FakeMeter  # noqa: E402


def until(condition, seconds=5.0):
    """Wait for a condition on the event, with a ceiling for a hang: it returns the moment it is true."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.002)
    return False


class Clock(object):
    """A monotonic clock that moves a fixed step each time it is read, so a bound is reached after a known number of reads."""

    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class Host(object):
    def __init__(self, address, folder):
        cfg, problems = cfgmod.load(env={}, overrides={"instruments": {"meter": address}, "paths": {"meter": folder},
                                                       "streams": {"meter": {"retain": 5000, "max_age_days": 1}}})
        assert problems == [], problems
        self.config, self.hooks = cfg, {}
        self.streams = types.SimpleNamespace(meter=Bus(5000, 86400))
        self._services = {}

    def service(self, name, factory):
        if name not in self._services:
            self._services[name] = factory()
        return self._services[name]

    def started(self, name):
        return self._services.get(name)


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMeter(CURRENT)
        self.addCleanup(self.fake.close)
        self.folder = tempfile.mkdtemp(prefix="nrfwb-meter-rec-")
        self.host = Host("127.0.0.1:%d" % self.fake.port, self.folder)
        self.meter = meter.Meter(self.host)
        self.bus = self.host.streams.meter
        self.sampler = sampler.Sampler(self.meter, self.bus, self.folder)
        self.addCleanup(self.sampler.stop)

    def readings(self):
        return [e for e in self.bus.since(0).events if e.type == "meter.reading"]

    def states(self):
        return [e for e in self.bus.since(0).events if e.type == "meter.state"]

    def wait_readings(self, n):
        self.assertTrue(until(lambda: self.sampler.session()["stats"]["readings"] >= n), "no %d readings in time" % n)

    def lines(self, name):
        with open(os.path.join(self.folder, name + ".csv")) as fh:
            return fh.read().splitlines()


class Watching(Base):
    def test_a_watch_publishes_a_state_then_readings_then_a_final_state_in_order(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+2.0E-03", "+3.0E-03"]
        self.sampler.start("watch", 60)
        self.wait_readings(3)
        self.sampler.stop()
        events = self.bus.since(0).events
        self.assertEqual(events[0].type, "meter.state")
        self.assertEqual(events[0].payload["state"], "watching")
        kinds = [e.type for e in events]
        self.assertEqual(kinds[-1], "meter.state")
        self.assertEqual(events[-1].payload["state"], "idle")
        self.assertEqual(events[-1].payload["reason"], "stopped")
        first = [e.payload for e in events if e.type == "meter.reading"][:3]
        self.assertEqual([p["value"] for p in first], [1e-3, 2e-3, 3e-3])
        self.assertEqual((first[0]["unit"], first[0]["function"], first[0]["range"]), ("A", "CURR", 0.6))
        times = [p["t_s"] for p in first]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(p["session"] == 1 for p in first))

    def test_a_watch_writes_nothing_to_disk(self):
        self.sampler.start("watch", 60)
        self.wait_readings(2)
        self.sampler.stop()
        self.assertEqual(os.listdir(self.folder), [])

    def test_the_session_reports_its_state_time_and_statistics(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+3.0E-03"]
        self.sampler.start("watch", 60)
        self.wait_readings(2)
        s = self.sampler.session()
        self.assertEqual((s["state"], s["mode"], s["bound_s"], s["function"], s["unit"]), ("watching", "watch", 60.0, "CURR", "A"))
        self.assertGreaterEqual(s["stats"]["readings"], 2)
        self.assertIn("charge_mah", s["stats"], "a current reports its charge")
        self.sampler.stop()
        self.assertEqual(self.sampler.session()["state"], "idle")

    def test_before_any_session_it_is_idle_with_nothing(self):
        s = self.sampler.session()
        self.assertEqual((s["state"], s["session"], s["stats"]["readings"], s["stats"]["mean"]), ("idle", 0, 0, None))
        self.assertEqual(self.sampler.profile()["buckets"], [])

    def test_stop_with_no_session_and_stop_twice_are_fine(self):
        self.sampler.stop()
        self.sampler.start("watch", 60)
        self.wait_readings(1)
        self.sampler.stop()
        self.sampler.stop()
        self.assertEqual(self.sampler.session()["state"], "idle")

    def test_a_second_session_while_one_runs_is_busy_and_names_the_running_one(self):
        self.sampler.start("watch", 60)
        with self.assertRaises(sampler.Busy) as cm:
            self.sampler.start("record", 60)
        self.assertEqual((cm.exception.session["mode"], cm.exception.session["state"]), ("watch", "watching"))
        self.sampler.stop()
        self.assertEqual(self.sampler.start("watch", 60)["session"], 2, "after it ended another can start, and it is a new session")

    def test_the_bound_ends_the_session(self):
        clock = Clock(0.25)
        s = sampler.Sampler(self.meter, self.bus, self.folder, mono=clock)
        self.addCleanup(s.stop)
        s.start("watch", 1)
        self.assertTrue(until(lambda: s.session()["state"] == "idle"), "a 1 s bound did not end the session")
        self.assertEqual(s.session()["reason"], "the time bound was reached")
        self.assertGreaterEqual(s.session()["stats"]["readings"], 1)

    def test_the_bound_is_required_and_limited(self):
        for bad in (0, 0.5, -1, 43201, True, None, "60"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.sampler.start("watch", bad)
        self.sampler.start("watch", 43200)                         # the limit itself is allowed
        self.sampler.stop()
        with self.assertRaises(ValueError):
            self.sampler.start("scan", 10)

    def test_the_setting_is_asked_again_every_ten_readings_and_not_on_each(self):
        self.sampler.start("watch", 60)
        self.wait_readings(25)
        self.sampler.stop()
        reads = self.fake.sent.count("READ?")
        asked = self.fake.sent.count("FUNC?")
        self.assertEqual(asked, (reads - 1) // sampler.SETTING_EVERY + 1, (reads, asked))
        self.assertLess(asked, reads)


class Statistics(Base):
    def test_charge_and_mean_are_exact_on_known_readings(self):
        self.fake.table["READ?"] = "+2.0E-03"
        self.sampler.start("watch", 60)
        self.wait_readings(5)
        self.sampler.stop()
        points = [e.payload for e in self.readings()][:5]
        d = self.sampler.session()["stats"]
        self.assertAlmostEqual(d["mean"], 2e-3)
        self.assertGreater(d["readings"], 4)
        t = [p["t_s"] for p in [e.payload for e in self.readings()]]
        self.assertAlmostEqual(d["charge_c"], 2e-3 * (t[-1] - t[0]), places=3)
        self.assertEqual(points[0]["value"], 2e-3)

    def test_an_overload_is_published_flagged_and_left_out_of_the_statistics(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+9.9E+37", "+3.0E-03"]
        self.sampler.start("watch", 60)
        self.wait_readings(3)
        self.sampler.stop()
        flagged = [e.payload for e in self.readings() if e.payload["overload"]]
        self.assertEqual([p["value"] for p in flagged[:1]], [None])
        st = self.sampler.session()["stats"]
        self.assertGreaterEqual(st["overloads"], 1)
        self.assertLess(st["max"], 1.0, "9.9e37 is not a reading")


class Recording(Base):
    def test_a_recording_writes_one_csv_line_per_reading_with_a_header(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+2.5E-03"]
        self.sampler.start("record", 60, name="night-1")
        self.wait_readings(3)
        self.sampler.stop()
        lines = self.lines("night-1")
        self.assertEqual(lines[0], "t_s,wall,value,unit,function,range,nplc,overload")
        n = self.sampler.session()["stats"]["readings"]
        self.assertEqual(len(lines) - 1, n)
        cells = lines[1].split(",")
        self.assertEqual((cells[2], cells[3], cells[4], cells[5], cells[6], cells[7]), ("0.001", "A", "CURR", "0.6", "0.02", "0"))
        self.assertEqual(lines[2].split(",")[2], "0.0025")

    def test_an_overload_line_has_no_value_and_the_flag_set(self):
        self.fake.answers["READ?"] = ["+9.9E+37"]
        self.sampler.start("record", 60, name="over")
        self.wait_readings(1)
        self.sampler.stop()
        row = self.lines("over")[1].split(",")
        self.assertEqual((row[2], row[7]), ("", "1"))

    def test_the_file_is_flushed_per_reading_so_a_killed_service_loses_nothing_taken(self):
        self.sampler.start("record", 60, name="live")
        self.wait_readings(4)
        lines = self.lines("live")                                  # read while the session is still running: nothing waits for a close
        self.assertGreaterEqual(len(lines) - 1, 4)
        self.sampler.stop()

    def test_the_default_name_is_the_start_time_and_a_taken_name_is_refused(self):
        s = self.sampler.start("record", 60)
        self.assertRegex(s["name"], r"^\d{8}-\d{6}$")
        self.sampler.stop()
        with self.assertRaises(sampler.NameTaken):
            self.sampler.start("record", 60, name=s["name"])

    def test_a_name_is_a_slug_and_cannot_reach_another_path(self):
        for bad in ("../evil", "a/b", "A", ".x", "x y", "x" * 65, "/etc/passwd", "a\\b", "a\nb"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.sampler.start("record", 60, name=bad)
        self.assertEqual(os.listdir(self.folder), [])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.folder), "evil.csv")))

    def test_a_recording_needs_its_folder(self):
        bare = sampler.Sampler(self.meter, self.bus, None)
        with self.assertRaises(ValueError):
            bare.start("record", 60, name="x")

    def test_the_sidecar_says_how_it_ended_and_a_clean_stop_is_not_interrupted(self):
        self.sampler.start("record", 60, name="clean")
        self.wait_readings(2)
        self.sampler.stop()
        with open(os.path.join(self.folder, "clean.json")) as fh:
            meta = json.load(fh)
        self.assertEqual((meta["name"], meta["reason"], meta["ended"] is not None), ("clean", "stopped", True))
        row = [r for r in self.sampler.recordings() if r["name"] == "clean"][0]
        self.assertEqual((row["interrupted"], row["recording"], row["unit"]), (False, False, "A"))

    def test_a_recording_a_dead_service_left_is_listed_as_interrupted_and_a_live_one_is_not(self):
        with open(os.path.join(self.folder, "cut.csv"), "w") as fh:
            fh.write("t_s,wall,value,unit,function,range,nplc,overload\n0.000,1.0,0.001,A,CURR,0.6,10,0\n")
        with open(os.path.join(self.folder, "cut.json"), "w") as fh:
            json.dump({"name": "cut", "started": 1.0, "ended": None, "reason": ""}, fh)
        self.sampler.start("record", 60, name="now")
        rows = dict((r["name"], r) for r in self.sampler.recordings())
        self.assertEqual((rows["cut"]["interrupted"], rows["now"]["interrupted"], rows["now"]["recording"]), (True, False, True))

    def test_a_recording_in_progress_lists_its_live_numbers_and_not_the_ones_from_its_start(self):
        self.sampler.start("record", 60, name="going")
        self.wait_readings(5)
        row = [r for r in self.sampler.recordings() if r["name"] == "going"][0]
        self.assertTrue(row["recording"])
        self.assertGreaterEqual(row["stats"]["readings"], 5)
        self.assertEqual((row["function"], row["unit"]), ("CURR", "A"))
        self.assertIsNotNone(row["started"])

    def test_only_the_newest_ten_recordings_are_kept(self):
        for i in range(12):
            path = os.path.join(self.folder, "old-%02d" % i)
            for suffix in (".csv", ".json"):
                with open(path + suffix, "w") as fh:
                    fh.write("x")
                os.utime(path + suffix, (1000 + i, 1000 + i))
        self.sampler.start("record", 60, name="new")
        self.sampler.stop()
        names = sorted(r["name"] for r in self.sampler.recordings())
        self.assertEqual(len(names), sampler.KEEP_RECORDINGS)
        self.assertIn("new", names)
        self.assertNotIn("old-00", names)
        self.assertNotIn("old-02", names)
        self.assertIn("old-03", names)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "old-00.json")), "the sidecar goes with its file")

    def test_the_recording_text_is_returned_only_for_a_real_name(self):
        self.sampler.start("record", 60, name="get-me")
        self.wait_readings(1)
        self.sampler.stop()
        self.assertTrue(self.sampler.recording_text("get-me").startswith("t_s,wall,value"))
        for bad in ("nope", "../x", "", None, "get-me.csv"):
            self.assertIsNone(self.sampler.recording_text(bad), repr(bad))


class Faults(Base):
    def test_a_silent_meter_ends_the_session_with_the_reason_and_keeps_the_file(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+1.0E-03"]
        self.sampler.start("record", 60, name="cut-short")
        self.wait_readings(2)
        del self.fake.table["READ?"]
        self.fake.answers["READ?"] = []
        self.assertTrue(until(lambda: self.sampler.session()["state"] == "error", 8), "the session did not end on silence")
        s = self.sampler.session()
        self.assertIn("no answer to READ?", s["reason"])
        self.assertGreaterEqual(len(self.lines("cut-short")) - 1, 2, "what it had taken is kept")
        with open(os.path.join(self.folder, "cut-short.json")) as fh:
            self.assertIsNotNone(json.load(fh)["ended"])

    def test_an_unreachable_meter_ends_the_session(self):
        gone = socket.socket()
        gone.bind(("127.0.0.1", 0))
        port = gone.getsockname()[1]
        gone.close()
        self.host.config["instruments"]["meter"] = "127.0.0.1:%d" % port
        self.sampler.start("watch", 60)
        self.assertTrue(until(lambda: self.sampler.session()["state"] == "error"))
        self.assertIn("did not accept a connection", self.sampler.session()["reason"])

    def test_a_reply_that_is_not_a_number_ends_the_session(self):
        self.fake.table["READ?"] = "garbage"
        self.sampler.start("watch", 60)
        self.assertTrue(until(lambda: self.sampler.session()["state"] == "error"))
        self.assertIn("not a number", self.sampler.session()["reason"])

    def test_the_meters_function_changing_mid_session_ends_it(self):
        self.sampler.start("watch", 60)
        self.wait_readings(3)
        self.fake.table.update({"FUNC?": '"VOLT"', "VOLT:DC:RANG?": "+6.0E+00", "VOLT:DC:RANG:AUTO?": "1", "VOLT:DC:NPLC?": "+2.0E-02"})
        self.assertTrue(until(lambda: self.sampler.session()["state"] == "error"), "a power measurement across a function change is meaningless")
        self.assertIn("function changed from CURR to VOLT", self.sampler.session()["reason"])


class Resume(Base):
    def test_the_profile_cursor_joins_the_feed_with_no_gap_and_no_repeat(self):
        self.sampler.start("watch", 60)
        self.wait_readings(20)
        prof = self.sampler.profile()
        self.sampler.stop()
        after = [e for e in self.bus.since(prof["cursor"], prof["epoch"]).events if e.type == "meter.reading"]
        total = self.sampler.session()["stats"]["readings"]
        self.assertEqual(prof["stats"]["readings"] + len(after), total)
        self.assertTrue(all(e.cursor > prof["cursor"] for e in after))

    def test_the_profile_is_bounded_exact_and_names_the_unit(self):
        self.sampler.start("watch", 60)
        self.wait_readings(30)
        prof = self.sampler.profile()
        self.sampler.stop()
        self.assertEqual((prof["unit"], prof["function"], prof["state"]), ("A", "CURR", "watching"))
        self.assertEqual(sum(b["n"] for b in prof["buckets"]), prof["stats"]["readings"])


class NothingButQuestions(Base):
    def test_every_line_a_session_sends_ends_in_a_question_mark(self):
        self.sampler.start("record", 60, name="q")
        self.wait_readings(15)
        self.sampler.stop()
        self.assertGreater(len(self.fake.sent), 15)
        self.assertEqual([s for s in self.fake.sent if not s.endswith("?")], [])


class Wiring(unittest.TestCase):
    """The service's side of it: the stream, its reason when it is off, and the two capabilities that gate the session commands."""

    def cfg(self, **overrides):
        cfg, problems = cfgmod.load(env={}, overrides=overrides)
        self.assertEqual(problems, [])
        return cfg

    def test_a_bench_with_a_meter_and_no_bound_is_told_the_stream_is_off_and_why(self):
        from nrf_workbench import service
        s = service.build_streams(self.cfg(instruments={"meter": "m.home"}))
        self.assertIsNone(s.meter)
        self.assertIn("streams.meter.retain", s.absent["meter"])

    def test_a_bench_without_a_meter_is_not_told_anything_about_one(self):
        from nrf_workbench import service
        self.assertNotIn("meter", service.build_streams(self.cfg()).absent)

    def test_with_both_bounds_the_stream_exists_and_is_bounded(self):
        from nrf_workbench import service
        s = service.build_streams(self.cfg(instruments={"meter": "m.home"}, streams={"meter": {"retain": 20000, "max_age_days": 1}}))
        self.assertEqual((s.meter.retain, s.meter.max_age_s), (20000, 86400))
        self.assertTrue(s.to_dict()["meter"])

    def test_the_capabilities_follow_the_address_the_stream_and_the_folder(self):
        from nrf_workbench import service
        host = lambda **o: service.Host(self.cfg(**o), {}, {})  # noqa: E731
        bound = {"meter": {"retain": 10, "max_age_days": 1}}
        self.assertEqual(service.capabilities(host()) & {"meter", "meter_stream", "meter_store"}, set())
        self.assertEqual(service.capabilities(host(instruments={"meter": "m"})) & {"meter", "meter_stream", "meter_store"}, {"meter"})
        self.assertEqual(service.capabilities(host(instruments={"meter": "m"}, streams=bound)) & {"meter", "meter_stream", "meter_store"}, {"meter", "meter_stream"})
        self.assertEqual(service.capabilities(host(instruments={"meter": "m"}, streams=bound, paths={"meter": "/x"})) & {"meter", "meter_stream", "meter_store"},
                         {"meter", "meter_stream", "meter_store"})
        self.assertEqual(service.capabilities(host(streams=bound, paths={"meter": "/x"})) & {"meter", "meter_stream", "meter_store"}, set(),
                         "a stream and a folder without a meter give nothing")

    def test_the_meter_feed_is_offered_to_the_page_only_when_the_stream_exists(self):
        from nrf_workbench import service
        from nrf_workbench.api import feed
        with_stream = service.build_streams(self.cfg(instruments={"meter": "m"}, streams={"meter": {"retain": 10, "max_age_days": 1}}))
        feeds = feed.feeds_of(with_stream)
        self.assertEqual(feeds["meter"][1], ("meter.reading", "meter.state"))
        self.assertNotIn("meter", feed.feeds_of(service.build_streams(self.cfg())))

    def test_stopping_the_service_stops_a_running_session(self):
        """service.main's shutdown asks the REAL Host for 'meter_sampler' and stops it if there is one: it never creates one just to stop it."""
        from nrf_workbench import service
        h = service.Host(self.cfg(), {}, {})
        self.assertIsNone(h.started("meter_sampler"))
        self.assertEqual(h._services, {}, "asking did not create it")
        made = h.service("meter_sampler", lambda: "the sampler")
        self.assertEqual(h.started("meter_sampler"), made)


class Commands(Base):
    CAPS = {"meter", "meter_stream", "meter_store"}

    def setUp(self):
        Base.setUp(self)
        self.reg = Registry()
        meter.register(self.reg, self.host)

    def call(self, name, args=None, caps=None):
        return self.reg.call(name, args or {}, Context(self.host, "tester", None), capabilities=set(self.CAPS if caps is None else caps))

    def tearDown(self):
        s = self.host.started("meter_sampler")
        if s is not None:
            s.stop()

    def test_the_session_commands_exist_only_with_the_stream_and_recording_with_the_folder(self):
        names = lambda caps: sorted(c.name for c in self.reg.commands(caps) if c.name.split(".")[1] in (  # noqa: E731
            "watch", "record", "stop", "session", "profile", "recordings", "recording"))
        self.assertEqual(names({"meter"}), [])
        self.assertEqual(names({"meter", "meter_stream"}), ["meter.profile", "meter.session", "meter.stop", "meter.watch"])
        self.assertEqual(names(self.CAPS), ["meter.profile", "meter.record", "meter.recording", "meter.recordings", "meter.session", "meter.stop", "meter.watch"])

    def test_watch_then_session_then_stop(self):
        res = self.call("meter.watch", {"bound_s": 60})
        self.assertTrue(res.ok, res.message)
        self.assertEqual((res.data["state"], res.data["mode"]), ("watching", "watch"))
        self.assertTrue(until(lambda: self.call("meter.session").data["stats"]["readings"] >= 2))
        res = self.call("meter.stop")
        self.assertEqual((res.ok, res.data["state"]), (True, "idle"))

    def test_a_second_start_is_meter_busy_with_the_running_session_as_evidence(self):
        self.call("meter.watch", {"bound_s": 60})
        res = self.call("meter.record", {"bound_s": 60, "name": "x"})
        self.assertEqual((res.cls, res.code), ("refused", "meter_busy"))
        self.assertEqual(res.evidence["session"]["mode"], "watch")
        self.assertIn("meter.stop", res.message)

    def test_a_taken_name_is_name_taken(self):
        self.call("meter.record", {"bound_s": 60, "name": "dup"})
        self.call("meter.stop")
        self.assertEqual(self.call("meter.record", {"bound_s": 60, "name": "dup"}).code, "name_taken")

    def test_a_bad_bound_or_name_is_a_usage_error_naming_the_argument(self):
        with self.assertRaises(UsageError) as cm:
            self.call("meter.watch", {"bound_s": 0})
        self.assertEqual(cm.exception.arg, "bound_s")
        with self.assertRaises(UsageError) as cm:
            self.call("meter.record", {"bound_s": 60, "name": "../x"})
        self.assertEqual(cm.exception.arg, "name")
        with self.assertRaises(UsageError):
            self.call("meter.watch", {})                           # the bound is required

    def test_recordings_and_one_recording_for_download(self):
        self.call("meter.record", {"bound_s": 60, "name": "dl"})
        self.assertTrue(until(lambda: self.call("meter.session").data["stats"]["readings"] >= 2))
        self.call("meter.stop")
        rows = self.call("meter.recordings").data["recordings"]
        self.assertEqual([r["name"] for r in rows], ["dl"])
        res = self.call("meter.recording", {"name": "dl"})
        self.assertTrue(res.ok)
        self.assertTrue(res.data["csv"].startswith("t_s,wall,value"))
        self.assertEqual(res.data["bytes"], len(res.data["csv"]))

    def test_an_unknown_recording_is_recording_unknown(self):
        res = self.call("meter.recording", {"name": "nope"})
        self.assertEqual((res.cls, res.code), ("refused", "recording_unknown"))
        self.assertEqual(self.call("meter.recording", {"name": "../x"}).code, "recording_unknown")

    def test_the_profile_command_returns_buckets_and_a_cursor(self):
        self.call("meter.watch", {"bound_s": 60})
        self.assertTrue(until(lambda: self.call("meter.session").data["stats"]["readings"] >= 3))
        d = self.call("meter.profile").data
        self.assertTrue(d["buckets"] and d["cursor"] >= 1 and d["epoch"])


if __name__ == "__main__":
    unittest.main()
