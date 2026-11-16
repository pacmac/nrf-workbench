#!/usr/bin/env python3
"""Tests for service.py, the composition root: loading, capabilities, the port checks and main().

The safety order is what is tested: a non-loopback address with no token never starts, a port below the
unprivileged limit refuses without root, a port another process holds refuses and names the holder, and
the token is never printed. main() is driven with a fake `run`, so no server is started.
Run:  python3 tests/service/test_service.py
"""
import contextlib
import io
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import Tree  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.devices import profile as profilemod  # noqa: E402

TOKEN = "s3cret-token-value"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def write(self, name, text, mode=0o600):
        p = self.path(name)
        with open(p, "w") as fh:
            fh.write(text)
        os.chmod(p, mode)
        return p


class Profiles(Base):
    def test_the_profiles_shipped_with_the_product_load(self):
        names = [p["name"] for p in service.load_profiles(service.PACKAGE_PROFILES)]
        self.assertIn("rak4631", names)

    def test_a_missing_or_empty_directory_is_a_configuration_error(self):
        with self.assertRaises(cfgmod.ConfigError):
            service.load_profiles(self.path("nope"))
        os.mkdir(self.path("empty"))
        with self.assertRaises(cfgmod.ConfigError):
            service.load_profiles(self.path("empty"))

    def test_only_profile_files_are_loaded(self):
        d = self.path("p")
        os.mkdir(d)
        with open(os.path.join(service.PACKAGE_PROFILES, "rak4631.yaml")) as fh:
            good = fh.read()
        for name, text in (("a.yaml", good), ("_draft.yaml", "not: a profile"), ("usb_ids.yaml", "x: 1"),
                           ("notes.txt", "hello")):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        self.assertEqual([p["name"] for p in service.load_profiles(d)], ["rak4631"])

    def test_an_invalid_profile_is_refused_not_skipped(self):
        d = self.path("p")
        os.mkdir(d)
        with open(os.path.join(d, "bad.yaml"), "w") as fh:
            fh.write("name: broken\n")
        with self.assertRaises(profilemod.ProfileError):
            service.load_profiles(d)


class Units(Base):
    def test_none_is_no_exceptions(self):
        self.assertEqual(service.load_units(None), {})

    def test_a_units_file_is_read_with_serials_as_strings(self):
        p = self.write("units.yaml", "ABC123:\n  protected: true\n12345:\n  protected: false\n")
        self.assertEqual(service.load_units(p), {"ABC123": {"protected": True}, "12345": {"protected": False}})

    def test_bad_files_are_configuration_errors(self):
        for text in ("- a\n- b\n", "ABC: 5\n"):
            with self.assertRaises(cfgmod.ConfigError, msg=text):
                service.load_units(self.write("u.yaml", text))
        with self.assertRaises(cfgmod.ConfigError):
            service.load_units(self.path("missing.yaml"))

    def test_an_empty_file_is_no_exceptions(self):
        self.assertEqual(service.load_units(self.write("u.yaml", "")), {})


class HostObject(Base):
    def test_a_service_is_built_once_even_when_asked_from_many_threads(self):
        host = service.Host(cfgmod.load(env={})[0], [], {})
        built, gate = [], threading.Barrier(8)

        def factory():
            threading.Event().wait(0.02)        # widens the race window so a missing lock shows; not a wait for a result
            built.append(1)
            return object()

        got = []

        def ask():
            gate.wait()
            got.append(host.service("thing", factory))

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(built), 1)
        self.assertEqual(len(set(id(x) for x in got)), 1)

    def test_the_lock_path_comes_from_the_configuration(self):
        cfg, _ = cfgmod.load(env={}, overrides={"paths": {"lock": "/x/lock"}})
        self.assertEqual(service.Host(cfg, [], {}).lock_path, "/x/lock")


