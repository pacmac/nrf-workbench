# Core — functional spec (DRAFT, not agreed)

The shared layer every area builds on. It holds no hardware and no front end. Inputs:
`architecture.md` (one registry, three front ends), `programmer.md` (the refusals), `devices.md`.
Tags: **[stated]** Peter said it; **[brief]** from the brief; **[unknown]** nobody has looked.

Area: `core` (library). Depends on nothing else in the project.

---

## GAP defects first

1. **The refusal codes exist only as prose in `programmer.md`.** Until they are a closed, named
   list in code, the CLI exit code and HTTP status cannot be derived from them (see Results).
2. **The lock path is host-specific.** The existing tool uses `/run/lock/rak-comm.lock`. On this
   host `/run/lock` exists and is writable **[verified, 2026-11-16]**. Whether it does on every
   other target host (Pi, PVE host, minimal VM) is **[unknown]**. The path must be configurable, with one documented
   default, and the probe must report when it is unusable.
3. **The HTTP framework is not chosen.** It is deferred to the `api` spec; nothing in `core` may
   depend on one (see Registry).

---

## TOOL the command registry

A command is declared **once**, as one row. The CLI, HTTP API and MCP tools are all generated from
the registry and hold no commands of their own. **[stated, via architecture.md]**

A row carries:

| field | meaning |
|---|---|
| `name` | `area.command`, lower-case, e.g. `programmer.flash_file`. The only identity a command has. |
| `summary` | one line, shown in CLI help, API listing and MCP description |
| `args` | typed argument list: name, type, required or default, one-line doc, and for numbers the unit |
| `handler` | a function that takes the typed args and returns a `Result` |
| `requires` | capability names the host and device must have (see Capability probe) |
| `effect` | `read` (observes only), `write` (changes hardware or files), or `stream` |
| `stream` | for `stream` commands, the event type it yields |
| `needs_service` | true if the command holds state across calls (a BLE or debug session, a monitor); the CLI then sends it to the running service (`api.md`) |
| `result` | the shape of `Result.data` on success, so front ends can document it |

Rules:

- **Names are derived, not written three times.** CLI: `workbench programmer flash-file`. HTTP:
  the same path segments. MCP: `programmer_flash_file`. One naming function; a front end cannot
  disagree with another about a command's name.
- **The registry imports no front end and no HTTP framework.** A handler is a plain function over
  typed arguments, so the same function runs from the console, a request and a test.
- **A command whose `requires` is unmet is absent** from CLI help, the API list and the MCP, not
  present and failing (`architecture.md`). It remains explainable: see `host.capabilities`.
- **A second row with the same `name` is refused at load**, naming both defining modules.
- **Registration:** each area exposes `register(registry, host)`. `core` calls it only for areas
  whose capability probe passes. An area that fails to import (a missing optional dependency, such
  as the BLE library) is reported in `host.capabilities` with the reason and the rest still loads.

## TOOL results and refusals

Every handler returns one `Result`; a handler never raises for an expected outcome.

```
ok        true only if the thing was done and judged good
class     ok | refused | failed | suspect
code      a stable machine string; "ok" on success, else one from the closed list below
message   one human sentence
data      the success payload, shape given by the row's `result`
evidence  what was found: pids, paths, ids, durations, and the WHOLE external-tool output
```

The classes are distinct and not interchangeable: **refused** means nothing was attempted and
nothing changed (a guard said no); **failed** means it was attempted and went wrong; **suspect**
means it finished but the evidence says do not trust it (a flash faster than a healthy flash,
brief rule 5). A refused request is not a failed write. **[brief]** The whole tool output is always
returned, never a tail. **[brief]**

**Codes are a closed list.** A new code is a spec change, not a free-text string:

