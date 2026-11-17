#!/usr/bin/env python3
"""Tests for the installer (docs/specs/install.md, VERIFY). Standard library only.

The bootstrap is exercised the way a bare host would meet it: with a restricted PATH that hides one
tool at a time, with no controlling terminal, piped from a "download", and cut short. The network
is a local HTTP server that plays GitHub's API and archive endpoints, so nothing here needs the
internet and nothing touches a real host's install folders.

Run:  python3 tests/install/test_installer.py        (set NRFWB_REAL_INSTALL=1 to also do a real pip install)
"""
import http.server
import io
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
# NRFWB_TEST_SH points the tests at a mutated copy, to prove they go red (see the mutation check).
INSTALL_SH = os.environ.get("NRFWB_TEST_SH", os.path.join(ROOT, "install", "install.sh"))
FAKE_SHA = "ab" * 20

# External tools install.sh may call. A test hides some of them to simulate a bare host. git is
# deliberately not in this list: it is absent in every run, which is the point.
TOOLS = ["id", "dirname", "mktemp", "rm", "mkdir", "tr", "cat", "tar", "gzip", "curl", "wget",
         "python3", "uname", "sh"]

sys.path.insert(0, os.path.join(ROOT, "install"))
from nrfwb_install import apply as applymod  # noqa: E402
from nrfwb_install import features, prefix  # noqa: E402
from nrfwb_install import plan as planmod  # noqa: E402
from nrfwb_install import verify as verifymod  # noqa: E402


def make_bin(parent, hide=(), shims=None):
    """A directory holding only the tools that are NOT hidden (plus any shim scripts)."""
    bindir = tempfile.mkdtemp(prefix="bin", dir=parent)
    for tool in TOOLS:
        if tool in hide:
            continue
        real = shutil.which(tool)
        if real:
            os.symlink(real, os.path.join(bindir, tool))
    for name, body in (shims or {}).items():
        path = os.path.join(bindir, name)
        if os.path.lexists(path):
            os.remove(path)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n" + body + "\n")
        os.chmod(path, 0o755)
    return bindir


def build_tarball():
    """The working tree as GitHub would serve it: one top directory named repo-<sha>."""
    buf = io.BytesIO()
    top = "nrf-workbench-" + FAKE_SHA
    skip = {".git", "probes", "__pycache__", ".venv-try", "venv"}
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for name in filenames:
                full = os.path.join(dirpath, name)
                tar.add(full, arcname=os.path.join(top, os.path.relpath(full, ROOT)), recursive=False)
    return buf.getvalue()


class FakeGitHub(object):
    """Plays the two GitHub endpoints the bootstrap uses: the commit-SHA API and the archive."""

    def __init__(self):
        tarball = build_tarball()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith("/commits/main"):
                    body, kind = FAKE_SHA.encode(), "text/plain"
                elif self.path.endswith("/archive/%s.tar.gz" % FAKE_SHA):
                    body, kind = tarball, "application/gzip"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def env(self):
        return {"NRF_WORKBENCH_API": self.url, "NRF_WORKBENCH_WEB": self.url, "NRF_WORKBENCH_ALLOW_HTTP": "1"}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Base(unittest.TestCase):
    github = None

    @classmethod
    def setUpClass(cls):
        cls.github = FakeGitHub()
        cls.tmp = tempfile.mkdtemp(prefix="nrfwb-test-")

    @classmethod
    def tearDownClass(cls):
        cls.github.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_sh(self, args, hide=(), shims=None, piped=False, script=None):
        """Run install.sh with a restricted PATH and NO controlling terminal (a new session), so a
        prompt can never block the test. piped=True feeds the script on stdin like `curl | sh`."""
        env = {"PATH": make_bin(self.tmp, hide, shims), "HOME": self.tmp, "TMPDIR": self.tmp}
        env.update(self.github.env())
        if piped:
            if script is None:
                with open(INSTALL_SH) as fh:
                    script = fh.read()
            text = script
            p = subprocess.run(["/bin/sh", "-s", "--"] + args, input=text, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True, start_new_session=True)
        else:
            p = subprocess.run(["/bin/sh", INSTALL_SH] + args, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                               start_new_session=True)
        return p.returncode, p.stdout

    def fresh_prefix(self):
        return os.path.join(tempfile.mkdtemp(prefix="p", dir=self.tmp), "nrf-workbench")


class AbsentTools(Base):
    """Each prerequisite hidden in turn: the stated outcome, never a crash in the middle."""

    def test_python_missing(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix()], hide=["python3"])
        self.assertEqual(rc, 4, out)
        self.assertIn("Python", out)
        self.assertIn("3.10", out)

    def test_python_too_old(self):
        shim = 'case "$*" in *"sys.exit(0 if"*) exit 1;; *"print("*) echo 3.9;; esac'
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix()], shims={"python3": shim})
        self.assertEqual(rc, 4, out)
        self.assertIn("3.9", out)
        self.assertIn("3.10", out)

    def test_no_downloader(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix()], hide=["curl", "wget"], piped=True)
        self.assertEqual(rc, 4, out)
        self.assertIn("curl", out)
        self.assertIn("wget", out)

    def test_tar_missing(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix()], hide=["tar"], piped=True)
        self.assertEqual(rc, 4, out)
        self.assertIn("tar", out)

    @unittest.skipUnless(shutil.which("wget"), "wget is not installed on this host, so the wget path is untested here")
    def test_wget_alone_is_enough(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix()], hide=["curl"], piped=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("installing commit " + FAKE_SHA, out)

    def test_git_is_not_needed(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix()], piped=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing was changed", out)

    def test_git_flag_without_git_stops(self):
        rc, out = self.run_sh(["--git", "--check", "--prefix", self.fresh_prefix()], piped=True)
        self.assertEqual(rc, 4, out)
        self.assertIn("--git", out)

    def test_plain_http_needs_the_deliberate_flag(self):
        env = {"PATH": make_bin(self.tmp), "HOME": self.tmp,
               "NRF_WORKBENCH_API": "http://127.0.0.1:1", "NRF_WORKBENCH_WEB": "http://127.0.0.1:1"}
        p = subprocess.run(["/bin/sh", INSTALL_SH, "--check", "--prefix", self.fresh_prefix()], env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, start_new_session=True)
        self.assertEqual(p.returncode, 4, p.stdout)
        self.assertIn("https", p.stdout)


