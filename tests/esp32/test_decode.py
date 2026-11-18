#!/usr/bin/env python3
"""Tests for esp32.decode (docs/specs/esp32.md, "Crash decoding"): the addresses a dump holds, the ELF symbol table read
in pure Python, the optional addr2line, and the command over a stored bundle. The ELF is built here byte by byte, so the
parser is checked against a file whose every symbol is known.
Run:  python3 tests/esp32/test_decode.py
"""
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from nrf_workbench.core.registry import UsageError  # noqa: E402
from nrf_workbench.esp32 import commands, decode  # noqa: E402
from test_esp32 import Base, Tool, bundle, merged  # noqa: E402

FUNCS = [("app_main", 0x400D1000, 0x40), ("do_crash", 0x400D1040, 0x20), ("sized_zero", 0x400D2000, 0)]


def make_elf(funcs=FUNCS, stripped=False, wide=False, extra=()):
    """A minimal ELF32 little-endian file: header, .symtab, .strtab, .shstrtab, and a section table."""
    strtab, sym = b"\0", b"\0" * 16
    for name, value, size in funcs:
        sym += struct.pack("<IIIBBH", len(strtab), value, size, 2, 0, 1)         # STT_FUNC
        strtab += name.encode() + b"\0"
    for name, value in extra:                                                    # STT_OBJECT: not a function
        sym += struct.pack("<IIIBBH", len(strtab), value, 4, 1, 0, 1)
        strtab += name.encode() + b"\0"
    shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
    body_at = 0x34
    sym_at, str_at, shstr_at = body_at, body_at + len(sym), body_at + len(sym) + len(strtab)
    sh_at = shstr_at + len(shstr)
    sections = [struct.pack("<10I", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    if not stripped:
        sections.append(struct.pack("<10I", 1, 2, 0, 0, sym_at, len(sym), 2, 0, 4, 16))       # .symtab, link -> .strtab
    sections.append(struct.pack("<10I", 9, 3, 0, 0, str_at, len(strtab), 0, 0, 1, 0))         # .strtab
    sections.append(struct.pack("<10I", 17, 3, 0, 0, shstr_at, len(shstr), 0, 0, 1, 0))       # .shstrtab
    head = bytearray(0x34)
    head[:4] = b"\x7fELF"
    head[4], head[5], head[6] = 2 if wide else 1, 1, 1
    struct.pack_into("<I", head, 0x20, sh_at)
    struct.pack_into("<HHH", head, 0x2E, 40, len(sections), len(sections) - 1)
    return bytes(head) + sym + strtab + shstr + b"".join(sections)


def tmp(case, data):
    fd, path = tempfile.mkstemp(prefix="esp-elf-")
    case.addCleanup(os.unlink, path)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


class Addresses(unittest.TestCase):
    def test_backtrace_pcs_come_first_in_order_and_once_each(self):
        text = "Backtrace: 0x400d1010:0x3ffb1e50 0x400d1044:0x3ffb1e70 0x400d1010:0x3ffb1e90\n"
        self.assertEqual(decode.addresses(text), [0x400D1010, 0x400D1044])

    def test_a_panic_header_names_the_faulting_pc_and_the_riscv_registers(self):
        text = "Guru Meditation Error: Core 0 panic'ed (LoadProhibited)\nPC      : 0x400d1004  PS      : 0x00060030\n"
        self.assertEqual(decode.addresses(text), [0x400D1004])
        self.assertEqual(decode.addresses("MEPC    : 0x42001234  RA      : 0x42005678\n"), [0x42001234, 0x42005678])

    def test_the_stack_pointer_half_of_a_pair_and_text_without_a_dump_are_not_addresses(self):
        self.assertEqual(decode.addresses("Backtrace: 0x400d1010:0x3ffb1e50"), [0x400D1010])
        self.assertEqual(decode.addresses("boot: ESP-IDF v5.1 2nd stage bootloader\nheap 0x3ffb1e50\n"), [])
        self.assertEqual(decode.addresses(""), [])

    def test_an_address_needs_eight_digits(self):
        self.assertEqual(decode.addresses("Backtrace: 0x400d10:0x3ffb1e50"), [])

    def test_a_long_dump_is_cut_at_the_frame_cap_and_says_so(self):
        text = "Backtrace:" + "".join(" 0x400d%04x:0x3ffb0000" % i for i in range(decode.MAX_FRAMES + 5))
        self.assertEqual(len(decode.addresses(text)), decode.MAX_FRAMES + 5)
        out = decode.decode(text, tmp(self, make_elf()))
        self.assertEqual((out["addresses"], out["truncated"]), (decode.MAX_FRAMES, True))


class Symbols(unittest.TestCase):
    def test_every_function_is_read_with_its_address_and_size_and_nothing_else_is(self):
        table = decode.symbols(tmp(self, make_elf(extra=[("a_variable", 0x3FFB0000)])))
        self.assertEqual(table, sorted((v, s, n) for n, v, s in FUNCS))

    def test_an_address_inside_a_function_gives_its_name_and_how_far_in(self):
        table = decode.symbols(tmp(self, make_elf()))
        self.assertEqual(decode.lookup(table, 0x400D1000), ("app_main", 0))
        self.assertEqual(decode.lookup(table, 0x400D103F), ("app_main", 0x3F))
        self.assertEqual(decode.lookup(table, 0x400D1040), ("do_crash", 0))

    def test_an_address_past_the_end_before_the_start_or_in_a_gap_has_no_name(self):
        table = decode.symbols(tmp(self, make_elf()))
        self.assertIsNone(decode.lookup(table, 0x400D1060))
        self.assertIsNone(decode.lookup(table, 0x400D0FFF))
        self.assertIsNone(decode.lookup(table, 0x10))

    def test_a_symbol_with_no_size_matches_only_its_own_address(self):
        table = decode.symbols(tmp(self, make_elf()))
        self.assertEqual(decode.lookup(table, 0x400D2000), ("sized_zero", 0))
        self.assertIsNone(decode.lookup(table, 0x400D2001))

    def test_files_that_are_not_a_readable_elf_say_why(self):
        for data, why in ((b"hello world, this is not an elf file at all......................", "not a 32-bit"),
                          (make_elf(wide=True), "not a 32-bit"), (make_elf()[:0x30], "not a 32-bit"),
                          (make_elf()[:-30], "cut short")):
            with self.assertRaises(decode.ElfError, msg=why) as cm:
                decode.symbols(tmp(self, data))
            self.assertIn(why, str(cm.exception))

    def test_a_stripped_elf_has_no_symbol_table_and_says_so(self):
        with self.assertRaises(decode.ElfError) as cm:
            decode.symbols(tmp(self, make_elf(stripped=True)))
        self.assertIn("stripped", str(cm.exception))


class Lines(unittest.TestCase):
    TEXT = "Backtrace: 0x400d1004:0x3ffb0000 0x400d1044:0x3ffb0000 0x400d1ff0:0x3ffb0000\n"

    def runner(self, rc=0, out=None):
        out = out if out is not None else ("0x400d1004: app_main at /src/main.c:42\n0x400d1044: do_crash at ??:?\n")
        self.calls = []

        def run(cmd, bound):
            self.calls.append((cmd, bound))
            return rc, out, 0.1, False
        return run

    def test_without_a_toolchain_the_functions_come_from_the_symbol_table_alone(self):
        out = decode.decode(self.TEXT, tmp(self, make_elf()), chip="esp32", runner=self.runner(), tool_hook=None)
        self.assertIsNone(out["tool"])
        self.assertEqual([(f["function"], f["offset"], f["source"]) for f in out["frames"]],
                         [("app_main", 4, "symtab"), ("do_crash", 4, "symtab"), (None, None, None)])

    def test_with_addr2line_the_file_and_line_are_added_where_it_could_place_them(self):
        run = self.runner()
        out = decode.decode(self.TEXT, tmp(self, make_elf()), chip="esp32", runner=run, tool_hook="/fake/a2l")
        self.assertEqual(len(self.calls), 1, "one run for every address")
        self.assertEqual(self.calls[0][0][:4], ["/fake/a2l", "-pfiaC", "-e", self.calls[0][0][3]])
        self.assertEqual(self.calls[0][0][4:], ["0x400d1004", "0x400d1044", "0x400d1ff0"])
        first, second, third = out["frames"]
        self.assertEqual((first["file"], first["line"], first["source"]), ("/src/main.c", 42, "addr2line"))
        self.assertEqual((second["file"], second["function"], second["source"]), (None, "do_crash", "symtab"),
                         "a ??:? answer is not a place")
        self.assertEqual(third["source"], None)

    def test_a_failing_addr2line_leaves_the_symbol_table_answers_and_does_not_fail_the_decode(self):
        died = "0x400d1004: app_main at /src/main.c:42\n"            # what a tool printed before it died: not trusted
        out = decode.decode(self.TEXT, tmp(self, make_elf()), runner=self.runner(rc=1, out=died), tool_hook="/fake/a2l")
        self.assertEqual([f["source"] for f in out["frames"]], ["symtab", "symtab", None])
        hung = lambda cmd, bound: (-1, died, 10.0, True)  # noqa: E731
        out = decode.decode(self.TEXT, tmp(self, make_elf()), runner=hung, tool_hook="/fake/a2l")
        self.assertEqual([f["source"] for f in out["frames"]], ["symtab", "symtab", None])

    def test_the_toolchain_is_chosen_by_chip(self):
        import shutil
        from unittest import mock
        with mock.patch.object(shutil, "which", side_effect=lambda n: "/bin/" + n):
            self.assertEqual(decode.find_addr2line("esp32s3"), "/bin/xtensa-esp32s3-elf-addr2line")
            self.assertEqual(decode.find_addr2line("esp32c6"), "/bin/riscv32-esp-elf-addr2line")
            self.assertEqual(decode.find_addr2line("esp32"), "/bin/xtensa-esp32-elf-addr2line")
            self.assertIsNone(decode.find_addr2line(None))
            self.assertIsNone(decode.find_addr2line("esp8266"))


class Command(Base):
    TEXT = "Backtrace: 0x400d1004:0x3ffb0000 0x400d1044:0x3ffb0000\n"

    def test_a_bundle_with_its_elf_is_decoded(self):
        fid = self.put(bundle(elf_bytes=make_elf()))
        res = self.call("esp32.decode", {"firmware": fid, "text": self.TEXT})
        self.assertTrue(res.ok, res.message)
        self.assertEqual([(f["address"], f["function"]) for f in res.data["frames"]],
                         [("0x400d1004", "app_main"), ("0x400d1044", "do_crash")])
        self.assertEqual((res.data["chip"], res.data["firmware_id"], res.message), ("esp32", fid, "2 addresses found, 2 named"))
        root = self.host.config["paths"]["firmware"]
        self.assertEqual([n for n in os.listdir(root) if n.startswith(".esp-")], [], "the scratch folder is gone")

    def test_addr2line_is_used_when_the_host_has_one(self):
        fid = self.put(bundle(elf_bytes=make_elf()))
        self.host.hooks["esp_addr2line"] = "/fake/a2l"
        self.host.hooks["esp_runner"] = Tool(out="0x400d1004: app_main at /src/main.c:42\n")
        res = self.call("esp32.decode", {"firmware": fid, "text": self.TEXT})
        self.assertEqual((res.data["frames"][0]["file"], res.data["tool"]), ("/src/main.c", "/fake/a2l"))

    def test_a_package_with_no_elf_an_nrf_package_and_an_unknown_id_are_each_refused(self):
        for data in (bundle(elf=None), merged()):
            res = self.call("esp32.decode", {"firmware": self.put(data), "text": self.TEXT})
            self.assertEqual((res.cls, res.code), ("refused", "bad_package"))
            self.assertIn("no ELF", res.message)
        self.assertEqual(self.call("esp32.decode", {"firmware": "0" * 64, "text": self.TEXT}).code, "firmware_unknown")

    def test_an_elf_that_cannot_be_read_is_refused_with_the_reason_and_leaves_no_scratch(self):
        fid = self.put(bundle(elf_bytes=make_elf(stripped=True)))
        res = self.call("esp32.decode", {"firmware": fid, "text": self.TEXT})
        self.assertEqual((res.cls, res.code), ("refused", "bad_package"))
        self.assertIn("stripped", res.message)
        root = self.host.config["paths"]["firmware"]
        self.assertEqual([n for n in os.listdir(root) if n.startswith(".esp-")], [])

    def test_text_over_the_cap_is_a_usage_error(self):
        fid = self.put(bundle(elf_bytes=make_elf()))
        with self.assertRaises(UsageError):
            self.call("esp32.decode", {"firmware": fid, "text": "x" * (commands.MAX_TEXT + 1)})
        self.assertTrue(self.call("esp32.decode", {"firmware": fid, "text": "x" * commands.MAX_TEXT}).ok)

    def test_text_with_no_address_decodes_to_nothing_and_says_so(self):
        fid = self.put(bundle(elf_bytes=make_elf()))
        res = self.call("esp32.decode", {"firmware": fid, "text": "just a log line"})
        self.assertEqual((res.ok, res.data["frames"], res.message), (True, [], "0 addresses found, 0 named"))


if __name__ == "__main__":
    unittest.main()
