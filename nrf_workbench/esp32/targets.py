"""ESP32 targets (docs/specs/esp32.md, "Targets"): which attached USB adapter is an ESP32 board, by someone's say-so.

A USB id says nothing about what is behind a bridge chip, so a board is only an ESP32 once it is DECLARED, under a
name, by the adapter's serial when exactly one attached device reports it, otherwise by its slot (the sysfs port
name, which is where it is plugged). READ-ONLY apart from the one file, which `core.jsonstore` writes atomically.
Nothing here opens a port.
"""
import os
import re

from ..core import jsonstore
from ..devices import identity

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FILE = "esp32.json"
KEY = "targets"
NATIVE_IDS = ("303a:1001",)            # the ESP32-S3/C3/C6 built-in USB Serial/JTAG: its serial is the chip's MAC


class Invalid(Exception):
    def __init__(self, message, arg):
        Exception.__init__(self, message)
        self.arg = arg


class Refused(Exception):
    def __init__(self, code, message, evidence=None):
        Exception.__init__(self, message)
        self.code, self.evidence = code, evidence or {}


class TargetStore(object):
    def __init__(self, folder):
        self.path = os.path.join(folder, FILE)

    def list(self):
        """[{name, key, key_kind}] by name."""
        return [dict(rec, name=name) for name, rec in sorted(jsonstore.read_map(self.path, KEY).items())]

    def get(self, name):
        rec = jsonstore.read_map(self.path, KEY).get(name)
        return dict(rec, name=name) if rec else None

    def add(self, name, key, key_kind):
        records = jsonstore.read_map(self.path, KEY)
        records[name] = {"key": key, "key_kind": key_kind}
        jsonstore.write_map(self.path, KEY, records)

    def remove(self, name):
        records = jsonstore.read_map(self.path, KEY)
        if name not in records:
            return False
        del records[name]
        jsonstore.write_map(self.path, KEY, records)
        return True


def matching(inv, key_kind, key):
    """The attached devices a target's key names."""
    field = "serial" if key_kind == "serial" else "name"
    return [d for d in inv if d.get(field) == key]


def key_for(inv, serial=None, slot=None):
    """(key, key_kind) for the adapter named by `serial` or by `slot`. Raises Invalid or Refused."""
    if (serial is None) == (slot is None):
        raise Invalid("name the adapter by its serial or by its slot, and not both", "serial")
    if serial is not None:
        found = matching(inv, "serial", serial)
        if not found:
            raise Refused("adapter_absent", "no attached USB device reports serial %s" % serial)
        if len(found) > 1:
            raise Refused("serial_unverifiable", "%d attached devices report serial %s, so it cannot tell them apart; "
                          "declare the target by slot" % (len(found), serial),
                          {"slots": sorted(d["name"] for d in found)})
        return serial, "serial"
    if not matching(inv, "slot", slot):
        raise Refused("adapter_absent", "no USB device is attached at slot %s" % slot)
    return slot, "slot"


def view(record, inv, devroot):
    """One target as a caller sees it: where it is attached now (no port is opened to find out)."""
    found = matching(inv, record["key_kind"], record["key"])
    out = dict(record, attached=len(found), node=None, problem="")
    if len(found) == 1:
        node, why = identity.node_for(found[0], devroot)
        out.update(node=node, problem="" if node else why, ids=found[0]["id"], product=found[0]["product"],
                   slot=found[0]["name"])
    elif found:
        out["problem"] = "%d attached devices match this target, so none can be chosen" % len(found)
    else:
        out["problem"] = "not attached"
    return out


def native(inv, declared):
    """Attached built-in-USB ESP32 devices no target names yet."""
    named = set((r["key_kind"], r["key"]) for r in declared)
    return [{"serial": d["serial"], "slot": d["name"], "ids": d["id"], "product": d["product"]}
            for d in inv if d["id"] in NATIVE_IDS and ("serial", d["serial"]) not in named
            and ("slot", d["name"]) not in named]
