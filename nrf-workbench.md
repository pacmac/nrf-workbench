# nrf-workbench — turn a Raspberry Pi into the nRF counterpart of the ESP testbench

Hand this file to a fresh Claude Code session. It is a brief, not a spec: it says what to build,
what is already known, what is NOT known, and in what order. **Investigate before you change
anything, and mark every claim you make as verified, recalled, or unknown.**

Confidence tags used below:
- **[verified]** read in source or run on real hardware while this was written
- **[recalled]** from memory, plausible, NOT checked — verify before building on it
- **[unknown]** nobody has looked

---

## 1. Goal

Upstream project: <https://github.com/SensorsIot/Embedded-AI-Harness> (MIT). Its testbench is a Pi
that gives an AI hands and eyes on **ESP32** hardware: flash, reset, serial over the network,
GDB debug, OTA, plus the Pi as test equipment (WiFi AP, BLE, MQTT, SDR, signal generator).

Build the equivalent for **Nordic nRF** devices, **generic** — not tied to any one firmware or
board (nRF52 first; nRF53 / nRF54L / nRF91 as later targets):

1. **Programmer** — every way an nRF gets firmware: SWD, serial DFU, UF2, BLE DFU, MCUboot.
2. **Debugger** — GDB over SWD, RTT logging, flash/RAM read-back, recovery from a dead chip.
3. **OTA** — BLE DFU (Nordic Legacy and Secure DFU, MCUboot SMP).
4. **nRF Connect parity** — what the nRF Connect mobile app and nRF Connect for Desktop do, but
   driveable by an AI over HTTP/MCP (section 5).
5. **Test equipment** — BLE central/peripheral partner, DTM radio tests, sniffer, power profiling.
6. **Pin-level I/O** — the Pi wired to the nRF's GPIOs to drive, read and time pins and to test I²C,
   SPI, UART, ADC and power behaviour from the outside (section 5b).

The ESP workbench is "plug a board in, it is ready, everything is an HTTP call". Keep that.

---

## 2. The upstream design you are extending [verified, read 2026-11-15, main pushed 2026-10-09]

Read these yourself first; line numbers are approximate and will drift.

| file | what it is |
|---|---|
| `pi/portal.py` (258 KB, ONE file) | HTTP server on :8080: slots, hotplug, proxy lifecycle, flash, debug, everything |
| `pi/plain_rfc2217_server.py` | per-slot RFC2217 serial proxy; exclusive open; read-only fan-out on `tcp_port+1000` |
| `pi/udev/99-rfc2217-hotplug.rules` + `pi/scripts/rfc2217-udev-notify.sh` | udev add/remove of `ttyACM*`/`ttyUSB*` -> `POST /api/hotplug` |
| `pi/rfc2217-learn-slots` | derives `usb_prefix` per hub port from udev `ID_PATH` |
| `pi/config/testbench.json` | slots: `label`, `usb_prefix`, `tcp_port`, `gdb_port`, `openocd_telnet_port` |
| `pi/install.sh` | apt + pip deps; installs `openocd-esp32` to `/usr/local/bin` (a separate binary) |
| `mcp/testbench_mcp.py` | thin MCP proxy: ONE `SPECS` row per endpoint, stdlib only |
| `pi/ble_controller.py` | bleak-based BLE bridge: scan / connect / write |
| `pytest/testbench_driver.py` | the test driver the AI's tests call |
| `.claude/skills/*` | method + dev skills; **`define/references/domains/esp32.md` and `build/references/domains/esp32/*`** are the seam for other device families |

Key facts:
- **Slot = a physical USB hub port**, matched by `usb_prefix` from udev `ID_PATH`. A slot survives
  board swaps and kernel renames. `_handle_hotplug` (`~:2977`) adopts a new devnode on `add`.
- `flash_slot` (`~:920`) and `flash_device` (`~:1970`) stop the proxy, run esptool on the Pi against
  the local devnode, restart the proxy. `POST /api/flash` (`~:4123`) is the multipart handler.
- The proxy holds the port with `TIOCEXCL`; every flash path must stop it first.
- Auto JTAG detection runs ~3 s after every `ttyACM` `add` (`~:3146`): ESP-only (`303a:1001`).
- `detected_chip`, `STATE_DOWNLOAD_MODE`, `gpio_boot`/`gpio_en`, `serial_reset` (DTR/RTS) are all
  ESP concepts. There is **no device-family concept** anywhere.
