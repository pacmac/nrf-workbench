"""Registers the GPIO pin-map commands (docs/specs/gpio.md) in the core registry.

These commands read and write the pin MAP, a file the bench keeps. None of them opens a gpiochip or reads or drives
a pin, and a gate test proves it. A refusal is made here, by the service, with a code the caller shows by name.
"""
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from . import header
from .pinmap import Invalid, PinStore, PinStoreError, Refused

GPIO = ("gpio",)


def _store(host):
    return host.service("gpio_pins", lambda: PinStore(host.config["paths"]["gpio"]))


def _failed(exc):
    return Result.failed("write_failed", str(exc))


def _header(ctx):
    try:
        pins = _store(ctx.host).list()
    except PinStoreError as exc:
        return _failed(exc)
    held = dict((p["gpio"], p["name"]) for p in pins)
    rows = [dict(row, assigned=held.get(row["gpio"])) for row in header.table()]
    return Result.success({"header": rows}, "%d of the header's GPIOs are assigned" % len(held))


def _pins(ctx):
    try:
        pins = _store(ctx.host).list()
    except PinStoreError as exc:
        return _failed(exc)
    return Result.success({"pins": pins}, "%d pin%s assigned" % (len(pins), "" if len(pins) == 1 else "s"))


def _assign(ctx, **fields):
    try:
        record, created = _store(ctx.host).assign(fields, by=ctx.caller)
    except Invalid as exc:
        raise UsageError(str(exc), exc.arg)
    except Refused as exc:
        return Result.refused(exc.code, str(exc), exc.evidence)
    except (PinStoreError, OSError) as exc:
        return _failed(exc)
    return Result.success(dict(record, created=created), "assigned" if created else "replaced")


def _unassign(ctx, name):
    try:
        gone = _store(ctx.host).unassign(name)
    except (PinStoreError, OSError) as exc:
        return _failed(exc)
    if not gone:
        return Result.refused("pin_unknown", "no pin is assigned under that name", {"name": name})
    return Result.success({"name": name}, "unassigned")


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("gpio.header", "The Pi's 40-pin header, each GPIO with whether it is assigned.", _header,
                requires=GPIO, result="header: one row per physical pin"))
    add(Command("gpio.pins", "The assigned pins: which GPIO is for which service and action.", _pins,
                requires=GPIO, result="pins"))
    add(Command(
        "gpio.assign",
        "Assign a header GPIO to a service and action, or replace the assignment of that name. Records the assignment only: no pin is driven.",
        _assign,
        args=(Arg("name", doc="a slug: 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit"),
              Arg("gpio", type="int", doc="the BCM number, 0 to 27 (0 and 1 are reserved)"),
              Arg("direction", doc="input or output"),
              Arg("service", doc="the service that uses it, a slug (programmer, power, manual ...)"),
              Arg("action", doc="what it is for, a slug (reset, boot, power-cycle, led ...)"),
              Arg("purpose", doc="one sentence, at most 200 characters, on one line"),
              Arg("active_low", type="bool", doc="true if the line is asserted by driving it low"),
              Arg("voltage_v", type="float", unit="V", doc="the volts the connected signal swings; above 3.3 is refused"),
              Arg("voltage_verified", type="bool", doc="true only if someone measured that voltage"),
              Arg("initial", required=False, doc="output: inactive or active, the state the line is put in when taken"),
              Arg("max_pulse_ms", type="int", required=False, unit="ms",
                  doc="output: the longest pulse this line may ever be given, derived from what it drives"),
              Arg("drives_dut", type="bool", required=False, doc="output: true if the line reaches a DUT pin (reset, boot, power)"),
              Arg("pull", required=False, doc="input: up, down or none")),
        requires=GPIO, effect="write", result="the assignment, and whether it was new"))
    add(Command("gpio.unassign", "Remove an assignment by name.", _unassign, args=(Arg("name", doc="the assignment's name"),),
                requires=GPIO, effect="write", result="the name removed"))
