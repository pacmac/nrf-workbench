"""The individual refusals. Each returns None if it passes, or a refused Result.

A refusal names what it found (a pid, an id, a path) so it can be acted on without reading this
file, and it means NOTHING was attempted. The codes are the closed list in docs/specs/core.md.
"""
import os
import stat
import zipfile

from ..core.result import Result

RETURN_METHODS = ("flash", "reset-line", "swd", "power-cycle")


def holders(node, proc="/proc", self_pid=None):
    """[(pid, cmdline)] of processes holding `node` open, matched by DEVICE NUMBER, not by path, so
    a process that reached the same device through another alias is not missed."""
    self_pid = os.getpid() if self_pid is None else self_pid
    try:
        st = os.stat(node)
    except OSError:
        return []
    if not stat.S_ISCHR(st.st_mode):
        return []
    found = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return []
    for pid in pids:
        if int(pid) == self_pid:
            continue
        fddir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fddir)
        except OSError:
            continue
        for fd in fds:
            try:
                s = os.stat(os.path.join(fddir, fd))          # follows the link to the device
            except OSError:
                continue
            if stat.S_ISCHR(s.st_mode) and s.st_rdev == st.st_rdev:
                try:
                    with open(os.path.join(proc, pid, "cmdline"), "rb") as fh:
                        cmd = fh.read().decode("utf-8", "replace").replace("\0", " ").strip()
                except OSError:
                    cmd = "?"
                found.append((int(pid), cmd or "?"))
                break
    return found


def port_held(node, proc="/proc"):
    held = holders(node, proc)
    if held:
        return Result.refused("port_held", "the port is held by " + "; ".join("pid %d (%s)" % h for h in held),
                              {"holders": [{"pid": p, "cmdline": c} for p, c in held]})
    return None


def serial_matches(named_serial, device):
    """The caller names the serial; the attached board must carry that one."""
    have = device.get("serial")
    if not named_serial:
        return Result.refused("serial_unverifiable", "no serial was named, so nothing can be compared",
                              {"attached_serial": have})
    if not have:
        return Result.refused("serial_unverifiable",
                              "the caller named a serial but this device exposes none to compare",
                              {"named": named_serial})
    if have != named_serial:
        return Result.refused("serial_mismatch", "the attached board's serial is not the one named",
                              {"named": named_serial, "attached": have})
    return None


def not_protected(units, serial):
    """`units` is the per-serial exceptions mapping (config/units.yaml). A board not named in it is
    flashable by default: this is not a roster."""
    if ((units or {}).get(serial) or {}).get("protected"):
        return Result.refused("protected", "this unit is marked protected (for example mid-measurement)",
                              {"serial": serial})
    return None


def return_path_declared(unit):
    """The unit must declare how it comes back from the bootloader BEFORE it is sent there: with
    only USB there is no software way back except a flash, and a board stranded in the bootloader
    is the 2026-07-27 incident (docs/specs/devices.md, 'the way back')."""
    rp = (unit or {}).get("return_path")
    if not isinstance(rp, dict) or rp.get("method") not in RETURN_METHODS:
        return Result.refused("no_return_path",
                              "this unit declares no way back from the bootloader; refusing to send it there",
                              {"allowed_methods": list(RETURN_METHODS)})
    if rp["method"] == "flash":
        pkg = rp.get("package")
        if not pkg or not os.path.isfile(pkg):
            return Result.refused("no_return_path", "the declared return package does not exist",
                                  {"package": pkg})
        try:
            with zipfile.ZipFile(pkg) as z:
                if "manifest.json" not in z.namelist():
                    raise KeyError("manifest.json")
        except (zipfile.BadZipFile, KeyError, OSError):
            return Result.refused("no_return_path",
                                  "the declared return package is not a DFU package (a zip with manifest.json)",
                                  {"package": pkg})
    return None
