#!/usr/bin/env python3
"""Tests for the browser login: POST /login, POST /logout, GET /api/session, through the real composition root.

What matters: the token is entered once and never held by script (an HttpOnly, SameSite=Strict cookie, never in
a response body), a write by cookie needs the session's own CSRF value and this site as its origin, the cookie
works on the websocket and a foreign page cannot ride it, a login never inherits an old session, and a logout
without the CSRF header does not log anyone out.
Run:  python3 tests/dashboard/test_login.py
"""
import json
import os
import re
import sys
import tempfile
import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402

TOKEN = "s3cret-token-value-for-the-login-tests"


class Base(unittest.TestCase):
    token = TOKEN
    overrides = None

    def setUp(self):
        from fakes import Tree
        self.t = Tree()
        self.addCleanup(self.t.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        over = {"paths": {"firmware": os.path.join(self.tmp.name, "fw")},
                "streams": {"activity": {"retain": 50, "max_age_days": 1}, "events": {"retain": 50, "max_age_days": 1}}}
        for k, v in (self.overrides or {}).items():
            over.setdefault(k, {}).update(v)
        cfg, _ = cfgmod.load(env={}, overrides=over)
        self.app, self.host, _r, _c = service.build(cfg, token=self.token, sysfs=self.t.sysfs, hooks={"tool": "/fake"})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def login(self, token=TOKEN, **kw):
        return self.client.post("/login", content=json.dumps({"token": token}), **kw)

    def cookie_header(self, response):
        return response.headers.get("set-cookie", "")


class Login(Base):
    def test_before_login_the_session_says_login_is_required_and_gives_nothing(self):
        info = self.client.get("/api/session").json()
        self.assertEqual((info["login_required"], info["authenticated"], info["caller"], info["csrf"]),
                         (True, False, None, None))
        self.assertEqual(info["access"], "token")
        self.assertFalse(info["secure_transport"])

    def test_a_wrong_token_is_refused_with_no_cookie_and_no_hint(self):
        for bad in ("wrong", "", TOKEN + "x", TOKEN[:-1]):
            r = self.login(bad)
            self.assertEqual(r.status_code, 401, bad)
            self.assertNotIn("set-cookie", r.headers)
            self.assertEqual(r.json()["error"], "unauthorized")
        self.assertEqual(len(self.host.sessions), 0)

    def test_the_right_token_gives_a_session_cookie_script_cannot_read(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        cookie = self.cookie_header(r)
        self.assertTrue(cookie.startswith("nrfwb_session="), cookie)
        for attribute in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=%d" % service.SESSION_MAX_AGE_S):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Secure", cookie, "the service is plain HTTP here: a Secure cookie would never be sent back")
        self.assertNotIn(TOKEN, cookie)

    def test_the_body_has_the_csrf_value_but_never_the_session_id_or_the_token(self):
        r = self.login()
        body = r.json()
        sid = re.search(r"nrfwb_session=([^;]+)", self.cookie_header(r)).group(1)
        self.assertTrue(body["ok"])
        self.assertNotEqual(body["csrf"], sid)
        self.assertNotIn(sid, r.text)
        self.assertNotIn(TOKEN, r.text)

    def test_the_cookie_is_secure_when_the_site_is_served_over_https(self):
        client = TestClient(self.app, base_url="https://testserver")
        self.addCleanup(client.close)
        r = client.post("/login", content=json.dumps({"token": TOKEN}))
        self.assertIn("Secure", self.cookie_header(r))
        self.assertTrue(client.get("/api/session").json()["secure_transport"])

    def test_malformed_logins_are_a_400_or_401_and_never_a_session(self):
        for body, want in ((b"not json", 400), (b"[1]", 400), (b"", 400), (b'"x"', 400), (b"{}", 401),
                           (json.dumps({"token": 5}).encode(), 401), (json.dumps({"token": None}).encode(), 401),
                           (json.dumps({"token": ["x"]}).encode(), 401)):
            r = self.client.post("/login", content=body)
            self.assertEqual(r.status_code, want, body)
            self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(len(self.host.sessions), 0)

    def test_a_login_from_another_site_is_refused(self):
        r = self.login(headers={"origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.host.sessions), 0)
        self.assertEqual(self.login(headers={"origin": "http://testserver"}).status_code, 200)

    def test_a_second_login_never_inherits_the_old_session(self):
        first = self.login()
        old = re.search(r"nrfwb_session=([^;]+)", self.cookie_header(first)).group(1)
        self.login()                                   # the client sends the old cookie, so it is destroyed
        self.assertIsNone(self.host.sessions.get(old))
        self.assertEqual(len(self.host.sessions), 1)

    def test_login_does_not_touch_hardware_or_the_activity_log_as_a_command(self):
        self.login()
        self.assertEqual(self.host.streams.activity.since(0).events, [])


class AfterLogin(Base):
    def setUp(self):
        Base.setUp(self)
        r = self.login()
        self.csrf = r.json()["csrf"]

    def test_the_session_is_authenticated_and_returns_its_own_csrf_value(self):
        info = self.client.get("/api/session").json()
        self.assertEqual((info["authenticated"], info["caller"], info["csrf"]), (True, "dashboard", self.csrf))

    def test_reads_work_with_the_cookie_alone(self):
        for path in ("/api/commands", "/api/devices/list", "/api/streams", "/api/activity", "/api/dashboard/status"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_a_write_needs_the_csrf_header(self):
        body = json.dumps({"id": "deadbeef"})
        self.assertEqual(self.client.post("/api/firmware/delete", content=body).status_code, 401)
        r = self.client.post("/api/firmware/delete", content=body, headers={"x-csrf-token": "wrong"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/firmware/delete", content=body, headers={"x-csrf-token": self.csrf})
        self.assertEqual(r.status_code, 404, "authorised: the command itself answered (no such firmware)")
        self.assertEqual(r.json()["code"], "firmware_unknown")

    def test_another_sessions_csrf_value_does_not_work(self):
        other = TestClient(self.app)
        self.addCleanup(other.close)
        theirs = other.post("/login", content=json.dumps({"token": TOKEN})).json()["csrf"]
        r = self.client.post("/api/firmware/delete", content=json.dumps({"id": "deadbeef"}), headers={"x-csrf-token": theirs})
        self.assertEqual(r.status_code, 401)

    def test_a_foreign_origin_cannot_ride_the_cookie_for_a_read_or_a_write(self):
        evil = {"origin": "http://evil.example"}
        self.assertEqual(self.client.get("/api/devices/list", headers=evil).status_code, 401)
        r = self.client.post("/api/firmware/delete", content=json.dumps({"id": "deadbeef"}),
                             headers=dict(evil, **{"x-csrf-token": self.csrf}))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.get("/api/session", headers=evil).json()["csrf"], None,
                         "the CSRF value is not given to a foreign origin")

    def test_the_activity_log_says_the_caller_was_the_dashboard(self):
        self.client.get("/api/devices/list")
        entries = [e.payload for e in self.host.streams.activity.since(0).events]
        self.assertEqual([(e["command"], e["caller"]) for e in entries], [("devices.list", "dashboard")])

    def test_the_bearer_token_still_works_beside_the_cookie(self):
        bare = TestClient(self.app)
        self.addCleanup(bare.close)
        self.assertEqual(bare.get("/api/devices/list").status_code, 401)
        self.assertEqual(bare.get("/api/devices/list", headers={"Authorization": "Bearer " + TOKEN}).status_code, 200)

    def test_the_session_response_is_never_cached(self):
        self.assertEqual(self.client.get("/api/session").headers["cache-control"], "no-store")


class Websocket(Base):
    def setUp(self):
        Base.setUp(self)
        self.login()

    def test_the_cookie_works_on_the_websocket(self):
        with self.client.websocket_connect("/ws/events") as ws:
            ws.send_json({"want": {"devices": {"cursor": 0}}})
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_no_cookie_and_no_token_is_refused(self):
        bare = TestClient(self.app)
        self.addCleanup(bare.close)
        with self.assertRaises(WebSocketDisconnect):
            with bare.websocket_connect("/ws/events"):
                pass

    def test_a_foreign_page_cannot_use_the_visitors_cookie_on_the_websocket(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/events", headers={"origin": "http://evil.example"}):
                pass
        with self.client.websocket_connect("/ws/events", headers={"origin": "http://testserver"}) as ws:
            ws.send_json({"want": {"devices": {}}})
            self.assertEqual(ws.receive_json()["type"], "events")

    def test_a_logged_out_session_is_refused_on_the_websocket(self):
        csrf = self.client.get("/api/session").json()["csrf"]
        self.client.post("/logout", headers={"x-csrf-token": csrf})
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/events"):
                pass


class Logout(Base):
    def setUp(self):
        Base.setUp(self)
        self.csrf = self.login().json()["csrf"]

    def test_a_logout_without_the_csrf_header_does_not_log_anyone_out(self):
        r = self.client.post("/logout")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(self.client.get("/api/session").json()["authenticated"])
        r = self.client.post("/logout", headers={"x-csrf-token": "wrong"})
        self.assertEqual(r.status_code, 403)

    def test_a_logout_from_another_site_does_not_log_anyone_out(self):
        r = self.client.post("/logout", headers={"x-csrf-token": self.csrf, "origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(self.client.get("/api/session").json()["authenticated"])

    def test_a_logout_ends_the_session_and_clears_the_cookie(self):
        r = self.client.post("/logout", headers={"x-csrf-token": self.csrf})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Max-Age=0", r.headers["set-cookie"])
        self.assertEqual(len(self.host.sessions), 0)
        self.assertFalse(self.client.get("/api/session").json()["authenticated"])
        self.assertEqual(self.client.get("/api/devices/list").status_code, 401)

    def test_a_logout_with_no_session_is_harmless_and_clears_the_cookie(self):
        bare = TestClient(self.app)
        self.addCleanup(bare.close)
        r = bare.post("/logout")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Max-Age=0", r.headers["set-cookie"])


class Expiry(Base):
    def test_an_expired_session_is_refused(self):
        self.csrf = self.login().json()["csrf"]
        clock = [0.0]
        self.host.sessions._clock = lambda: clock[0]
        for s in self.host.sessions._sessions.values():
            s.created = 0.0
        self.assertEqual(self.client.get("/api/devices/list").status_code, 200)
        clock[0] = service.SESSION_MAX_AGE_S + 1
        self.assertEqual(self.client.get("/api/devices/list").status_code, 401)
        self.assertFalse(self.client.get("/api/session").json()["authenticated"])

    def test_the_limits_are_the_derived_ones(self):
        self.assertEqual(service.SESSION_MAX_AGE_S, 12 * 3600)
        self.assertEqual(service.MAX_SESSIONS, 16)
        self.assertEqual((self.host.sessions.max_sessions, self.host.sessions.max_age_s), (16, 12 * 3600))


class NoToken(Base):
    token = None

    def test_a_loopback_only_service_needs_no_login(self):
        info = self.client.get("/api/session").json()
        self.assertEqual((info["login_required"], info["authenticated"], info["caller"]), (False, True, "local"))
        r = self.client.post("/login", content=json.dumps({"token": "anything"}))
        self.assertEqual((r.status_code, r.json()["login_required"]), (200, False))
        self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(len(self.host.sessions), 0)

    def test_a_foreign_origin_login_is_still_refused(self):
        self.assertEqual(self.login(headers={"origin": "http://evil.example"}).status_code, 403)


class OpenRead(Base):
    overrides = {"service": {"access": "open-read"}}

    def test_reads_are_open_and_the_session_says_so_but_a_write_still_needs_a_login(self):
        info = self.client.get("/api/session").json()
        self.assertEqual((info["authenticated"], info["caller"], info["access"]), (True, "anonymous", "open-read"))
        self.assertEqual(self.client.get("/api/devices/list").status_code, 200)
        self.assertEqual(self.client.post("/api/firmware/delete", content=json.dumps({"id": "deadbeef"})).status_code, 401)
        csrf = self.login().json()["csrf"]
        r = self.client.post("/api/firmware/delete", content=json.dumps({"id": "deadbeef"}), headers={"x-csrf-token": csrf})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=1)