- The method skills already split by domain (`domains/esp32`), so a `domains/nrf52` is the intended
  extension point for test-case docs (BLE, DFU, settings/flash storage, watchdog, low power).

### Hazards in upstream that any nRF device will hit [verified by reading, NOT tested]

1. `_handle_hotplug` only suppresses `add` during **`STATE_DEBUGGING`**. On any other `add` it sets
   `state = IDLE` and restarts the proxy. A flash that re-enumerates the device (nRF serial DFU
   does, twice) gets its state clobbered and an exclusive proxy re-opened on the bootloader port.
   ESP32 never hit this because its flash does not re-enumerate (the code comments say so).
2. **Flap recovery unbinds the USB device** (`FLAP_THRESHOLD` 10 events / 30 s). Harmless for a
   normal flash; destructive if a failing DFU cycles.
3. The proxy is pyserial's stock `PortManager`. **No baud filtering was found** (grepped only —
   verify). A client that negotiates 1200 baud could trigger the Adafruit-bootloader touch.
4. `serial_reset` / `serial_release` assume ESP DTR/RTS and the EN/BOOT GPIOs. What they do to an
   nRF is **[unknown]** — read before anyone calls them.
5. Slot identity by hub port alone is wrong for nRF: a device changes USB identity between app and
   bootloader and may be composite (CDC + MSD). Identify by **serial** (`ID_SERIAL_SHORT`) as well.

---

## 3. Architecture to aim for

Do not bolt nRF onto an ESP-shaped file. Introduce a **device-family driver layer** and make ESP32
the first driver (behaviour unchanged):

```
family driver: identify() flash() enter_bootloader() reset() debug() console() recover()
               read_flash() ota() capabilities()
```

Plus a **device profile** (YAML), keyed by USB VID:PID / serial, so board facts live in data:

```
bootloader type   adafruit-serial-dfu | nordic-secure-dfu | nordic-legacy-dfu | mcuboot-serial | uf2 | swd-only
USB ids per mode  app / bootloader (these differ, and so can the manufacturer-string case)
entry method      1200-baud touch | double-tap reset | GPREGRET via SWD | GPIO on RESET | none
memory map        MBR, SoftDevice (name + version + fwid), app start, bootloader, settings page
safety            protected: true  -> flash refused (a unit mid-measurement must not be reflashed)
```

Capability flags per slot, so the MCP/skills only offer what the attached family can do.

Upstream question to settle early: **PR upstream, or private fork the Pi runs?** The Pi's installed
copy may differ from upstream main. Establish shell access to the Pi first (see section 9).

---

## 4. Programming and debug paths

| path | tool | notes |
|---|---|---|
| Serial DFU, Adafruit bootloader | `adafruit-nrfutil dfu serial -pkg firmware.zip -p <port> -b 115200 --singlebank` | **[verified]** command used on a RAK4631 (nRF52840). Package is a zip with `manifest.json`; refuse a `.hex`. |
| Serial DFU, Nordic bootloaders | `nrfutil` (Nordic) | **[recalled]** |
| MCUboot serial recovery / SMP | `mcumgr` or `smpmgr` | **[recalled]**, Zephyr / nRF Connect SDK devices |
| UF2 | copy a `.uf2` to the bootloader's mass-storage volume | **[recalled]**; a block device, so udev rules for `tty*` will not see it |
| SWD | OpenOCD (nrf5 driver), pyOCD, `nrfutil device`, J-Link | see below |
| BLE DFU | Nordic Legacy / Secure DFU, MCUboot SMP over BLE | section 6 |

**SWD tool support — check each against current docs before choosing:**
- pyOCD: `nrf52840` is a built-in target; an nRF54L target exists and needs a recent pyOCD
  (one source says >= 0.38.0); nRF5340 target name **not confirmed**. Sources:
  <https://pyocd.io/docs/target_support>,
  <https://wiki.makerdiary.com/nrf54l15-connectkit/guides/programming/programming-the-nrf54l15/>
  (from one web search — **[recalled]**, verify with `pyocd list --targets`).
- OpenOCD stock (nrf5 flash driver, `mass_erase`, APPROTECT handling): **[unknown]** — verify
  versions available on the Pi's OS. It would be a **second** OpenOCD beside `openocd-esp32`.
- `nrfutil device` / `nrfjprog` / J-Link on Linux arm64: **[unknown]** — verify, and note they are
  proprietary downloads.

