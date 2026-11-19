"""A project's manifest (docs/specs/projects.md, "The manifest"): parsed and checked, never guessed.

`validate(text, name)` returns `(manifest, [])` or `(None, [errors])`; an error names the field (`pins[1].voltage_v: ...`). Every field a
command needs is stated and none is defaulted; an unknown key is an error, so a typo is not silently ignored. Nothing here touches the
bench: whether a pin is legal, an adapter present or a name free is the plan's business, with the live stores in hand.
"""
import re

import math

import yaml

from ..instruments.meter import EXPECT as METER_FUNCTIONS

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
HEX_ID = re.compile(r"^[0-9a-f]{8,64}$")
SERIAL = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
MAX_BYTES = 64 * 1024            # a manifest is a page or two of YAML; 64 KiB is thirty times a generous one
PURPOSE_MAX = 200                # the pin map's own limit for a sentence
NOTES_MAX = 4000
ROLE_RESERVED = ("i2c", "meter")   # an `expect` key that is not a role
PI_PREFIX = "uart:"
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
SECTIONS = ("name", "purpose", "requires", "devices", "esp32", "wiring", "pins", "macros", "firmware", "expect", "notes")
PIN_COMMON = ("gpio", "name", "direction", "service", "action", "purpose", "active_low", "voltage_v", "voltage_verified")
PIN_INPUT, PIN_OUTPUT = ("pull",), ("initial", "max_pulse_ms", "drives_dut")


def _unknown(where, mapping, allowed, errors):
    for key in sorted(set(mapping) - set(allowed), key=str):
        errors.append("%s: %r is not a field here (allowed: %s)" % (where, key, ", ".join(allowed)))


def _need(where, mapping, key, errors):
    if key not in mapping or mapping[key] is None:
        errors.append("%s.%s: is required" % (where, key))
        return False
    return True


def _slug(where, value, errors):
    if not isinstance(value, str) or not SLUG.match(value):
        errors.append("%s: 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit" % where)
        return False
    return True


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _devices(raw, errors):
    out = {}
    if not isinstance(raw, dict):
        errors.append("devices: a mapping of role to {esp32: <target name>} or {nrf: <usb serial>}")
        return out
    for role, spec in raw.items():
        where = "devices.%s" % role
        if not _slug(where, role, errors) or role in ROLE_RESERVED:
            if role in ROLE_RESERVED:
                errors.append("%s: %r is reserved" % (where, role))
            continue
        if not isinstance(spec, dict) or len(spec) != 1 or next(iter(spec)) not in ("esp32", "nrf"):
            errors.append("%s: exactly one of esp32 (a target name) or nrf (a USB serial)" % where)
            continue
        kind, key = next(iter(spec.items()))
        if kind == "esp32" and not _slug("%s.esp32" % where, key, errors):
            continue
        if kind == "nrf" and not (isinstance(key, str) and SERIAL.match(key)):
            errors.append("%s.nrf: the board's USB serial" % where)
            continue
        out[role] = {"kind": kind, "key": key}
    return out


def _esp32(raw, errors):
    out = []
    if not isinstance(raw, list):
        errors.append("esp32: a list of {name, adapter or slot, baud}")
        return out
    for i, item in enumerate(raw):
        where = "esp32[%d]" % i
        if not isinstance(item, dict):
            errors.append("%s: a mapping" % where)
            continue
        _unknown(where, item, ("name", "adapter", "slot", "baud"), errors)
        ok = _need(where, item, "name", errors) and _slug("%s.name" % where, item["name"], errors)
        if ("adapter" in item) == ("slot" in item):
            errors.append("%s: exactly one of adapter (a USB serial) or slot (a USB port)" % where)
            ok = False
        elif "adapter" in item and not (isinstance(item["adapter"], str) and SERIAL.match(item["adapter"])):
            errors.append("%s.adapter: the adapter's USB serial" % where)
            ok = False
        elif "slot" in item and not (isinstance(item["slot"], str) and re.match(r"^[0-9][0-9.-]*$", item["slot"])):
            errors.append("%s.slot: a USB port such as 1-1.3" % where)
            ok = False
        if not _need(where, item, "baud", errors) or item["baud"] not in BAUDS:
            errors.append("%s.baud: one of %s" % (where, ", ".join(str(b) for b in BAUDS)))
            ok = False
        if ok:
            rec = {"name": item["name"], "baud": item["baud"]}
            rec["serial" if "adapter" in item else "slot"] = item.get("adapter", item.get("slot"))
            out.append(rec)
    return out


