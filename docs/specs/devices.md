# Devices — functional spec (DRAFT, not agreed)

How nrf-workbench knows what is attached, what it can do, and when it may be touched. Inputs: the
brief (sections 2, 3, 7), `programmer.md`, `architecture.md`, and Peter's `lsusb` of a RAK4631
(2026-11-16). Tags: **[stated]** Peter said it; **[brief]** from the brief; **[unknown]** nobody
has looked.

Area: `devices` (library). Depends only on `core`.

---

## GAP defects first

1. **The serial is claimed stable across the app/bootloader transition, and only verified on one
   board.** The brief (rule 3) says the serial does not change on a RAK4631. Identity-by-serial
   rests on that. For any other family it is **[unknown]**, and a family where the bootloader
   reports a different serial would make a mid-flash board look like a new, unknown one.
2. **The role of `239a:802a` is unknown.** The brief says the board file lists `0029`, `002a` and
   `802a`; Peter's `lsusb` shows app `8029` and DFU `0029`. Which of the three are bootloader ids
   and which are app ids of other variants is not established.
3. **Knowing which board is attached and having a node to open are two different questions, and
   my first draft conflated them.** It said identity by serial cannot work on this host. That was
   wrong. **[verified by `probe_host.py`, 2026-11-16]** `/run/udev` is absent here, but sysfs
   (`/sys/bus/usb/devices`) lists the RAK4631 with its ids, product string and serial, so identity
   by serial CAN work without udev. What is missing is the **node**: `/dev/lxc-serial/` is empty
   and there is no `ttyACM*`, so there is nothing to open, and the existing flash tool would report
   `absent` for a board that is plugged in. The spec below therefore separates an identity source
   from a node resolver (see Identity and node). The existing tool's header claims this host has
   no sysfs USB tree; that claim is stale for the host as it is today.
4. **`protected` is a property of a unit, not of a board model.** The brief puts it in the profile.
   A RAK4631 is protected because *that one* is mid-measurement; another of the same model is not.
   Profile-level protection would block every board of the model or none (see Protected, below).

---

## Files this spec allows

The files the entry-method build task may create, and nothing else:
`nrf_workbench/__init__.py`; `nrf_workbench/core/__init__.py`, `core/result.py`;
`nrf_workbench/devices/__init__.py`, `devices/profile.py`, `devices/identity.py`,
`devices/events.py`, `devices/entry.py`; `nrf_workbench/guards/__init__.py`, `guards/lock.py`,
`guards/checks.py`, `guards/ledger.py`; `profiles/rak4631.yaml`; `tests/devices/`, `tests/guards/`, `tests/core/`.

## TOOL what a device is

A **device** is one physical board, identified by its **USB serial** (udev `ID_SERIAL_SHORT`). A
**slot** is a physical USB hub port (udev `ID_PATH` prefix), as upstream. Both are recorded:
the serial says *which board*, the slot says *where it is plugged*. A board moved to another port
is the same device in a different slot; a different board in the same port is a different device.
**[brief]**

A device is matched to a **profile** by USB `VID:PID` in either mode, then confirmed by serial.
Nothing is matched by `/dev` name: those are aliases the host may rename or customise (Peter's
pass-through). The node path is an *output* of identification, never an input.

## TOOL identity and node

`identify()` answers two separate questions with two separate mechanisms, each chosen by what the
host can do (capabilities, see `core.md`). Everything above them sees one device record.

**1. Identity source: which board is attached.** Ids, product string, serial, hub port.

| source | where it works | notes |
|---|---|---|
| **sysfs** | any Linux with a USB tree in `/sys/bus/usb/devices` | the baseline; needs no udev. Verified on this host. |
| **udev** | a host with a udev database | adds `ID_PATH` and `ID_SERIAL_SHORT` properties; optional enrichment |

**2. Node resolver: which path can be opened for it.** A serial from sysfs says nothing about a
path. The join is by device number: the tty class entry under the board's sysfs interface gives
`major:minor`, and a node whose `st_rdev` matches is that board's node.

