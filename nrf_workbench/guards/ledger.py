"""The touch ledger: a board's current USB enumeration is touched at most once.

Entering the bootloader is a reset. A SECOND reset in quick succession is the double-reset
detector (brief rule 2), so a board must never be touched twice for one transition, whether the
second call comes from a retry after a timeout, a racing caller, or an agent that did not read the
first answer. The ledger records, BEFORE the touch is made (write-ahead, so a crash cannot lose it),
the board's serial and its USB enumeration (`busnum:devnum`, which changes on every
re-enumeration). While the board is still on that enumeration, another touch is refused as
`touch_pending`. Once it has re-enumerated, the first transition resolved and it is allowed again.

Fail closed: an enumeration that cannot be read, or a ledger file that exists but cannot be read,
blocks; it never silently allows. Clearing a record is an explicit human decision (`forget`).
"""
import json
import os
import tempfile
import time

from ..core.result import Result


class LedgerError(Exception):
    pass


class TouchLedger(object):
    def __init__(self, path):
        self.path = path

    def _load(self):
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise LedgerError("the touch ledger %s exists but cannot be read: %s" % (self.path, exc))
        if not isinstance(data, dict):
            raise LedgerError("the touch ledger %s is not a mapping" % self.path)
        return data

    def _save(self, data):
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent or ".", prefix=".touches-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)          # atomic: a reader sees the old file or the new one
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def last(self, serial):
        return self._load().get(serial)

    def record(self, serial, enum, outcome):
        data = self._load()
        data[serial] = {"enum_touched": enum, "outcome": outcome, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self._save(data)

    def forget(self, serial):
        data = self._load()
        existed = data.pop(serial, None) is not None
        self._save(data)
        return existed

    def blocks(self, serial, current_enum):
        """None if a touch may proceed, else a refused Result."""
        try:
            last = self.last(serial)
        except LedgerError as exc:
            return Result.refused("touch_pending", "the touch ledger cannot be trusted, so no touch is made",
                                  {"reason": str(exc)})
        if last is None:
            return None
        if current_enum is None:
            return Result.refused("touch_pending",
                                  "this board was touched before and its USB enumeration cannot be read, "
                                  "so a re-enumeration cannot be proven",
                                  {"serial": serial, "earlier": last})
        if current_enum == last.get("enum_touched"):
            return Result.refused("touch_pending",
                                  "this board's current enumeration was already touched once and it has not "
                                  "re-enumerated since; a second touch would be a second reset in quick succession",
                                  {"serial": serial, "enumeration": current_enum, "earlier": last})
        return None