class Capabilities(Base):
    def host(self, firmware=None, tool=None, sysfs=True):
        t = Tree()
        self.addCleanup(t.close)
        if not sysfs:
            import shutil
            shutil.rmtree(t.sysfs)
        cfg, _ = cfgmod.load(env={}, overrides={"paths": {"firmware": firmware}})
        return service.Host(cfg, [], {}, sysfs=t.sysfs, hooks={"tool": tool} if tool else {})

    def test_each_capability_comes_from_what_is_really_there(self):
        with mock.patch.object(service.flash_file, "find_tool", lambda: None):
            self.assertEqual(service.capabilities(self.host()), {"sysfs"})
            self.assertEqual(service.capabilities(self.host(firmware="/x")), {"sysfs", "firmware_store"})
            self.assertEqual(service.capabilities(self.host(sysfs=False)), set())
        with mock.patch.object(service.flash_file, "find_tool", lambda: "/usr/bin/adafruit-nrfutil"):
            self.assertIn("uploader", service.capabilities(self.host()))
        self.assertIn("uploader", service.capabilities(self.host(tool="/fake")))


class Build(Base):
    def test_the_built_app_serves_the_commands_the_capabilities_allow(self):
        t = Tree()
        self.addCleanup(t.close)
        t.app_board()
        cfg, _ = cfgmod.load(env={}, overrides={"paths": {"firmware": self.path("fw")}})
        app, host, registry, caps = service.build(cfg, sysfs=t.sysfs, devroot=t.dev, proc=t.proc,
                                                  hooks={"tool": "/fake"})
        self.assertEqual(caps, {"sysfs", "firmware_store", "uploader"})
        client = TestClient(app)
        self.addCleanup(client.close)
        names = [c["name"] for c in client.get("/api/commands").json()["commands"]]
        self.assertEqual(len(names), 7)
        (b,) = client.get("/api/devices/list").json()["data"]["boards"]
        self.assertEqual((b["profile"], b["state"]), ("rak4631", "app"))
        self.assertEqual(client.get("/api/firmware/list").json()["data"]["items"], [])

    def test_without_a_firmware_directory_the_firmware_commands_are_absent_and_say_why(self):
        t = Tree()
        self.addCleanup(t.close)
        cfg, _ = cfgmod.load(env={})
        app, _h, _r, caps = service.build(cfg, sysfs=t.sysfs, hooks={"tool": "/fake"})
        self.assertNotIn("firmware_store", caps)
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/firmware/list").status_code, 404)
        absent = [a["name"] for a in client.get("/api/capabilities").json()["absent"]]
        self.assertIn("firmware.put", absent)

    def test_the_token_and_access_mode_reach_the_app(self):
        t = Tree()
        self.addCleanup(t.close)
        cfg, _ = cfgmod.load(env={}, overrides={"service": {"access": "open-read"},
                                                 "paths": {"firmware": self.path("fw")}})
        app, _h, _r, _c = service.build(cfg, token=TOKEN, sysfs=t.sysfs, hooks={"tool": "/fake"})
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/devices/list").status_code, 200)                 # a read is open
        self.assertEqual(client.post("/api/firmware/delete", json={"id": "deadbeef"}).status_code, 401)


