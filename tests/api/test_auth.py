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


class CookieSessions(unittest.TestCase):
    """A browser holds a session cookie, not the token. The cookie alone is not enough to write."""

    def setUp(self):
        from nrf_workbench.core.sessions import SessionStore
        self.store = SessionStore(8, 3600)
        self.a = Auth(TOKEN, "token", self.store)
        self.s = self.store.create()

    def cookie(self, sid=None, **extra):
        h = {"cookie": "nrfwb_session=%s" % (sid or self.s.sid), "host": "bench"}
        h.update(extra)
        return h

    def test_a_valid_session_may_read(self):
        self.assertEqual(self.a.authorise(self.cookie(), "read"), (True, "dashboard"))

    def test_a_write_needs_the_sessions_own_csrf_value(self):
        self.assertEqual(self.a.authorise(self.cookie(), "write"), (False, None), "no CSRF header")
        self.assertEqual(self.a.authorise(self.cookie(**{"x-csrf-token": "wrong"}), "write"), (False, None))
        self.assertEqual(self.a.authorise(self.cookie(**{"x-csrf-token": self.s.sid}), "write"), (False, None),
                         "the session id is not the CSRF value")
        self.assertEqual(self.a.authorise(self.cookie(**{"x-csrf-token": self.s.csrf}), "write"), (True, "dashboard"))

    def test_another_sessions_csrf_value_does_not_work(self):
        other = self.store.create()
        self.assertEqual(self.a.authorise(self.cookie(**{"x-csrf-token": other.csrf}), "write"), (False, None))

    def test_a_stream_is_not_a_read(self):
        self.assertEqual(self.a.authorise(self.cookie(), "stream"), (False, None))
        self.assertEqual(self.a.authorise(self.cookie(**{"x-csrf-token": self.s.csrf}), "stream"), (True, "dashboard"))

    def test_an_origin_that_is_not_this_host_is_refused_even_with_a_valid_session(self):
        for origin in ("http://evil.example", "null", "http://bench.evil.example"):
            self.assertEqual(self.a.authorise(self.cookie(origin=origin), "read"), (False, None), origin)
            self.assertEqual(self.a.authorise(self.cookie(origin=origin, **{"x-csrf-token": self.s.csrf}), "write"),
                             (False, None), origin)
        self.assertEqual(self.a.authorise(self.cookie(origin="http://bench"), "read"), (True, "dashboard"))

    def test_an_unknown_expired_or_destroyed_session_is_refused(self):
        self.assertEqual(self.a.authorise(self.cookie(sid="not-a-session"), "read"), (False, None))
        self.store.destroy(self.s.sid)
        self.assertEqual(self.a.authorise(self.cookie(), "read"), (False, None))

    def test_the_cookie_name_is_exact(self):
        self.assertEqual(self.a.authorise({"cookie": "x_nrfwb_session=%s" % self.s.sid, "host": "bench"}, "read"), (False, None))
        self.assertEqual(self.a.authorise({"cookie": "nrfwb_session2=%s" % self.s.sid, "host": "bench"}, "read"), (False, None))

    def test_cookies_are_parsed_among_others(self):
        from nrf_workbench.api.auth import cookie_value
        self.assertEqual(cookie_value({"cookie": "a=1; nrfwb_session=XYZ ; b=2"}), "XYZ")
        self.assertEqual(cookie_value({"cookie": "nrfwb_session=A=B"}), "A=B")
        for h in ({}, {"cookie": ""}, {"cookie": "a=1"}, {"cookie": "nrfwb_session"}):
            self.assertIsNone(cookie_value(h), h)

    def test_without_a_session_store_a_cookie_means_nothing(self):
        self.assertEqual(Auth(TOKEN, "token").authorise(self.cookie(), "read"), (False, None))

    def test_the_bearer_token_still_works_and_needs_no_csrf(self):
        self.assertEqual(self.a.authorise({"authorization": "Bearer " + TOKEN}, "write"), (True, "token"))

    def test_a_wrong_bearer_does_not_block_a_valid_session(self):
        self.assertEqual(self.a.authorise(self.cookie(authorization="Bearer wrong"), "read"), (True, "dashboard"))

    def test_with_no_token_everything_is_local_and_a_cookie_is_irrelevant(self):
        self.assertEqual(Auth(None, "token", self.store).authorise({}, "write"), (True, "local"))

    def test_in_open_read_a_read_is_open_but_a_write_still_needs_the_session_and_csrf(self):
        a = Auth(TOKEN, "open-read", self.store)
        self.assertEqual(a.authorise({}, "read"), (True, "anonymous"))
        self.assertEqual(a.authorise({}, "write"), (False, None))
        self.assertEqual(a.authorise(self.cookie(), "write"), (False, None))
        self.assertEqual(a.authorise(self.cookie(**{"x-csrf-token": self.s.csrf}), "write"), (True, "dashboard"))

    def test_token_matches(self):
        self.assertTrue(self.a.token_matches(TOKEN))
        for bad in ("", "x", TOKEN + "x", TOKEN[:-1], None, 5, b"x", TOKEN.upper()):
            self.assertFalse(self.a.token_matches(bad), repr(bad))
        self.assertFalse(Auth(None, "token").token_matches("anything"))

    def test_there_is_one_origin_rule_shared_with_the_login(self):
        from nrf_workbench.api import auth
        from nrf_workbench.core import sessions
        self.assertIs(auth.origin_allowed, sessions.same_origin)


class Construction(unittest.TestCase):
    def test_an_unknown_access_mode_is_refused(self):
        with self.assertRaises(ValueError):
            Auth(TOKEN, "everyone")


if __name__ == "__main__":
    unittest.main(verbosity=1)
