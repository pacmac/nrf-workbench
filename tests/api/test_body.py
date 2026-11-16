#!/usr/bin/env python3
"""Tests for api.body: a synchronous read(n) over an asynchronous request body.

The ASGI test client cannot simulate a client that vanishes mid-upload, so the body is tested directly
over a fake request. What matters: reads are lazy (a large upload is never held in memory), exact sizes
come back, the end is b"" repeatedly, and a vanished client is a ConnectionResetError (an OSError, which
the firmware store already treats as a failed body).
Run:  python3 tests/api/test_body.py
"""
import os
import sys
import unittest
from functools import partial

import anyio
from starlette.requests import ClientDisconnect

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.api.body import RequestBody  # noqa: E402


class FakeRequest(object):
    def __init__(self, chunks, then=None):
        self.chunks, self.then, self.pulled = list(chunks), then, 0

    async def stream(self):
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk
        if self.then:
            raise self.then
        yield b""


def run(request, reads, length=None):
    """Run the reads in a worker thread, as a handler does, and return what each returned."""
    body = RequestBody(request, length)
    out = []

    async def main():
        for n in reads:
            out.append(await anyio.to_thread.run_sync(partial(body.read, n)))

    anyio.run(main)
    return body, out


class Reading(unittest.TestCase):
    def test_exact_sizes_across_chunk_boundaries(self):
        _, out = run(FakeRequest([b"abc", b"defgh", b"ij"]), [2, 4, 4])
        self.assertEqual(out, [b"ab", b"cdef", b"ghij"])

    def test_the_end_is_empty_and_stays_empty(self):
        _, out = run(FakeRequest([b"abc"]), [3, 5, 5, 5])
        self.assertEqual(out, [b"abc", b"", b"", b""])

    def test_a_short_final_read_returns_what_is_left(self):
        _, out = run(FakeRequest([b"abcdef"]), [4, 4])
        self.assertEqual(out, [b"abcd", b"ef"])

    def test_read_all(self):
        _, out = run(FakeRequest([b"ab", b"cd", b"ef"]), [-1])
        self.assertEqual(out, [b"abcdef"])
        _, out = run(FakeRequest([b"ab", b"cd"]), [None])
        self.assertEqual(out, [b"abcd"])

    def test_the_declared_length_is_carried(self):
        body, _ = run(FakeRequest([b"abc"]), [1], length=3)
        self.assertEqual(body.length, 3)
        body, _ = run(FakeRequest([b"abc"]), [1], length=None)
        self.assertIsNone(body.length)

    def test_reading_is_lazy_so_a_large_upload_is_not_held_in_memory(self):
        req = FakeRequest([b"x" * 1000] * 50)
        _, out = run(req, [10])
        self.assertEqual(len(out[0]), 10)
        self.assertEqual(req.pulled, 1, "only the first network chunk may be pulled for a 10 byte read")

    def test_empty_chunks_are_skipped(self):
        _, out = run(FakeRequest([b"", b"ab", b"", b"cd"]), [4])
        self.assertEqual(out, [b"abcd"])


class Disconnect(unittest.TestCase):
    def test_a_vanished_client_is_a_connection_reset_which_is_an_oserror(self):
        with self.assertRaises(ConnectionResetError) as caught:
            run(FakeRequest([b"abc"], then=ClientDisconnect()), [3, 3])
        self.assertIsInstance(caught.exception, OSError)

    def test_bytes_already_received_are_still_returned_before_the_reset(self):
        body = RequestBody(FakeRequest([b"abcdef"], then=ClientDisconnect()), None)
        out = []

        async def main():
            out.append(await anyio.to_thread.run_sync(partial(body.read, 4)))
            try:
                await anyio.to_thread.run_sync(partial(body.read, 4))
            except ConnectionResetError as exc:
                out.append(type(exc).__name__)

        anyio.run(main)
        self.assertEqual(out, [b"abcd", "ConnectionResetError"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