| resolver | where it works |
|---|---|
| **kernel tty link** | sysfs shows a `tty/ttyACM*` under the board's interface: match its `major:minor` to a node in `/dev` |
| **by-id** | `/dev/serial/by-id/*` exists (the name carries the serial) |
| **node-map** | host configuration names which node means which slot and mode, for a host that presents named nodes only (this host's `/dev/lxc-serial/rak4631-run` and `-dfu`) |

- A **node-map** is a map of nodes to modes, not a roster of boards, so it does not reintroduce
  what the 2026-08-28 ruling forbids.
- **Absent has three different meanings and must not share a word:** *the board is not attached*
  (no sysfs entry), *the board is attached but no node was presented* (sysfs entry, no node:
  exactly this host today), and *a node exists but no board claims it*. The state is `absent`
  only for the first. The second is its own state (`attached_no_node`). `docs/spec/rak-comm.md`
  calls the same mistake RN-4: "absent is not a reading".
- **Serial check.** With an identity source the serial is known. If the node cannot be joined to
  that serial (no tty link, no by-id, node-map only), the guard can prove *a board with that
  serial is attached* but not that *this node is that board*. The record says
  `node_joined: false`, and a guard that needs the join refuses with `serial_unverifiable`. **A
  check that could not run must not read as a check that passed.**
- `protected` applies per serial where one is known, and per slot label otherwise.
- A host that cannot read any identity source never guesses a mode from a name.

## TOOL USB inventory and classification

Added after Peter pointed out that the ESP workbench auto-detects every USB connection, identifies
it and lists it at the top of its dashboard (`dashboard.md`). `devices` therefore inventories
**every USB device on every port, not only boards**, and classifies each.

| class | meaning |
|---|---|
| `board` | matched to a profile (a DUT) |
| `debug-adapter` | a USB serial adapter: a candidate, or a declared console link (`console.md`) |
| `swd-probe` | a debug probe for SWD (`swd` area) |
| `ble-radio` | a dedicated BLE radio (the nRF52840 dongle, `ble` area) |
| `power-profiler` | a current-measurement instrument (`instruments` area) |
| `hub` | a USB hub or root hub (shown only as the parent of a port) |
| `other` | identified, but not a bench device (an audio device, a keyboard hub, a Zigbee stick) |
| `unrecognised` | nothing identifies it: shown with its product string, never guessed |

- **Identification is data, in `usb_ids.yaml`**, mapping VID:PID to a class and a name, with the
  source of each row (verified on a bench, or recalled). Board profiles add to it. It is read by
  every front end, so the CLI, API, MCP and dashboard identify a device identically. Upstream
  keeps its table in browser JavaScript and it already misses this bench's dongle
  (`1a86:55d3` listed, `1a86:55d4` present). **[upstream; probe]**
- **Seen on this bench, from the probes (ids only; serials are not recorded here):**

  | VID:PID | what the probe reported | class |
  |---|---|---|
  | `239a:8029` | RAKwireless "WisCore RAK4631 Board" | `board` (app mode) |
  | `239a:0029` | "WisBlock RAK4631" (Peter's `lsusb`, bootloader) | `board` (bootloader mode) |
  | `1a86:55d4` | "USB Single Serial" | `debug-adapter` (the CH343-class dongle) |
  | `0665:5161` | Cypress "USB to Serial" | `debug-adapter` candidate, **undeclared** |
  | `10c4:ea60` | Nabu Casa "SkyConnect v1.0" | `other` (a Zigbee radio) |
  | `0bda:8922`, `2357:0604` | Realtek and TP-Link Bluetooth adapters | `other` (host Bluetooth, not the nRF bench radio) |
  | `1a86:5722` | "UsbMonitor", a 3.5-inch display | `other` |

  The classes are my reading of the product strings **[unverified]**; Peter confirms or corrects
  them, because a wrong class here is a wrong label on a port someone trusts.
- **A device on a port that is not a configured slot is still listed**, as an unassigned port, so
  nothing attached is invisible. A configured slot with nothing on it is `absent`.
- **Slots can be learned from observed ports** (the hub-port key from the udev `ID_PATH` or the
  sysfs path, normalised so two names for one port are one key), as upstream's `rfc2217-learn-slots`
  does, and are then written to host configuration.
- **Detection is event-driven.** A device appearing or disappearing emits `devices.changed`
  carrying that port's full record, so a front end updates one card without re-reading the
  rest. No timer is involved.
- An unrecognised or undeclared serial adapter is classified and listed and **never opened**
  (`console.md`).

## TOOL device state

Five states as the existing flash tool reports them **[stated]**, plus two it could not tell apart
from them (`attached_no_node`, which it reported as `absent`) or does not have (`handover`):

| state | meaning |
|---|---|
| **app** | exactly one node, its ids in the profile's app set |
| **bootloader** | exactly one node, ids in the bootloader set |
| **two** | an app node and a bootloader node at once; refuse to act, name both paths |
| **unrecognised** | a node exists, no profile claims its ids; refuse, name the ids |
| **absent** | no board attached: no sysfs entry for any board the profiles know |
| **attached_no_node** | a board is attached (sysfs lists it) but no node was presented to open; flash and monitor are refused, and the reason names the missing node |
| **handover** | absent, but a flash or mode change is in progress and the lock holder expects it |

**A mode change passes through absent. That gap is the handover, not a fault.** **[brief]** State
is derived from kernel events and the current device list, never from a fixed sleep. Silence is not
death: a device in app mode can be quiet (sleeping radio, DTR-gated CDC) and must not be reported
dead or trigger recovery. **[brief]**

## TOOL profile

Board facts are data, one YAML file per board model under `profiles/`, validated against a schema.
No board fact appears in code. **[brief]** Fields:

```yaml
name: rak4631
family: nrf52
chip: nrf52840
modes:
  app:         { ids: ["239a:8029"], product: "WisCore RAK4631 Board" }
  bootloader:  { ids: ["239a:0029", "239a:002a"], product: "WisBlock RAK4631" }
bootloader:
  type: adafruit-serial-dfu      # nordic-secure-dfu | nordic-legacy-dfu | mcuboot-serial | uf2 | swd-only
  entry: 1200-baud-touch         # double-tap-reset | gpregret-swd | gpio-on-reset | none
  baud: 115200
  package: dfu-zip               # what a flash accepts; a .hex is refused
memory:
  softdevice: { name: S140, version: "6.1.1", fwid: 182 }   # fwid 182 = S140 6.1.1 [brief]
serial_stable_across_modes: true # VERIFY per family; false means identity needs a second key
```

- **An id set per mode, not one id.** Two ids appear for the bootloader across sources (`0029`
  stated, `002a` in the brief). `802a` is listed in the board file with no known role (GAP: the role of `239a:802a`) and
  is deliberately **not** in the profile until its role is measured.
- **Product strings are recorded per mode and match case-sensitively or not at all** (`RAKwireless`
  vs `RAKWireless` seen). They corroborate; they never identify.
- Board-specific pins, the reset line and timings live here, never in code. **[brief]**

## TOOL entering the bootloader without a hand

Peter, 2026-11-16: an nRF does not need to be put into DFU by hand; the control lines can do it. A
human double-tap is only the manual fallback. The profile's `bootloader.entry` names which method
this board uses, and the driver's `enter_bootloader()` performs it.

| `entry` | what causes the transition | needs | state on the current bench |
|---|---|---|---|
| `1200-baud-touch` | open the board's own USB CDC at 1200 baud and close it; the application resets into the bootloader (the exact line-coding and DTR sequence is **[recalled]** from the Adafruit core) | nothing wired; the board's CDC node | possible now; `pio`'s upload already does it |
| `gpio-on-reset` / `double-tap-reset` | two reset pulses inside the bootloader's double-reset window, on a RESET line | a host line wired to RESET: a Pi GPIO, or an adapter's RTS or DTR | **not stated as wired** |
| `dfu-pin` | hold the DFU or boot pin low through a reset | a wired DFU pin and a reset | not stated as wired |
| `gpregret-swd` | write the bootloader's entry marker into `GPREGRET` over SWD, then reset (the value comes from the bootloader source, not from memory) | an SWD probe | no probe attached |

Rules:

- **Exactly one transition per flash, so exactly one actor** (brief rule 2). If the uploader already
  does the entry (`pio`'s touch), the profile says `entry_performed_by: uploader` and the driver
  does not repeat it: a second touch is a second reset. `enter_bootloader()` is for a flash path
  where nothing else will do it, and for causing a mode change on purpose (such as observing it).
- **A control line is declared in the slot's wiring and owned here**, never by `console`
  (`console.md`). A wired RTS-to-RESET or GPIO-to-RESET is a **reset line** with a declared voltage
  domain, and an unverified domain is refused, as for debug links.
- **The double-reset window is measured on the real board, not chosen.** The pulse spacing the
  bootloader accepts is a number to read from the bootloader and confirm with a measured pulse, and
  the host's own timing jitter is measured too (brief section 5b), because a pulse sent over HTTP
  would be too coarse; the timed pulse runs on the host, not across a request.
- **A mode change passes through `absent`.** `enter_bootloader()` waits on the handover event with a
  caller-supplied bound, never a sleep.
- **It is verified, not assumed.** The device's own boot counter (read through the debug link,
  `console.md`) must advance by exactly 1 for the transition.
- **The probes never do it.** Observing a mode change (serial stability, the id sets) uses the
  profile's method through the driver once it exists, or the manual fallback until then.

### The way back, and the authorisation (added 2026-11-16)

**GAP: with only USB, the touch has no software way back.** Putting a board into its bootloader
leaves it there until something resets it. With nothing wired (Peter, 2026-11-16: USB only) there
is no reset line, no DFU pin and no SWD probe, so the only software way out is a flash, which ends
with the bootloader activating the image and resetting; or a power cycle. A board stranded in the
bootloader is the 2026-07-27 incident (a RAK parked in DFU for seven hours, an overnight
measurement lost). So **the entry method refuses unless the unit declares how it comes back**:

- `return_path` is declared per unit (host configuration, `units.yaml`) as one of `flash` (a DFU
  package that is a zip containing `manifest.json`, checked to exist and to be shaped as a package),
  `reset-line`, `swd`, or `power-cycle` (only where per-port power control exists and is declared).
- Without one, `enter_bootloader()` refuses with **`no_return_path`**, before it touches anything.
- It does not perform the return itself: the return is the programmer's flash (`programmer.md`),
  which keeps exactly one actor per transition. Entry only guarantees there is a way.
- **Which package returns a given unit is Peter's call**: flashing writes firmware, and a package
  from another build may differ from what the unit runs. A built package for the RAK exists in the
  `mt-alarm` project (`.pio/build/v3/firmware.zip`), but which build the unit runs is not known.

**Authorisation.** Peter authorised `RAK_PORT_OK` for `nrf_workbench/devices/entry.py` **only**
(2026-11-16, "yes authorised"). The port-guard hook passes a file whose own text carries the marker,
so it lives in that file's header with this record (the precedent is `rak-dongle.py`, 2026-08-12).
It is never placed on a command, and it does not cover any other file; each further file that trips
the hook is reported and needs its own yes. The code names no port path anywhere: the node is found
by identity, so no command that runs it contains one.

### How a board leaves the bootloader, and never touching it twice (researched 2026-11-16)

Peter asked for two things: that a second call must never touch a board already in DFU ("I recall
that can brick it / make it unresponsive"), and to search the net for a programmatic exit. The
answers below are read from the bootloader's own source (`Adafruit_nRF52_Bootloader`, `master`,
fetched that day), the family the RAK4631 uses. **[unknown]**: the RAK's build may differ from
`master`.

**The exit, from the source:**

1. The serial DFU protocol has four packet types only: START, INIT, DATA and STOP_DATA
   (`dfu_transport_serial.c`). There is **no reset, exit or boot-the-app command.**
2. **A START packet erases the application** (`dfu_start_pkt_handle` calls
   `dfu_prepare_func_app_erase`), so "start an update and abort it", which a forum thread suggests,
   is not an exit: it leaves an erased app and the bootloader waiting. Never send DFU packets unless
   a complete valid image follows.
3. **Every reset runs the boot decision (`check_dfu_mode`).** With a valid app, no DFU magic in
   `GPREGRET`, no double-reset marker and no DFU button, it starts the app. So the exit IS a reset.
   A reset can come from a RESET pin, SWD, a power cycle, **or a completed flash**: STOP_DATA
   validates and activates the image, the transfer ends and the board starts the app.
4. A timeout exists only for a board entered by the application's single-tap, UF2 or serial-only
   magic, and **only while USB has not enumerated** (3000 ms, cancelled once USB enumerates:
   `bootloader_dfu_start(.., 3000, true)`). On USB power the board stays. A board entered by double
   reset has no timeout. This matches the 60 s passive watch in which the RAK did not leave.
5. `DFU_MAGIC_SKIP` in `GPREGRET` makes the next boot skip DFU, but only the application or SWD
   can write it. The ESPHome and Zephyr "reboot" behaviour found in a search is application-side.

**So with USB only, the one software exit is a completed flash of a valid package.** A reset line,
an SWD probe or a power cycle would also work; none exists on this bench. That is why
`no_return_path` exists.

**The "brick" worry, honestly:** the bootloader's sources contain no handler for a baud change or
line state, so a second touch against a board already in the bootloader changes nothing there. **I
could not confirm a brick from the source.** What the source does show is that an interrupted
transfer leaves an invalid or erased application with the bootloader still alive (recoverable by
another flash). The rule stands regardless, because a repeat touch is unnecessary risk and brief
rule 2 says exactly one transition.

**What prevents a second touch (all tested):**

- A board **already in the bootloader** is never touched: the state is read first and the call
  returns `already_in_bootloader`.
- **A touch ledger** records, before the touch is made (write-ahead), the board's serial and its
  **USB enumeration** (`busnum:devnum`, which changes on every re-enumeration). A later call for
  the same serial is refused with **`touch_pending`** while the board is still on that same
  enumeration, because a second touch then would be a second reset in quick succession (the
  double-reset detector). It is allowed again once the board has re-enumerated, which is
  evidence the first transition resolved. If the enumeration cannot be read, it is refused, never
  guessed. A touch that fails or times out also blocks until then. Clearing the ledger is an
  explicit human decision (`--forget-touch`), never automatic.

## TOOL protected

`protected` is a flag **per serial**, held in host configuration (`units.yaml`), not in the
profile. The profile may carry a default (normally false). Reason: protection is about a unit's
situation (mid-measurement for days), which changes without the board model changing (GAP: protected is per unit).
Only exceptions are listed, so this is not the removed roster: a board not named in `units.yaml`
is flashable by default. A flash of a protected unit is refused by `guards` (see `programmer.md`).

## TOOL hotplug and slot state

- **The baseline event source is inotify on the node directory, not udev.** [verified: this host
  has no `/run/udev`, and the existing tool already chose inotify for that reason]. udev
  monitoring is an optional extra where present. The wait is **level first, then watch, then
  level again**: read the state, add the watch, read the state once more (a node created between
  the first look and the watch would otherwise raise no event), then loop on events, re-reading
  the level every time. An event only means "look again"; the level is never inferred from it.
  The component never contains a sleep; every wait takes a required caller-supplied bound.
- **State is owned by whoever holds the lock.** While a flash holds the lock, `add` and `remove`
  events for that serial are recorded but do not reset state or restart anything. This closes
  upstream's hazard: its hotplug handler resets state to idle and reopens an exclusive proxy on
  any `add` outside a debug session, which a re-enumerating nRF flash triggers. **[verified by
  reading upstream `portal.py` at commit `db6a093`, 2026-11-16; not run]** `_handle_hotplug` ignores
  events only while recovering, and suppresses an `add` only while the slot is debugging
  (`STATE_DEBUGGING`, ~line 3036); every other event goes on to flap counting and state updates.
- **No automatic recovery that unbinds a device while a flash or handover is in progress.**
  **[verified by reading, same commit]** upstream counts every event toward `FLAP_THRESHOLD` (10 in
  `FLAP_WINDOW_S` 30, ~lines 71-72 and 3050-3084) and calls `_start_flap_recovery`, which uses
  `_usb_unbind` (~line 296). A DFU cycle is exactly the event burst that counts. Not run.
- Other-family USB events (a serial adapter, a CMSIS-DAP probe, the BLE dongle) are classified as
  non-board devices and are never treated as an unrecognised board.

## TOOL the family driver interface

Every family implements the same small interface, and a driver that cannot do a thing omits it,
so the command is absent (see `architecture.md`, capability gating). **[brief, section 3]**

```
identify()            -> device, profile, state
capabilities()        -> the set of things this device can do on this host
enter_bootloader()    -> one transition, by the entry method in the profile
flash(package)        -> result with output AND duration (see programmer.md)
reset()               -> one reset
console()             -> a stream from a SEPARATE debug channel (a UART link, RTT over SWD, NUS
                         over BLE; `console.md`). The DUT's own USB CDC is not a default source:
                         a profile must declare it safe. Never opens a port at 1200 baud (that
                         open IS bootloader entry, brief rule 1).
debug()               -> a debug session (SWD area)
read_flash(range)     -> bytes
recover()             -> bring a dead chip back (SWD area)
ota(package)          -> over-the-air update (BLE area)
```

**Per-slot wiring is not yet specified here, and two areas need it.** The debug link (which
adapter or header UART is wired to which DUT, at what voltage, with host TX connected or not, and
whether DTR or RTS reach a reset line) is declared in `console.md` as `links.yaml`; the pin layer
(`gpio`) needs a wiring map of Pi pins to DUT pin names (brief section 5b). Both describe the same
slot, and a Pi header pin used as a UART must never also be offered as a GPIO. The declaration
belongs here, once, read by both, so neither imports the other. To be specified before either
area is built.

Drivers are looked up by `bootloader.type` and by the probe type. Each is one module; none imports
another driver. Methods that touch hardware go through `guards`; the interface itself holds no
safety rules, so SWD and USB flashing cannot diverge.

## DECIDE derived (Claude; Peter may overrule)

1. **Identity comes from sysfs (udev optional), and the node is resolved separately; the node path
   is only an output.** Constraint: the board changes USB ids between modes and the node can be
   renumbered or aliased, and only the serial survives all of that; sysfs gives the serial on every
   host with a USB tree (verified on this one, without udev); the node is a different question and
   on this host is not presented at all, so a resolver (tty link, by-id, node-map) is required, and
   any check that could not run is reported as not run.
2. **`protected` is per serial in host config, profile gives a default.** Constraint: protection
   follows the unit, not the model (GAP: protected is per unit), and the dump removed a hardcoded roster, which an
   exceptions-only file does not reintroduce.
3. **Drivers do not hold safety rules.** Constraint: SWD and USB both flash, and two copies of the
   rules would drift (`programmer.md` decision 5). The rules live once in `guards`.
4. **`802a` stays out of the profile.** Constraint: its role is unknown, and an id in the wrong
   set could make an app look like a bootloader. Measure first.
5. **State and waits are event-driven.** Constraint: a fixed sleep is a blind bet; the transition
   passes through absent and an event, not a clock, says when it is over.

## VERIFY

- **Serial stability, per family:** read the serial in app mode and in bootloader mode on each
  board model before its profile sets `serial_stable_across_modes: true`. RAK4631 is the only one
  with a recorded observation.
- **`802a` role, partly answered (2026-11-16):** the project's own board file
  (`mt-alarm/device/rak/boards/wiscore_rak4631.json`) lists exactly four `hwids`:
  `239A:8029`, `239A:0029`, `239A:002A` and `239A:802A`, with `usb_product` "WisCore RAK4631 Board",
  `use_1200bps_touch` true and `wait_for_upload_port` true. 8029 is the application (observed), 0029
  and 002A are bootloaders (both observed, on two units or two bootloader builds). So `802A` is the
  fourth id of the same board family, with the same pattern as 8029 (a high-bit application-style
  PID), but **its role is still not established** by any run, so it stays out of the profile until a
  unit is seen enumerating as it. The file confirms the touch is the board's own entry method.
- **The RAK's LEDs (read from the project's variant, 2026-11-16):** `LED_GREEN` is pin 35 and
  `LED_BLUE` pin 36, both active high (`LED_STATE_ON 1`). This is what a parking image can blink as
  a human-visible "the application is running" signal (see `testpartner`).
- **Event source, two hosts probed (2026-11-16).** The dev LXC has no `/run/udev`: inotify on the
  node directory is the baseline there. The bare-metal Proxmox host has `/run/udev` and `udevadm`
  257, so udev is available there. **Event delivery itself is untested on both** (a mode change
  was not made). Still **[unknown]** on a Pi and a plain VM; probe, never assume.
  **Dry run on the Pi, 2026-11-16 [verified]:** the product code read the board's identity from
  sysfs (app mode, serial present, node joined by device number, held by nobody), passed the whole
  guard chain, called a RECORDER in place of the touch exactly once (no port opened), and waited
  1.004 s on the real kernel uevent socket against a 1.0 s bound before reporting
  `bootloader_not_reached`. So the uevent socket **opens** on a bare-metal Pi as root; whether it
  **delivers** the re-enumeration event is still unproven until a real mode change.
- **Node resolver on the Proxmox host [verified by `probe_host.py`]:** the RAK4631 in app mode is
  `/dev/ttyACM1` (`166:1`), reachable by `/dev/serial/by-id/...<serial>-if00` and by two
  `by-path` names (`usb-0:3:1.0` and `usbv2-0:3:1.0`, the same node), with udev `ID_SERIAL_SHORT`
  equal to the sysfs serial and `ID_PATH` `pci-0000:0d:00.0-usb-0:3:1.0` as a hub-port slot key.
  So the tty-link and by-id resolvers both work there and the join by `major:minor` is real. Two
  `by-path` names for one node means a slot key must be normalised, not compared as a string.
- **Numbering is not stable across hosts, measured.** The same two devices (the board and the
  debug dongle) were `ttyACM1` and `ttyACM0` on the Proxmox host and `ttyACM0` and `ttyACM1` on
  the Pi, numbered by plug order. The hub-port keys differ per host too (the Pi's end
  `usb-0:1.3:1.0` and `usb-0:1.1:1.0`, behind the Pi's own USB hub). So **identity is the serial
  and the ids; the node number and the hub-port key are both host-local outputs**, and a slot is a
  per-host mapping from a hub-port key to a label, learned on each host. **[verified, `probes.md`]**
- **`unrecognised` is not hypothetical.** The same host has `/dev/ttyUSB0` as a non-character
  entry (`major:minor` `0:0`, no udev properties), most likely a regular file created by something
  that wrote to a path before a device existed. The state table's refusal for "a node that is not a
  character device" would have caught it.
- A `two`, `unrecognised` and `absent` state each reproduced against the fake `/dev` fixtures
  (harness), with no hardware.
- A re-enumeration during a held lock leaves state unchanged and restarts nothing.

## TOOL names (added 2026-11-18, Peter: "we can associate a macid or serial number with a name cant we ? ... and save it in a local .yaml or json")

A board has a **name**: a label a person gave it, kept **against its USB serial** in `<paths.wiring>/device-names.json`
(`{"names": {"<serial>": "<name>"}}`). **The serial still identifies the board**; a name never selects, matches or guards
anything, so a flash, a protected unit and every refusal still go by serial. **[derived]**

- `devices.name(serial, name)` and `devices.unname(serial)`; `devices.list` and the live board list carry `name` (null when
  there is none). Both naming commands need the folder (`paths.wiring`), so they are absent without it.
- The name follows the board to any port, and survives a service restart and the board being unplugged.
- Rule: the project name rule (1 to 64 of `a-z 0-9 . _ -`, starting with a letter or digit). **Unique** across boards
  (`name_taken`). The serial must be attached when naming (`node_absent`), so a typo is never stored.
- The file is plain JSON, written atomically, **read on every use**, so a hand edit takes effect at once. One that cannot be
  read never takes the board list down (names are labels, so the list shows none), and `devices.name` then fails
  (`write_failed`) **without overwriting it**.
- ESP32 targets already carry their name in `esp32.json` (`esp32.declare`, `esp32.rename`); the two namespaces are separate.
- The page: a Name row on each Devices card (Name it, Rename, Remove name); the name shows in the Attached band, the flash
  dialog and the board picker. Not done: the Wiring page's device cards still show the serial only.
