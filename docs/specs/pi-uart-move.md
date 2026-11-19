# Bluetooth back on, the RAK's UART on another UART, the old pins blacklisted, Wi-Fi off the LAN

Peter, 2026-11-19: "you need to re-enable bluetooth and probably disable wifi from connecting to the network ... the NRF device's only method of
communication is via bluetooth and if we can't do that we cant really call it a workbench for the NRF. my intention is to do BLE discovery as another
enhancement. this means we will have to move the UART to different pins and blacklist the existing pins 8 & 10." Then: "I dont mind which pins we use,
everything discussed is needed." Extends `pi-uart.md` and `line-release.md`. BLE discovery itself is a later spec; this one makes it possible.

## What is true on the bench Pi (read 2026-11-19)

- `/boot/firmware/config.txt` `[all]`: `enable_uart=1` and `dtoverlay=disable-bt`. That overlay gives the PL011 (uart0, `ttyAMA0`, `fe201000.serial`) to
  header pins 8 and 10 (GPIO14/15) and turns Bluetooth off: `rfkill` lists only the Wi-Fi. `bluez` 5.82 and `bluez-firmware` are installed.
- `dtoverlay -h`: `uart2` = GPIO 0-3, `uart3` = 4-7, `uart4` = 8-11, **`uart5` = 12-15** (TX 12, RX 13, CTS/RTS 14/15, off by default). GPIO 12 and 13 are free inputs
  (pull-down) now. The device tree names the PL011s: `serial@7e201000` (uart0), `7e201400` (uart2), `7e201600` (uart3), `7e201800` (uart4), `7e201a00` (uart5).
- Wi-Fi: `wlan0` is joined to the LAN ("sayang", 5 GHz channel 44) by a NetworkManager profile generated from `/etc/netplan/90-NM-*.yaml`, autoconnect on; `eth0` is
  up too. `nrf-workbench.home` resolves to the **Wi-Fi** address (.215), which is why a client with a bad DNS or Wi-Fi path cannot reach the bench.
- The workbench hard-codes "the Pi's UART is `ttyAMA0` on GPIO14/15" in `console/links.py` (`PI_UART_PINS`), `wiring/detect.py`, `wiring/store.py`, `project/plan.py`,
  `wiring/commands.py` (`uart_status` tells the reader to add `dtoverlay=disable-bt`) and `wiring/pinctrl.py` (`a0`, `PI_UART_DEFAULT`).

## Defects first

1. **Bluetooth is off, and the bench's own setup instructions turn it off.** `wiring.uart_status` says to add `dtoverlay=disable-bt`. After this change it must say the
   opposite: leave Bluetooth on and use another PL011.
