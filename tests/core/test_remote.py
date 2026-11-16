#!/usr/bin/env python3
"""Tests for core.remote: how a registry row becomes an HTTP request, and a response a Result.

A stub server records exactly what goes over the wire (method, path and query, headers, body), so the
tests state what the client SENDS. What matters: typed arguments reach the service as the right text or
JSON, an upload is streamed with its length declared, a Result of ANY class is returned as a Result
(a refusal is not an error), there is no default timeout, an early refusal during an upload is read and
not mistaken for a network failure, and the token travels only in the Authorization header.
Run:  python3 tests/core/test_remote.py
"""
import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core import remote  # noqa: E402
from nrf_workbench.core.remote import Remote, RemoteError, file_sha256  # noqa: E402

OK_BODY = {"ok": True, "class": "ok", "code": "ok", "message": "fine", "data": {"x": 1}, "evidence": {"e": [1, 2]}}


class Stub(object):
    """A loopback HTTP server that records requests and answers from `self.answer(method, path)`."""

    def __init__(self):
        self.requests, self.answer = [], lambda method, path: (200, OK_BODY)
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle_one(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                stub.requests.append({"method": method, "path": self.path, "headers": dict(self.headers), "body": body})
                code, payload = stub.answer(method, self.path.split("?")[0])
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self.handle_one("GET")

            def do_POST(self):
                self.handle_one("POST")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.handle_error = lambda *a: None        # a client that hangs up is part of what is tested
        # shutdown() waits for the poll interval (0.5 s by default), which made every test pay for it
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


READ = {"name": "devices.list", "effect": "read", "body": None, "path": "/api/devices/list", "args": []}
WRITE = {"name": "programmer.flash", "effect": "write", "body": None, "path": "/api/programmer/flash", "args": []}
RAW = {"name": "firmware.put", "effect": "write", "body": "raw", "path": "/api/firmware/put", "args": []}


class Construction(unittest.TestCase):
    def test_the_address_must_be_http_or_https_with_a_host(self):
        for bad in ("", None, "localhost:8080", "ftp://x", "http://", "//x", "8080"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                Remote(bad)
        Remote("http://h:1")
        Remote("https://h")

    def test_there_is_no_default_timeout(self):
        self.assertIsNone(Remote("http://h:1").timeout)

    def test_the_timeout_reaches_the_connection_only_when_the_caller_gave_one(self):
        for given, want in ((None, None), (7.5, 7.5)):
            with mock.patch.object(remote.http.client, "HTTPConnection") as conn:
                conn.return_value.getresponse.return_value.read.return_value = b"{}"
                conn.return_value.getresponse.return_value.status = 200
                try:
                    Remote("http://h:1", timeout=given).commands()
                except RemoteError:
                    pass
                self.assertEqual(conn.call_args.kwargs["timeout"], want)

    def test_https_uses_the_tls_connection_and_its_port(self):
        with mock.patch.object(remote.http.client, "HTTPSConnection") as conn:
            conn.return_value.getresponse.return_value.read.return_value = b"{}"
            conn.return_value.getresponse.return_value.status = 200
            try:
                Remote("https://example.invalid").commands()
            except RemoteError:
                pass
            self.assertEqual(conn.call_args.args[:2], ("example.invalid", 443))


class Requests(unittest.TestCase):
    def test_the_list_calls_carry_the_token_in_the_header_only(self):
        with Stub() as s:
            s.answer = lambda m, p: (200, {"commands": [READ]})
            self.assertEqual(Remote(s.url, "s3cret").commands(), [READ])
            self.assertEqual(Remote(s.url).commands(), [READ])
        with_token, without = s.requests
        self.assertEqual(with_token["headers"]["Authorization"], "Bearer s3cret")
        self.assertNotIn("Authorization", without["headers"])
        for r in s.requests:
            self.assertNotIn("s3cret", r["path"])
            self.assertEqual((r["method"], r["path"]), ("GET", "/api/commands"))

    def test_a_read_sends_typed_arguments_as_query_text(self):
        with Stub() as s:
            Remote(s.url).call(READ, {"serial": "A B&C", "n": 7, "bound_s": 5.0, "dry": True, "off": False, "none": None})
        q = dict(pair.split("=") for pair in s.requests[0]["path"].split("?")[1].split("&"))
        self.assertEqual(q, {"serial": "A+B%26C", "n": "7", "bound_s": "5.0", "dry": "true", "off": "false"})
        self.assertEqual((s.requests[0]["method"], s.requests[0]["body"]), ("GET", b""))

    def test_a_json_write_sends_the_values_with_their_types(self):
        with Stub() as s:
            Remote(s.url, "t").call(WRITE, {"serial": "S", "enter_bound_s": 5, "flash_bound_s": 12.5, "dry": False, "x": None})
        r = s.requests[0]
        self.assertEqual((r["method"], r["path"].split("?")[0], r["headers"]["Content-Type"]), ("POST", "/api/programmer/flash", "application/json"))
        self.assertEqual(json.loads(r["body"]), {"serial": "S", "enter_bound_s": 5, "flash_bound_s": 12.5, "dry": False})

    def test_an_upload_is_streamed_with_its_length_and_the_arguments_in_the_query(self):
        data = os.urandom(remote.CHUNK * 3 + 17)                    # several chunks
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.zip")
            with open(path, "wb") as fh:
                fh.write(data)
            with Stub() as s:
                Remote(s.url, "t").call(RAW, {"name": "parking", "sha256": hashlib.sha256(data).hexdigest()}, path)
        r = s.requests[0]
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["headers"]["Content-Length"], str(len(data)))
        self.assertEqual(r["headers"]["Content-Type"], "application/octet-stream")
        self.assertEqual(r["body"], data)
        self.assertIn("name=parking", r["path"])
        self.assertIn("sha256=", r["path"])

    def test_an_upload_with_no_file_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            Remote("http://h:1").call(RAW, {})

    def test_a_path_prefix_is_kept(self):
        with Stub() as s:
            Remote(s.url + "/bench/").call(READ, {})
        self.assertEqual(s.requests[0]["path"], "/bench/api/devices/list")


class Results(unittest.TestCase):
    def test_a_result_of_every_class_comes_back_as_a_result_not_an_error(self):
        for code, body in ((200, OK_BODY),
                           (409, {"ok": False, "class": "refused", "code": "lock_held", "message": "m", "data": {}, "evidence": {"holder": "x"}}),
                           (422, {"ok": False, "class": "refused", "code": "bad_package", "message": "m", "data": {}, "evidence": {}}),
                           (411, {"ok": False, "class": "refused", "code": "length_required", "message": "m", "data": {}, "evidence": {}}),
                           (500, {"ok": False, "class": "failed", "code": "flash_failed", "message": "m", "data": {}, "evidence": {"output": "whole"}}),
                           (500, {"ok": False, "class": "suspect", "code": "flash_suspect", "message": "m", "data": {}, "evidence": {}})):
            with Stub() as s:
                s.answer = lambda m, p, code=code, body=body: (code, body)
                r = Remote(s.url).call(READ, {})
            self.assertEqual((r.cls, r.code), (body["class"], body["code"]), code)
            self.assertEqual((r.message, r.data, r.evidence), (body["message"], body["data"], body["evidence"]))

    def test_a_result_of_an_unknown_class_is_a_protocol_error(self):
        with Stub() as s:
            s.answer = lambda m, p: (200, {"class": "great", "code": "ok"})
            with self.assertRaises(RemoteError) as caught:
                Remote(s.url).call(READ, {})
        self.assertEqual(caught.exception.kind, "protocol")

    def test_each_failure_to_get_a_result_has_its_own_kind(self):
        cases = ((401, {"ok": False, "error": "unauthorized", "message": "need a token"}, "unauthorized"),
                 (400, {"ok": False, "error": "usage", "message": "bad n", "arg": "n"}, "usage"),
                 (404, {"ok": False, "error": "http", "message": "Not Found"}, "not_found"),
                 (500, {"ok": False, "error": "internal", "message": "boom"}, "internal"),
                 (502, {"nope": 1}, "internal"),
                 (418, {"nope": 1}, "protocol"))
        for code, body, kind in cases:
            with Stub() as s:
                s.answer = lambda m, p, code=code, body=body: (code, body)
                with self.assertRaises(RemoteError, msg=code) as caught:
                    Remote(s.url).call(READ, {})
            self.assertEqual((caught.exception.kind, caught.exception.status), (kind, code))
        self.assertEqual(caught.exception.status, 418)

    def test_a_usage_error_carries_the_argument(self):
        with Stub() as s:
            s.answer = lambda m, p: (400, {"ok": False, "error": "usage", "message": "bad n", "arg": "n"})
            with self.assertRaises(RemoteError) as caught:
                Remote(s.url).call(READ, {})
        self.assertEqual((caught.exception.arg, str(caught.exception)), ("n", "bad n"))

    def test_an_answer_that_is_not_json_is_a_protocol_error(self):
        with Stub() as s:
            s.answer = lambda m, p: (200, b"<html>nope</html>")
            with self.assertRaises(RemoteError) as caught:
                Remote(s.url).call(READ, {})
        self.assertEqual(caught.exception.kind, "protocol")

    def test_the_list_calls_raise_the_right_error_not_a_key_error(self):
        with Stub() as s:
            s.answer = lambda m, p: (401, {"ok": False, "error": "unauthorized", "message": "no"})
            for call in (Remote(s.url).commands, Remote(s.url).capabilities):
                with self.assertRaises(RemoteError) as caught:
                    call()
                self.assertEqual(caught.exception.kind, "unauthorized")
            s.answer = lambda m, p: (200, {"unexpected": True})
            with self.assertRaises(RemoteError) as caught:
                Remote(s.url).commands()
            self.assertEqual(caught.exception.kind, "protocol")

    def test_capabilities(self):
        with Stub() as s:
            s.answer = lambda m, p: (200, {"present": ["sysfs"], "absent": []})
            self.assertEqual(Remote(s.url).capabilities(), {"present": ["sysfs"], "absent": []})


class Unreachable(unittest.TestCase):
    def test_a_closed_port_is_unreachable_with_the_address_in_the_message(self):
        with Stub() as s:
            url = s.url
        with self.assertRaises(RemoteError) as caught:
            Remote(url).call(READ, {})
        self.assertEqual(caught.exception.kind, "unreachable")
        self.assertIn("127.0.0.1", str(caught.exception))

    def test_a_server_that_hangs_up_mid_answer_is_unreachable(self):
        with Stub() as s:
            class Hangup(Exception):
                pass
            s.answer = lambda m, p: (200, OK_BODY)
            with mock.patch.object(remote.http.client.HTTPConnection, "getresponse",
                                   side_effect=remote.http.client.RemoteDisconnected("closed")):
                with self.assertRaises(RemoteError) as caught:
                    Remote(s.url).call(READ, {})
        self.assertEqual(caught.exception.kind, "unreachable")


class FakeConnection(object):
    """A connection whose send fails part way, as when the service answers (refuses) before the end."""
    answer_status, answer_body, fail_after = 401, b'{"ok": false, "error": "unauthorized", "message": "need a token"}', 1

    def __init__(self, *a, **kw):
        self.sent = 0

    def putrequest(self, *a):
        pass

    def putheader(self, *a):
        pass

    def endheaders(self):
        pass

    def send(self, chunk):
        self.sent += 1
        if self.sent > self.fail_after:
            raise BrokenPipeError("the service closed the connection")

    def getresponse(self):
        class R:
            status = FakeConnection.answer_status

            def read(self_inner):
                return FakeConnection.answer_body
        return R()

    def close(self):
        pass


class EarlyAnswer(unittest.TestCase):
    def upload(self, size):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "big.zip")
            with open(path, "wb") as fh:
                fh.write(b"x" * size)
            with mock.patch.object(remote.http.client, "HTTPConnection", FakeConnection):
                return Remote("http://h:1").call(RAW, {}, path)

    def test_an_answer_that_arrives_before_the_body_is_finished_is_read_not_a_network_error(self):
        with self.assertRaises(RemoteError) as caught:
            self.upload(remote.CHUNK * 4)
        self.assertEqual(caught.exception.kind, "unauthorized", "the refusal must not be reported as unreachable")

    def test_an_early_refusal_that_is_a_result_is_returned_as_one(self):
        FakeConnection.answer_status = 411
        FakeConnection.answer_body = json.dumps({"ok": False, "class": "refused", "code": "length_required",
                                                 "message": "m", "data": {}, "evidence": {}}).encode()
        try:
            r = self.upload(remote.CHUNK * 4)
        finally:
            FakeConnection.answer_status = 401
            FakeConnection.answer_body = b'{"ok": false, "error": "unauthorized", "message": "need a token"}'
        self.assertEqual((r.cls, r.code), ("refused", "length_required"))


