"""The API describes itself (docs/specs/api.md, "Published for a client that has never seen it").

Three documents, all GENERATED from the command registry and this host's capabilities, so they cannot disagree with what the
service does and a command added to an area appears in them with no edit here:

  index(...)    GET /api               a small JSON map: how to call, and where the rest is
  openapi(...)  GET /api/openapi.json  OpenAPI 3.1 for the commands present on this host
  guide(...)    GET /api/guide         one markdown page an agent can read cold: the conventions, the result codes, every
                                       command with its arguments, worked examples, and the other front ends (CLI, MCP)
  LLMS_TXT      GET /llms.txt          a pointer, public, naming nothing about the host

Only the prose below is written by hand; the commands, arguments, effects, units, codes and statuses are read from the registry
and from core.status.
"""
from ..core import status
from ..core.registry import http_path
from ..core.result import FAILED, SUSPECT

TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}

LLMS_TXT = """# nrf-workbench

A bench service that gives an AI hands and eyes on Nordic nRF and Espressif ESP32 boards over HTTP.

- Read GET /api/guide first. It explains every call, every result code and every command this host has.
- GET /api/openapi.json is the same as OpenAPI 3.1.
- GET /api/commands is the raw command list; GET /api/capabilities says why a command is absent here.
- The guide needs the same access as the rest of the API (a bearer token, unless this host is open).
"""


def _example_value(arg):
    if arg.type == "int":
        return 1
    if arg.type == "float":
        return 1.0
    if arg.type == "bool":
        return True
    return "<%s>" % arg.name


def _curl(command, base):
    args = [a for a in command.args if a.required]
    url = base + http_path(command.name)
    if command.effect == "read":
        query = "&".join("%s=%s" % (a.name, _example_value(a)) for a in args)
        return "curl -s '%s%s'" % (url, "?" + query if query else "")
    if command.body == "raw":
        query = "&".join("%s=%s" % (a.name, _example_value(a)) for a in args)
        return "curl -s -X POST '%s%s' --data-binary @file -H 'Content-Type: application/octet-stream'" % (
            url, "?" + query if query else "")
    body = ", ".join('"%s": %s' % (a.name, _json_literal(_example_value(a))) for a in args)
    return "curl -s -X POST %s -H 'Content-Type: application/json' -d '{%s}'" % (url, body)


def _json_literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"%s"' % v


def _areas(commands):
    out = {}
    for c in commands:
        out.setdefault(c.name.split(".")[0], []).append(c)
    return out


def index(registry, capabilities):
    commands = registry.commands(capabilities)
    return {
        "name": "nrf-workbench",
        "read_this_first": "/api/guide",
        "openapi": "/api/openapi.json",
        "commands": "/api/commands",
        "capabilities": "/api/capabilities",
        "streams": "/api/streams",
        "how": {
            "read": "GET /api/<area>/<command>, arguments as query parameters",
            "write": "POST /api/<area>/<command>, arguments as a JSON object body (an upload sends the file as the body and the arguments as query parameters)",
            "result": "every command answers with the same Result: ok, class, code, message, data, evidence",
            "auth": "Authorization: Bearer <token>, unless this host's access is open",
        },
        "areas": dict((a, sorted(c.name for c in cs)) for a, cs in sorted(_areas(commands).items())),
    }


def _result_schema():
    return {"type": "object", "required": ["ok", "class", "code", "message"], "properties": {
        "ok": {"type": "boolean"},
        "class": {"type": "string", "enum": ["ok", "refused", "failed", "suspect"]},
        "code": {"type": "string", "description": "a closed list; see /api/guide, 'Result codes'"},
        "message": {"type": "string"},
        "data": {"description": "what the command returned"},
        "evidence": {"type": "object", "description": "facts behind a refusal or a failure, such as the whole tool output"}}}


