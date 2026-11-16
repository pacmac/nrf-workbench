#!/usr/bin/env python3
"""Tests for core.registry: one row per command, names derived once, arguments validated and typed.

A text source (a query string or a command line) is PARSED; a JSON body is only CHECKED, so the string
"5" is not an int there and a bool is never a number. A float must be finite: an infinite bound is a
bound that never fires.
Run:  python3 tests/core/test_registry.py
"""
import itertools
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core.registry import (Arg, Command, Context, DuplicateCommand, Registry,  # noqa: E402
                                         UnknownCommand, UsageError, cli_words, http_path, mcp_name)
from nrf_workbench.core.result import Result  # noqa: E402


def ok_handler(ctx, **kw):
    return Result.success(dict(kw, caller=ctx.caller))


def cmd(name="demo.run", args=(), **kw):
    return Command(name, "a demo command", ok_handler, args=args, **kw)


class Naming(unittest.TestCase):
    def test_one_name_gives_three_forms(self):
        self.assertEqual(http_path("programmer.flash_file"), "/api/programmer/flash_file")
        self.assertEqual(cli_words("programmer.flash_file"), ("programmer", "flash-file"))
        self.assertEqual(mcp_name("programmer.flash_file"), "programmer_flash_file")

    def test_no_two_commands_can_derive_the_same_mcp_name(self):
        names = ["%s.%s" % (a, c) for a in ("a", "ab", "firmware", "programmer") for c in ("b_c", "bc", "flash", "put_it")]
        for x, y in itertools.combinations(names, 2):
            self.assertNotEqual(mcp_name(x), mcp_name(y), (x, y))

    def test_an_area_with_an_underscore_is_not_a_valid_name(self):
        for bad in ("a_b.c", "Programmer.flash", "programmer", "programmer.", ".x", "programmer.Flash", "a.b.c", ""):
            with self.assertRaises(ValueError, msg=bad):
                cmd(name=bad)


class Rows(unittest.TestCase):
    def test_argument_rules(self):
        with self.assertRaises(ValueError):
            Arg("Bad")
        with self.assertRaises(ValueError):
            Arg("x", type="list")
        with self.assertRaises(ValueError):
            Arg("x", required=True, default=3)
        Arg("x", required=False, default=3, type="int")

    def test_command_rules(self):
        with self.assertRaises(ValueError):
            cmd(effect="delete")
        with self.assertRaises(ValueError):
            cmd(body="json")
        with self.assertRaises(ValueError):
            cmd(body="raw", effect="read")             # a raw-body command changes something
        cmd(body="raw", effect="write")
        with self.assertRaises(ValueError):
            cmd(effect="stream")                       # a stream names the event it yields
        cmd(effect="stream", stream="console.line")
        with self.assertRaises(ValueError):
            cmd(args=(Arg("a"), Arg("a")))
        for reserved in ("ctx", "body"):
            with self.assertRaises(ValueError):
                cmd(args=(Arg(reserved),))
        with self.assertRaises(ValueError):
            Command("demo.run", "x", "not callable")

    def test_to_dict_carries_every_derived_name(self):
        d = cmd(name="programmer.flash_file", args=(Arg("serial", doc="which board"),), effect="write").to_dict()
        self.assertEqual((d["path"], d["cli"], d["mcp"]), ("/api/programmer/flash_file", "programmer flash-file",
                                                           "programmer_flash_file"))
        self.assertEqual(d["args"][0]["name"], "serial")
        self.assertEqual(d["effect"], "write")


class Registration(unittest.TestCase):
    def test_a_duplicate_is_refused_naming_both_modules(self):
        r = Registry()
        r.add(cmd(), module="programmer.commands")
        with self.assertRaises(DuplicateCommand) as caught:
            r.add(cmd(), module="swd.commands")
        self.assertIn("programmer.commands", str(caught.exception))
        self.assertIn("swd.commands", str(caught.exception))

    def test_an_unmet_requirement_makes_the_command_absent_not_failing(self):
        r = Registry()
        r.add(cmd("a.open"))
        r.add(cmd("a.pi_only", requires=("pi_header",)))
        have = {"sysfs"}
        self.assertIsNone(r.get("a.pi_only", have))
        self.assertEqual([c.name for c in r.commands(have)], ["a.open"])
        self.assertEqual(r.unmet(r.get("a.pi_only"), have), ["pi_header"])
        self.assertIsNotNone(r.get("a.pi_only", have | {"pi_header"}))
        self.assertEqual([c.name for c in r.commands({"pi_header"})], ["a.open", "a.pi_only"])
        self.assertEqual(len(r.commands()), 2, "no capability set means: do not filter")

    def test_commands_are_listed_in_name_order(self):
        r = Registry()
        for n in ("b.x", "a.z", "a.y"):
            r.add(cmd(n))
        self.assertEqual([c.name for c in r.commands()], ["a.y", "a.z", "b.x"])


