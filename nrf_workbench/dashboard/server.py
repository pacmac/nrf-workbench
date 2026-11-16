"""The dashboard's server side (docs/specs/dashboard.md): the page at /, its assets, and its own status.

  GET /                        the UI's index.html if it has been built, else a plain page that SAYS it has not
  GET /assets/<path>           a file of the built UI (never anything outside its folder)
  GET /api/dashboard/status    what this dashboard is and what it can WRITE: nothing (it has no write route)

**The bench name is put into the page by the server, before any JavaScript runs.** It is right even when the
API is wedged, which is exactly when someone with two tabs open needs to know which bench they are on. It is
HTML-escaped: it comes from configuration or the machine's host name and lands inside a <title>.

The page and the assets are public: they are static and carry no data (a page load cannot carry a bearer
header). Every byte of live data comes through the authenticated API and feed. The status route is a read and
honours the same authorisation as any other read, injected as `authorise` so this package never imports `api`.

The dashboard has NO write route of its own; every action it will offer calls a registry command through the
same API (dashboard.md, "how it writes"). WRITE_ROUTES is the claim, and a test scans this file to check it.
"""
import html
import json
import os

from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from ..core.sessions import same_origin

PLACEHOLDER = "{{BENCH_NAME}}"
COOKIE_NAME = "nrfwb_session"
CSRF_HEADER = "x-csrf-token"
# What the dashboard can WRITE. The status route reports this and a test checks it against the source. It is
# session handling ONLY: nothing here touches hardware or a file, and every action the UI offers goes through a
# registry command on the same API, so it meets every guard exactly as a CLI call does.
WRITE_ROUTES = ("POST /login", "POST /logout")


class Login(object):
    """What the login routes need, handed over by the composition root so this package never imports `api`."""

    def __init__(self, required, check_token, sessions, access):
        self.required, self.check_token, self.sessions, self.access = required, check_token, sessions, access