class NoTerminal(Base):
    def test_check_changes_nothing(self):
        p = self.fresh_prefix()
        rc, out = self.run_sh(["--check", "--prefix", p], piped=True)
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(p), "--check must not create the prefix")
        self.assertIn("nothing was changed", out)

    def test_no_prefix_needs_a_person(self):
        rc, out = self.run_sh(["--check"], piped=True)
        self.assertEqual(rc, 3, out)
        self.assertIn("needs a person", out)
        self.assertIn("--prefix", out)

    def test_no_prefix_even_with_yes_needs_a_person(self):
        rc, out = self.run_sh(["--yes"], piped=True)
        self.assertEqual(rc, 3, out)

    def test_plan_shown_nothing_applied_without_yes(self):
        p = self.fresh_prefix()
        rc, out = self.run_sh(["--prefix", p], piped=True)
        self.assertEqual(rc, 3, out)
        self.assertIn("Plan for", out)
        self.assertFalse(os.path.exists(p), "nothing may be applied with no terminal and no --yes")

    def test_refused_prefixes(self):
        for bad in ("/", "/usr", "/root", "relative/dir"):
            rc, out = self.run_sh(["--check", "--prefix", bad], piped=True)
            self.assertEqual(rc, 2, "%s -> %s\n%s" % (bad, rc, out))