def openapi(registry, capabilities, title="nrf-workbench"):
    paths = {}
    for c in registry.commands(capabilities):
        if c.effect == "stream":
            continue
        params = [{"name": a.name, "in": "query", "required": a.required, "schema": {"type": TYPES[a.type]},
                  "description": (a.doc + (" Unit: %s." % a.unit if a.unit else "")).strip()} for a in c.args]
        op = {"operationId": c.name.replace(".", "_"), "summary": c.summary,
              "description": "Result data: %s." % c.result if c.result else c.summary,
              "tags": [c.name.split(".")[0]], "responses": {
                  "200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}},
                  "400": {"description": "a bad argument (never a Result)"},
                  "default": {"description": "a refusal, failure or suspect result: the same Result, with the status from the code",
                              "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}}}}
        if c.effect == "read":
            op["parameters"] = params
            method = "get"
        elif c.body == "raw":
            op["parameters"] = params
            op["requestBody"] = {"required": True, "description": "the file, with its Content-Length",
                                 "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
            method = "post"
        else:
            props = dict((a.name, {"type": TYPES[a.type], "description": (a.doc + (" Unit: %s." % a.unit if a.unit else "")).strip()})
                         for a in c.args)
            op["requestBody"] = {"required": bool(c.args), "content": {"application/json": {"schema": {
                "type": "object", "properties": props, "required": [a.name for a in c.args if a.required],
                "additionalProperties": False}}}}
            method = "post"
        paths[http_path(c.name)] = {method: op}
    return {"openapi": "3.1.0", "info": {"title": title, "version": "1",
                                          "description": "Generated from the service's command registry. Read /api/guide for the conventions."},
            "paths": paths,
            "components": {"schemas": {"Result": _result_schema()},
                           "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
            "security": [{"bearer": []}, {}]}


def _arg_line(a):
    bits = ["`%s` (%s%s%s)" % (a.name, a.type, ", required" if a.required else ", optional", ", " + a.unit if a.unit else "")]
    return "  - " + bits[0] + (": " + a.doc if a.doc else "")


def guide(registry, capabilities, base="http://<host>"):
    commands = registry.commands(capabilities)
    absent = [(c, registry.unmet(c, capabilities)) for c in registry.commands() if registry.unmet(c, capabilities)]
    out = []
    w = out.append
    w("# nrf-workbench: how to use it\n")
    w("A bench service on a Linux host (a Raspberry Pi) that gives you hands and eyes on Nordic nRF and Espressif ESP32 boards: "
      "see what is attached, flash it, reset it, read its serial console, and (where built) drive the host's pins. Every front "
      "end (this HTTP API, the CLI and the MCP server) calls the same commands and returns the same Result.\n")
    w("## Connect\n")
    w("- Base URL: `%s`. Every path below is relative to it." % base)
    w("- Access: `Authorization: Bearer <token>` on every request, unless this host's `GET /api/session` says `login_required: false` "
      "(an open host: no token on this network). A browser also needs its own `Origin` to be this host.")
    w("- JSON in, JSON out. A bad argument is HTTP 400 with `{ok:false, error:\"usage\", message, arg}` and is **not** a Result.\n")
    w("## Calling a command\n")
    w("- **read** commands: `GET /api/<area>/<command>?name=value&...`")
    w("- **write** commands: `POST /api/<area>/<command>` with a JSON object body, `Content-Type: application/json`")
    w("- **upload** commands (`body: raw`): `POST` the file as the body with a `Content-Length`, the other arguments as query "
      "parameters. Never base64.")
    w("- A command this host cannot run is **absent**: 404. `GET /api/capabilities` lists what is present and, for each absent "
      "command, what it needs. `GET /api/` (this index) and `GET /api/commands` are the live lists.")
    w("- `GET /api/<area>` alone is not a route; the area is a prefix.\n")
    w("## The Result (every command answers with one)\n")
    w("```\n{ \"ok\": true|false, \"class\": \"ok|refused|failed|suspect\", \"code\": \"...\", \"message\": \"...\", "
      "\"data\": {...}, \"evidence\": {...} }\n```")
    w("- **ok** (HTTP 200, CLI exit 0): it was done and judged good.")
    w("- **refused** (HTTP 409/404/422 by code, exit 1): **nothing was attempted and nothing changed.** The code says why; fix the "
      "cause and call again.")
    w("- **failed** (HTTP 500, exit 3): it was attempted and went wrong. The whole tool output is in `evidence`. It is **not retried** "
      "for you, and you should read it before trying again.")
    w("- **suspect** (HTTP 500, exit 4): it finished but the evidence says do not trust it (for example a flash that verified "
      "fewer parts than it wrote). Treat the board's state as unknown.")
    w("- Output is never tailed or summarised: you get the whole thing.\n")
    w("## Rules that shape how you call it\n")
    w("- **Boards are named by their USB serial**, never by a /dev path. `devices.list` shows what is attached; a name a person gave "
      "a board is a label only.")
    w("- **Every wait has a bound you supply**, in seconds, derived from the job (size / speed). A bound is a ceiling for a "
      "failure, not a sleep: the call returns the moment the job is done.")
    w("- **One attempt.** Nothing retries a flash or a write. After a failure, look at the evidence, then decide.")
    w("- **Receive-only by default.** A serial link only listens until it has a wired TX and a measured voltage; a send on one that "
      "has neither is refused (`link_rx_only`, `voltage_unverified`). Pins are never driven without a stated voltage of 3.3 V or "
      "less (`voltage_unsafe`), and a pin or adapter belongs to one claim only (`pin_taken`, `adapter_taken`).")
    w("- **A unit can be `protected`** (mid-measurement): a flash or reset of it is refused.")
    w("- **A reset or a flash resets the board** over the adapter, and the console's port on that adapter is released for the run and "
      "reopened after; the first lines printed after a reset can be lost.")
    w("- **nRF boards** are flashed with a DFU `.zip` (`firmware.put`, then `programmer.flash`); **ESP32 boards** are not recognised "
      "from a USB id (a bridge chip says nothing about what is behind it), so they are *declared* first (`esp32.declare`), then "
      "flashed (`esp32.flash`) from a whole-flash `.bin` or a zip with `flasher_args.json`.\n")
    w("## Result codes\n")
    w("| code | class | HTTP |\n|---|---|---|")
    for code, cls in sorted(status.CODES.items()):
        if code == "ok":
            continue
        http ={FAILED: 500, SUSPECT: 500}.get(cls) or status.REFUSED_HTTP.get(code, 409)
        w("| `%s` | %s | %s |" % (code, cls, "CLI only" if code in status.CLI_ONLY else http))
    w("\nWhat each code means is in `docs/specs/core.md` in the repository; `message` and `evidence` say it for the call you made.\n")
    w("## Commands on this host (%d)\n" % len(commands))
    for area, cs in sorted(_areas(commands).items()):
        w("### %s\n" % area)
        for c in sorted(cs, key=lambda c: c.name):
            names = c.to_dict()                               # the CLI words and the MCP tool name, derived once, in the registry
            w("**`%s`**  %s `%s`  (CLI `%s`, MCP tool `%s`)" % (c.name, c.effect.upper() if c.effect != "stream" else "STREAM",
                                                                   http_path(c.name), names["cli"], names["mcp"]))
            w("\n%s" % c.summary)
            if c.args:
                w("\nArguments:")
                for a in c.args:
                    w(_arg_line(a))
            if c.result:
                w("\nReturns: %s." % c.result)
            if c.effect != "stream":
                w("\n```\n%s\n```\n" % _curl(c, base))
    if absent:
        w("## Commands this host does not have\n")
        w("They are 404 here. `unmet` is what each needs.\n")
        for c, unmet in absent:
            w("- `%s`: needs %s" % (c.name, ", ".join(unmet)))
        w("")
    w("## Live data\n")
    w("- `GET /api/console/since?link=<name>&cursor=<n>` pages a serial link's log by byte cursor.")
    w("- `GET /api/streams` says which retained streams exist; `GET /api/activity?cursor=<n>` is every command anyone ran.")
    w("- `WS /ws/events` pushes device, activity and console changes (what the dashboard uses).\n")
    w("## Other front ends\n")
    w("- **CLI**: the same commands as words, `nrfwb <area> <command> --arg value`; `--json` prints the Result.")
    w("- **MCP** (stdio, for an agent with MCP): `python -m nrf_workbench.api.mcp_server --host %s [--token-file FILE]`. One tool per "
      "command, named `<area>_<command>`, built from this same list.\n" % base)
    w("## A first session\n")
    w("1. `GET /api/capabilities`, then `GET /api/devices/list`: what is here, what is attached.")
    w("2. Read before you act: `console.links` and `console.since` show what a board is already printing.")
    w("3. To flash: upload (`firmware.put`), then flash by the returned `sha256` with a bound you derived; read the whole Result.")
    w("4. After any write, check the board did what you meant (its console, its `devices.list` state), not just `ok`.")
    return "\n".join(out) + "\n"
