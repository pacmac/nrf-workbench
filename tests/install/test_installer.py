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
from nrfwb_install import features, prefix  # noqa: E402


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
            ("Choice:", target), ("Apply this plan?", "n")])
        self.assertEqual(rc, 3, out)
        self.assertEqual(left, [], "a prompt was never shown:\n" + out)
        self.assertIn("declined; nothing was changed", out)
        self.assertFalse(os.path.exists(target), "declining must change nothing")

    def test_a_bad_prefix_is_refused_and_asked_again(self):
        target = self.fresh_prefix()
        rc, out, left = self.run_interactive(["--features", "core"], [
            ("Choice:", "/usr"), ("Choice:", target), ("Apply this plan?", "n")])
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
