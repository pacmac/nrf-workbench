"""programmer.flash: flash a STORED firmware, named by id (docs/specs/firmware.md).

The caller names content (an id), never a path. The flow, in the order that keeps a refusal harmless:

  1. both bounds and the uploader are checked, and the stored image is RE-HASHED against its id
     (a store altered or damaged after upload is refused here, with nothing touched);
  2. the package is checked against THIS board's profile (the SoftDevice) BEFORE any touch. The
     flash step does the same check, but only after the board is already in its bootloader: a package
     for the wrong SoftDevice found there would leave the board stranded. So it is found here;
  3. ONE host lock is taken and held across the two steps (enter, then flash), so no other instance can
     act in the gap between them;
  4. if the board is running its application, `enter_bootloader` runs ONCE, with this very package as
     the unit's declared way back (the thing being flashed is the way back); if it is already in its
     bootloader, there is no touch;
  5. `flash_file` runs once against the stored file, the board must return as the application, and
     when `expect_product` is given it must report that product string.

If the flash does not succeed after the entry, the board is left in its bootloader; the result says so
in its message and in `evidence.entered_bootloader`, and a later call by id flashes it from there
without touching it again. Nothing is retried.
"""
from ..core.result import Result
from ..devices import entry, identity
from ..guards import checks
from ..guards.lock import DEFAULT_PATH, HostLock, LockHeld, LockVoid
from . import flash_file as ff


def _bound_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def flash_firmware(prof, store, ident, serial_no, enter_bound_s, flash_bound_s, units=None, sysfs="/sys",
                   devroot="/dev", proc="/proc", lock_path=DEFAULT_PATH, expect_product=None,
                   runner=ff.default_runner, tool=None, touch=entry.default_touch, watcher=None, ledger=None):
    for label, v in (("enter_bound_s", enter_bound_s), ("flash_bound_s", flash_bound_s)):
        if not _bound_ok(v):
            return Result.refused("no_bound", "%s is required: a caller-supplied bound in seconds, one for "
                                  "each of the two steps" % label, {"missing": label})
    if not serial_no:
        return Result.refused("serial_unverifiable", "no serial was named, so no board can be chosen")
    tool = tool or ff.find_tool()
    if not tool:
        return Result.refused("capability_missing", "adafruit-nrfutil was not found", {"needs": "adafruit-nrfutil"})

    verified = store.verify(ident)
    if not verified.ok:
        return verified
    sha, path, record = verified.data["id"], verified.data["path"], verified.data["record"]
    refusal, _info = checks.dfu_package(prof, path)            # the SoftDevice check, before any touch
    if refusal is not None:
        refusal.evidence = dict(refusal.evidence, firmware_id=sha)
        return refusal

    lock = HostLock(lock_path)
    try:
        lock.acquire("flashing firmware %s" % sha[:8])
    except LockHeld as exc:
        return Result.refused("lock_held", "another instance holds the host lock: %s" % exc.holder,
                              {"holder": exc.holder, "firmware_id": sha})
    except LockVoid as exc:
        return Result.refused("lock_void", str(exc), {"firmware_id": sha})
    try:
        return _run(prof, sha, path, record, serial_no, enter_bound_s, flash_bound_s, units, sysfs, devroot, proc,
                    lock, lock_path, expect_product, runner, tool, touch, watcher, ledger)
    finally:
        lock.release()


def _run(prof, sha, path, record, serial_no, enter_bound_s, flash_bound_s, units, sysfs, devroot, proc, lock,
         lock_path, expect_product, runner, tool, touch, watcher, ledger):
    # This package is the unit's way back; any other declaration the caller made is replaced by it.
    unit = dict((units or {}).get(serial_no) or {})
    unit["return_path"] = {"method": "flash", "package": path}
    units = dict(units or {})
    units[serial_no] = unit

    entered, entry_data = False, None
    if identity.state(prof, serial_no, identity.inventory(sysfs)) == "app":
        e = entry.enter_bootloader(prof, serial_no, enter_bound_s, units=units, sysfs=sysfs, devroot=devroot,
                                   proc=proc, lock_path=lock_path, touch=touch, watcher=watcher, ledger=ledger,
                                   held=lock)
        if not e.ok:
            # A refusal means nothing was touched; a failure means the touch WAS made and the board's
            # state is not known, so it is reported as unknown (None), never as "not entered".
            return _tag(e, sha, False if e.cls == "refused" else None, None)
        entered, entry_data = True, e.data

    res = ff.flash_file(prof, serial_no, path, flash_bound_s, units=units, sysfs=sysfs, devroot=devroot, proc=proc,
                        lock_path=lock_path, runner=runner, tool=tool, watcher=watcher,
                        expect_product=expect_product, held=lock)
    out = _tag(res, sha, entered, entry_data)
    out.data = dict(out.data, firmware={"id": sha, "name": record.get("name"), "bytes": record.get("bytes")})
    if entered and not res.ok:
        out.message = out.message + " The board was sent to its bootloader and is still there."
    return out


def _tag(res, sha, entered, entry_data):
    """The same Result with what this call did added: which firmware, and whether the board was touched."""
    data = dict(res.data)
    if res.ok:
        data["entered_bootloader"] = entered
        data["entry"] = entry_data
    return Result(res.cls, res.code, res.message, data,
                  dict(res.evidence, firmware_id=sha, entered_bootloader=entered))
