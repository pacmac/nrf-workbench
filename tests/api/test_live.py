#!/usr/bin/env python3
"""A real uvicorn server on loopback and a real socket: the two things the ASGI test client cannot show.

  1. an upload is STREAMED: the handler is already reading while the client has sent only part of the
     body, so a large file is never held in memory;
  2. a client that vanishes mid-upload leaves NOTHING in the firmware store (no entry, no temporary).

The waits below are failure-case ceilings only (a test that is not going to pass must end): each is
released the instant its event fires, so a passing run never waits on the clock.
Run:  python3 tests/api/test_live.py
"""
import json
import os
import socket
import sys
import threading
import unittest

import uvicorn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from nrf_workbench.api.app import build_app  # noqa: E402
from nrf_workbench.api.auth import Auth  # noqa: E402
from nrf_workbench.core.registry import Command, Registry  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from test_commands import ALL, Base as CommandsBase  # noqa: E402

CEILING_S = 10          # only ever reached by a failing test


class Live(object):
    """uvicorn in a thread on a free loopback port."""

    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        started = threading.Event()

        def watch():
            while not self.server.started:
                if not self.thread.is_alive():
                    break
                threading.Event().wait(0.005)
            started.set()

        threading.Thread(target=watch, daemon=True).start()
        started.wait(CEILING_S)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(CEILING_S)

    def connect(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=CEILING_S)
        return s


def request_head(path, length):
    return ("POST %s HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/octet-stream\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n" % (path, length)).encode()


def read_response(sock):
    raw = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        raw += chunk
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), (json.loads(body) if body else None)


class Streaming(unittest.TestCase):
    def test_the_handler_is_reading_while_the_client_has_sent_only_part_of_the_body(self):
        first_read = threading.Event()
        seen = {}

        def upload(ctx):
            first = ctx.body.read(100)
            seen["first"] = len(first)
            first_read.set()                       # we are in the handler, mid-upload
            total = len(first)
            while True:
                chunk = ctx.body.read(8192)
                if not chunk:
                    break
                total += len(chunk)
            return Result.success({"bytes": total, "length": ctx.body.length})

        reg = Registry()
        reg.add(Command("demo.upload", "upload", upload, effect="write", body="raw"), module="test")
        app = build_app(reg, None, Auth(None, "token"), set())
        size = 3 * 1024 * 1024
        with Live(app) as live:
            s = live.connect()
            s.sendall(request_head("/api/demo/upload", size))
            s.sendall(b"x" * 4096)                  # a tiny part of the declared 3 MiB
            self.assertTrue(first_read.wait(CEILING_S),
                            "the handler did not start reading until the whole body had arrived: it is buffered")
            self.assertEqual(seen["first"], 100)
            sent = 4096
            while sent < size:
                piece = min(65536, size - sent)
                s.sendall(b"y" * piece)
                sent += piece
            status, body = read_response(s)
            s.close()
        self.assertEqual(status, 200)
        self.assertEqual((body["data"]["bytes"], body["data"]["length"]), (size, size))


class Dropped(CommandsBase):
    def setUp(self):
        CommandsBase.setUp(self)
        self.app = build_app(self.reg, self.host, Auth(None, "token"), ALL)
        from nrf_workbench.programmer import commands
        self.store = commands._store(self.host)                                # the one the handlers use
        self.done = threading.Event()
        self.outcome = {}
        real = self.store.put

        def watched(*a, **kw):
            result = real(*a, **kw)
            self.outcome["result"] = result
            self.done.set()
            return result

        self.store.put = watched

    def store_files(self):
        return sorted(os.listdir(self.store.root))

    def drop(self, sent):
        with Live(self.app) as live:
            s = live.connect()
            s.sendall(request_head("/api/firmware/put?name=dropped", len(self.data)))
            if sent:
                s.sendall(self.data[:sent])
            s.close()                               # the client vanishes mid-body
            self.assertTrue(self.done.wait(CEILING_S), "the store never finished handling the dropped upload")
        return self.outcome["result"]

    def test_a_client_that_vanishes_mid_upload_leaves_nothing_in_the_store(self):
        result = self.drop(sent=len(self.data) // 2)
        self.assertEqual((result.cls, result.code), ("refused", "bad_package"), result.to_dict())
        self.assertEqual(self.store_files(), [], "a dropped upload left something behind")

    def test_a_client_that_vanishes_before_sending_any_body(self):
        result = self.drop(sent=0)
        self.assertEqual((result.cls, result.code), ("refused", "bad_package"), result.to_dict())
        self.assertEqual(self.store_files(), [])

    def test_the_same_server_then_accepts_a_complete_upload(self):
        self.drop(sent=len(self.data) // 3)
        self.done.clear()
        with Live(self.app) as live:
            s = live.connect()
            s.sendall(request_head("/api/firmware/put?name=whole", len(self.data)) + self.data)
            status, body = read_response(s)
            s.close()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["data"]["sha256"], self.id)
        self.assertEqual(len(self.store_files()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
