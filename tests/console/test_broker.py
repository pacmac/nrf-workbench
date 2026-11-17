#!/usr/bin/env python3
"""Tests for console.port and console.broker, against REAL pseudo-terminal pairs (never a real node).

One end of a pty is the adapter the broker opens, the other is "the device": a test writes to it to make the device
speak and reads from it to see what the host sent. The kernel's uevent socket is one end of a socketpair, so a test
raises an "event" by sending a byte. Every wait is on a condition the broker's own publish sets, so a passing run
never waits on the clock; the ceilings only end a test that is not going to pass.
Run:  python3 tests/console/test_broker.py
"""
import fcntl
import os
import select
import socket
import sys
import tempfile
import termios
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

import yaml  # noqa: E402
from nrf_workbench.console import broker as brokermod  # noqa: E402
from nrf_workbench.console import links, port  # noqa: E402
from nrf_workbench.console.broker import Broker  # noqa: E402
from nrf_workbench.console.port import Port, PortGone, PortHeld  # noqa: E402
from nrf_workbench.core.events import Bus  # noqa: E402

CEILING_S = 10            # only ever reached by a failing test


def raw_link(**kw):
    raw = {"name": "dbg", "backend": "usb-adapter", "adapter": {"ids": ["1a86:55d4"], "serial": "S1"}, "baud": 115200,
           "dut_tx_to_host": True, "host_tx_to_dut": True, "voltage": {"dut": 3.3, "adapter": 3.3, "verified": True},
           "dtr_rts_reach_dut": False, "reply_marker": "< "}
    raw.update(kw)
    return raw


class Rig(object):
    """A broker over a pty pair, with the node, presence and the published events under the test's control."""

    def __init__(self, tmp, raws=None, retain=1000, uevents=True, open_port=Port):
        self.tmp = tmp
        self.master, self.slave = os.openpty()               # master: the device; slave: the adapter's node
        self.node = os.ttyname(self.slave)
        self.present = True
        self.opened_on = []
        path = os.path.join(tmp, "links.yaml")
        with open(path, "w") as fh:
            yaml.safe_dump({"links": raws if raws is not None else [raw_link()]}, fh)
        found, problems = links.load(path, ["239a:8029"])
        assert problems == [], problems
        self.links = found
        self.bus = Bus(retain, 3600)
        self.cond = threading.Condition()
        self.token = self.bus.subscribe(self._poke)
        self.a, self.b = socket.socketpair()

        def opener(node, baud):
            self.opened_on.append(threading.current_thread().name)
            return open_port(node, baud)

        def resolve(link):
            if self.present:
                return {"device": {"serial": link.serial}, "node": self.node}, ""
            return None, "the adapter is not attached"

        def open_uevents():
            if not uevents:
                raise OSError("no uevents on this host")
            return self.a

        self.broker = Broker(self.links, resolve, opener, self.bus, open_uevents=open_uevents)

    def _poke(self, _event):
        with self.cond:
            self.cond.notify_all()

    def wait(self, predicate, what):
        with self.cond:
            if not self.cond.wait_for(predicate, timeout=CEILING_S):
                raise AssertionError("never happened: " + what)

    def events(self, kind):
        return [e.payload for e in self.bus.since(0, None, types=(kind,)).events]

    def say(self, data):
        os.write(self.master, data)

    def lines(self):
        return [(p["offset"], p["bytes"], p["text"]) for p in self.events("console.line")]

    def wait_lines(self, n):
        self.wait(lambda: len(self.events("console.line")) >= n, "%d lines" % n)

    def heard(self, n, bound=CEILING_S):
        """What the device received, `n` bytes, waiting on the descriptor and not on the clock."""
        got = b""
        while len(got) < n:
            ready, _, _ = select.select([self.master], [], [], bound)
            if not ready:
                raise AssertionError("the device heard %r and then nothing" % got)
            got += os.read(self.master, 4096)
        return got

    def kick(self):
        self.b.send(b"x")                                      # a uevent

    def close(self):
        self.broker.stop()
        self.bus.unsubscribe(self.token)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
        for s in (self.a, self.b):
            s.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def rig(self, start=True, **kw):
        r = Rig(self.tmp.name, **kw)
        self.addCleanup(r.close)
        if start:
            r.broker.start()
        return r


