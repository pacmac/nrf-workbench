"""The command line, generated from the registry rows (docs/specs/api.md, "CLI and service").

        workbench [--json] [--host URL] [--token-file F] [--timeout S] [--config F] AREA COMMAND [--arg V ...]

It holds no command of its own. The rows come from one of two places and everything after is the same:

  local   (no --host)   the rows of the in-process registry; the command runs here, in this process, with
                        no service. The common, safe case, and the only one that works when the service
                        is down.
  remote  (--host URL)  the rows the service lists at GET /api/commands; the command runs on the service
                        over HTTP through core.remote. The token is read from a file or the environment,
                        never from the command line (it would show in `ps`).

Arguments are validated by the registry's OWN validator (a command line is text, so it is parsed), so
there is one set of rules. `--json` prints the Result, which is the API body. The exit code comes from
core.status (ok 0, refused 1, usage 2, failed 3, suspect 4). Imports only core.
"""
import argparse
import json
import os
import sys

from ..core import status
from ..core.config import ConfigError
from ..core.registry import Arg, Command, Context, Registry, UnknownCommand, UsageError, cli_words
from ..core.remote import Remote, RemoteError, file_sha256
from ..core.result import Result

_VALUE_FLAGS = ("--host", "--token-file", "--timeout", "--config")


class FileBody(object):
    """What a raw-body command is given in local mode: read(n) and the length, like a request."""

    def __init__(self, path):
        self._fh = open(path, "rb")
        self.length = os.path.getsize(path)

    def read(self, n=-1):
        return self._fh.read(n)

    def close(self):
        self._fh.close()


def _split_globals(argv):
    """The global flags before the first word, and the rest. Done by hand so a command's own argument can
    never be mistaken for a global one."""
    g = {"json": False, "host": None, "token_file": None, "timeout": None, "config": None}
    argv, i = list(argv), 0
    while i < len(argv) and argv[i].startswith("-") and argv[i] not in ("-h", "--help"):
        flag = argv[i]
        if flag == "--json":
            g["json"] = True
        else:
            name, eq, value = flag.partition("=")
            if name not in _VALUE_FLAGS:
                raise UsageError("unknown option %s" % flag)
            if not eq:
                i += 1
                if i >= len(argv):
                    raise UsageError("%s needs a value" % name)
                value = argv[i]
            g[name[2:].replace("-", "_")] = value
        i += 1
    return g, argv[i:]


def _stub(row):
    """A Command built from a row dict, only so the registry's own validator can check the arguments."""
    args = [Arg(a["name"], a["type"], a["required"], a["default"], a.get("doc", ""), a.get("unit")) for a in row["args"]]
    return Command(row["name"], row["summary"], lambda ctx, **kw: None, args=args, effect=row["effect"],
                   body=row.get("body"), stream=row.get("stream"))


def _flag(name):
    return "--" + name.replace("_", "-")


def _argument_help(a):
    notes = [a["type"] + (" " + a["unit"] if a.get("unit") else ""), "required" if a["required"] else "optional"]
    return "%s [%s]" % (a.get("doc") or "", ", ".join(notes))


