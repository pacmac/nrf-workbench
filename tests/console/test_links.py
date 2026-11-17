#!/usr/bin/env python3
"""Tests for console.links: declared debug links, validated when loaded, and the adapter found by identity.

What matters: an invalid link is listed with its problems and never opened; a board's own ports and 1200 baud are
refused by name; sending needs a wire AND a measured voltage while reading needs neither; and resolving an adapter
reads sysfs and stat()s a node but never opens it.
Run:  python3 tests/console/test_links.py
"""
import builtins
import copy
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

import yaml  # noqa: E402
from fakes import Tree  # noqa: E402
from nrf_workbench.console import links  # noqa: E402

DONGLE = ("1a86", "55d4")
BOARD_IDS = ["239a:8029", "239a:0029"]


def good(**kw):
    raw = {"name": "bench-1-debug", "slot": "SLOT1", "backend": "usb-adapter",
           "adapter": {"ids": ["1a86:55d4"], "serial": "5B1F007437"}, "baud": 115200,
           "dut_tx_to_host": True, "host_tx_to_dut": False, "voltage": {"dut": 3.3, "adapter": 3.3, "verified": False},
           "dtr_rts_reach_dut": False, "reply_marker": "< "}
    raw.update(kw)
    return raw


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data, name="links.yaml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as fh:
            fh.write(data if isinstance(data, str) else yaml.safe_dump(data))
        return path

    def load(self, raws, board_ids=BOARD_IDS):
        found, file_problems = links.load(self.write({"links": raws}), board_ids)
        self.assertEqual(file_problems, [])
        return found

    def problems(self, **kw):
        (link,) = self.load([good(**kw)])
        return [(p.code, p.message) for p in link.problems]

    def codes(self, **kw):
        return sorted(set(c for c, _m in self.problems(**kw)))