class PortRules(Base):
    def test_a_rate_that_is_not_standard_is_not_opened_and_1200_never_is(self):
        r = self.rig(start=False)
        for baud in (1200, 0, 12345, "115200", None):
            with self.assertRaises(ValueError):
                Port(r.node, baud)

    def test_the_port_is_raw_8n1_ignores_modem_lines_and_does_not_drop_dtr_on_close(self):
        r = self.rig(start=False)
        before = termios.tcgetattr(r.slave)                  # start from a port that DOES hang up and is 7E2, so a
        before[2] = (before[2] | termios.HUPCL | termios.PARENB | termios.CSTOPB) & ~termios.CLOCAL   # cleared flag is the
        termios.tcsetattr(r.slave, termios.TCSANOW, before)  # code's doing, not the pty's default
        p = Port(r.node, 115200)
        self.addCleanup(p.close)
        iflag, oflag, cflag, lflag, ispeed, ospeed, _cc = termios.tcgetattr(r.slave)
        self.assertTrue(cflag & termios.CLOCAL, "modem-control lines must be ignored")
        self.assertTrue(cflag & termios.CREAD)
        self.assertFalse(cflag & termios.HUPCL, "closing must not drop DTR")
        self.assertEqual(cflag & termios.CSIZE, termios.CS8)
        self.assertFalse(cflag & (termios.PARENB | termios.CSTOPB))
        self.assertEqual((iflag, oflag, lflag), (0, 0, 0), "raw: no echo, no line editing, no translation")
        self.assertEqual((ispeed, ospeed), (termios.B115200, termios.B115200))

    def test_the_only_control_request_made_is_exclusive_and_no_modem_line_is_ever_set(self):
        r = self.rig(start=False)
        requests = []
        real = fcntl.ioctl

        def spy(fd, request, *a):
            requests.append(request)
            return real(fd, request, *a)

        with mock.patch.object(port.fcntl, "ioctl", spy):
            p = Port(r.node, 115200)
            p.write_all(b"x", 1)
            p.close()
        self.assertEqual(requests, [termios.TIOCEXCL])
        TIOCSBRK, TIOCCBRK = 0x5427, 0x5428                  # break on and off: Linux's values, which termios does not export
        for modem in (termios.TIOCMBIS, termios.TIOCMBIC, termios.TIOCMSET, TIOCSBRK, TIOCCBRK):
            self.assertNotIn(modem, requests)

    def test_a_second_opener_of_the_same_adapter_is_held_out(self):
        r = self.rig(start=False)
        first = Port(r.node, 115200)
        self.addCleanup(first.close)
        with self.assertRaises(PortHeld):
            Port(r.node, 115200)
        first.close()
        Port(r.node, 115200).close()                        # released on close

    def test_a_hung_up_port_is_gone_and_a_read_with_nothing_waiting_is_never_asked(self):
        r = self.rig(start=False)
        p = Port(r.node, 115200)
        self.addCleanup(p.close)
        os.close(r.master)
        r.master = os.open(os.devnull, os.O_RDONLY)          # so the rig's own cleanup has something to close
        with self.assertRaises(PortGone):
            p.read()

    def test_the_write_bound_is_the_line_time_doubled_plus_a_floor(self):
        self.assertAlmostEqual(port.write_bound_s(10, 115200), 2 * 10 * 10 / 115200 + 0.1)
        self.assertAlmostEqual(port.write_bound_s(1024, 9600), 2 * 1024 * 10 / 9600 + 0.1)
        self.assertGreater(port.write_bound_s(1, 921600), 0.1)

    def test_a_write_that_cannot_finish_gives_up_with_a_reason(self):
        r = self.rig(start=False)
        p = Port(r.node, 9600)
        self.addCleanup(p.close)
        with mock.patch.object(port.os, "write", side_effect=BlockingIOError), \
                mock.patch.object(port.select, "select", return_value=([], [], [])):
            with self.assertRaises(port.PortError) as cm:
                p.write_all(b"x" * 10, 0.05)
        self.assertIn("did not finish", str(cm.exception))


