#!/usr/bin/env python3
"""Tests for dashboard.server: the page at /, its assets, and its own status.

What matters: the bench name is in the page BEFORE any script and is escaped; the page says plainly when the UI
has not been built; an asset path can never leave its folder; the page is public while the API stays behind
its token; and the status route's claim that the dashboard has no write route is checked against the routes
themselves and against this package's source, so it cannot outlive a write route.
Run:  python3 tests/dashboard/test_server.py
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

from starlette.applications import Starlette
from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.dashboard import server  # noqa: E402

TOKEN = "s3cret-token"
H = {"Authorization": "Bearer " + TOKEN}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.static = os.path.join(self.tmp.name, "static")

    def build_ui(self, index="<!doctype html><title>{{BENCH_NAME}} dashboard</title><body>"
                              "<h1>{{BENCH_NAME}}</h1><script>boot()</script>", assets=None):
        os.makedirs(os.path.join(self.static, "assets"), exist_ok=True)
        with open(os.path.join(self.static, "index.html"), "w") as fh:
            fh.write(index)
        for name, text in (assets or {}).items():
            path = os.path.join(self.static, "assets", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)

    def client(self, bench="Bench One", authorise=None, static=None):
        app = Starlette(routes=server.routes(bench, self.static if static is None else static, authorise))
        c = TestClient(app)
        self.addCleanup(c.close)
        return c


class NotBuilt(Base):
    def test_a_page_is_served_at_the_root_and_says_plainly_the_ui_has_not_been_built(self):
        r = self.client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/html"))
        self.assertIn("The dashboard UI has not been built on this host", r.text)
        self.assertIn("The service is running", r.text)

    def test_the_bench_name_is_in_the_title_and_the_heading(self):
        text = self.client(bench="Garage Bench").get("/").text
        self.assertIn("<title>Garage Bench · nrf-workbench</title>", text)
        self.assertIn("<h1>Garage Bench</h1>", text)
        self.assertNotIn("{{BENCH_NAME}}", text)

    def test_it_points_at_the_real_api_routes(self):
        text = self.client().get("/").text
        for route in ("/api/commands", "/api/capabilities", "/api/streams", "/api/activity", "/api/devices/list",
                      "/api/dashboard/status", "/ws/events"):
            self.assertIn(route, text)

    def test_it_has_no_script_and_both_colour_schemes(self):
        text = self.client().get("/").text
        self.assertNotIn("<script", text.lower())
        self.assertIn("prefers-color-scheme: dark", text)
        self.assertIn("color-scheme: light dark", text)

    def test_it_works_at_a_phone_width_and_never_caches(self):
        r = self.client().get("/")
        self.assertIn('name="viewport"', r.text)
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_no_static_directory_at_all_is_the_same_page(self):
        r = self.client(static="").get("/")
        self.assertIn("has not been built", r.text)
        r = self.client(static=os.path.join(self.tmp.name, "does-not-exist")).get("/")
        self.assertIn("has not been built", r.text)

    def test_the_status_says_it_is_not_built(self):
        body = self.client().get("/api/dashboard/status").json()
        self.assertEqual((body["bench"], body["ui_built"], body["write_routes"]), ("Bench One", False, []))


class Built(Base):
    def test_the_built_index_is_served_with_every_placeholder_replaced(self):
        self.build_ui()
        text = self.client(bench="Lab 3").get("/").text
        self.assertIn("<title>Lab 3 dashboard</title>", text)
        self.assertIn("<h1>Lab 3</h1>", text)
        self.assertNotIn("{{BENCH_NAME}}", text)
        self.assertNotIn("has not been built", text)

    def test_the_name_is_in_the_page_before_any_script_runs(self):
        self.build_ui()
        text = self.client(bench="Lab 3").get("/").text
        self.assertLess(text.index("Lab 3"), text.index("<script"))
        self.assertLess(text.index("<title>"), text.index("<script"))

    def test_a_rebuilt_ui_is_served_without_restarting(self):
        c = self.client()
        self.assertIn("has not been built", c.get("/").text)
        self.build_ui(index="<title>{{BENCH_NAME}}</title>built")
        self.assertIn("built", c.get("/").text)
        self.assertNotIn("has not been built", c.get("/").text)

    def test_the_status_says_it_is_built(self):
        self.build_ui()
        self.assertTrue(self.client().get("/api/dashboard/status").json()["ui_built"])


class Escaping(Base):
    def test_a_hostile_bench_name_cannot_inject_markup(self):
        bench = '<script>alert(1)</script> & "q" \'s\''
        for ui in (False, True):
            if ui:
                self.build_ui()
            text = self.client(bench=bench).get("/").text
            self.assertNotIn("<script>alert(1)</script>", text)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot; &#x27;s&#x27;", text)

    def test_a_name_that_contains_the_placeholder_does_not_confuse_the_substitution(self):
        self.build_ui()
        text = self.client(bench="{{BENCH_NAME}}").get("/").text
        self.assertIn("<title>{{BENCH_NAME}} dashboard</title>", text)

    def test_the_substitute_function(self):
        self.assertEqual(server.substitute("a {{BENCH_NAME}} b {{BENCH_NAME}}", "x<y"), "a x&lt;y b x&lt;y")


class Assets(Base):
    def setUp(self):
        Base.setUp(self)
        self.build_ui(assets={"app.js": "console.log(1)", "style.css": "body{}", "sub/inner.txt": "inner"})
        self.c = self.client()

    def test_a_built_asset_is_served_with_its_type(self):
        r = self.c.get("/assets/app.js")
        self.assertEqual((r.status_code, r.text), (200, "console.log(1)"))
        self.assertIn("javascript", r.headers["content-type"])
        self.assertIn("css", self.c.get("/assets/style.css").headers["content-type"])
        self.assertEqual(self.c.get("/assets/sub/inner.txt").text, "inner")

    def test_a_missing_asset_and_a_directory_are_404(self):
        self.assertEqual(self.c.get("/assets/nope.js").status_code, 404)
        self.assertEqual(self.c.get("/assets/sub").status_code, 404)
        self.assertEqual(self.c.get("/assets/").status_code, 404)

    def test_no_path_can_leave_the_assets_folder(self):
        with open(os.path.join(self.static, "secret.txt"), "w") as fh:
            fh.write("NOT AN ASSET")
        for path in ("/assets/../secret.txt", "/assets/../index.html", "/assets/%2e%2e/secret.txt",
                     "/assets/%2e%2e%2fsecret.txt", "/assets/sub/../../secret.txt", "/assets//etc/passwd",
                     "/assets/%2fetc%2fpasswd", "/assets/..%5csecret.txt", "/assets/....//secret.txt"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertNotIn("NOT AN ASSET", r.text, path)

    def test_a_symlink_that_points_outside_is_refused(self):
        outside = os.path.join(self.tmp.name, "outside.txt")
        with open(outside, "w") as fh:
            fh.write("OUTSIDE")
        os.symlink(outside, os.path.join(self.static, "assets", "link.txt"))
        r = self.c.get("/assets/link.txt")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("OUTSIDE", r.text)

    def test_a_nul_byte_is_refused(self):
        self.assertEqual(self.c.get("/assets/app.js%00.png").status_code, 404)

    def test_a_folder_that_merely_starts_with_the_assets_path_is_not_inside_it(self):
        sibling = os.path.join(self.static, "assets-private")
        os.makedirs(sibling)
        with open(os.path.join(sibling, "x.txt"), "w") as fh:
            fh.write("PRIVATE")
        # The encoded form: a client normalises a literal "../" away before it is sent, so only this reaches
        # the check. "<root>/assets-private/x.txt" starts with "<root>/assets" but is not inside it.
        for path in ("/assets/%2e%2e/assets-private/x.txt", "/assets/..%2fassets-private/x.txt"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assertNotIn("PRIVATE", r.text, path)

    def test_with_no_static_directory_every_asset_is_404(self):
        c = self.client(static="")
        self.assertEqual(c.get("/assets/app.js").status_code, 404)


class StatusClaim(Base):
    def test_every_route_is_a_read_so_the_dashboard_can_write_nothing(self):
        for route in server.routes("b", self.static):
            self.assertTrue(set(route.methods) <= {"GET", "HEAD"}, (route.path, route.methods))

    def test_the_post_to_every_route_is_refused(self):
        c = self.client()
        for path in ("/", "/assets/x.js", "/api/dashboard/status"):
            for method in ("post", "put", "delete", "patch"):
                self.assertEqual(getattr(c, method)(path).status_code, 405, (method, path))

    def test_the_declared_write_routes_equal_what_the_source_defines(self):
        with open(server.__file__) as fh:
            source = fh.read()
        declared = set()
        for m in re.finditer(r"Route\(([^)]*)\)", source):
            methods = re.search(r"methods=\[([^\]]*)\]", m.group(1))
            for method in (methods.group(1) if methods else "GET").replace('"', "").replace("'", "").split(","):
                if method.strip().upper() not in ("GET", "HEAD", ""):
                    declared.add(method.strip().upper())
        self.assertEqual(declared, set(), "a write route was added: WRITE_ROUTES and the status must say so")
        self.assertEqual(list(server.WRITE_ROUTES), [])
        self.assertEqual(self.client().get("/api/dashboard/status").json()["write_routes"], list(server.WRITE_ROUTES))

    def test_the_package_never_imports_the_api(self):
        pkg = os.path.dirname(server.__file__)
        for name in os.listdir(pkg):
            if name.endswith(".py"):
                with open(os.path.join(pkg, name)) as fh:
                    text = fh.read()
                self.assertNotRegex(text, r"(from|import)\s+(nrf_workbench\.api|\.\.api|\.\.\s*import\s+api)", name)

    def test_the_status_honours_the_authorisation_it_is_given_and_the_page_does_not(self):
        def authorise(headers, effect):
            return (headers.get("authorization") == "Bearer " + TOKEN, "token")

        c = self.client(authorise=authorise)
        r = c.get("/api/dashboard/status")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["www-authenticate"], "Bearer")
        self.assertEqual(c.get("/api/dashboard/status", headers=H).status_code, 200)
        self.assertEqual(c.get("/").status_code, 200, "the page is static and public")
        self.assertEqual(c.get("/assets/x.js").status_code, 404, "an assets path is not a 401")


class Composed(Base):
    """Through the real composition root: the dashboard mounted on the real app."""

    def app(self, token=None, **overrides):
        from fakes import Tree
        t = Tree()
        self.addCleanup(t.close)
        cfg, _ = cfgmod.load(env={}, overrides=overrides)
        app, host, _r, _c = service.build(cfg, token=token, sysfs=t.sysfs, hooks={"tool": "/fake"}, static_dir=self.static)
        c = TestClient(app)
        self.addCleanup(c.close)
        return c

    def test_the_bench_name_comes_from_the_configuration(self):
        c = self.app(service={"bench_name": "Pi Bench"})
        self.assertIn("<title>Pi Bench · nrf-workbench</title>", c.get("/").text)
        self.assertEqual(c.get("/api/dashboard/status").json()["bench"], "Pi Bench")

    def test_with_no_name_set_it_is_this_machines_host_name(self):
        import socket
        c = self.app()
        self.assertIn("<title>%s · nrf-workbench</title>" % socket.gethostname(), c.get("/").text)

    def test_the_api_still_works_beside_the_dashboard(self):
        c = self.app()
        self.assertEqual(c.get("/api/commands").status_code, 200)
        self.assertEqual(c.get("/api/devices/list").status_code, 200)
        self.assertEqual(c.get("/nothing-here").status_code, 404)

    def test_in_token_mode_the_page_is_public_and_all_data_is_behind_the_token(self):
        c = self.app(token=TOKEN)
        self.assertEqual(c.get("/").status_code, 200)
        for path in ("/api/commands", "/api/devices/list", "/api/streams", "/api/activity", "/api/dashboard/status"):
            self.assertEqual(c.get(path).status_code, 401, path)
            self.assertEqual(c.get(path, headers=H).status_code in (200, 404), True, path)

    def test_a_built_ui_is_what_the_service_serves(self):
        self.build_ui()
        c = self.app(service={"bench_name": "Pi Bench"})
        self.assertIn("<h1>Pi Bench</h1>", c.get("/").text)


class Config(unittest.TestCase):
    def test_the_default_is_unset_and_the_name_falls_back_to_the_host_name(self):
        import socket
        cfg, problems = cfgmod.load(env={})
        self.assertEqual((problems, cfg["service"]["bench_name"]), ([], None))
        self.assertEqual(service.bench_name(cfg), socket.gethostname())

    def test_a_configured_name_wins(self):
        cfg, _ = cfgmod.load(env={}, overrides={"service": {"bench_name": "Lab"}})
        self.assertEqual(service.bench_name(cfg), "Lab")

    def test_the_environment_sets_it(self):
        cfg, _ = cfgmod.load(env={"NRFWB_BENCH_NAME": "From Env"})
        self.assertEqual(cfg["service"]["bench_name"], "From Env")

    def test_an_empty_or_non_string_name_is_a_problem(self):
        for bad in ("", "   ", 5, True, ["x"]):
            _, problems = cfgmod.load(env={}, overrides={"service": {"bench_name": bad}})
            self.assertTrue(any("bench_name" in p for p in problems), repr(bad))


if __name__ == "__main__":
    unittest.main(verbosity=1)
