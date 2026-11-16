#!/usr/bin/env python3
"""Tests for api.mcp_server: tools registered from the service's own command list.

Four groups. The tool list and its schemas (generated from rows, refreshed at every listing, no table of
its own); tool calls through the SDK's in-process client against a REAL uvicorn server over the fake
board; the failure and safety rules (checked-not-parsed arguments, the upload path, the whole Result with
is_error, the token never in any output); and the REAL stdio transport: the server spawned as a process
and spoken to over its pipes, which the earlier smoke test explicitly did not exercise.
Run:  python3 tests/api/test_mcp.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import anyio
from mcp import Client, StdioServerParameters

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL  # noqa: E402
from nrf_workbench.api import mcp_server  # noqa: E402
from nrf_workbench.api.app import build_app  # noqa: E402
from nrf_workbench.api.auth import Auth  # noqa: E402
from nrf_workbench.core.registry import Arg, Command, Registry  # noqa: E402
from nrf_workbench.core.remote import Remote  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from test_commands import ALL, Base as CommandsBase  # noqa: E402
from test_flash import Runner  # noqa: E402
from test_live import Live  # noqa: E402

TOKEN = "s3cret-token-value"


def run(coro_fn, server):
    async def main():
        async with Client(server) as client:
            return await coro_fn(client)
    return anyio.run(main)


def deep_message(exc):
    """The message of an exception, looking inside the groups the async runtime wraps it in."""
    inner = getattr(exc, "exceptions", None)
    return " ".join(deep_message(e) for e in inner) if inner else str(exc)


def body(result):
    """The Result JSON from a tool result, checked against its structured twin."""
    parsed = json.loads(result.content[0].text)
    assert parsed == result.structured_content, "the text and the structured content must be the same Result"
    return parsed


class FakeRemote(object):
    """A service stand-in for the listing tests: rows come from `rows_per_call`, calls are recorded."""
    base_url = "http://fake:1"

    def __init__(self, rows_per_call, result=None):
        self.rows_per_call, self.listed, self.calls, self.result = rows_per_call, 0, [], result

    def commands(self):
        rows = self.rows_per_call[min(self.listed, len(self.rows_per_call) - 1)]
        self.listed += 1
        return rows

    def call(self, row, args, path=None):
        self.calls.append((row["name"], args, path))
        return self.result or Result.success({"echo": args})


def rows_from(commands):
    return [c.to_dict() for c in commands]


def demo_commands():
    return [Command("demo.read", "Read something.", lambda ctx, **kw: None, args=(Arg("serial", doc="the board"),)),
            Command("demo.flash", "Write something.", lambda ctx, **kw: None, effect="write", result="what was done",
                    args=(Arg("serial"), Arg("bound_s", "float", unit="s", doc="the ceiling"),
                          Arg("count", "int", required=False, default=3), Arg("dry", "bool", required=False, default=False))),
            Command("demo.upload", "Upload a file.", lambda ctx, **kw: None, effect="write", body="raw",
                    args=(Arg("name", required=False), Arg("sha256", required=False))),
            Command("demo.watch", "A stream.", lambda ctx, **kw: None, effect="stream", stream="demo.event")]


class Listing(unittest.TestCase):
    def tools(self, remote):
        async def go(client):
            return (await client.list_tools()).tools
        return {t.name: t for t in run(go, mcp_server.build_server(remote))}

    def test_one_tool_per_command_named_area_underscore_command_and_no_streams(self):
        tools = self.tools(FakeRemote([rows_from(demo_commands())]))
        self.assertEqual(sorted(tools), ["demo_flash", "demo_read", "demo_upload"])

    def test_descriptions_say_the_effect_and_the_result_and_reads_are_marked_read_only(self):
        tools = self.tools(FakeRemote([rows_from(demo_commands())]))
        self.assertIn("Write something. [changes hardware or files]", tools["demo_flash"].description)
        self.assertIn("Result data: what was done", tools["demo_flash"].description)
        self.assertIn("[reads only]", tools["demo_read"].description)
        self.assertIs(tools["demo_read"].annotations.read_only_hint, True)
        self.assertIs(tools["demo_flash"].annotations.read_only_hint, False)

    def test_the_schema_has_the_right_types_required_defaults_units_and_is_closed(self):
        s = self.tools(FakeRemote([rows_from(demo_commands())]))["demo_flash"].input_schema
        self.assertEqual(s["type"], "object")
        self.assertIs(s["additionalProperties"], False)
        self.assertEqual(s["required"], ["serial", "bound_s"])
        p = s["properties"]
        self.assertEqual([p[k]["type"] for k in ("serial", "bound_s", "count", "dry")], ["string", "number", "integer", "boolean"])
        self.assertEqual((p["count"]["default"], p["dry"]["default"]), (3, False))
        self.assertIn("(unit: s)", p["bound_s"]["description"])
        self.assertIn("the ceiling", p["bound_s"]["description"])
        self.assertNotIn("default", p["serial"])

    def test_an_upload_tool_takes_a_path_first_and_it_is_required(self):
        s = self.tools(FakeRemote([rows_from(demo_commands())]))["demo_upload"].input_schema
        self.assertEqual(s["required"], ["path"])
        self.assertEqual(s["properties"]["path"]["type"], "string")
        self.assertIn("machine THIS MCP server runs on", s["properties"]["path"]["description"])
        self.assertIn("sha256", s["properties"])

    def test_a_command_with_its_own_path_argument_cannot_be_an_upload_tool(self):
        row = rows_from([Command("demo.bad", "x", lambda ctx, **kw: None, effect="write", body="raw", args=(Arg("path"),))])[0]
        with self.assertRaises(ValueError):
            mcp_server.input_schema(row)

    def test_the_list_follows_the_service_at_every_listing_not_just_at_start(self):
        first, second = demo_commands()[:1], demo_commands()[:2]
        remote = FakeRemote([rows_from(first), rows_from(second), rows_from(first)])

        async def go(client):
            return [sorted(t.name for t in (await client.list_tools()).tools) for _ in range(3)]

        seen = run(go, mcp_server.build_server(remote))
        self.assertEqual(seen, [["demo_read"], ["demo_flash", "demo_read"], ["demo_read"]])

    def test_a_service_that_cannot_be_listed_is_an_error_not_an_empty_list(self):
        class Down(FakeRemote):
            def commands(self):
                from nrf_workbench.core.remote import RemoteError
                raise RemoteError("unreachable", "no route")

        async def go(client):
            return await client.list_tools()

        with self.assertRaises(Exception) as caught:
            run(go, mcp_server.build_server(Down([[]])))
        self.assertIn("cannot list the service's commands", deep_message(caught.exception))
        self.assertIn("unreachable", deep_message(caught.exception))

    def test_an_unknown_tool_is_an_error_result_after_one_refresh(self):
        remote = FakeRemote([rows_from(demo_commands()[:1])])

        async def go(client):
            return await client.call_tool("demo_nope", {})

        r = run(go, mcp_server.build_server(remote))
        self.assertTrue(r.is_error)
        self.assertEqual(body(r)["error"], "unknown_tool")
        self.assertEqual(remote.listed, 1)

    def test_a_tool_added_after_the_last_listing_is_callable(self):
        remote = FakeRemote([rows_from(demo_commands()[:1]), rows_from(demo_commands()[:2])])

        async def go(client):
            await client.list_tools()
            return await client.call_tool("demo_flash", {"serial": "S", "bound_s": 1.5})

        r = run(go, mcp_server.build_server(remote))
        self.assertFalse(r.is_error, r)
        self.assertEqual(remote.calls[0][0], "demo.flash")


class Calls(CommandsBase):
    token = None

    def setUp(self):
        CommandsBase.setUp(self)
        self.t.app_board()
        self.live = Live(build_app(self.reg, self.host, Auth(self.token, "token"), ALL))
        self.live.__enter__()
        self.addCleanup(self.live.__exit__)
        self.url = "http://127.0.0.1:%d" % self.live.port
        self.server = mcp_server.build_server(Remote(self.url, self.token))
        self.dir = tempfile.mkdtemp(prefix="nrfwb-mcp-")
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.package = self.write("fw.zip", self.data)

    def write(self, name, data):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def tool(self, name, args):
        async def go(client):
            return await client.call_tool(name, args)
        return run(go, self.server)

    def flash_args(self, **kw):
        a = {"serial": SERIAL, "firmware": self.id, "enter_bound_s": 5, "flash_bound_s": 60}
        a.update(kw)
        return a

    def nothing_happened(self):
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))


class RealCalls(Calls):
    def test_the_tool_list_is_the_services_own_list(self):
        async def go(client):
            return sorted(t.name for t in (await client.list_tools()).tools)
        self.assertEqual(run(go, self.server), sorted(c.to_dict()["mcp"] for c in self.reg.commands(ALL)))

    def test_a_read_returns_the_whole_result_text_and_structured_and_is_not_an_error(self):
        r = self.tool("devices_list", {})
        self.assertFalse(r.is_error)
        b = body(r)
        self.assertEqual((b["ok"], b["class"], b["code"]), (True, "ok", "ok"))
        self.assertEqual(sorted(b), ["class", "code", "data", "evidence", "message", "ok"])
        self.assertEqual(b["data"]["boards"][0]["serial"], SERIAL)

    def test_a_refusal_is_an_error_result_with_everything_in_it(self):
        r = self.tool("firmware_show", {"id": "deadbeef"})
        self.assertTrue(r.is_error)
        b = body(r)
        self.assertEqual((b["class"], b["code"], b["evidence"]), ("refused", "firmware_unknown", {"id": "deadbeef"}))

    def test_a_failed_flash_keeps_the_whole_tool_output(self):
        self.tool("firmware_put", {"path": self.package})
        self.host.hooks["runner"] = Runner(rc=1, out="line one\nline two\n")
        r = self.tool("programmer_flash", self.flash_args())
        self.assertTrue(r.is_error)
        b = body(r)
        self.assertEqual((b["class"], b["code"]), ("failed", "flash_failed"))
        self.assertEqual(b["evidence"]["output"], "line one\nline two\n")
        self.assertIs(b["evidence"]["entered_bootloader"], True)

    def test_a_suspect_flash_is_an_error_result(self):
        self.tool("firmware_put", {"path": self.package})
        r = self.tool("programmer_flash", self.flash_args(expect_product="nrfwb-parking 2"))
        self.assertTrue(r.is_error)
        self.assertEqual(body(r)["class"], "suspect")

    def test_flash_by_id_succeeds_and_is_not_an_error(self):
        self.tool("firmware_put", {"path": self.package})
        r = self.tool("programmer_flash", self.flash_args())
        self.assertFalse(r.is_error, r)
        self.assertEqual(body(r)["data"]["entered_bootloader"], True)
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))


class Arguments(Calls):
    def test_a_missing_or_extra_argument_is_a_usage_error_and_nothing_is_sent(self):
        for args in ({"serial": SERIAL}, dict(self.flash_args(), extra=1)):
            r = self.tool("programmer_flash", args)
            self.assertTrue(r.is_error)
            self.assertEqual(body(r)["error"], "usage", args)
        self.nothing_happened()

    def test_json_is_checked_not_parsed(self):
        for bad in ({"enter_bound_s": "5"}, {"enter_bound_s": True}, {"serial": 5}, {"flash_bound_s": "60"}):
            r = self.tool("programmer_flash", self.flash_args(**bad))
            self.assertTrue(r.is_error, bad)
            self.assertEqual(body(r)["error"], "usage", bad)
        self.nothing_happened()

    def test_the_usage_error_names_the_argument(self):
        r = self.tool("programmer_flash", self.flash_args(enter_bound_s="5"))
        self.assertEqual(body(r)["arg"], "enter_bound_s")

    def test_null_for_an_optional_argument_means_not_given(self):
        self.tool("firmware_put", {"path": self.package})
        r = self.tool("programmer_flash", self.flash_args(expect_product=None))
        self.assertFalse(r.is_error, r)


class Uploads(Calls):
    def list_store(self):
        return [i["sha256"] for i in self.call("firmware.list").data["items"]]

    def test_put_streams_a_local_file_to_the_service(self):
        r = self.tool("firmware_put", {"path": self.package, "name": "parking"})
        self.assertFalse(r.is_error, r)
        self.assertEqual(body(r)["data"]["sha256"], self.id)
        self.assertEqual(self.list_store(), [self.id])

    def test_the_checksum_is_computed_and_sent_so_a_corrupt_transfer_is_refused(self):
        with mock.patch.object(mcp_server, "file_sha256", return_value="0" * 64):
            r = self.tool("firmware_put", {"path": self.package})
        self.assertTrue(r.is_error)
        self.assertEqual(body(r)["code"], "checksum_mismatch")
        self.assertEqual(self.list_store(), [])

    def test_a_checksum_the_caller_gave_is_used_not_replaced(self):
        with mock.patch.object(mcp_server, "file_sha256", return_value=self.id):
            r = self.tool("firmware_put", {"path": self.package, "sha256": "f" * 64})
        self.assertEqual(body(r)["code"], "checksum_mismatch")

    def test_the_path_must_be_given_and_be_a_file_on_this_machine(self):
        for args in ({}, {"path": ""}, {"path": 5}, {"path": os.path.join(self.dir, "missing.zip")}, {"path": self.dir}):
            r = self.tool("firmware_put", args)
            self.assertTrue(r.is_error, args)
            self.assertEqual((body(r)["error"], body(r)["arg"]), ("usage", "path"), args)
        self.assertEqual(self.list_store(), [])

    def test_flash_stream_uploads_and_flashes_in_one_tool_call(self):
        r = self.tool("programmer_flash_stream", {"path": self.package, "serial": SERIAL, "enter_bound_s": 5,
                                                  "flash_bound_s": 60, "name": "parking"})
        self.assertFalse(r.is_error, r)
        self.assertEqual(body(r)["data"]["upload"]["id"], self.id)
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))

    def test_a_bad_package_is_a_refusal_and_nothing_is_touched(self):
        r = self.tool("programmer_flash_stream", {"path": self.write("bad.zip", b"not a zip"), "serial": SERIAL,
                                                  "enter_bound_s": 5, "flash_bound_s": 60})
        self.assertTrue(r.is_error)
        self.assertEqual(body(r)["code"], "bad_package")
        self.nothing_happened()

    def test_the_file_handle_is_never_the_ais_problem_a_large_file_goes_through(self):
        import zipfile
        path = os.path.join(self.dir, "large.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            z.writestr(zipfile.ZipInfo("manifest.json", (2020, 1, 1, 0, 0, 0)), json.dumps({"manifest": {"application": {
                "bin_file": "firmware.bin", "dat_file": "firmware.dat",
                "init_packet_data": {"softdevice_req": [182], "device_type": 82}}}}))
            z.writestr(zipfile.ZipInfo("firmware.bin", (2020, 1, 1, 0, 0, 0)), os.urandom(2 * 1024 * 1024))
            z.writestr(zipfile.ZipInfo("firmware.dat", (2020, 1, 1, 0, 0, 0)), b"\0" * 14)
        r = self.tool("firmware_put", {"path": path})
        self.assertFalse(r.is_error, r)
        self.assertEqual(body(r)["data"]["bytes"], os.path.getsize(path))


class Failures(Calls):
    def test_a_service_that_is_down_is_a_refusal_service_not_running_even_on_the_first_call(self):
        self.live.__exit__()

        async def go(client):
            return await client.call_tool("devices_list", {})

        r = run(go, mcp_server.build_server(Remote(self.url)))     # no listing yet, so the row cannot be found
        self.assertTrue(r.is_error)
        self.assertEqual((body(r)["class"], body(r)["code"]), ("refused", "service_not_running"))

    def test_listing_a_service_that_is_down_raises_rather_than_returning_no_tools(self):
        self.live.__exit__()

        async def go(client):
            return await client.list_tools()

        with self.assertRaises(Exception) as caught:
            run(go, mcp_server.build_server(Remote(self.url)))
        self.assertIn("cannot list the service's commands", deep_message(caught.exception))

    def test_a_service_that_goes_down_after_listing_is_service_not_running(self):
        async def go(client):
            await client.list_tools()
            self.live.__exit__()
            return await client.call_tool("devices_list", {})

        r = run(go, self.server)
        self.assertTrue(r.is_error)
        self.assertEqual((body(r)["class"], body(r)["code"]), ("refused", "service_not_running"))
        self.assertEqual(body(r)["evidence"]["address"], self.url)


class TokenService(Calls):
    token = TOKEN

    def everything(self, *results):
        return " ".join(r.content[0].text + json.dumps(r.structured_content) for r in results)

    def test_no_token_is_an_unauthorized_error_result_naming_no_secret(self):
        server = mcp_server.build_server(Remote(self.url))

        async def go(client):
            return await client.call_tool("devices_list", {})

        r = run(go, server)
        self.assertTrue(r.is_error)
        self.assertEqual(body(r)["error"], "unauthorized")
        self.assertNotIn(TOKEN, self.everything(r))

    def test_listing_without_the_token_raises_naming_unauthorized_and_no_secret(self):
        async def go(client):
            return await client.list_tools()

        with self.assertRaises(Exception) as caught:
            run(go, mcp_server.build_server(Remote(self.url)))
        self.assertIn("unauthorized", deep_message(caught.exception))
        self.assertNotIn(TOKEN, deep_message(caught.exception))

    def test_with_the_token_it_works_and_the_token_is_in_no_output(self):
        results = [self.tool("devices_list", {}), self.tool("firmware_show", {"id": "deadbeef"}),
                   self.tool("firmware_put", {"path": self.package})]
        self.assertFalse(results[0].is_error)
        self.assertNotIn(TOKEN, self.everything(*results))

    def test_a_token_that_goes_stale_is_an_unauthorized_result_and_not_echoed(self):
        async def go(client):
            await client.list_tools()
            self.server_remote_token("wrong-" + TOKEN)
            return await client.call_tool("devices_list", {})

        remote = Remote(self.url, TOKEN)
        server = mcp_server.build_server(remote)
        self.server_remote_token = lambda t: setattr(remote, "token", t)
        r = run(go, server)
        self.assertTrue(r.is_error)
        self.assertEqual(body(r)["error"], "unauthorized")
        self.assertNotIn(TOKEN, self.everything(r))


class Main(unittest.TestCase):
    def test_no_host_is_exit_2_and_nothing_is_written_to_stdout(self):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mcp_server.main([], env={})
        self.assertEqual((rc, out.getvalue()), (2, ""))
        self.assertIn("--host", err.getvalue())

    def test_a_bad_address_or_an_unreadable_token_file_is_exit_2(self):
        import contextlib
        import io
        for argv, env in ((["--host", "localhost:80"], {}), (["--host", "http://127.0.0.1:1", "--token-file", "/no/such"], {})):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = mcp_server.main(argv, env=env)
            self.assertEqual((rc, out.getvalue()), (2, ""), argv)

    def test_it_serves_with_the_address_from_the_environment_and_the_token_from_a_file(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            tok = os.path.join(d, "token")
            with open(tok, "w") as fh:
                fh.write(TOKEN + "\n")
            rc = mcp_server.main(["--token-file", tok], env={"NRFWB_HOST": "http://127.0.0.1:9"},
                                 run=lambda fn, server: calls.append((fn, server)))
        self.assertEqual((rc, len(calls)), (0, 1))
        self.assertIs(calls[0][0], mcp_server.serve)

    def test_the_module_never_prints_to_stdout_because_the_protocol_owns_it(self):
        with open(mcp_server.__file__) as fh:
            lines = [l for l in fh.read().splitlines() if "print(" in l and not l.strip().startswith("#")]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn("file=sys.stderr", line)


class RealStdio(unittest.TestCase):
    """The server as a real process, spoken to over its pipes."""

    def serve(self, token, extra_env, scenario):
        """Run `scenario(client)` against the server process, with a real service behind it."""
        from test_commands import Base as B

        class Fixture(B):
            def runTest(self):
                pass

        fx = Fixture()
        fx.setUp()
        fx.t.app_board()
        live = Live(build_app(fx.reg, fx.host, Auth(token, "token"), ALL))
        live.__enter__()
        try:
            url = "http://127.0.0.1:%d" % live.port
            env = dict(os.environ, PYTHONPATH=ROOT)
            for k in ("NRFWB_HOST", "NRFWB_TOKEN", "NRFWB_TOKEN_FILE"):
                env.pop(k, None)
            env.update(extra_env)
            args = ["-m", "nrf_workbench.api.mcp_server", "--host", url]
            params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=ROOT)
            self.assertNotIn(TOKEN, " ".join(args), "the token must never be a command-line value")

            async def go():
                async with Client(params) as client:
                    return await scenario(client)

            return anyio.run(go), fx
        finally:
            live.__exit__()
            fx.doCleanups()

    def test_list_and_call_over_the_real_stdio_transport(self):
        async def scenario(client):
            tools = sorted(t.name for t in (await client.list_tools()).tools)
            return tools, await client.call_tool("devices_list", {})

        (tools, r), fx = self.serve(None, {}, scenario)
        self.assertEqual(tools, sorted(c.to_dict()["mcp"] for c in fx.reg.commands(ALL)))
        self.assertFalse(r.is_error, r)
        self.assertEqual(json.loads(r.content[0].text)["data"]["boards"][0]["serial"], SERIAL)

    def test_a_missing_token_reaches_the_client_as_a_readable_error_over_real_pipes(self):
        async def scenario(client):
            try:
                await client.list_tools()
            except Exception as exc:          # noqa: BLE001
                return deep_message(exc)
            return None

        message, _fx = self.serve(TOKEN, {}, scenario)
        self.assertIsNotNone(message, "listing without the token must fail")
        self.assertIn("cannot list the service's commands", message)
        self.assertIn("unauthorized", message)
        self.assertNotIn(TOKEN, message)
        self.assertNotIn("Internal server error", message, "the SDK must not have masked the reason")

    def test_the_token_in_the_environment_works_and_is_in_no_output(self):
        async def scenario(client):
            return await client.call_tool("devices_list", {}), await client.call_tool("firmware_show", {"id": "deadbeef"})

        (ok, refused), _fx = self.serve(TOKEN, {"NRFWB_TOKEN": TOKEN}, scenario)
        self.assertFalse(ok.is_error, ok)
        self.assertTrue(refused.is_error)
        for r in (ok, refused):
            self.assertNotIn(TOKEN, r.content[0].text + json.dumps(r.structured_content))


if __name__ == "__main__":
    unittest.main(verbosity=1)