class Files(unittest.TestCase):
    def test_a_missing_file_is_a_local_error_raised_as_itself_not_a_network_error(self):
        with self.assertRaises(FileNotFoundError):
            Remote("http://127.0.0.1:1").call(RAW, {}, "/does/not/exist.zip")

    def test_a_read_error_part_way_is_a_file_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f.zip")
            with open(path, "wb") as fh:
                fh.write(b"x" * 100)
            real_open = open

            class Broken(object):
                def __init__(self, fh):
                    self.fh = fh

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    self.fh.close()

                def read(self, n):
                    raise OSError("disk went away")

            with mock.patch.object(remote.http.client, "HTTPConnection", FakeConnection), \
                    mock.patch("builtins.open", lambda p, m="r", *a, **k: Broken(real_open(p, m)) if p == path else real_open(p, m, *a, **k)):
                with self.assertRaises(RemoteError) as caught:
                    Remote("http://h:1").call(RAW, {}, path)
        self.assertEqual(caught.exception.kind, "file")

    def test_the_checksum_helper_reads_in_chunks_and_matches(self):
        data = os.urandom(remote.CHUNK * 2 + 5)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "f")
            with open(path, "wb") as fh:
                fh.write(data)
            self.assertEqual(file_sha256(path), hashlib.sha256(data).hexdigest())
            with open(path, "wb"):
                pass
            self.assertEqual(file_sha256(path), hashlib.sha256(b"").hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=1)
