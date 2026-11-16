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
| `node_absent` | refused | no node for the device | |
| `node_two` | refused | an app node and a bootloader node at once | both paths |
| `node_not_char` | refused | the node is not a character device | path |
| `unrecognised` | refused | a node exists, no profile claims its ids | ids |
| `port_held` | refused | any process has the node open | pid, matched by device number |
| `serial_mismatch` | refused | attached serial is not the one the caller named | both serials |
| `serial_unverifiable` | refused | the caller named a serial and this host's provider has none to compare | provider |
| `two_candidates` | refused | more than one board could match | their serials |
| `protected` | refused | the unit is protected | serial |
| `bad_package` | refused | not a zip, no `manifest.json`, a `.hex`, or truncated | what was missing |
| `wrong_softdevice` | refused | package `softdevice_req` does not match the device | both values |
| `not_in_bootloader` | refused | a file flash was asked of a board not in the bootloader | state |
| `no_bound` | refused | a waiting command was started without a caller-supplied bound | |
| `capability_missing` | refused | the host or device lacks a required capability | the capability |
| `service_not_running` | refused | a `needs_service` command was run from the CLI and no service answers | the address tried |
| `flash_failed` | failed | the write ran and the tool said it failed | whole output |
| `flash_suspect` | suspect | the write reported success but duration or output disagrees | output, duration, healthy duration |
| `run_node_not_returned` | failed | after a flash the run node did not return within the bound | the bound, the elapsed time |

The list grows with each area's spec; it is closed per release.

### Exit codes and HTTP status, derived from `class` and `code`

One function maps a `Result` to both, so the CLI and API cannot disagree.

| class | CLI exit | HTTP |
|---|---|---|
| ok | 0 | 200 |
| refused | 1 | 409 for state, holder, lock, serial and protected refusals; 422 for `bad_package`, `wrong_softdevice`, `no_bound` |
| (usage error: bad arguments) | 2 | 400 |
| failed | 3 | 500 |
| suspect | 4 | 500, `ok` false |

`service_not_running` is only ever raised by the CLI, so it has an exit code (1) and no HTTP status.

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
  section 5b: its behaviour on Pi 5 is **[unknown]**)
- Bluetooth: controllers present, and whether a dedicated nRF52840 radio is attached
- debug probes: CMSIS-DAP or other SWD probes on USB
- external tools: presence and version of `pio`, `adafruit-nrfutil`, `nrfutil`, `openocd`, `pyocd`,
  `mcumgr`, `smpmgr`, and any proprietary download needed
- lock directory: present and writable, or not (GAP: the lock path is host-specific)
- identity provider: `udev` (serials available) or `node-map` (named nodes, no serial); see
  `devices.md`. This decides whether the serial interlock can run at all.

It is published by the command `host.capabilities`, which lists **what is present and, for each
missing one, what it blocks and why**, so an absent command is always explainable. Commands name
the capabilities they `require`; the registry hides a command when one is unmet.

This host today (probed by hand, 2026-11-16): x86_64, Python 3.13, with `pio` and
`adafruit-nrfutil`; without `nrfutil`, `openocd`, `pyocd`, `mcumgr`, `smpmgr`, `nrfjprog`,
`JLinkExe` or the BlueZ tools. **[verified]**

## TOOL configuration

Layered, later layers override earlier: built-in defaults, a host file (`/etc/nrf-workbench/`),
a user file, environment, command-line flags. YAML. Two kinds of content:

- **host settings:** lock path, profile directory, service bind address
- **`units.yaml`:** exceptions per serial only, chiefly `protected` (`devices.md`). A board not
  named is flashable by default; this is not a roster.

No timeout, retry count or tolerance has a default in code. A wait takes a **caller-supplied
bound**, derived from a measurement by whoever calls it, and `no_bound` refuses a wait without one.
The one exception is configuration that records a measured value together with the measurement it
came from. **[stated and derived, `programmer.md`]**

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
