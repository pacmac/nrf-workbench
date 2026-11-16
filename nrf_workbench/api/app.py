"""The HTTP application, generated from the core registry (docs/specs/api.md).

It holds no command of its own. For each command the registry says is present on this host:

  read    GET  /api/<area>/<command>   arguments as query parameters
  write   POST /api/<area>/<command>   arguments as a JSON object body, OR, for a `body: raw` command
                                       (an upload), as query parameters with the FILE as the body

and the response body of every command is the unmodified Result, with the HTTP status from
core.status (one mapping, shared with the CLI). A bad argument is a 400 and is never a Result.
A command whose requirements are unmet is absent: a 404 here, and `GET /api/capabilities` says why.

Handlers are synchronous and may take tens of seconds (a flash), so each runs in a worker thread and
never blocks the event loop. A raw upload is NOT read into memory: the handler is given a `read(n)`
over the request (api.body) and the declared Content-Length.
"""
import functools
import json
import logging

import anyio
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..core import status
from ..core.registry import Context, UnknownCommand, UsageError, http_path
from .body import RequestBody

log = logging.getLogger("nrf_workbench.api")


class _BadRequest(Exception):
    pass


def _error(code, kind, message, **extra):
    body = {"ok": False, "error": kind, "message": message}
    body.update(extra)
    return JSONResponse(body, status_code=code)


def _unauthorized():
    return JSONResponse({"ok": False, "error": "unauthorized", "message": "a valid bearer token is required"},
                        status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _query_args(request):
    items = list(request.query_params.multi_items())
    keys = [k for k, _ in items]
    if len(set(keys)) != len(keys):
        raise _BadRequest("a query parameter was repeated")
    return dict(items)


def _declared_length(request):
    value = request.headers.get("content-length")
    if value is None or not value.isdigit():
        return None
    return int(value)


async def _arguments(command, request):
    """(args, from_text, body) for this request, or _BadRequest."""
    if command.effect == "read":
        return _query_args(request), True, None
    if command.body == "raw":
        return _query_args(request), True, RequestBody(request, _declared_length(request))
    if request.query_params:
        raise _BadRequest("this command takes its arguments as a JSON object in the body, not in the query")
    raw = await request.body()
    if not raw.strip():
        return {}, False, None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise _BadRequest("the body is not valid JSON: %s" % exc)
    if not isinstance(data, dict):
        raise _BadRequest("the body must be a JSON object")
    return data, False, None


def build_app(registry, host, auth, capabilities):
    """`capabilities` is what this host has; commands needing more are absent."""
    capabilities = frozenset(capabilities)

    def make_endpoint(command):
        async def endpoint(request):
            allowed, caller = auth.authorise(request.headers, command.effect)
            if not allowed:
                return _unauthorized()
            try:
                args, from_text, body = await _arguments(command, request)
            except _BadRequest as exc:
                return _error(400, "usage", str(exc))
            ctx = Context(host, caller, body)
            call = functools.partial(registry.call, command.name, args, ctx, capabilities, from_text)
            try:
                result = await anyio.to_thread.run_sync(call)
            except UsageError as exc:
                return _error(400, "usage", str(exc), arg=exc.arg)
            except UnknownCommand:
                return _error(404, "not_found", "no such command here")
            except Exception as exc:                    # a handler bug: say so, leak no traceback
                log.exception("command %s raised", command.name)
                return _error(500, "internal", "the command raised an unexpected error (%s)" % type(exc).__name__)
            return JSONResponse(result.to_dict(), status_code=status.http_status(result) or 500)

        return endpoint

    async def list_commands(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        return JSONResponse({"commands": [c.to_dict() for c in registry.commands(capabilities)]})

    async def list_capabilities(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        absent = [{"name": c.name, "unmet": registry.unmet(c, capabilities)}
                  for c in registry.commands() if registry.unmet(c, capabilities)]
        return JSONResponse({"present": sorted(capabilities), "absent": absent})

    routes = [Route("/api/commands", list_commands, methods=["GET"]),
              Route("/api/capabilities", list_capabilities, methods=["GET"])]
    for command in registry.commands(capabilities):
        if command.effect == "stream":
            continue                                    # a websocket, not an HTTP route (not built here)
        method = "GET" if command.effect == "read" else "POST"
        routes.append(Route(http_path(command.name), make_endpoint(command), methods=[method]))

    async def http_error(request, exc):
        return _error(exc.status_code, "http", exc.detail)

    return Starlette(routes=routes, exception_handlers={HTTPException: http_error})
