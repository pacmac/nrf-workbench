# Probes — spec for the read-only host probes (DRAFT)

Peter, 2026-11-16: the repo is cloned onto another host; add the scripts needed to settle the
unknowns that need hardware or another host. These are measurement tools, not product code. They
live in `scripts/`, outside the `nrf_workbench` package, and are stdlib-only so they run before
anything is installed. Area: `harness`.

Files this spec allows (the only files its task may create):

| file | purpose |
|---|---|
| `scripts/probe_host.py` | one passive snapshot of the host: platform, tools, libraries, USB, nodes, GPIO, Bluetooth |
| `scripts/observe_usb.py` | passively log USB and node events while a human changes the board's mode (udev, else the kernel uevent socket, plus inotify on node directories) |
| `scripts/try_stack.sh` | install the pinned service stack into a throwaway venv and run the two smoke tests |
| `scripts/smoke_mcp_dynamic.py` | MCP SDK registers tools from data rows at runtime and calls them |
| `scripts/smoke_ws.py` | Starlette on uvicorn serves a websocket on loopback |
| `.gitignore` | keeps `probes/` (local reports) and `__pycache__/` out of the public repo |

## Safety — what these never do

- **Never open a serial device**, at any baud, and never touch DTR or RTS. Nodes under `/dev` are
  only `stat`ed. Opening a port at 1200 baud IS bootloader entry (brief, rule 1), and a probe that
  opened one could reset or park a board.
- **Never request a mode change.** `observe_usb.py` watches while a human double-taps, replugs or
  flashes by the sanctioned route. It causes nothing.
- **Never write outside their own output.** `probe_host.py` prints to stdout; the lock directory
  is checked with `os.access`, not by creating a file.
- **Never contain a sleep or poll.** `observe_usb.py` waits on events with `select`, and its
  ceiling is a required `--max-s`. `probe_host.py` and `try_stack.sh` run subprocesses:
  `probe_host.py` bounds each `--version` call as below; `try_stack.sh` runs `pip`, whose bound is
  the human's Ctrl-C plus pip's own retry limits (a download cannot be derived from a local rate).

## Derived numbers

- **`observe_usb.py --max-s`:** required, no default. For a flash by the sanctioned route use
  **60**: the brief records a healthy RAK4631 flash at 25 to 29 s, the capture must also hold the
  two handovers (app to bootloader, bootloader to app), so about 2x the upper figure (2 x 29 = 58,
  rounded to 60). For a double-tap only, the human chooses a smaller one.
- **Version-call bound in `probe_host.py`:** self-scaling, not a constant. It first times the
  host's own interpreter start (`t0`) and allows a tool's `--version` call 22 x `t0`. Derivation,
  measured on this x86_64 host: `python3 -c pass` is 0.011 s and `pio --version` is 0.082 s, a
  ratio of about 7.5; a 3x margin on the ratio gives 22. A call that exceeds it is reported as
  "no answer within X s", never as absent; re-run it (the first call can be cold-cache).

## What each probe settles

| unknown (see the named spec) | answered by |
|---|---|
| lock path portability, `/run/lock` writable (`core.md`) | `probe_host.py`: `fs.lock_dir` |
| identity provider: udev or node-map (`devices.md`, `core.md`) | `probe_host.py`: `udev`, `nodes` |
| udev events on a Pi, a PVE host, a plain VM (`devices.md`) | `probe_host.py`: `udev`; `observe_usb.py` mode line |
| `/dev/mem` and `libgpiod` on this Pi model, Pi 5 (`nrf-workbench.md` section 5b) | `probe_host.py`: `pi`, `gpio` |
| onboard Bluetooth controller present (brief section 9) | `probe_host.py`: `bluetooth` |
| Pi header UART as a debug link: aliases, enabled, held by a getty (`console.md`) | `probe_host.py`: `uart` (version 3; unrun on a Pi) |
| which external tools exist and their versions (`core.md`) | `probe_host.py`: `tools` |
| oldest Python the host runs, against the 3.10 floor (`api.md`) | `probe_host.py`: `platform.python` |
| serial stable across the app and bootloader modes (`devices.md`) | `observe_usb.py` summary |
| which ids belong to which mode, role of `239a:802a` (`devices.md`) | `observe_usb.py` event list |
| the stack installs and runs on arm64, websockets work there (`api.md`) | `try_stack.sh` |
| SDK stdio and HTTP transports (`api.md`) | **not covered here**; needs a separate probe |