def _wiring(raw, roles, errors):
    out = []
    if not isinstance(raw, list):
        errors.append("wiring: a list of {name, device, adapter, baud, send}")
        return out
    for i, item in enumerate(raw):
        where = "wiring[%d]" % i
        if not isinstance(item, dict):
            errors.append("%s: a mapping" % where)
            continue
        _unknown(where, item, ("name", "device", "adapter", "baud", "send", "voltage_measured"), errors)
        ok = True
        for key in ("name", "device", "adapter", "baud", "send"):
            ok = _need(where, item, key, errors) and ok
        if not ok:
            continue
        if not _slug("%s.name" % where, item["name"], errors):
            ok = False
        if roles.get(item["device"], {}).get("kind") != "nrf":
            errors.append("%s.device: %r is not an nrf role in devices (a debug log is wired to an nRF board)" % (where, item["device"]))
            ok = False
        if not (isinstance(item["adapter"], str) and item["adapter"]):
            errors.append("%s.adapter: a USB adapter's serial, or uart:<port> for the Pi's own UART, such as uart:ttyAMA1" % where)
            ok = False
        if item["baud"] not in BAUDS:
            errors.append("%s.baud: one of %s" % (where, ", ".join(str(b) for b in BAUDS)))
            ok = False
        if not isinstance(item["send"], bool):
            errors.append("%s.send: true or false" % where)
            ok = False
        pi = isinstance(item["adapter"], str) and item["adapter"].startswith(PI_PREFIX)
        if ok and item["send"] and not pi and not isinstance(item.get("voltage_measured"), bool):
            errors.append("%s.voltage_measured: true or false when sending through a USB adapter (has its voltage been measured)" % where)
            ok = False
        if ok and (not item["send"] or pi) and "voltage_measured" in item:
            errors.append("%s.voltage_measured: only for sending through a USB adapter" % where)
            ok = False
        if ok:
            out.append({"name": item["name"], "device": item["device"], "adapter": item["adapter"], "baud": item["baud"],
                        "send": item["send"], "voltage_measured": item.get("voltage_measured")})
    return out


def _pins(raw, errors):
    out = []
    if not isinstance(raw, list):
        errors.append("pins: a list of pin assignments, as gpio.assign takes them")
        return out
    for i, item in enumerate(raw):
        where = "pins[%d]" % i
        if not isinstance(item, dict):
            errors.append("%s: a mapping" % where)
            continue
        _unknown(where, item, PIN_COMMON + PIN_INPUT + PIN_OUTPUT, errors)
        missing = [k for k in PIN_COMMON if not _need(where, item, k, errors)]
        if missing:
            continue
        direction = item["direction"]
        extra = PIN_OUTPUT if direction == "output" else PIN_INPUT
        if direction not in ("input", "output"):
            errors.append("%s.direction: input or output" % where)
            continue
        if any(not _need(where, item, k, errors) for k in extra):
            continue
        out.append(dict((k, item[k]) for k in PIN_COMMON + extra))          # the pin map's own validation judges the values
    return out


def _macros(raw, project, errors):
    out = {}
    if not isinstance(raw, dict):
        errors.append("macros: a mapping of name to a list of lines")
        return out
    for name, lines in raw.items():
        where = "macros.%s" % name
        if not _slug(where, name, errors):
            continue
        if len("%s.%s" % (project, name)) > 64:
            errors.append("%s: the stored name %s.%s is more than 64 characters" % (where, project, name))
            continue
        if not isinstance(lines, list) or not lines or not all(isinstance(x, str) for x in lines):
            errors.append("%s: a list of one or more lines, each a string" % where)
            continue
        out[name] = "\n".join(lines)
    return out


def _firmware(raw, roles, errors):
    out = {}
    if not isinstance(raw, dict):
        errors.append("firmware: a mapping of role to {id: <package id>}")
        return out
    for role, spec in raw.items():
        where = "firmware.%s" % role
        if role not in roles:
            errors.append("%s: %r is not a role in devices" % (where, role))
        elif not isinstance(spec, dict) or set(spec) != {"id"} or not (isinstance(spec["id"], str) and HEX_ID.match(spec["id"])):
            errors.append("%s: {id: <8 to 64 lowercase hex characters>}" % where)
        else:
            out[role] = {"id": spec["id"]}
    return out


