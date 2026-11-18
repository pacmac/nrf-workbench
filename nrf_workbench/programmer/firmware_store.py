"""The firmware store (docs/specs/firmware.md): where a built image lives between the build and the flash.

A directory on the host with one folder per project inside it:

    <store>/<project>/<sha256>.zip     exactly the bytes received
    <store>/<project>/<sha256>.json    the record

The id is the SHA-256 of the file, so an id cannot come to mean different bytes, an upload is idempotent,
and a package lives in exactly one project. The only strings that ever become part of a path are a hash and
a project name that passed `check_project`: its alphabet has no separator and cannot start with a dot, so
`..`, `a/b` and an absolute path can never be a project. `name` is data in the record and nothing more.
The folder a package is in is the one source of truth for its project; the record does not repeat it.

`put` is the careful part. The body is read to exactly the declared length (no more), into a temporary
file inside the store, hashing as it arrives, and **nothing becomes visible until the whole file has
arrived and passed** its checks. A short body, a dropped connection, a wrong checksum, a bad package or
a crash leaves no entry, and no project folder either. A stale temporary is told from one still being
written by a lock the writer holds, not by a guessed age.

An entry exists only if it has a record: a file copied in by hand is not listed and never flashed.
A crash leaves an unlisted image, never a listed entry with no image: delete removes the record first, and
a move links the image into the new project, then moves the record, then removes the old image.
Expected outcomes are Results; an unexpected operating-system error propagates (the service turns it
into a failure), and a bad argument is a ValueError or TypeError, which is a usage error, not a Result.
"""
import contextlib
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
from ..esp32 import package as esp_package
from ..guards import checks
from .flash_file import expected_packets