2. **Pins 8 and 10 would still be driven after Bluetooth returns.** With `enable_uart=1` and Bluetooth on, the firmware puts the *mini-UART* on GPIO14/15 and drives TX
   high: the original problem (a RAK's RX line pulled high while it boots) comes back on the old wire. So the pins must be set to floating inputs at boot, and refused to
   the pin map.
3. **A UART's pins cannot be a constant.** Which `ttyAMAn` is which PL011, and so which pins, depends on the overlays. It is derived from the device itself: the
   hardware address in `/sys/class/tty/<port>/device`, looked up in one table of the BCM2711's PL011s.
4. **Ethernet becomes the only way in.** With Wi-Fi off the LAN, a pulled cable leaves the Pi unreachable. This is accepted (Peter: Wi-Fi is wanted free for
   provisioning other devices), and it is done last, after the reboot has proved the Ethernet address comes back.

## The Pi's UARTs, by hardware address (`wiring/piuart.py`, new)

| PL011 | sysfs device name starts | TX, RX (BCM) | function when claimed |
|---|---|---|---|
| uart0 | `fe201000` | 14, 15 | `a0` |
| uart2 | `fe201400` | 0, 1 | `a4` |
| uart3 | `fe201600` | 4, 5 | `a4` |
| uart4 | `fe201800` | 8, 9 | `a4` |
| uart5 | `fe201a00` | **12, 13** | `a4` |

`a4` for uart2 to uart5 is **[derived from the BCM2711 pin table, to be verified on the live Pi after the reboot]**: engage checks the function *name* the pin reports
(`TXD5`, `RXD5`) and not only the code, so a wrong code is a `pins_not_set`, never a silent success. Engaged pulls are the overlay's: TX none, RX pull-up, as for uart0
**[derived; verified by reading `pinctrl get 12,13` once the overlay is on]**.

`piuart.uart_of(sysfs, port)` returns `{name, tx, rx, alt}` or None (an unknown device). A port with no known UART is not offered and not opened.

## Changes

- **Offered ports.** `detect.pi_uarts` lists every `ttyAMAn` node whose device address is in the table (not just `ttyAMA0`), each row carrying its `pins`, named
  "Raspberry Pi serial port uart5 (header pins 32 and 33)", the header pins from the pin table. No node = no row; `wiring.uart_status` says how to enable one.
- **Connection pins** come from that row (`record["pins"]`), no longer from a constant. `project.plan` takes them from the same function.
- **`console/links.py`** keeps the port-name pattern and drops the "only ttyAMA0 is known" refusal (the console never guesses pins; the wiring store and plan decide).
- **`wiring.release` / `wiring.engage`** take each pin's engaged function and pull from `piuart` (a table of the pins above), not from `a0` and `PI_UART_DEFAULT`; "driven"
  means every pin reads its engaged function. Unchanged otherwise.
- **`wiring.uart_status`** reports every Pi UART found and the state of Bluetooth, and its steps become: add `dtoverlay=uart5`; **do not** disable Bluetooth (the step is
  now "remove `dtoverlay=disable-bt` if present"); remove any serial console from `cmdline.txt`; stop the serial getty; add `gpio=14,15=ip,pn` so the old pins float.
- **Reserved pins by configuration**: `pins.reserved` (a list of BCM numbers, default none). `gpio.assign` refuses one with `pin_reserved` and says it is reserved on this
  bench; the header table shows it as reserved. The bench's `host.yaml` sets `[14, 15]`. It is configuration, not code, because another bench may still wire pins 8 and 10.
  A pin already held by an existing connection stays held until that connection is moved or removed (this change does not break a live link).
- **UI**: the strings "header pins 8 and 10" become the row's real pins; the Wiring page, Help and the agent guide say what is true after the move.
- **Bluetooth status** (read-only, so the reboot can be checked from the page): `bench.overview` gains `bluetooth: {present, powered, address}` from
  `/sys/class/bluetooth/hci*` and rfkill, shown on Home as one line. Not discovery; no Bluetooth is opened.

## Procedure on the Pi (a person's step: the installer never edits boot configuration, install.md)

1. Back up: `config.txt` to `config.txt.bak-before-bt-20261119`, the netplan Wi-Fi file likewise.
2. `config.txt` `[all]`: remove `dtoverlay=disable-bt` and `enable_uart=1`; add `dtoverlay=uart5` and `gpio=14,15=ip,pn`. Tell `device` first (the reboot empties the console log).
3. Reboot once. Verify: Ethernet `.214` answers; `hciconfig`/`bluetoothctl show` finds an adapter; `ttyAMA0` is **absent** or not on pins 14/15; the uart5 node exists and
   `pinctrl get 12,13` says `a4` with `TXD5`/`RXD5`; `pinctrl get 14,15` says input, no pull.
4. Peter moves the RAK's two wires from header pins 8 and 10 to **32 (TX) and 33 (RX)**, GND at pin 34. The existing connection `rak-uart0` (uart:ttyAMA0) is then removed and
   remade on the uart5 port. Until then it shows as absent, loudly.
5. Wi-Fi: set the LAN profile not to autoconnect in the netplan source (`connection.autoconnect: "false"` under `networkmanager.passthrough`), `netplan generate`, keep the radio
   on. Point `nrf-workbench.home` at `.214`. Verified by `wlan0` having no address and the service answering on `.214`.
- **Rollback**: restore the two backups and reboot (or `nmcli con up netplan-wlan0-sayang` for Wi-Fi alone).

## Checks

Unit: the address-to-pins table; a fake sysfs with `ttyAMA1 -> fe201a00.serial`; an unknown device is not offered; connection pins come from the row; release/engage use the
table (uart5 pins, function name checked); `pins.reserved` refusals and header class; `uart_status` steps and Bluetooth state; overview `bluetooth`. Mutants. Browser: the Wiring
page offers "uart5 (header pins 32 and 33)" and no "pins 8 and 10" anywhere; Home shows Bluetooth. Live: after the reboot, the proofs in the procedure.

## Not done here

BLE discovery, a Bluetooth adapter choice (`hci0` only), a dongle. Wi-Fi provisioning of other devices (the radio is left on for it).

## Files this spec allows

`nrf_workbench/wiring/{piuart,detect,store,commands,pinctrl}.py`, `nrf_workbench/console/links.py`, `nrf_workbench/project/{plan,manifest}.py`, `nrf_workbench/gpio/{header,pinmap,commands}.py`,
`nrf_workbench/bench/overview.py`, `nrf_workbench/core/config.py`, `nrf_workbench/service.py`, `nrf_workbench/api/docs.py`, `ui/src/{Home.jsx,help.js,overview.js}`, `ui/src/wiring/*`,
`docs/specs/{pi-uart-move,pi-uart,line-release,gpio,wiring}.md`, `tests/` (wiring, devices, console, gpio, bench, project, api, dashboard), `scripts/verify_*.py`.