SWD hardware options: CMSIS-DAP probe (e.g. a Pi Pico running debugprobe — open, works with
OpenOCD and pyOCD), J-Link, ST-Link, an nRF DK's on-board J-Link, FTDI, or Pi GPIO bit-bang
(OpenOCD `linuxgpiod` driver, **[recalled]**). Default recommendation: **CMSIS-DAP**.

SWD is the only way to: recover a dead bootloader, mass erase, write SoftDevice + bootloader + UICR,
read flash back, reset deterministically, and enter a bootloader without a USB touch (by setting
the bootloader's entry marker in `GPREGRET` then resetting — **magic values [recalled]**, take them
from the bootloader source, not from this file). SWD attached **keeps the core clocked**, so
deep-sleep current cannot be measured with it attached.

Debug extras to include: GDB server per slot (`gdb_port` pattern as upstream), **RTT** logging
(**[recalled]**: OpenOCD and J-Link both support it), HardFault/core-dump capture, SVD-aware
register view.

---

## 5. nRF Connect parity — the feature list

Everything here is **[recalled]** from the nRF Connect for Mobile and nRF Connect for Desktop apps.
Verify against Nordic's current docs. Every item becomes an HTTP endpoint + an MCP tool.

**Scanner** (mobile app / Desktop "Bluetooth Low Energy")
- Scan with filters: name, address, RSSI floor, service UUID, manufacturer data
- Per-device: RSSI history, parsed advertisement structures AND raw bytes, connectable flag,
  advertising interval, PHY, extended-advertising / periodic-advertising data, scan responses

**GATT client**
- Connect, discover services / characteristics / descriptors, read, write, write-without-response,
  **long (prepared) write**, notify / indicate subscribe (stream to a websocket), descriptor access
- MTU exchange, connection-parameter update, **PHY selection (1M / 2M / Coded)**, data length
- Pairing and bonding: LE Secure Connections, passkey, just-works, OOB; bond list and removal
- Nordic UART Service terminal (NUS), Heart Rate / Battery / Device Information as built-in views

**GATT server / advertiser** (the Pi acts as a peripheral)
- Define a custom GATT database; start / stop advertising with configurable payload, interval, PHY,
  extended advertising; accept connections; log every read / write the peer makes

**DFU** — Nordic Legacy, Secure DFU, MCUboot SMP, with progress and resume

**Logs and macros** — timestamped GATT/event log per connection; a scripted-sequence format
(replayable macro of reads/writes/waits)

**Desktop tools to mirror**
- *Programmer*: multi-region hex, region map, erase / write / verify / read, SoftDevice + app
- *Power Profiler*: Nordic PPK2 (USB device; **[recalled]** open Python protocol libraries exist) —
  and an adapter so other instruments (e.g. a coulomb counter) plug into the same endpoint
- *Direct Test Mode (DTM)*: TX / RX packet tests, PER, channel sweep
- *RSSI viewer*, *Serial terminal*, *Sniffer* (nRF Sniffer for Bluetooth LE firmware on a dongle,
  decoded in Wireshark — **[recalled]**)