class Validation(unittest.TestCase):
    def setUp(self):
        self.r = Registry()
        self.c = cmd(args=(Arg("serial"), Arg("bound_s", type="float", unit="s"), Arg("count", type="int", required=False,
                                                                                          default=3),
                           Arg("dry", type="bool", required=False, default=False)))

    def v(self, raw, from_text=False):
        return self.r.validate(self.c, raw, from_text)

    def test_defaults_are_applied_and_required_is_enforced(self):
        self.assertEqual(self.v({"serial": "S", "bound_s": 1.5}), {"serial": "S", "bound_s": 1.5, "count": 3, "dry": False})
        with self.assertRaises(UsageError) as caught:
            self.v({"bound_s": 1.5})
        self.assertEqual(caught.exception.arg, "serial")

    def test_an_unknown_argument_is_refused_and_names_what_is_allowed(self):
        with self.assertRaises(UsageError) as caught:
            self.v({"serial": "S", "bound_s": 1, "bnd_s": 5})
        self.assertIn("bound_s", str(caught.exception))

    def test_text_is_parsed(self):
        got = self.v({"serial": "S", "bound_s": "2.5", "count": "7", "dry": "TRUE"}, from_text=True)
        self.assertEqual((got["bound_s"], got["count"], got["dry"]), (2.5, 7, True))
        self.assertEqual(self.v({"serial": "S", "bound_s": "1", "dry": "no"}, from_text=True)["dry"], False)

    def test_bad_text_is_a_usage_error(self):
        for key, bad in (("bound_s", "abc"), ("count", "1.5"), ("count", ""), ("dry", "maybe")):
            with self.assertRaises(UsageError, msg=(key, bad)):
                self.v({"serial": "S", "bound_s": "1", key: bad}, from_text=True)

    def test_a_float_must_be_finite_so_a_bound_cannot_be_infinite(self):
        for bad in ("inf", "-inf", "nan", "Infinity"):
            with self.assertRaises(UsageError, msg=bad):
                self.v({"serial": "S", "bound_s": bad}, from_text=True)
        for bad in (float("inf"), float("nan")):
            with self.assertRaises(UsageError):
                self.v({"serial": "S", "bound_s": bad})

    def test_json_is_checked_not_parsed(self):
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": "2.5"})            # a string is not a number in JSON
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": 1, "count": "7"})
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": 1, "count": 1.5})  # a float is not an int
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": 1, "dry": "true"})
        with self.assertRaises(UsageError):
            self.v({"serial": 5, "bound_s": 1})

    def test_a_bool_is_never_a_number(self):
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": True})
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": 1, "count": True})

    def test_null_for_an_optional_argument_means_not_given_and_for_a_required_one_is_an_error(self):
        got = self.v({"serial": "S", "bound_s": 1, "count": None, "dry": None})
        self.assertEqual((got["count"], got["dry"]), (3, False), "the defaults, as if they were never sent")
        with self.assertRaises(UsageError):
            self.v({"serial": None, "bound_s": 1})
        with self.assertRaises(UsageError):
            self.v({"serial": "S", "bound_s": None})
        # a validated result can be validated again (the CLI does exactly this on its way to call())
        self.assertEqual(self.v(self.v({"serial": "S", "bound_s": 1})), self.v({"serial": "S", "bound_s": 1}))

    def test_an_int_is_a_float_in_json(self):
        got = self.v({"serial": "S", "bound_s": 2})
        self.assertEqual(got["bound_s"], 2.0)
        self.assertIsInstance(got["bound_s"], float)


class Calling(unittest.TestCase):
    def test_the_handler_gets_the_context_and_typed_arguments(self):
        r = Registry()
        r.add(cmd(args=(Arg("n", type="int"),)))
        res = r.call("demo.run", {"n": "4"}, Context(host="H", caller="peter"), from_text=True)
        self.assertEqual((res.cls, res.data), ("ok", {"n": 4, "caller": "peter"}))

    def test_an_unknown_or_unmet_command_is_unknown(self):
        r = Registry()
        r.add(cmd(requires=("x",)))
        with self.assertRaises(UnknownCommand):
            r.call("nope.nothing", {}, Context())
        with self.assertRaises(UnknownCommand):
            r.call("demo.run", {}, Context(), capabilities=set())
        self.assertTrue(r.call("demo.run", {}, Context(), capabilities={"x"}).ok)

    def test_a_handler_that_does_not_return_a_result_is_a_programming_error(self):
        r = Registry()
        r.add(Command("demo.bad", "x", lambda ctx: {"ok": True}))
        with self.assertRaises(TypeError):
            r.call("demo.bad", {}, Context())

    def test_a_usage_error_never_reaches_the_handler(self):
        calls = []
        r = Registry()
        r.add(Command("demo.run", "x", lambda ctx, **kw: calls.append(kw) or Result.success(), args=(Arg("n", type="int"),)))
        with self.assertRaises(UsageError):
            r.call("demo.run", {"n": "x"}, Context(), from_text=True)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
