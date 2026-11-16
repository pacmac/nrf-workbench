"""entry.py - enter_bootloader(): put an nRF board into its bootloader by SOFTWARE, with no human.

    RAK_PORT_OK=1

AUTHORISED BY PETER, 2026-11-16, answering a presented choice ("yes authorised"), for THIS FILE
ONLY. It is the one place in this project allowed to open a board's own USB CDC at the touch baud
(the Adafruit bootloader touch, `use_1200bps_touch`). It is not authorised anywhere else, and a
test fails the build if any other module does it. The marker above is how the port-guard hook
recognises a file Peter has accepted, exactly as `rak-dongle.py` carries its own (2026-08-12); it is
never placed on a command and never set in an environment.

WHY IT IS ACCEPTABLE HERE, and what keeps it from being the 2026-07-27 accident (a RAK parked in
DFU for seven hours, an overnight measurement lost):

  * It runs only after EVERY guard passes, and a refusal opens nothing: the host-wide lock; the
    board is attached, in the app mode, with a node that joins to its sysfs entry by device number;
    the serial the caller named is the serial attached; the unit is not protected; **the unit has
    declared a way BACK from the bootloader** (`no_return_path` otherwise), because with USB only a
    stranded board has no software way out except a flash; nothing else holds the port.
  * EXACTLY ONE touch per call, and it is never retried. A touch that does not reach the bootloader
    is reported as `bootloader_not_reached`; a retry is another reset (the prior art's rule).
  * The node is found by IDENTITY. No port path is an input, so no command that runs this contains
    one (`block-cdc.py`: "every path lies").
  * The wait for the handover is on EVENTS with the caller's bound; there is no sleep and no poll.
  * It does not perform the return. The return is the programmer's flash, which keeps one actor per
    transition (docs/specs/devices.md).

It is stdlib plus pyserial (imported only at the moment of the touch).
"""
import argparse
import sys
import time

from ..core.result import Result
from ..guards import checks
from ..guards.ledger import TouchLedger
from ..guards.lock import DEFAULT_PATH, HostLock, LockHeld, LockVoid
from . import identity
from . import profile as profilemod
from .events import UeventWatcher

TOUCH_BAUD = 1200


def default_touch(node):
    """Open the board's own CDC at the touch baud and close it. Called at most once per call."""
    import serial
    port = serial.Serial(node, TOUCH_BAUD)
    port.close()


def _brief(dev):
    return {"id": dev["id"], "serial": dev["serial"], "product": dev["product"], "sysfs": dev["name"]}


def enter_bootloader(prof, serial_no, bound_s, units=None, unit=None, sysfs="/sys", devroot="/dev",
                     proc="/proc", lock_path=DEFAULT_PATH, touch=default_touch, watcher=None, ledger=None,
                     held=None):
    """Returns a Result. `touch` and `watcher` are injectable so the guards can be proven without
    hardware; the real defaults are the touch above and the kernel uevent socket.

    `held` is a HostLock the CALLER already holds. It is neither taken nor released here, so a caller
    that runs several steps (programmer.flash: enter, then flash) holds ONE lock across all of them
    instead of releasing it between the steps."""
    if prof["bootloader"].get("entry") != "1200-baud-touch":
        return Result.refused("capability_missing", "this profile's entry method is not implemented here",
                              {"entry": prof["bootloader"].get("entry")})
    if not isinstance(bound_s, (int, float)) or isinstance(bound_s, bool) or bound_s <= 0:
        return Result.refused("no_bound", "the wait for the handover needs a caller-supplied bound in seconds")
    if not serial_no:
        return Result.refused("serial_unverifiable", "no serial was named, so no board can be chosen")

    lock = held
    if lock is None:
        lock = HostLock(lock_path)
        try:
            lock.acquire("entering the bootloader")
        except LockHeld as exc:
            return Result.refused("lock_held", "another instance holds the host lock: %s" % exc.holder,
                                  {"holder": exc.holder})
        except LockVoid as exc:
            return Result.refused("lock_void", str(exc))
    try:
        ledger = ledger if ledger is not None else TouchLedger(lock_path + ".touches.json")
        return _enter(prof, serial_no, bound_s, units, unit, sysfs, devroot, proc, touch, watcher, ledger)
    finally:
        if held is None:
            lock.release()


