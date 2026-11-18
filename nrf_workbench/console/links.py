"""Declared debug links (docs/specs/console.md, "the debug link" and "slice 1").

A link is a named connection between a host-side adapter and one device slot, declared in `links.yaml`. It is host
configuration, a map of adapters to slots. It is validated when loaded: **an invalid link is listed with its
problems and is never opened.** The adapter is found by USB ids and serial through sysfs and joined to its node by
device number (the same `devices.identity` code the boards use), never by a `/dev` name.

Reading is safe on any valid link; sending needs a wire to the device (`host_tx_to_dut`) and a measured voltage.
"""
import os
import re
import stat

import yaml

from ..devices import identity

# The Pi's own UARTs a link may use, and the BCM pins each one is on, TX then RX (docs/specs/pi-uart.md). Only the PL011 on header
# pins 8 and 10 is known; any other port is listed with the reason and never opened.
PI_PORT = re.compile(r"^ttyAMA[0-9]$")
PI_UART_PINS = {"ttyAMA0": (14, 15)}
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
IDS = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")
# The standard rates the kernel names a constant for. 1200 is deliberately absent: a 1200-baud open IS the bootloader touch.
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
EOLS = ("\n", "\r", "\r\n")
BACKENDS = ("usb-adapter", "pi-uart")
KNOWN = frozenset(["name", "slot", "device", "backend", "adapter", "port", "baud", "dut_tx_to_host", "host_tx_to_dut", "voltage",
                   "dtr_rts_reach_dut", "reply_marker", "eol", "own_bridge"])


class Problem(object):
    """One reason a link cannot be used. `code` is a Result code where one exists, else a short word."""
    __slots__ = ("code", "message")

    def __init__(self, code, message):
        self.code, self.message = code, message

    def to_dict(self):
        return {"code": self.code, "message": self.message}


class Link(object):
    def __init__(self, raw, problems):
        raw = raw if isinstance(raw, dict) else {}
        self.name = raw.get("name") if isinstance(raw.get("name"), str) else None
        self.slot = raw.get("slot")
        self.device = raw.get("device") if isinstance(raw.get("device"), str) else None       # the nRF this is wired to (wiring.md)
        self.backend = raw.get("backend")
        adapter = raw.get("adapter") if isinstance(raw.get("adapter"), dict) else {}
        self.ids = [i for i in (adapter.get("ids") or []) if isinstance(i, str)]
        self.serial = adapter.get("serial") if isinstance(adapter.get("serial"), str) else None
        # An adapter with no unique serial is found by where it is plugged (the sysfs port name); only an ESP32 target's own
        # bridge is declared that way (docs/specs/esp32.md, "Targets"), because a bridge chip often has no unique serial.
        self.adapter_slot = adapter.get("slot") if isinstance(adapter.get("slot"), str) else None
        # True for the USB bridge that is part of an ESP32 dev board: DTR/RTS reach its reset pins BY DESIGN, so opening the
        # port is the one case where `dtr_rts_reach_dut` is allowed. This code still never sets either line.
        self.own_bridge = raw.get("own_bridge") is True
        self.port = raw.get("port") if isinstance(raw.get("port"), str) else None          # the Pi's own UART (backend pi-uart), by name
        self.baud = raw.get("baud")
        self.dut_tx_to_host = raw.get("dut_tx_to_host")
        self.host_tx_to_dut = raw.get("host_tx_to_dut")
        voltage = raw.get("voltage") if isinstance(raw.get("voltage"), dict) else {}
        self.voltage = {"dut": voltage.get("dut"), "adapter": voltage.get("adapter"),
                        "verified": voltage.get("verified") is True}
        self.dtr_rts_reach_dut = raw.get("dtr_rts_reach_dut")
        self.reply_marker = raw.get("reply_marker")
        self.eol = raw.get("eol", "\n")
        self.problems = list(problems)

    @property
    def usable(self):
        """True when nothing is wrong with the declaration. An unusable link is never opened."""
        return not self.problems

    def can_send(self):
        """(ok, code, message): whether this link may transmit. Reading needs none of this."""
        if self.host_tx_to_dut is not True:
            return False, "link_rx_only", "this link is receive-only: the host's TX is not wired to the device"
        if not self.voltage["verified"]:
            return False, "voltage_unverified", "this link's voltage has not been measured, so nothing is sent on it"
        return True, "ok", ""

    def to_dict(self):
        return {"name": self.name, "slot": self.slot, "device": self.device, "backend": self.backend,
                "adapter": {"ids": self.ids, "serial": self.serial, "slot": self.adapter_slot},
                "own_bridge": self.own_bridge, "dtr_rts_reach_dut": self.dtr_rts_reach_dut, "port": self.port,
                "baud": self.baud, "dut_tx_to_host": self.dut_tx_to_host, "host_tx_to_dut": self.host_tx_to_dut,
                "rx_only": self.host_tx_to_dut is not True, "voltage": self.voltage, "reply_marker": self.reply_marker,
                "eol": self.eol, "problems": [p.to_dict() for p in self.problems]}


