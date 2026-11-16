# API — functional spec (DRAFT, not agreed)

The service surface: HTTP, websocket and MCP, all generated from the `core` registry. Inputs:
`core.md`, `architecture.md`, `devices.md`, and the upstream design in the brief (section 2).
Tags: **[stated]** Peter said it; **[brief]** from the brief; **[recalled]** from memory, not
checked; **[unknown]** nobody has looked.

Area: `api` (service). Imports only `core`. Contains no rules of its own.

---

## GAP defects first

1. **`programmer.md` (command surface) says neither front end calls the other and that the CLI
   needs no running service. That is too strong, and it is wrong for stateful commands.** A BLE connection, a debug session or a serial monitor holds state
   across calls. A one-shot CLI process cannot own that; the long-running service must. So the CLI
   has to reach the service for those commands. The rule is narrowed below (see CLI and service).
2. **A caller cannot derive a bound today.** `core.md` refuses any waiting command without a
   caller-supplied bound, and the caller must derive it from a measurement (healthy flash time,
   re-enumeration time). Those measurements are recorded nowhere. Until they are, every waiting
   command refuses with `no_bound`. The service must publish recorded measurements (see Measurements).
3. **`core.md` is missing a code and a row field** this spec needs: `service_not_running`, and a
   `needs_service` flag on a row. That edit belongs to the core spec task and is made there.
4. **Upstream's HTTP layer is one 258 KB file**, and its auth, if any, is **[unknown]**. Nothing
   here reuses it; the design is borrowed, not the code (`architecture.md`).
5. **Port 80 is not just a number, and the first draft was silent on all of it.** A port below 1024
   needs privilege; port 80 on a LAN is a non-loopback listen, so the token rule applies; a
   browser cannot authenticate with the bearer header the draft specified (and the dashboard draft
   said the token must not be stored in the browser, which left a browser with no way in); the
   traffic is cleartext HTTP; and another service may already hold the port. See "listening".

---

## Files this spec allows (the HTTP build, 2026-11-16, Peter: "go")

`nrf_workbench/api/__init__.py`, `api/auth.py` (the bearer check and the two access modes),
`api/body.py` (a synchronous `read(n)` over the request's asynchronous body, so a handler that reads a
file as a stream is the same function from the console and from a request), `api/app.py` (the Starlette
application generated from the registry), and `tests/api/`. **Not in this unit:** websocket streams,
the login page and cookie (they belong with the dashboard), the MCP server, the OpenAPI document, the
listen-port holder and privilege checks, and `service.py`, the composition root that wires it.
`api` imports only `core` and the web framework.

