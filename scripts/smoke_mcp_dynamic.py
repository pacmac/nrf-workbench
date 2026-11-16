"""smoke_mcp_dynamic.py - can the MCP SDK register tools from DATA at runtime, and call them?

Registry rows are plain dicts, as nrf-workbench's registry would give them. The server holds no
per-tool Python function: list_tools is built from the rows and call_tool dispatches by name.
Proves a row added AFTER startup appears on the next list_tools, a JSON Schema is kept verbatim,
and a refused Result comes back whole with is_error true. In-process client; stdio and HTTP
transports are the SDK's own and are not exercised.

Needs the `mcp` package (scripts/try_stack.sh installs it into a throwaway venv). Exits non-zero
on the first failed check and prints one RESULT line.
"""
import asyncio
import json
import sys

from mcp import Client
from mcp import types as t
from mcp.server.lowlevel import Server

ROWS = {
    "programmer_status": {
        "summary": "print the board state",
        "schema": {"type": "object", "properties": {"serial": {"type": "string"}}, "required": []},
        "handler": lambda a: {"ok": True, "class": "ok", "code": "ok",
                              "data": {"state": "app", "serial": a.get("serial")}},
    },
    "programmer_flash_file": {
        "summary": "write a prebuilt DFU zip",
        "schema": {"type": "object",
                   "properties": {"file": {"type": "string"}, "serial": {"type": "string"},
                                  "bound_s": {"type": "number"}},
                   "required": ["file", "serial", "bound_s"]},
        "handler": lambda a: {"ok": False, "class": "refused", "code": "not_in_bootloader",
                              "evidence": {"state": "app"}},
    },
}


async def on_list_tools(ctx, params):
    return t.ListToolsResult(
        tools=[t.Tool(name=n, description=r["summary"], inputSchema=r["schema"]) for n, r in ROWS.items()])


async def on_call_tool(ctx, params):
    row = ROWS.get(params.name)
    if row is None:
        return t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps({"code": "capability_missing"}))],
                                isError=True)
    result = row["handler"](params.arguments or {})
    return t.CallToolResult(content=[t.TextContent(type="text", text=json.dumps(result))],
                            isError=not result["ok"])


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        print("RESULT FAIL")
        sys.exit(1)


async def main():
    server = Server("nrf-workbench-smoke", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    async with Client(server) as c:
        names = [x.name for x in (await c.list_tools()).tools]
        check(names == ["programmer_status", "programmer_flash_file"], "tools built from data rows: %s" % names)
        r = await c.call_tool("programmer_status", {"serial": "ABC"})
        check(json.loads(r.content[0].text)["data"]["serial"] == "ABC" and not r.is_error, "call returns the Result, is_error false")
        r = await c.call_tool("programmer_flash_file", {"file": "x.zip", "serial": "ABC", "bound_s": 1.0})
        check(r.is_error and json.loads(r.content[0].text)["code"] == "not_in_bootloader",
              "a refusal returns whole with is_error true")
        ROWS["swd_reset"] = {"summary": "added after startup", "schema": {"type": "object", "properties": {}},
                             "handler": lambda a: {"ok": True, "class": "ok", "code": "ok"}}
        tools = (await c.list_tools()).tools
        check("swd_reset" in [x.name for x in tools], "a row added at runtime appears on the next list_tools")
        schema = {x.name: x.input_schema for x in tools}["programmer_flash_file"]
        check(schema["required"] == ["file", "serial", "bound_s"], "JSON Schema kept verbatim")
    print("RESULT OK")


asyncio.run(main())
