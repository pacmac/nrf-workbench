#!/usr/bin/env python3
"""Tests for devices.monitor: `devices.changed` with no polling and no timer.

The kernel's uevent socket is replaced by one end of a socketpair, so a test raises an "event" by sending a
byte. Every wait here is on an Event the code under test sets (the publish, or the read of the state), so a
passing run never waits on the clock; the ceilings only end a test that is not going to pass.
Run:  python3 tests/devices/test_monitor.py
"""
import os
import select as real_select
import socket
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.devices import monitor  # noqa: E402
from nrf_workbench.devices.monitor import DeviceMonitor  # noqa: E402

CEILING_S = 10         # only ever reached by a failing test


class Harness(object):
    """A monitor over a socketpair, with the state and the published events under the test's control."""

    def __init__(self, states=None):
        self.kernel, self.source = socket.socketpair()
        self.states = list(states or [[]])
        self.published = []
        self.read_calls = 0
        self.read_seen = threading.Event()
        self.published_seen = threading.Event()
        self.raise_next = False
        self.order = []

        def open_source():
            self.order.append("open")
            return self.source

        def read_state():
            self.order.append("read")
            self.read_calls += 1
            self.read_seen.set()
            if self.raise_next:
                self.raise_next = False
                raise RuntimeError("sysfs vanished")
            return self.states[0] if len(self.states) == 1 else self.states.pop(0)

        def publish(type, area, payload):
            self.published.append((type, area, payload))
            self.published_seen.set()

        self.monitor = DeviceMonitor(read_state, publish, open_source)

    def event(self):
        """The kernel raises a uevent."""
        self.kernel.send(b"add@/devices/usb1/1-1")

    def wait(self, ev):
        assert ev.wait(CEILING_S), "the monitor did not react"
        ev.clear()

    def close(self):
        self.monitor.stop()
        self.kernel.close()


class Snapshot(unittest.TestCase):
    def test_the_socket_is_opened_before_the_first_read_so_nothing_between_them_is_missed(self):
        h = Harness([[{"serial": "A"}]])
        self.addCleanup(h.close)
        h.monitor.start()
        self.assertEqual(h.order[:2], ["open", "read"])

    def test_start_publishes_the_first_level_before_it_returns(self):
        h = Harness([[{"serial": "A"}]])
        self.addCleanup(h.close)
        h.monitor.start()
        self.assertEqual(h.published, [("devices.changed", "devices", {"boards": [{"serial": "A"}], "reason": "start"})])
        self.assertTrue(h.monitor.live)

    def test_starting_twice_is_refused(self):
        h = Harness()
        self.addCleanup(h.close)
        h.monitor.start()
        with self.assertRaises(RuntimeError):
            h.monitor.start()


class Changes(unittest.TestCase):
    def test_a_uevent_that_changes_the_state_publishes_it(self):
        h = Harness([[{"serial": "A"}], [{"serial": "A"}, {"serial": "B"}]])
        self.addCleanup(h.close)
        h.monitor.start()
        h.published_seen.clear()
        h.event()
        h.wait(h.published_seen)
        self.assertEqual(h.published[-1], ("devices.changed", "devices",
                                           {"boards": [{"serial": "A"}, {"serial": "B"}], "reason": "uevent"}))

    def test_a_uevent_that_changes_nothing_publishes_nothing(self):
        h = Harness([[{"serial": "A"}]])                          # the level never changes
        self.addCleanup(h.close)
        h.monitor.start()
        h.read_seen.clear()
        h.event()
        h.wait(h.read_seen)                                       # it looked again...
        self.assertEqual(len(h.published), 1)                     # ...and found the same state: only the snapshot

    def test_a_burst_of_uevents_is_one_event_per_distinct_state(self):
        h = Harness([[], [{"serial": "A"}]])                      # one re-enumeration raises many uevents
        self.addCleanup(h.close)
        h.monitor.start()
        h.published_seen.clear()
        for _ in range(8):
            h.event()
        h.wait(h.published_seen)
        h.read_seen.clear()
        h.event()                                                 # one more, to prove the loop is idle and healthy
        h.wait(h.read_seen)
        changed = [p for p in h.published if p[2]["reason"] == "uevent"]
        self.assertEqual(len(changed), 1, h.published)

    def test_the_level_is_reread_after_every_event_and_never_inferred_from_the_message(self):
        h = Harness([[], [{"serial": "A"}]])
        self.addCleanup(h.close)
        h.monitor.start()
        before = h.read_calls
        h.published_seen.clear()
        h.kernel.send(b"nonsense that says nothing about a device")
        h.wait(h.published_seen)
        self.assertGreater(h.read_calls, before)