**Files for the CLI and the MCP (added 2026-11-16, Peter: "yes", CLI and MCP before the dashboard):**
`nrf_workbench/core/remote.py` (the client of the service: stdlib only, ONE definition of how a registry row
becomes an HTTP request and how a response becomes a `Result`; it lives in `core` because `cli` and `api`
may import only `core`, so sharing it there keeps them from importing each other),
`nrf_workbench/cli/__init__.py` and `cli/app.py` (the command line, generated from the rows),
`nrf_workbench/__main__.py` (the composition for local mode), `nrf_workbench/api/mcp_server.py`
(the MCP server, a thin client of the HTTP service, tools registered from `GET /api/commands`), the small
refactor of `nrf_workbench/service.py` that lets local mode build the host and registry without an HTTP
app, their tests (`tests/core/test_remote.py`, `tests/cli/`, `tests/api/test_mcp.py`), and the README's
status banner (it said the rest of the product was not built) and a short "Use it" section. **The client
has no default timeout** (the derive rule: the service decides nothing about a caller's bound, and the
caller's bound is inside the call); a caller may pass one.

**Built and tested, 2026-11-16: the client and the CLI** (`core/remote.py`, `cli/app.py`, `__main__.py`,
and the `service.build_host` / `service.local` split): 24 tests for the client against a stub server that
records what goes over the wire, 37 for the CLI in local mode, 23 for the CLI in remote mode against a
real uvicorn server, 6 running `python -m nrf_workbench` as a real process; 38 mutants caught. How it
behaves: the commands come from the registry rows (in-process, or the service's own list with `--host`);
arguments are validated by the registry's own validator, so a bad value is exit 2 with the API's message;
`--json` is the Result the API sends (a test proves the two modes give equal JSON, and the same refusal
has the same exit code over the wire: a 404 carrying a Result is still "refused", exit 1); the CLI
computes and sends the SHA-256 of an upload itself (a corrupt transfer is caught), unless the user gave
one; an unreachable service is a **refusal** `service_not_running`, exit 1; the token comes from a file or
the environment, never the command line, and is never printed (a test scans stdout and stderr for it); there
is no default timeout. **Real faults the tests found in my own code:** optional arguments given as `null`
were rejected on the way into `call()` (the registry now treats `null` for an optional argument as "not
given"), and one malformed assertion of mine. **Not exercised on the Pi:** the CLI against the real service
there (the same code ran against a real server on this host).

**Built and tested, 2026-11-16: the MCP server** (`api/mcp_server.py`; 40 tests, 31 of 32 mutants caught,
the survivor equivalent). It has no tool table: `list_tools` fetches `GET /api/commands` **at every
listing**, so a command added to the service appears with no edit here (a test lists three times against a
service whose rows change). Schemas come from the rows (types, required, defaults, units, closed); an
upload tool takes `path` first and required; arguments are **checked, not parsed**, by `Command.from_row`,
the registry's own validator, so the CLI and the MCP cannot disagree with the service. The Result comes
back **whole**, as text and as structured content, `is_error` true unless ok. **The real stdio transport is
tested** (the server spawned as a process and spoken to over its pipes), which the earlier smoke test
explicitly did not exercise, and it found the one thing that matters most to an AI: **the SDK masks any
ordinary exception from a handler as "Internal server error"**, so a failing tool listing would tell the AI
nothing. The listing now raises the SDK's `MCPError` (which carries the message, and a test proves
"unauthorized" arrives across real pipes), while a failing tool CALL returns an error result
(`service_not_running`, `unauthorized`) instead of raising. The stdout is the protocol's, so a test fails
if the module prints to it. **Not built:** a `url` upload argument the service would fetch (the firmware spec
mentions it; the service has no fetch capability), websocket streams, and prompts or resources.

**Proved on the real bench, 2026-11-16, following the README:** the service ran on the Pi as the README shows
(foreground, loopback, reached from this machine through an SSH tunnel). **The MCP server**, spawned as a
process over stdio and driven through the SDK client, listed the seven tools, flashed the stored pinscan
image **by id** to the real RAK4631 (113 of 113 packets in 4.639 s, the board reporting `nrfwb-pinscan 1`
on the same serial, handover 0.589 s), and returned a refusal and a usage error whole across real pipes.
**The CLI in remote mode** then streamed the parking image from this machine in one call (109 of 109 in
4.428 s, `new: false` because the same bytes were already stored, exit 0), and the board reported
`nrfwb-parking 2`. Checked independently of both: the Pi's own USB view, and 4 heartbeats in 4 s from the
firmware on the debug dongle. The README's local-mode commands also ran on the Pi itself. The entry handover
across all the cycles run so far is **0.362 to 0.589 s** (the earlier 0.4 to 0.5 s in the README is the
middle of that, not its range).

**The README is tested against the code (2026-11-16):** `tests/cli/test_readme.py` runs every command in
its "Use it" section against the real service parser and the real generated command line, so a typo in an
option or a command that does not exist fails a test (four mutants of the README were caught). That also
exposed one inconsistency: the CLI read its own `env` but did not pass it to the local-mode factory, which
read the process environment separately; it now passes the same one. The older installer test that checked
README options had to be narrowed to lines that actually run the installer, because it treated every flag
in every code block as an installer option.

**Files for the composition root (added 2026-11-16, Peter: "go"):** `nrf_workbench/service.py` (the ONE
module that imports `api` and the areas and wires them, `architecture.md`) and `tests/service/`. It holds the
concrete `Host`, loads profiles (the package's own `profiles/` unless `paths.profiles` says otherwise: they
ship with the product, they are not a host path) and `units`, derives the capability set, applies the
listen rules from `core.config`, checks the port (`port_in_use` naming its holder from `/proc`,
`port_privileged` from the limit read in `/proc/sys`), and runs uvicorn. It never prints the token.

**Built and tested, 2026-11-16: `service.py`** (42 tests; 27 mutants caught). The listen rules and the port
checks are applied in order before anything listens, cheapest first (a test proves the port is not even
touched when the token rule has already refused): `token_missing`; `port_privileged` from the limit read in
`/proc/sys` (an unreadable limit is **not guessed**: only the bind may say no); `port_in_use` naming
its holder from `/proc/net/tcp` and the process table (a holder the user cannot see is said to be
invisible, not omitted). The probe socket is closed explicitly on every path (a mutant that left it to the
garbage collector survived until a test recorded the `close()`). The token is never printed: a test
scans stdout and stderr. **Not built:** a systemd unit, the login page, TLS.

**Built and tested, 2026-11-16:** `api/auth.py`, `api/body.py`, `api/app.py`: 71 tests (auth 12, the body
bridge 9, the app 33, the registered commands over real HTTP 13, and 4 against a real uvicorn server on a
loopback socket). 24 mutants of the guards were caught; the one that survived the ASGI test client
(reading the whole body before the handler runs) cannot be seen through that client at all, so the live
tests prove it instead: **an upload is streamed** (the handler is reading while the client has sent only
a few KB of 3 MiB), and **a client that vanishes mid-upload leaves nothing in the store**. A raw upload
that fails authorisation is refused before a byte is read. One mutant is equivalent at that level and is
left to the unit test of the body bridge. **Not built:** websocket streams, the login page and cookie, the
MCP server, the OpenAPI document, the listen-port holder and privilege checks, and the composition root.
A JSON body is read whole (it is small; an upload is the streamed case), and has no size cap yet.

## TOOL what is generated, and from what

Everything below comes from the registry. The service holds no command of its own.

| surface | shape |
|---|---|
| list | `GET /api/commands`: every present command with its args, units, `effect`, `requires`, result shape |
| capabilities | `GET /api/capabilities`: the host probe, including what is absent and why |
| `read` command | `GET /api/<area>/<command>` with arguments as query parameters |
| `write` command | `POST /api/<area>/<command>` with arguments as a JSON body |
| `stream` command | websocket `/ws/<area>/<command>`; one JSON event per message |
| schema | `GET /api/openapi.json`, generated from the same rows |

The response body of every command is the `Result` from `core.md`, unmodified. The HTTP status and
the CLI exit code come from the one mapping function in `core`. **[stated: the CLI's `--json` is
the API body]**

A command whose `requires` is unmet is absent from the list and returns 404 here, not 500.

**One deliberate exception to "a `write` takes a JSON body": a row may declare `body: raw`**, so the
request body is the file itself and the other arguments are query parameters. It exists for firmware
upload (`firmware.md`: `firmware.put`, `programmer.flash_stream`). A raw-body row is still generated,
still listed in `GET /api/commands` and the OpenAPI document, still returns the unmodified `Result`,
and still needs the token.

## TOOL CLI and service: the narrowed rule

Replaces the "neither front end calls the other" bullet in the command surface section of
`programmer.md`. `architecture.md` is unaffected: it only says `cli` and `api` import `core`.

- **One-shot commands run in the CLI process, through the library, with no service.** Status,
  build, a file flash, an id probe. The console must work when the service is down or being
  recovered. This is the common case and the safe one.
- **Stateful commands go through the service.** A row declares `needs_service` when it holds state
  across calls (BLE sessions, debug sessions, monitors, anything with a connection). The CLI sends
  those to the running service over HTTP and prints the same `Result`. If no service is running the
  command refuses with `service_not_running`. It does not start one silently.
- **Neither ever shells out to the other.** No subprocess calls between them. The CLI talks to the
  service only through the documented HTTP API, so there is still one definition of every command.
- **Exclusion is unchanged and host-wide.** Because the lock and port-holder check are OS-level, a
  one-shot CLI flash and a service request cannot collide even though they are different processes.

## TOOL concurrency

- A command that changes hardware takes the host lock (see `guards`). **A second request while the
  lock is held is refused at once with `lock_held`, naming the holder. It is not queued.** Queueing
  would be an unbounded wait, and the existing tool already refuses a second instance. **[stated]**
- Read-only commands do not take the lock and run concurrently.
- A long write (a flash is about 25 to 29 s on the RAK4631 **[brief]**) is a synchronous request
  that returns the `Result` when finished. Progress is available on a stream if the client wants it.
  The client's HTTP timeout must exceed the bound it passed; the service never decides a bound.

## TOOL listening: where, and who may reach it

Peter, 2026-11-16: "I want the dashboard running on port 80", and, when told port 80 needs
privilege and may be taken: "nothing listens on port 80, or you can make that one of the user
choices?" It is **a user choice**. One service, one port: the dashboard at `/`, the API under
`/api`, the live feed at `/ws/events`.

- **The address and port are configuration (`service.listen`, or `--listen ADDR:PORT`), never
  hardcoded, and nothing assumes 80.** With nothing chosen the service listens on
  `127.0.0.1:8080` (this machine only, an unprivileged port). 8080 is a convention and not a
  derived value; it may be taken, which is why the next rule exists. The installer asks
  (`install.md`); Peter's choice would be `0.0.0.0:80`.
- **Whether the port is free is checked, not assumed.** Before it starts, and when the installer
  asks, the holder of a port is read from `/proc/net/tcp` and the process table (read-only). A taken
  port is reported with its holder (`port_in_use`), never as a traceback.
- **A port below 1024 needs privilege**, and the limit is read from
  `/proc/sys/net/ipv4/ip_unprivileged_port_start` (it is 1024 on the dev host **[verified]**), not
  assumed. If the service runs as root (as on Peter's hosts) nothing more is needed. A non-root
  service under systemd gets `AmbientCapabilities=CAP_NET_BIND_SERVICE` in its unit **[recalled;
  verify when the unit is built]**. Anywhere else it refuses with the exact reason and suggests a
  higher port. **It never changes the system-wide setting, never grants a capability to the
  interpreter, never installs a proxy, and never edits a firewall.**

### Who may reach it

The service can flash firmware and power-cycle hardware, so it is not open by default.

- **A non-loopback listen address (which port 80 on a LAN is) requires a configured token, and the
  service refuses to start without one**, whatever the access mode.
- **`service.access` has two modes, and there is no mode with unauthenticated writes:**
  - `token` (the default for any non-loopback address): every request needs the token;
  - `open-read` (opt-in): the dashboard pages and read-only data are open to the LAN; every `write`
    command and every stream that can send needs the token. The Bench page says plainly that read
    access is open.
- **Clients (the CLI over HTTP, the API, the MCP) send the token as a bearer header**, compared in
  constant time, never logged. Websocket upgrades carry it too.
- **A browser cannot send a bearer header on a page load, and putting the token in
  script-readable storage hands it to any script that runs on the page.** So the dashboard has a
  login page: the token is entered once and the server answers with an `HttpOnly`,
  `SameSite=Strict` session cookie. A write also needs a per-session value in a custom header, and
  the websocket checks that its `Origin` matches the host, so another website cannot drive it
  (`dashboard.md`).
- **Plain HTTP on port 80 sends the token and the cookie unencrypted across the LAN.** Anyone who
  can read that traffic can reuse them, and they allow flashing hardware. That is stated, not
  hidden: it is acceptable only on a LAN Peter trusts; the `Secure` cookie flag cannot be set over
  plain HTTP; the Bench page shows "unencrypted" whenever it is listening beyond loopback without
  TLS; and TLS (built in, or behind a reverse proxy) is **not designed yet** **[unknown]**.
- **The token is generated by the installer into a file readable only by its owner, and the
  installer prints the path, never the value**, so it does not end up in a log or in scrollback. The
  user reads it on the host with a shell they already have.
- Authentication is not a safety layer. A valid token still goes through every guard; a guard
  refusal looks the same to every caller.

## TOOL what the dashboard needs from the service

Added with `dashboard.md`. All of it is generated from or carried by the core event bus
(`core.md`); none of it is a command of its own.

- **`/ws/events`: one websocket for everything live.** The client sends a `want` message naming
  the feeds it needs (`devices`, a link's console, `activity`, `flash`, `readiness`) and a cursor to
  resume from; the server sends only those, and a `gap` event says how many were lost if the cursor
  is older than what is retained. No polling anywhere.
- **`GET /api/activity`: the activity log**, with a cursor, for a page that wants history. It is
  the record of every `command.result` from every caller.
- **Readiness is `programmer.status`, re-evaluated whenever `devices.changed` fires** and published
  as `readiness.changed`, so the dashboard's "can I flash this now?" is the dry-run of the same
  guards a flash runs.
- **Static assets and `/`:** the built UI is served at the root, with the bench name substituted
  into the page title on the server before any JavaScript runs. If the UI has not been built, `/`
  says so plainly.
- **The dashboard's own status route** reports what the dashboard can write and which routes it
  uses, so its claim can be checked against its source (`dashboard.md`).
- **No write route belongs to the dashboard.** Every action calls a registry command.

## TOOL MCP

- **A separate MCP server, `nrf-workbench`. It does not share the ESP testbench MCP.** See the
  decision below.
- **It uses the official `mcp` Python SDK, not hand-written protocol code.** The SDK is already
  installed on this host at 2.3.0 and needs Python 3.10 or newer **[verified: package metadata]**.
  Upstream's MCP is a stdlib-only proxy **[brief]**; that was a choice for a minimal install, not a
  requirement here (see the decision below).
- It is a thin server over the HTTP service. At start it calls `GET /api/commands` and registers
  one tool per command from the answer, so it is generated from the registry and has no tool table
  of its own. **[verified 2026-11-16, run on this host]** SDK 2.3.0's low-level `Server` takes
  `on_list_tools` and `on_call_tool` handlers, so tools are built from plain data rows with the
  JSON Schema passed through verbatim, no per-tool Python function. A row added after start appears
  on the next `list_tools`. A refused `Result` is returned whole in the tool content with
  `is_error` true. (The client side of the SDK names these `is_error` and `input_schema`.) This was
  tried in-process; the stdio and HTTP transports are the SDK's own and were not exercised.
- Tool names are `<area>_<command>` (for example `programmer_flash_file`); a server prefix already
  separates them from the ESP tools.
- A tool returns the `Result` JSON in full, including the whole tool output and all evidence.
  Nothing is summarised or tailed. **[brief, rule 7]**
- A stream command is exposed as a bounded poll: it takes a bound and returns the events so far.

## TOOL measurements

The service publishes recorded measurements so that a caller can derive a bound instead of
guessing one. They are stored with the measurement that produced them (what, when, on which unit,
how many samples) in the profile or in `units.yaml`, and returned by `GET /api/devices`:

- healthy flash duration per profile (brief: 25 to 29 s on a RAK4631, a single recorded range)
- re-enumeration time across a mode change
- any other duration a guard compares against (`flash_suspect` needs the healthy duration)

A value with no recorded measurement is returned as absent, and a command that needs it refuses.
The `MEASURE` step in the `guards` task is what fills them in. **[derive rule]**

## DECIDE derived (Claude; Peter may overrule)

1. **A separate MCP server, not a shared one.** Constraint: upstream's portal has no device-family
   concept (brief section 2), so a shared server would mean forking it; the set of commands here
   depends on the host and the attached board, which upstream's fixed table cannot express; and MCP
   already namespaces tools by server, so there is no name collision to avoid. Runner-up: one
   combined server, rejected for the fork cost. Both servers can be listed in the same client config.
2. **Starlette and uvicorn for HTTP and websocket (reverses my first draft, which said aiohttp).**
   Constraint: the official MCP SDK already requires Starlette and uvicorn **[verified: its
   declared dependencies]**, so aiohttp would add a second web stack beside it for no gain. The
   registry already holds typed args and result shapes, so OpenAPI is generated and a
   schema-generating framework adds nothing. Runner-up: aiohttp, rejected for the duplicate stack.
   The CLI's client for stateful commands needs an HTTP client, and the SDK already brings `httpx2`.
   **[verified 2026-11-16, run on this host]** Starlette on uvicorn serves a websocket on loopback
   (three JSON events received, via the `websockets` library). `websockets` is installed here but
   is **not** among the `mcp` SDK's declared dependencies, so this project must declare it itself
   (`uvicorn[standard]` or `websockets`); relying on it being present by accident would be the bug.
3. **Use the official MCP SDK for the protocol.** Constraint: initialisation, tool listing, call
   dispatch, schemas and transports are a protocol that changes; maintaining a copy by hand is the
   drift the single-source rule exists to stop. Cost: the full set (`mcp`, `uvicorn`,
   `websockets`, `starlette` and everything they pull in) is 29 binary wheels on aarch64, including
   pydantic, jsonschema, opentelemetry-api and PyJWT **[verified 2026-11-16: `pip download
   --only-binary` resolved all 29 for aarch64 on CPython 3.10, 3.11 and 3.13, so nothing compiles
   on a Pi]**. That is heavier than upstream's stdlib proxy, and accepted because the alternative is
   owning the protocol. Runner-up: a stdlib proxy as upstream, rejected for the maintenance. This
   also sets a floor: **Python 3.10 or newer** (the SDK's own requirement).
4. **GET for `read`, POST for `write`, websocket for `stream`.** Constraint: the registry already
   carries `effect`, so the verb costs nothing and a read can never be mistaken for a write.
5. **Refuse a second hardware request, never queue it.** Constraint: a queue is an unbounded wait,
   and the derive rule says no wait is unbounded.
6. **Loopback on an unprivileged port by default, token to leave it, refuse to start otherwise;
   the address and port are the user's choice.** Constraint: this flashes and power-cycles
   hardware, so an open default on a LAN is the failure to rule out and one extra configuration step
   is cheap; and Peter wants port 80, which another host or person may not, so it is chosen, not
   assumed.
7. **A login cookie for the browser, bearer for everything else.** Constraint: a page load cannot
   carry a bearer header and script-readable storage exposes the token. This **reverses** the
   dashboard draft's "the token is not stored in the page or in browser storage", which could not
   have worked for a browser on another machine.
8. **Narrow the CLI independence rule** (GAP: the CLI independence rule is too strong). Constraint: stateful commands need one owner of
   state, and only a long-running process can be that owner.

## VERIFY

- **Listening is a choice:** the service starts on whatever `service.listen` says; with none set it
  listens on `127.0.0.1:8080` and nowhere else; nothing in the code names port 80.
- **A taken port** returns `port_in_use` naming the holder, not a traceback.
- **A port below 1024 as a non-root user without the capability** refuses with the reason; as root,
  and with the capability under systemd, it binds. (The limit is read from `/proc`, not assumed.)
- **Non-loopback with no token refuses to start**, in both access modes.
- **`open-read`:** a page and a read are served without a token; a `write` and a sending stream are
  refused without one; there is no mode in which a write is unauthenticated.
- **The browser path:** the login sets an `HttpOnly`, `SameSite=Strict` cookie; a write without the
  per-session header is refused; a websocket with a foreign `Origin` is refused.
- **The Bench page says "unencrypted"** whenever the service listens beyond loopback without TLS.
- `GET /api/commands`, the CLI help and the MCP tool list are the same set on one host, and differ
  on an x86_64 host with no Pi hardware exactly by the Pi-only commands.
- A command added to one area appears in all three with no edit to `api/`.
- The service refuses to start on a non-loopback address with no token.
- A second flash request while the first holds the lock returns `lock_held` immediately, naming
  the pid, and does not wait.
- A one-shot CLI command works with the service stopped; a `needs_service` command returns
  `service_not_running`.
- The MCP server returns the same `Result` the HTTP API returns, unabridged.
- **Done 2026-11-16 (scratch scripts, not kept):** data-driven MCP tools with `mcp` 2.3.0 over an
  in-process client, and a Starlette websocket on uvicorn over loopback. These become the first
  `tests/api` smoke tests when the area is built, so the evidence is kept as a test.
- **Done 2026-11-16:** all 29 wheels resolve for aarch64 on CPython 3.10, 3.11 and 3.13.
- **Still unrun:** the stack actually starting on a physical Pi (wheels resolving is not running),
  and the SDK's stdio and streamable-HTTP transports. **[unknown]** until run on the bench Pi.