**Serial stability without events.** A container may receive no uevents. The fallback needs no
event at all: run `probe_host.py` once with the board in the app mode and once in the bootloader
mode, and compare the `usb` section. **No hand is required to change the mode**: the board's
profile names an entry method (a 1200-baud touch on its own USB CDC, a double reset on a wired
reset line, a DFU pin held low at reset, or SWD), and the family driver's `enter_bootloader()`
performs it (`devices.md`). A double-tap is only the manual fallback. The probe still causes
nothing itself: the board's id, product and serial are read from
sysfs each time. Same serial, two ids: stable.

## Results so far (this host, 2026-11-16; serials and hostnames deliberately not recorded)

Run by the author on the dev host; the clone host's results are still to come.

- **The host is an LXC container on a Proxmox kernel** (`systemd-detect-virt` reports `lxc`),
  x86_64, Python 3.13.5, Debian 13.
- **sysfs shows the board.** `/sys/bus/usb/devices` lists 18 entries including the RAK4631 in app
  mode as `239a:8029`, product "WisCore RAK4631 Board", with a serial. So **the serial IS readable
  here without udev.** This corrects the first draft of `devices.md`, which said no serial is
  available on this host. See the correction there.
- **No device node is presented.** `/dev/lxc-serial/` exists and is empty, and there is no
  `/dev/ttyACM*`, while the board is plugged in. The existing flash tool would report `absent`
  for a board that is attached (the failure `docs/spec/rak-comm.md` calls RN-4: "absent is not a
  reading").
- **No `/run/udev`**, `pyudev` not installed, `udevadm` present. No `/dev/mem`, no `gpiochip`.
- **Bluetooth:** `hci0` present (the controller's BLE 5 features are not probed). `bleak` 3.0.2
  installed.
- **Tools present:** `pio` 6.2.0 and `adafruit-nrfutil` 0.5.3.post16. Absent: `nrfutil`, `nrfjprog`,
  `JLinkExe`, `openocd`, `pyocd`, `mcumgr`, `smpmgr`, `bluetoothctl`, `gpioinfo`.
- **`try_stack.sh` passes:** the pinned stack installs in a throwaway venv and both smoke tests
  succeed (data-driven MCP tools; a websocket via `websockets`).
- **`observe_usb.py`** starts with the uevent socket and inotify here, refuses to start without
  `--max-s`, and reports "no event arrived" when nothing changes. Whether this container receives
  kernel uevents at all is **unproven** until a real mode change is made.

## Results, the clone host (2026-11-16; serials and hostnames not recorded)

The clone host is **the bare-metal Proxmox host itself**, not a Pi: x86_64, Python 3.13.5, Debian
13, `systemd-detect-virt` reports `none`. It is the machine the board is physically attached to.
So everything Pi-specific is still unprobed.

- **udev is present:** `/run/udev` exists and `udevadm` is 257. `pyudev` is not installed.
- **The board is a normal `/dev/ttyACM1`** (`166:1`), with by-id and by-path names and a udev serial
  equal to the sysfs serial. See `devices.md` for what this settles about node resolution.
- **A non-character `/dev/ttyUSB0`** is present (`0:0`): a real example of the `unrecognised` state.
- **GPIO:** `/dev/gpiochip0` exists and `/dev/mem` is readable and writable, on a machine that is
  not a Pi (`/proc/device-tree/model` is absent). **A gpiochip and `/dev/mem` are not evidence of
  a Pi header**, and upstream's `/dev/mem` BCM helper would be unsafe here. See `core.md`. The
  probe now records each chip's label and line count (probe version 2), unrun on this host.
- **Tools:** `bluetoothctl` 5.82, `btmgmt` and `hciconfig` present; **no** `pio` and no
  `adafruit-nrfutil` on this host, so a flash cannot be driven from it as it stands.
- **Libraries:** `pyserial` and `PyYAML` only; `mcp`, `starlette`, `uvicorn`, `websockets`, `bleak`
  are absent from that interpreter (they installed fine in the throwaway venv).
- **`try_stack.sh` passes** here too: same pinned stack, both smoke tests ok. Same CPU and Python as
  the dev host, so this is not evidence about arm64.
- **Still unsettled:** serial stability and the id sets across the mode change (needs the probe, or
  `observe_usb.py`, run once per mode); udev event delivery; everything on a Pi.

## Results, the Pi (2026-11-16; serials and addresses not recorded)

Run over SSH on the new Pi, in `/usr/share/pac/nrf-workbench`. A Raspberry Pi 4 Model B Rev 1.4,
aarch64, kernel `6.18.50+rpt-rpi-v8`, Debian 13, system Python 3.13.5, user root, udev present,
lock directory writable.

- **Both devices are plugged into the Pi's USB hub and seen at every level:** the RAK4631 in app
  mode (`239a:8029`) and the debug dongle (`1a86:55d4`) appear in sysfs, udev, by-id, by-path and as
  tty nodes. These are the same two devices as on the Proxmox host, moved.
- **The device numbering swapped between hosts.** On the Proxmox host the dongle was `ttyACM0` and
  the board `ttyACM1`; on the Pi the board is `ttyACM0` and the dongle `ttyACM1`. Same two devices,
  numbered by plug order. Direct evidence for "never open by a `/dev` name". The hub-port keys
  (`ID_PATH` ending `usb-0:1.3:1.0` for the board, `usb-0:1.1:1.0` for the dongle) differ from the
  Proxmox host's, so slot keys are per host. Two `by-path` names per node again (`usb` and
  `usbv2`).
- **Only "plugged in" is known.** Whether the dongle is wired to the RAK's debug UART, with which
  wires, is not stated and not probed; nothing was sent to either device.
- **GPIO:** `gpiochip0` is `pinctrl-bcm2711` (58 lines) and `gpiochip1` is `raspberrypi-exp-gpio`
  (8 lines). The probe's glob also listed `gpiochip4`, but `gpiodetect` reports two chips, so that
  path is an alias; probe version 4 dedupes by real path and reads labels from `gpiodetect`.
  `/dev/mem` is readable and writable as root, and this host really is a Pi. The Python
  `gpiod` is **2.2.0** and `gpioinfo` **2.2.1**, so the libgpiod **v2** API is what is present.
- **UART:** the device tree aliases `serial0` to `/soc/serial@7e215040` and `serial1` to
  `/soc/serial@7e201000`. `cmdline.txt` carries `console=serial0,115200`, `config.txt` has no
  `enable_uart`, no `serial0` node exists, and no serial getty runs. So the header UART is
  **disabled today, and the kernel console would claim it if enabled.** The overlays `uart2` to
  `uart5` exist and give a spare UART each on GPIOs 0-1, 4-5, 8-9 and 12-13 (RTS and CTS on the
  next two), BCM2711 only **[verified: `dtoverlay -h`]**. See `console.md`.
- **Bluetooth, the onboard controller (`hci0`), verified:** `btmgmt phy` lists only `LE1MTX` and
  `LE1MRX`, so **no LE 2M and no LE Coded PHY**; `btmgmt advinfo` gives a **31-byte** advertising
  limit and no extended-advertising flags. It supports `le`, `advertising`, `secure-conn` and
  `phy-configuration`. It is a BLE 4.x-class radio. This settles the brief's open question and
  backs the decision for a dedicated nRF52840 radio. These two were run by hand over SSH, not by the
  probe: **`btmgmt` hangs when its stdin is `/dev/null`** (`timeout 5 btmgmt phy </dev/null` exited
  124 after 5 s) yet answers in 3 ms from a shell, and the probe closes stdin on purpose. My
  first probe version ran them and reported "no answer within 0.86 s"; that bound was derived for
  `--version` calls and the real cause was the hang, so the calls were removed. Commands to run
  by hand: `btmgmt phy; btmgmt advinfo` (not `btmgmt info`, which prints the adapter address).
- **Tools and libraries on the Pi:** `bluetoothctl` 5.82, `btmgmt`, `hciconfig` and `gpioinfo`
  present; no `pio`, `adafruit-nrfutil`, `nrfutil`, `openocd` or `pyocd`; Python libraries `gpiod`
  and `PyYAML` only.
- **Not yet run on the Pi:** `try_stack.sh` (the first attempt was stopped because the clone was in
  the wrong folder; the script now builds its scratch venv inside the checkout, not `/tmp`), and any
  mode change for serial stability.

## How results come back

The repo is public and a report holds hostnames, serial numbers and USB paths, so **reports are
not committed**. Run a probe, read the output, and paste it into the session (or the part you are
content to share). `probes/` is ignored for the local copy:

```
git pull
mkdir -p probes
python3 scripts/probe_host.py | tee probes/$(hostname).json
python3 scripts/observe_usb.py --max-s 60 | tee probes/$(hostname)-observe.txt   # then change the board's mode
scripts/try_stack.sh | tee probes/$(hostname)-stack.txt
```

## VERIFY

- On this host: `probe_host.py` runs and prints valid JSON; `observe_usb.py` runs to its bound and
  summarises; `try_stack.sh` reports each step.
- No probe opens a serial device: checked by reading each file for opens of `/dev` and by a run
  with `strace` where it exists (it is absent on this host, so the reading is the check here).
- On the clone host: the unknowns in the table above get a recorded answer in their spec.
