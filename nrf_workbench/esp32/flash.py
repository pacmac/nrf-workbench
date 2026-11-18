"""esp32.flash and esp32.identify: write a STORED ESP32 package to a declared target (docs/specs/esp32.md, "Flash").

The same shape as `programmer.flash_firmware`, in the same order, so a refusal is harmless:

  1. the bound is checked, `esptool` is found, and the stored image is RE-HASHED against its id;
  2. the package must be an ESP kind (an nRF DFU package is refused here, as an ESP one is refused there);
  3. ONE host lock is taken;
  4. the target must resolve to exactly one attached device, not protected, with a node that joins by device
     number and that nobody holds;
  5. ONE attempt of esptool, the whole output returned. The verdict is not the exit status alone: esptool prints
     `Hash of data verified.` once per written part and the count must equal the number of parts.

No bootloader request and no return path: an ESP32 leaves download mode by the same reset esptool ends with.
"""
import os
import re
import shutil
import sys
import tempfile
import zipfile

from ..core.result import SUSPECT, Result
from ..devices import identity
from ..guards import checks
from ..guards.lock import DEFAULT_PATH, HostLock, LockHeld, LockVoid
from ..programmer import flash_file as ff
from . import package, targets

VERIFIED = "Hash of data verified."
BAUDS = (115200, 230400, 460800, 921600)     # esptool's own documented range; 115200 is the ROM's default
DEFAULT_BAUD = 115200
_CHIP = re.compile(r"^Chip type:\s+(.+?)\s*$", re.M)
_MAC = re.compile(r"^MAC:\s+([0-9a-fA-F:]{17})\s*$", re.M)


def find_tool():
    """esptool next to this interpreter (the venv), else on PATH, else None."""
    local = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "esptool")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    return shutil.which("esptool")


def unit_key(rec):
    """The key in the per-unit exceptions file: the serial, or `slot:<port>` for a slot-keyed target."""
    return rec["key"] if rec["key_kind"] == "serial" else "slot:" + rec["key"]


def _facts(out):
    chip, mac = _CHIP.search(out), _MAC.search(out)
    return {"chip": chip.group(1) if chip else None, "mac": mac.group(1).lower() if mac else None}


def _bound_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _device(rec, units, sysfs, devroot, proc):
    """(device, node, refusal): the one attached device the target names, or why none can be chosen."""
    inv = identity.inventory(sysfs)
    found = targets.matching(inv, rec["key_kind"], rec["key"])
    if not found:
        return None, None, Result.refused("node_absent", "the target's adapter is not attached",
                                          {"state": "absent", "target": rec["name"]})
    if len(found) > 1:
        return None, None, Result.refused("serial_unverifiable", "%d attached devices match target %s, so none can be "
                                          "chosen" % (len(found), rec["name"]),
                                          {"slots": sorted(d["name"] for d in found)})
    dev = found[0]
    refusal = checks.not_protected(units, unit_key(rec))
    if refusal is not None:
        return None, None, refusal
    node, why = identity.node_for(dev, devroot)
    if node is None:
        return None, None, Result.refused("node_absent", "the adapter is attached but there is no node to open",
                                          {"state": "attached_no_node", "reason": why, "target": rec["name"]})
    held = checks.port_held(node, proc)
    if held is not None:
        return None, None, held
    return dev, node, None


def _locked(lock_path, why, work):
    lock = HostLock(lock_path)
    try:
        lock.acquire(why)
    except LockHeld as exc:
        return Result.refused("lock_held", "another instance holds the host lock: %s" % exc.holder,
                              {"holder": exc.holder})
    except LockVoid as exc:
        return Result.refused("lock_void", str(exc))
    try:
        return work()
    finally:
        lock.release()


def flash_target(rec, store, ident, flash_bound_s, baud=DEFAULT_BAUD, units=None, sysfs="/sys", devroot="/dev",
                 proc="/proc", lock_path=DEFAULT_PATH, runner=ff.default_runner, tool=None):
    if not _bound_ok(flash_bound_s):
        return Result.refused("no_bound", "flash_bound_s is required: a caller-supplied bound in seconds",
                              {"missing": "flash_bound_s"})
    if baud not in BAUDS:
        return Result.refused("baud_refused", "esptool is run at one of %s" % ", ".join(str(b) for b in BAUDS),
                              {"baud": baud})
    tool = tool or find_tool()
    if not tool:
        return Result.refused("capability_missing", "esptool was not found", {"needs": "esptool"})
    verified = store.verify(ident)
    if not verified.ok:
        return verified
    sha, path = verified.data["id"], verified.data["path"]
    try:
        info = package.inspect(path)
    except package.Bad:
        return Result.refused("bad_package", "this stored firmware is not an ESP32 package (it is an nRF DFU package)",
                              {"firmware_id": sha})
    return _locked(lock_path, "flashing ESP32 firmware %s" % sha[:8],
                   lambda: _run(rec, sha, path, info, flash_bound_s, baud, units, sysfs, devroot, proc, runner, tool,
                                os.path.dirname(os.path.dirname(path))))