NOT_BUILT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{BENCH_NAME}} · nrf-workbench</title>
<style>
  :root { color-scheme: light dark; --fg: #1d2330; --bg: #f6f7f9; --muted: #5a6475; --card: #ffffff; --line: #d9dde5; --accent: #1b5fd1; }
  @media (prefers-color-scheme: dark) { :root { --fg: #e6e9ef; --bg: #14171d; --muted: #9aa4b5; --card: #1c2028; --line: #2c323d; --accent: #6aa0ff; } }
  html { font-size: 16px; }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 1rem/1.5 system-ui, sans-serif; }
  main { max-width: 42rem; margin: 0 auto; padding: 2rem 1rem; }
  h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
  p { margin: .5rem 0; }
  .muted { color: var(--muted); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: .5rem; padding: 1rem; margin: 1rem 0; }
  a { color: var(--accent); }
  code { font-family: ui-monospace, monospace; font-size: .95em; }
  ul { margin: .5rem 0; padding-left: 1.25rem; }
</style>
</head>
<body>
<main>
  <h1>{{BENCH_NAME}}</h1>
  <p class="muted">nrf-workbench</p>
  <div class="card">
    <p><strong>The service is running. The dashboard UI has not been built on this host.</strong></p>
    <p>This page is all there is at <code>/</code> until the UI is built. Nothing is hidden: the API, the live feed
    and the activity log below are real and answer now.</p>
  </div>
  <div class="card">
    <p>What you can open:</p>
    <ul>
      <li><a href="/api/commands">/api/commands</a>: every command, with its arguments</li>
      <li><a href="/api/capabilities">/api/capabilities</a>: what this host has, and why a command is absent</li>
      <li><a href="/api/streams">/api/streams</a>: which live streams are on, and why one is off</li>
      <li><a href="/api/activity">/api/activity</a>: what every caller has done</li>
      <li><a href="/api/devices/list">/api/devices/list</a>: the boards attached right now</li>
      <li><a href="/api/dashboard/status">/api/dashboard/status</a>: what this dashboard is</li>
    </ul>
    <p class="muted">If the service has a token, these answer 401 without it. The live feed is a websocket at
    <code>/ws/events</code>.</p>
  </div>
</main>
</body>
</html>
"""


def substitute(page, bench_name):
    """The bench name into the page, escaped. Done on the server, so it is there before any script runs."""
    return page.replace(PLACEHOLDER, html.escape(bench_name, quote=True))


def _error(code, kind, message):
    return JSONResponse({"ok": False, "error": kind, "message": message}, status_code=code)


def routes(bench_name, static_dir, authorise=None, login=None):
    """The dashboard's routes. `static_dir` holds the built UI (index.html, assets/); it may not exist yet.
    `login` (a Login) turns on POST /login, POST /logout and GET /api/session."""
    index_path = os.path.join(static_dir, "index.html") if static_dir else None
    assets_dir = os.path.join(static_dir, "assets") if static_dir else None

    def built():
        return bool(index_path) and os.path.isfile(index_path)

    async def index(request):
        if built():
            with open(index_path, "r", encoding="utf-8") as fh:
                page = fh.read()
        else:
            page = NOT_BUILT
        return HTMLResponse(substitute(page, bench_name), headers={"Cache-Control": "no-store"})

    async def asset(request):
        rel = request.path_params["path"]
        if not assets_dir or "\0" in rel:
            return Response(status_code=404)
        root = os.path.realpath(assets_dir)
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return Response(status_code=404)
        return FileResponse(full)

    async def status(request):
        if authorise is not None:
            allowed, _caller = authorise(request.headers, "read")
            if not allowed:
                return JSONResponse({"ok": False, "error": "unauthorized", "message": "a valid bearer token is required"},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return JSONResponse({"bench": bench_name, "ui_built": built(),
                             "write_routes": list(WRITE_ROUTES) if login is not None else []})

    def set_cookie(response, request, sid):
        response.set_cookie(COOKIE_NAME, sid, max_age=int(login.sessions.max_age_s), path="/", httponly=True,
                            samesite="strict", secure=request.url.scheme == "https")

    def clear_cookie(response, request):
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict",
                               secure=request.url.scheme == "https")

    async def do_login(request):
        if not same_origin(request.headers):
            return _error(403, "forbidden_origin", "a login must come from this site")
        if not login.required:
            return JSONResponse({"ok": True, "login_required": False})
        try:
            candidate = json.loads(await request.body()).get("token")
        except (ValueError, AttributeError):
            return _error(400, "usage", 'send {"token": "<the service token>"}')
        if not login.check_token(candidate):
            return _error(401, "unauthorized", "that token is not right")
        old = request.cookies.get(COOKIE_NAME)
        if old:
            login.sessions.destroy(old)                       # a login never inherits an old session
        session = login.sessions.create()
        response = JSONResponse({"ok": True, "login_required": True, "csrf": session.csrf})
        set_cookie(response, request, session.sid)
        return response

    async def do_logout(request):
        sid = request.cookies.get(COOKIE_NAME)
        if sid and login.sessions.get(sid) is not None:
            allowed, _caller = authorise(request.headers, "write") if authorise else (False, None)
            if not allowed:
                return _error(403, "forbidden", "a logout needs the session's CSRF header and this site as its origin")
            login.sessions.destroy(sid)
        response = JSONResponse({"ok": True})
        clear_cookie(response, request)
        return response

    async def session_info(request):
        info = {"login_required": login.required, "access": login.access, "authenticated": False, "caller": None,
                "csrf": None, "secure_transport": request.url.scheme == "https"}
        if authorise is not None:
            allowed, caller = authorise(request.headers, "read")
            info["authenticated"], info["caller"] = bool(allowed), caller
        sid = request.cookies.get(COOKIE_NAME)
        session = login.sessions.get(sid) if sid else None
        if session is not None and info["caller"] == "dashboard":
            info["csrf"] = session.csrf          # given only to a request that already holds the session
        return JSONResponse(info, headers={"Cache-Control": "no-store"})

    found = [Route("/", index, methods=["GET"]),
             Route("/assets/{path:path}", asset, methods=["GET"]),
             Route("/api/dashboard/status", status, methods=["GET"])]
    if login is not None:
        found += [Route("/login", do_login, methods=["POST"]),
                  Route("/logout", do_logout, methods=["POST"]),
                  Route("/api/session", session_info, methods=["GET"])]
    return found
