"""The firmware store (docs/specs/firmware.md): where a built image lives between the build and the flash.

A directory on the host. An entry is `<sha256>.zip` (exactly the bytes received) and `<sha256>.json`
(the record). The id is the SHA-256 of the file, so an id cannot come to mean different bytes, an
upload is idempotent, and no client-supplied string is ever part of a filesystem path: `name` is data
in the record and the stored name is the hash.

`put` is the careful part. The body is read to exactly the declared length (no more), into a temporary
file inside the store, hashing as it arrives, and **nothing becomes visible until the whole file has
arrived and passed** its checks. A short body, a dropped connection, a wrong checksum, a bad package or
a crash leaves no entry. A stale temporary is told from one still being written by a lock the writer
holds, not by a guessed age.

An entry exists only if it has a record: a file copied in by hand is not listed and never flashed.
Expected outcomes are Results; an unexpected operating-system error propagates (the service turns it
into a failure), and a bad argument is a ValueError or TypeError, which is a usage error, not a Result.
"""
import errno
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time

from ..core.result import Result
from ..guards import checks
from .flash_file import expected_packets

MIN_PREFIX = 8                      # spec: "any unique prefix of at least 8 hex characters"
CHUNK = io.DEFAULT_BUFFER_SIZE      # the standard library's own buffer size, not a limit on the file
TEMP_PREFIX = ".incoming-"
_HEX = re.compile(r"^[0-9a-f]+$")


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class Store(object):
    def __init__(self, root, free_bytes=None, in_use_by=None, now=time.time):
        """`free_bytes()` is the free space on the store's filesystem (the OS's answer by default);
        `in_use_by(sha256)` names the units that declare this package as their way back, which the
        store cannot know itself."""
        self.root = os.path.abspath(root)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self._free = free_bytes or (lambda: shutil.disk_usage(self.root).free)
        self._in_use = in_use_by or (lambda sha: [])
        self._now = now
        self.clean_stale()

    # -- paths: the only names the store ever builds are hashes -------------------------------------
    def image_path(self, sha):
        return os.path.join(self.root, sha + ".zip")

    def _record_path(self, sha):
        return os.path.join(self.root, sha + ".json")

    # -- stale temporaries --------------------------------------------------------------------------
    def clean_stale(self):
        """Remove temporaries nobody is writing. A writer holds an exclusive lock on its temporary for
        as long as it is writing it, so a temporary whose lock can be taken is abandoned."""
        for name in os.listdir(self.root):
            if not name.startswith(TEMP_PREFIX):
                continue
            path = os.path.join(self.root, name)
            try:
                fd = os.open(path, os.O_RDWR)
            except OSError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    continue                    # still being written
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
            finally:
                os.close(fd)

    # -- records ------------------------------------------------------------------------------------
    def _read_record(self, sha):
        try:
            with open(self._record_path(sha), "r") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(rec, dict) or rec.get("sha256") != sha:
            return None
        return rec

    def _write_record(self, sha, rec):
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=TEMP_PREFIX, suffix=".json.part")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._record_path(sha))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _fsync_dir(self):
        try:
            dfd = os.open(self.root, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dfd)
        except OSError:
            pass
        finally:
            os.close(dfd)

    def _ids(self):
        return sorted(n[:-5] for n in os.listdir(self.root) if n.endswith(".json") and not n.startswith("."))

    # -- put ----------------------------------------------------------------------------------------
    def put(self, body, content_length, name=None, sha256=None, uploaded_by=None):
        """Store the `content_length` bytes read from `body`. Returns a Result; on ok, data is the
        record plus `new` (False when the same bytes were already stored)."""
        if name is not None and not isinstance(name, str):
            raise TypeError("name must be a string")
        if uploaded_by is not None and not isinstance(uploaded_by, str):
            raise TypeError("uploaded_by must be a string")
        if sha256 is not None:
            if not isinstance(sha256, str) or len(sha256) != 64 or not _HEX.match(sha256.lower()):
                raise ValueError("sha256 must be 64 hex characters")
            sha256 = sha256.lower()
        if not isinstance(content_length, int) or isinstance(content_length, bool) or content_length < 0:
            return Result.refused("length_required",
                                  "the upload has no usable Content-Length, so its size and the free space "
                                  "cannot be checked before the first byte", {"content_length": content_length})
        free = self._free()
        if content_length > free:
            return Result.refused("store_full", "the filesystem cannot hold this upload; nothing was evicted",
                                  {"needed": content_length, "free": free})

        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=TEMP_PREFIX, suffix=".part")
        keep = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)              # tells clean_stale this one is live
            keep = os.dup(fd)                           # same lock; held until the move, past the write handle
            h = hashlib.sha256()
            got = 0
            with os.fdopen(fd, "wb") as f:
                while got < content_length:
                    try:
                        chunk = body.read(min(CHUNK, content_length - got))
                    except OSError as exc:
                        return Result.refused("bad_package",
                                              "the body failed after %d of %d bytes: %s" % (got, content_length, exc),
                                              {"received": got, "expected": content_length})
                    if not chunk:
                        break
                    try:
                        f.write(chunk)
                    except OSError as exc:
                        if exc.errno == errno.ENOSPC:
                            return Result.refused("store_full", "the filesystem filled while receiving",
                                                  {"needed": content_length, "received": got})
                        raise
                    h.update(chunk)
                    got += len(chunk)
                if got != content_length:
                    return Result.refused("bad_package",
                                          "the body ended after %d of %d bytes" % (got, content_length),
                                          {"received": got, "expected": content_length})
                f.flush()
                os.fsync(f.fileno())

            digest = h.hexdigest()
            if sha256 is not None and sha256 != digest:
                return Result.refused("checksum_mismatch", "the SHA-256 sent is not that of the bytes received",
                                      {"sent": sha256, "received": digest})
            refusal, info = checks.dfu_package({}, tmp)         # shape only: the SoftDevice is checked at flash
            if refusal is not None:
                refusal.evidence = dict(refusal.evidence, package="the uploaded file")
                return refusal
            existing = self._read_record(digest)
            if existing is not None:
                return Result.success(dict(existing, new=False), "already stored")
            os.replace(tmp, self.image_path(digest))            # atomic: the same filesystem
            record = {"sha256": digest, "bytes": got, "name": name, "uploaded_at": _iso(self._now()),
                      "uploaded_by": uploaded_by, "image_bytes": info["image_bytes"],
                      "softdevice_req": info["softdevice_req"], "packets": expected_packets(info["image_bytes"])}
            self._write_record(digest, record)
            self._fsync_dir()
            return Result.success(dict(record, new=True), "stored")
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            if keep is not None:
                os.close(keep)

    # -- reading ------------------------------------------------------------------------------------
    def resolve(self, ident):
        """(sha256, None) or (None, a refusal). An id is at least 8 lowercase hex characters and must
        name exactly one entry."""
        if not isinstance(ident, str) or len(ident) < MIN_PREFIX or len(ident) > 64 or not _HEX.match(ident):
            return None, Result.refused("firmware_unknown",
                                        "an id is %d to 64 lowercase hex characters" % MIN_PREFIX, {"id": ident})
        found = [s for s in self._ids() if s.startswith(ident)]
        if not found:
            return None, Result.refused("firmware_unknown", "no stored firmware has that id", {"id": ident})
        if len(found) > 1:
            return None, Result.refused("firmware_unknown", "that prefix names more than one firmware",
                                        {"id": ident, "candidates": found})
        return found[0], None

    def list(self):
        items, unreadable = [], []
        for sha in self._ids():
            rec = self._read_record(sha)
            if rec is None:
                unreadable.append(sha)
            else:
                items.append(rec)
        items.sort(key=lambda r: (r.get("uploaded_at", ""), r["sha256"]), reverse=True)
        return Result.success({"items": items}, "%d stored" % len(items), {"unreadable": unreadable})

    def show(self, ident):
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        rec = self._read_record(sha)
        if rec is None:
            return Result.refused("bad_package", "the record for that id cannot be read", {"id": sha})
        return Result.success(dict(rec, used_by=list(self._in_use(sha))))

    def verify(self, ident):
        """Re-hash the stored image and compare it with its id. Run before every flash: a store that was
        altered or damaged after upload is refused before any hardware is touched."""
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        rec = self._read_record(sha)
        if rec is None:
            return Result.refused("bad_package", "the record for that id cannot be read", {"id": sha})
        path = self.image_path(sha)
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(CHUNK), b""):
                    h.update(chunk)
        except OSError as exc:
            return Result.refused("bad_package", "the stored image cannot be read: %s" % exc, {"id": sha})
        if h.hexdigest() != sha:
            return Result.refused("bad_package", "the stored image no longer matches its id",
                                  {"id": sha, "actual": h.hexdigest()})
        return Result.success({"id": sha, "path": path, "record": rec})

    # -- delete -------------------------------------------------------------------------------------
    def delete(self, ident):
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        users = list(self._in_use(sha))
        if users:
            return Result.refused("in_use", "this firmware is a unit's declared way back from the bootloader",
                                  {"id": sha, "units": users})
        # Record first: a crash between the two leaves an unlisted image, never a listed entry with no image.
        for path in (self._record_path(sha), self.image_path(sha)):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        self._fsync_dir()
        return Result.success({"id": sha}, "deleted")
