#!/usr/bin/env python3
"""Tests for the documents the API publishes about itself (api/docs.py): the index, the guide, OpenAPI and /llms.txt.

Peter, 2026-11-18: "we do not publish the api and any connected agent has no idea how to use this workbench". A client that has
never seen the service has to be able to find out how to call it from the service alone. The documents are generated from the
registry, so what matters is that they match it: every present command is in them, an absent one is named as absent, and access
is the same as for the rest of the API.
Run:  python3 tests/api/test_docs.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from nrf_workbench.core import status  # noqa: E402
from test_app import Base, H, TOKEN  # noqa: E402


class Index(Base):
    def test_the_index_says_how_to_call_and_where_the_rest_is_and_lists_only_what_is_present(self):
        body = self.client.get("/api").json()
        self.assertEqual((body["read_this_first"], body["openapi"]), ("/api/guide", "/api/openapi.json"))
        self.assertIn("demo.echo", body["areas"]["demo"])
        self.assertNotIn("demo.pionly", body["areas"]["demo"])
        self.assertIn("POST /api/<area>/<command>", body["how"]["write"])
        self.assertEqual(self.client.get("/api/").json(), body, "with or without the trailing slash")


class Guide(Base):
    def setUp(self):
        Base.setUp(self)
        r = self.client.get("/api/guide")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/markdown"))
        self.text = r.text

    def test_every_present_command_is_in_it_with_its_route_effect_and_an_example(self):
        for c in self.reg.commands({"sysfs"}):
            if c.effect == "stream":
                continue
            self.assertIn("`%s`" % c.name, self.text, c.name)
            self.assertIn("/api/" + c.name.replace(".", "/"), self.text, c.name)
        self.assertIn("curl -s 'http://testserver/api/demo/echo?text=<text>'", self.text, "only the required arguments are in the example")
        self.assertIn("curl -s -X POST http://testserver/api/demo/add -H 'Content-Type: application/json' -d '{\"a\": 1, \"b\": 1}'", self.text)
        self.assertIn("--data-binary @file", self.text, "an upload is shown as an upload")

    def test_arguments_carry_their_type_and_whether_they_are_required(self):
        self.assertIn("`text` (str, required)", self.text)
        self.assertIn("`n` (int, optional)", self.text)

    def test_a_command_this_host_cannot_run_is_listed_as_absent_with_what_it_needs(self):
        section = self.text.split("## Commands this host does not have")[1]
        self.assertIn("`demo.pionly`: needs pi_header", section)
        self.assertNotIn("curl -s 'http://testserver/api/demo/pionly", self.text)

    def test_the_result_codes_come_from_the_service_and_not_from_a_copy(self):
        table = self.text.split("## Result codes")[1].split("## Commands")[0]
        for code in status.CODES:
            if code != "ok":
                self.assertIn("| `%s` |" % code, table, code)
        self.assertIn("| `flash_failed` | failed | 500 |", table)
        self.assertIn("| `bad_package` | refused | 422 |", table)
        self.assertIn("| `lock_held` | refused | 409 |", table)

    def test_the_first_session_tells_an_agent_to_look_for_a_project_and_that_apply_never_flashes(self):
        session = self.text.split("## A first session")[1]
        self.assertIn("project.show", session)
        self.assertIn("Apply never flashes", session)
        self.assertIn("/api/bench/overview", session)

    def test_the_conventions_an_agent_needs_are_there(self):
        for needle in ("Authorization: Bearer", "GET /api/<area>/<command>", "nothing was attempted and nothing changed",
                       "One attempt", "serial", "Content-Length", "/api/capabilities", "mcp_server"):
            self.assertIn(needle, self.text, needle)


class OpenApi(Base):
    def setUp(self):
        Base.setUp(self)
        self.doc = self.client.get("/api/openapi.json").json()

    def test_it_is_openapi_with_the_server_and_the_result_schema(self):
        self.assertEqual(self.doc["openapi"], "3.1.0")
        self.assertEqual(self.doc["servers"], [{"url": "http://testserver"}])
        self.assertEqual(sorted(self.doc["components"]["schemas"]["Result"]["required"]), ["class", "code", "message", "ok"])

    def test_only_present_commands_are_paths_and_each_has_the_right_method(self):
        paths = self.doc["paths"]
        self.assertIn("/api/demo/echo", paths)
        self.assertNotIn("/api/demo/pionly", paths)
        self.assertNotIn("/api/demo/watch", paths, "a stream is not an HTTP route")
        self.assertEqual(list(paths["/api/demo/echo"]), ["get"])
        self.assertEqual(list(paths["/api/demo/add"]), ["post"])

    def test_arguments_become_query_parameters_for_a_read_and_a_json_body_for_a_write(self):
        echo = self.doc["paths"]["/api/demo/echo"]["get"]
        self.assertEqual([(p["name"], p["in"], p["required"], p["schema"]["type"]) for p in echo["parameters"]],
                         [("text", "query", True, "string"), ("n", "query", False, "integer")])
        body = self.doc["paths"]["/api/demo/add"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual((body["required"], body["properties"]["a"]["type"]), (["a", "b"], "integer"))

    def test_an_upload_takes_the_file_as_the_body(self):
        op = self.doc["paths"]["/api/demo/upload"]["post"]
        self.assertIn("application/octet-stream", op["requestBody"]["content"])
        self.assertEqual([p["name"] for p in op["parameters"]], ["label"])


class Access(Base):
    token = TOKEN

    def test_the_index_the_guide_and_openapi_need_the_same_token_as_the_rest(self):
        for path in ("/api", "/api/guide", "/api/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
            self.assertEqual(self.client.get(path, headers=H).status_code, 200, path)

    def test_llms_txt_is_public_and_only_points_at_the_guide(self):
        r = self.client.get("/llms.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/api/guide", r.text)
        self.assertNotIn("demo", r.text, "it names nothing about this host")
        self.assertNotIn(TOKEN, r.text)


class NotFound(Base):
    def test_a_guessed_api_route_is_told_where_the_real_ones_are_and_another_404_is_not(self):
        r = self.client.get("/api/gpio")
        self.assertEqual(r.status_code, 404)
        self.assertIn("/api/guide", r.json()["see"])
        self.assertNotIn("see", self.client.get("/nowhere").json())


if __name__ == "__main__":
    unittest.main()
