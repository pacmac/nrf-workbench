"""The pin map: validation and the store (docs/specs/gpio.md, "the pin map").

A pin is declared with every field stated and none defaulted. A malformed field is `Invalid` (a usage error); a rule
the service enforces is `Refused` with a code the caller shows by name. Nothing here touches a pin.
"""
import math
import os
import re
import threading
import time

from ..core import jsonstore
from . import header

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# One clear sentence is about 100 to 140 characters; 200 is 1.5x that: room for a sentence, not a paragraph.
PURPOSE_MAX = 200
SIGNAL_MAX_V = 3.3                                  # header GPIO is 3.3 V and is not 5 V tolerant
DIRECTIONS = ("input", "output")
STATES = ("inactive", "active")
PULLS = ("up", "down", "none")

PinStoreError = jsonstore.StoreError


class Invalid(Exception):
    """A field is malformed or missing. `arg` names it."""

    def __init__(self, message, arg):
        Exception.__init__(self, message)
        self.arg = arg


class Refused(Exception):
    """A rule the service enforces. `code` is the refusal code."""

    def __init__(self, code, message, evidence=None):
        Exception.__init__(self, message)
        self.code, self.evidence = code, evidence or {}


def _slug(value, arg):
    if not isinstance(value, str) or not SLUG.match(value):
        raise Invalid("%s is 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit" % arg, arg)
    return value


def _need(value, arg, what):
    if value is None:
        raise Invalid("%s is required (%s)" % (arg, what), arg)
    return value


def build(fields, others, reserved=()):
    """The record for a declaration, or an exception. `fields` are the declared values (None for a field not stated);
    `others` is {name: record} for every OTHER declared pin, so a name that is being replaced does not conflict with
    itself. Shape first (Invalid), then the rules (Refused)."""
    f = fields
    name = _slug(f.get("name"), "name")
    gpio = f.get("gpio")
    if isinstance(gpio, bool) or not isinstance(gpio, int):
        raise Invalid("gpio is the BCM number, a whole number", "gpio")
    direction = f.get("direction")
    if direction not in DIRECTIONS:
        raise Invalid("direction is one of %s" % ", ".join(DIRECTIONS), "direction")
    service, action = _slug(f.get("service"), "service"), _slug(f.get("action"), "action")
    purpose = f.get("purpose")
    if not isinstance(purpose, str) or not 1 <= len(purpose.strip()) <= PURPOSE_MAX or any(ord(c) < 32 for c in purpose):
        raise Invalid("purpose is one sentence of 1 to %d characters, on one line" % PURPOSE_MAX, "purpose")
    active_low = _need(f.get("active_low"), "active_low", "true if the line is asserted by driving it low")
    if not isinstance(active_low, bool):
        raise Invalid("active_low is true or false", "active_low")
    signal = _need(f.get("voltage_v"), "voltage_v", "the volts the connected signal swings")
    if isinstance(signal, bool) or not isinstance(signal, (int, float)) or not math.isfinite(signal) or signal <= 0:
        raise Invalid("voltage_v is the volts the signal swings, more than 0", "voltage_v")
    verified = _need(f.get("voltage_verified"), "voltage_verified", "true only if someone measured it")
    if not isinstance(verified, bool):
        raise Invalid("voltage_verified is true or false", "voltage_verified")

    record = {"name": name, "gpio": gpio, "direction": direction, "service": service, "action": action,
              "purpose": purpose.strip(), "active_low": active_low, "voltage": {"signal": float(signal), "verified": verified}}
    if direction == "output":
        for arg in ("pull",):
            if f.get(arg) is not None:
                raise Invalid("%s is for an input" % arg, arg)
        initial = _need(f.get("initial"), "initial", "inactive or active: the state the line is put in when taken")
        if initial not in STATES:
            raise Invalid("initial is one of %s" % ", ".join(STATES), "initial")
        pulse = _need(f.get("max_pulse_ms"), "max_pulse_ms", "the longest pulse this line may be given, derived from what it drives")
        if isinstance(pulse, bool) or not isinstance(pulse, int) or pulse < 1:
            raise Invalid("max_pulse_ms is a whole number of milliseconds, 1 or more", "max_pulse_ms")
        drives = _need(f.get("drives_dut"), "drives_dut", "true if the line reaches a DUT pin: reset, boot or power")
        if not isinstance(drives, bool):
            raise Invalid("drives_dut is true or false", "drives_dut")
        record.update({"initial": initial, "max_pulse_ms": pulse, "drives_dut": drives})
    else:
        for arg in ("initial", "max_pulse_ms", "drives_dut"):
            if f.get(arg) is not None:
                raise Invalid("%s is for an output" % arg, arg)
        pull = _need(f.get("pull"), "pull", "up, down or none")
        if pull not in PULLS:
            raise Invalid("pull is one of %s" % ", ".join(PULLS), "pull")
        record["pull"] = pull

    # the rules, after the shape
    if gpio not in header.HEADER_GPIOS:
        raise Refused("pin_not_on_header", "GPIO %d is not on the 40-pin header (it is 0 to 27)" % gpio, {"gpio": gpio})
    if gpio in header.RESERVED_GPIOS:
        raise Refused("pin_reserved", "GPIO %d is the HAT ID EEPROM's bus and is never offered" % gpio, {"gpio": gpio})
    if gpio in reserved:
        raise Refused("pin_reserved", "GPIO %d is reserved on this bench (pins.reserved in its configuration): a wire to a board may still be on it, "
                      "and the bench keeps it floating" % gpio, {"gpio": gpio, "reserved_by": "configuration"})
    if signal > SIGNAL_MAX_V:
        raise Refused("voltage_unsafe", "a header GPIO is 3.3 V and is not 5 V tolerant; a %s V signal would damage it" % signal,
                      {"gpio": gpio, "voltage": signal})
    for other in others.values():
        if other["gpio"] == gpio:
            raise Refused("pin_taken", "GPIO %d is already assigned to %s" % (gpio, other["name"]), {"gpio": gpio, "held_by": other["name"]})
    record["class"] = header.gpio_class(gpio, reserved)
    return record


class PinStore(object):
    def __init__(self, directory, now=time.time):
        self.path = os.path.join(directory, "pins.json")
        self._now = now
        self._lock = threading.Lock()

    def _read(self):
        return jsonstore.read_map(self.path, "pins")

    def list(self):
        """Every declared pin, in GPIO order."""
        with self._lock:
            pins = self._read()
        return sorted(pins.values(), key=lambda p: p["gpio"])

    def assign(self, fields, by=None, reserved=()):
        """(record, created). Raises Invalid or Refused; a name already declared is replaced. `reserved` is the bench's own list of pins it never hands out."""
        with self._lock:
            pins = self._read()
            name = fields.get("name")
            others = dict((n, p) for n, p in pins.items() if n != name)
            record = build(fields, others, reserved)
            created = record["name"] not in pins
            record["updated_at"], record["updated_by"] = self._now(), by
            pins[record["name"]] = record
            jsonstore.write_map(self.path, "pins", pins)
            return record, created

    def unassign(self, name):
        """True if it was declared."""
        with self._lock:
            pins = self._read()
            if name not in pins:
                return False
            del pins[name]
            jsonstore.write_map(self.path, "pins", pins)
            return True
