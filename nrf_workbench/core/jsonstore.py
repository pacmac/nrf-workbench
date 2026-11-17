"""A small bench-owned store: one JSON file holding {key: {name: record}}, written atomically.

Used by the things the bench itself keeps and every front end shares (the console macros, the GPIO pin map): a
temporary file in the same folder, `fsync`, then `os.replace`, so a reader sees the old file or the new one and
never half of one, and a failed write leaves the old file whole and no temporary behind. The folder is `0700`.
A file that cannot be read, or is not what this writes, is an error that names it and is NEVER overwritten: it
is left for a person to look at.
"""
import json
import os
import tempfile


class StoreError(Exception):
    """The store's file is unreadable or not what this code writes."""


def read_map(path, key):
    """The mapping under `key`, or {} if the file does not exist yet."""
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise StoreError("%s cannot be read: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get(key), dict):
        raise StoreError("%s is not a %s file" % (path, key))
    return data[key]


def write_map(path, key, mapping):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".store-", suffix=".part")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({key: mapping}, fh, sort_keys=True, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
