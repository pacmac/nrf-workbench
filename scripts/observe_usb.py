#!/usr/bin/env python3
"""observe_usb.py - passively log USB and node events while a HUMAN changes the board's mode.

Settles: is the serial the same in the app and bootloader modes, which USB ids belong to which
mode (including the role of 239a:802a), and whether kernel/udev events reach this host at all.

  * NEVER opens a serial device, never touches DTR or RTS, never requests a mode change. It only
    watches. You cause the transition: double-tap reset, replug, or a flash by the sanctioned
    route, while this runs.
  * Waits on events with select(); there is no sleep and no poll. The ceiling is the REQUIRED
    --max-s (no default): the capture must hold the transitions you cause. For a flash use 60:
    a healthy RAK4631 flash is 25-29 s (brief) plus two handovers, about 2 x 29 = 58.

Event sources, all watched together where they exist:
  udev      `udevadm monitor`, only if /run/udev exists. Carries the serial and the ids.
  uevent    the kernel's own netlink uevent socket. Works WITHOUT udev, but a container often
            receives nothing; if no event arrives the summary says so rather than guessing.
  inotify   the node directories (/dev, /dev/serial/by-id, /dev/lxc-serial). Node names only.

Without events there is still a way to settle serial stability: run scripts/probe_host.py once with
the board in each mode (the "usb" section lists id, product and serial from sysfs) and compare.

stdlib only, conservative syntax (older Python).

Usage:  python3 scripts/observe_usb.py --max-s 60 | tee probes/$(hostname)-observe.txt
"""
import argparse
import ctypes
import ctypes.util
import os
import select
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time

# inotify(7) bits: the kernel's, not chosen here.
IN_ATTRIB, IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x4, 0x40, 0x80, 0x100, 0x200
IN_MASK = IN_ATTRIB | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
NETLINK_KOBJECT_UEVENT = 15
DEFAULT_DIRS = ["/dev", "/dev/serial/by-id", "/dev/lxc-serial"]
WANT = ["ACTION", "SUBSYSTEM", "DEVTYPE", "DEVNAME", "PRODUCT", "ID_VENDOR_ID", "ID_MODEL_ID",
        "ID_SERIAL_SHORT", "ID_MODEL", "ID_VENDOR", "ID_PATH"]


class Capture(object):
    def __init__(self):
        self.t0 = time.monotonic()
        self.events = []        # usb/tty events (udev or uevent)
        self.node_events = []   # inotify

    def ts(self):
        return "+%7.3f" % (time.monotonic() - self.t0)


def read(path):
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def vid_pid(props):
    """'239a:0029' from udev props, else from the kernel PRODUCT field (hex, leading zeros dropped)."""
    if props.get("ID_VENDOR_ID") and props.get("ID_MODEL_ID"):
        return "%s:%s" % (props["ID_VENDOR_ID"], props["ID_MODEL_ID"])
    parts = props.get("PRODUCT", "").split("/")
    if len(parts) >= 2:
        return "%s:%s" % (parts[0].zfill(4), parts[1].zfill(4))
    return "?"


def show(cap, source, rec):
    cap.events.append(rec)
    print("%s %-6s %-6s %-9s %-11s %-9s serial=%s model=%r node=%s" % (
        cap.ts(), source, rec.get("ACTION"), rec.get("SUBSYSTEM"), rec.get("DEVTYPE") or "",
        vid_pid(rec), rec.get("ID_SERIAL_SHORT") or "-", rec.get("ID_MODEL") or "-",
        rec.get("DEVNAME") or "-"), flush=True)


