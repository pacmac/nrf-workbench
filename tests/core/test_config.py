#!/usr/bin/env python3
"""Tests for core.config: layering, no assumed folder, unknown keys reported, and the listen rules.

The listen rules are the safety ones: the service flashes and power-cycles hardware, so leaving
loopback without a token refuses to start, whatever the access mode.
Run:  python3 tests/core/test_config.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.core import config  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text, mode=0o600):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as fh:
            fh.write(text)
        os.chmod(path, mode)
        return path


class Defaults(Base):
    def test_loopback_on_8080_and_no_folder_is_assumed(self):
        cfg, problems = config.load(env={})
        self.assertEqual(problems, [])
        self.assertEqual(cfg["service"]["listen"], "127.0.0.1:8080")
        self.assertEqual(cfg["service"]["access"], "token")
        self.assertEqual(cfg["paths"], {"lock": None, "profiles": None, "firmware": None, "units": None,
                                        "links": None, "console": None, "gpio": None})
        self.assertEqual(cfg["streams"]["console"], {"retain": None, "max_age_days": None})   # no default bound
        for banned in ("/usr/share/pac", "/etc", "/root", "/home", "/tmp"):
            self.assertNotIn(banned, json.dumps(config.DEFAULTS), "a folder is assumed")

    def test_defaults_are_not_shared_between_loads(self):
        a, _ = config.load(env={}, overrides={"paths": {"lock": "/x"}})
        b, _ = config.load(env={})
        self.assertIsNone(b["paths"]["lock"])
        self.assertIsNone(config.DEFAULTS["paths"]["lock"])
        self.assertEqual(a["paths"]["lock"], "/x")

    def test_a_caller_changing_a_loaded_config_cannot_change_the_defaults(self):
        # No layer touches these sections, so without a copy the caller would be holding the defaults.
        a, _ = config.load(env={})
        a["paths"]["lock"] = "/changed"
        a["service"]["listen"] = "0.0.0.0:80"
        self.assertIsNone(config.DEFAULTS["paths"]["lock"])
        self.assertEqual(config.DEFAULTS["service"]["listen"], "127.0.0.1:8080")
        b, _ = config.load(env={})
        self.assertEqual((b["paths"]["lock"], b["service"]["listen"]), (None, "127.0.0.1:8080"))


class Layers(Base):
    def test_later_layers_override_earlier_ones(self):
        host = self.write("host.yaml", "service:\n  listen: 0.0.0.0:80\npaths:\n  firmware: /host/fw\n")
        user = self.write("user.yaml", "paths:\n  firmware: /user/fw\n")
        cfg, problems = config.load(files=[host, user], env={})
        self.assertEqual(problems, [])
        self.assertEqual(cfg["service"]["listen"], "0.0.0.0:80")          # only the host file set it
        self.assertEqual(cfg["paths"]["firmware"], "/user/fw")             # the user file won
        cfg, _ = config.load(files=[host, user], env={"NRFWB_FIRMWARE_DIR": "/env/fw"})
        self.assertEqual(cfg["paths"]["firmware"], "/env/fw")              # the environment beats the files
        cfg, _ = config.load(files=[host, user], env={"NRFWB_FIRMWARE_DIR": "/env/fw"},
                             overrides={"paths": {"firmware": "/flag/fw"}})
        self.assertEqual(cfg["paths"]["firmware"], "/flag/fw")             # a flag beats the environment

    def test_an_empty_environment_variable_is_not_a_setting(self):
        cfg, _ = config.load(env={"NRFWB_LISTEN": ""})
        self.assertEqual(cfg["service"]["listen"], "127.0.0.1:8080")

    def test_an_optional_file_may_be_absent_a_required_one_may_not(self):
        cfg, problems = config.load(files=[os.path.join(self.tmp.name, "nope.yaml")], env={})
        self.assertEqual(problems, [])
        with self.assertRaises(config.ConfigError):
            config.load(required_files=[os.path.join(self.tmp.name, "nope.yaml")], env={})

    def test_a_file_that_is_not_a_mapping_is_an_error(self):
        with self.assertRaises(config.ConfigError):
            config.load(files=[self.write("bad.yaml", "- a\n- b\n")], env={})

    def test_an_empty_file_is_fine(self):
        cfg, problems = config.load(files=[self.write("empty.yaml", "")], env={})
        self.assertEqual(problems, [])

    def test_an_unknown_setting_is_reported_with_its_path(self):
        path = self.write("typo.yaml", "service:\n  listn: 0.0.0.0:80\n  access: token\nfoo: 1\n")
        cfg, problems = config.load(files=[path], env={})
        text = " ".join(problems)
        self.assertIn("service.listn", text)
        self.assertIn("foo", text)
        self.assertEqual(cfg["service"]["listen"], "127.0.0.1:8080", "a typo must not look like it worked")

    def test_a_section_that_is_not_a_mapping_is_reported(self):
        cfg, problems = config.load(files=[self.write("x.yaml", "service: 5\n")], env={})
        self.assertTrue(any("service" in p and "mapping" in p for p in problems), problems)

    def test_an_unknown_override_is_reported(self):
        _, problems = config.load(env={}, overrides={"service": {"port": 1}})
        self.assertTrue(any("service.port" in p for p in problems), problems)


class Validation(Base):
    def test_bad_listen_and_bad_access_are_problems(self):
        _, problems = config.load(env={}, overrides={"service": {"listen": "nonsense", "access": "everyone"}})
        text = " ".join(problems)
        self.assertIn("service.listen", text)
        self.assertIn("service.access", text)

    def test_the_three_access_modes_are_fine(self):
        for mode in ("token", "open-read", "open"):
            _, problems = config.load(env={}, overrides={"service": {"access": mode}})
            self.assertEqual(problems, [], mode)


class Streams(Base):
    """Retained streams: both bounds or neither, and no default for either."""

    def test_no_stream_has_a_bound_by_default(self):
        cfg, problems = config.load(env={})
        self.assertEqual(problems, [])
        for name in ("events", "activity"):
            self.assertEqual(cfg["streams"][name], {"retain": None, "max_age_days": None})
            self.assertIsNone(config.stream_bounds(cfg, name))

    def test_both_bounds_give_a_count_and_seconds(self):
        cfg, problems = config.load(env={}, overrides={"streams": {"activity": {"retain": 500, "max_age_days": 2}}})
        self.assertEqual(problems, [])
        self.assertEqual(config.stream_bounds(cfg, "activity"), (500, 2 * 86400))
        self.assertIsNone(config.stream_bounds(cfg, "events"), "one stream configured does not configure the other")

    def test_days_may_be_fractional(self):
        cfg, _ = config.load(env={}, overrides={"streams": {"events": {"retain": 10, "max_age_days": 0.5}}})
        self.assertEqual(config.stream_bounds(cfg, "events"), (10, 43200))

    def test_one_bound_without_the_other_is_a_problem_naming_the_stream(self):
        for half in ({"retain": 10}, {"max_age_days": 3}):
            _, problems = config.load(env={}, overrides={"streams": {"activity": half}})
            self.assertEqual(len(problems), 1, half)
            self.assertIn("streams.activity", problems[0])
            self.assertIn("BOTH", problems[0])

    def test_unusable_values_are_problems(self):
        bad = ({"retain": 0, "max_age_days": 1}, {"retain": -5, "max_age_days": 1}, {"retain": True, "max_age_days": 1},
               {"retain": 1.5, "max_age_days": 1}, {"retain": "10", "max_age_days": 1},
               {"retain": 10, "max_age_days": 0}, {"retain": 10, "max_age_days": -1}, {"retain": 10, "max_age_days": True},
               {"retain": 10, "max_age_days": "1"}, {"retain": 10, "max_age_days": float("inf")})
        for values in bad:
            _, problems = config.load(env={}, overrides={"streams": {"events": values}})
            self.assertTrue(problems, values)
            self.assertIn("streams.events", problems[0], values)

    def test_a_file_can_set_the_bounds_and_a_typo_is_reported(self):
        path = self.write("c.yaml", "streams:\n  activity:\n    retain: 100\n    max_age_days: 7\n    retian: 5\n")
        cfg, problems = config.load(files=[path], env={})
        self.assertTrue(any("streams.activity.retian" in p for p in problems), problems)
        self.assertEqual(config.stream_bounds(cfg, "activity"), (100, 7 * 86400))

    def test_a_stream_that_does_not_exist_is_an_unknown_setting(self):
        _, problems = config.load(env={}, overrides={"streams": {"console": {"retain": 1}}})
        self.assertTrue(any("streams.console" in p for p in problems), problems)


class Listen(unittest.TestCase):
    def test_parse_listen(self):
        self.assertEqual(config.parse_listen("0.0.0.0:80"), ("0.0.0.0", 80))
        self.assertEqual(config.parse_listen(" 127.0.0.1:8080 "), ("127.0.0.1", 8080))
        self.assertEqual(config.parse_listen("[::1]:8080"), ("::1", 8080))
        self.assertEqual(config.parse_listen("myhost.example:65535"), ("myhost.example", 65535))

    def test_bad_listen_values(self):
        for bad in ("", None, "nohost", ":80", "127.0.0.1", "127.0.0.1:0", "127.0.0.1:65536", "127.0.0.1:eighty",
                    "[::1]8080", "[::1", "127.0.0.1:-1", 80):
            with self.assertRaises(ValueError, msg=repr(bad)):
                config.parse_listen(bad)

    def test_loopback(self):
        for host in ("127.0.0.1", "127.0.0.5", "::1", "localhost"):
            self.assertTrue(config.is_loopback(host), host)
        for host in ("0.0.0.0", "::", "10.0.0.5", "myhost.example", "", "127.0.0.1.evil.example"):
            self.assertFalse(config.is_loopback(host), host)

    def test_loopback_may_start_with_no_token(self):
        self.assertIsNone(config.check_listen("127.0.0.1:8080", None))
        self.assertIsNone(config.check_listen("[::1]:8080", None))

    def test_leaving_loopback_without_a_token_refuses_whatever_the_port(self):
        for listen in ("0.0.0.0:80", "0.0.0.0:8080", "10.0.0.5:8080", "[::]:80", "myhost.example:8080"):
            for token in (None, ""):
                r = config.check_listen(listen, token)
                self.assertEqual((r.cls, r.code), ("refused", "token_missing"), (listen, token))

    def test_open_access_may_start_on_the_network_with_no_token_and_nothing_else_may(self):
        for listen in ("0.0.0.0:80", "10.0.0.5:8080", "[::]:80", "myhost.example:8080"):
            self.assertIsNone(config.check_listen(listen, None, "open"), listen)
            for access in ("token", "open-read"):
                self.assertEqual(config.check_listen(listen, None, access).code, "token_missing", (listen, access))
        self.assertEqual(config.check_listen("0.0.0.0:80", None).code, "token_missing", "the default is still the strict one")

    def test_leaving_loopback_with_a_token_may_start(self):
        self.assertIsNone(config.check_listen("0.0.0.0:80", "s3cret"))


class Token(Base):
    def test_a_private_token_file_is_read_and_stripped(self):
        token, note = config.read_token(self.write("tok", "  abc123\n", 0o600))
        self.assertEqual((token, note), ("abc123", None))

    def test_a_token_file_others_can_read_is_noted(self):
        token, note = config.read_token(self.write("tok", "abc123\n", 0o644))
        self.assertEqual(token, "abc123")
        self.assertIn("readable by other users", note)

    def test_missing_empty_or_unset(self):
        self.assertEqual(config.read_token(None), (None, None))
        token, note = config.read_token(os.path.join(self.tmp.name, "nope"))
        self.assertIsNone(token)
        self.assertIn("cannot be read", note)
        self.assertEqual(config.read_token(self.write("empty", "  \n", 0o600))[0], None)


if __name__ == "__main__":
    unittest.main(verbosity=1)
