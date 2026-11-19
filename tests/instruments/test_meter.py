#!/usr/bin/env python3
"""Tests for the LAN multimeter (docs/specs/meter.md), against a fake SCPI meter on a REAL socket (framing, silence and close are real).

The fake records every line it receives. The invariant under test is the spec's first defect: the service writes no setting to the meter,
so every line it sends ends in `?`.
Run:  python3 tests/instruments/test_meter.py
"""
import os
import socket
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.core.registry import Context, Registry, UsageError  # noqa: E402
from nrf_workbench.instruments import meter, scpi  # noqa: E402

IDN = "Siglent Technologies,SDM3045X,SDM35EAC1L0704,5.01.01.09R2"
# The meter as it was found on 2026-11-19 (current mode, range 0.6, auto off) but with NPLC 0.02 so a reading takes 14 ms of period, not 0.2 s.
CURRENT = {"*IDN?": IDN, "FUNC?": '"CURR"', "CURR:DC:RANG?": "+6.00000000E-01", "CURR:DC:RANG:AUTO?": "0",
           "CURR:DC:NPLC?": "+2.00000000E-02", "READ?": "+1.71706024E-03", "SYST:ERR?": '0,"No error"'}


class FakeMeter(object):
    """A threaded raw-socket SCPI server. `table` maps a query to its answer; a query not in it gets NO reply (the real meter's behaviour)."""

    def __init__(self, table):
        self.table = dict(table)
        self.sent = []                       # every line received, in order
        self.open, self.max_open, self.connections = 0, 0, 0
        self._guard = threading.Lock()
        self.answers = {}                    # query -> list of answers, consumed in turn (for a series of readings)
        self.delay = {}                      # query -> seconds to wait before answering
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self._guard:
                self.open += 1
                self.connections += 1
                self.max_open = max(self.max_open, self.open)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    text = line.decode().strip()
                    with self._guard:
                        self.sent.append(text)
                        queue = self.answers.get(text)
                        answer = queue.pop(0) if queue else self.table.get(text)
                    time.sleep(self.delay.get(text, 0))
                    if answer is not None:
                        conn.sendall((answer + "\n").encode())
        except OSError:
            return
        finally:
            conn.close()
            with self._guard:
                self.open -= 1

    def close(self):
        self.sock.close()


class Host(object):
    def __init__(self, address):
        cfg, problems = cfgmod.load(env={}, overrides={"instruments": {"meter": address}})
        assert problems == [], problems
        self.config, self.hooks = cfg, {}


def setUpMeter(case, table=None, address=None):
    case.fake = FakeMeter(CURRENT if table is None else table)
    case.addCleanup(case.fake.close)
    case.host = Host(address or "127.0.0.1:%d" % case.fake.port)
    case.reg = Registry()
    meter.register(case.reg, case.host)


def call(case, name, args=None, caps=("meter",)):
    return case.reg.call(name, args or {}, Context(case.host, "tester", None), capabilities=set(caps))


