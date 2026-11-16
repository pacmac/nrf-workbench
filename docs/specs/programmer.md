# Programmer — functional spec (DRAFT, not agreed)

Source: Peter's requirements dump of 2026-11-16, describing the existing `rak-comm.py` tool, read
against `nrf-workbench.md` (the brief). Provenance tag: **[stated]** = Peter said it, not
re-verified here. **[brief]** = from the brief. **[unknown]** = nobody has looked.

The dump describes a CLI that already exists for one board family (RAK4631, Adafruit bootloader,
PlatformIO). This spec treats it as **prior art for the nRF programmer's guard layer**: the behaviour
to keep, generalise, and where it contradicts the brief, to settle.

---

## GAP defects first — where the dump contradicts the brief or itself

1. **The wrong-board interlock was removed, the brief requires it.** The dump: "Removed today: the
   serial and `--unit` wrong-board interlock, the roster". Brief rule 9: refuse unless the attached
   serial matches the one the caller named; refuse if two candidates. Brief rule 10: a `protected`
   flag per device. As described, nothing stops flashing the wrong or a mid-measurement board.
2. ~~`--flash` fails ("pio can't find the port")~~ **Not a product defect.** Peter: it comes from
   this dev host's USB pass-through and custom `/dev` naming, and will not occur on a PVE host, a
   Pi or a VM. Out of scope; no fix designed around it. **[stated]** One consequence kept: the
   tool must find the node by udev identity, never by a hardcoded `/dev` name.
3. **"No sleep, poll or timeout" contradicts "waits on inotify for the run node to return, and
   warns if it doesn't."** An inotify wait with no bound never warns; it hangs. Brief rule 11 says
   derive every bound from a measurement. The bound has to live somewhere.
