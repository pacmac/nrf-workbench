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
   **Why it was removed, read from the code [verified: `rak-comm.py` header, 2026-11-16]:** this
   host hands over only two named nodes (`/dev/lxc-serial/rak4631-run` and `-dfu`), with no sysfs
   USB tree and no `/dev/serial/by-id`, so there is no serial to compare. Peter ruled on
   2026-11-16 that the tool use only those two nodes. It was a host limit, not a design choice.
   The 2026-08-28 ruling ("any board can be any site, do NOT start hard-coding them") forbids a
   roster of named boards; it does not forbid checking a serial the caller names.
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
9. **The dump differs from the code it describes [verified: read `rak-comm.py`, 428 lines,
   2026-11-16].** Do not port these:
   - **`--build` and `--status` do take the lock.** `main()` calls `claim_the_link()` before it
     dispatches any mode, so a build or a read is refused while another instance flashes. The dump
     says "no USB, no lock". Reads and builds must take no lock.
   - **A refused flash prints only the last 800 characters** (`blob.strip()[-800:]`). Brief rule 7:
     return the whole output, never a tail.
   - **Exit codes differ from `core.md`.** The tool uses 1 for failure, 2 for a bad file or a
     board not in the bootloader, 3 for a missing node or a holder. `core.md` maps by class
     instead; the difference is deliberate (see `core.md`), not compatibility.
   - **The holder check compares `os.path.realpath`**, so a node reached through a different path
     to the same device is missed. Confirms GAP: port-holder check misses aliases.
   - **The `--upload-port` ban has a host-specific reason:** pio resolves the port from the board
     manifest's `build.hwids`, which this host's named nodes cannot match. On a host where the
     board enumerates as `ttyACM*`, pio's own discovery works. The ban is a workaround for this
     host's pass-through, not a principle; it stays only where pio is used to flash.

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
  Must match by device number, not path (GAP: port-holder check misses aliases).
- **One attempt.** Never retry. A retry is another reset. **[stated]**
- **Verdict.** `--flash` succeeds only if the build tool exits 0 and prints `Device programmed.`.
  `--flash-file` is judged by nrfutil's exit code. (Both strengthened by GAP: the verdict is weaker than the brief.)
- **Build first, unlocked.** A failed build disturbs nothing: no lock, no port touched.
- **Input checks for `--flash-file`.** The file must exist and be a zip containing `manifest.json`,
  so a `.hex` or truncated download is refused before any write. (Brief also wants a wrong
  SoftDevice refused before write; `softdevice_req` must match. **[brief]**)

## TOOL never-does

- open a serial port, or touch DTR or RTS
- request the bootloader
- pass `--upload-port` (the build tool discovers the port itself; kept as stated)
- contain a sleep, poll or timeout; "the bound belongs to the caller" (GAP: no timeout contradicts the inotify wait)

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
   mismatch or two candidates is refused. `protected` is held per serial (`devices.md`), not in
   the profile. **Capability-gated:** on a host like this one, whose nodes carry no serial, the
   check cannot run. It must then say so (`serial_checked: false` in the evidence) and not read as
   passed; protection then applies per named node (`devices.md`). A check that could not run must
   not look like a check that passed.
2. **The bound is a required argument supplied by the caller, with no default in the tool.**
   Constraint: the dump says "the bound belongs to the caller", and an unbounded wait cannot
   report failure (GAP: no timeout contradicts the inotify wait). The service derives it from a measured re-enumeration time
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
- **Neither front end shells out to the other.** The API does not run the CLI (subprocess cost,
  structure lost through stdout, no clean streaming). One-shot commands run in the CLI process
  through the library and need no service (the console must work when the service is down or being
  recovered). **Stateful commands** (sessions, monitors) are the exception: the CLI sends them to the
  running service over its HTTP API, and refuses with `service_not_running` if none answers. Narrowed
  in `api.md`; both still use the one registry.
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
