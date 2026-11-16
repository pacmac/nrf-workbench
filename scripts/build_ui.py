#!/usr/bin/env python3
"""Build the dashboard UI and stamp the build with the digest of the source it was built from.

    python3 scripts/build_ui.py          # needs node and npm; `npm ci` is run first if node_modules is absent

The built files are COMMITTED (the installer fetches a tarball of a commit, so the Pi never needs Node). The
stamp, `nrf_workbench/dashboard/static/source.sha256`, is the SHA-256 over every file the build reads: a test
recomputes it, so a committed build older than its source is a failing test, not a quiet stale dashboard.
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
UI = os.path.join(ROOT, "ui")
STATIC = os.path.join(ROOT, "nrf_workbench", "dashboard", "static")
STAMP = os.path.join(STATIC, "source.sha256")
TOP_LEVEL = ("index.html", "package.json", "package-lock.json", "vite.config.js")


def source_files(ui=UI):
    """Every file the build reads, as sorted relative paths."""
    found = [name for name in TOP_LEVEL if os.path.isfile(os.path.join(ui, name))]
    for base, dirs, names in os.walk(os.path.join(ui, "src")):
        dirs.sort()
        for name in sorted(names):
            found.append(os.path.relpath(os.path.join(base, name), ui))
    return sorted(found)


def source_digest(ui=UI):
    h = hashlib.sha256()
    for rel in source_files(ui):
        with open(os.path.join(ui, rel), "rb") as fh:
            h.update(rel.replace(os.sep, "/").encode() + b"\0" + fh.read() + b"\0")
    return h.hexdigest()


def main():
    if not os.path.isdir(os.path.join(UI, "node_modules")):
        subprocess.check_call(["npm", "ci", "--no-audit", "--no-fund"], cwd=UI)
    subprocess.check_call(["npm", "run", "build"], cwd=UI)
    digest = source_digest()
    with open(STAMP, "w") as fh:
        fh.write(digest + "\n")
    print("built; source digest %s" % digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