MIN_PREFIX = 8                      # spec: "any unique prefix of at least 8 hex characters"
CHUNK = io.DEFAULT_BUFFER_SIZE      # the standard library's own buffer size, not a limit on the file
TEMP_PREFIX = ".incoming-"
DEFAULT_PROJECT = "unsorted"        # where a put that names no project goes; an ordinary project otherwise
_HEX = re.compile(r"^[0-9a-f]+$")
_ENTRY_RECORD = re.compile(r"^[0-9a-f]{64}\.json$")
_ENTRY_IMAGE = re.compile(r"^[0-9a-f]{64}\.zip$")
IMAGE_EXTS = (".zip", ".bin")       # a whole-flash ESP image is stored as .bin: it is not a zip (docs/specs/esp32.md)
# 1 to 64 characters from a-z 0-9 . _ - and the first a letter or a digit (docs/specs/firmware.md, "projects")
_PROJECT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def check_project(name):
    """None if `name` may be a project folder, else a `bad_project` refusal. A non-string is a usage error."""
    if not isinstance(name, str):
        raise TypeError("project must be a string")
    if _PROJECT.fullmatch(name) is None:
        return Result.refused("bad_project",
                              "a project name is 1 to 64 characters from a-z 0-9 . _ - and starts with a letter "
                              "or a digit", {"project": name})
    return None


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
        self.filed = self._file_flat_entries()      # how many entries from a flat store were filed on open

    # -- paths: the only names the store ever builds are hashes and checked project names ------------
    def _project_dir(self, project):
        return os.path.join(self.root, project)

    def image_path(self, sha, project=None, ext=None):
        """Where the image is (or would be, in `project`). With no project, the project that holds it; an
        id the store does not hold gives a path that does not exist. The extension is the one the stored
        file has (`.bin` for a whole-flash ESP image, `.zip` for the rest); `ext` names it for a file not yet
        written."""
        project = project or self._locate(sha) or DEFAULT_PROJECT
        base = os.path.join(self._project_dir(project), sha)
        if ext is None:
            ext = next((e for e in IMAGE_EXTS if os.path.exists(base + e)), ".zip")
        return base + ext

    def _record_path(self, sha, project):
        return os.path.join(self._project_dir(project), sha + ".json")

    @contextlib.contextmanager
    def _exclusive(self):
        """One writer at a time for anything that decides where a package lives. The lock is on the store
        directory itself (no lock file to show up in a listing), and closing the descriptor releases it."""
        fd = os.open(self.root, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    # -- projects ----------------------------------------------------------------------------------
    def projects(self):
        """Every project folder, empty ones too. A name that breaks the rule, or a link, is not a project."""
        out = []
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name)
            if _PROJECT.fullmatch(name) and os.path.isdir(path) and not os.path.islink(path):
                out.append(name)
        return out

    def _entries(self):
        """{sha256: project}. A package lives in one project; if a copy made by hand puts one in two, the first
        project in name order is the one the store uses."""
        found = {}
        for project in self.projects():
            for name in os.listdir(self._project_dir(project)):
                if name.endswith(".json") and not name.startswith("."):
                    found.setdefault(name[:-5], project)
        return found

    def _make_project(self, project):
        """Create the folder for a checked project name, or refuse if the name is already taken by something
        that is not a project folder: a symbolic link (which could lead out of the store) or a plain file."""
        path = self._project_dir(project)
        if os.path.islink(path) or (os.path.exists(path) and not os.path.isdir(path)):
            return Result.refused("bad_project", "that name is already used by something that is not a project "
                                  "folder", {"project": project})
        os.makedirs(path, mode=0o700, exist_ok=True)
        return None

    def _locate(self, sha):
        for project in self.projects():
            if os.path.exists(self._record_path(sha, project)):
                return project
        return None

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

    # -- a flat store from before projects -----------------------------------------------------------
    def _file_flat_entries(self):
        """Move entries sitting directly in the store into `unsorted/`, by the same three steps as a move, and
        finish a pair that a crash left half moved. Idempotent: a second open finds nothing to do."""
        moved = 0
        with self._exclusive():
            for name in sorted(os.listdir(self.root)):
                if _ENTRY_RECORD.match(name) is None:
                    continue
                sha = name[:-5]
                dest = self._project_dir(DEFAULT_PROJECT)
                os.makedirs(dest, mode=0o700, exist_ok=True)
                old_image = os.path.join(self.root, sha + ".zip")
                if os.path.exists(old_image):
                    try:
                        os.link(old_image, os.path.join(dest, sha + ".zip"))
                    except FileExistsError:
                        pass
                os.replace(os.path.join(self.root, name), os.path.join(dest, name))
                if os.path.exists(old_image):
                    os.unlink(old_image)
                moved += 1
            for name in sorted(os.listdir(self.root)):      # an image whose record a crash already moved
                if _ENTRY_IMAGE.match(name) is None:
                    continue
                holder = self._locate(name[:-4])
                if holder is None:
                    continue                                # no record anywhere: copied in by hand, left alone
                try:
                    os.link(os.path.join(self.root, name), os.path.join(self._project_dir(holder), name))
                except FileExistsError:
                    pass
                os.unlink(os.path.join(self.root, name))
                moved += 1
            if moved:
                self._fsync_dir(self.root)
                self._fsync_dir(self._project_dir(DEFAULT_PROJECT))
        return moved

    # -- records ------------------------------------------------------------------------------------
    def _read_record(self, sha, project=None):
        project = project or self._locate(sha)
        if project is None:
            return None
        try:
            with open(self._record_path(sha, project), "r") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(rec, dict) or rec.get("sha256") != sha:
            return None
        return rec

    def _write_record(self, sha, rec, project):
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=TEMP_PREFIX, suffix=".json.part")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._record_path(sha, project))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _fsync_dir(self, path):
        try:
            dfd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dfd)
        except OSError:
            pass
        finally:
            os.close(dfd)

    # -- put ----------------------------------------------------------------------------------------
    def put(self, body, content_length, name=None, sha256=None, uploaded_by=None, project=None):
        """Store the `content_length` bytes read from `body` in `project` (default `unsorted`). Returns a
        Result; on ok, data is the record plus `project` and `new` (False when the same bytes were already
        stored, in which case `project` is the one that holds them)."""
        if name is not None and not isinstance(name, str):
            raise TypeError("name must be a string")
        if uploaded_by is not None and not isinstance(uploaded_by, str):
            raise TypeError("uploaded_by must be a string")
        if sha256 is not None:
            if not isinstance(sha256, str) or len(sha256) != 64 or not _HEX.match(sha256.lower()):
                raise ValueError("sha256 must be 64 hex characters")
            sha256 = sha256.lower()
        project = DEFAULT_PROJECT if project is None else project
        bad = check_project(project)                    # before the body is read: a bad name costs no upload
        if bad is not None:
            return bad
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
            esp_kind = esp_package.kind_of(tmp)
            if esp_kind is not None:
                try:
                    info = esp_package.inspect(tmp)
                except esp_package.Bad as exc:
                    return Result.refused("bad_package", str(exc), {"package": "the uploaded file"})
            else:
                why = esp_package.single_part_message(tmp)
                if why is not None:
                    return Result.refused("bad_package", why, {"package": "the uploaded file"})
                refusal, info = checks.dfu_package({}, tmp)     # shape only: the SoftDevice is checked at flash
                if refusal is not None:
                    refusal.evidence = dict(refusal.evidence, package="the uploaded file")
                    return refusal
            with self._exclusive():                             # decide where it lives, and put it there, as one act
                holder = self._locate(digest)
                if holder is not None:
                    existing = self._read_record(digest, holder)
                    if existing is not None:
                        where = ("already stored" if holder == project else
                                 "already stored in project %s and not copied; firmware.move files it elsewhere"
                                 % holder)
                        return Result.success(dict(existing, project=holder, new=False), where)
                    project = holder                            # a record that cannot be read is healed in place
                refusal = self._make_project(project)
                if refusal is not None:
                    return refusal
                ext = ".bin" if esp_kind == esp_package.KIND_IMAGE else ".zip"
                os.replace(tmp, self.image_path(digest, project, ext))   # atomic: the same filesystem
                record = {"sha256": digest, "bytes": got, "name": name, "uploaded_at": _iso(self._now()),
                          "uploaded_by": uploaded_by, "image_bytes": info["image_bytes"]}
                if esp_kind is None:
                    record.update(softdevice_req=info["softdevice_req"], packets=expected_packets(info["image_bytes"]))
                else:
                    # An ESP package has no SoftDevice and no DFU packets; `kind` is only written for it, so a
                    # record without one is an nRF DFU package, as every record before this was.
                    record.update(softdevice_req=None, packets=None, kind=esp_kind, parts=len(info["parts"]),
                                  chip=info["chip"], elf=info["elf"])
                self._write_record(digest, record, project)
                self._fsync_dir(self._project_dir(project))
            return Result.success(dict(record, project=project, new=True), "stored")
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
        name exactly one entry, in whatever project it is."""
        if not isinstance(ident, str) or len(ident) < MIN_PREFIX or len(ident) > 64 or not _HEX.match(ident):
            return None, Result.refused("firmware_unknown",
                                        "an id is %d to 64 lowercase hex characters" % MIN_PREFIX, {"id": ident})
        found = sorted(s for s in self._entries() if s.startswith(ident))
        if not found:
            return None, Result.refused("firmware_unknown", "no stored firmware has that id", {"id": ident})
        if len(found) > 1:
            return None, Result.refused("firmware_unknown", "that prefix names more than one firmware",
                                        {"id": ident, "candidates": found})
        return found[0], None

    def list(self):
        items, unreadable = [], []
        for sha, project in sorted(self._entries().items()):
            rec = self._read_record(sha, project)
            if rec is None:
                unreadable.append(sha)
            else:
                items.append(dict(rec, project=project))
        items.sort(key=lambda r: (r.get("uploaded_at", ""), r["sha256"]), reverse=True)
        evidence = {"unreadable": unreadable}
        if self.filed:
            evidence["filed_on_open"] = self.filed
        return Result.success({"items": items, "projects": self.projects()}, "%d stored" % len(items), evidence)

    def show(self, ident):
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        project = self._locate(sha)
        rec = self._read_record(sha, project)
        if rec is None:
            return Result.refused("bad_package", "the record for that id cannot be read", {"id": sha})
        return Result.success(dict(rec, project=project, used_by=list(self._in_use(sha))))

    def verify(self, ident):
        """Re-hash the stored image and compare it with its id. Run before every flash: a store that was
        altered or damaged after upload is refused before any hardware is touched."""
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        project = self._locate(sha)
        rec = self._read_record(sha, project)
        if rec is None:
            return Result.refused("bad_package", "the record for that id cannot be read", {"id": sha})
        path = self.image_path(sha, project)
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
        return Result.success({"id": sha, "path": path, "record": rec, "project": project})

    # -- delete and move ----------------------------------------------------------------------------
    def delete(self, ident):
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        users = list(self._in_use(sha))
        if users:
            return Result.refused("in_use", "this firmware is a unit's declared way back from the bootloader",
                                  {"id": sha, "units": users})
        with self._exclusive():
            project = self._locate(sha)
            if project is None:
                return Result.refused("firmware_unknown", "no stored firmware has that id", {"id": ident})
            # Record first: a crash between the two leaves an unlisted image, never a listed entry with no image.
            for path in (self._record_path(sha, project), self.image_path(sha, project)):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
            self._fsync_dir(self._project_dir(project))
        return Result.success({"id": sha}, "deleted")

    def move(self, ident, project):
        """File a package under another project: link the image there, move the record, remove the old
        image, so at every point either the old entry or the new one is whole. Refused (`in_use`) for a
        package a unit names as its way back, because that unit's declared path would stop existing."""
        bad = check_project(project)
        if bad is not None:
            return bad
        sha, refusal = self.resolve(ident)
        if refusal is not None:
            return refusal
        users = list(self._in_use(sha))
        if users:
            return Result.refused("in_use", "this firmware is a unit's declared way back, and its path would change",
                                  {"id": sha, "units": users})
        with self._exclusive():
            old = self._locate(sha)
            if old is None:
                return Result.refused("firmware_unknown", "no stored firmware has that id", {"id": ident})
            if old == project:
                return Result.success({"id": sha, "from": old, "project": project}, "already in that project")
            old_image = self.image_path(sha, old)
            if not os.path.exists(old_image):
                return Result.refused("bad_package", "the stored image cannot be found, so nothing was moved",
                                      {"id": sha})
            dest = self._project_dir(project)
            refusal = self._make_project(project)
            if refusal is not None:
                return refusal
            try:
                os.link(old_image, os.path.join(dest, sha + os.path.splitext(old_image)[1]))
            except FileExistsError:
                pass                                            # a link a crash left: the same file
            os.replace(self._record_path(sha, old), self._record_path(sha, project))
            os.unlink(old_image)
            self._fsync_dir(dest)
            self._fsync_dir(self._project_dir(old))
        return Result.success({"id": sha, "from": old, "project": project}, "moved")

    # -- projects as folders ------------------------------------------------------------------------
    def project_new(self, project):
        bad = check_project(project)
        if bad is not None:
            return bad
        with self._exclusive():
            path = self._project_dir(project)
            new = not os.path.isdir(path)
            refusal = self._make_project(project)
            if refusal is not None:
                return refusal
            self._fsync_dir(self.root)
        return Result.success({"project": project, "new": new}, "created" if new else "already there")

    def project_delete(self, project):
        """Remove a project folder, only if it is empty: a mistyped name must be removable and a package must
        never be lost to it. Files that are not listed packages also stop the removal, and are named."""
        bad = check_project(project)
        if bad is not None:
            return bad
        with self._exclusive():
            path = self._project_dir(project)
            if project not in self.projects():
                return Result.refused("project_unknown", "there is no such project", {"project": project})
            held = sum(1 for n in os.listdir(path) if n.endswith(".json") and not n.startswith("."))
            if held:
                return Result.refused("project_not_empty", "the project still holds %d package%s" % (
                    held, "" if held == 1 else "s"), {"project": project, "packages": held})
            try:
                os.rmdir(path)
            except OSError as exc:
                if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                    return Result.refused("project_not_empty", "the folder holds files that are not listed packages; "
                                          "nothing was removed", {"project": project, "files": sorted(os.listdir(path))})
                raise
            self._fsync_dir(self.root)
        return Result.success({"project": project}, "removed")
