#!/usr/bin/env python3
"""Tests for api.feed (the /ws/events websocket) and the activity API (GET /api/activity, /api/streams).

Two groups. The protocol and the rules, over Starlette's in-process websocket client: want, cursors,
epochs, lost counts, errors that do not disconnect, auth, the Origin check, and that every command a
caller runs is recorded. Then a REAL uvicorn server with a real websocket client, for what only a real
socket shows: a publisher on another thread waking a connected client, and a client that vanishes leaving
no subscriber behind. Every wait is on an Event the code sets; the ceilings only end a failing test.
Run:  python3 tests/api/test_feed.py
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from nrf_workbench.api import app as app_module  # noqa: E402
from nrf_workbench.api import feed  # noqa: E402
from nrf_workbench.api.app import build_app  # noqa: E402
from nrf_workbench.api.auth import Auth  # noqa: E402
from nrf_workbench.core.activity import ActivityLog  # noqa: E402
from nrf_workbench.core.events import Bus, Streams  # noqa: E402
from nrf_workbench.core.registry import Arg, Command, Registry  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from test_live import Live  # noqa: E402

TOKEN = "s3cret-token"
H = {"Authorization": "Bearer " + TOKEN}
CEILING_S = 10          # only ever reached by a failing test


def make_registry():
    r = Registry()

    def echo(ctx, text, api_key=None):
        return Result.success({"text": text})

    def refuse(ctx):
        return Result.refused("lock_held", "another instance holds the lock")

    def crash(ctx):
        raise RuntimeError("secret detail")

    r.add(Command("demo.echo", "echo", echo, args=(Arg("text"), Arg("api_key", required=False))), module="t")
    r.add(Command("demo.refuse", "refuse", refuse), module="t")
    r.add(Command("demo.crash", "crash", crash), module="t")
    r.add(Command("demo.write", "write", lambda ctx: Result.success(), effect="write"), module="t")
    r.add(Command("demo.typed", "typed", lambda ctx, n, ratio, dry: Result.success(),
                  args=(Arg("n", "int"), Arg("ratio", "float"), Arg("dry", "bool"))), module="t")

    def typed_crash(ctx, n):
        raise RuntimeError("secret detail")

    r.add(Command("demo.typedcrash", "typed crash", typed_crash, args=(Arg("n", "int"),)), module="t")
    return r


def streams(events=(100, 3600), activity=(100, 3600)):
    return Streams(Bus(*events) if events else None, ActivityLog(*activity) if activity else None,
                   dict((n, "no bound configured") for n, b in (("events", events), ("activity", activity)) if not b))


def app_for(st, token=None, access="token"):
    return build_app(make_registry(), None, Auth(token, access), set(), st)


def want(ws, **feeds):
    ws.send_json({"want": feeds})


class Base(unittest.TestCase):
    token = None
    access = "token"

    def setUp(self):
        self.st = streams()
        self.client = TestClient(app_for(self.st, self.token, self.access))
        self.addCleanup(self.client.close)

    def connect(self, headers=None):
        return self.client.websocket_connect("/ws/events", headers=headers or {})


class Protocol(Base):
    def test_the_first_page_is_sent_even_when_empty_so_the_client_learns_the_epoch(self):
        with self.connect() as ws:
            want(ws, devices={"cursor": 0})
            m = ws.receive_json()
        self.assertEqual((m["type"], m["feed"]), ("events", "devices"))
        self.assertEqual(m["page"], {"events": [], "lost": 0, "reset": False, "latest": 0, "epoch": self.st.events.epoch})

    def test_what_was_published_before_is_sent_then_what_comes_after_is_pushed_alone(self):
        for i in range(3):
            self.st.events.publish("devices.changed", "devices", {"i": i})
        with self.connect() as ws:
            want(ws, devices={"cursor": 0})
            first = ws.receive_json()["page"]
            self.assertEqual([e["cursor"] for e in first["events"]], [1, 2, 3])
            self.st.events.publish("devices.changed", "devices", {"i": 3})
            second = ws.receive_json()["page"]
        self.assertEqual([(e["cursor"], e["payload"]) for e in second["events"]], [(4, {"i": 3})])

    def test_a_resume_from_a_cursor_sends_only_what_came_after(self):
        for i in range(5):
            self.st.events.publish("devices.changed", "devices", {"i": i})
        with self.connect() as ws:
            want(ws, devices={"cursor": 3, "epoch": self.st.events.epoch})
            page = ws.receive_json()["page"]
        self.assertEqual(([e["cursor"] for e in page["events"]], page["lost"], page["reset"]), ([4, 5], 0, False))

    def test_only_the_feeds_event_types_are_on_the_feed(self):
        self.st.events.publish("console.line", "console", {})
        self.st.events.publish("devices.changed", "devices", {})
        with self.connect() as ws:
            want(ws, devices={"cursor": 0})
            page = ws.receive_json()["page"]
        self.assertEqual([e["type"] for e in page["events"]], ["devices.changed"])

    def test_the_activity_feed_carries_command_results(self):
        self.st.activity.record("cli", "programmer.flash", {"serial": "S"}, Result.success(), 1.5)
        with self.connect() as ws:
            want(ws, activity={"cursor": 0})
            m = ws.receive_json()
        self.assertEqual(m["feed"], "activity")
        self.assertEqual(m["page"]["events"][0]["payload"]["command"], "programmer.flash")

    def test_two_feeds_at_once_and_a_second_want_adds_one(self):
        with self.connect() as ws:
            want(ws, devices={"cursor": 0})
            self.assertEqual(ws.receive_json()["feed"], "devices")
            want(ws, devices={"cursor": 0}, activity={"cursor": 0})
            self.assertEqual(sorted(ws.receive_json()["feed"] for _ in range(2)), ["activity", "devices"])

    def test_a_cursor_older_than_what_is_retained_says_how_many_were_lost(self):
        self.st = streams(events=(3, 3600))
        self.client = TestClient(app_for(self.st))
        for _ in range(10):
            self.st.events.publish("devices.changed", "devices")
        with self.connect() as ws:
            want(ws, devices={"cursor": 0})
            page = ws.receive_json()["page"]
        self.assertEqual((page["lost"], [e["cursor"] for e in page["events"]]), (7, [8, 9, 10]))

    def test_another_epoch_is_a_reset_and_not_a_loss_count(self):
        self.st.events.publish("devices.changed", "devices")
        with self.connect() as ws:
            want(ws, devices={"cursor": 7, "epoch": "the-service-before-it-restarted"})
            page = ws.receive_json()["page"]
        self.assertEqual((page["reset"], page["lost"], page["epoch"]), (True, None, self.st.events.epoch))

    def burst(self, st, n):
        """Read a feed while another thread publishes `n` events as fast as it can. Returns (cursors seen, lost)."""
        client = TestClient(app_for(st))
        self.addCleanup(client.close)
        seen, lost = [], 0

        def publisher():
            for i in range(n):
                st.events.publish("devices.changed", "devices", {"i": i})

        with client.websocket_connect("/ws/events") as ws:
            want(ws, devices={"cursor": 0})
            t = threading.Thread(target=publisher)
            t.start()
            while len(seen) + lost < n:               # every event is either delivered or counted as lost
                page = ws.receive_json()["page"]
                lost += page["lost"] or 0
                seen += [e["cursor"] for e in page["events"]]
            t.join()
        return seen, lost

    def test_after_a_reset_the_connection_follows_the_new_epoch_and_does_not_replay_everything(self):
        self.st.events.publish("devices.changed", "devices", {"i": 0})
        with self.connect() as ws:
            want(ws, devices={"cursor": 5, "epoch": "the-service-before-it-restarted"})
            first = ws.receive_json()["page"]
            self.assertTrue(first["reset"])
            self.st.events.publish("devices.changed", "devices", {"i": 1})
            second = ws.receive_json()["page"]
        self.assertFalse(second["reset"], "the stored epoch must have been updated, or every page is a reset")
        self.assertEqual([e["payload"] for e in second["events"]], [{"i": 1}], "only the new event, not a replay")

    def test_the_wake_up_from_a_publisher_thread_goes_through_the_loops_thread_safe_call(self):
        # asyncio.Event.set() is NOT thread-safe: called from another thread it can leave an idle loop asleep.
        # (A test that waits for a delivery cannot tell: the server's own timers wake the loop within seconds.)
        calls = []

        class LoopSpy(object):
            def __init__(self, loop):
                self._loop = loop

            def call_soon_threadsafe(self, *args, **kw):
                calls.append(args[0].__qualname__)
                return self._loop.call_soon_threadsafe(*args, **kw)

            def __getattr__(self, name):
                return getattr(self._loop, name)

        real = feed.asyncio.get_running_loop
        with mock.patch.object(feed.asyncio, "get_running_loop", lambda: LoopSpy(real())):
            with self.connect() as ws:
                want(ws, devices={"cursor": 0})
                ws.receive_json()
                for i in range(3):
                    self.st.events.publish("devices.changed", "devices", {"i": i})
                got = 0
                while got < 3:
                    got += len(ws.receive_json()["page"]["events"])
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all("set" in name for name in calls), calls)

    def test_a_reader_that_resumes_sees_every_event_exactly_once(self):
        seen, lost = self.burst(streams(events=(1000, 3600)), 200)         # room for all of them: nothing can be lost
        self.assertEqual((seen, lost), (list(range(1, 201)), 0))

    def test_when_events_are_lost_every_one_is_still_accounted_for_exactly(self):
        # A bus that holds 5 while 500 are published at full speed: the reader cannot keep up, so some are
        # pruned. Each is either delivered or counted as lost, never both and never neither, and what IS
        # delivered is in order with no repeat.
        for _ in range(5):
            seen, lost = self.burst(streams(events=(5, 3600)), 500)
            self.assertEqual(len(seen) + lost, 500)
            self.assertEqual(seen, sorted(set(seen)), "delivered cursors must be increasing and never repeated")
            self.assertEqual(seen[-1], 500, "the newest event always arrives")


class Errors(Base):
    def test_every_malformed_message_is_an_error_and_the_connection_stays_open(self):
        with self.connect() as ws:
            for bad in ("not json", "[1, 2]", json.dumps({"hello": 1}), json.dumps({"want": "devices"}),
                        json.dumps({"want": ["devices"]})):
                ws.send_text(bad)
                m = ws.receive_json()
                self.assertEqual(m["type"], "error", bad)
            want(ws, devices={"cursor": 0})                      # and it still works afterwards
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_an_unknown_feed_names_the_feeds_that_exist(self):
        with self.connect() as ws:
            want(ws, nonsense={"cursor": 0})
            m = ws.receive_json()
        self.assertEqual((m["type"], m["feed"]), ("error", "nonsense"))
        self.assertIn("activity, devices", m["message"])

    def test_a_bad_cursor_is_an_error_for_that_feed_only(self):
        with self.connect() as ws:
            for bad in (-1, True, "3", 1.5, None):
                want(ws, devices={"cursor": bad})
                m = ws.receive_json()
                self.assertEqual((m["type"], m["feed"]), ("error", "devices"), repr(bad))
            want(ws, devices={"cursor": 0}, nonsense={})
            kinds = sorted(ws.receive_json()["type"] for _ in range(2))
        self.assertEqual(kinds, ["error", "events"])


class Security(unittest.TestCase):
    def client_for(self, token=None, access="token", st=None):
        client = TestClient(app_for(st or streams(), token, access))
        self.addCleanup(client.close)
        return client

    def refused(self, client, headers):
        with self.assertRaises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/ws/events", headers=headers):
                pass
        return caught.exception.code

    def test_a_browser_origin_must_be_this_host(self):
        client = self.client_for()
        self.assertEqual(self.refused(client, {"origin": "http://evil.example"}), feed.CLOSE_FORBIDDEN_ORIGIN)
        self.assertEqual(self.refused(client, {"origin": "http://testserver.evil.example"}), feed.CLOSE_FORBIDDEN_ORIGIN)
        self.assertEqual(self.refused(client, {"origin": "null"}), feed.CLOSE_FORBIDDEN_ORIGIN)
        with client.websocket_connect("/ws/events", headers={"origin": "http://testserver"}) as ws:
            want(ws, devices={})
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_no_origin_is_not_a_browser_and_is_allowed(self):
        client = self.client_for()
        with client.websocket_connect("/ws/events") as ws:
            want(ws, devices={})
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_origin_allowed_rule(self):
        for origin, host, ok in ((None, "h", True), ("http://h", "h", True), ("http://H:8080", "h:8080", True),
                                 ("http://h:81", "h:80", False), ("http://other", "h", False), ("", "h", False),
                                 ("http://h", None, False), ("null", "h", False)):
            headers = {}
            if origin is not None:
                headers["origin"] = origin
            if host is not None:
                headers["host"] = host
            self.assertEqual(feed.origin_allowed(headers), ok, (origin, host))

    def test_in_token_mode_the_token_is_required_for_the_feed(self):
        client = self.client_for(token=TOKEN)
        self.assertEqual(self.refused(client, {}), feed.CLOSE_UNAUTHORIZED)
        self.assertEqual(self.refused(client, {"Authorization": "Bearer wrong"}), feed.CLOSE_UNAUTHORIZED)
        with client.websocket_connect("/ws/events", headers=H) as ws:
            want(ws, devices={})
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_in_open_read_mode_the_feed_is_a_read_and_needs_no_token(self):
        client = self.client_for(token=TOKEN, access="open-read")
        with client.websocket_connect("/ws/events") as ws:
            want(ws, devices={})
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_the_origin_is_checked_before_the_token_so_a_foreign_page_learns_nothing(self):
        client = self.client_for(token=TOKEN)
        self.assertEqual(self.refused(client, dict(H, origin="http://evil.example")), feed.CLOSE_FORBIDDEN_ORIGIN)
        # With NO token the answer must still be the origin refusal: a foreign page is not even told that a
        # token exists (checking the token first would answer 4401 here).
        self.assertEqual(self.refused(client, {"origin": "http://evil.example"}), feed.CLOSE_FORBIDDEN_ORIGIN)

    def test_with_no_streams_configured_there_is_no_feed_at_all(self):
        client = self.client_for(st=streams(events=None, activity=None))
        with self.assertRaises(Exception):
            with client.websocket_connect("/ws/events"):
                pass

    def test_a_feed_that_is_not_configured_is_not_offered_but_the_other_is(self):
        client = self.client_for(st=streams(events=None))
        with client.websocket_connect("/ws/events") as ws:
            want(ws, devices={"cursor": 0})
            m = ws.receive_json()
            self.assertEqual((m["type"], m["feed"]), ("error", "devices"))
            self.assertIn("activity", m["message"])
            self.assertNotIn("devices", m["message"].split("offers:")[1])


class ActivityRecording(Base):
    def entries(self):
        return [e.payload for e in self.st.activity.since(0).events]

    def test_every_command_is_recorded_with_who_what_class_code_and_duration(self):
        self.client.get("/api/demo/echo", params={"text": "hi"})
        self.client.get("/api/demo/refuse")
        self.client.post("/api/demo/write")
        a, b, c = self.entries()
        self.assertEqual((a["command"], a["class"], a["ok"], a["caller"], a["args"]), ("demo.echo", "ok", True, "local", {"text": "hi"}))
        self.assertEqual((b["command"], b["class"], b["code"], b["ok"]), ("demo.refuse", "refused", "lock_held", False))
        self.assertEqual((c["command"], c["class"]), ("demo.write", "ok"))
        for e in (a, b, c):
            self.assertGreaterEqual(e["duration_s"], 0)

    def test_the_duration_is_the_measured_time_the_command_took(self):
        class FakeTime(object):
            readings = iter([100.0, 102.5])

            def monotonic(self):
                return next(self.readings)

        with mock.patch.object(app_module, "time", FakeTime()):
            self.client.get("/api/demo/refuse")
        (e,) = self.entries()
        self.assertEqual(e["duration_s"], 2.5)

    def test_the_arguments_recorded_are_the_typed_values_the_command_ran_with(self):
        # A query string is text; the log must show what the command RAN with, not '7' and '2.5' and 'true'.
        self.client.get("/api/demo/typed", params={"n": "7", "ratio": "2.5", "dry": "true"})
        (e,) = self.entries()
        self.assertEqual(e["args"], {"n": 7, "ratio": 2.5, "dry": True})
        self.assertEqual([type(e["args"][k]) for k in ("n", "ratio", "dry")], [int, float, bool])

    def test_a_crash_is_recorded_with_the_typed_arguments_too(self):
        with self.assertLogs("nrf_workbench.api", level="ERROR"):
            self.client.get("/api/demo/typedcrash", params={"n": "42"})
        (e,) = self.entries()
        self.assertEqual((e["class"], e["args"]), ("error", {"n": 42}))

    def test_only_the_arguments_the_caller_sent_are_recorded_not_the_defaults_filled_in(self):
        self.client.get("/api/demo/echo", params={"text": "hi"})          # api_key is optional and was not sent
        self.assertEqual(self.entries()[0]["args"], {"text": "hi"})

    def test_a_rejected_argument_is_recorded_as_the_caller_sent_it_so_the_mistake_can_be_seen(self):
        self.client.get("/api/demo/typed", params={"n": "seven", "ratio": "1", "dry": "false"})
        (e,) = self.entries()
        self.assertEqual((e["class"], e["args"]), ("usage", {"n": "seven", "ratio": "1", "dry": "false"}))

    def test_a_secret_is_still_redacted_when_the_typed_arguments_are_recorded(self):
        self.client.get("/api/demo/echo", params={"text": "hi", "api_key": "SECRET-VALUE"})
        self.assertEqual(self.entries()[0]["args"], {"text": "hi", "api_key": "***"})

    def test_a_bad_argument_is_recorded_as_usage(self):
        self.client.get("/api/demo/echo")                                  # missing text
        self.client.get("/api/demo/echo?text=a&text=b")                    # a repeated parameter
        a, b = self.entries()
        self.assertEqual((a["class"], a["code"], a["ok"]), ("usage", "usage", False))
        self.assertEqual(b["class"], "usage")

    def test_a_handler_that_crashes_is_recorded_as_an_error_with_no_detail(self):
        with self.assertLogs("nrf_workbench.api", level="ERROR"):
            self.client.get("/api/demo/crash")
        (e,) = self.entries()
        self.assertEqual((e["class"], e["code"], e["ok"]), ("error", "internal", False))
        self.assertNotIn("secret detail", json.dumps(e))
        self.assertIn("RuntimeError", e["message"])

    def test_a_secret_argument_is_redacted_in_the_record(self):
        self.client.get("/api/demo/echo", params={"text": "hi", "api_key": "SECRET-VALUE"})
        self.assertNotIn("SECRET-VALUE", json.dumps(self.entries()))
        self.assertEqual(self.entries()[0]["args"]["api_key"], "***")

    def test_an_unauthorised_request_is_not_a_command_that_ran_and_is_not_recorded(self):
        client = TestClient(app_for(self.st, TOKEN))
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/demo/echo", params={"text": "x"}).status_code, 401)
        self.assertEqual(self.entries(), [])
        client.get("/api/demo/echo", params={"text": "x"}, headers=H)
        self.assertEqual(self.entries()[0]["caller"], "token")

    def test_an_anonymous_read_in_open_read_mode_is_recorded_as_anonymous(self):
        client = TestClient(app_for(self.st, TOKEN, "open-read"))
        self.addCleanup(client.close)
        client.get("/api/demo/echo", params={"text": "x"})
        self.assertEqual(self.entries()[0]["caller"], "anonymous")

    def test_with_no_activity_log_commands_still_work_and_nothing_is_recorded(self):
        client = TestClient(app_for(streams(activity=None)))
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/demo/echo", params={"text": "x"}).status_code, 200)

    def test_a_recorded_command_wakes_a_connected_activity_feed(self):
        with self.connect() as ws:
            want(ws, activity={"cursor": 0})
            ws.receive_json()
            self.client.get("/api/demo/echo", params={"text": "hi"})
            page = ws.receive_json()["page"]
        self.assertEqual(page["events"][0]["payload"]["command"], "demo.echo")


class Gone(BaseException):
    """What a cancelled request raises through the endpoint: not an Exception, so no `except Exception` sees it."""


class InProgress(Base):
    """A write command announces its start and ends it on EVERY path out (docs/specs/core.md), so a start with no
    result always means the command is really still running."""

    def setUp(self):
        Base.setUp(self)
        self.seen_during = []

        def watch(ctx):
            # the start must already be public while the handler is still running
            self.seen_during.append([(e.type, e.payload) for e in self.st.activity.bus.since(0).events])
            return Result.success()

        def refuse(ctx):
            return Result.refused("lock_held", "another instance holds the lock")

        def crash(ctx):
            raise RuntimeError("secret detail")

        def gone(ctx):
            raise Gone()

        def bad(ctx):
            from nrf_workbench.core.registry import UsageError
            raise UsageError("the handler did not like it", "x")

        r = make_registry()
        for name, fn in (("watch", watch), ("wrefuse", refuse), ("wcrash", crash), ("wgone", gone), ("wbad", bad)):
            r.add(Command("demo." + name, name, fn, effect="write"), module="t")
        r.add(Command("demo.wtyped", "typed", lambda ctx, n, api_key=None: Result.success(), effect="write",
                      args=(Arg("n", "int"), Arg("api_key", required=False))), module="t")
        self.client = TestClient(build_app(r, None, Auth(None, "token"), set(), self.st))
        self.addCleanup(self.client.close)

    def everything(self):
        return [(e.type, e.payload) for e in self.st.activity.bus.since(0).events]

    def pairs(self):
        """{run: (start payload, result payload or None)} from the stream."""
        out = {}
        for kind, p in self.everything():
            if kind == "command.started":
                out[p["run"]] = [p, None]
            elif "run" in p:
                out[p["run"]][1] = p
        return out

    def test_the_start_is_public_before_the_handler_runs_and_the_result_carries_the_same_run(self):
        self.client.post("/api/demo/watch")
        (during,) = self.seen_during
        self.assertEqual([k for k, _p in during], ["command.started"], "the handler saw no start of its own command")
        ((start, result),) = self.pairs().values()
        self.assertEqual((start["command"], start["caller"], result["class"]), ("demo.watch", "local", "ok"))
        self.assertEqual(start["run"], result["run"])

    def test_a_read_is_never_announced(self):
        self.client.get("/api/demo/echo", params={"text": "hi"})
        self.assertEqual([k for k, _p in self.everything()], ["command.result"])
        self.assertNotIn("run", self.everything()[0][1])

    def test_every_way_a_write_can_end_ends_its_start(self):
        self.client.post("/api/demo/watch")                                      # a result
        self.client.post("/api/demo/wrefuse")                                    # a refusal
        with self.assertLogs("nrf_workbench.api", level="ERROR"):
            self.client.post("/api/demo/wcrash")                                 # a handler that raised
        self.client.post("/api/demo/wbad")                                       # a usage error from the handler
        with self.assertRaises(Gone):
            self.client.post("/api/demo/wgone")                                  # a request that was cancelled
        pairs = self.pairs()
        self.assertEqual(len(pairs), 5)
        classes = sorted(result["class"] for _start, result in pairs.values())
        self.assertEqual(classes, ["error", "error", "ok", "refused", "usage"])
        for start, result in pairs.values():
            self.assertIsNotNone(result, "%s was started and never ended" % start["command"])
        cancelled = [r for s, r in pairs.values() if s["command"] == "demo.wgone"][0]
        self.assertIn("ended before the command reported", cancelled["message"])

    def test_a_bad_argument_to_a_write_is_not_announced_it_never_ran(self):
        r = self.client.post("/api/demo/wtyped", json={"n": "seven"})          # right place, wrong type
        self.assertEqual(r.status_code, 400)
        self.assertIn("n", r.json()["message"])
        self.assertEqual([k for k, _p in self.everything()], ["command.result"])
        self.assertEqual(self.everything()[0][1]["class"], "usage")
        self.assertNotIn("run", self.everything()[0][1])

    def test_the_start_shows_the_typed_arguments_with_secrets_redacted(self):
        self.assertEqual(self.client.post("/api/demo/wtyped", json={"n": 7, "api_key": "SECRET-VALUE"}).status_code, 200)
        ((start, _result),) = self.pairs().values()
        self.assertEqual(start["args"], {"n": 7, "api_key": "***"})
        self.assertNotIn("SECRET-VALUE", json.dumps(self.everything()))

    def test_two_writes_are_two_runs(self):
        self.client.post("/api/demo/watch")
        self.client.post("/api/demo/watch")
        self.assertEqual(len(self.pairs()), 2)

    def test_an_unauthorised_write_is_not_announced(self):
        client = TestClient(app_for(self.st, TOKEN))
        self.addCleanup(client.close)
        self.assertEqual(client.post("/api/demo/write").status_code, 401)
        self.assertEqual(self.everything(), [])

    def test_the_history_endpoint_has_results_only_and_the_live_feed_has_both(self):
        self.client.post("/api/demo/watch")
        history = self.client.get("/api/activity").json()
        self.assertEqual([e["type"] for e in history["events"]], ["command.result"])
        with self.connect() as ws:
            want(ws, activity={"cursor": 0})
            page = ws.receive_json()["page"]
        self.assertEqual([e["type"] for e in page["events"]], ["command.started", "command.result"])

    def test_a_connection_made_while_a_command_is_running_is_told_it_is_running(self):
        # the page opened mid-flash: the retained start replays and no result follows it yet
        self.st.activity.begin("mcp", "programmer.flash", {"serial": "S"})
        with self.connect() as ws:
            want(ws, activity={"cursor": 0})
            page = ws.receive_json()["page"]
        self.assertEqual([e["type"] for e in page["events"]], ["command.started"])
        self.assertEqual(page["events"][0]["payload"]["caller"], "mcp")

    def test_with_no_activity_log_a_write_still_works(self):
        r = make_registry()
        r.add(Command("demo.w", "w", lambda ctx: Result.success(), effect="write"), module="t")
        client = TestClient(build_app(r, None, Auth(None, "token"), set(), streams(activity=None)))
        self.addCleanup(client.close)
        self.assertEqual(client.post("/api/demo/w").status_code, 200)


class ActivityApi(Base):
    def test_the_activity_endpoint_pages_by_cursor_and_epoch(self):
        for i in range(4):
            self.client.get("/api/demo/echo", params={"text": str(i)})
        body = self.client.get("/api/activity").json()
        self.assertEqual(([e["cursor"] for e in body["events"]], body["lost"], body["reset"], body["latest"]), ([1, 2, 3, 4], 0, False, 4))
        self.assertEqual(body["epoch"], self.st.activity.bus.epoch)
        again = self.client.get("/api/activity", params={"cursor": "2", "epoch": body["epoch"]}).json()
        self.assertEqual([e["cursor"] for e in again["events"]], [3, 4])
        reset = self.client.get("/api/activity", params={"cursor": "2", "epoch": "an-older-service"}).json()
        self.assertEqual((reset["reset"], reset["lost"]), (True, None))

    def test_a_bad_cursor_is_a_400(self):
        for bad in ("-1", "x", "1.5"):
            self.assertEqual(self.client.get("/api/activity", params={"cursor": bad}).status_code, 400, bad)
        self.assertEqual(self.client.get("/api/activity?cursor=1&cursor=2").status_code, 400)

    def test_without_a_log_it_is_a_404_that_says_why(self):
        client = TestClient(app_for(streams(activity=None)))
        self.addCleanup(client.close)
        r = client.get("/api/activity")
        self.assertEqual(r.status_code, 404)
        self.assertIn("no bound configured", r.json()["message"])

    def test_the_streams_endpoint_says_what_is_on_and_what_is_off_and_why(self):
        self.assertEqual(self.client.get("/api/streams").json(),
                         {"events": True, "activity": True, "console": False, "meter": False, "absent": {}})
        client = TestClient(app_for(streams(events=None)))
        self.addCleanup(client.close)
        body = client.get("/api/streams").json()
        self.assertEqual((body["events"], body["activity"]), (False, True))
        self.assertEqual(body["absent"], {"events": "no bound configured"})

    def test_an_app_built_with_no_streams_at_all_still_answers(self):
        client = TestClient(build_app(make_registry(), None, Auth(None, "token"), set()))
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/streams").json(),
                         {"events": False, "activity": False, "console": False, "meter": False, "absent": {}})
        self.assertEqual(client.get("/api/activity").status_code, 404)
        self.assertEqual(client.get("/api/demo/echo", params={"text": "x"}).status_code, 200)

    def test_token_mode_protects_both_endpoints_and_open_read_opens_them(self):
        locked = TestClient(app_for(streams(), TOKEN))
        self.addCleanup(locked.close)
        for path in ("/api/activity", "/api/streams"):
            self.assertEqual(locked.get(path).status_code, 401, path)
            self.assertEqual(locked.get(path, headers=H).status_code, 200, path)
        opened = TestClient(app_for(streams(), TOKEN, "open-read"))
        self.addCleanup(opened.close)
        for path in ("/api/activity", "/api/streams"):
            self.assertEqual(opened.get(path).status_code, 200, path)


class RealSocket(unittest.TestCase):
    """A real uvicorn server and a real websocket client."""

    def setUp(self):
        self.st = streams()
        self.live = Live(app_for(self.st))
        self.live.__enter__()
        self.addCleanup(self.live.__exit__)
        self.url = "ws://127.0.0.1:%d/ws/events" % self.live.port

    def connect(self, **kw):
        from websockets.sync.client import connect
        return connect(self.url, **kw)

    def test_a_publisher_on_another_thread_wakes_a_connected_client(self):
        ws = self.connect()
        self.addCleanup(ws.close)
        ws.send(json.dumps({"want": {"devices": {"cursor": 0}}}))
        first = json.loads(ws.recv(timeout=CEILING_S))
        self.assertEqual(first["page"]["events"], [])
        t = threading.Thread(target=lambda: self.st.events.publish("devices.changed", "devices", {"boards": ["X"]}))
        t.start()
        t.join()
        pushed = json.loads(ws.recv(timeout=CEILING_S))
        self.assertEqual(pushed["page"]["events"][0]["payload"], {"boards": ["X"]})

    def test_a_client_that_vanishes_leaves_no_subscriber_behind(self):
        gone = threading.Event()
        bus = self.st.events
        real = bus.unsubscribe

        def watched(token):
            result = real(token)
            gone.set()
            return result

        bus.unsubscribe = watched
        ws = self.connect()
        ws.send(json.dumps({"want": {"devices": {"cursor": 0}}}))
        ws.recv(timeout=CEILING_S)
        self.assertEqual(bus.subscriber_count, 1)
        ws.socket.close()                                    # no close handshake: the connection just dies
        self.assertTrue(gone.wait(CEILING_S), "the server never unsubscribed")
        self.assertEqual(bus.subscriber_count, 0)

    def test_several_clients_each_get_every_event(self):
        clients = [self.connect() for _ in range(3)]
        for c in clients:
            self.addCleanup(c.close)
            c.send(json.dumps({"want": {"devices": {"cursor": 0}}}))
            c.recv(timeout=CEILING_S)
        for i in range(5):
            self.st.events.publish("devices.changed", "devices", {"i": i})
        for c in clients:
            got = []
            while len(got) < 5:
                got += [e["payload"]["i"] for e in json.loads(c.recv(timeout=CEILING_S))["page"]["events"]]
            self.assertEqual(got, [0, 1, 2, 3, 4])

    def test_a_foreign_origin_is_refused_on_a_real_connection(self):
        from websockets.exceptions import InvalidStatus
        with self.assertRaises(InvalidStatus) as caught:
            self.connect(origin="http://evil.example")
        self.assertEqual(caught.exception.response.status_code, 403)
        ws = self.connect(origin="http://127.0.0.1:%d" % self.live.port)
        ws.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