def _expect(raw, roles, errors):
    out = {"console": {}, "i2c": None, "meter": None}
    if not isinstance(raw, dict):
        errors.append("expect: a mapping of role to {console: <text>}, and optionally i2c: {bus, address} and meter: {function, min, max}")
        return out
    for key, spec in raw.items():
        where = "expect.%s" % key
        if key == "i2c":
            if not isinstance(spec, dict) or set(spec) != {"bus", "address"} or not _int(spec["bus"]) or not _int(spec["address"]) \
                    or not 0x08 <= spec["address"] <= 0x77:
                errors.append("%s: {bus: <number>, address: <7-bit address, 8 to 119>}" % where)
            else:
                out["i2c"] = {"bus": spec["bus"], "address": spec["address"]}
        elif key == "meter":
            if not isinstance(spec, dict) or set(spec) != {"function", "min", "max"} or spec["function"] not in METER_FUNCTIONS \
                    or not _number(spec["min"]) or not _number(spec["max"]) or spec["min"] > spec["max"]:
                errors.append("%s: {function: one of %s, min: <number>, max: <number, not below min>} in volts, amps, ohms, hertz or farads"
                              % (where, ", ".join(METER_FUNCTIONS)))
            else:
                out["meter"] = {"function": spec["function"], "min": spec["min"], "max": spec["max"]}
        elif key not in roles:
            errors.append("%s: %r is not a role in devices" % (where, key))
        elif not isinstance(spec, dict) or set(spec) != {"console"} or not (isinstance(spec["console"], str) and 1 <= len(spec["console"]) <= 200):
            errors.append("%s: {console: <a line of text, 1 to 200 characters>}" % where)
        else:
            out["console"][key] = spec["console"]
    return out


def validate(text, name):
    if not isinstance(text, str):
        return None, ["the manifest is text"]
    if len(text.encode("utf-8")) > MAX_BYTES:
        return None, ["the manifest is at most %d bytes" % MAX_BYTES]
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, ["not valid YAML: %s" % (str(exc).splitlines()[0] if str(exc) else exc)]
    if not isinstance(raw, dict):
        return None, ["the manifest is a mapping of sections"]
    errors = []
    _unknown("manifest", raw, SECTIONS, errors)
    if raw.get("name") != name:
        errors.append("name: must be %r, the project's own name (it is %r)" % (name, raw.get("name")))
    purpose = raw.get("purpose", "")
    if not isinstance(purpose, str) or len(purpose) > PURPOSE_MAX or "\n" in purpose:
        errors.append("purpose: one line of at most %d characters" % PURPOSE_MAX)
        purpose = ""
    notes = raw.get("notes", "")
    if not isinstance(notes, str) or len(notes) > NOTES_MAX:
        errors.append("notes: text of at most %d characters" % NOTES_MAX)
        notes = ""
    requires = raw.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(x, str) and SLUG.match(x) for x in requires):
        errors.append("requires: a list of names such as i2c, pi-uart, esp32")
        requires = []
    roles = _devices(raw["devices"], errors) if "devices" in raw else {}
    m = {"name": name, "purpose": purpose, "notes": notes, "requires": list(requires), "devices": roles,
         "esp32": _esp32(raw["esp32"], errors) if "esp32" in raw else [],
         "wiring": _wiring(raw["wiring"], roles, errors) if "wiring" in raw else [],
         "pins": _pins(raw["pins"], errors) if "pins" in raw else [],
         "macros": _macros(raw["macros"], name, errors) if "macros" in raw else {},
         "firmware": _firmware(raw["firmware"], roles, errors) if "firmware" in raw else {},
         "expect": _expect(raw["expect"], roles, errors) if "expect" in raw else {"console": {}, "i2c": None, "meter": None}}
    for section, key in (("esp32", "name"), ("wiring", "name"), ("pins", "name"), ("pins", "gpio")):
        seen = set()
        for item in m[section]:
            if item[key] in seen:
                errors.append("%s: %s %r is used twice" % (section, key, item[key]))
            seen.add(item[key])
    if errors:
        return None, errors
    return m, []
