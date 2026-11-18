"""Names for boards: a label kept against a board's USB serial, in a small JSON file the bench owns.

    <paths.wiring>/device-names.json     {"names": {"<usb serial>": "<name>"}}

The serial is what identifies a board (docs/specs/devices.md); a name is only a label a person gave it, so nothing here chooses,
guesses or matches a board by it. The file is written atomically (`core/jsonstore`), is read on every use (so editing it by hand
takes effect at once), and one that cannot be read is an error that names it and is never overwritten.
"""
import os
import re

from ..core import jsonstore

FILE = "device-names.json"
KEY = "names"
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class NameStore(object):
    def __init__(self, folder):
        self.path = os.path.join(folder, FILE)

    def all(self):
        """{serial: name}."""
        return jsonstore.read_map(self.path, KEY)

    def set(self, serial, name):
        """'ok', or 'taken' when another serial already has `name`."""
        names = self.all()
        if any(s != serial and n == name for s, n in names.items()):
            return "taken"
        names[serial] = name
        jsonstore.write_map(self.path, KEY, names)
        return "ok"

    def clear(self, serial):
        """True if the serial had a name."""
        names = self.all()
        if serial not in names:
            return False
        del names[serial]
        jsonstore.write_map(self.path, KEY, names)
        return True


def names_for(host):
    """{serial: name} for a host, or {} where there is no folder or the file cannot be read: a name is a label, so a bad file
    must never take the board list down with it (devices.name reports it when someone tries to write)."""
    folder = ((getattr(host, "config", None) or {}).get("paths") or {}).get("wiring")
    if not folder:
        return {}
    try:
        return NameStore(folder).all()
    except jsonstore.StoreError:
        return {}