class LocalMode(Base):
    """What the CLI's local mode builds: the same host and registry, with no HTTP app."""

    def test_local_builds_the_registry_and_host_from_the_environment(self):
        registry, host, caps = service.local(None, env={"NRFWB_FIRMWARE_DIR": self.path("fw")})
        self.assertIn("firmware_store", caps)
        names = [c.name for c in registry.commands(caps)]
        self.assertIn("firmware.put", names)
        self.assertIn("devices.list", names)
        self.assertEqual(host.config["paths"]["firmware"], self.path("fw"))

    def test_without_a_firmware_directory_the_store_commands_are_absent(self):
        registry, host, caps = service.local(None, env={})
        self.assertNotIn("firmware_store", caps)
        self.assertNotIn("firmware.put", [c.name for c in registry.commands(caps)])

    def test_a_config_file_is_read_and_a_missing_one_is_an_error(self):
        cfg = self.write("c.yaml", "paths:\n  firmware: %s\n" % self.path("fw2"))
        _r, host, _c = service.local(cfg, env={})
        self.assertEqual(host.config["paths"]["firmware"], self.path("fw2"))
        with self.assertRaises(cfgmod.ConfigError):
            service.local(self.path("nope.yaml"), env={})

    def test_problems_in_the_configuration_are_an_error_naming_them(self):
        cfg = self.write("c.yaml", "service:\n  listn: 0.0.0.0:80\n")
        with self.assertRaises(cfgmod.ConfigError) as caught:
            service.local(cfg, env={})
        self.assertIn("service.listn", str(caught.exception))

    def test_build_and_local_register_the_same_commands(self):
        t = Tree()
        self.addCleanup(t.close)
        cfg, _ = cfgmod.load(env={}, overrides={"paths": {"firmware": self.path("fw")}})
        _app, _h, registry_a, caps_a = service.build(cfg, sysfs=t.sysfs, hooks={"tool": "/fake"})
        host, registry_b, caps_b = service.build_host(cfg, sysfs=t.sysfs, hooks={"tool": "/fake"})
        self.assertEqual(caps_a, caps_b)
        self.assertEqual([c.name for c in registry_a.commands()], [c.name for c in registry_b.commands()])


