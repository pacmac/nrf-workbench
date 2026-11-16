#!/usr/bin/env python3
"""probe_host.py - one passive snapshot of this host, printed as JSON.

Settles the unknowns in docs/specs/probes.md that need another host or hardware. Read-only:

  * NEVER opens a serial device, at any baud; never touches DTR or RTS. Nodes under /dev are only
    stat()ed. (Opening a port at 1200 baud IS bootloader entry.)
  * NEVER writes: the lock directory is checked with os.access, not by creating a file.
  * Runs subprocesses only for `--version`-style calls, each with a bound derived below.

stdlib only and conservative syntax, so it runs on an older Python than the product needs; the
interpreter version it reports is itself one of the answers.

Usage:  python3 scripts/probe_host.py | tee probes/$(hostname).json
"""
import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import time

PROBE_VERSION = 3   # 2: gpio records each chip's label and line count; 3: uart section

# name -> argv after the program. A tool NOT listed here is only looked for, never run
# (JLinkExe is interactive; btmgmt and hciconfig are not version-queries).
RUN = {
    "pio": ["--version"],
    "platformio": ["--version"],
    "adafruit-nrfutil": ["version"],
    "nrfutil": ["--version"],
    "nrfjprog": ["--version"],
    "openocd": ["--version"],
    "pyocd": ["--version"],
    "mcumgr": ["version"],
    "smpmgr": ["--version"],
    "bluetoothctl": ["--version"],
    "udevadm": ["--version"],
    "gpioinfo": ["--version"],
}
ONLY_LOOK = ["JLinkExe", "btmgmt", "hciconfig", "sigrok-cli", "systemd-detect-virt"]

LIBS = ["mcp", "starlette", "uvicorn", "websockets", "wsproto", "httpx", "pydantic", "anyio",
        "pyudev", "bleak", "pyocd", "gpiod", "pyserial", "PyYAML"]

NODE_GLOBS = ["/dev/ttyACM*", "/dev/ttyUSB*", "/dev/serial/by-id/*", "/dev/serial/by-path/*",
              "/dev/lxc-serial/*"]
UDEV_KEYS = ["ID_VENDOR_ID", "ID_MODEL_ID", "ID_SERIAL_SHORT", "ID_PATH", "ID_MODEL", "ID_VENDOR"]

# A `--version` call's bound is SELF-SCALING, not a constant. Measured on an x86_64 host:
# `python3 -c pass` 0.011 s, `pio --version` 0.082 s, a ratio of about 7.5. A 3x margin on that
# ratio is 22, so a call is allowed 22 x this host's own interpreter start. Slower host, slower
# bound. See docs/specs/probes.md.
RATIO_MARGIN = 22


def read(path):
    try:
        with open(path, "r") as fh:
            return fh.read().strip().replace("\0", "")
    except (OSError, UnicodeDecodeError):
        return None


def interpreter_start_s():
    """Best of three: the first run is cold-cache, the others are what a warm call costs."""
    best = None
    for _ in range(3):
        t = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        d = time.perf_counter() - t
        best = d if best is None or d < best else best
    return best


