"""Which boards are attached, in which mode, and which node belongs to them.

READ-ONLY. It reads sysfs and stat()s nodes; it never opens one. Identity is the USB serial plus
the ids; the node is found by joining the board's sysfs tty entry to a node by major:minor, never
by a name (docs/specs/devices.md, "identity and node"). Every root is a parameter so the tests can
run against a fake tree.
"""
import os
import stat


def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def inventory(sysfs="/sys"):
    """Every USB device sysfs lists: [{name, path, id, manufacturer, product, serial}]."""
    base = os.path.join(sysfs, "bus", "usb", "devices")
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if ":" in name:                       # an interface, not a device
            continue
        path = os.path.join(base, name)
        vid, pid = _read(os.path.join(path, "idVendor")), _read(os.path.join(path, "idProduct"))
        if not vid or not pid:
            continue
        bus, num = _read(os.path.join(path, "busnum")), _read(os.path.join(path, "devnum"))
        out.append({"name": name, "path": path, "id": "%s:%s" % (vid, pid),
                    "manufacturer": _read(os.path.join(path, "manufacturer")),
                    "product": _read(os.path.join(path, "product")),
                    "serial": _read(os.path.join(path, "serial")),
                    # busnum:devnum changes on EVERY re-enumeration, so it tells one appearance of a
                    # device from the next. None if the host does not expose it (never guessed).
                    "enum": "%s:%s" % (bus, num) if bus and num else None})
    return out


def board_devices(profile, inv):
    """The devices in `inv` whose ids belong to this profile, each tagged with its mode."""
    out = []
    for dev in inv:
        for mode in ("app", "bootloader"):
            if dev["id"] in profile["modes"][mode]["ids"]:
                out.append(dict(dev, mode=mode))
    return out


def state(profile, serial, inv):
    """app | bootloader | two | absent, for the board with this serial. 'absent' means no sysfs
    entry: a board that is attached but has no node is a separate question (see node_for)."""
    mine = [d for d in board_devices(profile, inv) if d["serial"] == serial]
    modes = set(d["mode"] for d in mine)
    if modes == set(["app", "bootloader"]):
        return "two"
    if "app" in modes:
        return "app"
    if "bootloader" in modes:
        return "bootloader"
    return "absent"


def _tty_entries(devpath):
    """[(name, 'major:minor')] for the tty class entries under a USB device's sysfs directory."""
    found = []
    for root, dirs, files in os.walk(devpath):
        depth = root[len(devpath):].count(os.sep)
        if depth >= 4:
            dirs[:] = []
            continue
        if "dev" in files and os.path.basename(root).startswith("tty"):
            majmin = _read(os.path.join(root, "dev"))
            if majmin:
                found.append((os.path.basename(root), majmin))
    return sorted(found)


def node_for(device, devroot="/dev"):
    """(node path or None, reason). The join is by device number: the tty entry's major:minor must
    equal the node's st_rdev, and the node must be a character device."""
    entries = _tty_entries(device["path"])
    if not entries:
        return None, "no tty interface under the device in sysfs"
    reasons = []
    for name, majmin in entries:
        node = os.path.join(devroot, name)
        try:
            st = os.stat(node)
        except OSError:
            reasons.append("sysfs lists %s but there is no such node to open" % name)
            continue
        if not stat.S_ISCHR(st.st_mode):
            reasons.append("%s exists but is not a character device" % node)
            continue
        if "%d:%d" % (os.major(st.st_rdev), os.minor(st.st_rdev)) != majmin:
            reasons.append("%s has a different device number from the sysfs entry" % node)
            continue
        return node, ""
    return None, "; ".join(reasons)