| code | class | when | evidence it names |
|---|---|---|---|
| `lock_held` | refused | another instance holds the host lock | holder pid |
| `lock_void` | refused | the lock file was replaced mid-run ("exclusion is void") | lock path |
| `node_absent` | refused | there is no node to open; the evidence says whether a board IS attached (`attached_no_node`) or not at all | sysfs entry, if any |
| `node_two` | refused | an app node and a bootloader node at once | both paths |
| `node_not_char` | refused | the node is not a character device | path |
| `unrecognised` | refused | a node exists, no profile claims its ids | ids |
| `port_held` | refused | any process has the node open | pid, matched by device number |
| `serial_mismatch` | refused | attached serial is not the one the caller named | both serials |
| `serial_unverifiable` | refused | the caller named a serial, and either no identity source can read one or the node cannot be joined to it | what was missing |
| `two_candidates` | refused | more than one board could match | their serials |
| `protected` | refused | the unit is protected | serial |
| `bad_package` | refused | not a zip, no `manifest.json`, a `.hex`, or truncated | what was missing |
| `wrong_softdevice` | refused | package `softdevice_req` does not match the device | both values |
| `not_in_bootloader` | refused | a file flash was asked of a board not in the bootloader | state |
| `no_bound` | refused | a waiting command was started without a caller-supplied bound | |
| `capability_missing` | refused | the host or device lacks a required capability | the capability |
| `service_not_running` | refused | a `needs_service` command was run from the CLI and no service answers | the address tried |
| `link_absent` | refused | the declared debug adapter is not attached (`console.md`) | the adapter identity |
| `not_a_declared_link` | refused | the node is not a declared debug link, or belongs to a DUT | the node |
| `baud_refused` | refused | a baud other than the link's declared one, or 1200 | the baud asked |
| `link_rx_only` | refused | a send on a link whose host TX is not wired to the DUT | the link |
| `voltage_unverified` | refused | the link's voltage domain has not been measured | the link |
| `dtr_rts_unsafe` | refused | DTR or RTS reach the DUT's reset or boot line | the link |
| `no_reply` | failed | a sent verb got no reply within the caller's bound | the bound, the elapsed time |
| `no_return_path` | refused | the unit declares no way back from the bootloader (or the declared package is missing or not a DFU zip), so it will not be sent there (`devices.md`) | the allowed methods, or the package |
| `touch_failed` | failed | the bootloader touch could not be performed (the one attempt failed); nothing is known to have changed | the error |
| `bootloader_not_reached` | failed | the touch was made once and no bootloader appeared within the caller's bound | the bound, the elapsed time, the event source |
| `port_in_use` | refused | the service's listen port is held by another process (`api.md`) | the holder, or "not visible to this user" |
| `port_privileged` | refused | the listen port is below the unprivileged limit and the service has neither root nor the capability | the port, the limit read from the host |
| `token_missing` | refused | a non-loopback listen address was configured with no token | the address |
| `flash_failed` | failed | the write ran and the tool said it failed | whole output |
| `flash_suspect` | suspect | the write reported success but duration or output disagrees | output, duration, healthy duration |
| `run_node_not_returned` | failed | after a flash the run node did not return within the bound | the bound, the elapsed time |

The list grows with each area's spec; it is closed per release.

### Exit codes and HTTP status, derived from `class` and `code`

One function maps a `Result` to both, so the CLI and API cannot disagree.

| class | CLI exit | HTTP |
|---|---|---|
| ok | 0 | 200 |
| refused | 1 | 409 for state, holder, lock, serial, protected and link-wiring refusals (`link_absent`, `link_rx_only`, `voltage_unverified`, `dtr_rts_unsafe`); 422 for `bad_package`, `wrong_softdevice`, `no_bound`, `baud_refused`, `not_a_declared_link` |
| (usage error: bad arguments) | 2 | 400 |
| failed | 3 | 500 |
| suspect | 4 | 500, `ok` false |

`service_not_running`, `port_in_use`, `port_privileged` and `token_missing` are only ever raised by
the CLI or at service start, never by a running service answering a request, so each has an exit
code (1) and no HTTP status.

