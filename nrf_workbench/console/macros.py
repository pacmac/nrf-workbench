"""The console's macros: named lines the bench keeps, so a command used often is not typed again.

A macro is a name and some lines. It belongs to the BENCH, not to a browser: it lives in `<paths.console>/macros.json`
(written atomically), so the CLI, the MCP and every browser see the same ones. Running one sends its lines in order
through the same send path as a typed line, so every guard applies to every line (docs/specs/console.md, "slice 1").
"""
import os
import re
import threading
import time

from ..core import jsonstore
from .broker import LINE_CAP_BYTES

SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# A macro is a few commands. 4 096 bytes is four times the line cap: room for a handful of full-length lines.
MAX_BYTES = 4 * LINE_CAP_BYTES

MacroError = jsonstore.StoreError                  # the macros file cannot be used (unreadable or not what this code writes)


def check_line(line):
    """None if `line` may be sent as one command, else the reason. One call is one command: no newline, no carriage
    return, no NUL, and no longer than the line cap."""
    if any(c in line for c in ("\n", "\r", "\0")):
        return "a line may not contain a newline, a carriage return or a NUL"
    if len(line.encode("utf-8")) > LINE_CAP_BYTES:
        return "a line is at most %d bytes" % LINE_CAP_BYTES
    return None


def check_macro(name, text):
    """(lines, None) or (None, the reason). `text` is the lines of the macro separated by newlines."""
    if not isinstance(name, str) or not SLUG.match(name):
        return None, "a macro name is 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit"
    if not isinstance(text, str):
        return None, "a macro's text is a string"
    if len(text.encode("utf-8")) > MAX_BYTES:
        return None, "a macro is at most %d bytes" % MAX_BYTES
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()                                   # a trailing newline is not another line
    if not lines:
        return None, "a macro has at least one line"
    for line in lines:
        why = check_line(line)
        if why:
            return None, why
    return lines, None


class MacroStore(object):
    def __init__(self, directory, now=time.time):
        self.path = os.path.join(directory, "macros.json")
        self._now = now
        self._lock = threading.Lock()

    def _read(self):
        return jsonstore.read_map(self.path, "macros")

    def _write(self, macros):
        jsonstore.write_map(self.path, "macros", macros)

    def list(self):
        with self._lock:
            macros = self._read()
        return [dict(name=n, **macros[n]) for n in sorted(macros)]

    def get(self, name):
        with self._lock:
            return self._read().get(name)

    def set(self, name, text, by=None):
        """(record, created). The caller has already checked the name and the text."""
        with self._lock:
            macros = self._read()
            created = name not in macros
            macros[name] = {"text": text, "lines": text.replace("\r\n", "\n").rstrip("\n").count("\n") + 1,
                            "updated_at": self._now(), "updated_by": by}
            self._write(macros)
            return dict(name=name, **macros[name]), created

    def delete(self, name):
        with self._lock:
            macros = self._read()
            if name not in macros:
                return False
            del macros[name]
            self._write(macros)
            return True
