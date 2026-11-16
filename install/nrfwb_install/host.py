"""Detect the host. Nothing is assumed: every fact is read, and an unknown stays unknown.

Verified classes are listed in docs/specs/install.md; anything else is reported as untested, never
refused on a guess and never reported as fine on a guess.
"""
import glob
import grp
import importlib.util
import os
import platform
import shutil
import sys

from . import MIN_PYTHON


def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _os_release():
    out = {}
    for line in (_read("/etc/os-release") or "").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def _libc():
    if glob.glob("/lib/ld-musl-*") or glob.glob("/usr/lib/ld-musl-*"):
        return "musl"
    name, _version = platform.libc_ver()
    return name or "unknown"


def _serial_group():
    """The group that owns serial nodes, READ from an existing node, never assumed to be `dialout`."""
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/serial/by-id/*"):
        for node in sorted(glob.glob(pattern)):
            try:
                return grp.getgrgid(os.stat(node).st_gid).gr_name
            except (OSError, KeyError):
                continue
    return None


def detect():
    osr = _os_release()
    ids = set([osr.get("ID", "")] + osr.get("ID_LIKE", "").split())
    ids.discard("")
    pi = (_read("/proc/device-tree/model") or "").replace("\0", "").strip() or None
    return {
        "os": osr.get("PRETTY_NAME", "unknown"),
        "os_ids": sorted(ids),
        "arch": platform.machine(),
        "libc": _libc(),
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
        "python_ok": sys.version_info[:2] >= MIN_PYTHON,
        "has_venv": importlib.util.find_spec("ensurepip") is not None,
        "root": os.geteuid() == 0,
        "sudo": shutil.which("sudo"),
        "apt": shutil.which("apt-get"),
        "systemd": bool(os.path.isdir("/run/systemd/system") and shutil.which("systemctl")),
        "pi_model": pi if pi and "Raspberry Pi" in pi else None,
        "terminal": sys.stdin.isatty(),
        "home": os.path.expanduser("~"),
        "serial_group": _serial_group(),
    }


def untested_reasons(h):
    """Why this host is NOT in the tested set. Empty means it is a class that was actually run."""
    why = []
    if "debian" not in h["os_ids"] and "ubuntu" not in h["os_ids"]:
        why.append("distribution family '%s' (tested: Debian 13)" % ",".join(h["os_ids"] or ["unknown"]))
    if h["arch"] not in ("x86_64", "aarch64"):
        why.append("architecture %s (tested: x86_64, aarch64)" % h["arch"])
    if h["libc"] != "glibc":
        why.append("C library %s (tested: glibc)" % h["libc"])
    if not h["systemd"]:
        why.append("no systemd (the service unit is not offered anyway in this release)")
    return why