class Shown(unittest.TestCase):
    def setUp(self):
        setUpMeter(self)

    def test_show_says_what_it_is_and_how_it_is_set(self):
        r = call(self, "meter.show")
        self.assertTrue(r.ok, r)
        d = r.data
        self.assertEqual((d["identity"], d["function"], d["word"], d["unit"]), (IDN, "CURR", "dci", "A"))
        self.assertEqual((d["range"], d["autorange"], d["nplc"], d["error"]), (0.6, False, 0.02, '0,"No error"'))
        self.assertTrue(d["in_series"])
        self.assertIn("in series", r.message)

    def test_a_voltage_meter_is_not_in_series(self):
        table = dict(CURRENT, **{"FUNC?": '"VOLT"', "VOLT:DC:RANG?": "+6.0E+00", "VOLT:DC:RANG:AUTO?": "1", "VOLT:DC:NPLC?": "+1.0E+00"})
        setUpMeter(self, table)
        d = call(self, "meter.show").data
        self.assertEqual((d["word"], d["unit"], d["in_series"], d["autorange"]), ("dcv", "V", False, True))

    def test_a_function_it_does_not_know_still_shows_and_says_the_unit_is_unknown(self):
        table = dict(CURRENT, **{"FUNC?": '"DIOD"'})
        setUpMeter(self, table)
        d = call(self, "meter.show").data
        self.assertEqual((d["function"], d["word"], d["unit"], d["range"], d["nplc"]), ("DIOD", None, "", None, None))

    def test_the_connection_is_closed_after_the_call_whatever_it_returned(self):
        """Closed by the service, not left to the garbage collector: a leaked socket would hold the meter's one SCPI session."""
        closed = []
        real = scpi.Connection.close
        scpi.Connection.close = lambda conn: (closed.append(1), real(conn))[1]
        self.addCleanup(setattr, scpi.Connection, "close", real)
        call(self, "meter.show")
        self.assertEqual(len(closed), 1)
        del self.fake.table["READ?"]
        del closed[:]
        call(self, "meter.read")                              # silent: the call's own connection and the error-queue one, both closed
        self.assertEqual(len(closed), 2)
        deadline = time.monotonic() + 2
        while self.fake.open and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.fake.open, 0)


class Read(unittest.TestCase):
    def setUp(self):
        setUpMeter(self)

    def test_one_reading_with_its_unit_and_setting(self):
        r = call(self, "meter.read")
        self.assertTrue(r.ok, r)
        self.assertAlmostEqual(r.data["value"], 1.71706024e-3)
        self.assertEqual((r.data["unit"], r.data["function"], r.data["range"], r.data["overload"]), ("A", "CURR", 0.6, False))
        self.assertEqual(r.data["raw"], "+1.71706024E-03")

    def test_the_expected_function_reads(self):
        self.assertTrue(call(self, "meter.read", {"expect_function": "dci"}).ok)

    def test_a_different_function_is_refused_and_nothing_is_read(self):
        r = call(self, "meter.read", {"expect_function": "dcv"})
        self.assertEqual((r.cls, r.code), ("refused", "meter_function"))
        self.assertEqual(r.evidence["function"], "CURR")
        self.assertEqual(r.evidence["expected"], "dcv")
        self.assertNotIn("READ?", self.fake.sent)

    def test_an_expected_function_that_is_not_a_function_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            call(self, "meter.read", {"expect_function": "volts"})

    def test_overload_is_suspect_and_not_a_number_to_use(self):
        self.fake.table["READ?"] = "+9.90000000E+37"
        r = call(self, "meter.read")
        self.assertEqual((r.cls, r.code), ("suspect", "meter_overload"))
        self.assertTrue(r.data["overload"])
        self.assertEqual(r.evidence["raw"], "+9.90000000E+37")

    def test_a_reply_that_is_not_a_number_is_no_reply(self):
        self.fake.table["READ?"] = "garbage"
        r = call(self, "meter.read")
        self.assertEqual((r.cls, r.code), ("failed", "no_reply"))
        self.assertEqual(r.evidence["raw"], "garbage")

    def test_a_silent_meter_is_no_reply_within_the_ceiling_with_the_meters_own_error(self):
        del self.fake.table["READ?"]
        self.fake.answers["SYST:ERR?"] = ['-113,"Undefined header"']
        started = time.monotonic()
        r = call(self, "meter.read")
        took = time.monotonic() - started
        self.assertEqual((r.cls, r.code), ("failed", "no_reply"))
        self.assertEqual((r.evidence["query"], r.evidence["error"]), ("READ?", '-113,"Undefined header"'))
        ceiling = meter.reading_ceiling_s(0.02)
        self.assertLess(took, ceiling + 0.5, "returned at the ceiling, not later")
        self.assertGreaterEqual(took, ceiling - 0.05, "waited the ceiling out")

    def test_a_reading_that_takes_its_period_is_waited_for_and_not_cut_short(self):
        self.fake.delay["READ?"] = 0.26                     # longer than the query ceiling (0.25 s), inside the reading ceiling (2 x 14 ms + 0.25 s)
        self.assertTrue(call(self, "meter.read").ok)