class FakeMonitor(object):
    """Stands in for the device monitor: records how it was built, started and stopped."""
    instances = []

    def __init__(self, read_state, publish):
        self.read_state, self.publish, self.started, self.stopped = read_state, publish, 0, 0
        FakeMonitor.instances.append(self)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class StreamsWiring(Base):
    def setUp(self):
        Base.setUp(self)
        FakeMonitor.instances = []

    def run_main(self, argv, run=None, env=None):
        calls = []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = service.main(argv, run=run or (lambda app, **kw: calls.append(app)), env=env or {},
                              make_monitor=FakeMonitor)
        return rc, calls, out.getvalue(), err.getvalue()

    def addr(self):
        return ["--listen", "127.0.0.1:%d" % free_port()]

    def test_with_no_bounds_both_streams_are_absent_with_their_reasons_and_no_monitor_runs(self):
        t = Tree()
        self.addCleanup(t.close)
        cfg, _ = cfgmod.load(env={})
        _app, host, _r, _c = service.build(cfg, sysfs=t.sysfs, hooks={"tool": "/fake"})
        self.assertIsNone(host.streams.events)
        self.assertIsNone(host.streams.activity)
        self.assertIn("streams.events.retain", host.streams.absent["events"])
        self.assertIn("streams.activity.max_age_days", host.streams.absent["activity"])

    def test_bounds_in_days_become_seconds_in_the_streams(self):
        t = Tree()
        self.addCleanup(t.close)
        cfg, _ = cfgmod.load(env={}, overrides={"streams": {"events": {"retain": 50, "max_age_days": 2},
                                                             "activity": {"retain": 7, "max_age_days": 0.5}}})
        _app, host, _r, _c = service.build(cfg, sysfs=t.sysfs, hooks={"tool": "/fake"})
        self.assertEqual((host.streams.events.retain, host.streams.events.max_age_s), (50, 2 * 86400))
        self.assertEqual((host.streams.activity.bus.retain, host.streams.activity.bus.max_age_s), (7, 43200))
        self.assertEqual(host.streams.absent, {})

    def test_the_two_streams_are_independent_and_one_alone_is_fine(self):
        cfg, _ = cfgmod.load(env={}, overrides={"streams": {"activity": {"retain": 5, "max_age_days": 1}}})
        streams = service.build_streams(cfg)
        self.assertIsNone(streams.events)
        self.assertIsNotNone(streams.activity)
        self.assertEqual(list(streams.absent), ["events"])

    def test_every_command_through_the_built_app_is_recorded(self):
        from starlette.testclient import TestClient
        t = Tree()
        self.addCleanup(t.close)
        t.app_board()
        cfg, _ = cfgmod.load(env={}, overrides={"streams": {"activity": {"retain": 10, "max_age_days": 1}}})
        app, host, _r, _c = service.build(cfg, sysfs=t.sysfs, hooks={"tool": "/fake"})
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.get("/api/devices/list").status_code, 200)
        (entry,) = [e.payload for e in host.streams.activity.since(0).events]
        self.assertEqual((entry["command"], entry["class"], entry["caller"]), ("devices.list", "ok", "local"))
        self.assertEqual(client.get("/api/activity").json()["events"][0]["payload"]["command"], "devices.list")

    def test_the_flags_reach_the_configuration_and_the_monitor_is_started_then_stopped(self):
        events = []

        def run(app, **kw):
            events.append((FakeMonitor.instances[0].started, FakeMonitor.instances[0].stopped))

        rc, calls, out, err = self.run_main(self.addr() + ["--events-retain", "50", "--events-max-age-days", "2"], run=run)
        self.assertEqual(rc, 0, err)
        (m,) = FakeMonitor.instances
        self.assertEqual(events, [(1, 0)], "started before the service ran, not yet stopped")
        self.assertEqual((m.started, m.stopped), (1, 1), "stopped when the service ended")
        self.assertIn("stream events: on", out)
        self.assertIn("stream activity: OFF (no bound configured", out)

    def test_the_monitor_is_stopped_even_when_the_service_raises(self):
        def run(app, **kw):
            raise RuntimeError("uvicorn died")

        with self.assertRaises(RuntimeError):
            self.run_main(self.addr() + ["--events-retain", "5", "--events-max-age-days", "1"], run=run)
        self.assertEqual((FakeMonitor.instances[0].started, FakeMonitor.instances[0].stopped), (1, 1))

    def test_no_events_bound_means_no_monitor_at_all(self):
        rc, calls, out, err = self.run_main(self.addr() + ["--activity-retain", "5", "--activity-max-age-days", "1"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(FakeMonitor.instances, [])
        self.assertIn("stream activity: on", out)
        self.assertIn("stream events: OFF", out)

    def test_the_monitor_reads_the_boards_devices_list_returns_and_publishes_to_the_events_bus(self):
        rc, calls, out, err = self.run_main(self.addr() + ["--events-retain", "5", "--events-max-age-days", "1"])
        (m,) = FakeMonitor.instances
        self.assertIsInstance(m.read_state(), list)
        self.assertEqual(m.publish.__name__, "publish")
        event = m.publish("devices.changed", "devices", {"boards": []})
        self.assertEqual(event.cursor, 1)

    def test_one_bound_without_the_other_never_starts_the_service(self):
        for argv in (["--events-retain", "5"], ["--activity-max-age-days", "1"]):
            rc, calls, out, err = self.run_main(self.addr() + argv)
            self.assertEqual((rc, calls, FakeMonitor.instances), (2, [], []), argv)
            self.assertIn("BOTH", err)

    def test_unusable_values_never_start_the_service(self):
        for argv in (["--events-retain", "0", "--events-max-age-days", "1"],
                     ["--activity-retain", "5", "--activity-max-age-days", "-1"]):
            rc, calls, out, err = self.run_main(self.addr() + argv)
            self.assertEqual((rc, calls), (2, []), argv)

    def test_the_flags_are_in_the_help_and_say_there_is_no_default(self):
        help_text = service._parser().format_help()
        for flag in ("--events-retain", "--events-max-age-days", "--activity-retain", "--activity-max-age-days"):
            self.assertIn(flag, help_text)
        self.assertIn("has no default", help_text)


class DashboardWiring(Base):
    def run_main(self, argv):
        apps = []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = service.main(argv, run=lambda app, **kw: apps.append(app), env={}, make_monitor=FakeMonitor)
        return rc, apps, out.getvalue(), err.getvalue()

    def test_the_bench_name_flag_reaches_the_page_and_the_banner(self):
        from starlette.testclient import TestClient
        rc, apps, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port(), "--bench-name", "Pi Bench"])
        self.assertEqual(rc, 0, err)
        self.assertIn("dashboard: bench 'Pi Bench'", out)
        client = TestClient(apps[0])
        self.addCleanup(client.close)
        self.assertIn("<title>Pi Bench · nrf-workbench</title>", client.get("/").text)

    def test_the_banner_says_whether_the_ui_is_built(self):
        os.makedirs(self.path("empty"))
        with mock.patch.object(service, "STATIC_DIR", self.path("empty")):      # not the repo's own build
            rc, apps, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port()])
        self.assertIn("UI NOT built (a plain page says so)", out)
        with mock.patch.object(service, "STATIC_DIR", self.path("static")):
            os.makedirs(self.path("static"))
            with open(self.path("static", "index.html"), "w") as fh:
                fh.write("<title>x</title>")
            rc, apps, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port()])
        self.assertIn("UI built", out)
        self.assertNotIn("NOT built", out)

    def test_with_no_name_the_banner_uses_the_host_name(self):
        import socket
        rc, apps, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port()])
        self.assertIn("bench %r" % socket.gethostname(), out)

    def test_the_flag_is_in_the_help(self):
        self.assertIn("--bench-name", service._parser().format_help())


