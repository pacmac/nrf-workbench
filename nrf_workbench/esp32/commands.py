"""Registers the ESP32 commands (docs/specs/esp32.md) in the core registry.

`esp32.targets` reads sysfs and never opens a port. `declare` and `forget` write the bench's own `esp32.json` and hand the
console its links. `flash`, `identify` and `reset` run esptool, which resets the board over the adapter's DTR/RTS, and so only
when asked; the console's port on that adapter is released for the run and reopened after it, because the adapter is the same
one. `host.hooks`: `esp_runner` and `esp_tool` are the test seams, empty in production.
"""
import contextlib
import os
import shutil
import tempfile
import zipfile

from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from ..devices import identity
from ..guards.lock import DEFAULT_PATH
from ..programmer import commands as programmer_commands
from ..programmer import flash_file as ff
from ..wiring import commands as wiring_commands
from . import decode, flash, package, targets
from .targets import Invalid, Refused

WIRING = ("wiring",)
FLASH = ("firmware_store", "esp32")
ESP32 = ("esp32",)


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
    shown = []
    for r in records:
        v = targets.view(r, inv, host.devroot)
        st = host.console.state(r["name"]) if host.console is not None else None
        v["console"] = {k: st[k] for k in ("state", "reason", "lines", "bytes", "last_byte_at")} if st else None
        shown.append(v)
    return Result.success({"targets": shown, "undeclared_native": targets.native(inv, records)},
                          "%d target%s" % (len(shown), "" if len(shown) == 1 else "s"))


def _apply(host):
    """Give the console its links, now: a target is one of them."""
    wiring_commands._apply(host, wiring_commands._store(host).list())


def _declare(ctx, name, serial=None, slot=None, baud=None):
    host = ctx.host
    if not targets.NAME.match(name or ""):
        raise UsageError("a target name is 1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit", "name")
    baud = targets.DEFAULT_BAUD if baud is None else baud
    from ..console import links as linkmod
    if baud not in linkmod.BAUDS:                      # the console's own list, which has no touch rate in it
        raise UsageError("baud is one of %s: the rate the board's firmware logs at" % ", ".join(str(b) for b in linkmod.BAUDS),
                         "baud")
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
        ids = targets.matching(inv, kind, key)[0]["id"]
        wired = wiring_commands._store(host)
        for c in wired.list():
            if kind == "serial" and c["adapter"]["serial"] == key:
                return Result.refused("adapter_taken", "that adapter is already used by the wiring connection %s" % c["name"],
                                      {"held_by": c["name"]})
        store.add(name, key, kind, ids, baud)
        _apply(host)
    except Invalid as exc:
        raise UsageError(str(exc), exc.arg)
    except Refused as exc:
        return Result.refused(exc.code, str(exc), exc.evidence)
    except (targets.jsonstore.StoreError, OSError) as exc:
        return _failed(exc)
    return Result.success({"name": name, "key": key, "key_kind": kind, "baud": baud}, "declared")


def _rename(ctx, name, new_name):
    host = ctx.host
    if not targets.NAME.match(new_name or ""):
        raise UsageError("a target name is 1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit", "new_name")
    try:
        outcome = _store(host).rename(name, new_name)
        if outcome == "ok":
            _apply(host)                 # the console link is named like the target: the old one goes and the new one is made
    except (targets.jsonstore.StoreError, OSError) as exc:
        return _failed(exc)
    if outcome == "unknown":
        return Result.refused("target_unknown", "no target has that name", {"name": name})
    if outcome == "taken":
        return Result.refused("name_taken", "a target already has the name %s" % new_name, {"name": new_name})
    return Result.success({"name": new_name, "was": name}, "renamed")


def _forget(ctx, name):
    host = ctx.host
    try:
        if not _store(host).remove(name):
            return Result.refused("target_unknown", "no target has that name", {"name": name})
        _apply(host)
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


@contextlib.contextmanager
def _console_released(host, name):
    """The console's port on this target's adapter is closed while esptool uses it, and opened again afterwards, whatever
    happened. The link keeps its byte offset, so the log carries on where it was."""
    broker = host.console
    held = broker is not None and broker.suspend(name)
    try:
        yield
    finally:
        if held:
            broker.resume(name)


def _hooks(host):
    hooks = host.hooks or {}
    return {"runner": hooks.get("esp_runner", ff.default_runner), "tool": hooks.get("esp_tool")}


def _flash(ctx, target, firmware, flash_bound_s, baud=None):
    host = ctx.host
    baud = flash.DEFAULT_BAUD if baud is None else baud       # an argument left out arrives as None
    rec, refusal = _record(host, target)
    if refusal is not None:
        return refusal
    with _console_released(host, target):
        return flash.flash_target(rec, programmer_commands._store(host), firmware, flash_bound_s, baud, units=host.units,
                                  sysfs=host.sysfs, devroot=host.devroot, proc=host.proc,
                                  lock_path=host.lock_path or DEFAULT_PATH, **_hooks(host))


def _identify(ctx, target, bound_s):
    host = ctx.host
    rec, refusal = _record(host, target)
    if refusal is not None:
        return refusal
    with _console_released(host, target):
        return flash.identify_target(rec, bound_s, units=host.units, sysfs=host.sysfs, devroot=host.devroot, proc=host.proc,
                                     lock_path=host.lock_path or DEFAULT_PATH, **_hooks(host))


