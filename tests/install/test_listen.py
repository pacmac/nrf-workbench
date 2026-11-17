#!/usr/bin/env python3
"""Tests for the installer's listen choice (docs/specs/install.md, "the service: where it listens").

Standard library only, like the installer. The product's own address parser is compared with the installer's copy on
a table, the port check runs against a real listening socket and a fake /proc, the token is created for real in a
temporary folder, and the `--check` plan is read from a real run of install.sh.
Run:  python3 tests/install/test_listen.py
"""
import os
import socket
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "install"))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from nrfwb_install import apply as applymod  # noqa: E402
from nrfwb_install import cli  # noqa: E402
from nrfwb_install import listen as L  # noqa: E402
from nrfwb_install import plan as planmod  # noqa: E402
from nrfwb_install import verify as verifymod  # noqa: E402
from nrfwb_install.ui import NeedsPerson  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from test_installer import Base  # noqa: E402


class FakeUi(object):
    def __init__(self, terminal, answers=()):
        self.terminal, self.said, self.answers = terminal, [], list(answers)

    def say(self, text=""):
        self.said.append(text)

    def warn(self, text):
        self.said.append(text)

    def ask(self, question):
        if not self.answers:
            raise NeedsPerson(question)
        return self.answers.pop(0)


class Args(object):
    def __init__(self, listen=None, access=None):
        self.listen, self.access = listen, access


class Log(object):
    def __init__(self):
        self.lines = []

    def stamp(self, text):
        self.lines.append(text)


class Dirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pfx = os.path.join(self.tmp.name, "nrf-workbench")
        os.makedirs(os.path.join(self.pfx, "config"))
        self.cfg = os.path.join(self.pfx, "config", "host.yaml")

    def write_host_yaml(self, text):
        with open(self.cfg, "w") as fh:
            fh.write(text)


class Addresses(unittest.TestCase):
    CASES = ["127.0.0.1:8080", "0.0.0.0:80", "[::1]:8080", "[::]:80", "host.example:9", "localhost:1", ":80", "x", "",
             "1.2.3.4:0", "1.2.3.4:65536", "1.2.3.4:65535", "1.2.3.4:abc", "[::1]8080", "[::1:80", "  10.0.0.1:81  ", None, 80]

    def test_the_installers_parser_agrees_with_the_products_on_every_case(self):
        # The installer cannot import the product (it runs first), so its copy is held to the original here.
        for text in self.CASES:
            try:
                want = cfgmod.parse_listen(text)
            except ValueError:
                want = None
            try:
                got = L.parse(text)
            except ValueError:
                got = None
            self.assertEqual(got, want, repr(text))

    def test_loopback_is_decided_like_the_products(self):
        for host in ("127.0.0.1", "127.9.9.9", "::1", "localhost", "0.0.0.0", "::", "192.168.0.97", "example.org", "[::1]"):
            self.assertEqual(L.is_loopback(host), cfgmod.is_loopback(host.strip("[]") if host.startswith("[") else host), host)

    def test_join_brackets_only_an_ipv6_address(self):
        self.assertEqual(L.join("::1", 80), "[::1]:80")
        self.assertEqual(L.join("0.0.0.0", 80), "0.0.0.0:80")

    def test_a_client_reaches_a_wildcard_bind_through_loopback(self):
        self.assertEqual((L.client_address("0.0.0.0"), L.client_address("::"), L.client_address("[::1]"),
                          L.client_address("192.168.0.9")), ("127.0.0.1", "::1", "::1", "192.168.0.9"))


class Privilege(Dirs):
    def test_only_a_non_root_service_below_the_limit_needs_the_capability(self):
        table = [(80, False, 1024, True), (1023, False, 1024, True), (1024, False, 1024, False), (8080, False, 1024, False),
                 (80, True, 1024, False), (80, False, 0, False), (80, False, None, False)]
        for port, root, limit, want in table:
            self.assertEqual(L.needs_bind_capability(port, root, limit), want, (port, root, limit))

    def test_the_limit_is_read_from_the_machine_and_an_unreadable_one_is_none(self):
        path = os.path.join(self.tmp.name, "start")
        with open(path, "w") as fh:
            fh.write("80\n")
        self.assertEqual(L.unprivileged_start(path), 80)
        self.assertIsNone(L.unprivileged_start(os.path.join(self.tmp.name, "missing")))
        with open(path, "w") as fh:
            fh.write("nope")
        self.assertIsNone(L.unprivileged_start(path))


