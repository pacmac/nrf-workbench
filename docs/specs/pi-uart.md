# The Pi's own UART as a debug link (DRAFT)

Peter, 2026-11-18: "instead of the serial dongle plugged in, UART RX and TX on the Pi GPIOs are being used for debugging", wired to
**header pins 8 (TX, BCM14) and 10 (RX, BCM15)**. Until now the console's only backend was a USB adapter (`usb-adapter`); `pi-uart` was
declared in `console/links.py` and refused as `backend_not_built`. Tags: **[stated]**, **[derived]**, **[unknown]**.

## GAP defects first

1. **The Pi's login console owns that port by default.** Raspberry Pi OS can run a serial login (`serial-getty`, and `console=serial0,115200`
   in `cmdline.txt`) on the very pins. Two readers on one UART garble each other, so a link on it is refused `port_held` while anything holds
   the node, and `wiring.uart_status` says what to switch off. **[derived]**
2. **On a Pi 4 the good UART on pins 8 and 10 is shared with Bluetooth.** The PL011 (`/dev/ttyAMA0`) is wired to the Bluetooth chip unless
   the overlay `disable-bt` moves it; without it, pins 8 and 10 are the mini UART (`ttyS0`), whose baud is tied to the core clock and drifts.
   The bench does not use Bluetooth, so the setting is `dtoverlay=disable-bt`; that is a boot-config change and a reboot, never made by the
   installer. **[derived]; the choice is Peter's to overrule** (he said pins 8 and 10, which settles the pins, not the Bluetooth).
3. **Nothing says the voltage is safe by looking at it, so the rule is stated:** the Pi's GPIO is 3.3 V by design and the RAK19003's UART is
   3.3 V logic, so no measured voltage is asked, as for I2C (i2c.md). A USB adapter's 3.3/5 V jumper is why *its* sending needs a measurement.
   **[stated by Peter for I2C, applied here]**

## The adapter row

`wiring.show` lists the Pi's UART as one more adapter, named `Raspberry Pi serial port (header pins 8 and 10)`, `serial: uart:ttyAMA0`,
`ids: pi-uart`, with `node`, and a `problem` when it cannot be used (no `/dev/ttyAMA0`: the UART is not enabled; held by another process; its
pins assigned to another service in the pin map). Because it is an adapter row, **the existing Wiring page offers it with no new code**:
`wiring.connect(device=<nRF serial>, intent=log, adapter="uart:ttyAMA0", baud=115200, send=false)`.

## The connection and the link

- A record whose adapter `ids` is `pi-uart` builds a link with `backend: pi-uart` and `port: ttyAMA0`. `port` must be `ttyAMA` and a digit.
- The link is found by its node (`/dev/ttyAMA0`, a character device), opened by the same `console/port.py` (raw 8N1, `HUPCL` cleared, locked,
  never sets a modem line: the pins have none), read by the same broker, shown on the same Console page.
- Receive-only by default; `send: true` needs no measured voltage (3 above) and is allowed by the same rules as any link.
- A name collision with an existing connection is `name_taken`; the Pi's UART belongs to one connection (`adapter_taken`).

## Pins

Port `ttyAMA0` uses BCM 14 (TX) and 15 (RX). A connection **holds** them: `gpio.assign` of either is refused `pin_taken`, naming the connection,
and a connection on the Pi UART is refused `pin_taken` if the pin map already assigns either to another service. Other ports (the extra UARTs
a Pi 4 can enable by overlay) have no known pins here and are refused `not_a_declared_link`. **[derived]**

## wiring.uart_status

Reads, opens nothing: `{enabled, node, kernel_console, holders, bluetooth_on_it, pins, steps}`. `steps` is the list of changes a person makes
(`enable_uart=1` and `dtoverlay=disable-bt` in `/boot/firmware/config.txt`, remove `console=serial0,115200` from `cmdline.txt`, disable
`serial-getty@ttyAMA0`), each with whether it is already done, and a reboot is said to be needed when a boot file differs. It changes nothing.

## Status (2026-11-18)

**Built and tested, not yet proved on the real Pi**, which stopped answering on the network just before. 1289 tests (27 here, 12 mutants
caught after two survivors were fixed), and a real browser run in light and dark (`scripts/verify_pi_uart_live.py`, 12 checks): the Pi's
serial port is offered on the Wiring page, the sheet connects a board to it receive-only, the console has a `pi-uart` link that reads what the
board prints, the pin map refuses BCM14 while the connection holds it, and removing the connection frees it. The Wiring page says the port
"is built into the Pi" instead of "plugged in". **Step 2 (the real Pi) remains:** deploy, apply the four setup steps `wiring.uart_status` lists
(with backups of `config.txt` and `cmdline.txt`), reboot, move `rak-uart0` onto the UART and read the RAK.

## Files this spec allows

`nrf_workbench/console/{links,broker}.py`, `nrf_workbench/wiring/{detect,store,commands}.py`, `nrf_workbench/gpio/commands.py`,
`nrf_workbench/core/status.py` only if a code is added, `docs/specs/{pi-uart,wiring,console,gpio}.md`, `tests/console/`, `tests/wiring/`,
`tests/gpio/`.