# ---- source: udev -----------------------------------------------------------------------------
class Udev(object):
    name = "udev"

    def __init__(self, cap):
        self.cap = cap
        self.buf = b""
        self.proc = subprocess.Popen(
            ["udevadm", "monitor", "--udev", "--property", "--subsystem-match=usb",
             "--subsystem-match=tty"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self.fd = self.proc.stdout.fileno()

    def ready(self):
        chunk = os.read(self.fd, 65536)
        if not chunk:
            return False
        self.buf += chunk
        while b"\n\n" in self.buf:
            block, self.buf = self.buf.split(b"\n\n", 1)
            props = {}
            for line in block.decode("utf-8", "replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v
            if props.get("ACTION"):
                show(self.cap, self.name, {k: props.get(k) for k in WANT if props.get(k)})
        return True

    def close(self):
        self.proc.terminate()
        self.proc.wait()


# ---- source: kernel uevent over netlink (no udev needed) --------------------------------------
class Uevent(object):
    name = "uevent"

    def __init__(self, cap):
        self.cap = cap
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
        self.sock.bind((0, 1))          # group 1: the kernel's own messages
        self.fd = self.sock.fileno()

    def ready(self):
        data = self.sock.recv(65536)
        parts = data.split(b"\0")
        props = {}
        for p in parts[1:]:
            if b"=" in p:
                k, v = p.decode("utf-8", "replace").split("=", 1)
                props[k] = v
        if props.get("SUBSYSTEM") not in ("usb", "tty") or not props.get("ACTION"):
            return True
        rec = {k: props.get(k) for k in WANT if props.get(k)}
        # The kernel message has no ID_* fields; read the sysfs attributes at event time.
        base = "/sys" + props.get("DEVPATH", "")
        if props.get("DEVTYPE") == "usb_device":
            rec["ID_SERIAL_SHORT"] = read(base + "/serial") or ""
            rec["ID_MODEL"] = read(base + "/product") or ""
            rec = dict((k, v) for k, v in rec.items() if v)
        show(self.cap, self.name, rec)
        return True

    def close(self):
        self.sock.close()


# ---- source: inotify on node directories ------------------------------------------------------
class Inotify(object):
    name = "inotify"

    def __init__(self, cap, dirs):
        self.cap = cap
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = libc.inotify_init()
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init failed")
        self.wd_dir = {}
        for d in dirs:
            wd = libc.inotify_add_watch(self.fd, d.encode(), IN_MASK)
            if wd >= 0:
                self.wd_dir[wd] = d

    def ready(self):
        data = os.read(self.fd, 65536)
        off = 0
        while off < len(data):
            wd, mask, _cookie, ln = struct.unpack_from("iIII", data, off)
            name = data[off + 16: off + 16 + ln].split(b"\0", 1)[0].decode("utf-8", "replace")
            off += 16 + ln
            base = self.wd_dir.get(wd, "?")
            if base == "/dev" and not (name.startswith("ttyACM") or name.startswith("ttyUSB")
                                       or name in ("serial", "lxc-serial")):
                continue                 # /dev has many unrelated nodes
            kind = ("create" if mask & IN_CREATE else "delete" if mask & IN_DELETE
                    else "move-to" if mask & IN_MOVED_TO else "move-from" if mask & IN_MOVED_FROM
                    else "attrib")
            path = os.path.join(base, name)
            extra = ""
            if kind in ("create", "move-to", "attrib"):
                try:
                    st = os.stat(path)       # stat only: never opens the device
                    extra = " -> %s major:minor=%d:%d%s" % (
                        os.path.realpath(path), os.major(st.st_rdev), os.minor(st.st_rdev),
                        " (char)" if stat.S_ISCHR(st.st_mode) else " (NOT char)")
                except OSError:
                    extra = " (gone before stat)"
            self.cap.node_events.append({"kind": kind, "path": path})
            print("%s inotify %-9s %s%s" % (self.cap.ts(), kind, path, extra), flush=True)
        return True

    def close(self):
        os.close(self.fd)


def summarise(cap, sources):
    print("\n== summary ==")
    print("sources active: %s" % (", ".join(s.name for s in sources) or "none"))
    by_serial = {}
    for e in cap.events:
        if e.get("SUBSYSTEM") == "usb" and e.get("DEVTYPE") == "usb_device" and e.get("ACTION") == "add":
            s = e.get("ID_SERIAL_SHORT") or "(no serial)"
            by_serial.setdefault(s, {})[vid_pid(e)] = e.get("ID_MODEL") or "?"
    if not cap.events and not cap.node_events:
        print("NO EVENT of any kind arrived. Either nothing changed, or this host does not deliver "
              "uevents (a container often does not). This is not evidence about the board. Settle "
              "serial stability instead by running scripts/probe_host.py once per mode and comparing.")
        return
    if not by_serial:
        print("%d USB/tty events and %d node events, but no USB device was ADDED, so no id/serial "
              "pair to compare." % (len(cap.events), len(cap.node_events)))
        return
    for s, ids in sorted(by_serial.items()):
        print("serial %s seen as: %s" % (s, "; ".join("%s (%s)" % (i, m) for i, m in sorted(ids.items()))))
    many = [s for s, ids in by_serial.items() if len(ids) >= 2]
    if many:
        print("VERDICT: serial %s is IDENTICAL across %d ids -> the serial survives the mode change "
              "(for this board only)." % (many[0], len(by_serial[many[0]])))
    elif len(by_serial) >= 2:
        print("VERDICT: different serials appeared, each with one id. Either the serial CHANGES between "
              "modes or these are different boards. Repeat with ONE board through ONE mode change.")
    else:
        print("VERDICT: only one id was seen. The mode did not change during the capture.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-s", type=float, required=True,
                    help="REQUIRED capture ceiling in seconds. 60 for a flash (derivation in the file header)")
    ap.add_argument("--dir", action="append", default=None, help="directory to watch for nodes")
    a = ap.parse_args()

    cap = Capture()
    sources, notes = [], []
    if os.path.isdir("/run/udev") and shutil.which("udevadm"):
        sources.append(Udev(cap))
    else:
        notes.append("udev: unavailable (no /run/udev or no udevadm)")
        try:
            sources.append(Uevent(cap))
        except OSError as exc:
            notes.append("uevent: cannot open the kernel netlink socket (%s)" % exc)
    dirs = [d for d in (a.dir or DEFAULT_DIRS) if os.path.isdir(d)]
    try:
        sources.append(Inotify(cap, dirs))
    except OSError as exc:
        notes.append("inotify: %s" % exc)

    print("This tool never opens a device and never changes the board's mode. You do that.")
    for n in notes:
        print(n)
    print("watching with: %s; node dirs: %s" % (", ".join(s.name for s in sources) or "NOTHING",
                                                  ", ".join(dirs) or "none"))
    print("Capturing for up to %.0f s. Change the board's mode now. Ctrl-C ends early." % a.max_s, flush=True)

    fds = dict((s.fd, s) for s in sources)
    deadline = time.monotonic() + a.max_s
    try:
        while fds:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(list(fds), [], [], remaining)
            if not ready:
                break
            for fd in ready:
                if not fds[fd].ready():
                    del fds[fd]
    except KeyboardInterrupt:
        pass
    finally:
        for s in sources:
            s.close()
    summarise(cap, sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