class Holder(Base):
    def proc(self, listening=True, state="0A", port=8080, owned=True):
        import shutil
        root = self.path("proc")
        shutil.rmtree(root, ignore_errors=True)                 # a test may build several of these
        os.makedirs(os.path.join(root, "net"), exist_ok=True)
        with open(os.path.join(root, "net", "tcp"), "w") as fh:
            fh.write("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n")
            if listening:
                fh.write("   0: 00000000:%04X 00000000:0000 %s 00000000:00000000 00:00000000 00000000     0        0 9917 1 0 0\n"
                         % (port, state))
        os.makedirs(os.path.join(root, "net"), exist_ok=True)
        open(os.path.join(root, "net", "tcp6"), "w").close()
        fd = os.path.join(root, "4242", "fd")
        os.makedirs(fd, exist_ok=True)
        os.symlink("socket:[9917]" if owned else "socket:[1]", os.path.join(fd, "3"))
        os.symlink("/dev/null", os.path.join(fd, "0"))
        with open(os.path.join(root, "4242", "cmdline"), "wb") as fh:
            fh.write(b"/usr/sbin/nginx\0-g\0daemon off;\0")
        os.makedirs(os.path.join(root, "5000"), exist_ok=True)                  # no readable fd directory
        return root

    def test_the_listening_process_is_found_by_its_socket_inode(self):
        self.assertEqual(service.port_holder(8080, self.proc()), {"pid": 4242, "cmdline": "/usr/sbin/nginx -g daemon off;"})

    def test_other_ports_other_states_and_other_owners_are_not_it(self):
        self.assertIsNone(service.port_holder(80, self.proc()))
        self.assertIsNone(service.port_holder(8080, self.proc(state="01")))          # ESTABLISHED, not LISTEN
        self.assertIsNone(service.port_holder(8080, self.proc(listening=False)))
        self.assertIsNone(service.port_holder(8080, self.proc(owned=False)), "listening, owner not visible")

    def test_a_missing_proc_is_none_not_a_crash(self):
        self.assertIsNone(service.port_holder(8080, self.path("no-proc")))


