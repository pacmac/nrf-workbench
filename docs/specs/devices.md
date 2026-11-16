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
3. **Identity by serial cannot work on every host, and this is one that it cannot.**
   **[verified, 2026-11-16]** `/run/udev` does not exist here, `pyudev` is not installed, and the
   existing flash tool documents that this host hands over only two named nodes
   (`/dev/lxc-serial/rak4631-run` and `-dfu`), with no sysfs USB tree and no
   `/dev/serial/by-id`. A node carries no serial. The spec below therefore has two identity
   providers, not one (see Identity providers).
4. **`protected` is a property of a unit, not of a board model.** The brief puts it in the profile.
   A RAK4631 is protected because *that one* is mid-measurement; another of the same model is not.
   Profile-level protection would block every board of the model or none (see Protected, below).

---

## TOOL what a device is

A **device** is one physical board, identified by its **USB serial** (udev `ID_SERIAL_SHORT`). A
**slot** is a physical USB hub port (udev `ID_PATH` prefix), as upstream. Both are recorded:
the serial says *which board*, the slot says *where it is plugged*. A board moved to another port
is the same device in a different slot; a different board in the same port is a different device.
**[brief]**

A device is matched to a **profile** by USB `VID:PID` in either mode, then confirmed by serial.
Nothing is matched by `/dev` name: those are aliases the host may rename or customise (Peter's
pass-through). The node path is an *output* of identification, never an input.

## TOOL identity providers

`identify()` is answered by one of two providers, chosen by what the host can do (a capability, see
`core.md`). Everything above the provider sees the same device record.

| provider | where it works | what it knows |
|---|---|---|
| **udev** | a host where USB devices appear in sysfs and `/dev/serial/by-id` (Pi, PVE host, most VMs) | VID:PID per mode, product string, **serial**, hub port |
| **node-map** | a host that hands over named nodes only (this host's `/dev/lxc-serial/*`) | which named node exists; **no serial** |

- A **node-map** is host configuration: for each slot, which node path means which mode
  (`run` and `dfu`). It is a map of nodes to modes, not a roster of boards, so it does not
  reintroduce what the 2026-08-28 ruling forbids.
- On node-map, **the slot is the identity** and a serial is unknown. The record says so
  (`serial: null`, `serial_checked: false`). A guard that needs a serial refuses with an explicit
  `serial_unverifiable`, or proceeds only where the caller did not name one, and always reports that
  the check did not run. **A check that could not run must not read as a check that passed.**
- `protected` applies per serial where a serial exists, and per slot label on a node-map host.
- A node-map host never guesses a mode from a VID:PID it cannot see.

## TOOL device state

Five states, as the existing flash tool reports them **[stated]**, plus one that is not a fault:

| state | meaning |
|---|---|
| **app** | exactly one node, its ids in the profile's app set |
| **bootloader** | exactly one node, ids in the bootloader set |
| **two** | an app node and a bootloader node at once; refuse to act, name both paths |
| **unrecognised** | a node exists, no profile claims its ids; refuse, name the ids |
| **absent** | no node |
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
console()             -> a stream (serial / RTT / NUS, per what the profile offers); never
                         opens a port at 1200 baud (that open IS bootloader entry, brief rule 1)
debug()               -> a debug session (SWD area)
read_flash(range)     -> bytes
recover()             -> bring a dead chip back (SWD area)
ota(package)          -> over-the-air update (BLE area)
```

Drivers are looked up by `bootloader.type` and by the probe type. Each is one module; none imports
another driver. Methods that touch hardware go through `guards`; the interface itself holds no
safety rules, so SWD and USB flashing cannot diverge.

## DECIDE derived (Claude; Peter may overrule)

1. **Identity is the serial where the host has one, the slot where it does not; the node path is
   only an output.** Constraint: the board changes USB ids between modes and the node can be
   renumbered or aliased, and only the serial survives all of that; but Peter's own host exposes
   named nodes with no serial, so a second provider (node-map) is required, and any check that
   could not run is reported as not run.
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
- **`802a` role:** read the Adafruit board file for this board and state what each id is.
- **Event source: resolved for this host.** udev is unavailable here (no `/run/udev`); inotify on
  the node directory is the baseline. Still **[unknown]** on a Pi, a PVE host and a plain VM, where
  udev is expected and should be probed, not assumed.
- A `two`, `unrecognised` and `absent` state each reproduced against the fake `/dev` fixtures
  (harness), with no hardware.
- A re-enumeration during a held lock leaves state unchanged and restarts nothing.