**Radio hardware reality.** bleak (what upstream's `ble_controller.py` uses) is a **central only**.
Peripheral / GATT-server / advertising needs BlueZ's D-Bus API (or a library over it), and the Pi's
onboard controller may not expose BLE 5 features (Coded PHY, extended advertising, periodic) —
**[unknown]**, check `btmgmt` / `hciconfig` on the actual Pi. The robust answer is a **dedicated
nRF52840 dongle on the Pi** running Nordic's connectivity / HCI / sniffer / DTM firmware, so the
bench has a full BLE 5 radio it controls. Decide this early; it shapes the BLE design.

---

## 5b. GPIO and peripheral test — the Pi wired to the nRF's pins

**What upstream has [verified by reading]:** `pi/bcm_gpio.py` is a minimal `/dev/mem` register
helper for the BCM2835/2837 map, shared by the SDR/signal-generator drivers (`gpclk.py`,
`pe4302.py`). The portal's only pin control is **one global pair**, `gpio_boot` (default 18) and
`gpio_en` (default 17), applied to every slot — `testbench.json` has no per-slot wiring. The MCP
exposes `gpio_status` and `gpio_set` (value `0` / `1` / `'z'` = high-impedance). Every slot in the
live bench reports `gpio_wired: null`, i.e. nothing proven wired. `install.sh` already installs
`python3-libgpiod`. **[unknown]** whether the `/dev/mem` map works on a Pi 5 (different I/O chip);
the README claims Pi 3/4/5.

So this is new work, not an extension: build a general, per-slot pin layer.

### What the Pi can be to the DUT

| role | what it covers | notes |
|---|---|---|
| Digital stimulus / sense | drive 0 / 1 / hi-Z, read, pulse of N µs, PWM, edge counting, frequency | open-drain emulation = drive low or hi-Z. Use the kernel's `libgpiod` (line ownership, edge events with kernel timestamps — **[recalled]**) rather than raw `/dev/mem`. |
| Bus controller | I²C master (scan / read / write / register dump / ACK-NACK probe), SPI master (modes 0-3, chip selects), UART, 1-Wire | the Pi's hardware I²C has a known clock-stretching weakness (**[recalled]**, depends on the Pi model); measure it before trusting a stretching test |
| Bus **target** | emulate an I²C / SPI sensor so the DUT's driver can be tested | Pi I²C-target mode via the BSC peripheral is **[recalled]** on BCM283x and **[unknown]** on Pi 5. A Raspberry Pi Pico over USB-serial as a programmable peripheral emulator is the sturdier option (design choice). |
| Analog | read the DUT's analog outputs; drive its SAADC inputs | the Pi has **no ADC or DAC**. Add I²C/SPI parts (e.g. an ADS1115 ADC and an MCP4725 DAC — **[recalled]** part numbers) and state each one's resolution and calibration. |
| Power | switch the DUT's supply, set VDD, power-cycle, coarse in-line current | a GPIO-driven load switch / MOSFET; programmable VDD via DAC-controlled regulator. For µA-level current use a coulomb counter or PPK2, not the Pi. Brownout / reset-threshold sweeps need a controllable VDD. |
| Reset and bootloader entry | a Pi GPIO on the nRF RESET pin; double-reset to enter an Adafruit-style bootloader; hold a DFU pin low through reset | pin numbers and timing are **board-specific**: put them in the device profile, never in code. |
| Wake / sleep tests | wake from System OFF / System ON sleep by a pin edge, measure wake latency | needs a timestamped capture on the Pi and a DUT-side marker (below) |

### nRF-specific pin gotchas (all **[recalled]** — verify against the product spec and the board)
- The NFC pins (P0.09 / P0.10) are NFC antenna pins until the UICR NFCPINS field says otherwise.
- The reset pin only exists as a pin if the UICR enables it; it is not a plain GPIO by default.
- SWD pins, the 32 kHz crystal pins and any pin shared with the board's debug UART are not free.
- Pull, drive strength, sense and direction are firmware-configured. **A pin test must read the
  DUT's configured state, not assume it.**

### Electrical safety — non-negotiable, put it in the wiring map
- Pi GPIO is **3.3 V** logic and weak (**[recalled]**: ~16 mA per pin recommended, ~50 mA total).
  An nRF powered at 1.8 V, or any DUT net that reaches 5 V, needs a **level shifter**. Never
  connect 5 V to an nRF52840 pin: its absolute maximum is only a few hundred mV above VDD.
- Common ground first. A series resistor (e.g. 330 Ω - 1 kΩ) on every signal wire so a mistaken
  contention cannot destroy either side.
- Before first connection, **measure** the DUT net's real voltage with the bench ADC; do not trust a
  schematic.

### Design
- **Wiring map per slot** (YAML): DUT pin name <-> Pi pin, direction as seen by the DUT, voltage
  domain, level shifter yes/no, allowed operations. **Tests are written in DUT pin names.** The API
  **refuses** an operation the map contradicts (drive a pin declared as a DUT output = contention),
  refuses Pi pins already claimed (SWD, reset, boot, I²C), and refuses a pin whose voltage domain is
  unverified.
- Endpoints (names illustrative): `/api/pins` (map + live state), `pin/set|read|pulse|pwm|watch`
  (edges streamed over a websocket), `i2c/{scan,read,write,probe}`, `spi/xfer`, `uart/*`, `adc/read`,
  `dac/set`, `power/{on,off,cycle,set_vdd}`.
- **Timing-critical actions run on the Pi, not over HTTP.** A round trip costs milliseconds and
  ruins µs-level stimulus. Provide **`/api/sequence`**: upload a timed pin sequence, execute it
  locally, return a timestamped capture. **Derive every timing tolerance from a measurement of the
  Pi's own jitter** (drive a pulse of N µs, capture it, report the error) — do not pick a number.
- **Capture**: a timestamped edge ring buffer per watched pin, exportable as VCD for sigrok /
  PulseView. For fast buses (I²C at 1 MHz, SPI) a Pi GPIO cannot sample reliably — use an external
  USB logic analyser (sigrok-supported, **[recalled]**) and expose it as a bench instrument.
- **A DUT-side test partner**, the analogue of upstream's `test-firmware/`: an nRF firmware image
  with a command console (UART / USB-CDC / RTT / BLE NUS) that sets and reads GPIO, runs TWIM /
  SPIM / UARTE transactions, reads SAADC, drives PWM, and does System-OFF with wake-on-pin, so the
  Pi's stimulus is always checked against the DUT's own view. Built in CI in a pinned container.
  Framework (Zephyr / nRF Connect SDK / Arduino-PlatformIO) is **[unknown]**: decide with the
  owner.
- `domains/nrf52` test cases to author: GPIO loopback matrix, pull / drive configuration,
  wake-from-System-OFF latency, I²C ACK / NACK / address scan / clock stretching, SPI modes, UART
  baud tolerance, brownout / reset-threshold sweep, reset-pin behaviour, edge counting, PWM duty
  accuracy, SAADC accuracy.

### Acceptance (measured)
- A pin loopback matrix: every wired pin read back correctly in both directions.
- I²C: the Pi finds a known device at the expected address and refuses a bad address cleanly.
- Timing: a measured error figure for pulse generation and edge capture, recorded in the repo.
- Contention: a deliberate wrong-direction request is refused, not executed.
- Level-shifter procedure: documented and run once on the real wiring before any connection.

---

## 6. BLE DFU — a working reference exists on this host [verified, read-only]

mesh-gw implements **Nordic Legacy DFU over BLE with bleak** and has flashed real RAK4631 radios:

```
/usr/share/pac/dev/projects/mt-radar/mesh-gw/core/nordic_dfu_session.py   the GATT conversation
/usr/share/pac/dev/projects/mt-radar/mesh-gw/core/dfu_cli.py              a CLI around it
/usr/share/pac/dev/projects/mt-radar/mesh-gw/core/ble_device.py           service/char UUIDs; trigger at ~:4125
```

- DFU service `00001530-1212-efde-1523-785feabcd123`, control point `...1531...`, packet `...1532...`.
- It also handles the **entry step**: a device already running its app is asked to reboot into the
  bootloader (`_send_nordic_dfu_trigger`; for Meshtastic, an admin message `reboot_ota_mode`).
  **Entry is the firmware's job and differs per firmware** — a generic workbench cannot assume it.
- Its operator notes (`/root/.claude/skills/rak-ota/SKILL.md`): the package must be a DFU zip
  (`manifest.json`, `.bin`, `.dat`), and its `softdevice_req` must match the device's SoftDevice
  (182 = S140 6.1.1); a mid-write abort on the stock Adafruit bootloader can need a serial reflash.
- These are another project's files: **read them, do not edit them.**

---

## 7. Hard-won rules for nRF USB / DFU (from a real RAK4631 + Adafruit-bootloader project)

These are generic to the Adafruit nRF52 bootloader family and each cost real time:

1. **A 1200-baud open-and-close IS the bootloader entry.** Nothing else should open the port at
   1200 baud. It once parked a deployed-style unit in DFU for 7 hours.
2. **Two resets in quick succession is the double-tap detector.** A tool that does its own touch
   AND lets the uploader touch again gets bootloader entry "by accident" and several tries per
   flash. **Exactly one transition per flash.** Verify with the device's own boot counter (advances
   by 1, not 2).
