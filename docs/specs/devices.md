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
- **`802a` role:** read the Adafruit board file for this board and state what each id is.
- **Event source, two hosts probed (2026-11-16).** The dev LXC has no `/run/udev`: inotify on the
  node directory is the baseline there. The bare-metal Proxmox host has `/run/udev` and `udevadm`
  257, so udev is available there. **Event delivery itself is untested on both** (a mode change
  was not made). Still **[unknown]** on a Pi and a plain VM; probe, never assume.
- **Node resolver on the Proxmox host [verified by `probe_host.py`]:** the RAK4631 in app mode is
  `/dev/ttyACM1` (`166:1`), reachable by `/dev/serial/by-id/...<serial>-if00` and by two
  `by-path` names (`usb-0:3:1.0` and `usbv2-0:3:1.0`, the same node), with udev `ID_SERIAL_SHORT`
  equal to the sysfs serial and `ID_PATH` `pci-0000:0d:00.0-usb-0:3:1.0` as a hub-port slot key.
  So the tty-link and by-id resolvers both work there and the join by `major:minor` is real. Two
  `by-path` names for one node means a slot key must be normalised, not compared as a string.
- **`unrecognised` is not hypothetical.** The same host has `/dev/ttyUSB0` as a non-character
  entry (`major:minor` `0:0`, no udev properties), most likely a regular file created by something
  that wrote to a path before a device existed. The state table's refusal for "a node that is not a
  character device" would have caught it.
- A `two`, `unrecognised` and `absent` state each reproduced against the fake `/dev` fixtures
  (harness), with no hardware.
- A re-enumeration during a held lock leaves state unchanged and restarts nothing.