def _validate(raw, seen, board_ids):
    problems = []
    if not isinstance(raw, dict):
        return [Problem("invalid", "a link is a mapping")]
    name = raw.get("name")
    if not isinstance(name, str) or not SLUG.match(name):
        problems.append(Problem("invalid", "name must be 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit"))
    elif name in seen:
        problems.append(Problem("invalid", "the name %s is used by another link" % name))
    for key in sorted(set(raw) - KNOWN):
        problems.append(Problem("invalid", "unknown setting %s" % key))

    backend = raw.get("backend")
    if backend not in BACKENDS:
        problems.append(Problem("invalid", "backend must be one of %s" % ", ".join(BACKENDS)))
    elif backend == "pi-uart":
        port = raw.get("port")
        if not isinstance(port, str) or not PI_PORT.match(port):
            problems.append(Problem("invalid", "port is the Pi's own UART by name, such as ttyAMA0"))
        elif port not in PI_UART_PINS:
            problems.append(Problem("not_a_declared_link", "%s is not a UART whose header pins are known here (only %s is)"
                                    % (port, ", ".join(sorted(PI_UART_PINS)))))

    adapter = raw.get("adapter")
    ids = adapter.get("ids") if isinstance(adapter, dict) else None
    if backend == "usb-adapter":
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and IDS.match(i) for i in ids):
            problems.append(Problem("invalid", "adapter.ids must be a list of USB ids written vvvv:pppp in lower case"))
        elif set(ids) & set(board_ids):
            problems.append(Problem("not_a_declared_link", "these ids belong to a board profile: the console never opens a device's own port"))
        serial = adapter.get("serial") if isinstance(adapter, dict) else None
        slot = adapter.get("slot") if isinstance(adapter, dict) else None
        if raw.get("own_bridge") is True:
            if bool(slot) == bool(serial):
                problems.append(Problem("invalid", "an ESP32's own bridge is found by exactly one of adapter.serial and adapter.slot"))
        elif not (isinstance(serial, str) and serial):
            problems.append(Problem("invalid", "adapter.serial is required: an adapter is found by its serial, never by a /dev name"))

    baud = raw.get("baud")
    if baud == 1200:
        problems.append(Problem("baud_refused", "1200 baud is refused on every link: opening a port at 1200 baud is the bootloader touch"))
    elif baud not in BAUDS:
        problems.append(Problem("baud_refused", "baud must be one of %s" % ", ".join(str(b) for b in BAUDS)))

    for key in ("dut_tx_to_host", "host_tx_to_dut", "dtr_rts_reach_dut"):
        if not isinstance(raw.get(key), bool):
            problems.append(Problem("invalid", "%s must be declared, true or false" % key))
    if raw.get("dtr_rts_reach_dut") is True and raw.get("own_bridge") is not True:
        problems.append(Problem("dtr_rts_unsafe", "DTR or RTS reach the device: opening the adapter could reset it, and a deliberately wired reset line belongs to the family driver"))

    voltage = raw.get("voltage")
    if not isinstance(voltage, dict) or not isinstance(voltage.get("verified"), bool):
        problems.append(Problem("invalid", "voltage must say dut, adapter and verified (true or false)"))
    marker = raw.get("reply_marker")
    if marker is not None and (not isinstance(marker, str) or not marker):
        problems.append(Problem("invalid", "reply_marker is a non-empty string, or null"))
    if raw.get("eol", "\n") not in EOLS:
        problems.append(Problem("invalid", "eol must be a newline, a carriage return or both"))
    return problems


def build(raw, seen=(), board_ids=()):
    """A Link from a plain dict, validated exactly as one loaded from links.yaml (the wiring store builds its links here, so
    the two sources can never disagree about what a usable link is)."""
    return Link(raw, _validate(raw, set(seen), board_ids))


def load(path, board_ids=()):
    """([Link], [file problem strings]). A missing or unreadable file is one problem and no links, never a crash."""
    if not path:
        return [], ["no links file is configured"]
    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        return [], ["%s cannot be read: %s" % (path, exc)]
    except yaml.YAMLError as exc:
        return [], ["%s is not valid YAML: %s" % (path, str(exc).splitlines()[0] if str(exc) else exc)]
    if data is None:
        return [], []
    if not isinstance(data, dict) or not isinstance(data.get("links", []), list):
        return [], ["%s must be a mapping with a `links` list" % path]
    unknown = sorted(set(data) - set(["links"]))
    file_problems = ["%s: unknown setting %s" % (path, k) for k in unknown]
    links, seen = [], set()
    for raw in data.get("links") or []:
        problems = _validate(raw, seen, board_ids)
        link = Link(raw, problems)
        if link.name and SLUG.match(link.name):
            seen.add(link.name)                     # the first link of a name keeps it; a later one is the duplicate
        links.append(link)
    return links, file_problems


def resolve(link, sysfs="/sys", devroot="/dev"):
    """({device, node} or None, reason). READ-ONLY: it reads sysfs and stat()s a node, and never opens one. The adapter
    is the USB device whose ids and serial are the link's; its node is joined by device number."""
    if not link.usable:
        return None, "the link is not usable: " + "; ".join(p.message for p in link.problems)
    if link.backend == "pi-uart":
        node = os.path.join(devroot, link.port)
        try:
            mode = os.stat(node).st_mode
        except OSError:
            return None, "there is no %s: the Pi's UART is not enabled (wiring.uart_status says how)" % node
        if not stat.S_ISCHR(mode):
            return None, "%s exists but is not a character device" % node
        return {"device": {"name": link.port}, "node": node}, ""
    by_slot = link.adapter_slot is not None
    mine = [d for d in identity.inventory(sysfs)
            if d["id"] in link.ids and (d["name"] == link.adapter_slot if by_slot else d["serial"] == link.serial)]
    where = "slot %s" % link.adapter_slot if by_slot else "serial %s" % link.serial
    if not mine:
        return None, "the adapter %s (%s) is not attached" % (", ".join(link.ids), where)
    if len(mine) > 1:
        return None, "more than one USB device has these ids and this %s, so no node is chosen" % where.split()[0]
    node, why = identity.node_for(mine[0], devroot)
    if node is None:
        return None, why
    return {"device": mine[0], "node": node}, ""
