"""Registers the ESP32 commands (docs/specs/esp32.md) in the core registry.

`esp32.targets` reads sysfs and never opens a port. `declare` and `forget` write the bench's own `esp32.json`. `flash` and
`identify` run esptool, which resets the board over the adapter's DTR/RTS, and so only when asked.
`host.hooks`: `esp_runner` and `esp_tool` are the test seams, empty in production.
"""
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from ..devices import identity
from ..guards.lock import DEFAULT_PATH
from ..programmer import commands as programmer_commands
from ..programmer import flash_file as ff
from . import flash, targets
from .targets import Invalid, Refused

WIRING = ("wiring",)
FLASH = ("firmware_store", "esp32")
IDENTIFY = ("esp32",)


def _store(host):
    return host.service("esp32_targets", lambda: targets.TargetStore(host.config["paths"]["wiring"]))


def _failed(exc):
    return Result.failed("write_failed", str(exc))


def _targets(ctx):
    host = ctx.host
    try:
        records = _store(host).list()
    except targets.jsonstore.StoreError as exc:
        return _failed(exc)
    inv = identity.inventory(host.sysfs)
    shown = [targets.view(r, inv, host.devroot) for r in records]
    return Result.success({"targets": shown, "undeclared_native": targets.native(inv, records)},
                          "%d target%s" % (len(shown), "" if len(shown) == 1 else "s"))


def _declare(ctx, name, serial=None, slot=None):
    host = ctx.host
    if not targets.NAME.match(name or ""):
        raise UsageError("a target name is 1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit", "name")
    try:
        store = _store(host)
        inv = identity.inventory(host.sysfs)
        key, kind = targets.key_for(inv, serial, slot)
        records = store.list()
        if any(r["name"] == name for r in records):
            return Result.refused("name_taken", "a target already has that name", {"name": name})
        for r in records:
            if (r["key"], r["key_kind"]) == (key, kind):
                return Result.refused("adapter_taken", "target %s is already that adapter" % r["name"],
                                      {"held_by": r["name"]})
        store.add(name, key, kind)
    except Invalid as exc:
        raise UsageError(str(exc), exc.arg)
    except Refused as exc:
        return Result.refused(exc.code, str(exc), exc.evidence)
    except (targets.jsonstore.StoreError, OSError) as exc:
        return _failed(exc)
    return Result.success({"name": name, "key": key, "key_kind": kind}, "declared")


def _forget(ctx, name):
    try:
        if not _store(ctx.host).remove(name):
            return Result.refused("target_unknown", "no target has that name", {"name": name})
    except (targets.jsonstore.StoreError, OSError) as exc:
        return _failed(exc)
    return Result.success({"name": name}, "forgotten")


def _record(host, name):
    try:
        rec = _store(host).get(name)
    except targets.jsonstore.StoreError as exc:
        return None, _failed(exc)
    if rec is None:
        return None, Result.refused("target_unknown", "no target has that name", {"name": name})
    return rec, None


def _flash(ctx, target, firmware, flash_bound_s, baud=None):
    host = ctx.host
    baud = flash.DEFAULT_BAUD if baud is None else baud       # an argument left out arrives as None
    rec, refusal = _record(host, target)
    if refusal is not None:
        return refusal
    hooks = host.hooks or {}
    return flash.flash_target(rec, programmer_commands._store(host), firmware, flash_bound_s, baud, units=host.units,
                              sysfs=host.sysfs, devroot=host.devroot, proc=host.proc,
                              lock_path=host.lock_path or DEFAULT_PATH, runner=hooks.get("esp_runner", ff.default_runner),
                              tool=hooks.get("esp_tool"))


def _identify(ctx, target, bound_s):
    host = ctx.host
    rec, refusal = _record(host, target)
    if refusal is not None:
        return refusal
    hooks = host.hooks or {}
    return flash.identify_target(rec, bound_s, units=host.units, sysfs=host.sysfs, devroot=host.devroot, proc=host.proc,
                                 lock_path=host.lock_path or DEFAULT_PATH,
                                 runner=hooks.get("esp_runner", ff.default_runner), tool=hooks.get("esp_tool"))


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("esp32.targets", "The declared ESP32 targets, where each is attached now, and built-in-USB ESP32 "
                "devices nobody has declared. Opens no port.", _targets, requires=WIRING,
                result="targets (name, key, key_kind, attached, node, problem), undeclared_native"))
    add(Command("esp32.declare", "Declare an attached USB adapter to be an ESP32 board, under a name. By its serial, or by "
                "its slot when more than one attached device reports the same serial.", _declare,
                args=(Arg("name", doc="1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit"),
                      Arg("serial", required=False, doc="the adapter's USB serial; unique among attached devices"),
                      Arg("slot", required=False, doc="the adapter's sysfs port name, such as 1-1.3")),
                requires=WIRING, effect="write", result="name, key, key_kind"))
    add(Command("esp32.forget", "Forget a declared ESP32 target.", _forget, args=(Arg("name", doc="the target's name"),),
                requires=WIRING, effect="write", result="name"))
    add(Command("esp32.flash", "Flash a stored ESP32 package to a declared target with esptool, verify every part, and "
                "reset the board.", _flash,
                args=(Arg("target", doc="the declared target's name"), Arg("firmware", doc="the id of the stored firmware"),
                      Arg("flash_bound_s", type="float", unit="s",
                          doc="ceiling for the whole esptool run; derive it from the image size and the baud"),
                      Arg("baud", type="int", required=False, doc="one of 115200 230400 460800 921600; default 115200")),
                requires=FLASH, effect="write",
                result="duration_s, image_bytes, parts, verified, baud, chip, mac, output"))
    add(Command("esp32.identify", "Ask the chip behind a target what it is. This resets the board over DTR/RTS.", _identify,
                args=(Arg("target", doc="the declared target's name"),
                      Arg("bound_s", type="float", unit="s", doc="ceiling for the esptool run")),
                requires=IDENTIFY, effect="write", result="chip, mac, duration_s, output"))
