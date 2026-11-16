"""The MCP server (docs/specs/api.md): a thin client of the HTTP service.

It holds NO tool table. Its tools are registered, at every `list_tools`, from the service's own command
list (GET /api/commands), so a command added to an area appears here with no edit to this file, and a
command the host cannot run is absent. One tool per command, named `<area>_<command>`.

  * Arguments are validated by the registry's own validator (Command.from_row), as JSON is: CHECKED, not
    parsed. A bad argument is an error result and nothing is sent to the service.
  * An upload tool takes `path`, a file on the machine THIS server runs on (the MCP server runs where the
    build output is), and streams it to the service. The AI never reads or encodes the bytes, which an AI
    could not do for a 332 KB image. The SHA-256 is computed and sent, so a corrupt transfer is caught.
  * The Result comes back WHOLE (text and structured), `is_error` true unless it is ok. Nothing is
    summarised or tailed.
  * The token is read from a file or the environment, never an argument, and is never in any result.

The stdio transport owns stdout: this module never prints to it.
"""
import argparse
import functools
import json
import os
import sys

import anyio
from mcp import MCPError
from mcp import types as t
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from ..core.registry import Command, Registry, UsageError
from ..core.remote import Remote, RemoteError, file_sha256, token_from
from ..core.result import Result

_JSON_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
_EFFECT = {"read": "reads only", "write": "changes hardware or files", "stream": "streams"}


def input_schema(row):
    """The JSON Schema for a command's arguments, from its row."""
    props, required = {}, []
    for a in row["args"]:
        text = a.get("doc") or ""
        if a.get("unit"):
            text = (text + " " if text else "") + "(unit: %s)" % a["unit"]
        prop = {"type": _JSON_TYPES[a["type"]], "description": text}
        if not a["required"] and a.get("default") is not None:
            prop["default"] = a["default"]
        props[a["name"]] = prop
        if a["required"]:
            required.append(a["name"])
    if row.get("body") == "raw":
        if "path" in props:
            raise ValueError("%s has an argument named 'path', which an upload tool reserves" % row["name"])
        props["path"] = {"type": "string", "description": "the file to upload, on the machine THIS MCP server runs on; "
                                                           "it is streamed to the service"}
        required.insert(0, "path")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def tool_for(row):
    description = row["summary"] + " [" + _EFFECT.get(row["effect"], row["effect"]) + "]"
    if row.get("result"):
        description += " Result data: " + row["result"]
    return t.Tool(name=row["mcp"], description=description, input_schema=input_schema(row),
                  annotations=t.ToolAnnotations(read_only_hint=row["effect"] == "read"))


def _content(body, is_error):
    return t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps(body, indent=2, sort_keys=True))],
                            structured_content=body, is_error=is_error)


def _error(kind, message, **extra):
    body = {"ok": False, "error": kind, "message": message}
    body.update(extra)
    return _content(body, True)


class Bridge(object):
    def __init__(self, remote):
        self.remote = remote
        self.rows = {}

    async def _refresh(self):
        """Fetch the service's command list. RemoteError if it cannot be had."""
        rows = await anyio.to_thread.run_sync(self.remote.commands)
        self.rows = dict((r["mcp"], r) for r in rows if r["effect"] != "stream")
        return self.rows

    def _remote_failure(self, exc):
        """A failure to get a Result from the service, as a tool result (never a protocol exception)."""
        if exc.kind == "unreachable":
            return _content(Result.refused("service_not_running", str(exc),
                                           {"address": self.remote.base_url}).to_dict(), True)
        return _error(exc.kind, str(exc))

    async def on_list_tools(self, ctx, params):
        try:
            rows = await self._refresh()
        except RemoteError as exc:
            # A listing has nothing sensible to return, so this one raises. It must be an MCPError: the SDK
            # masks any other exception as "Internal server error", and the AI would learn nothing.
            raise MCPError(t.INTERNAL_ERROR, "cannot list the service's commands (%s): %s" % (exc.kind, exc),
                           {"kind": exc.kind})
        return t.ListToolsResult(tools=[tool_for(r) for r in rows.values()])

    async def on_call_tool(self, ctx, params):
        args = dict(params.arguments or {})
        row = self.rows.get(params.name)
        if row is None:
            try:
                row = (await self._refresh()).get(params.name)         # a command added after the last listing
            except RemoteError as exc:
                return self._remote_failure(exc)
        if row is None:
            return _error("unknown_tool", "the service offers no tool named %s" % params.name)
        path = None
        if row.get("body") == "raw":
            path = args.pop("path", None)
            if not isinstance(path, str) or not path:
                return _error("usage", "%s takes the file to upload: path" % params.name, arg="path")
            if not os.path.isfile(path):
                return _error("usage", "path %s is not a file on the machine this MCP server runs on" % path, arg="path")
        try:
            typed = Registry().validate(Command.from_row(row), args, from_text=False)      # checked, not parsed
        except UsageError as exc:
            return _error("usage", str(exc), arg=exc.arg)
        try:
            if path is not None and any(a["name"] == "sha256" for a in row["args"]) and typed.get("sha256") is None:
                typed["sha256"] = await anyio.to_thread.run_sync(file_sha256, path)
            result = await anyio.to_thread.run_sync(functools.partial(self.remote.call, row, typed, path))
        except RemoteError as exc:
            return self._remote_failure(exc)
        except OSError as exc:
            return _error("usage", "the file could not be read: %s" % exc, arg="path")
        return _content(result.to_dict(), not result.ok)


def build_server(remote):
    bridge = Bridge(remote)
    return Server("nrf-workbench", on_list_tools=bridge.on_list_tools, on_call_tool=bridge.on_call_tool)


async def serve(server):
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv=None, env=None, run=None):
    env = os.environ if env is None else env
    p = argparse.ArgumentParser(prog="python -m nrf_workbench.api.mcp_server",
                                description="MCP server for nrf-workbench (stdio). Its tools come from the service's "
                                            "own command list. The token is read from --token-file, NRFWB_TOKEN_FILE "
                                            "or NRFWB_TOKEN, never from a command-line value.")
    p.add_argument("--host", help="the service address, http://host:port (or NRFWB_HOST)")
    p.add_argument("--token-file", dest="token_file", help="a file holding the service token")
    args = p.parse_args(argv)
    host = args.host or env.get("NRFWB_HOST")
    if not host:
        print("usage: no service address: pass --host or set NRFWB_HOST", file=sys.stderr)
        return 2
    try:
        remote = Remote(host, token_from(args.token_file, env))
    except ValueError as exc:
        print("usage: %s" % exc, file=sys.stderr)
        return 2
    (run or anyio.run)(serve, build_server(remote))
    return 0


if __name__ == "__main__":
    sys.exit(main())
