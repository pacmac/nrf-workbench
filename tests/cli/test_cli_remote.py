#!/usr/bin/env python3
"""Tests for cli.app in REMOTE mode: the same command line, run against a real uvicorn server.

The service is the production app over the fake board; the CLI talks to it through core.remote over a
real socket. What matters: the commands come from the service's own list, --json is the same Result the
local mode gives, the exit codes are the same, the SHA-256 of an upload is computed and sent by the CLI
(so a corrupt transfer is caught), an unreachable service is a refusal (service_not_running), and the
token travels only in the header and is never printed.
Run:  python3 tests/cli/test_cli_remote.py
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests", "api"))
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL  # noqa: E402
from nrf_workbench.api.app import build_app  # noqa: E402
from nrf_workbench.api.auth import Auth  # noqa: E402
from nrf_workbench.cli import app as cli  # noqa: E402
from test_cli import invoke  # noqa: E402
from test_commands import ALL, Base as CommandsBase  # noqa: E402
from test_flash import Runner  # noqa: E402
from test_live import Live  # noqa: E402

TOKEN = "s3cret-token-value"


class RemoteBase(CommandsBase):
    token = None

    def setUp(self):
        CommandsBase.setUp(self)
        self.t.app_board()
        self.live = Live(build_app(self.reg, self.host, Auth(self.token, "token"), ALL))
        self.live.__enter__()
        self.addCleanup(self.live.__exit__)
        self.url = "http://127.0.0.1:%d" % self.live.port
        self.dir = tempfile.mkdtemp(prefix="nrfwb-clir-")
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.package = self.write("fw.zip", self.data)

    def write(self, name, data, mode=0o600):
        path = os.path.join(self.dir, name)
        with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)
        os.chmod(path, mode)
        return path

    def cli(self, *argv, env=None, host=True):
        args = (["--host", self.url] if host else []) + list(argv)
        return invoke(args, local=None, env=env)

    def flash(self, *extra):
        return self.cli("programmer", "flash", "--serial", SERIAL, "--firmware", self.id,
                        "--enter-bound-s", "5", "--flash-bound-s", "60", *extra)


class Parity(RemoteBase):
    def test_the_commands_are_the_ones_the_service_lists(self):
        rc, out, err = self.cli("--json", "commands")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {"commands": [c.to_dict() for c in self.reg.commands(ALL)]})
        rc, out, err = self.cli("commands")
        self.assertEqual(len(out.strip().splitlines()), 7)

    def test_capabilities_come_from_the_service(self):
        rc, out, err = self.cli("--json", "capabilities")
        self.assertEqual(json.loads(out), {"present": sorted(ALL), "absent": []})

    def test_json_is_the_same_result_local_mode_gives(self):
        remote_rc, remote_out, _ = self.cli("--json", "devices", "list")
        local_rc, local_out, _ = invoke(["--json", "devices", "list"], lambda cfg, env=None: (self.reg, self.host, ALL), env={})
        self.assertEqual((remote_rc, local_rc), (0, 0))
        self.assertEqual(json.loads(remote_out), json.loads(local_out))

    def test_a_refusal_is_the_same_result_and_exit_code_in_both_modes(self):
        argv = ("--json", "firmware", "show", "--id", "deadbeef")
        remote_rc, remote_out, _ = self.cli(*argv)
        local_rc, local_out, _ = invoke(list(argv), lambda cfg, env=None: (self.reg, self.host, ALL), env={})
        self.assertEqual((remote_rc, local_rc), (1, 1))                      # a 404 over the wire is still "refused"
        self.assertEqual(json.loads(remote_out), json.loads(local_out))


class Flow(RemoteBase):
    def test_upload_list_show_flash_delete_over_the_wire(self):
        rc, out, err = self.cli("firmware", "put", "--file", self.package, "--name", "parking")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(self.id, out)
        rc, out, err = self.cli("--json", "firmware", "list")
        self.assertEqual([i["name"] for i in json.loads(out)["data"]["items"]], ["parking"])
        rc, out, err = self.cli("--json", "firmware", "show", "--id", self.id[:8])
        self.assertEqual(json.loads(out)["data"]["sha256"], self.id)
        rc, out, err = self.flash()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))
        rc, out, err = self.cli("firmware", "delete", "--id", self.id)
        self.assertEqual(rc, 0)
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_flash_stream_uploads_and_flashes_in_one_command(self):
        rc, out, err = self.cli("programmer", "flash-stream", "--file", self.package, "--serial", SERIAL,
                                "--enter-bound-s", "5", "--flash-bound-s", "60", "--name", "parking")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(self.id, self.call("firmware.list").data["items"][0]["sha256"])
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))

    def test_every_exit_code_survives_the_wire(self):
        self.cli("firmware", "put", "--file", self.package)
        rc, out, err = self.flash("--expect-product", "nrfwb-parking 2")
        self.assertEqual(rc, 4, out + err)                                   # suspect
        self.t.unplug("1-3")
        self.t.app_board()
        self.host.hooks["runner"] = Runner(rc=1, out="Failed.\n")
        rc, out, err = self.flash()
        self.assertEqual(rc, 3, out + err)                                   # failed
        rc, out, err = self.cli("programmer", "flash", "--serial", SERIAL, "--firmware", "deadbeef",
                                "--enter-bound-s", "5", "--flash-bound-s", "60")
        self.assertEqual(rc, 1)                                              # refused
        rc, out, err = self.cli("programmer", "flash", "--serial", SERIAL)
        self.assertEqual(rc, 2)                                              # usage, before any request

    def test_a_usage_error_the_service_would_refuse_is_caught_before_any_request(self):
        with mock.patch.object(cli, "Remote") as remote_cls:
            remote_cls.return_value.commands.return_value = [c.to_dict() for c in self.reg.commands(ALL)]
            rc, out, err = self.cli("programmer", "flash", "--serial", SERIAL, "--firmware", "x", "--enter-bound-s",
                                    "inf", "--flash-bound-s", "1")
        self.assertEqual(rc, 2)
        remote_cls.return_value.call.assert_not_called()

    def test_the_timeout_flag_is_accepted_and_passed_on(self):
        rc, out, err = self.cli("--timeout", "30", "devices", "list")
        self.assertEqual(rc, 0, err)
        with mock.patch.object(cli, "Remote") as remote_cls:
            remote_cls.return_value.commands.return_value = []
            self.cli("--timeout", "12.5", "commands")
        self.assertEqual(remote_cls.call_args.args[2], 12.5)


class Checksum(RemoteBase):
    def test_the_cli_computes_and_sends_the_checksum_so_a_corrupt_transfer_is_caught(self):
        with mock.patch.object(cli, "file_sha256", return_value="0" * 64):
            rc, out, err = self.cli("firmware", "put", "--file", self.package)
        self.assertEqual(rc, 1, "a checksum that does not match the bytes must be refused")
        self.assertIn("checksum_mismatch", out)
        self.assertEqual(self.call("firmware.list").data["items"], [])
        rc, out, err = self.cli("firmware", "put", "--file", self.package)
        self.assertEqual(rc, 0, out + err)

    def test_a_checksum_the_user_gave_is_used_not_replaced(self):
        with mock.patch.object(cli, "file_sha256", return_value=self.id):     # the CORRECT one, were it used
            rc, out, err = self.cli("firmware", "put", "--file", self.package, "--sha256", "f" * 64)
        self.assertEqual(rc, 1)
        self.assertIn("checksum_mismatch", out)

    def test_flash_stream_also_sends_the_checksum(self):
        with mock.patch.object(cli, "file_sha256", return_value="0" * 64):
            rc, out, err = self.cli("programmer", "flash-stream", "--file", self.package, "--serial", SERIAL,
                                    "--enter-bound-s", "5", "--flash-bound-s", "60")
        self.assertEqual(rc, 1)
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))


class Unreachable(RemoteBase):
    def test_a_service_that_is_not_running_is_a_refusal_with_its_own_code(self):
        self.live.__exit__()
        rc, out, err = self.cli("devices", "list")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("refused service_not_running: "), out + err)
        self.assertIn(self.url.split("//")[1], out)

    def test_in_json_it_is_a_result_too(self):
        self.live.__exit__()
        rc, out, err = self.cli("--json", "devices", "list")
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["code"], "service_not_running")

    def test_a_bad_address_is_usage(self):
        for bad in ("localhost:8080", "ftp://x", "http://"):
            rc, out, err = invoke(["--host", bad, "commands"], local=None, env={})
            self.assertEqual(rc, 2, bad)


class Environment(RemoteBase):
    def test_the_address_may_come_from_the_environment_and_a_flag_beats_it(self):
        rc, out, err = self.cli("devices", "list", host=False, env={"NRFWB_HOST": self.url})
        self.assertEqual(rc, 0, out + err)
        rc, out, err = invoke(["--host", self.url, "devices", "list"], local=None, env={"NRFWB_HOST": "http://127.0.0.1:1"})
        self.assertEqual(rc, 0, "the flag must beat the environment")


class TokenServer(RemoteBase):
    token = TOKEN

    def everything_printed_is_free_of_the_token(self, *results):
        for rc, out, err in results:
            self.assertNotIn(TOKEN, out + err)

    def test_no_token_is_unauthorized_exit_1(self):
        rc, out, err = self.cli("devices", "list")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("unauthorized", err)
        rc, out, err = self.cli("--json", "devices", "list")
        self.assertEqual((rc, json.loads(out)["error"]), (1, "unauthorized"))

    def test_the_token_by_file_by_file_in_the_environment_and_by_value_in_the_environment(self):
        tok = self.write("token", TOKEN + "\n")
        results = [self.cli("--token-file", tok, "devices", "list"),
                   self.cli("devices", "list", env={"NRFWB_TOKEN_FILE": tok}),
                   self.cli("devices", "list", env={"NRFWB_TOKEN": TOKEN})]
        for rc, out, err in results:
            self.assertEqual(rc, 0, out + err)
        self.everything_printed_is_free_of_the_token(*results)

    def test_a_wrong_token_is_unauthorized_and_is_not_echoed(self):
        results = [self.cli("devices", "list", env={"NRFWB_TOKEN": "wrong-" + TOKEN})]
        self.assertEqual(results[0][0], 1)
        self.assertNotIn("wrong-" + TOKEN, results[0][1] + results[0][2])

    def test_an_unreadable_token_file_is_usage_an_empty_one_is_no_token(self):
        rc, out, err = self.cli("--token-file", os.path.join(self.dir, "missing"), "devices", "list")
        self.assertEqual(rc, 2)
        rc, out, err = self.cli("--token-file", self.write("empty", "  \n"), "devices", "list")
        self.assertEqual(rc, 1)
        self.assertIn("unauthorized", err)

    def test_the_flag_beats_the_environment_for_the_token_file(self):
        good, bad = self.write("good", TOKEN), self.write("bad", "nope")
        rc, out, err = self.cli("--token-file", good, "devices", "list", env={"NRFWB_TOKEN_FILE": bad})
        self.assertEqual(rc, 0, out + err)

    def test_an_upload_refused_for_its_token_is_unauthorized_not_unreachable(self):
        # A real socket: the service answers 401 and the body is never read. The client must read that
        # answer, not report a network failure.
        big = self.write("big.zip", b"x" * (3 * 1024 * 1024))
        for attempt in range(15):
            rc, out, err = self.cli("firmware", "put", "--file", big)
            self.assertEqual(rc, 1, (attempt, out, err))
            self.assertIn("unauthorized", err, (attempt, out, err))
            self.assertNotIn("service_not_running", out + err)


class Streaming(RemoteBase):
    def test_a_large_upload_goes_through_in_chunks_and_is_stored_intact(self):
        import zipfile
        path = os.path.join(self.dir, "large.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            z.writestr(zipfile.ZipInfo("manifest.json", (2020, 1, 1, 0, 0, 0)), json.dumps({"manifest": {"application": {
                "bin_file": "firmware.bin", "dat_file": "firmware.dat",
                "init_packet_data": {"softdevice_req": [182], "device_type": 82}}}}))
            z.writestr(zipfile.ZipInfo("firmware.bin", (2020, 1, 1, 0, 0, 0)), os.urandom(2 * 1024 * 1024))
            z.writestr(zipfile.ZipInfo("firmware.dat", (2020, 1, 1, 0, 0, 0)), b"\0" * 14)
        rc, out, err = self.cli("--json", "firmware", "put", "--file", path)
        self.assertEqual(rc, 0, out + err)
        d = json.loads(out)["data"]
        self.assertEqual(d["bytes"], os.path.getsize(path))
        with open(path, "rb") as fh:
            import hashlib
            self.assertEqual(d["sha256"], hashlib.sha256(fh.read()).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=1)
