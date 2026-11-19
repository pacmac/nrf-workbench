# GPIO — functional spec (first slice built; the driver is designed, not built)

Peter, 2026-11-17, verbatim: *"then we have GPIO management and the ability to assign GPIOs on the PI to
services / actions etc etc."* Area: `gpio` (library, `architecture.md`). Depends on `core` and `devices`.
Tags: **[stated]** Peter said it; **[verified]** read on the bench Pi 4B (Raspberry Pi 4 Model B Rev 1.4,
2026-11-17, `gpioinfo -c 0`, which reads line info and requests nothing); **[recalled]** from the 40-pin
header's published layout, not checked; **[decided]** a decision taken unattended, with how to undo it.

## GAP defects first

1. **Nothing declared which Pi pin does what.** The brief (section 5b) and `devices.md` want a wiring map
   of Pi pins to DUT pin names, and `console.md` says a pin a link claims must never also be offered as a
   GPIO. No file, command or page holds any of it.
2. **Driving a pin is hardware on Peter's bench.** A header line may be wired to a DUT's RESET or a boot/DFU
   pin; one wrong pulse resets a unit mid-flash or parks it in a bootloader (the seven-hour DFU incident,
   `console.md`). So this slice **does not drive or read any pin**. It only records what each pin is
   assigned to, and refuses what is certainly wrong.
3. **`gpiod` is not in the service's venv.** It is a system Python module on the Pi (`python3 -c "import
   gpiod"` gives 2.2.0 **[verified]**; `ModuleNotFoundError` inside the venv **[verified]**). A driver needs
   it; how it gets there is open (see "the driver").

## TOOL what is built: the pin map

The bench keeps a **pin map**: for each header GPIO, which **service** uses it for which **action**, in what
direction, and what is known about the wire. It is the bench's, not the browser's, so the CLI, the MCP and
every browser see the same one. It is read and changed only through commands, never by editing a file.

- **A pin is named by its BCM number, 0 to 27, which is its line offset on `gpiochip0`.** **[verified]** Lines
  0 to 27 on `gpiochip0` are named `ID_SDA`, `ID_SCL`, `GPIO2` … `GPIO27`, and lines 28 and up are not on the
  header (`RGMII_MDIO`, `CTS0`, `SD1_CLK` …), with no header line held by the kernel at the time. The physical
  pin number is shown beside it from the header table below **[recalled]**.
- **The header table** (`gpio.header`): physical pins 1 to 40, each power, ground or a GPIO with its usual
  alternate function. Pins 27 and 28 (BCM 0 and 1, the HAT ID EEPROM) are **reserved**; BCM 2 and 3 (I2C1), 7
  to 11 (SPI0) and 14 and 15 (UART0) are **bus** pins, assignable but flagged, because turning on the matching
  overlay claims them; the rest are **free**. The table is read-only data in the code.
- **A declaration** (`gpio.assign`) has these fields, **all required unless stated, and none has a default**
  (the rule everywhere in this product: a value the declarer did not state is a value nobody derived):

  | field | meaning |
  |---|---|
  | `name` | a slug (`a-z 0-9 . _ -`, starts with a letter or digit, at most 64), unique |
  | `gpio` | BCM number 0 to 27, unique |
  | `direction` | `input` or `output` |
  | `service` | which service uses it, a slug (`programmer`, `power`, `manual` …): a label, not a list this code owns |
  | `action` | what it is for, a slug (`reset`, `boot`, `power-cycle`, `led` …) |
  | `purpose` | one sentence a person reads, 1 to 200 characters |
  | `active_low` | the line is asserted by driving it low |
  | `voltage` | `{signal: volts the wire swings, verified: bool}`; **above 3.3 is refused** (`voltage_unsafe`): header GPIO is 3.3 V and not 5 V tolerant, whatever `verified` says |
  | output: `initial` | `inactive` or `active`: the state the line is put in when it is taken |
  | output: `max_pulse_ms` | the longest pulse this line may ever be given, a whole number above 0, **derived by the declarer from what it drives** |
  | output: `drives_dut` | the line reaches a DUT pin (reset, boot, power): such a line goes through the guards when it is driven |
  | input: `pull` | `up`, `down` or `none` |

  `purpose` is 200 because one clear sentence is about 100 to 140 characters and 1.5x that is the room, not
  a paragraph. 26 usable pins make the store at most 26 entries, so it needs no other bound.
- **Refusals are by name and made by the service.** `pin_reserved` (BCM 0 or 1, and any pin in the bench's `pins.reserved` configuration: the pins of a UART it gave up, `pi-uart-move.md`), `pin_not_on_header` (28 and up
  or negative), `pin_taken` (that GPIO is already assigned to a different name), `voltage_unsafe`. A malformed
  field (a bad slug, an unknown direction, a missing required field, `max_pulse_ms` of 0, an output field on an
  input) is `usage`. Assigning an existing **name** replaces it (an edit); a name that moves to a GPIO another
  name holds is `pin_taken`. `gpio.unassign` of an unknown name is `pin_unknown`.