def _enter(prof, serial_no, bound_s, units, unit, sysfs, devroot, proc, touch, watcher, ledger):
    inv = identity.inventory(sysfs)
    st = identity.state(prof, serial_no, inv)
    mine = [d for d in identity.board_devices(prof, inv) if d["serial"] == serial_no]
    if st == "absent":
        return Result.refused("node_absent", "no board with that serial is attached (no sysfs entry)",
                              {"state": "absent", "named": serial_no})
    if st == "two":
        return Result.refused("node_two", "the board shows as both app and bootloader at once",
                              {"state": "two", "devices": [_brief(d) for d in mine]})
    if st == "bootloader":
        return Result.success({"already_in_bootloader": True, "device": _brief(mine[0])},
                              "the board is already in its bootloader; nothing was touched")

    dev = [d for d in mine if d["mode"] == "app"][0]
    unit = unit if unit is not None else ((units or {}).get(serial_no) or {})
    for refusal in (checks.serial_matches(serial_no, dev),
                    checks.not_protected(units, serial_no),
                    checks.return_path_declared(unit)):
        if refusal is not None:
            return refusal

    # Never touch the same enumeration twice (docs/specs/devices.md, "never touching it twice").
    pending = ledger.blocks(serial_no, dev["enum"])
    if pending is not None:
        return pending

    node, why = identity.node_for(dev, devroot)
    if node is None:
        return Result.refused("node_absent", "the board is attached but there is no node to open",
                              {"state": "attached_no_node", "reason": why, "device": _brief(dev)})
    held = checks.port_held(node, proc)
    if held is not None:
        return held

    before_boot = set((d["name"], d["serial"]) for d in identity.board_devices(prof, inv)
                      if d["mode"] == "bootloader")
    found = {}

    def reached():
        for d in identity.board_devices(prof, identity.inventory(sysfs)):
            if d["mode"] == "bootloader" and (d["name"], d["serial"]) not in before_boot:
                found["dev"] = d
                return True
        return False

    watcher = watcher if watcher is not None else UeventWatcher()
    watcher.open()                    # open the event source BEFORE the touch, so no event is missed
    try:
        # Write-ahead: the ledger records the touch BEFORE it is made, so a crash cannot lose it.
        try:
            ledger.record(serial_no, dev["enum"], "touching")
        except OSError as exc:
            return Result.refused("touch_pending", "the touch ledger cannot be written, so no touch is made",
                                  {"reason": str(exc)})
        t0 = time.monotonic()
        try:
            touch(node)               # exactly one touch. Never retried.
        except Exception as exc:      # the touch itself failed: nothing is known to have changed
            ledger.record(serial_no, dev["enum"], "touch_failed")
            return Result.failed("touch_failed", "the touch could not be performed: %r" % (exc,),
                                 {"device": _brief(dev)})
        res = watcher.wait(reached, bound_s)
    finally:
        watcher.close()

    ledger.record(serial_no, dev["enum"], "ok" if res.reached else "bootloader_not_reached")
    if not res.reached:
        return Result.failed("bootloader_not_reached",
                             "the touch was made once, and no bootloader appeared within the bound",
                             {"bound_s": bound_s, "elapsed_s": round(res.elapsed_s, 3),
                              "event_source": res.source, "note": res.note, "device": _brief(dev)})
    after = found["dev"]
    after_node, _why = identity.node_for(after, devroot)
    return Result.success({
        "before": _brief(dev), "after": _brief(after),
        "serial_same": (after["serial"] == serial_no) if after["serial"] else None,
        "handover_s": round(time.monotonic() - t0, 3), "event_source": res.source,
        "bootloader_node_resolved": after_node is not None,
    }, "the board is in its bootloader; to leave it, use the unit's declared return path")


def main(argv=None):
    p = argparse.ArgumentParser(description="Put an nRF board into its bootloader by software.")
    p.add_argument("--profile", required=True, help="the board profile (profiles/*.yaml)")
    p.add_argument("--serial", required=True, help="the serial of the board to act on")
    p.add_argument("--bound-s", required=True, type=float, dest="bound_s",
                   help="REQUIRED ceiling in seconds for the handover; derive it from a measurement")
    p.add_argument("--return-package", dest="return_package",
                   help="the DFU zip that brings this unit back (its declared return path); required")
    p.add_argument("--protected", action="store_true", help="treat this unit as protected (it will refuse)")
    p.add_argument("--lock", default=DEFAULT_PATH, help="the host lock file")
    p.add_argument("--forget-touch", action="store_true", dest="forget_touch",
                   help="clear the touch ledger for this serial and exit WITHOUT touching; only after "
                        "you have confirmed by other means that the board is not mid-transition")
    a = p.parse_args(argv)
    if a.forget_touch:
        existed = TouchLedger(a.lock + ".touches.json").forget(a.serial)
        print(Result.success({"forgotten": a.serial, "had_a_record": existed}, "nothing was touched").to_json())
        return 0
    if not a.return_package:
        p.error("--return-package is required: a unit must declare how it comes back")
    prof = profilemod.load(a.profile)
    units = {a.serial: {"protected": a.protected,
                        "return_path": {"method": "flash", "package": a.return_package}}}
    result = enter_bootloader(prof, a.serial, a.bound_s, units=units, lock_path=a.lock)
    print(result.to_json())
    return result.exit_code()


if __name__ == "__main__":
    sys.exit(main())