These numbers are **not** the existing tool's. `rak-comm.py` uses 1 for a failure, 2 for a bad file
or a board not in the bootloader, and 3 for a missing node or a holder **[verified: read the code,
2026-11-16]**, so a refusal there can be 2 or 3 and a failure is 1: the opposite of this table for
failures. My first draft claimed exit 1 "matches the existing tool"; that was wrong (only `--idle`
returns 1 for a holder). The choice here is deliberate: exit 2 is what argument parsers already
use for bad usage, and a refusal (nothing was done) is the common, safe outcome. Anything scripted
against `rak-comm.py`'s codes must be re-read.

## TOOL the host capability probe

Run once at startup, read-only. **It never opens a serial port, never touches a board, and never
writes DTR or RTS.** It records, as named capabilities:

- platform: architecture, OS, Python version, whether this is a Raspberry Pi (device-tree model)
- USB event source: whether udev events are available (`devices.md`)
- GPIO: the `libgpiod` chips present, if any; the Pi `/dev/mem` path is **not** used (`nrf-workbench.md`
  section 5b: its behaviour on Pi 5 is **[unknown]**). **A gpiochip, and a readable `/dev/mem`, are
  not evidence of a Pi header.** [verified: the bare-metal x86_64 Proxmox host has `/dev/gpiochip0`
  and a read-write `/dev/mem`, and is not a Pi.] The Pi backend is offered only when the
  device-tree model says Raspberry Pi; upstream's helper maps BCM2835 registers through
  `/dev/mem`, which on any other machine would write arbitrary physical memory. The probe records
  each chip's label and line count so a motherboard chip is not mistaken for a header.
- network: which TCP ports are listening and who holds them (read from `/proc/net/tcp`, read-only),
  and the unprivileged-port limit (`/proc/sys/net/ipv4/ip_unprivileged_port_start`), so a
  chosen port is reported free or taken, and privileged or not, from the host and not from a guess
- serial: USB serial adapters present (by identity, never by `/dev` name); on a Pi the header UART
  (`serial0`, `serial1`, `ttyAMA*`), whether it is enabled, and whether a login getty holds it
  (`console.md`); the Pi UART capability requires the device-tree model to say Raspberry Pi
- Bluetooth: controllers present, and whether a dedicated nRF52840 radio is attached
- debug probes: CMSIS-DAP or other SWD probes on USB
- external tools: presence and version of `pio`, `adafruit-nrfutil`, `nrfutil`, `openocd`, `pyocd`,
  `mcumgr`, `smpmgr`, and any proprietary download needed
- lock directory: present and writable, or not (GAP: the lock path is host-specific)
- identity source: `sysfs` and whether `udev` adds to it (serials come from either); and node
  resolver: `tty link`, `by-id` or `node-map` (`devices.md`). The two together decide whether the
  serial interlock can be completed, or only proves a board with that serial is attached.

It is published by the command `host.capabilities`, which lists **what is present and, for each
missing one, what it blocks and why**, so an absent command is always explainable. Commands name
the capabilities they `require`; the registry hides a command when one is unmet.

This host today (probed by hand, 2026-11-16): x86_64, Python 3.13, with `pio` and
`adafruit-nrfutil`; without `nrfutil`, `openocd`, `pyocd`, `mcumgr`, `smpmgr`, `nrfjprog`,
`JLinkExe` or the BlueZ tools. **[verified]**

## TOOL configuration

Layered, later layers override earlier: built-in defaults, a host file (`<prefix>/config/`, where
`<prefix>` is the folder the user chose at install, `install.md`; **not** `/etc`, so one
self-contained folder holds the whole install; no folder is assumed), a user file, environment,
command-line flags. YAML. Two kinds of content:

