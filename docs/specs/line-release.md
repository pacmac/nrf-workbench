# Releasing a Pi UART link's lines, so a board can be power-cycled

`device`, 2026-11-18, for Peter: with the Pi's UART wired to the RAK, a RAK on a bench supply does not boot at power-up while its RX wire
(the Pi's TXD, header pin 8) is connected; GND and the RAK's TX alone boot every time. Peter asks whether the workbench must be able to
disable a resource such as the UART and set its pins to input / floating. Extends `pi-uart.md`.

## What is true on the Pi (measured 2026-11-18, `pinctrl get 14,15`, service running)

- GPIO14 (pin 8, TXD0): `a0 pn | hi`: UART function, no pull, **driven high at 3.3 V**, whether or not a port is open (the UART idles high).
- GPIO15 (pin 10, RXD0): `a0 pu | hi`: UART function, the Pi's pull-up.
- Closing `/dev/ttyAMA0` does not change either: the function is the driver's, set at boot.
- `pinctrl` is on the Pi and writes the pin registers directly (a debug tool; it ignores the driver). One call takes 2 to 3 ms (20 runs).

## Defects first

1. **Nothing can stop the Pi driving a board that is not powered.** `wiring.disconnect` deletes the record and stops reading; the pin stays
   driven. A power-cycle test cannot run.
2. **A pin released behind the workbench's back is invisible.** So the state is **read from the pins** every time (`pinctrl get`), never
   remembered: a service restart, a hand-run `pinctrl`, or a crash between the two steps cannot make the page say "driven" when it is not.
3. **I2C cannot be floated usefully.** GPIO2 and GPIO3 have 1.8 kohm pull-ups to 3.3 V fitted on the Pi board; making the pins inputs
   leaves them pulled up. No I2C release is offered, and the help says why. A USB serial adapter's lines belong to its chip, not to the Pi,
   so they cannot be floated either: `wiring.release` refuses them (`pins_not_controllable`) and says so.
4. **Releasing must not lose the log silently.** The link is held (the console's `held` state, as during a flash), its byte offset carries on
   at engage, and the line says what it is doing.

## Commands (area `wiring`, capability `pinctrl`: a Raspberry Pi with `pinctrl` present)

| command | effect | what it does |
|---|---|---|
| `wiring.release` `name` | write | 1. close the link's port and keep it closed (`held`); 2. `pinctrl set <pin> ip pn` for each of the connection's pins (input, no pull); 3. read each pin back and **report it**. Refuses a connection that is not a Pi UART (`pins_not_controllable`). Already released is a success that reports the state again. |
| `wiring.engage` `name` | write | 1. `pinctrl set <pin> a0` and the pull the Pi has by default (14: none, 15: pull-up); 2. read back; 3. only if both read `a0`, let the link open again. A pin that does not read back as `a0` is `failed` (`pins_not_set`) and the link stays held. Release fails the same way if the pins do not read back as floating. Already engaged is a success. |
| `wiring.show` | read | each Pi UART connection gains `pin_state`: `{state: driven / floating / mixed / unknown, pins: [{gpio, function, pull, level}], why}`, read live. (`release` and `engage` return the same object as `pin_state`.) |

Both name the connection, not a pin: the pins are the connection's own (`pins` in its record), so a caller cannot float a pin the bench does
not own. Neither touches the connection file.

## Reported state (never asserted)

`state` is derived from the read-back: **driven** = every pin `a0`; **floating** = every pin an input (`ip`) with no pull; **mixed** =
anything else. The level (`hi`/`lo`) is reported as read and is NOT a claim about the wire: a floating pin's level is whatever the other board
makes it. Nothing says "safe to power the RAK"; it says what the Pi's pin is doing.

## Elsewhere in the bench

- `bench.overview` shows a Pi UART connection whose lines are not driven as **needs attention**, "lines released". Home therefore says
  so after a restart that left them released.
- The Wiring page's Pi UART connection gets one button, **Release lines** / **Engage lines**, and shows the state in words. Help gets a
  topic paragraph with the power-cycle recipe: release, power the board, engage, read the boot log.
- `project.apply` / `release` do not call these: a project describes a setup, a power-cycle is an action.

## Numbers

- `pinctrl` runs with a ceiling of **0.25 s**: it takes 2 to 3 ms (measured); a busy 4-core Pi may take 50 ms to start a process, and 5 times
  that is the point where something is wrong. It is a give-up ceiling; the call returns when `pinctrl` exits.
- Pull defaults are the measured ones above, in one table (`PI_UART_DEFAULT`), so the engaged state is the one the Pi booted with.

## Checks

Unit: a fake `pinctrl` that keeps pin state; release/engage order (port closed before the pin is touched, port opened only after a read-back
of `a0`), refusal of a USB adapter and of an unknown name, idempotence, a pin that will not restore leaves the link held, `lines` in
`wiring.show`, overview attention. Mutants on each. Browser: both themes, phone and desktop. Live: read the Pi's real pins before and after
a release/engage round trip and leave it engaged.

## Files this spec allows

`nrf_workbench/wiring/{pinctrl,commands}.py` (new `pinctrl.py`), `nrf_workbench/service.py` (the capability), `nrf_workbench/bench/overview.py`,
`nrf_workbench/core/status.py`, `docs/specs/{line-release,core,pi-uart}.md`, `ui/src/wiring/*`, `ui/src/help.js`, `tests/wiring/`,
`tests/bench/`, `scripts/verify_pi_uart_live.py`.
