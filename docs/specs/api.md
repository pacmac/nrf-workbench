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

---

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

## TOOL authentication

The service can flash firmware and power-cycle hardware, so it is not open by default.

- **It binds to loopback by default.** Binding to any other address requires a configured token,
  and the service **refuses to start** without one rather than serving open on the network.
- The token is sent as a bearer header, compared in constant time, and never logged. Websocket
  upgrades carry the same token.
- Authentication is not a safety layer. A valid token still goes through every guard; a guard
  refusal looks the same to every caller.

## TOOL MCP

- **A separate MCP server, `nrf-workbench`. It does not share the ESP testbench MCP.** See the
  decision below.
- **It uses the official `mcp` Python SDK, not hand-written protocol code.** The SDK is already
  installed on this host at 2.3.0 and needs Python 3.10 or newer **[verified: package metadata]**.
  Upstream's MCP is a stdlib-only proxy **[brief]**; that was a choice for a minimal install, not a
  requirement here (see the decision below).
- It is a thin server over the HTTP service. At start it calls `GET /api/commands` and registers
  one tool per command from the answer, so it is generated from the registry and has no tool table
  of its own. Whether SDK 2.x registers tools from data at runtime this way is **[unknown]**: it is
  the first thing to try (see Verify).
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
   Whether uvicorn serves websockets without an extra package is **[unknown]**; verify below.
3. **Use the official MCP SDK for the protocol.** Constraint: initialisation, tool listing, call
   dispatch, schemas and transports are a protocol that changes; maintaining a copy by hand is the
   drift the single-source rule exists to stop. Cost: the SDK pulls about fifteen transitive
   dependencies (pydantic, jsonschema, opentelemetry-api, PyJWT and others) **[verified: declared
   dependencies]**, heavier than upstream's stdlib proxy; that is accepted on a Pi because the
   alternative is owning the protocol. Runner-up: a stdlib proxy as upstream, rejected for the
   maintenance. This also sets a floor: **Python 3.10 or newer**.
4. **GET for `read`, POST for `write`, websocket for `stream`.** Constraint: the registry already
   carries `effect`, so the verb costs nothing and a read can never be mistaken for a write.
5. **Refuse a second hardware request, never queue it.** Constraint: a queue is an unbounded wait,
   and the derive rule says no wait is unbounded.
6. **Loopback by default, token to leave it, refuse to start otherwise.** Constraint: this flashes
   and power-cycles hardware; an open default on a LAN is the failure to rule out, and one extra
   configuration step is cheap.
7. **Narrow the CLI independence rule** (GAP 1). Constraint: stateful commands need one owner of
   state, and only a long-running process can be that owner.

## VERIFY

- `GET /api/commands`, the CLI help and the MCP tool list are the same set on one host, and differ
  on an x86_64 host with no Pi hardware exactly by the Pi-only commands.
- A command added to one area appears in all three with no edit to `api/`.
- The service refuses to start on a non-loopback address with no token.
- A second flash request while the first holds the lock returns `lock_held` immediately, naming
  the pid, and does not wait.
- A one-shot CLI command works with the service stopped; a `needs_service` command returns
  `service_not_running`.
- The MCP server returns the same `Result` the HTTP API returns, unabridged.
- **First try, before any other API code:** with `mcp` 2.3.0, register a tool from a data row at
  runtime and call it. If SDK 2.x cannot, this spec's MCP section changes. **[unknown]**
- Starlette, uvicorn and the `mcp` SDK install and run on one arm64 Pi and one x86_64 host, with
  websockets working. **[unknown]** until run.