class Validation(Files):
    def test_a_good_link_is_usable_and_says_what_it_is(self):
        (link,) = self.load([good()])
        self.assertTrue(link.usable)
        d = link.to_dict()
        self.assertEqual((d["name"], d["backend"], d["baud"], d["rx_only"], d["problems"]), ("bench-1-debug", "usb-adapter", 115200, True, []))
        self.assertEqual(d["voltage"], {"dut": 3.3, "adapter": 3.3, "verified": False})

    def test_the_name_is_a_slug_and_unique(self):
        for bad in ("", "Bad Name", "../x", "-x", "a" * 65, None, 7):
            self.assertIn("invalid", self.codes(name=bad), repr(bad))
        first, second = self.load([good(), good()])
        self.assertTrue(first.usable)
        self.assertEqual([p.code for p in second.problems], ["invalid"])
        self.assertIn("used by another link", second.problems[0].message)

    def test_an_unknown_setting_is_a_problem_so_a_typo_is_not_silently_ignored(self):
        self.assertIn(("invalid", "unknown setting boud"), self.problems(boud=9600))

    def test_the_backend_is_known_and_pi_uart_is_listed_but_not_built(self):
        self.assertIn("invalid", self.codes(backend="rtt"))
        raws = good(backend="pi-uart")
        raws.pop("adapter")
        (link,) = self.load([raws])
        self.assertEqual([p.code for p in link.problems], ["backend_not_built"])
        self.assertFalse(link.usable)

    def test_the_adapter_is_identified_by_ids_and_a_serial(self):
        for ids in ([], None, ["1A86:55D4"], ["1a86"], ["1a86:55d4:00"], [7]):
            self.assertIn("invalid", self.codes(adapter={"ids": ids, "serial": "x"}), repr(ids))
        self.assertIn("invalid", self.codes(adapter={"ids": ["1a86:55d4"]}))
        self.assertIn("invalid", self.codes(adapter={"ids": ["1a86:55d4"], "serial": ""}))

    def test_a_board_profiles_own_ids_are_refused_so_a_devices_own_port_is_never_opened(self):
        self.assertEqual(self.codes(adapter={"ids": ["239a:8029"], "serial": "x"}), ["not_a_declared_link"])
        self.assertEqual(self.codes(adapter={"ids": ["1a86:55d4", "239a:0029"], "serial": "x"}), ["not_a_declared_link"])

    def test_1200_baud_is_refused_by_name_and_so_is_any_rate_that_is_not_standard(self):
        self.assertEqual(self.codes(baud=1200), ["baud_refused"])
        self.assertNotIn(1200, links.BAUDS)
        self.assertIn("bootloader touch", self.problems(baud=1200)[0][1], "1200 gets its own reason, not the generic one")
        for baud in (0, 12345, "115200", None, 115200.5):
            self.assertEqual(self.codes(baud=baud), ["baud_refused"], repr(baud))
        for baud in links.BAUDS:
            self.assertEqual(self.problems(baud=baud), [], baud)

    def test_every_wiring_fact_must_be_declared(self):
        for key in ("dut_tx_to_host", "host_tx_to_dut", "dtr_rts_reach_dut", "voltage"):
            raw = good()
            del raw[key]
            (link,) = self.load([raw])
            self.assertIn("invalid", [p.code for p in link.problems], key)
        self.assertIn("invalid", self.codes(host_tx_to_dut="yes"))

    def test_dtr_or_rts_reaching_the_device_is_refused(self):
        self.assertEqual(self.codes(dtr_rts_reach_dut=True), ["dtr_rts_unsafe"])

    def test_the_marker_and_the_line_ending(self):
        self.assertEqual(self.problems(reply_marker=None), [])
        self.assertIn("invalid", self.codes(reply_marker=""))
        for eol in ("\n", "\r", "\r\n"):
            self.assertEqual(self.problems(eol=eol), [], repr(eol))
        self.assertIn("invalid", self.codes(eol="x"))

    def test_a_problem_in_one_link_does_not_spoil_the_others(self):
        ok, bad = self.load([good(), good(name="other", baud=1200)])
        self.assertTrue(ok.usable)
        self.assertFalse(bad.usable)


class Sending(Files):
    def test_reading_needs_nothing_but_sending_needs_a_wire_and_a_measured_voltage(self):
        def link(**kw):
            v = kw.pop("verified", False)
            return self.load([good(voltage={"dut": 3.3, "adapter": 3.3, "verified": v}, **kw)])[0]
        self.assertEqual(link().can_send()[:2], (False, "link_rx_only"))                       # receive-only wins
        self.assertEqual(link(host_tx_to_dut=True).can_send()[:2], (False, "voltage_unverified"))
        self.assertEqual(link(host_tx_to_dut=True, verified=True).can_send(), (True, "ok", ""))
        self.assertEqual(link(host_tx_to_dut=False, verified=True).can_send()[:2], (False, "link_rx_only"))

    def test_the_voltage_is_only_verified_when_it_is_exactly_true(self):
        for value in ("true", 1, "yes", None):
            (link,) = self.load([good(host_tx_to_dut=True, voltage={"dut": 3.3, "adapter": 3.3, "verified": value})])
            self.assertFalse(link.voltage["verified"], repr(value))


class Loading(Files):
    def test_no_path_a_missing_file_bad_yaml_and_a_wrong_shape_are_problems_never_crashes(self):
        self.assertEqual(links.load(None), ([], ["no links file is configured"]))
        found, problems = links.load(os.path.join(self.tmp.name, "nope.yaml"))
        self.assertEqual((found, len(problems)), ([], 1))
        found, problems = links.load(self.write("links: [ not closed"))
        self.assertEqual((found, len(problems)), ([], 1))
        for shape in ("- a\n- b\n", "links: 7\n", "links: {a: 1}\n"):
            found, problems = links.load(self.write(shape))
            self.assertEqual((found, len(problems)), ([], 1), shape)

    def test_an_empty_file_and_an_empty_list_are_no_links_and_no_problem(self):
        self.assertEqual(links.load(self.write("")), ([], []))
        self.assertEqual(links.load(self.write("links: []\n")), ([], []))

    def test_an_unknown_top_level_setting_is_a_problem(self):
        found, problems = links.load(self.write({"links": [], "linkz": []}))
        self.assertEqual(found, [])
        self.assertIn("unknown setting linkz", problems[0])