class CheckPort(Base):
    def test_below_the_limit_without_root_is_refused_with_the_limit_that_was_read(self):
        limit = self.write("limit", "1024\n")
        r = service.check_port("127.0.0.1", 80, euid=1000, limit_path=limit)
        self.assertEqual((r.cls, r.code), ("refused", "port_privileged"))
        self.assertEqual(r.evidence, {"port": 80, "limit": 1024})

    def test_the_limit_is_the_machines_not_a_constant(self):
        limit = self.write("limit", "80\n")
        port = free_port()
        self.assertIsNone(service.check_port("127.0.0.1", port, euid=1000, limit_path=self.write("l2", "1\n")))
        r = service.check_port("127.0.0.1", 79, euid=1000, limit_path=limit)
        self.assertEqual(r.code, "port_privileged")
        # exactly at the limit is unprivileged:
        import unittest.mock as m
        with m.patch.object(service.socket, "socket") as sock:
            self.assertIsNone(service.check_port("127.0.0.1", 80, euid=1000, limit_path=limit))
            sock.return_value.bind.assert_called_once_with(("127.0.0.1", 80))

    def test_root_may_use_a_low_port_so_only_the_bind_decides(self):
        import unittest.mock as m
        with m.patch.object(service.socket, "socket") as sock:
            self.assertIsNone(service.check_port("127.0.0.1", 80, euid=0, limit_path=self.write("limit", "1024\n")))
            sock.return_value.bind.assert_called_once()

    def test_an_unreadable_limit_is_not_guessed(self):
        port = free_port()
        self.assertIsNone(service.check_port("127.0.0.1", port, euid=1000, limit_path=self.path("nope")))
        # And a LOW port with an unreadable limit is not refused on a remembered number either: only the
        # bind itself may say no. (The bind is mocked so port 80 is not really taken.)
        with mock.patch.object(service.socket, "socket") as sock:
            self.assertIsNone(service.check_port("127.0.0.1", 80, euid=1000, limit_path=self.path("nope")))
            sock.return_value.bind.assert_called_once_with(("127.0.0.1", 80))

    def test_the_probe_socket_is_always_closed_on_success_and_on_every_error(self):
        # Closed explicitly, not left to the garbage collector.
        import errno as errno_mod
        with mock.patch.object(service.socket, "socket") as sock:
            service.check_port("127.0.0.1", 8080, euid=0)
            sock.return_value.close.assert_called_once()
        for code in (errno_mod.EADDRINUSE, errno_mod.EACCES, errno_mod.EADDRNOTAVAIL):
            with mock.patch.object(service.socket, "socket") as sock:
                sock.return_value.bind.side_effect = OSError(code, "x")
                self.assertIsNotNone(service.check_port("127.0.0.1", 8080, euid=0, proc=self.path("no-proc")), code)
                sock.return_value.close.assert_called_once()
        with mock.patch.object(service.socket, "socket") as sock:
            sock.return_value.bind.side_effect = OSError(errno_mod.EIO, "unexpected")
            with self.assertRaises(OSError):
                service.check_port("127.0.0.1", 8080, euid=0)
            sock.return_value.close.assert_called_once()

    def test_a_free_port_passes(self):
        self.assertIsNone(service.check_port("127.0.0.1", free_port(), euid=0))

    def test_a_port_another_process_holds_is_refused_and_names_the_holder(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        r = service.check_port("127.0.0.1", port, euid=0)
        self.assertEqual((r.cls, r.code), ("refused", "port_in_use"), r.to_dict())
        self.assertEqual(r.evidence["port"], port)
        self.assertEqual(r.evidence["holder"]["pid"], os.getpid(), "the holder is this very process")

    def test_an_invisible_holder_is_said_to_be_invisible(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        r = service.check_port("127.0.0.1", holder.getsockname()[1], euid=0, proc=self.path("no-proc"))
        self.assertEqual(r.evidence["holder"], "not visible to this user")

    def test_an_address_this_machine_does_not_have(self):
        r = service.check_port("203.0.113.9", 8080, euid=0)
        self.assertEqual((r.cls, r.code), ("refused", "capability_missing"))

    def test_nothing_is_left_listening_by_the_check(self):
        port = free_port()
        service.check_port("127.0.0.1", port, euid=0)
        again = socket.socket()
        again.bind(("127.0.0.1", port))          # would raise if the check had kept the port
        again.close()


class Main(Base):
    def run_main(self, argv, env=None):
        calls = []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = service.main(argv, run=lambda app, **kw: calls.append((app, kw)), env=env or {})
        return rc, calls, out.getvalue(), err.getvalue()

    def test_the_default_is_loopback_with_no_token_and_it_runs(self):
        port = free_port()
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % port])
        self.assertEqual((rc, len(calls)), (0, 1))
        self.assertEqual((calls[0][1]["host"], calls[0][1]["port"]), ("127.0.0.1", port))
        self.assertIn("loopback only", out)
        self.assertIn("no token", out)

    def test_leaving_loopback_with_no_token_never_starts(self):
        rc, calls, out, err = self.run_main(["--listen", "0.0.0.0:%d" % free_port()])
        self.assertEqual((rc, calls), (1, []))
        self.assertIn("token_missing", err)

    def test_the_cheap_rule_is_applied_before_the_port_is_touched(self):
        with mock.patch.object(service, "check_port", side_effect=AssertionError("the port was checked")):
            rc, calls, out, err = self.run_main(["--listen", "0.0.0.0:%d" % free_port()])
        self.assertEqual(rc, 1)
        self.assertIn("token_missing", err)

    def test_leaving_loopback_with_a_token_starts_and_says_it_is_unencrypted_without_printing_the_token(self):
        tok = self.write("token", TOKEN + "\n")
        rc, calls, out, err = self.run_main(["--listen", "0.0.0.0:%d" % free_port(), "--token-file", tok])
        self.assertEqual((rc, len(calls)), (0, 1))
        self.assertIn("NETWORK, UNENCRYPTED http", out)
        self.assertIn("token required", out)
        self.assertNotIn(TOKEN, out + err)

    def test_open_read_says_what_is_open(self):
        tok = self.write("token", TOKEN + "\n")
        rc, calls, out, err = self.run_main(["--listen", "0.0.0.0:%d" % free_port(), "--token-file", tok,
                                             "--access", "open-read"])
        self.assertEqual(rc, 0)
        self.assertIn("reads open, writes need the token", out)

    def test_an_unreadable_or_empty_token_file_refuses(self):
        for tok in (self.path("missing"), self.write("empty", "  \n")):
            rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port(), "--token-file", tok])
            self.assertEqual((rc, calls), (1, []), tok)

    def test_a_token_file_others_can_read_warns_but_starts(self):
        tok = self.write("token", TOKEN + "\n", 0o644)
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port(), "--token-file", tok])
        self.assertEqual(rc, 0)
        self.assertIn("readable by other users", err)
        self.assertNotIn(TOKEN, out + err)

    def test_a_port_in_use_refuses_and_says_so(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % holder.getsockname()[1]])
        self.assertEqual((rc, calls), (1, []))
        self.assertIn("port_in_use", err)

    def test_bad_configuration_is_exit_2_and_never_starts(self):
        for argv in (["--listen", "nonsense"], ["--config", self.path("missing.yaml")]):
            rc, calls, out, err = self.run_main(argv)
            self.assertEqual((rc, calls), (2, []), argv)
            self.assertIn("configuration error", err)

    def test_an_unknown_setting_in_the_config_file_is_exit_2(self):
        cfg = self.write("c.yaml", "service:\n  listn: 0.0.0.0:80\n")
        rc, calls, out, err = self.run_main(["--config", cfg])
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("service.listn", err)

    def test_the_banner_lists_the_commands_and_what_is_absent(self):
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port()])
        self.assertIn("commands:", out)
        self.assertIn("firmware.put", out.split("absent here")[1] if "absent here" in out else "",
                      "with no firmware directory the firmware commands are absent")

    def test_a_firmware_directory_makes_the_store_commands_present(self):
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port(), "--firmware-dir", self.path("fw")])
        self.assertIn("firmware.put", out.split("absent here")[0])

    def test_the_environment_is_a_layer(self):
        port = free_port()
        rc, calls, out, err = self.run_main([], env={"NRFWB_LISTEN": "127.0.0.1:%d" % port})
        self.assertEqual((rc, calls[0][1]["port"]), (0, port))

    def test_a_flag_beats_the_environment(self):
        a, b = free_port(), free_port()
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % b], env={"NRFWB_LISTEN": "127.0.0.1:%d" % a})
        self.assertEqual(calls[0][1]["port"], b)

    def test_an_invalid_profile_directory_is_exit_2(self):
        os.mkdir(self.path("p"))
        rc, calls, out, err = self.run_main(["--listen", "127.0.0.1:%d" % free_port(), "--profiles", self.path("p")])
        self.assertEqual((rc, calls), (2, []))


if __name__ == "__main__":
    unittest.main(verbosity=1)