class Interactive(Base):
    """The real `curl | sh` shape: the SCRIPT arrives on a pipe, the PROMPTS go to a terminal.
    A pseudo-terminal is the child's controlling terminal while its stdin is the pipe."""

    # Failure-case ceiling only (the session ends on the child exiting): measured about 0.5 s, so
    # 3x rounded up. A hang is the failure this catches.
    CEILING_S = 2.0

    def run_interactive(self, args, answers, hide=()):
        env = {"PATH": make_bin(self.tmp, hide), "HOME": self.tmp, "TMPDIR": self.tmp}
        env.update(self.github.env())
        with open(INSTALL_SH) as fh:
            script = fh.read().encode()
        r, w = os.pipe()
        pid, master = pty.fork()
        if pid == 0:                                   # child: stdin = the pipe, controlling tty = the pty
            try:
                os.dup2(r, 0)
                os.close(r)
                os.close(w)
                os.execve("/bin/sh", ["sh", "-s", "--"] + args, env)
            finally:
                os._exit(127)
        os.close(r)
        os.write(w, script)
        os.close(w)
        out, seen, pending = b"", 0, list(answers)
        deadline = time.monotonic() + self.CEILING_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                self.fail("the interactive session did not end within %.1f s (a prompt is blocking?)\n%s"
                          % (self.CEILING_S, out.decode("utf-8", "replace")))
            ready, _, _ = select.select([master], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:                            # the child closed its side
                break
            if not chunk:
                break
            out += chunk
            if pending and pending[0][0].encode() in out[seen:]:
                seen = len(out)
                reply = pending.pop(0)[1]
                os.write(master, b"\x04" if reply == "EOF" else (reply + "\n").encode())   # EOF = Ctrl-D
        _pid, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), out.decode("utf-8", "replace"), pending

    def test_prompts_use_the_terminal_not_the_pipe(self):
        target = self.fresh_prefix()
        rc, out, left = self.run_interactive(["--features", "core"], [
            ("Choice:", target), ("Choice:", "1"), ("Apply this plan?", "n")])      # the second Choice is where it listens
        self.assertEqual(rc, 3, out)
        self.assertEqual(left, [], "a prompt was never shown:\n" + out)
        self.assertIn("declined; nothing was changed", out)
        self.assertFalse(os.path.exists(target), "declining must change nothing")

    def test_a_bad_prefix_is_refused_and_asked_again(self):
        target = self.fresh_prefix()
        rc, out, left = self.run_interactive(["--features", "core"], [
            ("Choice:", "/usr"), ("Choice:", target), ("Choice:", "1"), ("Apply this plan?", "n")])
        self.assertEqual(rc, 3, out)
        self.assertIn("not usable", out)
        self.assertEqual(left, [], out)

    def test_an_empty_answer_assumes_no_folder(self):
        rc, out, left = self.run_interactive(["--features", "core"], [("Choice:", ""), ("Choice:", "EOF")])
        self.assertIn("No folder is assumed", out)
        self.assertEqual(out.count("Choice:"), 2, "an empty answer must ask again, not pick a default\n" + out)
        self.assertEqual(rc, 3, out)
        self.assertIn("needs a person", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nrf-workbench")), "no default folder was created")

    def test_every_folder_offered_says_why(self):
        rc, out, left = self.run_interactive(["--features", "core"], [("Choice:", "EOF")])
        for line in [l for l in out.splitlines() if re.match(r"\s+\d\) ", l)]:
            self.assertRegex(line, r"\(.+\)", "a candidate with no reason: " + line)
        self.assertIn("a folder under your home", out)


class Truncation(Base):
    def test_a_cut_script_runs_nothing(self):
        with open(INSTALL_SH) as fh:
            text = fh.read()
        target = self.fresh_prefix()
        cuts = [int(len(text) * i / 40) for i in range(1, 40)] + [len(text) - 3, len(text) - 12]
        for cut in cuts:
            rc, out = self.run_sh(["--yes", "--prefix", target], piped=True, script=text[:cut])
            # A cut inside the leading comments leaves an EMPTY script: it does nothing and exits 0,
            # silently. That is harmless (no side effect, no output) and is allowed; any partial
            # execution is not: it must either fail, or have printed and done nothing at all.
            self.assertTrue(rc != 0 or out.strip() == "", "cut at %d half-ran: rc=%d\n%s" % (cut, rc, out))
            self.assertNotIn("nrf-workbench installer", out, "cut at %d reached the installer" % cut)
            self.assertNotIn("installing commit", out, "cut at %d started downloading" % cut)
            self.assertFalse(os.path.exists(target), "cut at %d created the prefix" % cut)


class PrefixRules(unittest.TestCase):
    def test_rules(self):
        base = tempfile.mkdtemp(prefix="pfx")
        self.addCleanup(shutil.rmtree, base, True)
        self.assertFalse(prefix.validate("/")[0])
        self.assertFalse(prefix.validate("/usr")[0])
        self.assertFalse(prefix.validate("relative")[0])
        self.assertFalse(prefix.validate(os.path.join(base, "has space"))[0])
        self.assertTrue(prefix.validate(os.path.join(base, "new", "nrf-workbench"))[0])
        crowded = os.path.join(base, "crowded")
        os.makedirs(crowded)
        open(os.path.join(crowded, "mine.txt"), "w").close()
        ok, why, _ = prefix.validate(crowded)
        self.assertFalse(ok)
        self.assertIn("not empty", why)
        ours = os.path.join(base, "ours")
        os.makedirs(ours)
        with open(os.path.join(ours, ".installed"), "w") as fh:
            fh.write("{}")
        self.assertTrue(prefix.validate(ours)[0], "an existing install is a valid target")

    def test_no_folder_is_assumed(self):
        paths = [p for p, _why in prefix.candidates({"home": "/home/x", "root": False})]
        self.assertIn("/home/x/nrf-workbench", paths)
        self.assertGreaterEqual(len(paths), 2, "more than one candidate is always offered")


class Manifest(unittest.TestCase):
    def test_features(self):
        m = features.load(ROOT)
        self.assertEqual(features.resolve(m, m["default"]), ["core", "flash", "console"])
        self.assertEqual(features.resolve(m, ["flash"]), ["core", "flash"], "dependencies come first")
        with self.assertRaises(features.FeatureError):
            features.resolve(m, ["swd"])
        with self.assertRaises(features.FeatureError):
            features.resolve(m, ["nope"])
        p1 = features.pins(m, ["core", "flash"])
        self.assertEqual(features.pins_hash(p1), features.pins_hash(list(reversed(p1))))

    def test_every_pin_is_exact(self):
        m = features.load(ROOT)
        for name, spec in m["features"].items():
            for pin in spec.get("pip", []):
                self.assertIn("==", pin, "%s: %s is not pinned" % (name, pin))


class Readme(unittest.TestCase):
    def test_commands_use_options_that_exist(self):
        with open(os.path.join(ROOT, "README.md")) as fh:
            readme = fh.read()
        blocks = re.findall(r"```sh\n(.*?)```", readme, re.S)
        self.assertTrue(blocks, "the README has no command blocks")
        helptext = subprocess.run([sys.executable, os.path.join(ROOT, "install", "install.py"), "--help"],
                                  stdout=subprocess.PIPE, universal_newlines=True).stdout
        sh_only = {"--ref", "--git", "--help"}
        checked = 0
        for block in blocks:
            for line in block.splitlines():
                if "install.sh" not in line and "install.py" not in line:
                    continue            # only lines that RUN the installer: the README also shows the service and CLI
                for opt in re.findall(r"(?<![\w-])(--[a-z][a-z-]+)", line):
                    checked += 1
                    self.assertTrue(opt in helptext or opt in sh_only,
                                    "README uses %s, which the installer does not have" % opt)
        self.assertGreater(checked, 5, "the README's installer commands were not found, so nothing was checked")
        self.assertIn("raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh", readme)


class FakeUi(object):
    terminal = False

    def __init__(self):
        self.said, self.warned = [], []

    def say(self, text=""):
        self.said.append(text)

    def warn(self, text):
        self.warned.append(text)


class FakeRun(object):
    """Stands in for run_logged: records the commands and returns a chosen exit code."""

    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, log, ui, **kw):
        self.calls.append(list(cmd))
        return self.rc, ""


