#!/usr/bin/env python3
"""Tests for cli.app in LOCAL mode: the command line generated from the registry rows.

Two groups. The real registered commands against the fake board (every exit code, the raw-body upload,
--json), and a synthetic registry for the generic parser rules (bool flags, units in help, defaults,
needs_service). Arguments are validated by the registry's own validator, so a bad value is exit 2
with the same message the API gives.
Run:  python3 tests/cli/test_cli.py
"""
import contextlib
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
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL  # noqa: E402
from nrf_workbench.cli import app as cli  # noqa: E402
from nrf_workbench.core.config import ConfigError  # noqa: E402
from nrf_workbench.core.registry import Arg, Command, Registry  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402
from test_commands import ALL, Base as CommandsBase  # noqa: E402
from test_flash import Runner  # noqa: E402


def invoke(argv, local, env=None):
    """(exit code, stdout, stderr) including what argparse itself prints (help, parse errors)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.run(list(argv), local=local, env={} if env is None else env, out=out, err=err)
    return rc, out.getvalue(), err.getvalue()


class Real(CommandsBase):
    def cli(self, *argv, caps=ALL):
        return invoke(argv, lambda cfg, env=None: (self.reg, self.host, caps))

    def write_package(self, data=None):
        d = tempfile.mkdtemp(prefix="nrfwb-cli-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        path = os.path.join(d, "fw.zip")
        with open(path, "wb") as fh:
            fh.write(self.data if data is None else data)
        return path


class Globals(unittest.TestCase):
    def test_globals_before_the_first_word(self):
        g, rest = cli._split_globals(["--json", "--host", "http://x:1", "--timeout=30", "devices", "list"])
        self.assertEqual((g["json"], g["host"], g["timeout"], rest), (True, "http://x:1", "30", ["devices", "list"]))

    def test_a_flag_after_the_area_is_not_a_global(self):
        g, rest = cli._split_globals(["devices", "list", "--host", "x", "--json"])
        self.assertEqual((g["host"], g["json"], rest), (None, False, ["devices", "list", "--host", "x", "--json"]))

    def test_unknown_or_valueless_globals_are_usage_errors(self):
        from nrf_workbench.core.registry import UsageError
        for bad in (["--bogus", "devices"], ["--host"], ["--token", "SECRET", "devices"]):
            with self.assertRaises(UsageError, msg=bad):
                cli._split_globals(bad)

    def test_help_is_left_for_the_parser(self):
        self.assertEqual(cli._split_globals(["--help"])[1], ["--help"])
        self.assertEqual(cli._split_globals(["--json", "-h"])[1], ["-h"])

    def test_a_token_is_never_accepted_on_the_command_line(self):
        rc, out, err = invoke(["--token", "SECRET-VALUE", "devices", "list"], local=None)
        self.assertEqual(rc, 2)
        self.assertNotIn("SECRET-VALUE", out + err.replace("--token", ""))


class Listing(Real):
    def test_help_names_every_area_and_the_exit_codes(self):
        rc, out, err = self.cli("--help")
        self.assertEqual(rc, 0)
        for word in ("devices", "firmware", "programmer", "commands", "capabilities", "--host", "--token-file",
                     "exit: 0 ok, 1 refused, 2 usage, 3 failed, 4 suspect"):
            self.assertIn(word, out)

    def test_a_command_help_shows_each_argument_its_type_unit_and_whether_it_is_required(self):
        rc, out, err = self.cli("programmer", "flash", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("--enter-bound-s", out)
        self.assertIn("float s, required", out)
        self.assertIn("--expect-product", out)
        self.assertIn("optional", out)
        rc, out, err = self.cli("firmware", "put", "--help")
        self.assertIn("--file PATH", out)

    def test_commands_lists_name_effect_and_summary(self):
        rc, out, err = self.cli("commands")
        self.assertEqual(rc, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 7)
        self.assertEqual([l.split()[0] for l in lines], sorted(l.split()[0] for l in lines))
        self.assertIn("programmer.flash_stream", out)
        self.assertRegex([l for l in lines if l.startswith("firmware.put")][0], r"firmware\.put\s+write\s+Upload")

    def test_json_commands_is_the_registry_rows(self):
        rc, out, err = self.cli("--json", "commands")
        self.assertEqual(json.loads(out), {"commands": [c.to_dict() for c in self.reg.commands(ALL)]})

    def test_capabilities_says_what_is_absent_and_why(self):
        rc, out, err = self.cli("capabilities", caps={"sysfs"})
        self.assertIn("present: sysfs", out)
        self.assertIn("absent:  programmer.flash (needs firmware_store, uploader)", out)
        rc, out, err = self.cli("--json", "capabilities", caps={"sysfs"})
        self.assertEqual(json.loads(out)["present"], ["sysfs"])

    def test_an_absent_command_is_not_in_the_parser(self):
        rc, out, err = self.cli("programmer", "flash", "--serial", SERIAL, caps={"sysfs"})
        self.assertEqual(rc, 2)


class ExitCodes(Real):
    def setUp(self):
        Real.setUp(self)
        self.t.app_board()
        self.package = self.write_package()
        self.cli("firmware", "put", "--file", self.package)

    def flash(self, *extra, firmware=None):
        return self.cli("programmer", "flash", "--serial", SERIAL, "--firmware", firmware or self.id,
                        "--enter-bound-s", "5", "--flash-bound-s", "60", *extra)

    def test_ok_is_0(self):
        rc, out, err = self.flash()
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(out.startswith("ok: written, judged good"), out)

    def test_refused_is_1_and_names_the_code(self):
        rc, out, err = self.flash(firmware="deadbeef")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("refused firmware_unknown: "), out)

    def test_failed_is_3(self):
        self.host.hooks["runner"] = Runner(rc=1, out="Failed.\n")
        rc, out, err = self.flash()
        self.assertEqual(rc, 3)
        self.assertIn("flash_failed", out)

    def test_suspect_is_4(self):
        rc, out, err = self.flash("--expect-product", "nrfwb-parking 2")
        self.assertEqual(rc, 4)
        self.assertIn("flash_suspect", out)

    def test_usage_is_2_and_goes_to_stderr_naming_the_problem(self):
        cases = (("programmer", "flash", "--serial", SERIAL),                                   # missing required
                 ("programmer", "flash", "--serial", SERIAL, "--firmware", "x", "--enter-bound-s", "abc",
                  "--flash-bound-s", "1"),
                 ("programmer", "flash", "--serial", SERIAL, "--firmware", "x", "--enter-bound-s", "inf",
                  "--flash-bound-s", "1"),
                 ("programmer", "flash", "--serial", SERIAL, "--firmware", "x", "--enter-bound-s", "nan",
                  "--flash-bound-s", "1"),
                 ("programmer", "nosuch"), ("nosuch", "thing"), ("programmer",), (), ("devices", "list", "--bogus", "1"))
        for argv in cases:
            rc, out, err = self.cli(*argv)
            self.assertEqual(rc, 2, argv)
            self.assertFalse(out.strip().startswith(("ok", "refused", "failed", "suspect")), argv)   # no Result

    def test_the_usage_message_names_the_argument(self):
        rc, out, err = self.cli("programmer", "flash", "--serial", SERIAL)
        self.assertIn("firmware", err)
        rc, out, err = self.cli("programmer", "flash", "--serial", SERIAL, "--firmware", "x", "--enter-bound-s", "inf",
                                "--flash-bound-s", "1")
        self.assertIn("enter_bound_s", err)

    def test_a_usage_error_touches_nothing(self):
        self.cli("programmer", "flash", "--serial", SERIAL, "--firmware", self.id, "--enter-bound-s", "inf",
                 "--flash-bound-s", "1")
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))


class Json(Real):
    def test_json_is_the_result_the_api_would_send(self):
        self.t.app_board()
        rc, out, err = self.cli("--json", "devices", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), self.call("devices.list").to_dict())
        self.assertEqual(sorted(json.loads(out)), ["class", "code", "data", "evidence", "message", "ok"])

    def test_a_refusal_in_json_keeps_its_class_code_and_exit(self):
        rc, out, err = self.cli("--json", "firmware", "show", "--id", "deadbeef")
        self.assertEqual(rc, 1)
        body = json.loads(out)
        self.assertEqual((body["ok"], body["class"], body["code"]), (False, "refused", "firmware_unknown"))


class Uploads(Real):
    def test_put_reads_the_file_as_the_body(self):
        rc, out, err = self.cli("firmware", "put", "--file", self.write_package(), "--name", "parking")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(self.id, out)
        rc, out, err = self.cli("--json", "firmware", "list")
        self.assertEqual([i["name"] for i in json.loads(out)["data"]["items"]], ["parking"])

    def test_the_file_is_required_and_must_be_a_file(self):
        for argv in (("firmware", "put"), ("firmware", "put", "--file", "/does/not/exist.zip"),
                     ("firmware", "put", "--file", tempfile.gettempdir())):
            rc, out, err = self.cli(*argv)
            self.assertEqual(rc, 2, argv)
        self.assertEqual(self.call("firmware.list").data["items"], [])

    def test_a_wrong_checksum_is_a_refusal_and_a_malformed_one_is_usage(self):
        rc, out, err = self.cli("firmware", "put", "--file", self.write_package(), "--sha256", "0" * 64)
        self.assertEqual(rc, 1)
        self.assertIn("checksum_mismatch", out)
        rc, out, err = self.cli("firmware", "put", "--file", self.write_package(), "--sha256", "abc")
        self.assertEqual(rc, 2)

    def test_not_a_package_is_a_refusal(self):
        rc, out, err = self.cli("firmware", "put", "--file", self.write_package(b"not a zip"))
        self.assertEqual(rc, 1)
        self.assertIn("bad_package", out)

    def test_the_uploaded_file_is_closed_explicitly_on_every_path(self):
        # Recorded, not left to the garbage collector (which would close it by accident).
        closed, real = [], cli.FileBody

        class Spy(real):
            def close(self):
                closed.append(True)
                real.close(self)

        with mock.patch.object(cli, "FileBody", Spy):
            self.cli("firmware", "put", "--file", self.write_package())                                   # ok
            self.assertEqual(len(closed), 1)
            self.cli("firmware", "put", "--file", self.write_package(b"not a zip"))                       # refused
            self.assertEqual(len(closed), 2)
            self.cli("firmware", "put", "--file", self.write_package(), "--sha256", "abc")                # usage, inside
            self.assertEqual(len(closed), 3)

    def test_local_mode_does_not_hash_the_file_itself(self):
        with mock.patch.object(cli, "file_sha256", side_effect=AssertionError("hashed in local mode")):
            rc, out, err = self.cli("firmware", "put", "--file", self.write_package())
        self.assertEqual(rc, 0, out + err)

    def test_flash_stream_uploads_and_flashes_in_one_command(self):
        self.t.app_board()
        rc, out, err = self.cli("programmer", "flash-stream", "--file", self.write_package(), "--serial", SERIAL,
                                "--enter-bound-s", "5", "--flash-bound-s", "60")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))
        self.assertIn("upload", out)


class Output(Real):
    def test_the_header_then_data_then_evidence_and_nothing_is_truncated(self):
        reg = Registry()
        long_output = "\n".join("line %d of the tool output" % i for i in range(3000))
        reg.add(Command("demo.run", "demo", lambda ctx: Result.failed("flash_failed", "it failed", {"output": long_output},
                                                                    {"kept": True})), module="t")
        rc, out, err = invoke(["demo", "run"], lambda cfg, env=None: (reg, None, set()))
        self.assertEqual(rc, 3)
        self.assertTrue(out.startswith("failed flash_failed: it failed\ndata:\n"), out[:80])
        self.assertIn("evidence:", out)
        self.assertEqual(out.count("line "), 3000, "the WHOLE tool output, never a tail")


class Generic(unittest.TestCase):
    def setUp(self):
        self.reg = Registry()
        seen = self.seen = []

        def echo(ctx, text, n=3, ratio=None, dry=False):
            seen.append(dict(text=text, n=n, ratio=ratio, dry=dry))
            return Result.success(seen[-1])

        self.reg.add(Command("demo.echo", "echo the arguments", echo,
                             args=(Arg("text", doc="what to say"), Arg("n", "int", False, 3, "how often", "ms"),
                                   Arg("ratio", "float", False, None, "a ratio"), Arg("dry", "bool", False, False, "dry run"))),
                     module="t")
        self.reg.add(Command("demo.session", "keeps state", lambda ctx: Result.success(), needs_service=True), module="t")

    def run_cli(self, *argv):
        return invoke(argv, lambda cfg, env=None: (self.reg, None, set()))

    def test_defaults_and_typed_text(self):
        rc, out, err = self.run_cli("demo", "echo", "--text", "hi")
        self.assertEqual((rc, self.seen[-1]), (0, {"text": "hi", "n": 3, "ratio": None, "dry": False}))
        self.run_cli("demo", "echo", "--text", "hi", "--n", "7", "--ratio", "0.5")
        self.assertEqual((self.seen[-1]["n"], self.seen[-1]["ratio"]), (7, 0.5))

    def test_a_bool_is_a_bare_flag_or_a_value(self):
        self.run_cli("demo", "echo", "--text", "x", "--dry")
        self.assertIs(self.seen[-1]["dry"], True)
        self.run_cli("demo", "echo", "--text", "x", "--dry", "false")
        self.assertIs(self.seen[-1]["dry"], False)
        self.run_cli("demo", "echo", "--text", "x", "--dry=true")
        self.assertIs(self.seen[-1]["dry"], True)
        rc, out, err = self.run_cli("demo", "echo", "--text", "x", "--dry", "maybe")
        self.assertEqual(rc, 2)

    def test_text_that_is_not_the_type_is_usage(self):
        for bad in (("--n", "1.5"), ("--n", "x"), ("--ratio", "inf"), ("--ratio", "abc")):
            rc, out, err = self.run_cli("demo", "echo", "--text", "x", *bad)
            self.assertEqual(rc, 2, bad)

    def test_help_shows_type_unit_and_required(self):
        rc, out, err = self.run_cli("demo", "echo", "--help")
        self.assertIn("how often [int ms, optional]", out)
        self.assertIn("what to say [str, required]", out)
        self.assertIn("--dry [true|false]", out)

    def test_a_command_that_keeps_state_refuses_locally_with_service_not_running(self):
        rc, out, err = self.run_cli("demo", "session")
        self.assertEqual(rc, 1)
        self.assertIn("service_not_running", out)
        rc, out, err = self.run_cli("--json", "demo", "session")
        self.assertEqual(json.loads(out)["code"], "service_not_running")


class Setup(unittest.TestCase):
    def test_a_bad_configuration_is_exit_2(self):
        def broken(cfg, env=None):
            raise ConfigError("the units file x cannot be read")
        rc, out, err = invoke(["devices", "list"], broken)
        self.assertEqual(rc, 2)
        self.assertIn("configuration error", err)

    def test_no_host_and_no_local_mode_is_exit_2(self):
        rc, out, err = invoke(["devices", "list"], None)
        self.assertEqual(rc, 2)

    def test_the_timeout_must_be_a_positive_number(self):
        for bad in ("0", "-1", "abc", "nan"):
            rc, out, err = invoke(["--timeout", bad, "commands"], lambda cfg, env=None: (Registry(), None, set()))
            self.assertEqual(rc, 2, bad)

    def test_the_config_path_and_the_same_environment_reach_the_local_factory(self):
        got = []
        env = {"NRFWB_FIRMWARE_DIR": "/some/dir"}
        invoke(["--config", "/some/config.yaml", "commands"],
               lambda cfg, env=None: got.append((cfg, env)) or (Registry(), None, set()), env=env)
        self.assertEqual(got, [("/some/config.yaml", env)], "the factory must see the environment the CLI was given")


if __name__ == "__main__":
    unittest.main(verbosity=1)