- **host settings:** lock path, profile directory, and the service's `listen` (address and port,
  with no assumed value; the unset default is loopback on 8080) and `access` (`token` or
  `open-read`), see `api.md`
- **`units.yaml`:** exceptions per serial only, chiefly `protected` (`devices.md`). A board not
  named is flashable by default; this is not a roster.

No timeout, retry count or tolerance has a default in code. A wait takes a **caller-supplied
bound**, derived from a measurement by whoever calls it, and `no_bound` refuses a wait without one.
The one exception is configuration that records a measured value together with the measurement it
came from. **[stated and derived, `programmer.md`]**

## TOOL the event bus and the activity log

Added for the dashboard (`dashboard.md`), which needs a live view of the whole bench and a record of
what every caller did. `core` owns both; nothing else does.

- **An event** has a `type` (for example `devices.changed`, `console.line`, `flash.progress`,
  `command.result`), the `area` that raised it, a typed payload, a monotonic and a wall timestamp,
  and a **cursor**: a per-bus integer that only goes up.
- **Areas publish; subscribers wait.** The API's `/ws/events` feed and the activity log are
  subscribers. A subscriber waits on the next event; nothing polls.
- **The activity log records every `command.result`:** the caller (`cli`, `api`, `mcp`,
  `dashboard`, plus the token's label if one was used), the command, its arguments (any secret
  redacted), the `class` and `code`, and the duration. A refusal is recorded exactly like a
  success. This is the record of what an AI did to hardware.
- **Every retained stream declares its bound, with no default:** `retain` (a count) and
  `max_age_days`, both required, each with its derivation written beside it. An unbounded log is
  retained for ever by oversight. A stream without a bound refuses to start (`no_bound`).
  **[prior art: the mesh-gw dashboard's streams]**
- **Resume from a cursor.** A subscriber asks for events after cursor N. If N is older than what is
  retained, it is told **how many events were lost** (a `gap` event), never silently skipped.
- **Events are the same data the commands return.** A `devices.changed` payload is the port's
  device record from `devices.md`, so the feed and `devices.list` cannot disagree.

## TOOL streams

A `stream` command yields typed events, each with a monotonic timestamp and a wall-clock timestamp.
The CLI prints one line per event, the API sends them over a websocket, and MCP returns them in
polled batches. A stream is always bounded: it ends on the caller's bound, on a terminal event, or
on the client closing. No stream or wait is unbounded. **[derive rule]**

## DECIDE derived (Claude; Peter may overrule)

1. **`class` has four values, with `suspect` separate from `failed`.** Constraint: brief rule 5
   (a 15 s flash once "succeeded" and boot-looped the board) means a result can be neither a clean
   success nor a failure, and folding it into either hides the case that matters most.
2. **Exit and HTTP codes come from one function over `class` and `code`.** Constraint: the
   requirement that the CLI and API agree; two tables would drift.
3. **Refusals are returned, not raised.** Constraint: front ends must map them identically, and
   an exception path would give the CLI and the API two ways to report the same refusal.
4. **The codes are a closed list owned by `core`.** Constraint: stable codes are the contract
   callers (and the AI) write checks against; free-text strings cannot be checked.
5. **No default timeout in code.** Constraint: the derive rule and `programmer.md` decision 2; a
   default is a plucked number.

## VERIFY

- **One row yields three front ends:** a command added with no edit to the CLI or API appears in CLI
  help, the API list and the MCP. (Also in `architecture.md`.)
- **Mapping is total:** every code in the closed list has an exit code, and an HTTP status unless
  it is CLI-only (`service_not_running`); a code missing from the mapping fails a test.
- **Absent, not failing:** on this x86_64 host with no Pi, the Pi-only commands are absent from all
  three, and `host.capabilities` says why.
- **The probe is passive:** run under a process that records opens, it opens no serial device.
- **A duplicate command name** is refused at load.
- **`lock_void`:** the lock file replaced mid-run is detected (against the fixtures, no hardware).