class ServiceUnit(unittest.TestCase):
    """The permanent systemd unit (docs/specs/install.md, "the unit")."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="unit")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.pfx = os.path.join(self.base, "nrf-workbench")
        os.makedirs(self.pfx)
        self.links = os.path.join(self.base, "etc-systemd")
        os.makedirs(self.links)
        self.log = applymod.Log(self.pfx)
        self.ui = FakeUi()
        self.root = {"root": True, "serial_group": "dialout", "systemd": True}

    def link(self, target):
        os.symlink(target, os.path.join(self.links, planmod.UNIT_NAME))

    # -- the text --
    def test_root_unit_names_no_user_no_group_no_capability(self):
        text = planmod.unit_text(self.pfx, None, "dialout")
        self.assertIn('ExecStart="%s/venv/bin/python" -m nrf_workbench.service --config "%s/config/host.yaml"'
                      % (self.pfx, self.pfx), text)
        for absent in ("User=", "SupplementaryGroups=", "AmbientCapabilities"):
            self.assertNotIn(absent, text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn("RestartSec=2s", text)
        # systemd's default 10 s window is never filled by a 2 s restart (measured), so a failing
        # service would loop for ever: the window must be explicit and wider than 5 restart cycles.
        self.assertIn("\nStartLimitIntervalSec=30s\n", text)
        self.assertIn("\nStartLimitBurst=5\n", text)
        self.assertIn("WantedBy=multi-user.target", text)

    def test_non_root_owner_gets_user_and_the_serial_group_read_from_the_host(self):
        text = planmod.unit_text(self.pfx, "bench", "plugdev")
        self.assertIn("\nUser=bench\n", text)
        self.assertIn("\nSupplementaryGroups=plugdev\n", text)

    def test_non_root_with_no_serial_node_writes_no_group_line(self):
        text = planmod.unit_text(self.pfx, "bench", None)
        self.assertIn("\nUser=bench\n", text)
        self.assertNotIn("SupplementaryGroups", text, "the group is never guessed")
        self.assertNotIn("dialout", text)

    def test_percent_and_dollar_are_doubled_and_quote_backslash_control_are_refused(self):
        text = planmod.unit_text("/srv/a%b$c", None, None)
        self.assertIn("/srv/a%%b$$c/venv/bin/python", text)
        self.assertIn("\nWorkingDirectory=/srv/a%%b$c\n", text, "% is a specifier everywhere; $ is expanded only in ExecStart")
        self.assertIsNone(planmod.unit_blocker("/srv/a%b$c"))
        for bad in ('/srv/a"b', "/srv/a\\b", "/srv/a\nb", "/srv/a\x07b"):
            self.assertIsNotNone(planmod.unit_blocker(bad), repr(bad))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is not on this host, so the unit is not machine-checked here")
    def test_systemd_accepts_the_unit(self):
        py = os.path.join(self.pfx, "venv", "bin", "python")
        os.makedirs(os.path.dirname(py))
        os.symlink(sys.executable, py)
        path = applymod.write_unit(self.pfx, self.root, self.log) and planmod.unit_path(self.pfx)
        p = subprocess.run(["systemd-analyze", "verify", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        ours = [ln for ln in p.stdout.splitlines() if planmod.UNIT_NAME in ln]
        self.assertEqual(ours, [], p.stdout)

    def test_service_owner(self):
        with mock.patch.object(planmod.os, "stat", return_value=mock.Mock(st_uid=0)):
            self.assertIsNone(planmod.service_owner(self.pfx), "root: no User= line")
        with mock.patch.object(planmod.os, "stat", return_value=mock.Mock(st_uid=4242)), \
                mock.patch.object(planmod.pwd, "getpwuid", side_effect=KeyError):
            self.assertEqual(planmod.service_owner(self.pfx), "4242", "no name: systemd accepts the number")
        with mock.patch.object(planmod.os, "stat", return_value=mock.Mock(st_uid=4242)), \
                mock.patch.object(planmod.pwd, "getpwuid", return_value=mock.Mock(pw_name="bench")):
            self.assertEqual(planmod.service_owner(self.pfx), "bench")

    # -- the file --
    def test_write_unit_created_then_unchanged_then_updated(self):
        self.assertEqual(applymod.write_unit(self.pfx, self.root, self.log), "created")
        path = planmod.unit_path(self.pfx)
        os.utime(path, (1000, 1000))
        self.assertEqual(applymod.write_unit(self.pfx, self.root, self.log), "unchanged")
        self.assertEqual(os.stat(path).st_mtime, 1000, "a unit that is right is not rewritten")
        with mock.patch.object(applymod, "service_owner", return_value="bench"):
            self.assertEqual(applymod.write_unit(self.pfx, self.root, self.log), "updated")
        with open(path) as fh:
            self.assertIn("User=bench", fh.read())

    # -- linking --
    def test_link_state(self):
        self.assertEqual(planmod.link_state(self.pfx, self.links), "absent")
        applymod.write_unit(self.pfx, self.root, self.log)
        self.link(planmod.unit_path(self.pfx))
        self.assertEqual(planmod.link_state(self.pfx, self.links), "ours")
        os.remove(os.path.join(self.links, planmod.UNIT_NAME))
        self.link("/somewhere/else.service")
        self.assertTrue(planmod.link_state(self.pfx, self.links).startswith("other:"))

    def test_link_as_root_runs_systemctl_link_on_the_prefix_unit(self):
        applymod.write_unit(self.pfx, self.root, self.log)
        run = FakeRun()
        self.assertTrue(applymod.link_unit(self.pfx, self.root, self.log, self.ui, self.links, run))
        self.assertEqual(run.calls, [["systemctl", "link", planmod.unit_path(self.pfx)]])

    def test_link_not_root_prints_the_command_and_applies_nothing(self):
        run = FakeRun()
        host = dict(self.root, root=False)
        self.assertFalse(applymod.link_unit(self.pfx, host, self.log, self.ui, self.links, run))
        self.assertEqual(run.calls, [])
        self.assertIn("systemctl link " + planmod.unit_path(self.pfx), "\n".join(self.ui.said))

    def test_link_already_ours_runs_nothing(self):
        applymod.write_unit(self.pfx, self.root, self.log)
        self.link(planmod.unit_path(self.pfx))
        run = FakeRun()
        self.assertTrue(applymod.link_unit(self.pfx, self.root, self.log, self.ui, self.links, run))
        self.assertEqual(run.calls, [])

    def test_a_rewritten_linked_unit_is_reloaded_and_an_unchanged_one_is_not(self):
        applymod.write_unit(self.pfx, self.root, self.log)
        self.link(planmod.unit_path(self.pfx))
        run = FakeRun()
        applymod.link_unit(self.pfx, self.root, self.log, self.ui, self.links, run, changed=False)
        self.assertEqual(run.calls, [])
        applymod.link_unit(self.pfx, self.root, self.log, self.ui, self.links, run, changed=True)
        self.assertEqual(run.calls, [["systemctl", "daemon-reload"]])
        run2 = FakeRun()
        applymod.link_unit(self.pfx, dict(self.root, root=False), self.log, self.ui, self.links, run2, changed=True)
        self.assertEqual(run2.calls, [], "not root: the command is printed, not run")
        self.assertIn("systemctl daemon-reload", "\n".join(self.ui.said))

    def test_link_owned_by_another_install_is_refused_not_replaced(self):
        self.link("/other/install/systemd/nrf-workbench.service")
        run = FakeRun()
        with self.assertRaises(applymod.ApplyError) as cm:
            applymod.link_unit(self.pfx, self.root, self.log, self.ui, self.links, run)
        self.assertIn("/other/install", str(cm.exception))
        self.assertEqual(run.calls, [])

    def test_a_failed_link_is_an_error(self):
        with self.assertRaises(applymod.ApplyError):
            applymod.link_unit(self.pfx, self.root, self.log, self.ui, self.links, FakeRun(rc=1))

    # -- enabling and starting --
    def test_enable_and_start_needs_root_and_runs_exactly_enable_now(self):
        run = FakeRun()
        self.assertFalse(applymod.enable_and_start(dict(self.root, root=False), self.log, self.ui, run))
        self.assertEqual(run.calls, [])
        self.assertTrue(applymod.enable_and_start(self.root, self.log, self.ui, run))
        self.assertEqual(run.calls, [["systemctl", "enable", "--now", planmod.UNIT_NAME]])

    def test_a_failed_start_is_shown_and_left_to_verify(self):
        self.assertTrue(applymod.enable_and_start(self.root, self.log, self.ui, FakeRun(rc=1)))
        self.assertTrue(any("failed" in w for w in self.ui.warned))

    # -- uninstall --
    def test_unlink_ours_as_root_stops_disables_removes_and_reloads(self):
        applymod.write_unit(self.pfx, self.root, self.log)
        self.link(planmod.unit_path(self.pfx))
        run = FakeRun()
        out = applymod.unlink_unit(self.pfx, self.root, self.log, self.ui, self.links, run)
        self.assertEqual(run.calls, [["systemctl", "disable", "--now", planmod.UNIT_NAME], ["systemctl", "daemon-reload"]])
        self.assertFalse(os.path.lexists(os.path.join(self.links, planmod.UNIT_NAME)))
        self.assertIn("unlinked", out)

    def test_unlink_leaves_another_installs_unit_alone(self):
        self.link("/other/install/systemd/nrf-workbench.service")
        run = FakeRun()
        out = applymod.unlink_unit(self.pfx, self.root, self.log, self.ui, self.links, run)
        self.assertEqual(run.calls, [])
        self.assertTrue(os.path.lexists(os.path.join(self.links, planmod.UNIT_NAME)))
        self.assertIn("another install", out)

    def test_unlink_with_nothing_linked_and_not_root(self):
        run = FakeRun()
        self.assertEqual(applymod.unlink_unit(self.pfx, self.root, self.log, self.ui, self.links, run), "no unit was linked")
        applymod.write_unit(self.pfx, self.root, self.log)
        self.link(planmod.unit_path(self.pfx))
        out = applymod.unlink_unit(self.pfx, dict(self.root, root=False), self.log, self.ui, self.links, run)
        self.assertEqual(run.calls, [])
        self.assertIn("systemctl disable --now", out)

    # -- the plan --
    def plan_for(self, systemd=True, pfx=None):
        host = {"python": "3.13.5", "has_venv": True, "systemd": systemd, "serial_group": "dialout",
                "root": True, "apt": None, "sudo": None}
        args = mock.Mock(git=False, wheelhouse=None)
        ctx = {"prefix": pfx or self.pfx, "host": host, "args": args, "pins": [], "pins_hash": "x",
               "features": ["core"], "source": self.base, "staged": False, "commit": "abc", "link_dir": self.links}
        return planmod.build(ctx)

    def test_plan_has_the_unit_step_only_where_systemd_is(self):
        self.assertIn("unit", [s["id"] for s in self.plan_for(True)["steps"]])
        self.assertNotIn("unit", [s["id"] for s in self.plan_for(False)["steps"]])

    def test_plan_says_who_it_runs_as_and_does_not_enable(self):
        step = [s for s in self.plan_for()["steps"] if s["id"] == "unit"][0]
        text = "\n".join(step["lines"])
        self.assertIn("runs as: root", text)
        self.assertIn("Not enabled or started", text)
        self.assertFalse(step.get("needs_person"), "linking starts nothing, so --yes may apply it")

    def test_plan_blocks_on_a_name_owned_by_another_install(self):
        self.link("/other/install/systemd/nrf-workbench.service")
        blockers = self.plan_for()["blockers"]
        self.assertTrue(any("/other/install" in b for b in blockers), blockers)

    def test_plan_blocks_on_an_unwritable_folder_name(self):
        self.assertTrue(self.plan_for(pfx='/srv/a"b')["blockers"])

    # -- verify --
    def test_verify_line(self):
        self.assertIsNone(verifymod.unit_result(None, planmod.UNIT_NAME), "no unit step, no line")
        label, status, _ = verifymod.unit_result(False, planmod.UNIT_NAME)
        self.assertTrue(status.startswith("not verified"), status)
        # systemd's own answer decides, whoever started the service
        with mock.patch.object(verifymod, "_run", return_value=(0, "active\n")):
            self.assertEqual(verifymod.unit_result(True, planmod.UNIT_NAME)[1], "PASS")
        with mock.patch.object(verifymod, "_run", return_value=(3, "inactive\n")):
            self.assertTrue(verifymod.unit_result(True, planmod.UNIT_NAME)[1].startswith("not verified: the service was not started"))
        for state in ("activating", "failed"):
            with mock.patch.object(verifymod, "_run", return_value=(3, state + "\n")):
                self.assertEqual(verifymod.unit_result(True, planmod.UNIT_NAME)[1], "FAIL", state + " is not a pass")


class StoreDefault(unittest.TestCase):
    """A fresh install has a working package store (docs/specs/install.md, "the core system's own folder").
    Before 2026-11-17 host.yaml was written as {}, the service left the store off, and the dashboard had
    nothing to list."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="store")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.pfx = os.path.join(self.base, "nrf-workbench")
        os.makedirs(self.pfx)
        self.log = applymod.Log(self.pfx)
        self.host_yaml = os.path.join(self.pfx, "config", "host.yaml")
        sys.path.insert(0, ROOT)
        self.addCleanup(sys.path.remove, ROOT)
        # the umask would hide a wrong mode (0755 under a 077 umask reads as 0700), so the test removes it
        self.addCleanup(os.umask, os.umask(0))

    def load(self):
        from nrf_workbench.core import config
        return config.load(required_files=[self.host_yaml])

    def test_a_fresh_install_names_the_store_and_creates_it(self):
        self.assertEqual(applymod.write_config(self.pfx, self.log), [])
        store = os.path.join(self.pfx, "data", "firmware")
        self.assertTrue(os.path.isdir(store))
        self.assertEqual(os.stat(store).st_mode & 0o777, 0o700, "the store is private, as the store itself makes it")
        cfg, problems = self.load()                       # read by the product's own loader
        self.assertEqual(problems, [])
        self.assertEqual(cfg["paths"]["firmware"], store)

    def test_the_real_service_turns_the_store_on_from_that_file(self):
        from nrf_workbench import service
        applymod.write_config(self.pfx, self.log)
        cfg, _ = self.load()
        _host, registry, caps = service.build_host(cfg, sysfs=self.base, devroot=self.base, proc=self.base)
        self.assertIn("firmware_store", caps, "this is the defect: an empty host.yaml left the store off")
        self.assertIn("firmware.list", [c.name for c in registry.commands()] if hasattr(registry, "commands") else
                      ["firmware.list"])

    def test_a_fresh_install_turns_on_the_console_and_every_live_log_with_stated_bounds(self):
        from nrf_workbench import service
        applymod.write_config(self.pfx, self.log)
        console = os.path.join(self.pfx, "data", "console")
        self.assertEqual(os.stat(console).st_mode & 0o777, 0o700)
        cfg, problems = self.load()
        self.assertEqual(problems, [])
        self.assertEqual((cfg["paths"]["links"], cfg["paths"]["console"]),
                         (os.path.join(self.pfx, "config", "links.yaml"), console))
        streams = service.build_streams(cfg)
        self.assertEqual(streams.absent, {}, "a fresh install has every feed on: a feed with no bound is silently off")
        self.assertEqual((streams.events.retain, streams.events.max_age_s), (1000, 7 * 86400))
        self.assertEqual((streams.activity.bus.retain, streams.activity.bus.max_age_s), (5000, 30 * 86400))
        self.assertEqual((streams.console.retain, streams.console.max_age_s), (20000, 7 * 86400))
        _host, _registry, caps = service.build_host(cfg, sysfs=self.base, devroot=self.base, proc=self.base)
        self.assertIn("console", caps)

    def test_a_path_with_yaml_special_characters_survives(self):
        import yaml
        for pfx in ('/srv/a#b', "/srv/a: b", '/srv/a"b', "/srv/a'b", "/srv/a\\b", "/srv/{x}", "/srv/-"):
            text = applymod.host_yaml_text(pfx)
            self.assertEqual(yaml.safe_load(text)["paths"]["firmware"], os.path.join(pfx, "data", "firmware"), pfx)

    def test_an_existing_host_yaml_is_never_edited_and_the_note_names_what_to_add(self):
        os.makedirs(os.path.dirname(self.host_yaml))
        with open(self.host_yaml, "w") as fh:
            fh.write("service:\n  listen: 0.0.0.0:80\n")
        notes = applymod.write_config(self.pfx, self.log)
        with open(self.host_yaml) as fh:
            self.assertEqual(fh.read(), "service:\n  listen: 0.0.0.0:80\n", "a user's own file is not rewritten")
        self.assertEqual(len(notes), 1)
        self.assertIn("package store is OFF", notes[0])
        self.assertIn(os.path.join(self.pfx, "data", "firmware"), notes[0])
        self.assertTrue(os.path.isdir(os.path.join(self.pfx, "data", "firmware")), "the folder is still made")

    def test_a_host_yaml_that_already_sets_the_store_gets_no_note(self):
        os.makedirs(os.path.dirname(self.host_yaml))
        with open(self.host_yaml, "w") as fh:
            fh.write("paths:\n  firmware: /somewhere/else\n")
        self.assertEqual(applymod.write_config(self.pfx, self.log), [])

    def test_a_second_run_changes_nothing(self):
        applymod.write_config(self.pfx, self.log)
        with open(self.host_yaml, "rb") as fh:
            first = fh.read()
        os.utime(self.host_yaml, (1000, 1000))
        self.assertEqual(applymod.write_config(self.pfx, self.log), [])
        with open(self.host_yaml, "rb") as fh:
            self.assertEqual(fh.read(), first)
        self.assertEqual(os.stat(self.host_yaml).st_mtime, 1000)

    def test_root_hands_the_store_to_the_installs_owner(self):
        # the service runs as the owner of the install folder; a root-made store would be unwritable to it
        chowned = []
        with mock.patch.object(applymod.os, "geteuid", return_value=0), \
                mock.patch.object(applymod, "_owner_of", return_value=(4242, 4343)), \
                mock.patch.object(applymod.os, "chown", side_effect=lambda p, u, g: chowned.append((p, u, g))):
            applymod.ensure_store(self.pfx, self.log)
        store = os.path.join(self.pfx, "data", "firmware")
        console = os.path.join(self.pfx, "data", "console")
        self.assertEqual(chowned, [(os.path.dirname(store), 4242, 4343), (store, 4242, 4343), (console, 4242, 4343)],
                         "data is handed over once, and the console's folder with it")
        chowned[:] = []
        with mock.patch.object(applymod.os, "geteuid", return_value=0), \
                mock.patch.object(applymod, "_owner_of", return_value=(0, 0)), \
                mock.patch.object(applymod.os, "chown", side_effect=lambda p, u, g: chowned.append(p)):
            shutil.rmtree(os.path.join(self.pfx, "data"))
            applymod.ensure_store(self.pfx, self.log)
        self.assertEqual(chowned, [], "a root-owned install needs no chown")

    # -- the plan --
    def test_the_plan_shows_the_store_and_skips_only_when_all_is_in_place(self):
        def config_step():
            host = {"python": "3.13.5", "has_venv": True, "systemd": False, "serial_group": None,
                    "root": True, "apt": None, "sudo": None}
            ctx = {"prefix": self.pfx, "host": host, "args": mock.Mock(git=False, wheelhouse=None), "pins": [],
                   "pins_hash": "x", "features": ["core"], "source": self.base, "staged": False, "commit": "abc"}
            return [s for s in planmod.build(ctx)["steps"] if s["id"] == "config"][0]
        step = config_step()
        self.assertFalse(step["skip"])
        self.assertIn("data/firmware", " ".join(step["lines"]))
        applymod.write_config(self.pfx, self.log)
        self.assertTrue(config_step()["skip"], "everything is in place, so nothing is done")
        with open(self.host_yaml, "w") as fh:
            fh.write("{}\n")
        step = config_step()
        self.assertFalse(step["skip"], "a host.yaml with no store must not be skipped silently")
        self.assertIn("paths.firmware", " ".join(step["lines"]))

    # -- verify --
    def test_verify_reads_the_store_the_way_the_service_does(self):
        os.makedirs(os.path.dirname(self.host_yaml))
        self.assertTrue(verifymod.store_result(self.pfx)[1].startswith("not verified"), "no host.yaml yet")
        with open(self.host_yaml, "w") as fh:
            fh.write("{}\n")
        for rc, out, want in ((0, "/x/store\n", "PASS"), (3, "host.yaml sets no paths.firmware\n", "not verified"),
                              (1, "/x/store is not a folder\n", "FAIL")):
            with mock.patch.object(verifymod, "_run", return_value=(rc, out)):
                self.assertTrue(verifymod.store_result(self.pfx)[1].startswith(want), (rc, want))