class Lines(Base):
    def test_the_broker_opens_the_adapter_and_says_so(self):
        r = self.rig()
        st = r.broker.state("dbg")
        self.assertEqual((st["state"], st["node"]), ("open", r.node))
        self.assertEqual([(p["state"], p["link"]) for p in r.events("console.link")], [("open", "dbg")])

    def test_bytes_become_lines_with_the_offset_where_each_starts(self):
        r = self.rig()
        r.say(b"hello\nworld\r\n")
        r.wait_lines(2)
        self.assertEqual(r.lines(), [(0, 6, "hello"), (6, 7, "world")])

    def test_a_half_received_line_is_held_until_it_is_whole_and_the_next_cursor_excludes_it(self):
        r = self.rig()
        r.say(b"one\ntw")
        r.wait_lines(1)
        page, = [r.broker.since("dbg", 0)]
        self.assertEqual(([l["text"] for l in page["lines"]], page["next"]), (["one"], 4))
        r.say(b"o\n")
        r.wait_lines(2)
        self.assertEqual(r.lines(), [(0, 4, "one"), (4, 4, "two")])
        self.assertEqual([l["text"] for l in r.broker.since("dbg", 4)["lines"]], ["two"])

    def test_a_line_over_the_cap_is_cut_and_marked_partial_and_nothing_is_lost(self):
        r = self.rig()
        cap = brokermod.LINE_CAP_BYTES
        r.say(b"x" * (cap * 2 + 50) + b"\n")
        r.wait_lines(3)
        events = r.events("console.line")
        self.assertEqual([(p["bytes"], p.get("partial", False)) for p in events], [(cap, True), (cap, True), (51, False)])
        self.assertEqual(sum(p["bytes"] for p in events), cap * 2 + 51)

    def test_a_long_line_with_no_newline_yet_is_cut_at_the_cap_when_it_reaches_it_and_the_rest_waits(self):
        r = self.rig()
        cap = brokermod.LINE_CAP_BYTES
        r.say(b"y" * (cap + 10))
        r.wait_lines(1)
        self.assertEqual([(p["bytes"], p.get("partial")) for p in r.events("console.line")], [(cap, True)])
        self.assertEqual(r.broker.since("dbg", 0)["next"], cap, "the 10 bytes beyond the cap are not delivered yet")
        r.say(b"\n")
        r.wait_lines(2)
        self.assertEqual([(p["bytes"], p.get("partial", False)) for p in r.events("console.line")], [(cap, True), (11, False)])

    def test_invalid_utf8_is_replaced_not_dropped_and_not_a_crash(self):
        r = self.rig()
        r.say(b"ok \xff\xfe bytes\n")
        r.wait_lines(1)
        self.assertIn("�", r.lines()[0][2])

    def test_a_line_starting_with_the_reply_marker_is_flagged_a_reply(self):
        r = self.rig()
        r.say(b"boot\n< pong\n")
        r.wait_lines(2)
        self.assertEqual([p["reply"] for p in r.events("console.line")], [False, True])

    def test_no_marker_means_nothing_is_a_reply(self):
        r = self.rig(raws=[raw_link(reply_marker=None)])
        r.say(b"< pong\n")
        r.wait_lines(1)
        self.assertEqual([p["reply"] for p in r.events("console.line")], [False])

    def test_since_says_how_many_bytes_fell_out_of_retention(self):
        r = self.rig(retain=3)
        for i in range(10):
            r.say(b"line%d\n" % i)
        r.wait(lambda: r.broker.state("dbg")["lines"] == 10, "ten lines")
        got = r.broker.since("dbg", 0)
        self.assertEqual(len(got["lines"]), 3)
        self.assertEqual(got["lost"], got["lines"][0]["offset"])
        self.assertEqual(got["next"], 60)
        self.assertEqual(r.broker.since("dbg", 60), {"lines": [], "next": 60, "lost": 0})

    def test_since_for_an_unknown_link_is_none(self):
        self.assertIsNone(self.rig().broker.since("nope", 0))

    def test_a_burst_arrives_complete_and_in_order(self):
        r = self.rig()
        for i in range(200):
            r.say(b"n%03d\n" % i)
        r.wait_lines(200)
        self.assertEqual([t for _o, _b, t in r.lines()], ["n%03d" % i for i in range(200)])
        self.assertEqual([o for o, _b, _t in r.lines()], [5 * i for i in range(200)])


class Sending(Base):
    def test_a_send_writes_the_text_and_the_links_line_ending(self):
        r = self.rig()
        self.assertEqual(r.broker.send("dbg", "ping")["sent"], 5)
        self.assertEqual(r.heard(5), b"ping\n")
        r2 = self.rig(raws=[raw_link(eol="\r\n")])
        r2.broker.send("dbg", "ping")
        self.assertEqual(r2.heard(6), b"ping\r\n")

    def test_with_a_bound_and_a_marker_it_returns_on_the_reply_not_on_the_bound(self):
        r = self.rig()
        done = []

        def device():
            r.heard(5)
            r.say(b"noise\n< pong\n")
        threading.Thread(target=device, daemon=True).start()
        result = r.broker.send("dbg", "ping", bound_s=CEILING_S)
        done.append(result)
        self.assertEqual((result["waited"], result["reply"]), (True, "< pong"))

    def test_a_reply_that_never_comes_gives_up_at_the_callers_bound_and_says_so(self):
        r = self.rig()
        result = r.broker.send("dbg", "ping", bound_s=0.05)
        self.assertEqual((result["waited"], result["reply"]), (True, None))

    def test_with_no_bound_it_does_not_wait_and_with_no_marker_there_is_nothing_to_wait_for(self):
        r = self.rig()
        self.assertEqual((r.broker.send("dbg", "x")["waited"], r.broker.send("dbg", "x")["reply"]), (False, None))
        r2 = self.rig(raws=[raw_link(reply_marker=None)])
        self.assertEqual(r2.broker.send("dbg", "x", bound_s=5)["waited"], False)

    def test_a_reply_from_before_the_send_does_not_answer_it(self):
        r = self.rig()
        r.say(b"< stale\n")
        r.wait_lines(1)
        result = r.broker.send("dbg", "ping", bound_s=0.05)
        self.assertIsNone(result["reply"])

    def test_a_send_on_a_link_that_is_not_open_is_refused(self):
        r = self.rig()
        r.present = False
        r.kick()
        r.wait(lambda: r.broker.state("dbg")["state"] == "absent", "absent")
        with self.assertRaises(PortGone):
            r.broker.send("dbg", "x")


