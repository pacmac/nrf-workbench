#!/usr/bin/env python3
"""Tests for api.app: routes generated from the registry, driven through a real ASGI client.

A fake registry exercises each shape (read, JSON write, raw upload, a handler that crashes, a command
whose requirement is unmet, a stream). What matters: the response body is the unmodified Result with the
status from core.status, a bad argument is a 400 and never a Result, a handler bug leaks no traceback,
auth is applied before anything is read, and an upload is streamed and never buffered whole.
Run:  python3 tests/api/test_app.py
"""
import hashlib
import os
import sys
import threading
import unittest

from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.api.app import build_app  # noqa: E402
from nrf_workbench.api.auth import Auth  # noqa: E402
from nrf_workbench.core.registry import Arg, Command, Registry  # noqa: E402
from nrf_workbench.core.result import SUSPECT, Result  # noqa: E402

TOKEN = "s3cret-token"
H = {"Authorization": "Bearer " + TOKEN}


class Recorder(object):
    """What the handlers saw, so a test can say what was and was not run."""

    def __init__(self):
        self.upload_calls = 0
        self.callers = []
        self.slow = threading.Event()


def make_registry(rec):
    r = Registry()

    def echo(ctx, text, n=None):
        rec.callers.append(ctx.caller)
        return Result.success({"text": text, "n": n})

    def refuse(ctx, code):
        return Result.refused(code, "refused for the test")

    def fail(ctx):
        return Result.failed("flash_failed", "it went wrong", {"output": "whole output"})

    def suspect(ctx):
        return Result(SUSPECT, "flash_suspect", "do not trust it")

    def crash(ctx):
        raise RuntimeError("secret detail /root/key")

    def add(ctx, a, b):
        rec.callers.append(ctx.caller)
        return Result.success({"sum": a + b})

    def upload(ctx, label=None):
        rec.upload_calls += 1
        h, total = hashlib.sha256(), 0
        while True:
            chunk = ctx.body.read(8192)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)
        return Result.success({"bytes": total, "sha256": h.hexdigest(), "length": ctx.body.length, "label": label,
                               "caller": ctx.caller})

    def slow(ctx):
        rec.slow.wait(timeout=10)
        return Result.success({"slow": True})

    def fast(ctx):
        return Result.success({"fast": True})

    def cli_only(ctx):
        return Result.refused("port_in_use", "x")

    for row in (
        Command("demo.echo", "echo", echo, args=(Arg("text"), Arg("n", type="int", required=False))),
        Command("demo.refuse", "refuse", refuse, args=(Arg("code"),)),
        Command("demo.fail", "fail", fail),
        Command("demo.suspect", "suspect", suspect),
        Command("demo.crash", "crash", crash),
        Command("demo.clionly", "clionly", cli_only),
        Command("demo.fast", "fast", fast),
        Command("demo.slow", "slow", slow),
        Command("demo.add", "add", add, args=(Arg("a", type="int"), Arg("b", type="int")), effect="write"),
        Command("demo.upload", "upload", upload, args=(Arg("label", required=False),), effect="write", body="raw"),
        Command("demo.pionly", "pi", fast, requires=("pi_header",)),
        Command("demo.watch", "watch", fast, effect="stream", stream="demo.event"),
    ):
        r.add(row, module="test")
    return r


class Base(unittest.TestCase):
    token = None
    access = "token"

    def setUp(self):
        self.rec = Recorder()
        self.reg = make_registry(self.rec)
        self.app = build_app(self.reg, host=None, auth=Auth(self.token, self.access), capabilities={"sysfs"})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)


class Listing(Base):
    def test_commands_lists_what_is_present_with_every_derived_name(self):
        body = self.client.get("/api/commands").json()
        names = [c["name"] for c in body["commands"]]
        self.assertIn("demo.echo", names)
        self.assertNotIn("demo.pionly", names, "an unmet requirement is absent, not listed")
        echo = [c for c in body["commands"] if c["name"] == "demo.echo"][0]
        self.assertEqual((echo["path"], echo["cli"], echo["mcp"], echo["effect"]),
                         ("/api/demo/echo", "demo echo", "demo_echo", "read"))
        self.assertEqual([a["name"] for a in echo["args"]], ["text", "n"])

    def test_capabilities_say_what_is_present_and_why_a_command_is_absent(self):
        body = self.client.get("/api/capabilities").json()
        self.assertEqual(body["present"], ["sysfs"])
        self.assertEqual(body["absent"], [{"name": "demo.pionly", "unmet": ["pi_header"]}])

    def test_an_absent_command_is_a_404_not_a_500(self):
        r = self.client.get("/api/demo/pionly")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["ok"], False)

    def test_a_stream_command_is_not_an_http_route(self):
        self.assertEqual(self.client.get("/api/demo/watch").status_code, 404)

    def test_the_wrong_method_is_a_json_405_and_an_unknown_path_a_json_404(self):
        for method, path, code in (("post", "/api/demo/echo", 405), ("get", "/api/demo/add", 405),
                                   ("get", "/api/nothing/here", 404)):
            r = getattr(self.client, method)(path)
            self.assertEqual(r.status_code, code, path)
            self.assertEqual(r.json()["error"], "http", path)


