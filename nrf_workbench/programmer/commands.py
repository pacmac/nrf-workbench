"""Registers the firmware and flash commands (docs/specs/firmware.md) in the core registry.

Each handler is a plain function `handler(ctx, **args) -> Result` over the store and `flash_firmware`
that are already built and tested, so the same function will serve HTTP, the CLI and the MCP. Nothing
here knows a web framework.

`host` is a small contract, not a class (the concrete one belongs to the composition root):
  host.config      the loaded configuration (core.config shape; paths.firmware is the store directory)
  host.sysfs, host.devroot, host.proc     the trees to read ("/sys", "/dev", "/proc" in production)
  host.lock_path   the host lock file, or None for guards.lock's own default
  host.profiles    the loaded board profiles (a list)
  host.units       per-serial exceptions, chiefly `protected`, and any declared return path
  host.hooks       test seams: touch, runner, tool, watcher, ledger. Empty in production.
  host.service(name, factory)    a cached, lazily built singleton
"""
import os
import re

from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from ..devices import entry, identity
from ..guards.lock import DEFAULT_PATH
from . import flash_file as ff
from . import flash_firmware as ffw
from .firmware_store import Store

_SHA = re.compile(r"^[0-9a-fA-F]{64}$")
STORE = ("firmware_store",)
FLASH = ("firmware_store", "uploader")


def _in_use(host, sha):
    """The units that declare this stored package as their way back from the bootloader."""
    image = os.path.realpath(os.path.join(host.config["paths"]["firmware"], sha + ".zip"))
    users = []
    for serial, unit in sorted((host.units or {}).items()):
        rp = (unit or {}).get("return_path") or {}
        package = rp.get("package")
        if package and os.path.realpath(package) == image:
            users.append(serial)
    return users


def _store(host):
    root = host.config["paths"]["firmware"]
    return host.service("firmware_store", lambda: Store(root, in_use_by=lambda sha: _in_use(host, sha)))


def _body(ctx):
    if ctx.body is None:
        raise UsageError("this command takes the file as the request body")
    return ctx.body


# -- the store ------------------------------------------------------------------------------------
def _put(ctx, name=None, sha256=None):
    body = _body(ctx)
    if sha256 is not None and not _SHA.match(sha256):
        raise UsageError("sha256 must be 64 hex characters", "sha256")
    return _store(ctx.host).put(body, body.length, name=name, sha256=sha256, uploaded_by=ctx.caller)


def _list(ctx):
    return _store(ctx.host).list()


def _show(ctx, id):
    return _store(ctx.host).show(id)


def _delete(ctx, id):
    return _store(ctx.host).delete(id)


# -- flashing -------------------------------------------------------------------------------------
def _profile_for(host, serial):
    inv = identity.inventory(host.sysfs)
    for prof in host.profiles:
        if any(d["serial"] == serial for d in identity.board_devices(prof, inv)):
            return prof
    return None


def _flash(ctx, serial, firmware, enter_bound_s, flash_bound_s, expect_product=None):
    host = ctx.host
    prof = _profile_for(host, serial)
    if prof is None:
        return Result.refused("node_absent", "no attached board has that serial, so no profile applies",
                              {"state": "absent", "named": serial})
    hooks = host.hooks or {}
    return ffw.flash_firmware(
        prof, _store(host), firmware, serial, enter_bound_s, flash_bound_s, units=host.units, sysfs=host.sysfs,
        devroot=host.devroot, proc=host.proc, lock_path=host.lock_path or DEFAULT_PATH,
        expect_product=expect_product, runner=hooks.get("runner", ff.default_runner), tool=hooks.get("tool"),
        touch=hooks.get("touch", entry.default_touch), watcher=hooks.get("watcher"), ledger=hooks.get("ledger"))


def _flash_stream(ctx, serial, enter_bound_s, flash_bound_s, expect_product=None, name=None, sha256=None):
    """Receive and validate the WHOLE file, store it, then flash it from the store: the board is erased
    when the transfer starts, so it is never fed from the socket. The upload stays in the store either
    way, so a refused flash is retried by id with nothing sent twice."""
    put = _put(ctx, name=name, sha256=sha256)
    if not put.ok:
        return put
    upload = {"id": put.data["sha256"], "new": put.data["new"], "bytes": put.data["bytes"]}
    res = _flash(ctx, serial, upload["id"], enter_bound_s, flash_bound_s, expect_product)
    res.data = dict(res.data, upload=upload)
    res.evidence = dict(res.evidence, upload=upload)
    return res


_BOUNDS = (
    Arg("enter_bound_s", type="float", unit="s",
        doc="ceiling for the bootloader handover; derive it from the measured handover time (GET /api/devices)"),
    Arg("flash_bound_s", type="float", unit="s",
        doc="ceiling for the write and the board's return; derive it from the image size and the measured throughput"),
)


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("firmware.put", "Upload a firmware package into the store; the file is the request body.", _put,
                args=(Arg("name", required=False, doc="a label for the entry; data only, never a path"),
                      Arg("sha256", required=False, doc="the SHA-256 of the file; a mismatch refuses the upload")),
                requires=STORE, effect="write", body="raw",
                result="the record: sha256 (the id), bytes, name, image_bytes, softdevice_req, packets, new"))
    add(Command("firmware.list", "List the stored firmware, newest first.", _list, requires=STORE,
                result="items: the records"))
    add(Command("firmware.show", "Show one stored firmware and which units name it as their way back.", _show,
                args=(Arg("id", doc="the SHA-256, or a unique prefix of at least 8 hex characters"),),
                requires=STORE, result="the record plus used_by"))
    add(Command("firmware.delete", "Delete a stored firmware, unless a unit declares it as its way back.", _delete,
                args=(Arg("id", doc="the SHA-256, or a unique prefix of at least 8 hex characters"),),
                requires=STORE, effect="write", result="the id deleted"))
    add(Command("programmer.flash", "Flash a stored firmware to a board, entering its bootloader if needed.", _flash,
                args=(Arg("serial", doc="the USB serial of the board to flash"),
                      Arg("firmware", doc="the id of the stored firmware")) + _BOUNDS +
                (Arg("expect_product", required=False,
                     doc="the USB product string the image announces; the returned board must report it"),),
                requires=FLASH, effect="write",
                result="duration_s, packets, marks, entered_bootloader, firmware, app_after"))
    add(Command("programmer.flash_stream", "Upload a firmware package and flash it in one call; the file is the "
                "request body.", _flash_stream,
                args=(Arg("serial", doc="the USB serial of the board to flash"),) + _BOUNDS +
                (Arg("expect_product", required=False, doc="the USB product string the image announces"),
                 Arg("name", required=False, doc="a label for the stored entry"),
                 Arg("sha256", required=False, doc="the SHA-256 of the file")),
                requires=FLASH, effect="write", body="raw",
                result="as programmer.flash, plus upload: id, new, bytes"))
