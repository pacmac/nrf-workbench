#!/usr/bin/env python3
"""The gate: only `nrf_workbench/console/port.py` opens a serial device for reading and writing (docs/specs/console.md,
"safety"). A gate that has never failed proves nothing, so `test_the_gate_goes_red` plants an offender in a scratch copy
and shows this scan catching it. No file may name a tty path, and only port.py may use terminal control. (The
serial-library and 1200-baud patterns, which have the touch's `devices/entry.py` as their named exception, have their own
gate in tests/devices/test_entry.py.)
Run:  python3 tests/console/test_gate.py
"""
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PACKAGE = os.path.join(ROOT, "nrf_workbench")

PORT = os.path.join("console", "port.py")

# Fragments, so this file does not trip the host's port guard itself. Each pattern lists the files allowed to contain it.
# No file at all may name a tty path: a node is always an OUTPUT of identity resolution, never a literal.
RULES = [
    ("a device path", r"/dev/" + "tty", ()),
    ("a port opened without a controlling terminal", "O_" + "NOCTTY", (PORT,)),
    ("exclusive-use ioctl", "TIOC" + "EXCL", (PORT,)),
    ("terminal control", r"\bterm" + r"ios\b", (PORT,)),
]


def scan(package):
    """[(relative path, what)] for every file outside the allowances that contains a forbidden pattern."""
    offenders = []
    for dirpath, dirs, files in os.walk(package):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "static")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, package)
            with open(path) as fh:
                text = fh.read()
            for what, pattern, allowed in RULES:
                if rel not in allowed and re.search(pattern, text):
                    offenders.append((rel, what))
    return sorted(offenders)


class Gate(unittest.TestCase):
    def test_nothing_outside_the_named_allowances_opens_a_serial_device(self):
        self.assertEqual(scan(PACKAGE), [], "a module outside the named allowances opens or names a serial device")

    def test_the_allowances_are_real_files_that_really_need_them(self):
        # an allowance that points at a file which no longer needs it is a hole kept open for nothing
        for what, pattern, allowed in RULES:
            for rel in allowed:
                text = open(os.path.join(PACKAGE, rel)).read()
                self.assertRegex(text, pattern, "%s no longer contains %s, so its allowance should go" % (rel, what))

    def test_the_port_module_is_the_only_one_in_the_console_package_that_touches_the_terminal(self):
        console = os.path.join(PACKAGE, "console")
        for name in sorted(os.listdir(console)):
            if name.endswith(".py") and name != "port.py":
                text = open(os.path.join(console, name)).read()
                self.assertNotRegex(text, r"\bos\.open\(|\bfcntl\b|\btermios\b", "console/%s must go through port.py" % name)

    def test_the_gate_goes_red(self):
        tmp = tempfile.mkdtemp(prefix="gate-")
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = os.path.join(tmp, "nrf_workbench")
        shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__", "static"))
        self.assertEqual(scan(copy), [], "the copy must start clean, or the next assertion proves nothing")
        planted = os.path.join(copy, "programmer", "sneaky.py")
        with open(planted, "w") as fh:
            fh.write("import os\nfd = os.open('/dev/" + "ttyACM0', os.O_RDWR | os.O_" + "NOCTTY)\n")
        found = scan(copy)
        self.assertEqual([rel for rel, _w in found], [os.path.join("programmer", "sneaky.py")] * 2)
        os.remove(planted)
        with open(os.path.join(copy, "console", "broker.py"), "a") as fh:
            fh.write("\nimport term" + "ios\n")
        self.assertIn((os.path.join("console", "broker.py"), "terminal control"), scan(copy),
                      "even a console module other than port.py is caught")


if __name__ == "__main__":
    unittest.main(verbosity=1)