class Failures(unittest.TestCase):
    def test_a_failing_read_is_published_not_swallowed_and_the_monitor_keeps_running(self):
        h = Harness([[], [{"serial": "A"}]])
        self.addCleanup(h.close)
        h.monitor.start()
        h.raise_next = True
        h.published_seen.clear()
        h.event()
        h.wait(h.published_seen)
        self.assertEqual(h.published[-1][0], "devices.read_failed")
        self.assertIn("sysfs vanished", h.published[-1][2]["error"])
        h.published_seen.clear()
        h.event()                                                 # and it still works afterwards
        h.wait(h.published_seen)
        self.assertEqual(h.published[-1][2]["boards"], [{"serial": "A"}])
        self.assertTrue(h.monitor.running)

    def test_a_host_with_no_uevents_says_so_after_one_snapshot_and_starts_no_thread(self):
        published = []

        def no_source():
            raise OSError("Protocol not supported")

        m = DeviceMonitor(lambda: [{"serial": "A"}], lambda t, a, p: published.append((t, p)), no_source)
        m.start()
        self.assertEqual([t for t, _ in published], ["devices.changed", "devices.live_unavailable"])
        self.assertIn("Protocol not supported", published[1][1]["reason"])
        self.assertFalse(m.live)
        self.assertFalse(m.running)
        m.stop()                                                  # safe when nothing ever ran

    def test_a_source_that_closes_is_reported_and_the_thread_ends_instead_of_spinning(self):
        h = Harness()
        self.addCleanup(h.close)
        h.monitor.start()
        h.published_seen.clear()
        h.kernel.close()                                          # the peer is gone: the socket reads EOF forever
        h.wait(h.published_seen)
        self.assertEqual(h.published[-1][0], "devices.live_unavailable")
        h.monitor._thread.join(CEILING_S)
        self.assertFalse(h.monitor.running)
        self.assertFalse(h.monitor.live)


class Stopping(unittest.TestCase):
    def test_stop_wakes_a_thread_blocked_with_no_timeout_and_closes_everything(self):
        h = Harness()
        h.monitor.start()
        thread, r, w = h.monitor._thread, h.monitor._wake_r, h.monitor._wake_w
        done = threading.Event()
        threading.Thread(target=lambda: (h.monitor.stop(), done.set()), daemon=True).start()
        self.assertTrue(done.wait(CEILING_S), "stop() never returned: the blocked thread was not woken")
        self.assertFalse(thread.is_alive())
        for fd in (r, w):
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertFalse(h.monitor.running)
        h.kernel.close()

    def test_stop_is_safe_twice_and_before_start(self):
        h = Harness()
        h.monitor.stop()
        h.monitor.start()
        h.monitor.stop()
        h.monitor.stop()
        h.kernel.close()

    def test_nothing_is_published_after_stop(self):
        h = Harness([[], [{"serial": "A"}]])
        h.monitor.start()
        h.monitor.stop()
        count = len(h.published)
        try:
            h.event()
        except OSError:
            pass
        self.assertEqual(len(h.published), count)
        h.kernel.close()

    def test_it_can_be_started_again_after_a_stop(self):
        h = Harness([[]])
        h.monitor.start()
        h.monitor.stop()
        h.kernel2, h.source2 = socket.socketpair()
        h.monitor._open_source = lambda: h.source2
        h.monitor.start()
        self.assertTrue(h.monitor.running)
        h.monitor.stop()
        h.kernel.close()
        h.kernel2.close()


class NoClock(unittest.TestCase):
    def test_select_is_called_with_no_timeout(self):
        h = Harness([[]])
        calls = []
        real = real_select.select

        def spy(*args):
            calls.append(args)
            return real(*args)

        with mock.patch.object(monitor.select, "select", spy):
            h.monitor.start()
            h.read_seen.clear()
            h.event()
            h.wait(h.read_seen)
        h.close()
        self.assertTrue(calls)
        for args in calls:
            self.assertEqual(len(args), 3, "select() was given a timeout: that is a clock, not an event")

    def test_the_module_has_no_sleep_and_no_poll(self):
        with open(monitor.__file__) as fh:
            source = fh.read()
        code = "\n".join(l for l in source.splitlines() if not l.strip().startswith(("#", '"""')))
        for banned in ("time.sleep", "sleep(", ".poll(", "setInterval", "timeout="):
            self.assertNotIn(banned, code)


class KernelSocket(unittest.TestCase):
    def test_the_real_opener_returns_a_socket_or_an_oserror_never_something_else(self):
        try:
            sock = monitor.open_kernel_uevents()
        except OSError:
            return                                                # a container may have no uevent source
        self.addCleanup(sock.close)
        self.assertEqual(sock.family, socket.AF_NETLINK)


if __name__ == "__main__":
    unittest.main(verbosity=1)