class Results(Base):
    def test_a_read_passes_its_query_arguments_typed_and_returns_the_result_unmodified(self):
        r = self.client.get("/api/demo/echo", params={"text": "hi", "n": "7"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "class": "ok", "code": "ok", "message": "", "data": {"text": "hi", "n": 7},
                                    "evidence": {}})

    def test_each_refusal_kind_has_the_status_core_gives_it(self):
        for code, want in (("lock_held", 409), ("protected", 409), ("in_use", 409), ("bad_package", 422),
                           ("wrong_softdevice", 422), ("checksum_mismatch", 422), ("firmware_unknown", 404),
                           ("length_required", 411), ("store_full", 507)):
            r = self.client.get("/api/demo/refuse", params={"code": code})
            self.assertEqual(r.status_code, want, code)
            self.assertEqual((r.json()["class"], r.json()["code"], r.json()["ok"]), ("refused", code, False))

    def test_failed_and_suspect_are_500_with_ok_false_and_the_whole_evidence(self):
        r = self.client.get("/api/demo/fail")
        self.assertEqual((r.status_code, r.json()["class"], r.json()["evidence"]["output"]), (500, "failed", "whole output"))
        r = self.client.get("/api/demo/suspect")
        self.assertEqual((r.status_code, r.json()["class"], r.json()["ok"]), (500, "suspect", False))

    def test_a_cli_only_code_never_gets_a_none_status(self):
        r = self.client.get("/api/demo/clionly")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["code"], "port_in_use")

    def test_a_handler_that_crashes_is_a_500_that_leaks_no_detail(self):
        with self.assertLogs("nrf_workbench.api", level="ERROR"):
            r = self.client.get("/api/demo/crash")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json()["error"], "internal")
        self.assertIn("RuntimeError", r.json()["message"])
        self.assertNotIn("secret detail", r.text)
        self.assertNotIn("/root/key", r.text)


class Usage(Base):
    def test_bad_arguments_are_a_400_and_never_a_result(self):
        for path, params in (("/api/demo/echo", {}),                                  # missing required
                             ("/api/demo/echo", {"text": "x", "n": "abc"}),           # not an int
                             ("/api/demo/echo", {"text": "x", "extra": "1"})):        # unknown argument
            r = self.client.get(path, params=params)
            self.assertEqual(r.status_code, 400, (path, params))
            body = r.json()
            self.assertEqual((body["ok"], body["error"]), (False, "usage"))
            self.assertNotIn("class", body, "a usage error is not a Result")

    def test_a_repeated_query_parameter_is_a_400(self):
        r = self.client.get("/api/demo/echo?text=a&text=b")
        self.assertEqual(r.status_code, 400)

    def test_the_usage_error_names_the_argument(self):
        r = self.client.get("/api/demo/echo", params={"text": "x", "n": "abc"})
        self.assertEqual(r.json()["arg"], "n")


class JsonWrites(Base):
    def test_a_json_write(self):
        r = self.client.post("/api/demo/add", json={"a": 1, "b": 2})
        self.assertEqual((r.status_code, r.json()["data"]), (200, {"sum": 3}))

    def test_json_is_checked_not_parsed(self):
        for body in ({"a": "1", "b": 2}, {"a": 1.5, "b": 2}, {"a": True, "b": 2}, {"a": 1}, {"a": 1, "b": 2, "c": 3}):
            r = self.client.post("/api/demo/add", json=body)
            self.assertEqual(r.status_code, 400, body)

    def test_not_json_or_not_an_object_or_empty(self):
        for content in (b"{ nope", b"[1, 2]", b"7", b""):
            r = self.client.post("/api/demo/add", content=content)
            self.assertEqual(r.status_code, 400, content)

    def test_a_json_command_does_not_take_query_arguments(self):
        r = self.client.post("/api/demo/add?a=1&b=2", json={"a": 1, "b": 2})
        self.assertEqual(r.status_code, 400)


