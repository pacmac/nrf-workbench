"""flash_file(): write a prebuilt DFU zip to a board ALREADY in its Adafruit bootloader.

It never requests the bootloader (that is devices.entry.enter_bootloader) and it never opens a
port itself: the node is found by IDENTITY and handed to adafruit-nrfutil, the documented uploader.

Guards, all before anything is written (docs/specs/programmer.md): the host lock; a valid DFU
package for the right SoftDevice; the board is in the bootloader (never the app), carries the named
serial, is not protected, has a node that joins by device number, and nothing else holds it.

ONE attempt. The write is not judged by exit status alone: it must exit 0, print `Device
programmed.`, and print exactly one progress mark per 512-byte packet of the image, so
`ceil(image_bytes / 512)` of them (the packet size is the uploader's own DFU_PACKET_MAX_SIZE). That
is a content check, not a timing one: a first draft judged the duration against the wire time at
the profile's baud, and the first real flash (native USB, where the baud limits nothing) took
21.554 s, completed, and was wrongly called suspect. Duration is now RECORDED, not judged. Finally
the board must return as the application, on events, within the caller's bound. The WHOLE tool
output is returned as evidence, never a tail.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

from ..core.result import SUSPECT, Result
from ..devices import identity
from ..devices import profile as profilemod
from ..devices.events import UeventWatcher
from ..guards import checks
from ..guards.lock import DEFAULT_PATH, HostLock, LockHeld, LockVoid

PROGRAMMED = "Device programmed."


def find_tool():
    """adafruit-nrfutil next to this interpreter (the venv), else on PATH, else None."""
    local = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "adafruit-nrfutil")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    return shutil.which("adafruit-nrfutil")


def default_runner(cmd, timeout_s):
    """Run the tool once. Returns (returncode, WHOLE output, seconds, timed_out)."""
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           timeout=timeout_s)
        return p.returncode, p.stdout or "", time.monotonic() - t0, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return -1, out, time.monotonic() - t0, True


# adafruit-nrfutil sends the image in packets of its DFU_PACKET_MAX_SIZE (read in
# nordicsemi/dfu/dfu_transport_serial.py) and prints one '#' per packet.
PACKET_BYTES = 512


def expected_packets(image_bytes):
    """How many progress marks a COMPLETE transfer of this image prints."""
    return -(-image_bytes // PACKET_BYTES)


def _brief(dev):
    return {"id": dev["id"], "serial": dev["serial"], "product": dev["product"], "sysfs": dev["name"]}


def flash_file(prof, serial_no, package, bound_s, units=None, sysfs="/sys", devroot="/dev", proc="/proc",
               lock_path=DEFAULT_PATH, runner=default_runner, tool=None, watcher=None, expect_product=None,
               held=None):
    """`held` is a HostLock the CALLER already holds (programmer.flash holds one across enter and
    flash); it is neither taken nor released here."""
    if not isinstance(bound_s, (int, float)) or isinstance(bound_s, bool) or bound_s <= 0:
        return Result.refused("no_bound", "a flash needs a caller-supplied bound in seconds")
    if not serial_no:
        return Result.refused("serial_unverifiable", "no serial was named, so no board can be chosen")
    tool = tool or find_tool()
    if not tool:
        return Result.refused("capability_missing", "adafruit-nrfutil was not found", {"needs": "adafruit-nrfutil"})
    refusal, info = checks.dfu_package(prof, package)
    if refusal is not None:
        return refusal

    lock = held
    if lock is None:
        lock = HostLock(lock_path)
        try:
            lock.acquire("flashing a package")
        except LockHeld as exc:
            return Result.refused("lock_held", "another instance holds the host lock: %s" % exc.holder,
                                  {"holder": exc.holder})
        except LockVoid as exc:
            return Result.refused("lock_void", str(exc))
    try:
        return _flash(prof, serial_no, package, info, bound_s, units, sysfs, devroot, proc, runner, tool, watcher,
                      expect_product)
    finally:
        if held is None:
            lock.release()


def _flash(prof, serial_no, package, info, bound_s, units, sysfs, devroot, proc, runner, tool, watcher,
           expect_product):
    inv = identity.inventory(sysfs)
    st = identity.state(prof, serial_no, inv)
    mine = [d for d in identity.board_devices(prof, inv) if d["serial"] == serial_no]
    if st == "absent":
        return Result.refused("node_absent", "no board with that serial is attached (no sysfs entry)",
                              {"state": "absent", "named": serial_no})
    if st == "two":
        return Result.refused("node_two", "the board shows as both app and bootloader at once",
                              {"state": "two", "devices": [_brief(d) for d in mine]})
    if st == "app":
        return Result.refused("not_in_bootloader",
                              "the board is running its application; this never requests the bootloader",
                              {"state": "app", "device": _brief(mine[0])})
    dev = [d for d in mine if d["mode"] == "bootloader"][0]
    for refusal in (checks.serial_matches(serial_no, dev), checks.not_protected(units, serial_no)):
        if refusal is not None:
            return refusal
    node, why = identity.node_for(dev, devroot)
    if node is None:
        return Result.refused("node_absent", "the board is in its bootloader but there is no node to open",
                              {"state": "attached_no_node", "reason": why, "device": _brief(dev)})
    held = checks.port_held(node, proc)
    if held is not None:
        return held

    rate = int(prof["bootloader"].get("baud", 115200))
    packets = expected_packets(info["image_bytes"])
    cmd = [tool, "dfu", "serial", "-pkg", package, "-p", node, "-b", str(rate), "--singlebank"]
    facts = {"image_bytes": info["image_bytes"], "expected_packets": packets, "rate": rate}

    def app_is_back():
        return identity.state(prof, serial_no, identity.inventory(sysfs)) == "app"

    watcher = watcher if watcher is not None else UeventWatcher()
    watcher.open()                    # before the write, so the return is never missed
    try:
        rc, out, secs, timed_out = runner(cmd, bound_s)      # exactly one attempt
        facts.update({"duration_s": round(secs, 3), "output": out, "command": cmd})
        if timed_out:
            return Result.failed("flash_failed", "the upload did not finish within the bound and was stopped",
                                 dict(facts, bound_s=bound_s))
        if rc != 0 or PROGRAMMED not in out:
            return Result.failed("flash_failed", "the upload did not report success (exit %s); not retried" % rc, facts)
        marks = out.count("#")
        facts["marks"] = marks
        if marks != packets:
            return Result(SUSPECT, "flash_suspect",
                          "the upload reported success but printed %d progress marks where a complete image of "
                          "%d bytes prints %d; do not trust it" % (marks, info["image_bytes"], packets), None, facts)
        res = watcher.wait(app_is_back, max(bound_s - secs, 0.001))
    finally:
        watcher.close()

    if not res.reached:
        return Result.failed("run_node_not_returned",
                             "the image was written but the application did not return within the bound",
                             dict(facts, bound_s=bound_s, event_source=res.source, note=res.note))
    after = [d for d in identity.board_devices(prof, identity.inventory(sysfs))
             if d["mode"] == "app" and d["serial"] == serial_no][0]
    # A device-side check of WHICH image is running, independent of timing: when the caller knows the
    # product string the image announces (the parking image does), the returned device must report it.
    if expect_product is not None and after["product"] != expect_product:
        return Result(SUSPECT, "flash_suspect",
                      "the application that returned reports a different product than the image that was written",
                      None, dict(facts, expected_product=expect_product, reported_product=after["product"],
                                 app_after=_brief(after)))
    return Result.success({"duration_s": facts["duration_s"], "image_bytes": facts["image_bytes"],
                           "throughput_bytes_per_s": round(facts["image_bytes"] / max(facts["duration_s"], 1e-9), 1),
                           "packets": packets, "marks": marks, "rate": rate, "output": out,
                           "app_after": _brief(after), "event_source": res.source},
                          "written, judged good, and the board is back running its application")


def main(argv=None):
    p = argparse.ArgumentParser(description="Write a prebuilt DFU zip to a board already in its bootloader.")
    p.add_argument("--profile", required=True, help="the board profile (profiles/*.yaml)")
    p.add_argument("--serial", required=True, help="the serial of the board to flash")
    p.add_argument("--package", required=True, help="the DFU zip")
    p.add_argument("--bound-s", required=True, type=float, dest="bound_s",
                   help="REQUIRED ceiling in seconds for the write and the return; derive it (about 2x the wire time)")
    p.add_argument("--protected", action="store_true", help="treat this unit as protected (it will refuse)")
    p.add_argument("--lock", default=DEFAULT_PATH, help="the host lock file")
    p.add_argument("--expect-product", dest="expect_product",
                   help="the USB product string the written image announces; the returned device must report it")
    a = p.parse_args(argv)
    prof = profilemod.load(a.profile)
    result = flash_file(prof, a.serial, a.package, a.bound_s, units={a.serial: {"protected": a.protected}},
                        lock_path=a.lock, expect_product=a.expect_product)
    print(result.to_json())
    return result.exit_code()


if __name__ == "__main__":
    sys.exit(main())
