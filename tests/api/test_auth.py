#!/usr/bin/env python3
"""Tests for api.auth: there is NO mode with an unauthenticated write.

token      every request needs the bearer token;
open-read  a read is open, a write needs the token;
no token   the service is loopback-only (core.config refuses otherwise): everything is allowed.
Run:  python3 tests/api/test_auth.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.api.auth import Auth  # noqa: E402

TOKEN = "s3cret-token"


def bearer(value):
    return {"authorization": value}


class TokenMode(unittest.TestCase):
    def setUp(self):
        self.a = Auth(TOKEN, "token")

    def test_the_right_token_is_allowed_for_read_and_write(self):
        for effect in ("read", "write", "stream"):
            self.assertEqual(self.a.authorise(bearer("Bearer " + TOKEN), effect), (True, "token"), effect)

    def test_no_header_is_refused_for_everything_including_reads(self):
        for effect in ("read", "write"):
            self.assertEqual(self.a.authorise({}, effect), (False, None), effect)

    def test_a_wrong_token_is_refused(self):
        for bad in ("Bearer nope", "Bearer " + TOKEN + "x", "Bearer " + TOKEN[:-1], "Bearer " + TOKEN.upper()):
            self.assertEqual(self.a.authorise(bearer(bad), "read"), (False, None), bad)

    def test_the_scheme_is_case_insensitive_the_token_is_not(self):
        self.assertTrue(self.a.authorise(bearer("bearer " + TOKEN), "write")[0])
        self.assertTrue(self.a.authorise(bearer("BEARER " + TOKEN), "write")[0])

    def test_malformed_headers_are_refused(self):
        for bad in ("Basic " + TOKEN, "Bearer", "Bearer ", "Bearer   ", TOKEN, "Token " + TOKEN, ""):
            self.assertEqual(self.a.authorise(bearer(bad), "read"), (False, None), repr(bad))

    def test_the_token_is_compared_with_a_constant_time_function(self):
        import inspect
        from nrf_workbench.api import auth
        self.assertIn("hmac.compare_digest", inspect.getsource(auth))


class OpenRead(unittest.TestCase):
    def setUp(self):
        self.a = Auth(TOKEN, "open-read")

    def test_a_read_is_open(self):
        self.assertEqual(self.a.authorise({}, "read"), (True, "anonymous"))

    def test_a_write_and_a_stream_need_the_token(self):
        for effect in ("write", "stream"):
            self.assertEqual(self.a.authorise({}, effect), (False, None), effect)
            self.assertEqual(self.a.authorise(bearer("Bearer wrong"), effect), (False, None), effect)
            self.assertEqual(self.a.authorise(bearer("Bearer " + TOKEN), effect), (True, "token"), effect)

    def test_a_valid_token_on_a_read_is_still_recognised_as_the_token(self):
        self.assertEqual(self.a.authorise(bearer("Bearer " + TOKEN), "read")[0], True)


class NoToken(unittest.TestCase):
    def test_everything_is_allowed_because_the_service_is_loopback_only(self):
        for access in ("token", "open-read"):
            a = Auth(None, access)
            for effect in ("read", "write"):
                self.assertEqual(a.authorise({}, effect), (True, "local"), (access, effect))

    def test_an_empty_token_is_no_token(self):
        self.assertIsNone(Auth("", "token").token)


class Construction(unittest.TestCase):
    def test_an_unknown_access_mode_is_refused(self):
        with self.assertRaises(ValueError):
            Auth(TOKEN, "everyone")


if __name__ == "__main__":
    unittest.main(verbosity=1)