class Uploads(Base):
    def test_a_raw_body_is_streamed_with_its_declared_length_and_query_arguments(self):
        data = os.urandom(200000)
        r = self.client.post("/api/demo/upload", params={"label": "parking"}, content=data)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual((d["bytes"], d["length"], d["label"]), (len(data), len(data), "parking"))
        self.assertEqual(d["sha256"], hashlib.sha256(data).hexdigest())

    def test_a_chunked_body_has_no_declared_length(self):
        def chunks():
            for _ in range(5):
                yield b"x" * 1000

        r = self.client.post("/api/demo/upload", content=chunks())
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["data"]
        self.assertEqual((d["bytes"], d["length"]), (5000, None))

    def test_a_large_upload_arrives_complete(self):
        size, piece = 5 * 1024 * 1024, 64 * 1024

        def chunks():
            for i in range(size // piece):
                yield bytes([i % 251]) * piece

        want = hashlib.sha256()
        for i in range(size // piece):
            want.update(bytes([i % 251]) * piece)
        r = self.client.post("/api/demo/upload", content=chunks())
        self.assertEqual(r.json()["data"]["bytes"], size)
        self.assertEqual(r.json()["data"]["sha256"], want.hexdigest())

    def test_an_empty_upload(self):
        r = self.client.post("/api/demo/upload", content=b"")
        self.assertEqual((r.status_code, r.json()["data"]["bytes"]), (200, 0))

    def test_bad_query_arguments_refuse_before_the_body_is_read(self):
        r = self.client.post("/api/demo/upload?nope=1", content=b"abc")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.rec.upload_calls, 0)


class Concurrency(Base):
    def test_a_slow_handler_does_not_block_the_server(self):
        with TestClient(self.app) as client:
            out = {}
            t = threading.Thread(target=lambda: out.update(slow=client.get("/api/demo/slow")))
            t.start()
            try:
                fast = client.get("/api/demo/fast")
                self.assertEqual(fast.status_code, 200)
                self.assertTrue(t.is_alive(), "the slow request was still waiting when the fast one finished")
            finally:
                self.rec.slow.set()
                t.join(timeout=10)
            self.assertEqual(out["slow"].status_code, 200)


class TokenMode(Base):
    token = TOKEN

    def test_every_route_needs_the_token(self):
        for method, path in (("get", "/api/commands"), ("get", "/api/capabilities"), ("get", "/api/demo/echo"),
                             ("post", "/api/demo/add"), ("post", "/api/demo/upload")):
            r = getattr(self.client, method)(path)
            self.assertEqual(r.status_code, 401, path)
            self.assertEqual(r.headers["www-authenticate"], "Bearer")
            self.assertEqual(r.json()["error"], "unauthorized")

    def test_a_wrong_token_is_401(self):
        r = self.client.get("/api/demo/fast", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 401)

    def test_the_right_token_works_and_the_caller_label_is_not_the_token(self):
        r = self.client.get("/api/demo/echo", params={"text": "x"}, headers=H)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rec.callers, ["token"])
        self.assertNotIn(TOKEN, r.text)

    def test_an_unauthorised_upload_is_refused_before_a_byte_is_read_or_the_handler_runs(self):
        r = self.client.post("/api/demo/upload", content=b"x" * 1000)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.rec.upload_calls, 0)

    def test_the_401_does_not_echo_the_token(self):
        r = self.client.get("/api/demo/fast", headers={"Authorization": "Bearer " + TOKEN + "x"})
        self.assertNotIn(TOKEN, r.text)

    def test_an_authorised_upload(self):
        r = self.client.post("/api/demo/upload", content=b"abc", headers=H)
        self.assertEqual((r.status_code, r.json()["data"]["caller"]), (200, "token"))


class OpenReadMode(Base):
    token = TOKEN
    access = "open-read"

    def test_reads_are_open_including_the_listing(self):
        for path in ("/api/commands", "/api/capabilities", "/api/demo/fast"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_a_read_by_an_unauthenticated_caller_is_labelled_anonymous(self):
        self.client.get("/api/demo/echo", params={"text": "x"})
        self.assertEqual(self.rec.callers, ["anonymous"])

    def test_every_write_needs_the_token(self):
        self.assertEqual(self.client.post("/api/demo/add", json={"a": 1, "b": 2}).status_code, 401)
        self.assertEqual(self.client.post("/api/demo/upload", content=b"abc").status_code, 401)
        self.assertEqual(self.rec.upload_calls, 0)
        self.assertEqual(self.client.post("/api/demo/add", json={"a": 1, "b": 2}, headers=H).status_code, 200)


class NoTokenMode(Base):
    def test_a_loopback_only_service_with_no_token_is_open_and_labels_the_caller_local(self):
        self.assertEqual(self.client.post("/api/demo/add", json={"a": 1, "b": 2}).status_code, 200)
        self.assertEqual(self.rec.callers, ["local"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
