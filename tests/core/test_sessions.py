#!/usr/bin/env python3
"""Tests for core.sessions: the store behind the browser login cookie.

What matters: it refuses to exist without both bounds, an id is unguessable and independent of its CSRF value,
an expired or evicted session is gone, the oldest goes first when full, and it holds under threads.
Run:  python3 tests/core/test_sessions.py
"""
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core.events import BoundError  # noqa: E402
from nrf_workbench.core.sessions import SessionStore, same_origin  # noqa: E402


class Clock(object):
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def store(max_sessions=5, max_age_s=100):
    clock = Clock()
    return SessionStore(max_sessions, max_age_s, clock), clock


class Bounds(unittest.TestCase):
    def test_both_bounds_are_required_and_have_no_default(self):
        with self.assertRaises(TypeError):
            SessionStore()
        with self.assertRaises(TypeError):
            SessionStore(5)
        for bad in (None, 0, -1, True, 1.5, "5"):
            with self.assertRaises(BoundError, msg=repr(bad)):
                SessionStore(bad, 10)
        for bad in (None, 0, -1, True, "1", float("inf"), float("nan")):
            with self.assertRaises(BoundError, msg=repr(bad)):
                SessionStore(5, bad)


class Creating(unittest.TestCase):
    def test_an_id_and_a_csrf_value_are_long_distinct_and_independent(self):
        s, _ = store(max_sessions=1000)
        made = [s.create() for _ in range(200)]
        self.assertEqual(len(set(m.sid for m in made)), 200)
        self.assertEqual(len(set(m.csrf for m in made)), 200)
        for m in made:
            self.assertGreaterEqual(len(m.sid), 43)                  # 32 random bytes, url-safe
            self.assertGreaterEqual(len(m.csrf), 43)
            self.assertNotEqual(m.sid, m.csrf, "a page that learns the CSRF value must learn nothing of the id")
            self.assertNotIn(m.csrf, m.sid)

    def test_get_returns_the_live_session_and_nothing_for_anything_else(self):
        s, _ = store()
        m = s.create()
        self.assertIs(s.get(m.sid), m)
        for bad in (None, "", "nope", 5, b"x", m.csrf, m.sid + "x", m.sid[:-1]):
            self.assertIsNone(s.get(bad), repr(bad))

    def test_destroy_removes_it_and_is_true_once(self):
        s, _ = store()
        m = s.create()
        self.assertTrue(s.destroy(m.sid))
        self.assertFalse(s.destroy(m.sid))
        self.assertIsNone(s.get(m.sid))
        self.assertEqual(len(s), 0)


class Expiry(unittest.TestCase):
    def test_a_session_expires_after_its_maximum_age_and_not_before(self):
        s, clock = store(max_age_s=100)
        m = s.create()
        clock.t = 100
        self.assertIs(s.get(m.sid), m, "exactly the maximum age is still live")
        clock.t = 100.001
        self.assertIsNone(s.get(m.sid))
        self.assertEqual(len(s), 0, "an expired session is removed when it is looked at")

    def test_expiry_is_measured_from_creation(self):
        s, clock = store(max_age_s=100)
        m = s.create()
        for t in (30, 60, 90):
            clock.t = t
            self.assertIsNotNone(s.get(m.sid), "looking at a session does not extend it")
        clock.t = 101
        self.assertIsNone(s.get(m.sid))

    def test_expired_sessions_are_dropped_before_a_live_one_is_evicted(self):
        s, clock = store(max_sessions=3, max_age_s=100)
        old = [s.create() for _ in range(3)]
        clock.t = 150
        live = s.create()
        self.assertEqual(len(s), 1)
        self.assertIsNotNone(s.get(live.sid))
        for m in old:
            self.assertIsNone(s.get(m.sid))


class Eviction(unittest.TestCase):
    def test_beyond_the_count_the_oldest_goes_first(self):
        s, clock = store(max_sessions=3)
        made = []
        for i in range(5):
            clock.t = i
            made.append(s.create())
        self.assertEqual(len(s), 3)
        self.assertEqual([bool(s.get(m.sid)) for m in made], [False, False, True, True, True])

    def test_the_newest_is_never_the_one_evicted(self):
        s, clock = store(max_sessions=1)
        a = s.create()
        clock.t = 1
        b = s.create()
        self.assertIsNone(s.get(a.sid))
        self.assertIs(s.get(b.sid), b)
        self.assertEqual(len(s), 1)

    def test_the_store_never_holds_more_than_its_bound_under_threads(self):
        s, _ = SessionStore(16, 3600), None
        made, errors = [], []

        def worker():
            try:
                for _ in range(50):
                    made.append(s.create().sid)
                    self.assertLessEqual(len(s), 16)
            except Exception as exc:                  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(made)), 400)
        self.assertEqual(len(s), 16)


class SameOrigin(unittest.TestCase):
    def test_the_rule(self):
        for origin, host, ok in ((None, "h", True), ("http://h", "h", True), ("http://H:8080", "h:8080", True),
                                 ("http://h:81", "h:80", False), ("http://other", "h", False), ("", "h", False),
                                 ("http://h", None, False), ("null", "h", False), ("http://h.evil.example", "h", False),
                                 ("http://evil.example/h", "h", False)):
            headers = {}
            if origin is not None:
                headers["origin"] = origin
            if host is not None:
                headers["host"] = host
            self.assertEqual(same_origin(headers), ok, (origin, host))


if __name__ == "__main__":
    unittest.main(verbosity=1)