3. **Identify by serial, through the transition.** The port number changes between app and
   bootloader; the serial does not. Observed on a RAK4631: app `239a:8029`, bootloader
   `239a:002a` (the board file lists `0029`, `002a`, `802a`); the manufacturer string differs in
   case (`RAKwireless` vs `RAKWireless`) — match case-sensitively or not at all.
4. **A mode change passes through ABSENT.** The device disappears and reappears. That gap is the
   handover, not a fault. Wait on the kernel event / re-read the level, never a fixed sleep.
5. **Judge a flash by output AND duration, not exit status.** A healthy flash on that board was
   25-29 s and printed `Device programmed.`; a 15 s run once reported success, wrote a partial
   image and boot-looped the board.
6. **One attempt. Never loop an upload.** A refused bootloader request is not a failed write; a
   failed write stops at one attempt and is reported.
7. **Build separately from flashing** and hold the port only for the write. Return the WHOLE tool
   output, never a `tail`.
8. **Mode is not reachability.** A device in app mode can be silent (sleeping radio, DTR-gated CDC).
   Silence is not death and must not trip flap recovery or a "dead" verdict.
9. **Serial interlock.** Refuse to flash unless the attached board's serial matches the one the
   caller named. Refuse if two candidate boards are attached. Refuse if anything else holds the port,
   naming the holder.
