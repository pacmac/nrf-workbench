"""Where projects live (docs/specs/projects.md, "Where it lives").

    <paths.wiring>/projects/<name>/project.yaml     the manifest, the project's own hand-editable file
    <paths.wiring>/projects/state.json              {"projects": {<name>: {pending, owns, uses, applied_at, applied_by}}}

Both are written atomically. The firmware folder of the same name stays in `<paths.firmware>`; nothing here touches it. A state file that
cannot be read is an error that names it and is never overwritten.
"""
import os
import tempfile
import threading
import time

from ..core import jsonstore
from . import manifest

MANIFEST = "project.yaml"
STATE = "state.json"
KEY = "projects"
SECTIONS = ("targets", "connections", "pins", "macros")       # the order things are made in; released in reverse


class ProjectStore(object):
    def __init__(self, wiring_dir, now=time.time):
        self.root = os.path.join(wiring_dir, "projects")
        self.state_path = os.path.join(self.root, STATE)
        self._now = now
        self._lock = threading.Lock()

    def _dir(self, name):
        if not manifest.SLUG.match(name or ""):
            raise ValueError("a project name is 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit")
        return os.path.join(self.root, name)

    def names(self):
        try:
            return sorted(n for n in os.listdir(self.root)
                          if manifest.SLUG.match(n) and os.path.isfile(os.path.join(self.root, n, MANIFEST)))
        except OSError:
            return []

    def read(self, name):
        """The manifest text, or None."""
        try:
            with open(os.path.join(self._dir(name), MANIFEST), "r") as fh:
                return fh.read()
        except OSError:
            return None

    def write(self, name, text):
        d = self._dir(name)
        with self._lock:
            os.makedirs(d, mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".manifest-", suffix=".part")
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, os.path.join(d, MANIFEST))
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def delete(self, name):
        """True if there was a manifest. The folder goes with it only when nothing else is in it."""
        d = self._dir(name)
        with self._lock:
            path = os.path.join(d, MANIFEST)
            if not os.path.isfile(path):
                return False
            os.unlink(path)
            try:
                os.rmdir(d)
            except OSError:
                pass
            return True

    # -- what is applied ----------------------------------------------------------------------------
    def states(self):
        with self._lock:
            return jsonstore.read_map(self.state_path, KEY)

    def state(self, name):
        return self.states().get(name)

    def set_state(self, name, entry):
        """Replace (or, with None, remove) one project's entry, atomically."""
        with self._lock:
            states = jsonstore.read_map(self.state_path, KEY)
            if entry is None:
                states.pop(name, None)
            else:
                states[name] = entry
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            jsonstore.write_map(self.state_path, KEY, states)

    def owners(self):
        """{(section, object name): project name} over every project's `owns`, applied or pending."""
        out = {}
        for project, entry in self.states().items():
            for section in SECTIONS:
                for obj in (entry.get("owns") or {}).get(section, []):
                    out[(section, obj)] = project
        return out

    def stamp(self):
        return self._now()


StoreError = jsonstore.StoreError