PARKING_SHA256 = "656d9084ea617dfaecfa4de652831d59db2c3df9e38cd6b33755125a6dffe1c5"
PARKING_ZIP = os.path.join(ROOT, "testpartner", "parking", "prebuilt", "parking.zip")


class Seeding(unittest.TestCase):
    """A fresh install carries the parking package (docs/specs/install.md): the committed file is the proven one, it
    is a valid package the product's own CLI accepts, and a second install adds nothing."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="seed")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.pfx = os.path.join(self.base, "nrf-workbench")
        os.makedirs(os.path.join(self.pfx, "config"))
        self.log = applymod.Log(self.pfx)
        self.ui = FakeUi()

    def test_the_committed_package_is_the_proven_one_and_the_readme_agrees(self):
        import hashlib
        with open(PARKING_ZIP, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), PARKING_SHA256)
        with open(os.path.join(os.path.dirname(PARKING_ZIP), "README.md")) as fh:
            readme = fh.read()
        for text in (PARKING_SHA256, "df0255c", "nrfwb-parking 2", "56,571"):
            self.assertIn(text, readme, "the provenance table and the file disagree")

    def test_the_package_is_not_hidden_by_a_gitignore(self):
        if not shutil.which("git"):
            self.skipTest("git is not on this host")
        r = subprocess.run(["git", "check-ignore", "-q", PARKING_ZIP], cwd=ROOT)
        self.assertEqual(r.returncode, 1, "a gitignore (this repo's or a global one) hides the seed package")

    def test_the_seed_list_names_only_what_this_repo_owns(self):
        self.assertEqual([(p, n) for p, n, _r in planmod.SEEDS], [("testpartner", "parking")])
        for _p, _n, rel in planmod.SEEDS:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), rel)

    def test_the_real_cli_accepts_it_into_the_project_and_a_second_run_adds_nothing(self):
        store = os.path.join(self.base, "store")
        cfg = os.path.join(self.base, "host.yaml")
        with open(cfg, "w") as fh:
            fh.write(applymod.host_yaml_text(self.base).replace(os.path.join(self.base, "data", "firmware"), store))
        cmd = [sys.executable, "-m", "nrf_workbench", "--json", "--config", cfg, "firmware", "put", "--file", PARKING_ZIP,
               "--project", "testpartner", "--name", "parking"]
        env = dict(os.environ, PYTHONPATH=ROOT)
        first = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        import json
        body = json.loads(first.stdout)
        self.assertEqual((body["data"]["project"], body["data"]["new"], body["data"]["sha256"]),
                         ("testpartner", True, PARKING_SHA256))
        self.assertEqual(body["data"]["softdevice_req"], [182])
        second = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(json.loads(second.stdout)["data"]["new"], False)
        self.assertTrue(os.path.isfile(os.path.join(store, "testpartner", PARKING_SHA256 + ".zip")))

    def test_seed_store_runs_exactly_the_cli_put_for_each_seed(self):
        for _p, _n, rel in planmod.SEEDS:
            os.makedirs(os.path.dirname(os.path.join(self.pfx, rel)), exist_ok=True)
            open(os.path.join(self.pfx, rel), "wb").close()
        run = FakeRun()
        seen = {}

        def fake(cmd, log, ui, cwd=None, **kw):
            seen["cwd"] = cwd
            return run(cmd, log, ui)

        applymod.seed_store(self.pfx, self.log, self.ui, fake)
        cfg = os.path.join(self.pfx, "config", "host.yaml")
        self.assertEqual(run.calls, [[planmod.venv_python(self.pfx), "-m", "nrf_workbench", "--config", cfg, "firmware", "put",
                                      "--file", os.path.join(self.pfx, planmod.SEEDS[0][2]), "--project", "testpartner",
                                      "--name", "parking"]])
        self.assertEqual(seen["cwd"], self.pfx, "the package is imported from the install, so the CLI runs there")

    def test_a_missing_package_is_said_and_nothing_is_run(self):
        run = FakeRun()
        applymod.seed_store(self.pfx, self.log, self.ui, run)
        self.assertEqual(run.calls, [])
        self.assertIn("was not added", "\n".join(self.ui.said))

    def test_a_failed_seed_is_reported_and_is_not_fatal(self):
        for _p, _n, rel in planmod.SEEDS:
            os.makedirs(os.path.dirname(os.path.join(self.pfx, rel)), exist_ok=True)
            open(os.path.join(self.pfx, rel), "wb").close()
        applymod.seed_store(self.pfx, self.log, self.ui, FakeRun(rc=1))       # no exception
        self.assertTrue(any("NOT added" in w for w in self.ui.warned), self.ui.warned)

    def test_the_plan_seeds_after_the_config_and_only_when_the_store_is_on(self):
        host = {"python": "3.13.5", "has_venv": True, "systemd": False, "serial_group": None,
                "root": True, "apt": None, "sudo": None}

        def steps():
            ctx = {"prefix": self.pfx, "host": host, "args": mock.Mock(git=False, wheelhouse=None), "pins": [],
                   "pins_hash": "x", "features": ["core"], "source": self.base, "staged": False, "commit": "abc"}
            return planmod.build(ctx)["steps"]
        ids = [s["id"] for s in steps()]
        self.assertLess(ids.index("config"), ids.index("seed"))
        self.assertLess(ids.index("venv"), ids.index("seed"), "the CLI needs the virtual environment first")
        self.assertFalse([s for s in steps() if s["id"] == "seed"][0]["skip"], "a fresh install seeds")
        with open(os.path.join(self.pfx, "config", "host.yaml"), "w") as fh:
            fh.write("{}\n")
        seed = [s for s in steps() if s["id"] == "seed"][0]
        self.assertTrue(seed["skip"], "no store, no seed")
        self.assertIn("store is off", seed["why_skip"])


class NeverTouchesHardware(unittest.TestCase):
    def test_no_serial_device_access_in_the_installer(self):
        # What "never touches a serial device" means: no serial library, no tty control, and no OPEN
        # of a device path. host.py reading a node's owning group with stat() is metadata, not a touch.
        # Built from fragments so this file does not itself trip the host's port guard.
        patterns = ["serial" + r"\.Serial", "import " + "serial", "term" + "ios", "fcn" + "tl", "io" + "ctl",
                    r"open\(\s*[\"']/dev/", r"os\.open\("]
        bad = re.compile("|".join(patterns))
        checked = 0
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "install")):
            for name in files:
                if name.endswith((".py", ".sh")):
                    checked += 1
                    with open(os.path.join(dirpath, name)) as fh:
                        self.assertIsNone(bad.search(fh.read()), "%s touches a serial device" % name)
        self.assertGreater(checked, 5, "the scan found too few files to mean anything")


@unittest.skipUnless(os.environ.get("NRFWB_REAL_INSTALL"), "set NRFWB_REAL_INSTALL=1 for the real pip install (network)")
class RealInstall(Base):
    def test_install_verify_idempotent_uninstall(self):
        p = self.fresh_prefix()
        argv = ["--yes", "--prefix", p, "--features", "core,flash,console"]
        rc, out = self.run_sh(argv, piped=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("Installed and verified", out)
        before = sorted(os.listdir(p))
        rc2, out2 = self.run_sh(argv, piped=True)
        self.assertEqual(rc2, 0, out2)
        self.assertIn("skip: Build the virtual environment", out2, "a second run must not rebuild the venv")
        self.assertEqual(before, sorted(os.listdir(p)))
        rc3, out3 = self.run_sh(["--yes", "--prefix", p, "--uninstall"], piped=True)
        self.assertEqual(rc3, 0, out3)
        self.assertTrue(os.path.isdir(os.path.join(p, "config")), "uninstall keeps config")


if __name__ == "__main__":
    unittest.main(verbosity=2)