def _run(rec, sha, path, info, bound_s, baud, units, sysfs, devroot, proc, runner, tool, store_root):
    dev, node, refusal = _device(rec, units, sysfs, devroot, proc)
    if refusal is not None:
        return refusal
    work = tempfile.mkdtemp(prefix=".esp-flash-", dir=store_root)   # a dot-name is never a project
    try:
        if info["kind"] == package.KIND_IMAGE:
            files = [(0, path)]
        else:
            with zipfile.ZipFile(path) as z:
                files = [(p["offset"], z.extract(p["name"], work)) for p in info["parts"]]
        cmd = [tool, "--port", node, "--baud", str(baud)]
        if info["chip"]:
            cmd += ["--chip", info["chip"]]
        cmd += ["--before", "default-reset", "--after", "hard-reset", "write-flash", "--flash-mode", "keep"]
        for offset, file in files:
            cmd += ["0x%x" % offset, file]
        rc, out, secs, timed_out = runner(cmd, bound_s)           # exactly one attempt
    finally:
        shutil.rmtree(work, ignore_errors=True)
    facts = dict(_facts(out), image_bytes=info["image_bytes"], parts=len(files), baud=baud, node=node,
                 duration_s=round(secs, 3), output=out, command=cmd, target=rec["name"], firmware_id=sha)
    if timed_out:
        return Result.failed("flash_failed", "esptool did not finish within the bound and was stopped",
                             dict(facts, bound_s=bound_s))
    if rc != 0:
        return Result.failed("flash_failed", "esptool did not report success (exit %s); not retried" % rc, facts)
    done = out.count(VERIFIED)
    facts["verified"] = done
    if done != len(files):
        return Result(SUSPECT, "flash_suspect",
                      "esptool exited 0 but verified %d of %d written parts; do not trust it" % (done, len(files)),
                      None, facts)
    return Result.success({"duration_s": facts["duration_s"], "image_bytes": info["image_bytes"],
                           "throughput_bytes_per_s": round(info["image_bytes"] / max(secs, 1e-9), 1),
                           "parts": len(files), "verified": done, "baud": baud, "chip": facts["chip"],
                           "mac": facts["mac"], "output": out},
                          "written, every part verified by esptool, and the board was reset")


def identify_target(rec, bound_s, units=None, sysfs="/sys", devroot="/dev", proc="/proc", lock_path=DEFAULT_PATH,
                    runner=ff.default_runner, tool=None):
    """Ask the chip what it is (`esptool chip-id`). This TOGGLES DTR/RTS to reset the board into its ROM, so it is only
    ever run when someone asks, never by a listing."""
    if not _bound_ok(bound_s):
        return Result.refused("no_bound", "bound_s is required: a caller-supplied bound in seconds",
                              {"missing": "bound_s"})
    tool = tool or find_tool()
    if not tool:
        return Result.refused("capability_missing", "esptool was not found", {"needs": "esptool"})

    def work():
        dev, node, refusal = _device(rec, units, sysfs, devroot, proc)
        if refusal is not None:
            return refusal
        cmd = [tool, "--port", node, "--after", "hard-reset", "chip-id"]
        rc, out, secs, timed_out = runner(cmd, bound_s)
        facts = dict(_facts(out), duration_s=round(secs, 3), output=out, command=cmd, target=rec["name"])
        if timed_out:
            return Result.failed("flash_failed", "esptool did not finish within the bound and was stopped",
                                 dict(facts, bound_s=bound_s))
        if rc != 0 or not facts["chip"]:
            return Result.failed("flash_failed", "esptool could not read the chip (exit %s): is an ESP32 behind this "
                                 "adapter, and is it wired to reset?" % rc, facts)
        return Result.success({"chip": facts["chip"], "mac": facts["mac"], "duration_s": facts["duration_s"],
                               "output": out}, "the chip answered")

    return _locked(lock_path, "reading the chip behind ESP32 target %s" % rec["name"], work)