10. **A `protected` flag per device** — a unit in the middle of a multi-day measurement must refuse
    a flash.
11. **Don't invent timeouts.** Derive every bound from a measurement (healthy flash time, measured
    enumeration time) and write the derivation down.

---

## 8. Build order and acceptance

1. **Investigate the real Pi** (section 9). Nothing below starts before the unknowns are answered.
2. **Driver layer + profile layer**, ESP32 behaviour unchanged, upstream's host tests still green.
3. **nRF serial DFU** (Adafruit bootloader) with section 7 enforced, and the hotplug / flap-recovery
   hazards from section 2 fixed or guarded.
4. **SWD** programmer + debugger + recovery + RTT, on a CMSIS-DAP probe.
5. **BLE central parity** (scanner, GATT client, bonding) on bleak; then **peripheral / advertiser**
   over BlueZ or a dedicated nRF52840 dongle.
6. **BLE DFU**, ported from the mesh-gw reference.
7. **DTM, sniffer, power profiler.**
7b. **GPIO / peripheral layer** (section 5b): wiring map, `libgpiod` pin control, I²C / SPI / UART
   controller, `/api/sequence`, edge capture, ADC/DAC, switchable DUT supply, DUT-side test partner.
   Needs the wiring decisions in section 9 before any code.
8. **Skills + `domains/nrf52`** test-case docs, the pytest driver methods, a CI artefact flow
   (flash the artefact CI built, not a local build — upstream's principle).

Acceptance for the programmer (measured, not read):
- 10 consecutive flashes, each succeeding on the first invocation, no manual reset
- exactly 1 device reset per flash, from the device's own boot counter
- a monitor open before a flash is running after it, 10 of 10
- a flash with a foreign holder is refused, naming the holder, 10 of 10
- wrong serial refused; two candidate boards refused; `protected` refused
- a deliberately truncated / `.hex` / wrong-SoftDevice package is refused before any write

---

## 9. Unknowns to resolve first

1. **Shell access to the Pi.** The bench in this environment is `testbench-91df` (192.168.0.146);
   its portal is HTTP on :8080 and the MCP tools are `mcp__testbench__*`. Whether the session can SSH
   in is **[unknown]**. If not, say so and stop.
2. Pi model, OS, Python version, what is installed (`openocd`, `pyocd`, `adafruit-nrfutil`,
   `bleak`, BlueZ version), free USB ports, whether any GPIO is wired.
3. Whether the Pi runs upstream main or a modified copy: diff it.
4. The slot map: SLOT1 holds a RAK4631 (`239a:8029`), SLOT3 an ESP32-C3 (`303a:1001`).
5. The DFU udev event sequence and whether `ttyACM*` renumbers across the mode switch.
6. Which SWD hardware exists, and whether the target board exposes SWDIO/SWCLK at all.
7. The onboard Bluetooth controller's BLE 5 capabilities.
8. Tool support matrix in section 4 — verify every **[recalled]** row.
9. GPIO: which Pi header pins are free, which are wired to anything today, whether any I²C device,
   ADC/DAC or level shifter is already attached, whether the Pi model supports the `/dev/mem`
   helper, and the DUT board's available pins and their voltage domain. Ask the owner what is to be
   wired before designing the wiring map.

---

## 10. Ground rules for the session that builds this

- **Investigate first, change second.** Read-only until the plan is agreed.
- Label every claim verified / recalled / unknown. A false "does not exist" is the expensive kind:
  check before writing that something is absent.
- Do not touch hardware on the bench that is not yours to touch: confirm which board, by serial,
  before any flash. Do not flash a unit that is mid-measurement.
- Keep terse. Report the defect first.
