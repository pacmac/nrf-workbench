#!/usr/bin/env python3
"""The README's "Use it" examples must be real: every option it shows exists, on the real service
parser and on the real command line (the command's own generated --help).

A README that shows `--firmware-dirr` or a command that does not exist is worse than no README, and
nothing else would notice. Run:  python3 tests/cli/test_readme.py
"""
import contextlib
import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench import service  # noqa: E402
from nrf_workbench.cli import app as cli  # noqa: E402
from nrf_workbench.core import config  # noqa: E402

GLOBAL_WITH_VALUE = ("--host", "--token-file", "--timeout", "--config")


def use_it_commands():
    """The shell commands in the README's 'Use it' section, with continuation lines joined."""
    with open(os.path.join(ROOT, "README.md")) as fh:
        readme = fh.read()
    section = readme.split("## Use it", 1)[1].split("\n## ", 1)[0]
    commands = []
    for block in re.findall(r"```sh\n(.*?)```", section, re.S):
        joined = re.sub(r"\\\n\s*", " ", block)
        commands.extend(line.strip() for line in joined.splitlines() if line.strip() and not line.strip().startswith("#"))
    return commands


def help_of(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.run(argv, local=service.local, env={"NRFWB_FIRMWARE_DIR": "/tmp/nrfwb-readme-check"}, out=out, err=err)
    return rc, out.getvalue() + err.getvalue()


class ReadmeIsReal(unittest.TestCase):
    def test_the_section_was_found(self):
        self.assertGreater(len(use_it_commands()), 6, use_it_commands())

    def test_the_service_command_uses_real_options(self):
        lines = [c for c in use_it_commands() if "nrf_workbench.service" in c]
        self.assertGreaterEqual(len(lines), 2)
        helptext = service._parser().format_help()
        for line in lines:
            for opt in re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", line):
                self.assertIn(opt, helptext, "README shows %s, which the service does not have: %s" % (opt, line))

    def test_the_cli_commands_exist_and_use_real_options(self):
        lines = [c for c in use_it_commands() if c.startswith("python -m nrf_workbench ")]
        self.assertGreaterEqual(len(lines), 5)
        top = help_of(["--help"])[1]
        for line in lines:
            tokens = line.split()[3:]
            while tokens and tokens[0].startswith("--"):                      # global options come first
                opt = tokens.pop(0)
                self.assertTrue(opt == "--json" or opt in GLOBAL_WITH_VALUE, "unknown global option %s in %s" % (opt, line))
                self.assertIn(opt, top, "the top-level help does not show %s" % opt)
                if opt in GLOBAL_WITH_VALUE:
                    tokens.pop(0)
            area, word = tokens[0], tokens[1]
            rc, text = help_of([area, word, "--help"])
            self.assertEqual(rc, 0, "README shows `%s %s`, which does not exist: %s" % (area, word, line))
            for opt in re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", " ".join(tokens[2:])):
                self.assertIn(opt, text, "README shows %s for `%s %s`, which does not have it" % (opt, area, word))

    def test_the_default_listen_address_the_readme_states_is_the_real_default(self):
        with open(os.path.join(ROOT, "README.md")) as fh:
            readme = fh.read()
        self.assertEqual(config.DEFAULTS["service"]["listen"], "127.0.0.1:8080")
        self.assertIn("`127.0.0.1:8080`", readme)

    def test_the_readme_says_a_token_is_never_a_command_line_value(self):
        with open(os.path.join(ROOT, "README.md")) as fh:
            readme = fh.read()
        self.assertIn("never from a command-line value", readme)
        for line in use_it_commands():
            self.assertNotRegex(line, r"--token\s", "the token must come from a file or the environment: " + line)


if __name__ == "__main__":
    unittest.main(verbosity=1)
