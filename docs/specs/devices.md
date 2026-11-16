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
3. **`protected` is a property of a unit, not of a board model.** The brief puts it in the profile.
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
  stated, `002a` in the brief). `802a` is listed in the board file with no known role (GAP 2) and
  is deliberately **not** in the profile until its role is measured.
- **Product strings are recorded per mode and match case-sensitively or not at all** (`RAKwireless`
  vs `RAKWireless` seen). They corroborate; they never identify.
- Board-specific pins, the reset line and timings live here, never in code. **[brief]**

## TOOL protected

`protected` is a flag **per serial**, held in host configuration (`units.yaml`), not in the
profile. The profile may carry a default (normally false). Reason: protection is about a unit's
situation (mid-measurement for days), which changes without the board model changing (GAP 3).
Only exceptions are listed, so this is not the removed roster: a board not named in `units.yaml`
is flashable by default. A flash of a protected unit is refused by `guards` (see `programmer.md`).

## TOOL hotplug and slot state

- Events come from the kernel (udev), not polling. The component never contains a sleep or a
  fixed wait; waits are on events with a required caller-supplied bound.
- **State is owned by whoever holds the lock.** While a flash holds the lock, `add` and `remove`
  events for that serial are recorded but do not reset state or restart anything. This closes
  upstream's hazard: its hotplug handler resets state to idle and reopens an exclusive proxy on
  any `add` outside a debug session, which a re-enumerating nRF flash triggers. **[brief: verified
  by reading upstream, not tested]**
- **No automatic recovery that unbinds a device while a flash or handover is in progress.**
  Upstream's flap recovery (many events in a short window) would destroy a failing DFU. **[brief]**
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
console()             -> a stream (serial / RTT / NUS, per what the profile offers)
debug()               -> a debug session (SWD area)
read_flash(range)     -> bytes
recover()             -> bring a dead chip back (SWD area)
ota(package)          -> over-the-air update (BLE area)
```

Drivers are looked up by `bootloader.type` and by the probe type. Each is one module; none imports
another driver. Methods that touch hardware go through `guards`; the interface itself holds no
safety rules, so SWD and USB flashing cannot diverge.

## DECIDE derived (Claude; Peter may overrule)

1. **Identity is the serial, location is the slot, the node path is only an output.** Constraint:
   the board changes USB ids between modes, the node can be renumbered or aliased, and Peter's own
   host uses custom `/dev` names; only the serial survives all three.
2. **`protected` is per serial in host config, profile gives a default.** Constraint: protection
   follows the unit, not the model (GAP 3), and the dump removed a hardcoded roster, which an
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
- **udev event source:** confirm udev events are available on the target hosts (Pi, PVE host, VM,
  with and without USB pass-through) and what the fallback is when they are not. **[unknown]**
- A `two`, `unrecognised` and `absent` state each reproduced against the fake `/dev` fixtures
  (harness), with no hardware.
- A re-enumeration during a held lock leaves state unchanged and restarts nothing.
