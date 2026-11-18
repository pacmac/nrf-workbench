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
import time

import anyio
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route, WebSocketRoute

from ..core import status
from ..core.events import Streams
from ..core.registry import Context, UnknownCommand, UsageError, http_path
from . import docs, feed
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


def build_app(registry, host, auth, capabilities, streams=None, extra_routes=()):
    """`capabilities` is what this host has; commands needing more are absent. `streams` (core.events.Streams)
    holds the retained streams: with an activity log, EVERY command a caller runs is recorded in it.
    `extra_routes` are Starlette routes the composition root mounts (the dashboard's): this module never imports
    the package that made them."""
    capabilities = frozenset(capabilities)
    activity = streams.activity if streams is not None else None

    def make_endpoint(command):
        async def endpoint(request):
            allowed, caller = auth.authorise(request.headers, command.effect)
            if not allowed:
                return _unauthorized()
            started = time.monotonic()
            try:
                args, from_text, body = await _arguments(command, request)
            except _BadRequest as exc:
                if activity is not None:
                    activity.record_usage(caller, command.name, {}, str(exc), time.monotonic() - started)
                return _error(400, "usage", str(exc))
            ctx = Context(host, caller, body)
            run = None
            try:
                typed = registry.validate(command, args, from_text)
                recorded = dict((name, typed[name]) for name in args)    # what the caller SENT, typed: not query text
                if activity is not None and command.effect == "write":
                    run = activity.begin(caller, command.name, recorded)  # announced only once it will really run
            except UsageError:
                recorded = args                                          # the call below raises the same error
            call = functools.partial(registry.call, command.name, args, ctx, capabilities, from_text)
            ended = False
            try:
                try:
                    result = await anyio.to_thread.run_sync(call)
                except UsageError as exc:
                    ended = True
                    if activity is not None:
                        activity.record_usage(caller, command.name, args, str(exc), time.monotonic() - started, run=run)
                    return _error(400, "usage", str(exc), arg=exc.arg)
                except UnknownCommand:
                    ended = True
                    if run is not None:
                        activity.record_usage(caller, command.name, args, "no such command here",
                                              time.monotonic() - started, run=run)
                    return _error(404, "not_found", "no such command here")
                except Exception as exc:                # a handler bug: say so, leak no traceback
                    ended = True
                    log.exception("command %s raised", command.name)
                    if activity is not None:
                        activity.record_error(caller, command.name, recorded,
                                              "the command raised %s" % type(exc).__name__,
                                              time.monotonic() - started, run=run)
                    return _error(500, "internal", "the command raised an unexpected error (%s)" % type(exc).__name__)
                ended = True
                if activity is not None:
                    activity.record(caller, command.name, recorded, result, time.monotonic() - started, run=run)
                return JSONResponse(result.to_dict(), status_code=status.http_status(result) or 500)
            finally:
                if run is not None and not ended:       # a cancelled request: never leave a start with no end
                    activity.record_error(caller, command.name, recorded, "the request ended before the command reported",
                                          time.monotonic() - started, run=run)

        return endpoint

    async def get_activity(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        if activity is None:
            reason = (streams.absent.get("activity") if streams is not None else None) or "no activity log is configured"
            return _error(404, "not_found", "the activity log is absent here: %s" % reason)
        try:
            args = _query_args(request)
            cursor = int(args.get("cursor", "0"))
            page = activity.since(cursor, args.get("epoch") or None)
        except (_BadRequest, ValueError) as exc:
            return _error(400, "usage", "cursor must be a whole number, 0 or more (%s)" % exc)
        return JSONResponse(page.to_dict())

    async def get_streams(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        return JSONResponse((streams or Streams()).to_dict())

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

    def _base(request):
        return str(request.base_url).rstrip("/")

    async def api_index(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        return JSONResponse(docs.index(registry, capabilities))

    async def api_openapi(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        doc = docs.openapi(registry, capabilities)
        doc["servers"] = [{"url": _base(request)}]
        return JSONResponse(doc)

    async def api_guide(request):
        allowed, _caller = auth.authorise(request.headers, "read")
        if not allowed:
            return _unauthorized()
        return PlainTextResponse(docs.guide(registry, capabilities, _base(request)), media_type="text/markdown; charset=utf-8")

    async def llms_txt(request):
        return PlainTextResponse(docs.LLMS_TXT, media_type="text/plain; charset=utf-8")     # a pointer: public, names nothing of the host

    routes = [Route("/api", api_index, methods=["GET"]),
              Route("/api/", api_index, methods=["GET"]),
              Route("/api/guide", api_guide, methods=["GET"]),
              Route("/api/openapi.json", api_openapi, methods=["GET"]),
              Route("/llms.txt", llms_txt, methods=["GET"]),
              Route("/api/commands", list_commands, methods=["GET"]),
              Route("/api/capabilities", list_capabilities, methods=["GET"]),
              Route("/api/streams", get_streams, methods=["GET"]),
              Route("/api/activity", get_activity, methods=["GET"])]
    feeds = feed.feeds_of(streams)
    if feeds:
        routes.append(WebSocketRoute("/ws/events", feed.make_endpoint(feeds, auth)))
    routes.extend(extra_routes)
    for command in registry.commands(capabilities):
        if command.effect == "stream":
            continue                                    # a websocket, not an HTTP route (not built here)
        method = "GET" if command.effect == "read" else "POST"
        routes.append(Route(http_path(command.name), make_endpoint(command), methods=[method]))

    async def http_error(request, exc):
        extra = {}
        if exc.status_code == 404 and request.url.path.startswith("/api"):
            # a client that guessed a route is told where the real ones are listed, not left with a bare "Not Found"
            extra["see"] = "GET /api/guide explains every call; GET /api/commands lists them; routes are /api/<area>/<command>"
        return _error(exc.status_code, "http", exc.detail, **extra)

    return Starlette(routes=routes, exception_handlers={HTTPException: http_error})