class Attaching(Base):
    def test_an_adapter_that_appears_later_is_opened_on_the_kernels_word(self):
        r = self.rig(start=False)
        r.present = False
        r.broker.start()
        self.assertEqual(r.broker.state("dbg")["state"], "absent")
        self.assertIn("not attached", r.broker.state("dbg")["reason"])
        r.present = True
        r.kick()
        r.wait(lambda: r.broker.state("dbg")["state"] == "open", "opened after the uevent")
        self.assertEqual([p["state"] for p in r.events("console.link")], ["absent", "open"])

    def test_an_adapter_that_vanishes_is_closed_and_the_change_is_published(self):
        r = self.rig()
        r.present = False
        r.kick()
        r.wait(lambda: r.broker.state("dbg")["state"] == "absent", "closed after the uevent")
        self.assertEqual([p["state"] for p in r.events("console.link")], ["open", "absent"])

    def test_a_hangup_with_no_uevent_is_noticed_from_the_port_itself(self):
        r = self.rig()
        os.close(r.master)
        r.master = os.open(os.devnull, os.O_RDONLY)
        r.wait(lambda: r.broker.state("dbg")["state"] == "absent", "hangup noticed")
        self.assertIn("port", r.broker.state("dbg")["reason"])

    def test_a_host_with_no_uevents_says_so_and_re_checks_on_request(self):
        r = self.rig(start=False, uevents=False)
        r.present = False
        r.broker.start()
        self.assertFalse(r.broker.live)
        self.assertIn("no uevents", r.broker.reason)
        r.present = True
        self.assertEqual(r.broker.state("dbg")["state"], "absent")
        r.broker.refresh()
        self.assertEqual(r.broker.state("dbg")["state"], "open")

    def test_an_adapter_held_by_another_process_is_held_not_an_error_and_not_opened(self):
        r = self.rig(start=False)
        other = Port(r.node, 115200)
        self.addCleanup(other.close)
        r.broker.start()
        st = r.broker.state("dbg")
        self.assertEqual(st["state"], "held")
        self.assertIn("locked", st["reason"])
        other.close()
        r.broker.refresh()
        self.assertEqual(r.broker.state("dbg")["state"], "open")

    def test_an_unusable_link_is_listed_with_its_problem_and_its_port_is_never_opened(self):
        spy = mock.Mock(side_effect=lambda node, baud: Port(node, baud))
        r = self.rig(raws=[raw_link(baud=1200), raw_link(name="ok")], open_port=spy)
        by = dict((s["name"], s) for s in r.broker.states())
        self.assertEqual(by["dbg"]["state"], "unusable")
        self.assertIn("bootloader touch", by["dbg"]["reason"])
        self.assertEqual(by["ok"]["state"], "open", "the good link beside it is unaffected")
        self.assertEqual([c[0][1] for c in spy.call_args_list], [115200], "only the usable link was ever opened")

    def test_a_port_that_cannot_be_opened_is_an_error_with_the_reason(self):
        def boom(node, baud):
            raise port.PortError("cannot open: no such device")
        r = self.rig(open_port=boom)
        self.assertEqual(r.broker.state("dbg")["state"], "error")
        self.assertIn("cannot open", r.broker.state("dbg")["reason"])

    def test_reopen_closes_and_opens_again_on_the_brokers_own_thread(self):
        r = self.rig()
        self.assertEqual(r.opened_on, ["MainThread"], "the first level is taken before the thread exists")
        st = r.broker.reopen("dbg")
        self.assertEqual(st["state"], "open")
        self.assertEqual(r.opened_on[-1], "console-broker", "a request from another thread runs on the broker's")
        self.assertIsNone(r.broker.reopen("nope"))

    def test_stop_ends_the_thread_closes_the_port_and_is_safe_twice(self):
        r = self.rig()
        r.broker.stop()
        r.broker.stop()
        self.assertFalse([t for t in threading.enumerate() if t.name == "console-broker"])
        Port(r.node, 115200).close()                        # the lock was released


if __name__ == "__main__":
    unittest.main(verbosity=1)