def _parser(rows):
    p = argparse.ArgumentParser(
        prog="workbench", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="nrf-workbench: hands and eyes on Nordic nRF hardware.\n\n"
                    "global options, BEFORE the area:\n"
                    "  --json           print the Result as JSON (the API body)\n"
                    "  --host URL       run on a service at URL instead of locally (token: --token-file or NRFWB_TOKEN)\n"
                    "  --token-file F   a file holding the service token\n"
                    "  --timeout S      give up on the service after S seconds (none by default)\n"
                    "  --config F       a configuration file for local mode",
        epilog="exit: 0 ok, 1 refused, 2 usage, 3 failed, 4 suspect")
    sub = p.add_subparsers(dest="area", metavar="AREA")
    sub.add_parser("commands", help="list the commands present here")
    sub.add_parser("capabilities", help="what this host has, and why a command is absent")
    areas = {}
    for row in rows:
        area, word = cli_words(row["name"])
        areas.setdefault(area, []).append((word, row))
    for area in sorted(areas):
        ap = sub.add_parser(area, help="%d command(s)" % len(areas[area]))
        csub = ap.add_subparsers(dest="command", metavar="COMMAND")
        for word, row in sorted(areas[area], key=lambda x: x[0]):
            cp = csub.add_parser(word, help=row["summary"], description=row["summary"] +
                                 ("\nresult: " + row["result"] if row.get("result") else ""))
            for a in row["args"]:
                if a["type"] == "bool":
                    cp.add_argument(_flag(a["name"]), dest=a["name"], nargs="?", const="true", default=None,
                                    help=_argument_help(a), metavar="true|false")
                else:
                    cp.add_argument(_flag(a["name"]), dest=a["name"], default=None, help=_argument_help(a),
                                    metavar=a["type"].upper())
            if row.get("body") == "raw":
                cp.add_argument("--file", dest="_file", default=None, metavar="PATH",
                                help="the file to upload (it is the request body) [required]")
    return p


# -- output ----------------------------------------------------------------------------------------
def _emit(result, as_json, out):
    if as_json:
        print(result.to_json(), file=out)
        return
    head = result.cls if result.code == "ok" else "%s %s" % (result.cls, result.code)
    print("%s: %s" % (head, result.message) if result.message else head, file=out)
    for label, block in (("data", result.data), ("evidence", result.evidence)):
        if block:
            print("%s:" % label, file=out)
            print(json.dumps(block, indent=2, sort_keys=True, default=str), file=out)


def _remote_failure(exc, host, as_json, out, err):
    """The exit code for a failure to get a Result from the service, and what to print."""
    if exc.kind == "unreachable":
        result = Result.refused("service_not_running", str(exc), {"address": host})
        _emit(result, as_json, out)
        return status.exit_code(result)
    message = "%s: %s" % (exc.kind, exc)
    if as_json:
        print(json.dumps({"ok": False, "error": exc.kind, "message": str(exc)}, indent=2), file=out)
    else:
        print(message, file=err)
    return {"unauthorized": 1, "usage": status.USAGE_EXIT, "file": status.USAGE_EXIT, "not_found": status.USAGE_EXIT}.get(
        exc.kind, 3)


def _read_token(g, env, err):
    path = g["token_file"] or env.get("NRFWB_TOKEN_FILE")
    if path:
        try:
            with open(path) as fh:
                return fh.read().strip() or None
        except OSError as exc:
            raise UsageError("the token file %s cannot be read: %s" % (path, exc))
    return env.get("NRFWB_TOKEN") or None


