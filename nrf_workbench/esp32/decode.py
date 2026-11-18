"""esp32.decode: turn the addresses in a crash dump into functions (docs/specs/esp32.md, "Crash decoding").

Two sources, tried in this order for each address:

  1. the ELF's own symbol table, read here in pure Python (ELF32, little-endian, which is every ESP32 family): the function
     the address is in and how far into it. It needs no toolchain, so it works on a bench Pi that has none.
  2. `addr2line` from the chip's toolchain, WHEN it is on the PATH: it adds the file and the line from the debug info.

READ-ONLY: it opens the stored ELF and nothing else. Addresses are used exactly as the dump printed them.
"""
import bisect
import re
import shutil
import struct

# The dump formats of ESP-IDF and the Arduino core. `Backtrace:` carries PC:SP pairs; the PC is the first of each. A panic
# header names the faulting PC (Xtensa) or MEPC and RA (RISC-V).
_BACKTRACE = re.compile(r"Backtrace:(.*)")
_PAIR = re.compile(r"0x([0-9a-fA-F]{8}):0x[0-9a-fA-F]{8}")
_REGISTER = re.compile(r"\b(?:PC|MEPC|RA)\s*:\s*0x([0-9a-fA-F]{8})\b")
MAX_FRAMES = 128          # ESP-IDF's backtrace is at most 100 deep; 128 is the next power of two, so nothing real is cut
SHT_SYMTAB, STT_FUNC = 2, 2
# A give-up ceiling for one addr2line run, not a wait: it returns when done. The symbol table of a real ESP32 ELF (2.8 MB,
# 1064 functions) parsed in 2 ms here; addr2line reads DWARF line tables, which is slower, and a bound of the order of seconds
# only has to catch a hung process. [unknown]: nobody has timed the xtensa toolchain's addr2line on this bench.
A2L_BOUND_S = 10.0
TOOLCHAINS = {"esp32": "xtensa-esp32-elf-addr2line", "esp32s2": "xtensa-esp32s2-elf-addr2line",
              "esp32s3": "xtensa-esp32s3-elf-addr2line"}
RISCV = "riscv32-esp-elf-addr2line"           # the C2, C3, C5, C6, C61 and H2 are RISC-V
_A2L = re.compile(r"^0x([0-9a-fA-F]+): (.+?) at (.+?):(\d+|\?)")


class ElfError(Exception):
    """The file is not an ELF this reads (32-bit little-endian with a symbol table)."""


def addresses(text):
    """The addresses a dump holds, in the order printed and once each: backtrace PCs, then the panic registers."""
    found, seen = [], set()
    for line in text.splitlines():
        m = _BACKTRACE.search(line)
        values = _PAIR.findall(m.group(1)) if m else []
        values += _REGISTER.findall(line)
        for v in values:
            addr = int(v, 16)
            if addr not in seen:
                seen.add(addr)
                found.append(addr)
    return found


def symbols(path):
    """[(start, size, name)] for every function in the ELF's symbol table, by address."""
    with open(path, "rb") as fh:
        head = fh.read(0x34)
        if len(head) < 0x34 or head[:4] != b"\x7fELF" or head[4] != 1 or head[5] != 1:
            raise ElfError("not a 32-bit little-endian ELF file")
        shoff, = struct.unpack_from("<I", head, 0x20)
        shentsize, shnum = struct.unpack_from("<HH", head, 0x2E)
        if shentsize != 40 or not shoff or not shnum:
            raise ElfError("the ELF has no section table")
        fh.seek(shoff)
        raw = fh.read(shentsize * shnum)
        if len(raw) < shentsize * shnum:
            raise ElfError("the ELF's section table is cut short")
        sections = [struct.unpack_from("<10I", raw, i * shentsize) for i in range(shnum)]
        tab = next((s for s in sections if s[1] == SHT_SYMTAB), None)
        if tab is None or tab[6] >= shnum:
            raise ElfError("the ELF has no symbol table: it was built stripped")
        strtab = sections[tab[6]]
        fh.seek(tab[4])
        symraw = fh.read(tab[5])
        fh.seek(strtab[4])
        names = fh.read(strtab[5])
    out = []
    for off in range(0, len(symraw) - 15, 16):
        name_at, value, size, info = struct.unpack_from("<IIIB", symraw, off)
        if info & 0xF != STT_FUNC or not value:
            continue
        end = names.find(b"\0", name_at)
        out.append((value, size, names[name_at:end if end >= 0 else None].decode("utf-8", "replace")))
    out.sort()
    return out


def lookup(table, addr):
    """(name, offset) of the function `addr` falls in, or None. A symbol with no size matches only its own address."""
    i = bisect.bisect_right([s[0] for s in table], addr) - 1
    if i < 0:
        return None
    start, size, name = table[i]
    if addr < start + (size or 1):
        return name, addr - start
    return None


def find_addr2line(chip, hook=None):
    """The toolchain's addr2line for this chip, or None. `chip` is the bundle's, such as esp32s3 or esp32c3."""
    if hook:
        return hook
    chip = (chip or "").lower().replace("-", "")
    name = TOOLCHAINS.get(chip) or (RISCV if chip.startswith(("esp32c", "esp32h")) else None)
    return shutil.which(name) if name else None


def lines_for(tool, elf, addrs, runner):
    """{address: (file, line)} from addr2line, for the addresses it could place. One run for all of them."""
    rc, out, _secs, timed_out = runner([tool, "-pfiaC", "-e", elf] + ["0x%08x" % a for a in addrs], A2L_BOUND_S)
    found = {}
    if rc != 0 or timed_out:
        return found
    for line in out.splitlines():
        m = _A2L.match(line.strip())
        if m and m.group(3) != "??" and m.group(4) != "?":
            found[int(m.group(1), 16)] = (m.group(3), int(m.group(4)))
    return found


def decode(text, elf, chip=None, runner=None, tool_hook=None):
    """{addresses, frames: [{address, function, offset, file, line, source}], truncated, tool}. Raises ElfError."""
    found = addresses(text)
    truncated = len(found) > MAX_FRAMES
    found = found[:MAX_FRAMES]
    table = symbols(elf)
    tool = find_addr2line(chip, tool_hook) if runner else None
    lines = lines_for(tool, elf, found, runner) if tool and found else {}
    frames = []
    for addr in found:
        hit = lookup(table, addr)
        file, line = lines.get(addr, (None, None))
        frames.append({"address": "0x%08x" % addr, "function": hit[0] if hit else None, "offset": hit[1] if hit else None,
                       "file": file, "line": line, "source": "addr2line" if file else ("symtab" if hit else None)})
    return {"addresses": len(found), "frames": frames, "truncated": truncated, "tool": tool}