- **The store** is `<paths.gpio>/pins.json`, written atomically (a temporary file, `fsync`, `os.replace`) in a
  `0700` folder; a file that cannot be read or is not what this code writes is a failure that names it and is
  **never overwritten**, as for the console macros. `paths.gpio` has no default (`core.md`).
- **The capability `gpio` exists only on a Raspberry Pi with `paths.gpio` set.** A Pi is what the device tree
  says (`/proc/device-tree/model` contains `Raspberry Pi`), not that a `gpiochip` exists: **a gpiochip is not
  evidence of a Pi header** (`core.md`, `probes.md`: the Proxmox host has `/dev/gpiochip0` and is not a Pi).
  Elsewhere the commands are absent, not failing.
- **Commands** (all `gpio.*`, in the registry, so HTTP, CLI and MCP get them): `gpio.header` (the table, with
  each GPIO's assignment), `gpio.pins` (the declared pins), `gpio.assign`, `gpio.unassign`. `assign` and
  `unassign` are `effect: write` (they write the store, **not a pin**) and are recorded in the activity log
  like every command.

## DECIDE derived (Claude, unattended; Peter may overrule)

1. **Declare first, drive later.** Constraint: a header line can reach a DUT's reset or boot pin, and the only
   way to know is Peter's wiring. So the map exists, is useful (the dashboard and other areas can read what a
   pin is for), and nothing moves. **Undo:** the slice adds files and one config key; delete the folder.
2. **Commands, not a hand-edited YAML.** Constraint: "assign GPIOs … to services / actions" is a thing done
   from the dashboard, the CLI or the AI, and a file rewritten by a program loses a person's comments.
   **Undo:** the store is one JSON file; a page can still be pointed at a YAML later.
3. **Bus pins are flagged, not refused.** Constraint: BCM 2 and 3 are ordinary GPIO until an I2C overlay
   claims them, and refusing them would stop a bench that does not use I2C. The rule that matters
   (`console.md`) is that a pin a `pi-uart` link claims is hidden from GPIO; that backend is not built, so there
   is nothing to claim yet, and the check lands with it.
4. **`service` and `action` are slugs, not a closed list.** Constraint: no consumer exists to define the list
   (the driver, the programmer's reset line and a power switch are all unbuilt). A closed list written now
   would be a guess.

## TOOL the driver (designed, not built; needs Peter's go)

Not built because it moves a pin. The design, so the work is ready the day he says go:

- **One owner thread, like the console broker** (`console.md`): it blocks in `select` on its wake pipe and the
  line-request descriptors with **no timeout** while nothing is pulsing, so an idle bench has no timer. A line
  is requested through libgpiod v2 (`gpiod.request_lines`, **[verified]** `gpiod` 2.2.0 and `gpioinfo` 2.2.1 are
  on the Pi), and only that thread touches a line, as only `console/port.py` touches a serial device (a gate
  test will enforce the same for the line request).
- **A pulse is the one legitimate timer**: a width is time by definition. While a pulse is active the thread's
  `select` timeout is the time to its end; there is one pulse per line at a time; a pulse longer than the
  declared `max_pulse_ms` is refused (`pulse_too_long`) before anything moves.
- **A line is taken only when it is safe to:** `voltage.verified` is true (`voltage_unverified` otherwise), the
  line's `initial` state is applied at once, and **a `drives_dut` line is refused while the host lock is held
  by a flash or a touch is pending** (the existing guards), so the bench cannot reset a unit it is flashing.
- **On stop or crash the lines are released and the kernel returns them to inputs.** A `drives_dut` reset line
  then floats unless Peter's wiring has a pull on it: that is a wiring fact to state on the declaration day, not
  something software can promise.
- **The venv problem (GAP 3):** the driver needs `gpiod`, which is a system package. Either the venv for the
  `gpio` feature is built with `--system-site-packages` (the installer already limits that feature to a device
  tree that says Raspberry Pi, `install.md`), or `gpiod` is built from source against `libgpiod` headers. The
  first is one flag; it is Peter's to choose because it changes what the venv can see.
- **The dashboard page** (assign and see pins, a header diagram) is a later task on top of `gpio.header` and
  `gpio.pins`; it adds no rule of its own.

## Files this spec allows

`nrf_workbench/gpio/__init__.py`, `gpio/header.py` (the table), `gpio/pinmap.py` (validation and the store),
`gpio/commands.py`, `nrf_workbench/core/config.py` (`paths.gpio`), `nrf_workbench/core/status.py` (the new
codes), `nrf_workbench/service.py` (the capability and wiring), `install/nrfwb_install/apply.py` and `plan.py`
(the folder and the key), the specs, and `tests/gpio/**`.

## VERIFY

- Every header GPIO 0 to 27 appears exactly once in the table, 0 and 1 are reserved, and the table agrees with
  the Pi's own line names (checked against `gpioinfo` output on 2026-11-17).
- Each refusal fires on its case and not on its neighbour; each malformed field is `usage`.
- A failed write leaves the old file whole and no temporary; an unreadable file is a failure and is left alone.
- The capability is absent on a host whose device tree is not a Pi, and absent without `paths.gpio`.
- No test, and no code in this slice, opens a `gpiochip`; a gate test proves it.