def _reset(ctx, target, mode, bound_s):
    host = ctx.host
    if mode not in flash.MODES:
        raise UsageError("mode is one of %s" % ", ".join(sorted(flash.MODES)), "mode")
    rec, refusal = _record(host, target)
    if refusal is not None:
        return refusal
    with _console_released(host, target):
        return flash.reset_target(rec, mode, bound_s, units=host.units, sysfs=host.sysfs, devroot=host.devroot,
                                  proc=host.proc, lock_path=host.lock_path or DEFAULT_PATH, **_hooks(host))


MAX_TEXT = 1024 * 128        # a console line is at most 1024 bytes (console.md) and a backtrace is at most 128 frames deep
MAX_ELF_BYTES = 64 * 1024 * 1024    # four times the largest flash chip an ESP32 takes (16 MiB); a debug ELF is bigger than the image


def _decode(ctx, firmware, text):
    host = ctx.host
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise UsageError("text is the crash dump, at most %d characters" % MAX_TEXT, "text")
    store = programmer_commands._store(host)
    verified = store.verify(firmware)
    if not verified.ok:
        return verified
    sha, path, record = verified.data["id"], verified.data["path"], verified.data["record"]
    if record.get("kind") != package.KIND_BUNDLE or not record.get("elf"):
        return Result.refused("bad_package", "this firmware carries no ELF to decode against: upload a zip with "
                              "flasher_args.json whose app_elf names it", {"firmware_id": sha})
    info = package.inspect(path)
    with zipfile.ZipFile(path) as z:
        size = z.getinfo(info["elf_name"]).file_size
        if size > MAX_ELF_BYTES:
            return Result.refused("bad_package", "the ELF is %d bytes, over the %d this reads" % (size, MAX_ELF_BYTES),
                                  {"firmware_id": sha})
        work = tempfile.mkdtemp(prefix=".esp-decode-", dir=os.path.dirname(os.path.dirname(path)))
        try:
            elf = z.extract(info["elf_name"], work)
            hooks = host.hooks or {}
            out = decode.decode(text, elf, chip=record.get("chip"), runner=hooks.get("esp_runner", ff.default_runner),
                                tool_hook=hooks.get("esp_addr2line"))
        except decode.ElfError as exc:
            return Result.refused("bad_package", "the ELF cannot be read: %s" % exc, {"firmware_id": sha})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    named = sum(1 for f in out["frames"] if f["function"])
    return Result.success(dict(out, firmware_id=sha, chip=record.get("chip")),
                          "%d address%s found, %d named" % (out["addresses"], "" if out["addresses"] == 1 else "es", named))


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("esp32.decode", "Turn the addresses in a crash dump (a Backtrace line, or PC, MEPC and RA) into functions, "
                "using the ELF in a stored bundle; file and line too when the chip's addr2line is on the PATH.", _decode,
                args=(Arg("firmware", doc="the id of the stored bundle that was flashed"),
                      Arg("text", doc="the crash dump as the console printed it")),
                # A write only because of how it is called: the dump is up to 128 KiB, which no query string carries, so it
                # is a POST with a body. It changes nothing that stays: its scratch folder is gone when it returns.
                requires=("firmware_store",), effect="write", result="frames (address, function, offset, file, line, "
                "source), addresses, truncated, tool, chip"))

    add(Command("esp32.targets", "The declared ESP32 targets, where each is attached now, its console, and built-in-USB "
                "ESP32 devices nobody has declared. Opens no port.", _targets, requires=WIRING,
                result="targets (name, key, key_kind, attached, node, problem, console), undeclared_native"))
    add(Command("esp32.declare", "Declare an attached USB adapter to be an ESP32 board, under a name. By its serial, or by "
                "its slot when more than one attached device reports the same serial. The board's console is then a "
                "receive-only link named like the target.", _declare,
                args=(Arg("name", doc="1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit"),
                      Arg("serial", required=False, doc="the adapter's USB serial; unique among attached devices"),
                      Arg("slot", required=False, doc="the adapter's sysfs port name, such as 1-1.3"),
                      Arg("baud", type="int", required=False, doc="the board's console rate; default 115200")),
                requires=WIRING, effect="write", result="name, key, key_kind, baud"))
    add(Command("esp32.rename", "Give a declared ESP32 target another name. Its adapter, baud and settings stay; its console link "
                "takes the new name, and the log kept under the old name stays with the old name.", _rename,
                args=(Arg("name", doc="the target's current name"),
                      Arg("new_name", doc="1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit")),
                requires=WIRING, effect="write", result="name (the new one), was"))
    add(Command("esp32.forget", "Forget a declared ESP32 target and its console link.", _forget,
                args=(Arg("name", doc="the target's name"),), requires=WIRING, effect="write", result="name"))
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
                requires=ESP32, effect="write", result="chip, mac, duration_s, output"))
    add(Command("esp32.reset", "Reset a target to run its application, or hold it in its ROM download mode.", _reset,
                args=(Arg("target", doc="the declared target's name"),
                      Arg("mode", doc="run, or bootloader"),
                      Arg("bound_s", type="float", unit="s", doc="ceiling for the esptool run")),
                requires=ESP32, effect="write", result="mode, chip, mac, duration_s, output"))