class PortState(Dirs):
    def test_a_real_listener_is_reported_with_this_process_as_the_holder(self):
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        st = L.port_state(port)
        self.assertTrue(st["listening"])
        self.assertIn("pid %d:" % os.getpid(), st["holder"] or "", st)

    def test_a_port_nobody_holds_is_free(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(L.port_state(port), {"listening": False, "holder": None, "visible": True})

    def test_a_listener_whose_owner_cannot_be_seen_is_never_reported_free(self):
        proc = os.path.join(self.tmp.name, "proc")
        os.makedirs(os.path.join(proc, "net"))
        with open(os.path.join(proc, "net", "tcp"), "w") as fh:
            fh.write("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                     "   0: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 99999 1 0 0 100 0 0 10 0\n")
        st = L.port_state(80, proc)
        self.assertEqual((st["listening"], st["holder"], st["visible"]), (True, None, False))
        self.assertIn("holder not visible", L.state_text(80, st, True, 1024))

    def test_only_the_listen_state_counts_not_an_established_connection(self):
        proc = os.path.join(self.tmp.name, "proc")
        os.makedirs(os.path.join(proc, "net"))
        with open(os.path.join(proc, "net", "tcp"), "w") as fh:
            fh.write("header\n   0: 0100007F:0050 0100007F:9999 01 00000000:00000000 00:00000000 00000000     0        0 5 1 0 0 100 0 0 10 0\n")
        self.assertFalse(L.port_state(80, proc)["listening"])

    def test_the_offer_line_says_what_the_port_needs(self):
        free = {"listening": False, "holder": None, "visible": True}
        self.assertEqual(L.state_text(8080, free, False, 1024), "port 8080 is free now")
        self.assertIn("CAP_NET_BIND_SERVICE", L.state_text(80, free, False, 1024))
        self.assertNotIn("CAP_NET_BIND_SERVICE", L.state_text(80, free, True, 1024))
        used = {"listening": True, "holder": "pid 7: nginx", "visible": True}
        self.assertIn("IN USE (pid 7: nginx)", L.state_text(80, used, True, 1024))


class Recorded(unittest.TestCase):
    def test_it_reads_only_the_service_block(self):
        text = ('paths:\n  listen: "no"\nservice:\n  listen: "0.0.0.0:80"   # the network\n  token_file: /x/token\n  access: open-read\n'
                'streams:\n  listen: nope\n')
        self.assertEqual(L.recorded(text), {"listen": "0.0.0.0:80", "token_file": "/x/token", "access": "open-read"})

    def test_quotes_comments_and_blank_lines(self):
        self.assertEqual(L.recorded("# top\nservice:\n\n  # c\n  listen: '127.0.0.1:9'\n"), {"listen": "127.0.0.1:9"})

    def test_flow_style_and_absence_read_as_nothing_recorded(self):
        self.assertEqual(L.recorded("service: {listen: 0.0.0.0:80}\n"), {})
        self.assertEqual(L.recorded("paths:\n  firmware: /x\n"), {})
        self.assertEqual(L.recorded(""), {})


class Token(Dirs):
    def test_created_owner_only_with_a_value_and_never_overwritten(self):
        path = os.path.join(self.pfx, "config", "token")
        self.addCleanup(os.umask, os.umask(0))                       # a umask must not be what makes it private
        self.assertEqual(L.create_token(path), "created")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as fh:
            first = fh.read()
        self.assertGreaterEqual(len(first.strip()), 43, "32 random bytes, url-safe, is 43 characters")
        self.assertEqual(L.create_token(path), "kept")
        with open(path) as fh:
            self.assertEqual(fh.read(), first, "an upgrade must not change the token")

    def test_two_tokens_differ(self):
        a, b = os.path.join(self.pfx, "a"), os.path.join(self.pfx, "b")
        L.create_token(a)
        L.create_token(b)
        with open(a) as fa, open(b) as fb:
            self.assertNotEqual(fa.read(), fb.read())

    def test_a_link_in_its_place_is_not_followed(self):
        target = os.path.join(self.pfx, "victim")
        with open(target, "w") as fh:
            fh.write("keep")
        path = os.path.join(self.pfx, "config", "token")
        os.symlink(target, path)
        self.assertEqual(L.create_token(path), "kept")
        with open(target) as fh:
            self.assertEqual(fh.read(), "keep")


class WaitForBind(unittest.TestCase):
    def test_an_already_bound_port_returns_at_once_without_sleeping(self):
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        slept = []
        self.assertTrue(L.wait_for_bind("127.0.0.1", s.getsockname()[1], sleep=slept.append))
        self.assertEqual(slept, [])

    def test_a_port_nothing_binds_gives_up_at_the_ceiling_stepping_by_the_step(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        clock, slept = [0.0], []

        def sleep(n):
            slept.append(n)
            clock[0] += n
        self.assertFalse(L.wait_for_bind("127.0.0.1", port, now=lambda: clock[0], sleep=sleep))
        self.assertEqual(set(slept), {L.BIND_STEP_S})
        self.assertAlmostEqual(sum(slept), L.BIND_CEILING_S, delta=L.BIND_STEP_S)

    def test_a_service_that_binds_late_is_found(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        later = socket.socket()
        self.addCleanup(later.close)
        calls = []

        def sleep(_n):
            calls.append(1)
            if len(calls) == 3:
                later.bind(("127.0.0.1", port))
                later.listen(1)
        self.assertTrue(L.wait_for_bind("127.0.0.1", port, sleep=sleep))
        self.assertEqual(len(calls), 3)


class HostYaml(Dirs):
    def load(self, text):
        self.write_host_yaml(text)
        cfg, problems = cfgmod.load(required_files=[self.cfg])
        self.assertEqual(problems, [])
        return cfg

    def test_a_network_choice_names_the_listen_address_and_the_token_file(self):
        cfg = self.load(applymod.host_yaml_text(self.pfx, {"listen": "0.0.0.0:80", "access": None}))
        self.assertEqual(cfg["service"]["listen"], "0.0.0.0:80")
        self.assertEqual(cfg["service"]["token_file"], os.path.join(self.pfx, "config", "token"))
        self.assertEqual(cfg["service"]["access"], "token")

    def test_a_loopback_choice_names_no_token_file(self):
        cfg = self.load(applymod.host_yaml_text(self.pfx, {"listen": "127.0.0.1:9000", "access": None}))
        self.assertEqual((cfg["service"]["listen"], cfg["service"]["token_file"]), ("127.0.0.1:9000", None))

    def test_open_read_is_recorded_only_when_asked(self):
        cfg = self.load(applymod.host_yaml_text(self.pfx, {"listen": "0.0.0.0:80", "access": "open-read"}))
        self.assertEqual(cfg["service"]["access"], "open-read")
        self.assertNotIn("open-read", applymod.host_yaml_text(self.pfx, {"listen": "0.0.0.0:80", "access": "token"}))

    def test_open_access_is_recorded_with_no_token_file_and_the_service_accepts_the_file(self):
        text = applymod.host_yaml_text(self.pfx, {"listen": "0.0.0.0:80", "access": "open"})
        self.assertNotIn("token_file", text)
        cfg = self.load(text)
        self.assertEqual((cfg["service"]["access"], cfg["service"]["listen"], cfg["service"]["token_file"]), ("open", "0.0.0.0:80", None))
        self.assertIsNone(cfgmod.check_listen(cfg["service"]["listen"], None, cfg["service"]["access"]))

    def test_no_choice_writes_no_service_block_and_the_rest_is_unchanged(self):
        text = applymod.host_yaml_text(self.pfx)
        self.assertNotIn("service:", text)
        self.assertEqual(text, applymod.host_yaml_text(self.pfx, None))

    def test_the_file_loads_and_the_service_accepts_it(self):
        # the loader's own listen check: a network address with the token file named is acceptable
        cfg = self.load(applymod.host_yaml_text(self.pfx, {"listen": "0.0.0.0:80", "access": None}))
        host, port = cfgmod.parse_listen(cfg["service"]["listen"])
        self.assertEqual((host, port), ("0.0.0.0", 80))


class WriteConfig(Dirs):
    def test_a_new_network_host_yaml_creates_the_token_and_says_where_but_not_what(self):
        log = Log()
        notes = applymod.write_config(self.pfx, log, {"listen": "0.0.0.0:80", "access": None})
        token = applymod.token_path(self.pfx)
        self.assertTrue(os.path.exists(token))
        self.assertEqual(stat.S_IMODE(os.stat(token).st_mode), 0o600)
        with open(token) as fh:
            value = fh.read().strip()
        self.assertTrue(any(token in n for n in notes), notes)
        self.assertFalse(any(value in n for n in notes) or any(value in l for l in log.lines), "the value must not be printed or logged")

    def test_open_access_on_the_network_creates_no_token(self):
        notes = applymod.write_config(self.pfx, Log(), {"listen": "0.0.0.0:80", "access": "open"})
        self.assertFalse(os.path.exists(applymod.token_path(self.pfx)))
        self.assertFalse(any("token" in n for n in notes), notes)

    def test_a_loopback_choice_creates_no_token(self):
        applymod.write_config(self.pfx, Log(), {"listen": "127.0.0.1:8080", "access": None})
        self.assertFalse(os.path.exists(applymod.token_path(self.pfx)))

    def test_an_existing_host_yaml_is_not_touched_and_gets_no_token(self):
        self.write_host_yaml("paths:\n  firmware: /x\n")
        applymod.write_config(self.pfx, Log(), {"listen": "0.0.0.0:80", "access": None})
        with open(self.cfg) as fh:
            self.assertEqual(fh.read(), "paths:\n  firmware: /x\n")
        self.assertFalse(os.path.exists(applymod.token_path(self.pfx)), "nothing names a token, so none is made")


class Unit(Dirs):
    HOST = {"serial_group": None}

    def unit(self, listen, owner, limit):
        self.write_host_yaml('service:\n  listen: "%s"\n' % listen if listen else "paths:\n  firmware: /x\n")
        sysctl = os.path.join(self.tmp.name, "start")
        with open(sysctl, "w") as fh:
            fh.write("%s\n" % limit)
        with mock.patch.object(applymod, "service_owner", return_value=owner):
            applymod.write_unit(self.pfx, self.HOST, Log(), sysctl=sysctl)
        with open(planmod.unit_path(self.pfx)) as fh:
            return fh.read()

    def test_a_non_root_service_on_port_80_gets_only_the_bind_capability(self):
        text = self.unit("0.0.0.0:80", "bench", 1024)
        self.assertIn("AmbientCapabilities=CAP_NET_BIND_SERVICE\n", text)
        self.assertIn("CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n", text)
        self.assertIn("User=bench", text)

    def test_nothing_else_gets_it(self):
        for listen, owner, limit in (("0.0.0.0:80", None, 1024), ("0.0.0.0:8080", "bench", 1024), (None, "bench", 1024),
                                     ("0.0.0.0:80", "bench", 80)):
            self.assertNotIn("Capabilit", self.unit(listen, owner, limit), (listen, owner, limit))

    def test_the_capability_sits_in_the_service_section_before_install(self):
        text = self.unit("0.0.0.0:80", "bench", 1024)
        self.assertLess(text.index("[Service]"), text.index("AmbientCapabilities"))
        self.assertLess(text.index("AmbientCapabilities"), text.index("[Install]"))

    def test_an_unusable_recorded_address_writes_no_capability_and_does_not_crash(self):
        self.assertNotIn("Capabilit", self.unit("not-an-address", "bench", 1024))

    @unittest.skipUnless(__import__("shutil").which("systemd-analyze"), "systemd-analyze is not installed")
    def test_systemd_accepts_the_unit_with_the_capability(self):
        import subprocess
        self.unit("0.0.0.0:80", "bench", 1024)
        venv_python = os.path.join(self.pfx, "venv", "bin", "python")
        os.makedirs(os.path.dirname(venv_python))
        os.symlink(sys.executable, venv_python)
        p = subprocess.run(["systemd-analyze", "verify", planmod.unit_path(self.pfx)], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True)
        ours = [ln for ln in p.stdout.splitlines() if planmod.UNIT_NAME in ln and "User" not in ln]
        self.assertEqual(ours, [], p.stdout)

    def test_the_unit_is_rewritten_when_the_port_moves_below_the_limit(self):
        self.unit("0.0.0.0:8080", "bench", 1024)
        self.write_host_yaml('service:\n  listen: "0.0.0.0:80"\n')
        sysctl = os.path.join(self.tmp.name, "start")
        with mock.patch.object(applymod, "service_owner", return_value="bench"):
            self.assertEqual(applymod.write_unit(self.pfx, self.HOST, Log(), sysctl=sysctl), "updated")


class Choice(Dirs):
    def choose(self, listen=None, access=None, terminal=False, answers=(), proc="/proc"):
        ui = FakeUi(terminal, answers)
        return cli.choose_listen(Args(listen, access), {}, ui, self.pfx, proc), ui

    def setUp(self):
        Dirs.setUp(self)
        os.remove(self.cfg) if os.path.exists(self.cfg) else None

    def test_with_no_terminal_and_no_flag_nothing_is_recorded_and_nothing_is_exposed(self):
        choice, _ = self.choose()
        self.assertIsNone(choice)

    def test_a_flag_is_the_answer_and_the_summary_names_the_token_file_but_no_value(self):
        choice, _ = self.choose(listen="0.0.0.0:80")
        self.assertEqual(choice["listen"], "0.0.0.0:80")
        self.assertIn("service.listen: 0.0.0.0:80", choice["summary"])
        self.assertIn(applymod.token_path(self.pfx), choice["summary"])

    def test_open_access_summary_says_no_token_is_asked_and_names_no_token_file(self):
        choice, _ = self.choose(listen="0.0.0.0:80", access="open")
        self.assertIn("access: open", choice["summary"])
        self.assertIn("NO token", choice["summary"])
        self.assertNotIn(applymod.token_path(self.pfx), choice["summary"])

    def test_a_loopback_flag_mentions_no_token(self):
        choice, _ = self.choose(listen="127.0.0.1:9000")
        self.assertNotIn("token", choice["summary"])

    def test_an_unusable_address_is_a_usage_error(self):
        for bad in ("nonsense", "1.2.3.4:0", ":80"):
            with self.assertRaises(cli.UsageError, msg=bad):
                self.choose(listen=bad)

    def test_access_alone_without_an_address_or_a_terminal_is_a_usage_error(self):
        with self.assertRaises(cli.UsageError):
            self.choose(access="open-read")

    def test_the_menu_shows_each_offers_real_state_and_takes_a_number_or_an_address(self):
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        choice, ui = self.choose(terminal=True, answers=["2"])
        self.assertEqual(choice["listen"], "0.0.0.0:80")
        said = "\n".join(ui.said)
        self.assertIn("this machine only", said)
        self.assertIn("127.0.0.1:8080", said)
        self.assertRegex(said, r"port 8080 is (free now|IN USE)")
        choice, _ = self.choose(terminal=True, answers=["10.1.2.3:9000"])
        self.assertEqual(choice["listen"], "10.1.2.3:9000")

    def test_a_bad_menu_answer_is_refused_and_asked_again(self):
        choice, ui = self.choose(terminal=True, answers=["nope", "1"])
        self.assertEqual(choice["listen"], "127.0.0.1:8080")
        self.assertIn("not usable", "\n".join(ui.said))

    def test_a_menu_answered_with_end_of_input_needs_a_person(self):
        with self.assertRaises(NeedsPerson):
            self.choose(terminal=True, answers=[])

    def test_a_port_that_is_in_use_is_reported_in_the_summary_not_refused(self):
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        choice, _ = self.choose(listen="127.0.0.1:%d" % s.getsockname()[1])
        self.assertIn("IN USE", choice["summary"])


class ExistingHostYaml(Dirs):
    def choose(self, listen=None, access=None):
        return cli.choose_listen(Args(listen, access), {}, FakeUi(False), self.pfx)

    def test_the_same_address_is_accepted_and_records_nothing(self):
        self.write_host_yaml('service:\n  listen: "0.0.0.0:80"\n')
        self.assertIsNone(self.choose(listen="0.0.0.0:80"))

    def test_a_different_address_is_refused_with_the_lines_to_add_and_the_file_is_untouched(self):
        text = 'service:\n  listen: "0.0.0.0:80"\n'
        self.write_host_yaml(text)
        with self.assertRaises(cli.UsageError) as cm:
            self.choose(listen="127.0.0.1:9000")
        self.assertIn("never edits an existing host.yaml", str(cm.exception))
        self.assertIn('listen: "127.0.0.1:9000"', str(cm.exception))
        with open(self.cfg) as fh:
            self.assertEqual(fh.read(), text)

    def test_a_file_recording_no_address_means_the_default_and_a_network_flag_differs_from_it(self):
        self.write_host_yaml("paths:\n  firmware: /x\n")
        self.assertIsNone(self.choose(listen="127.0.0.1:8080"))
        with self.assertRaises(cli.UsageError) as cm:
            self.choose(listen="0.0.0.0:80")
        self.assertIn("token_file", str(cm.exception), "a network address also needs the token file")

    def test_no_flag_leaves_an_existing_file_alone(self):
        self.write_host_yaml("paths:\n  firmware: /x\n")
        self.assertIsNone(self.choose())

    def test_a_different_access_is_refused(self):
        self.write_host_yaml('service:\n  listen: "0.0.0.0:80"\n')
        with self.assertRaises(cli.UsageError):
            self.choose(listen="0.0.0.0:80", access="open-read")
        self.assertIsNone(self.choose(listen="0.0.0.0:80", access="token"))


class HttpVerify(Dirs):
    def result(self, listen=None, wait=True, status=200, exc=None):
        if listen:
            self.write_host_yaml('service:\n  listen: "%s"\n' % listen)
        urls = []

        def get(url):
            urls.append(url)
            if exc:
                raise exc
            return status
        return verifymod.http_result(self.pfx, wait=lambda h, p: wait, get=get), urls

    def test_a_200_is_a_pass_naming_the_url_asked(self):
        (label, status, _d), urls = self.result("0.0.0.0:80")
        self.assertEqual((status, urls), ("PASS", ["http://127.0.0.1:80/"]))
        self.assertIn("http://127.0.0.1:80/", label)

    def test_the_default_address_is_used_when_none_is_recorded(self):
        (_l, status, _d), urls = self.result()
        self.assertEqual((status, urls), ("PASS", ["http://127.0.0.1:8080/"]))

    def test_an_ipv6_address_is_bracketed(self):
        _r, urls = self.result("[::1]:9000")
        self.assertEqual(urls, ["http://[::1]:9000/"])

    def test_nothing_listening_is_a_fail_that_says_how_long_it_waited(self):
        (_l, status, detail), urls = self.result("0.0.0.0:80", wait=False)
        self.assertEqual((status, urls), ("FAIL", []))
        self.assertIn("3 s", detail)

    def test_a_non_200_and_an_error_are_fails_with_the_evidence(self):
        (_l, status, detail), _u = self.result(status=503)
        self.assertEqual((status, detail), ("FAIL", "HTTP 503"))
        (_l, status, detail), _u = self.result(exc=OSError("reset"))
        self.assertEqual(status, "FAIL")
        self.assertIn("reset", detail)

    def test_an_unusable_recorded_address_is_a_fail_not_a_crash(self):
        (_l, status, detail), _u = self.result("garbage")
        self.assertEqual(status, "FAIL")
        self.assertIn("garbage", detail)

    def test_it_asks_only_when_the_unit_is_active(self):
        manifest = {"features": {}}
        for state, expect in (("active", True), ("inactive", False), ("failed", False)):
            with mock.patch.object(verifymod, "unit_state", return_value=state), \
                    mock.patch.object(verifymod, "http_result", return_value=("service: GET /", "PASS", "")) as http, \
                    mock.patch.object(verifymod, "_run", return_value=(0, "")):
                results = verifymod.run(self.pfx, [], manifest, linked=True, unit_name="u.service")
            self.assertEqual(http.called, expect, state)
            self.assertEqual(any(r[0] == "service: GET /" for r in results), expect, state)


class Plan(Base):
    """A real install.sh run with --check: the plan names the listen choice, and nothing is changed."""

    def test_a_flag_shows_in_the_plan_and_changes_nothing(self):
        target = self.fresh_prefix()
        rc, out = self.run_sh(["--check", "--prefix", target, "--features", "core", "--listen", "0.0.0.0:80"])
        self.assertEqual(rc, 0, out)
        self.assertIn("service.listen: 0.0.0.0:80", out)
        self.assertIn("token file", out)
        self.assertFalse(os.path.exists(target), "--check changes nothing")

    def test_a_bad_address_stops_with_a_usage_error_before_any_plan(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix(), "--features", "core", "--listen", "nonsense"])
        self.assertEqual(rc, 2, out)
        self.assertIn("--listen nonsense", out)

    def test_with_no_flag_and_no_terminal_the_plan_records_no_listen_choice(self):
        rc, out = self.run_sh(["--check", "--prefix", self.fresh_prefix(), "--features", "core"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("service.listen", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
