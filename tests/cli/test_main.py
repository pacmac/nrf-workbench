#!/usr/bin/env python3
"""End to end: `python -m nrf_workbench ...` as a real process, local mode, on this host's real /sys.

It proves the composition (__main__ -> service.local -> the registry -> the CLI) wires up, with the exit
codes a shell sees. No board is needed: with none attached `devices list` is an empty list, not an error.
Run:  python3 tests/cli/test_main.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


class Process(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, NRFWB_FIRMWARE_DIR=os.path.join(self.tmp.name, "fw"), PYTHONPATH=ROOT)
        for k in ("NRFWB_HOST", "NRFWB_TOKEN", "NRFWB_TOKEN_FILE", "NRFWB_LISTEN"):
            self.env.pop(k, None)

    def run_cli(self, *argv, env=None):
        p = subprocess.run([sys.executable, "-m", "nrf_workbench"] + list(argv), cwd=self.tmp.name,
                           env=env or self.env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def test_commands_lists_the_registered_commands(self):
        rc, out, err = self.run_cli("commands")
        self.assertEqual(rc, 0, err)
        for name in ("devices.list", "firmware.put", "firmware.list"):
            self.assertIn(name, out)

    def test_devices_list_json_is_a_result(self):
        rc, out, err = self.run_cli("--json", "devices", "list")
        self.assertEqual(rc, 0, err)
        body = json.loads(out)
        self.assertEqual((body["ok"], body["class"], sorted(body["data"])), (True, "ok", ["boards"]))

    def test_the_store_is_really_used_through_the_process(self):
        rc, out, err = self.run_cli("--json", "firmware", "list")
        self.assertEqual((rc, json.loads(out)["data"]["items"]), (0, []))
        self.assertTrue(os.path.isdir(os.path.join(self.tmp.name, "fw")), "the store directory was created from the environment")

    def test_exit_codes_a_shell_sees(self):
        self.assertEqual(self.run_cli("firmware", "show", "--id", "deadbeef")[0], 1)       # refused
        self.assertEqual(self.run_cli("firmware", "show")[0], 2)                           # usage
        self.assertEqual(self.run_cli("nosuch")[0], 2)

    def test_a_bad_configuration_is_exit_2_with_the_problem_on_stderr(self):
        cfg = os.path.join(self.tmp.name, "c.yaml")
        with open(cfg, "w") as fh:
            fh.write("service:\n  listn: 1\n")
        rc, out, err = self.run_cli("--config", cfg, "commands")
        self.assertEqual(rc, 2)
        self.assertIn("service.listn", err)

    def test_help_exits_0(self):
        rc, out, err = self.run_cli("--help")
        self.assertEqual(rc, 0)
        self.assertIn("nrf-workbench", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