def run_bounded(argv, bound):
    """(ok, text). Never raises. A timeout is reported as such, never as 'absent'."""
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=bound)
        return True, (r.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return False, "no answer within %.2f s (derived bound); re-run, a first call can be cold" % bound
    except OSError as exc:
        return False, "could not run: %s" % exc


def section_platform(bound):
    info = {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "python_implementation": platform.python_implementation(),
        "os_pretty_name": None,
        "virtualisation": None,
        "container": None,
    }
    for line in (read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            info["os_pretty_name"] = line.split("=", 1)[1].strip('"')
    if shutil.which("systemd-detect-virt"):
        info["virtualisation"] = run_bounded(["systemd-detect-virt"], bound)[1]
        info["container"] = run_bounded(["systemd-detect-virt", "--container"], bound)[1]
    return info


def section_pi():
    model = read("/proc/device-tree/model")
    return {
        "model": model,
        "is_raspberry_pi": bool(model and "Raspberry Pi" in model),
        "dev_mem_exists": os.path.exists("/dev/mem"),
        "dev_mem_rw_for_this_user": os.access("/dev/mem", os.R_OK | os.W_OK),
    }


def section_fs():
    d = "/run/lock"
    return {"lock_dir": d, "exists": os.path.isdir(d), "writable": os.access(d, os.W_OK)}


def section_udev(bound):
    have_db = os.path.isdir("/run/udev")
    try:
        import pyudev  # noqa: F401
        pyudev_ok = True
    except Exception:
        pyudev_ok = False
    return {
        "run_udev_dir_exists": have_db,
        "udevadm": shutil.which("udevadm"),
        "pyudev_importable": pyudev_ok,
        "identity_provider_hint": ("udev" if have_db and shutil.which("udevadm")
                                   else "node-map (no udev database on this host)"),
    }


def section_usb():
    root = "/sys/bus/usb/devices"
    if not os.path.isdir(root):
        return {"sysfs_usb_tree": False, "devices": []}
    out = []
    for name in sorted(os.listdir(root)):
        if ":" in name:                       # an interface, not a device
            continue
        base = os.path.join(root, name)
        vid, pid = read(base + "/idVendor"), read(base + "/idProduct")
        if not vid:
            continue
        out.append({
            "sysfs": name,
            "id": "%s:%s" % (vid, pid),
            "manufacturer": read(base + "/manufacturer"),
            "product": read(base + "/product"),
            "serial": read(base + "/serial"),
            "speed_mbps": read(base + "/speed"),
        })
    return {"sysfs_usb_tree": True, "devices": out}


def section_nodes(bound, have_udev):
    nodes = []
    for pattern in NODE_GLOBS:
        for path in sorted(glob.glob(pattern)):
            entry = {"path": path, "is_symlink": os.path.islink(path),
                     "realpath": os.path.realpath(path)}
            try:
                st = os.stat(path)                 # stat only: this never opens the device
                import stat as statmod
                entry["is_char_device"] = statmod.S_ISCHR(st.st_mode)
                entry["major_minor"] = "%d:%d" % (os.major(st.st_rdev), os.minor(st.st_rdev))
            except OSError as exc:
                entry["stat_error"] = str(exc)
            if have_udev and "major_minor" in entry:
                ok, text = run_bounded(["udevadm", "info", "--query=property", "--name=" + path], bound)
                if ok:
                    props = dict(l.split("=", 1) for l in text.splitlines() if "=" in l)
                    entry["udev"] = {k: props.get(k) for k in UDEV_KEYS if props.get(k)}
            nodes.append(entry)
    return {"patterns": NODE_GLOBS, "found": nodes,
            "note": "a node with no udev serial means the serial interlock cannot run for it"}


def section_gpio():
    """A gpiochip is NOT evidence of a Pi header: a bare-metal x86 host (verified) has
    /dev/gpiochip0 for a motherboard controller. So record each chip's label and line count, read
    best-effort from sysfs (the path varies by kernel), and let the reader decide."""
    chips = sorted(glob.glob("/dev/gpiochip*"))
    detail = {}
    for c in chips:
        name = os.path.basename(c)
        info = {"readable": os.access(c, os.R_OK), "label": None, "ngpio": None}
        for base in ("/sys/bus/gpio/devices/%s" % name, "/sys/class/gpio/%s" % name):
            info["label"] = info["label"] or read(base + "/label")
            info["ngpio"] = info["ngpio"] or read(base + "/ngpio")
        detail[c] = info
    return {"gpiochips": chips, "detail": detail}


def section_uart(bound):
    """The Pi header UART as a debug link (docs/specs/console.md). Read-only: aliases, nodes that are
    stat()ed (never opened), the boot config lines that enable a UART, kernel console= tokens, and
    whether a login getty holds a serial line (it would fight for the port)."""
    import stat as statmod
    nodes = []
    for pattern in ("/dev/serial0", "/dev/serial1", "/dev/ttyAMA*"):
        for path in sorted(glob.glob(pattern)):
            entry = {"path": path, "realpath": os.path.realpath(path), "is_symlink": os.path.islink(path)}
            try:
                st = os.stat(path)                      # stat only: never opens the device
                entry["is_char_device"] = statmod.S_ISCHR(st.st_mode)
                entry["major_minor"] = "%d:%d" % (os.major(st.st_rdev), os.minor(st.st_rdev))
            except OSError as exc:
                entry["stat_error"] = str(exc)
            nodes.append(entry)
    config = {}
    for path in ("/boot/firmware/config.txt", "/boot/config.txt"):
        text = read(path)
        if text is not None:
            config[path] = [l.strip() for l in text.splitlines()
                            if not l.lstrip().startswith("#")
                            and any(k in l.lower() for k in ("uart", "serial", "disable-bt", "miniuart"))]
    cmdline = (read("/proc/cmdline") or "").split()
    getty = None
    if shutil.which("systemctl"):
        ok, text = run_bounded(["systemctl", "list-units", "--no-legend", "--plain", "serial-getty@*"], bound)
        getty = text.splitlines() if ok else text
    return {
        "device_tree_serial0": read("/proc/device-tree/aliases/serial0"),
        "device_tree_serial1": read("/proc/device-tree/aliases/serial1"),
        "nodes": nodes,
        "boot_config_uart_lines": config,
        "kernel_console_tokens": [t for t in cmdline if t.startswith("console=")],
        "serial_getty_units": getty,
        "note": "a getty bound to the UART, or a console= token naming it, means the port is not free",
    }


def section_bluetooth():
    ctl = sorted(os.listdir("/sys/class/bluetooth")) if os.path.isdir("/sys/class/bluetooth") else []
    return {"controllers": ctl,
            "note": "BLE 5 features (coded PHY, extended advertising) are NOT probed here"}


def section_libs():
    try:
        from importlib import metadata
    except ImportError:
        return {"error": "importlib.metadata unavailable (Python older than 3.8)"}
    out = {}
    for name in LIBS:
        try:
            out[name] = metadata.version(name)
        except Exception:
            out[name] = None
    return out


def section_tools(bound):
    out = {}
    for name, args in RUN.items():
        path = shutil.which(name)
        entry = {"path": path}
        if path:
            ok, text = run_bounded([path] + args, bound)
            entry["answered"] = ok
            entry["version_output"] = text.splitlines()[0] if text else ""
        out[name] = entry
    for name in ONLY_LOOK:
        out[name] = {"path": shutil.which(name), "run": False}
    return out


def main():
    t0 = interpreter_start_s()
    bound = RATIO_MARGIN * t0
    report = {
        "meta": {"probe_version": PROBE_VERSION,
                 "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "interpreter_start_s": round(t0, 4),
                 "version_call_bound_s": round(bound, 3)},
        "platform": section_platform(bound),
        "pi": section_pi(),
        "fs": section_fs(),
    }
    udev = section_udev(bound)
    report["udev"] = udev
    report["usb"] = section_usb()
    report["nodes"] = section_nodes(bound, udev["run_udev_dir_exists"] and bool(udev["udevadm"]))
    report["gpio"] = section_gpio()
    report["uart"] = section_uart(bound)
    report["bluetooth"] = section_bluetooth()
    report["python_libs"] = section_libs()
    report["tools"] = section_tools(bound)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