class Resolving(Files):
    def setUp(self):
        Files.setUp(self)
        self.tree = Tree()
        self.addCleanup(self.tree.close)
        (self.link,) = self.load([good()])

    def dongle(self, name="1-4", tty="ttyACM1", majmin="166:1", serial="5B1F007437"):
        self.tree.usb(name, DONGLE, "USB Single Serial", serial=serial, manufacturer="wch.cn", tty=tty, majmin=majmin)
        a, b = majmin.split(":")
        return self.tree.node(tty, int(a), int(b))

    def resolve(self):
        return links.resolve(self.link, self.tree.sysfs, self.tree.dev)

    def test_the_adapter_is_found_by_ids_and_serial_and_its_node_by_device_number(self):
        node = self.dongle()
        found, why = self.resolve()
        self.assertEqual((found["node"], why), (node, ""))
        self.assertEqual(found["device"]["serial"], "5B1F007437")

    def test_an_adapter_that_is_not_there_is_absent_and_says_which(self):
        found, why = self.resolve()
        self.assertIsNone(found)
        self.assertIn("5B1F007437", why)
        self.assertIn("not attached", why)

    def test_the_wrong_serial_or_the_wrong_ids_do_not_match(self):
        self.dongle(serial="OTHER")
        self.assertIsNone(self.resolve()[0])
        self.tree.unplug("1-4")
        self.tree.usb("1-5", ("1a86", "7523"), "other", serial="5B1F007437", tty="ttyUSB0", majmin="188:0")
        self.assertIsNone(self.resolve()[0])

    def test_a_node_name_is_never_trusted_only_the_device_number(self):
        self.dongle()
        self.tree.usb("1-9", ("0000", "0000"), "decoy", serial="x", tty="ttyACM9", majmin="166:9")
        os.rename(os.path.join(self.tree.dev, "ttyACM1"), os.path.join(self.tree.dev, "ttyACM1.moved"))
        self.tree.node("ttyACM1", 166, 7)                    # the NAME is right, the device number is not
        found, why = self.resolve()
        self.assertIsNone(found)
        self.assertIn("different device number", why)

    def test_two_devices_with_the_same_ids_and_serial_choose_no_node(self):
        self.dongle()
        self.tree.usb("1-6", DONGLE, "USB Single Serial", serial="5B1F007437", tty="ttyACM2", majmin="166:2")
        found, why = self.resolve()
        self.assertIsNone(found)
        self.assertIn("more than one", why)

    def test_an_unusable_link_is_not_resolved_at_all(self):
        self.dongle()
        (bad,) = self.load([good(baud=1200)])
        found, why = links.resolve(bad, self.tree.sysfs, self.tree.dev)
        self.assertIsNone(found)
        self.assertIn("not usable", why)

    def test_resolving_reads_sysfs_and_stats_the_node_and_never_opens_it(self):
        node = self.dongle()
        real = builtins.open
        opened = []

        def spy(path, *a, **k):
            opened.append(str(path))
            return real(path, *a, **k)

        with mock.patch.object(builtins, "open", spy), mock.patch.object(os, "open", side_effect=AssertionError("os.open")):
            found, _ = self.resolve()
        self.assertEqual(found["node"], node)
        self.assertNotIn(node, opened)
        self.assertTrue(opened, "the spy saw nothing, so it proves nothing")


if __name__ == "__main__":
    unittest.main(verbosity=1)