4. **"Never requests the bootloader / never touches DTR or RTS" vs `--flash` via pio.** pio's
   upload performs the 1200-baud touch itself. So bootloader entry happens, just not in this tool.
   Brief rule 2 (exactly one transition per flash, verified by the device's boot counter) is
   unenforceable if the tool cannot see or control who caused the transition.
5. **`--flash-file` only works if the board is already in the bootloader**, and nothing in this
   tool puts it there. Who does, and how, is unspecified.
6. **The verdict is weaker than the brief.** The dump judges `--flash` by pio exit 0 plus the text
   `Device programmed.`, and `--flash-file` by nrfutil's exit code alone. Brief rule 5: judge by
   output AND duration (healthy 25-29 s; a 15 s run once "succeeded" and boot-looped the board).
7. **Port-holder check misses aliases** (`/dev/ttyACM1` vs `/dev/lxc-serial/...`) because it
   compares paths. **[stated]** Compare device number (`st_rdev`), not path.
8. **The port-guard hook does not name `/dev/lxc-serial/`.** **[stated]**

---

## TOOL purpose and scope

A local, single-host command that is the **only** sanctioned way to put firmware on an attached nRF
board and to say what state that board is in. It decides whether a flash may proceed; it does not
decide what to flash or when. The HTTP/MCP service (brief section 1) calls it, or reimplements
exactly these rules; the rules are the contract, not the CLI.

Not in scope here: SWD, BLE DFU, debugging, GPIO, test equipment (separate specs).

## TOOL modes

| mode | requirement |
|---|---|
| `--status` | Print the board state — **app**, **bootloader**, **two**, **unrecognised**, or **absent** — plus the node path, the process holding it, and whether a flash is ready. Read-only. |
| `--build --env E` | Compile through the build tool. Needs no USB and takes no lock. |
| `--flash --env E` | Build, then upload through the build tool. One attempt. The build tool also waits out the DFU handover. |
| `--flash-file FILE` | Write a prebuilt DFU zip to the bootloader node with `adafruit-nrfutil` (115200, single-bank). Only if the board is already in the bootloader. |
| `--idle` | Report who holds the port; exit 1 if anyone does. |
| `--dir`, `--env` | `--dir` defaults to the directory derived from the tool's own location. `--env` is required; the error lists the envs declared in the project's `platformio.ini`. |
| after a flash | Wait on inotify for the run node to return; warn if it does not. (See GAP: the bound.) |

**Generalisation needed** (the dump is RAK-specific): the default `--dir` of `rak/v3`, the
`/run/lock/rak-comm.lock` path, `platformio` as the only build tool, and the Adafruit bootloader as
the only DFU type must become profile data (brief section 3), not code. **[brief]**

## TOOL guards

Each guard is a refusal. A refusal names what it found (pid, path, count).

- **Single instance.** `flock` on a lock file. A second instance is refused and told which pid
  holds it. If the lock file is replaced mid-run, refuse with "exclusion is void". **[stated]**
- **State.** Refuse when no node exists, when both app and bootloader nodes exist, or when a node
  is not a character device; name the paths. **[stated]**
- **Port holder.** Refuse if any process has the node open (scan `/proc/*/fd`); name the pid.
  Must match by device number, not path (GAP 7).
- **One attempt.** Never retry. A retry is another reset. **[stated]**
- **Verdict.** `--flash` succeeds only if the build tool exits 0 and prints `Device programmed.`.
  `--flash-file` is judged by nrfutil's exit code. (Both strengthened by GAP 6.)
- **Build first, unlocked.** A failed build disturbs nothing: no lock, no port touched.
- **Input checks for `--flash-file`.** The file must exist and be a zip containing `manifest.json`,
  so a `.hex` or truncated download is refused before any write. (Brief also wants a wrong
  SoftDevice refused before write; `softdevice_req` must match. **[brief]**)

## TOOL never-does

- open a serial port, or touch DTR or RTS
- request the bootloader
- pass `--upload-port` (the build tool discovers the port itself; kept as stated)
- contain a sleep, poll or timeout; "the bound belongs to the caller" (GAP 3)

## TOOL guards outside the tool

- Lock hook `locked-files.py` blocks edits to the tool unless `LOCKED_FILES_OK=1`. **[stated]**
- Port hook `guard-port-ops.py` blocks raw writes to `/dev/tty*` and `/dev/serial/*`. **[stated]**
- Tests `test_board_arbitration.py` cover node states, refusals, `--flash-file`, the inotify wait.
  **[stated]**

---

## DECIDE answers derived from evidence (2026-11-16, Claude; Peter may overrule)

Each line: the constraint, then the conclusion.

1. **Interlock and `protected`: restored, in a new form.** Constraint: the bench is generic and may
   hold several boards (brief rule 9), and the removed thing was the *roster* (a hardcoded unit
   list plus sysfs/by-id lookups), which cannot be generic. Keep the guarantee, drop the roster:
   the caller names a serial, the tool reads the attached serial from udev `ID_SERIAL_SHORT`,
   mismatch or two candidates is refused. `protected` lives in the device profile as data.
2. **The bound is a required argument supplied by the caller, with no default in the tool.**
   Constraint: the dump says "the bound belongs to the caller", and an unbounded wait cannot
   report failure (GAP 3). The service derives it from a measured re-enumeration time
   (about 1.5-2x the measurement). The tool refuses to start the wait without one.
3. **One actor causes the transition per flash; the tool is never that actor.** Constraint: pio's
   upload already does the 1200-baud touch, and a second touch is a second reset (brief rules 1-2).
   On the `--flash` path pio is the single actor. On the `--flash-file` path the service calls the
   driver's `enter_bootloader()` (brief section 3) first. Proof: the device's boot counter advances
   by exactly 1, measured, not inferred.
4. **Closed: not an issue.** Constraint: Peter states the port-discovery failure is specific to
   this host's USB pass-through and custom `/dev` naming, absent on PVE, Pi or VM hosts. No
   investigation, no workaround. The `--upload-port` ban stays as stated.
5. **Guards are written once, as a library; the CLI is a thin front end.** Constraint: the product
   is the HTTP/MCP service, and two copies of the guard rules would drift (the same failure the
   brief warns about for a per-project count). Both the service and the CLI call the library.
6. **Family and bootloader type come from a profile keyed by USB VID:PID and serial; the caller
   names only the serial.** Constraint: one board has two USB identities. Peter's `lsusb` (2026-11-16):
   run `239a:8029` "WisCore RAK4631 Board", DFU `239a:0029` "WisBlock RAK4631". The brief recorded
   the bootloader as `239a:002a` and says the board file lists `0029`, `002a` and `802a`, so the
   profile holds a **set of ids per mode**, not one, and the product strings differ between modes
   too. VID:PID alone cannot identify a board; the serial survives the transition.

## TOOL command surface — one definition, three front ends (decided 2026-11-16)

Peter's requirement: a structured CLI that matches the API, so a command can be called over the API
or from the console, with one source of truth.

- **One command registry** in the library: each command is declared once — name, typed arguments,
  handler, capability gate, result schema, and whether it streams. The CLI, the HTTP API and the MCP
  tools are all generated from it. Adding a command is one row, as in upstream's `SPECS`.
- **Neither front end calls the other.** The API does not shell out to the CLI (subprocess cost,
  structure lost through stdout, no clean streaming). The CLI does not require a running service
  (the console must work when the service is down or being recovered). Both call the library.
- **Exclusion is host-wide, not in-process.** The single-instance `flock` and the port-holder check
  are OS-level, so a console command and a service request cannot collide.
- **Same result, same refusal.** The CLI's `--json` output is the API's response body. A refusal is
  a typed result with a stable code (state, holder, serial, protected, bad package); the CLI exit
  code and the HTTP status are both derived from that code.
- **Streaming commands** (monitor, watch, RTT) are declared as streams: CLI prints lines, API uses a
  websocket, MCP polls.
- **Capability gating applies to all three.** A command the host or the attached family cannot do is
  absent from CLI help, the API and the MCP, not present and failing.

## VERIFY acceptance (measured, not read)

Carried from the brief, section 8, to be run against this tool:
- 10 consecutive flashes, each succeeding first invocation, no manual reset
- exactly 1 device reset per flash, from the device's own boot counter
- a monitor open before a flash is running after it, 10 of 10
- a flash with a foreign holder is refused, naming the holder, 10 of 10 — **including a holder
  reached through an alias path**
- wrong serial refused; two candidate boards refused; `protected` refused
- a truncated, `.hex`, or wrong-SoftDevice package refused before any write
- a healthy-duration check: a flash that finishes faster than the measured healthy time is reported
  as suspect, not as success
