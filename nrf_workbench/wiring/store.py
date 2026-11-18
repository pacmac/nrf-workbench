"""The wiring store and its validation (docs/specs/wiring.md, "Backend slice 1: the debug log").

A connection belongs to one device, is named by a slug (which is the console link's name), and is kept in
`<paths.wiring>/connections.json`. A malformed field is `Invalid` (a usage error); a rule the service enforces is `Refused`
with a code the caller shows by name. Every field is stated by the caller; only the name has a derived default.
"""
import os
import re
import threading
import time

from ..console import links as linkmod
from ..core import jsonstore
from . import detect

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SERIAL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")          # a USB serial as a board or an adapter reports it
INTENTS = ("log", "control", "bus", "swd", "analog")
BUILT = ("log",)

StoreError = jsonstore.StoreError


class Invalid(Exception):
    def __init__(self, message, arg):
        Exception.__init__(self, message)
        self.arg = arg


class Refused(Exception):
    def __init__(self, code, message, evidence=None):
        Exception.__init__(self, message)
        self.code, self.evidence = code, evidence or {}


def default_name(device):
    return "debug-%s" % re.sub(r"[^a-z0-9]", "", device.lower())[:4]


def holder_of(connections, adapter_serial, ids, ignore=None):
    """The connection that holds this adapter, or None. An adapter is held by exactly one connection."""
    for c in connections:
        if c["name"] != ignore and c["adapter"]["serial"] == adapter_serial and ids in c["adapter"]["ids"]:
            return c
    return None


def to_link(record, board_ids=()):
    """The console Link for a stored debug-log connection. It is built and validated by the console's own code, so a
    connection is usable exactly when a link declared in links.yaml with the same facts would be."""
    if record["adapter"]["ids"][0] == detect.PI_IDS:
        # The Pi's own UART (docs/specs/pi-uart.md): 3.3 V by design on both ends, no modem lines on the pins, so sending needs no measurement.
        return linkmod.build({
            "name": record["name"], "device": record["device"], "backend": "pi-uart",
            "port": record["adapter"]["serial"][len(detect.PI_PREFIX):],
            "baud": record["baud"], "dut_tx_to_host": True, "host_tx_to_dut": bool(record["send"]),
            "voltage": {"dut": 3.3, "adapter": 3.3, "verified": bool(record["send"])},
            "dtr_rts_reach_dut": False, "reply_marker": None,
        }, (), board_ids)
    return linkmod.build({
        "name": record["name"], "device": record["device"], "backend": "usb-adapter",
        "adapter": {"ids": list(record["adapter"]["ids"]), "serial": record["adapter"]["serial"]},
        "baud": record["baud"],
        "dut_tx_to_host": True,                        # a debug log is the device's TX to the adapter: that is what it is
        "host_tx_to_dut": bool(record["send"]),
        "voltage": {"dut": 3.3, "adapter": None, "verified": bool(record["send"]) and record.get("voltage_measured") is True},
        "dtr_rts_reach_dut": False, "reply_marker": None,
    }, (), board_ids)


def build_log(fields, detected, others, board_ids, existing=None):
    """The record for a debug-log connection, or an exception. `detected` is detect.adapters(); `others` is every OTHER
    connection (so an edit does not collide with itself); `existing` is the record being changed, if any."""
    f = fields
    device = f.get("device")
    if not isinstance(device, str) or not SERIAL.match(device):
        raise Invalid("device is the nRF's USB serial number", "device")
    name = f.get("name")
    if name is None:
        name = default_name(device)
    if not isinstance(name, str) or not SLUG.match(name):
        raise Invalid("name is 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit", "name")
    adapter_serial = f.get("adapter")
    if not isinstance(adapter_serial, str) or not adapter_serial:
        raise Invalid("adapter is the serial number of a detected USB adapter", "adapter")
    baud = f.get("baud")
    if isinstance(baud, bool) or not isinstance(baud, int):
        raise Invalid("baud is a whole number: the rate the device's firmware uses", "baud")
    send = f.get("send")
    if not isinstance(send, bool):
        raise Invalid("send is true or false: may the bench send to the device", "send")
    measured = f.get("voltage_measured")
    if adapter_serial.startswith(detect.PI_PREFIX):
        measured = None                                 # the Pi's UART is 3.3 V by design: nothing to measure, and nothing is asked
    elif send and not isinstance(measured, bool):
        raise Invalid("voltage_measured is true or false when send is true: has someone measured the adapter's voltage", "voltage_measured")
    if not send and measured is not None:
        raise Invalid("voltage_measured is only asked when send is true", "voltage_measured")

    # the rules, after the shape
    if any(c["name"] == name for c in others):
        raise Refused("name_taken", "a connection is already named %s" % name, {"name": name})
    mine = [a for a in detected if a["serial"] == adapter_serial]
    kept = existing is not None and existing["adapter"]["serial"] == adapter_serial
    if not mine and not kept:
        raise Refused("adapter_absent", "no USB adapter with serial %s is detected" % adapter_serial, {"adapter": adapter_serial})
    if len(mine) > 1:
        raise Refused("adapter_absent", "more than one USB adapter reports serial %s, so none can be chosen" % adapter_serial,
                      {"adapter": adapter_serial})
    ids = mine[0]["ids"] if mine else existing["adapter"]["ids"][0]
    if mine and mine[0]["problem"]:
        raise Refused(mine[0].get("problem_code") or "adapter_absent", mine[0]["problem"], {"adapter": adapter_serial})
    taken = holder_of(others, adapter_serial, ids)
    if taken is not None:
        raise Refused("adapter_taken", "that adapter is already used by %s" % taken["name"],
                      {"adapter": adapter_serial, "held_by": taken["name"]})
    record = {"name": name, "intent": "log", "device": device, "adapter": {"ids": [ids], "serial": adapter_serial},
              "baud": baud, "send": send, "voltage_measured": measured if send else None}
    if ids == detect.PI_IDS:
        record["pins"] = list(linkmod.PI_UART_PINS.get(adapter_serial[len(detect.PI_PREFIX):], ()))     # held by this connection (gpio.assign refuses them)
    link = to_link(record, board_ids)
    for problem in link.problems:                       # the console's own refusals: a board's own port, the touch baud, ...
        if problem.code in ("not_a_declared_link", "baud_refused", "dtr_rts_unsafe"):
            raise Refused(problem.code, problem.message, {"adapter": adapter_serial, "baud": baud})
        raise Invalid(problem.message, "baud" if "baud" in problem.message else "adapter")
    return record


class WiringStore(object):
    def __init__(self, directory, now=time.time):
        self.path = os.path.join(directory, "connections.json")
        self._now = now
        self._lock = threading.Lock()

    def _read(self):
        return jsonstore.read_map(self.path, "connections")

    def list(self):
        with self._lock:
            conns = self._read()
        return [conns[n] for n in sorted(conns)]

    def get(self, name):
        with self._lock:
            return self._read().get(name)

    def put(self, record, by=None):
        """Write `record`, replacing one of the same name. Returns (record, created)."""
        with self._lock:
            conns = self._read()
            created = record["name"] not in conns
            record = dict(record, updated_at=self._now(), updated_by=by)
            conns[record["name"]] = record
            jsonstore.write_map(self.path, "connections", conns)
            return record, created

    def delete(self, name):
        with self._lock:
            conns = self._read()
            if name not in conns:
                return False
            del conns[name]
            jsonstore.write_map(self.path, "connections", conns)
            return True