class Unreachable(unittest.TestCase):
    def test_a_closed_port_is_meter_unreachable(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        setUpMeter(self, address="127.0.0.1:%d" % port)
        r = call(self, "meter.show")
        self.assertEqual((r.cls, r.code), ("failed", "meter_unreachable"))
        self.assertIn("127.0.0.1:%d" % port, r.evidence["meter"])


class Sample(unittest.TestCase):
    def setUp(self):
        setUpMeter(self)

    def test_statistics_and_timestamps(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+3.0E-03", "+2.0E-03"]
        self.fake.delay["READ?"] = 0.02
        r = call(self, "meter.sample", {"count": 3})
        self.assertTrue(r.ok, r)
        d = r.data
        self.assertEqual((d["n"], d["min"], d["max"]), (3, 1e-3, 3e-3))
        self.assertAlmostEqual(d["mean"], 2e-3)
        self.assertEqual([p["value"] for p in d["points"]], [1e-3, 3e-3, 2e-3])
        times = [p["t_s"] for p in d["points"]]
        self.assertEqual(times[0], 0)
        self.assertTrue(times[0] < times[1] < times[2], times)
        self.assertGreaterEqual(times[2], 0.035, "two readings of 20 ms apart the first")
        self.assertEqual(d["duration_s"], times[-1])
        self.assertIn("ARRIVED", d["clock"])

    def test_one_overload_makes_the_sample_suspect_and_keeps_the_points(self):
        self.fake.answers["READ?"] = ["+1.0E-03", "+9.9E+37"]
        r = call(self, "meter.sample", {"count": 2})
        self.assertEqual((r.cls, r.code), ("suspect", "meter_overload"))
        self.assertEqual((r.data["n"], r.data["overloads"], r.data["mean"]), (2, 1, 1e-3))

    def test_the_count_is_bounded(self):
        for count in (0, -1, meter.MAX_COUNT + 1):
            with self.assertRaises(UsageError, msg=count):
                call(self, "meter.sample", {"count": count})

    def test_the_span_is_bounded_by_the_meters_own_period(self):
        setUpMeter(self, dict(CURRENT, **{"CURR:DC:NPLC?": "+1.0E+01"}))        # NPLC 10: 0.2 s a reading, so 150 fit in 30 s
        self.assertTrue(call(self, "meter.sample", {"count": 150}).ok)
        with self.assertRaises(UsageError) as cm:
            call(self, "meter.sample", {"count": 151})
        self.assertIn("at most 150", str(cm.exception))

    def test_a_wrong_function_is_refused_before_any_reading(self):
        r = call(self, "meter.sample", {"count": 2, "expect_function": "dcv"})
        self.assertEqual(r.code, "meter_function")
        self.assertNotIn("READ?", self.fake.sent)


class NothingIsEverWritten(unittest.TestCase):
    """The spec's first defect: a setting written to the meter while it is in series cuts the supply to the board under test."""

    def test_every_line_sent_ends_in_a_question_mark(self):
        setUpMeter(self)
        call(self, "meter.show")
        call(self, "meter.read")
        call(self, "meter.read", {"expect_function": "dcv"})
        call(self, "meter.sample", {"count": 3})
        del self.fake.table["READ?"]
        call(self, "meter.read")                              # the silent path asks the error queue on a new connection
        self.assertGreater(len(self.fake.sent), 10)
        self.assertEqual([s for s in self.fake.sent if not s.endswith("?")], [])

    def test_the_transport_refuses_anything_that_is_not_a_query(self):
        setUpMeter(self)
        with scpi.Connection("127.0.0.1", self.fake.port) as conn:
            for line in ("FUNC VOLT", "*RST", "CURR:DC:RANG 0.6", "READ?\nFUNC VOLT", "READ?\r", "", None):
                with self.assertRaises(ValueError, msg=repr(line)):
                    conn.ask(line)
        self.assertEqual(self.fake.sent, [])


class Transport(unittest.TestCase):
    def setUp(self):
        setUpMeter(self)

    def test_a_silence_breaks_the_connection_so_a_late_answer_is_not_taken_for_the_next(self):
        del self.fake.table["READ?"]
        with scpi.Connection("127.0.0.1", self.fake.port) as conn:
            with self.assertRaises(scpi.Silent):
                conn.ask("READ?", ceiling=0.05)
            with self.assertRaises(scpi.ScpiError):
                conn.ask("*IDN?")

    def test_a_silence_that_is_expected_leaves_the_connection_usable(self):
        with scpi.Connection("127.0.0.1", self.fake.port) as conn:
            self.assertIsNone(conn.ask("NOT:A:QUERY?", ceiling=0.05, silent_ok=True))
            self.assertEqual(conn.ask("*IDN?"), IDN)

    def test_callers_are_served_one_at_a_time(self):
        self.fake.delay["READ?"] = 0.05
        results = []
        threads = [threading.Thread(target=lambda: results.append(call(self, "meter.read"))) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r.ok for r in results), results)
        self.assertEqual(self.fake.max_open, 1)


class Numbers(unittest.TestCase):
    def test_the_period_is_the_conversion_or_the_overhead_whichever_is_longer(self):
        self.assertAlmostEqual(meter.period_s(10), 0.2)
        self.assertAlmostEqual(meter.period_s(0.02), meter.READ_OVERHEAD_S)
        self.assertAlmostEqual(meter.period_s(None), 10 * meter.MAINS_CYCLE_S)

    def test_the_reading_ceiling_is_twice_the_period_and_the_query_ceiling(self):
        self.assertAlmostEqual(meter.reading_ceiling_s(10), 2 * 0.2 + scpi.CEILING_S)
        # 3 times what was measured (198 and 215 ms), not orders of magnitude
        self.assertLess(meter.reading_ceiling_s(10), 1.0)


class Configured(unittest.TestCase):
    def test_no_address_means_no_capability_and_no_commands(self):
        cfg, _ = cfgmod.load(env={})
        host = service.Host(cfg, {}, {})
        self.assertNotIn("meter", service.capabilities(host))
        reg = Registry()
        meter.register(reg, host)
        self.assertEqual(reg.commands(capabilities=service.capabilities(host)), [])

    def test_an_address_gives_the_capability_and_the_commands(self):
        cfg, _ = cfgmod.load(env={}, overrides={"instruments": {"meter": "sdm3045x.home"}})
        host = service.Host(cfg, {}, {})
        self.assertIn("meter", service.capabilities(host))
        reg = Registry()
        meter.register(reg, host)
        self.assertEqual([c.name for c in reg.commands(capabilities=service.capabilities(host))],
                         ["meter.read", "meter.sample", "meter.show"])

    def test_the_environment_sets_it(self):
        cfg, problems = cfgmod.load(env={"NRFWB_METER": "10.0.0.5:5025"})
        self.assertEqual((cfg["instruments"]["meter"], problems), ("10.0.0.5:5025", []))

    def test_a_bad_address_is_a_problem_at_start_up(self):
        for bad in ("", "  ", "host:", ":5025", "host:0", "host:99999", "host:abc", 7):
            _, problems = cfgmod.load(env={}, overrides={"instruments": {"meter": bad}})
            self.assertTrue(any("instruments.meter" in p for p in problems), (bad, problems))

    def test_the_address_defaults_to_the_instruments_own_port(self):
        self.assertEqual(meter.parse_address("sdm3045x.home"), ("sdm3045x.home", 5025))
        self.assertEqual(meter.parse_address("10.0.0.5:6000"), ("10.0.0.5", 6000))

    def test_no_command_takes_an_address(self):
        """The address is configuration: an argument naming a host would aim the service at anything the Pi can reach."""
        setUpMeter(self)
        for command in self.reg.commands():
            self.assertEqual([a.name for a in command.args if a.name in ("address", "host", "ip", "port", "url")], [], command.name)


if __name__ == "__main__":
    unittest.main()