def run(argv, local=None, env=None, out=None, err=None):
    """Returns the exit code. `local(config_path)` returns (registry, host, capabilities) for local mode."""
    env = os.environ if env is None else env
    out, err = out or sys.stdout, err or sys.stderr
    try:
        g, rest = _split_globals(argv)
        host_url = g["host"] or env.get("NRFWB_HOST")
        timeout = float(g["timeout"]) if g["timeout"] is not None else None
        if timeout is not None and not timeout > 0:
            raise UsageError("--timeout must be a positive number of seconds")
    except (UsageError, ValueError) as exc:
        print("usage: %s" % exc, file=err)
        return status.USAGE_EXIT

    remote = registry = host = caps = None
    try:
        if host_url:
            remote = Remote(host_url, _read_token(g, env, err), timeout)
            rows = remote.commands()
        elif local is None:
            print("usage: no service address (--host) and no local mode available", file=err)
            return status.USAGE_EXIT
        else:
            registry, host, caps = local(g["config"])
            rows = [c.to_dict() for c in registry.commands(caps)]
    except UsageError as exc:
        print("usage: %s" % exc, file=err)
        return status.USAGE_EXIT
    except ValueError as exc:
        print("usage: %s" % exc, file=err)
        return status.USAGE_EXIT
    except ConfigError as exc:
        print("configuration error: %s" % exc, file=err)
        return status.USAGE_EXIT
    except RemoteError as exc:
        return _remote_failure(exc, host_url, g["json"], out, err)

    parser = _parser(rows)
    try:
        ns = parser.parse_args(rest)
    except SystemExit as exc:                                  # argparse: --help (0) or a parse error (2)
        return exc.code if isinstance(exc.code, int) else status.USAGE_EXIT
    if ns.area is None:
        parser.print_help(file=err)
        return status.USAGE_EXIT

    if ns.area == "commands":
        if g["json"]:
            print(json.dumps({"commands": rows}, indent=2, sort_keys=True), file=out)
        else:
            width = max([len(r["name"]) for r in rows] or [0])
            for r in rows:
                print("%s  %-5s  %s" % (r["name"].ljust(width), r["effect"], r["summary"]), file=out)
        return 0
    if ns.area == "capabilities":
        try:
            if remote is not None:
                info = remote.capabilities()
            else:
                info = {"present": sorted(caps),
                        "absent": [{"name": c.name, "unmet": registry.unmet(c, caps)}
                                   for c in registry.commands() if registry.unmet(c, caps)]}
        except RemoteError as exc:
            return _remote_failure(exc, host_url, g["json"], out, err)
        if g["json"]:
            print(json.dumps(info, indent=2, sort_keys=True), file=out)
        else:
            print("present: %s" % (", ".join(info["present"]) or "nothing"), file=out)
            for a in info["absent"]:
                print("absent:  %s (needs %s)" % (a["name"], ", ".join(a["unmet"])), file=out)
        return 0

    word = ns.command
    row = [r for r in rows if cli_words(r["name"]) == (ns.area, word)]
    if word is None or not row:
        print("usage: name a command; try: workbench %s --help" % ns.area, file=err)
        return status.USAGE_EXIT
    row = row[0]
    raw = dict((a["name"], getattr(ns, a["name"])) for a in row["args"] if getattr(ns, a["name"]) is not None)
    try:
        typed = Registry().validate(_stub(row), raw, from_text=True)       # the registry's own rules
        file_path = None
        if row.get("body") == "raw":
            file_path = getattr(ns, "_file", None)
            if not file_path:
                raise UsageError("%s takes the file to upload: --file PATH" % row["name"])
            if not os.path.isfile(file_path):
                raise UsageError("--file %s is not a file" % file_path)
    except UsageError as exc:
        print("usage: %s" % exc, file=err)
        return status.USAGE_EXIT

    if row.get("needs_service") and remote is None:
        result = Result.refused("service_not_running", "%s keeps state across calls, so it needs the service: "
                                "run it with --host" % row["name"], {"address": None})
        _emit(result, g["json"], out)
        return status.exit_code(result)

    try:
        if remote is not None:
            if file_path and any(a["name"] == "sha256" for a in row["args"]) and typed.get("sha256") is None:
                typed["sha256"] = file_sha256(file_path)        # a corrupt transfer is caught, not found later
            result = remote.call(row, typed, file_path)
        else:
            body = FileBody(file_path) if file_path else None
            try:
                result = registry.call(row["name"], typed, Context(host, "cli", body), capabilities=caps)
            finally:
                if body is not None:
                    body.close()
    except UsageError as exc:
        print("usage: %s" % exc, file=err)
        return status.USAGE_EXIT
    except UnknownCommand:
        print("usage: %s is not available here" % row["name"], file=err)
        return status.USAGE_EXIT
    except RemoteError as exc:
        return _remote_failure(exc, host_url, g["json"], out, err)
    except OSError as exc:
        print("usage: the file could not be read: %s" % exc, file=err)
        return status.USAGE_EXIT
    _emit(result, g["json"], out)
    return status.exit_code(result)
