"""The command registry (docs/specs/core.md): every command declared ONCE, as one row.

The CLI, the HTTP API and the MCP tools are generated from these rows and hold no commands of their
own. This module imports no front end and no web framework: a handler is a plain function, so the same
function runs from the console, a request and a test.

A handler is `handler(ctx, **args) -> Result`. `ctx` carries the host (configuration and services), the
caller's label, and, for a raw-body command, the body. A handler never raises for an expected outcome.

Names are derived, never written three times: `programmer.flash_file` is `/api/programmer/flash_file`
over HTTP, `programmer flash-file` on the command line and `programmer_flash_file` as an MCP tool. The
area part cannot contain an underscore, so no two commands can ever derive the same MCP name.
"""
import math
import re

from .result import Result

TYPES = ("str", "int", "float", "bool")
EFFECTS = ("read", "write", "stream")
_NAME = re.compile(r"^[a-z][a-z0-9]*\.[a-z][a-z0-9_]*$")
_ARG = re.compile(r"^[a-z][a-z0-9_]*$")
_TRUE = ("true", "1", "yes")
_FALSE = ("false", "0", "no")


class UsageError(ValueError):
    """A bad argument: the argument parser's business, never a Result (HTTP 400, exit 2)."""

    def __init__(self, message, arg=None):
        ValueError.__init__(self, message)
        self.arg = arg


class DuplicateCommand(ValueError):
    pass


class UnknownCommand(KeyError):
    pass


class Arg(object):
    __slots__ = ("name", "type", "required", "default", "doc", "unit")

    def __init__(self, name, type="str", required=True, default=None, doc="", unit=None):
        if not _ARG.match(name or ""):
            raise ValueError("bad argument name %r" % (name,))
        if type not in TYPES:
            raise ValueError("argument %s: type must be one of %s" % (name, ", ".join(TYPES)))
        if required and default is not None:
            raise ValueError("argument %s is required, so it has no default" % name)
        self.name, self.type, self.required, self.default, self.doc, self.unit = name, type, required, default, doc, unit

    def to_dict(self):
        return {"name": self.name, "type": self.type, "required": self.required, "default": self.default,
                "doc": self.doc, "unit": self.unit}


def http_path(name):
    area, command = name.split(".")
    return "/api/%s/%s" % (area, command)


def cli_words(name):
    area, command = name.split(".")
    return (area, command.replace("_", "-"))


def mcp_name(name):
    return name.replace(".", "_")


class Command(object):
    def __init__(self, name, summary, handler, args=(), requires=(), effect="read", body=None,
                 needs_service=False, result="", stream=None):
        if not _NAME.match(name or ""):
            raise ValueError("a command is named area.command in lower case, got %r" % (name,))
        if effect not in EFFECTS:
            raise ValueError("%s: effect must be one of %s" % (name, ", ".join(EFFECTS)))
        if body not in (None, "raw"):
            raise ValueError("%s: body is None or 'raw'" % name)
        if body == "raw" and effect != "write":
            raise ValueError("%s: a raw-body command changes something, so it is a write" % name)
        if effect == "stream" and not stream:
            raise ValueError("%s: a stream command names the event type it yields" % name)
        if not callable(handler):
            raise ValueError("%s: the handler must be callable" % name)
        args = tuple(args)
        names = [a.name for a in args]
        if len(set(names)) != len(names):
            raise ValueError("%s: two arguments have the same name" % name)
        for reserved in ("ctx", "body"):
            if reserved in names:
                raise ValueError("%s: %r is reserved" % (name, reserved))
        self.name, self.summary, self.handler, self.args = name, summary, handler, args
        self.requires, self.effect, self.body = tuple(requires), effect, body
        self.needs_service, self.result, self.stream = needs_service, result, stream

    def to_dict(self):
        return {"name": self.name, "summary": self.summary, "args": [a.to_dict() for a in self.args],
                "requires": list(self.requires), "effect": self.effect, "body": self.body,
                "needs_service": self.needs_service, "result": self.result, "stream": self.stream,
                "path": http_path(self.name), "cli": " ".join(cli_words(self.name)),
                "mcp": mcp_name(self.name)}


class Context(object):
    """What a handler is given besides its arguments."""
    __slots__ = ("host", "caller", "body")

    def __init__(self, host=None, caller=None, body=None):
        self.host, self.caller, self.body = host, caller, body


def _parse_text(arg, text):
    try:
        if arg.type == "int":
            return int(text)
        if arg.type == "float":
            value = float(text)
            if not math.isfinite(value):
                raise ValueError("not a finite number")
            return value
        if arg.type == "bool":
            low = text.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
            raise ValueError("expected true or false")
    except ValueError as exc:
        raise UsageError("argument %s: %r is not a valid %s (%s)" % (arg.name, text, arg.type, exc), arg.name)
    return text


def _check_typed(arg, value):
    ok = {"str": isinstance(value, str),
          "int": isinstance(value, int) and not isinstance(value, bool),
          "float": isinstance(value, (int, float)) and not isinstance(value, bool),
          "bool": isinstance(value, bool)}[arg.type]
    if not ok:
        raise UsageError("argument %s must be a %s, got %r" % (arg.name, arg.type, value), arg.name)
    if arg.type == "float":
        value = float(value)
        if not math.isfinite(value):
            raise UsageError("argument %s must be a finite number" % arg.name, arg.name)
    return value


class Registry(object):
    def __init__(self):
        self._commands = {}
        self._defined_by = {}

    def add(self, command, module=None):
        if command.name in self._commands:
            raise DuplicateCommand("command %s is defined twice: by %s and by %s"
                                   % (command.name, self._defined_by[command.name], module or "an unnamed module"))
        self._commands[command.name] = command
        self._defined_by[command.name] = module or "an unnamed module"
        return command

    def unmet(self, command, capabilities):
        return [c for c in command.requires if c not in (capabilities or ())]

    def get(self, name, capabilities=None):
        """The command, or None if there is none or one of its requirements is unmet: an unmet
        command is ABSENT, not present and failing. Pass capabilities=None to ignore requirements."""
        command = self._commands.get(name)
        if command is None:
            return None
        if capabilities is not None and self.unmet(command, capabilities):
            return None
        return command

    def commands(self, capabilities=None):
        return sorted((c for c in self._commands.values()
                       if capabilities is None or not self.unmet(c, capabilities)), key=lambda c: c.name)

    def validate(self, command, raw, from_text=False):
        """Typed keyword arguments from `raw`. `from_text` is for a query string or a command line,
        where everything arrives as text and is parsed; a JSON body is NOT parsed, it is checked."""
        raw = dict(raw or {})
        known = dict((a.name, a) for a in command.args)
        for name in raw:
            if name not in known:
                raise UsageError("%s takes no argument %r (it takes: %s)"
                                 % (command.name, name, ", ".join(sorted(known)) or "none"), name)
        out = {}
        for arg in command.args:
            if arg.name not in raw:
                if arg.required:
                    raise UsageError("%s needs the argument %s" % (command.name, arg.name), arg.name)
                out[arg.name] = arg.default
                continue
            value = raw[arg.name]
            if from_text and isinstance(value, str):
                out[arg.name] = _parse_text(arg, value)
            else:
                out[arg.name] = _check_typed(arg, value)
        return out

    def call(self, name, args, ctx, capabilities=None, from_text=False):
        command = self.get(name, capabilities)
        if command is None:
            raise UnknownCommand(name)
        result = command.handler(ctx, **self.validate(command, args, from_text))
        if not isinstance(result, Result):
            raise TypeError("%s returned %r, not a Result" % (name, type(result).__name__))
        return result
