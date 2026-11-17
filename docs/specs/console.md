# Console — functional spec (DRAFT, not agreed)

Serial debugging: reading what the DUT says, and commanding it, through a channel that is **not the
DUT's own USB**. Inputs: Peter's requirement of 2026-11-16 (a Pi can use header GPIOs, a PVE host
needs a USB serial dongle, and often the DUT's CDC is unsuitable so a separate debugger is needed),
the existing dongle tool `rak-dongle.py` (875 lines) and its skill (read-only), the brief
(sections 4, 5b, 7), and the probe results in `probes.md`. Tags: **[stated]** Peter said it;
**[prior art]** read in the existing dongle tool or skill, not re-run; **[brief]**; **[recalled]**
from memory, not checked; **[unknown]** nobody has looked.

Area: `console` (library). Depends on `core` and `devices`.

---

## GAP defects first

1. **The v0.1 acceptance run cannot be measured without this area.** The brief's acceptance test
   is "exactly one device reset per flash, from the device's own boot counter". The only
   safe reader of that counter is the debug link. Console is therefore in the first release, not a
   later one.
2. **Wiring is declared nowhere.** Which adapter is wired to which DUT, at what baud, whether the
   host's TX reaches the DUT, at what voltage, and whether DTR or RTS reach a reset or boot pin.
   The brief wants a per-slot wiring map (section 5b) and `devices.md` does not yet specify it.
   Until it does, this spec carries a `links.yaml` and flags the overlap with the pin layer.
3. **The prior art can reset the unit it is reading.** With the dongle's TX wired to the DUT's
   receive pin the bench unit took twelve soft resets in five minutes, and a 5 V dongle on that pin
   exceeds the nRF52840's absolute maximum (issue 161, in the tool's own header). **[prior art]**
   A receive-only link gives the whole log. The wiring declaration must make that a refusal, not a
   caution in a comment.
4. **A quiet link is not a silent device, and a silent link is not a broken tool.** Only builds that
   compile in the debug output emit anything; a field build is quiet but not silent. **[prior art]**
   The old tool reports "nothing received". This spec keeps that, and adds the likely causes.
5. **A reader opened before the device sleeps gets nothing afterwards**, because the target tears
   its UART down for the whole sleep: measured as zero bytes over 18 minutes while a reader opened
   after the sleep got data at once. **[prior art]** A multi-beat capture needs a reopen, and the
   old tool leaves it off by default, so a naive capture is silently empty.
6. **The brief's `console()` driver method names no source.** RTT, NUS and a UART all feed one
   console; this spec makes `console` the common abstraction and the UART its first source.

---

## TOOL why a separate debug channel

The DUT's own USB CDC is often not usable for debugging **[stated]**, and for good reasons **[prior
art, brief]**:

- opening it at the wrong baud, or just opening it, can be bootloader entry (a 1200-baud open IS
  the touch, brief rule 1), and it parked a unit in DFU for seven hours;
- its node renumbers on every DFU entry and exit, so a path that was the tap a moment ago can be
  the CDC now;
- it is gated on DTR on some builds, and absent while the radio or the whole chip sleeps;
- a field build has no CDC output at all;
- reading it can collide with a flash.

So the default console is a **separate channel that does not use the DUT's USB**. Three kinds, all
independent of the DUT's USB and so unable to collide with a flash:

| source | needs | cost and limit |
|---|---|---|
| **UART link** (this spec) | firmware that drives a debug UART; two wires and a common ground to a USB serial adapter or the Pi header UART | the firmware must be built with the debug output; sleeps tear the DUT's UART down |
| **RTT over SWD** (`swd` area) | a separate SWD debug probe wired to the DUT | **SWD attached keeps the core clocked, so deep-sleep current cannot be measured with it attached** (brief section 4). Gives RTT, GDB, fault capture. |
| **NUS over BLE** (`ble` area) | the DUT awake and advertising | only while the radio is up |

The **DUT's own CDC is not a default source**. A profile may declare it safe (an ESP-style board,
a bootloader-free build); otherwise `console` refuses to open any node that belongs to a board.

## TOOL the debug link

A **link** is a named, declared connection between a host-side adapter and one DUT slot. It is
host configuration (`links.yaml`), a map of adapters to slots, not a roster of boards.

```yaml
links:
  - name: bench-1-debug         # a label for the wiring, not a board identity
    slot: SLOT1                 # the DUT slot this adapter is wired to
    backend: usb-adapter        # or: pi-uart
    adapter:                    # usb-adapter: identity read from sysfs / udev
      ids: ["1a86:55d4"]        # CH343-class dongle, as seen on the Proxmox host
      serial: "<adapter serial>"
    port: null                  # pi-uart: the device-tree alias, e.g. serial0
    baud: 115200                # fixed by the DUT firmware, profile data, never chosen here
    dut_tx_to_host: true
    host_tx_to_dut: false       # false = receive-only: any send is REFUSED
    voltage: { dut: 3.3, adapter: 3.3, verified: false }   # refuse until verified
    dtr_rts_reach_dut: false    # must be declared; true is refused for a bootloader-capable DUT
    reply_marker: "< "          # how this firmware starts a command reply (firmware-defined)
```

- **A link is found by the adapter's identity** (ids and serial from sysfs or udev), never by a
  `/dev` name, exactly as `devices.md` resolves nodes. A node path is an output.
- **`console` opens only nodes that resolve to a declared link adapter.** Any other node,
  including every node that belongs to a DUT, is refused with `not_a_declared_link`.
- **The wiring declaration is enforced.** A send on a link with `host_tx_to_dut: false` is
  refused (`link_rx_only`); a link whose voltage is unverified is refused (`voltage_unverified`)
  until someone measures it (brief section 5b).

### Backends

- **`usb-adapter`:** any USB serial adapter. Verified present on the Proxmox host: `1a86:55d4`
  "USB Single Serial" appeared as an ordinary `ttyACM` node with a by-id name; the existing
  dongle tool's `DONGLE_MARK` and serial name the same product, so it is almost certainly the
  dongle. **[probe + prior art; not run]**
- **`pi-uart`:** the Pi's header UART (GPIO14 and 15 on the 40-pin header, usually `serial0`).
  **Probed on the Pi 4B [verified, `probes.md`]:** `serial0` is `/soc/serial@7e215040` and `serial1`
  is `/soc/serial@7e201000`; `cmdline.txt` has `console=serial0,115200`; `enable_uart` is unset; no
  `serial0` node exists and no serial getty runs. So the header UART is **off, and the kernel
  console would claim it the moment it is enabled** (the console must be removed from
  `cmdline.txt` first). Which of the two addresses is the mini-UART and which the PL011, and that
  the mini-UART's baud is tied to the core clock, are **[recalled]**.
  **Proposal, not decided:** use a spare PL011 from the overlays that exist on this Pi (`uart2` to
  `uart5`, GPIOs 0-1, 4-5, 8-9 or 12-13, **[verified: `dtoverlay -h`]**) rather than the header's
  GPIO14 and 15. It leaves both the kernel console and the Bluetooth controller alone. Which pins
  to wire is Peter's choice, because it is wiring.
  The pins a link uses are **claimed** and must be hidden from the GPIO pin layer
  (`nrf-workbench.md` section 5b refuses already-claimed Pi pins).
- **The probe's `uart` section is now run on a Pi** and its "unrun" caveat no longer applies; what
  is still unrun is any traffic on the link.
- A Pi can also use a USB adapter; a PVE host cannot use the header. The backend is chosen by the
  link, and offered only where the host has it (capability, see `core.md`).

## TOOL safety — what the console never does

- **Never open a node that is not a declared link.** In particular never a DUT's own CDC.
- **Never open any port at 1200 baud**, on any link. A link's baud is its declared value; anything
  else is refused (`baud_refused`). The old tool is safe because of what it is, not because it
  checks; this one checks.
- **Never assert or drop DTR or RTS on purpose, and set `CLOCAL`** so modem-control lines are
  ignored **[prior art]**. The kernel may still raise DTR when a port is opened **[recalled]**; that
  is harmless only where DTR does not reach a reset or boot pin, which is why
  `dtr_rts_reach_dut` must be declared and a link where it is true is refused for a DUT whose
  bootloader can be entered that way.
- **A control line wired ON PURPOSE is a different thing and has a different owner.** Peter,
  2026-11-16: an nRF need not be put into DFU by hand; the control lines can do it. That works only
  if a host line is wired to the DUT's RESET (or a DFU pin): an adapter's RTS or DTR, or a Pi GPIO
  (`nrf-workbench.md` section 5b). Such a line is declared in the slot's wiring as a **reset or boot
  control line owned by the family driver** (`enter_bootloader()` and `reset()`, `devices.md`),
  never by `console`. `console` does not toggle it, and a link whose modem lines reach reset or boot
  is refused (`dtr_rts_unsafe`) **unless that line is declared as such a control line**. An
  accidental DTR flick on open and a deliberate double-reset pulse are therefore never the same code
  path.
- **Hold the adapter exclusively** (`TIOCEXCL` and a lock), so nothing else reads or writes it.
- **Never drive a DUT pin from a 5 V adapter.** The declared voltages must match before a send.
- **The only module that opens a serial device is `nrf_workbench/console/`.** A gate test fails
  the build on any other module that opens one (`serial.Serial(`, an open of a `/dev/tty*` path).
  Per `architecture.md` it must be seen to go RED once before it is trusted. The host's port-guard
  hook already blocks raw `/dev/tty*` access from shell commands; tests therefore use
  pseudo-terminal pairs, never real nodes.

## TOOL operations

| command | effect | what it does |
|---|---|---|
| `console.status` | read | the link's state: adapter present, who holds it, bytes read, time since the last byte |
| `console.capture` | stream | read the link for a caller-supplied bound; bytes also go to the log |
| `console.since` | read, `needs_service` | everything logged after a byte cursor, plus the next cursor |
| `console.send` | write | send a verb on a link that can transmit; returns on the reply |
| `console.reopen` | write | close and reopen the adapter (see Sleep) |

- **A byte cursor, not a tail.** An agent works in turns and cannot watch a stream. `since N`
  returns what is new and the next cursor, so "what happened while I was away" is one cheap call.
  **[prior art]**
- **`send` returns on the reply event, not on a clock.** The firmware's reply starts with the
  link's `reply_marker`; that line is the event, so the answer arrives in milliseconds. The
  caller's bound is only the give-up ceiling. **[prior art]**
- **Every command has a structured result** (the `Result` of `core.md`); nothing is returned as
  prose to parse. **[prior art: "parsing prose is where agents invent facts"]**
- **A capture that gets nothing is `ok` with `bytes: 0` and a `hints` list**, never an error:
  wrong firmware (no debug build), a floating or unwired line, the DUT asleep, a receive-only link
  with nothing transmitted, the wrong adapter. **[prior art]**

### The broker

A long-running process (the service) **owns the adapter** and `select`s on it and its clients:
bytes from the adapter go to the log and to every client, and bytes from a client go to the
adapter. The log is append-only with a byte cursor. **[prior art]** This is why `since` is
`needs_service` while the others also work as a one-shot with no service: a one-shot opens the
adapter itself, exclusively, and is refused with `port_held` if the service owns it.

## TOOL slice 1: what is built first (agreed 2026-11-17)

Peter, 2026-11-17: "we need a serial console, the serial console needs a command line input so that it can
interract with the device ... select a connected dongle or GPIO pins ... shortcut commands or macros ... history
... up and down arrow ... just like a console." The dashboard page is a separate task; this is the backend it sits on.
The rest of this spec stays draft. **Built in slice 1:** the `usb-adapter` backend, `console.links`,
`console.since`, `console.send`, `console.reopen`, the macros, and a live feed. **Not in slice 1, and said so:**
`console.capture` (a stream), `reopen_after` and sleep handling, the `pi-uart` backend, the boot-counter parse, RTT
and NUS.

- **The pins option waits.** "A dongle or GPIO pins" is `usb-adapter` or `pi-uart`. The Pi's header UART is **off on
  this Pi** and the kernel console would claim it (`probes.md`), and which pins to wire is Peter's choice. So the
  page offers whatever links are declared; the first is the dongle. A `pi-uart` link is declared the same way and
  appears in the same list the day its backend lands; nothing in the page or the commands changes.
- **Configuration, no defaults** (`install.md`): `paths.links` is `links.yaml`; `paths.console` is a folder for the
  macros; `streams.console` has `retain` (a count of LINES) and `max_age_days`, both required like every retained
  stream. **With any of the three unset the console is absent and `GET /api/streams` says which**, so a host that
  never declared a link shows no console at all.
- **A link is validated when loaded, and an invalid one is listed with its problems and never opened.** The name is a
  slug (`a-z 0-9 . _ -`, starts with a letter or digit, at most 64, unique). `backend` is `usb-adapter` (a `pi-uart`
  link is listed as "backend not built"). `adapter.ids` and `adapter.serial` are required: **the adapter is found by
  USB ids and serial through sysfs and joined to its node by device number, never by a `/dev` name** (the same
  `identity` code the boards use). A link whose adapter ids match **any board profile's ids is refused**
  (`not_a_declared_link`): the console never opens a DUT's own port. `baud` must be a standard rate and not 1200
  (`baud_refused`). `dtr_rts_reach_dut: true` is refused (`dtr_rts_unsafe`); a deliberately wired reset line belongs to
  the family driver, which is not built, so slice 1 has no exception. `eol` is `\n`, `\r` or `\r\n` (default `\n`).
- **Reading is safe on any valid link and sending is not.** A receive-only wire gives the whole log (GAP 3). A send is
  refused unless `host_tx_to_dut` is true (`link_rx_only`) **and** `voltage.verified` is true (`voltage_unverified`),
  and the voltages are shown. The refusals are made by the service, not by the page.
- **One broker, one thread, owns every adapter.** It blocks in `select` on the kernel's uevent socket, a wake pipe
  and every open port, with **no timeout** (the device monitor's rule): an adapter that appears is opened and one that
  vanishes is closed on the kernel's word, not on a timer, and the state change is published. A host with no uevents
  says so and re-checks on each command instead.
- **The port is opened with plain `termios`, not a library that asserts modem lines.** The fd is opened
  `O_NOCTTY | O_NONBLOCK`, made exclusive (`TIOCEXCL`), raw, 8N1, `CLOCAL | CREAD`, **`HUPCL` cleared so closing does
  not drop DTR**, and DTR and RTS are never set by this code. (pyserial's constructor sets both on open: the spec's
  "never assert on purpose" rule is why it is not used here.) Another process holding the adapter gives `port_held`.
- **A line is an event.** Bytes are split on `\n` (a trailing `\r` is dropped), decoded as UTF-8 with replacement, and
  published as `console.line` with the link, the **byte offset where the line starts**, its length and its text; a line
  starting with the link's `reply_marker` is flagged. A line longer than 1 024 bytes is cut and marked `partial`: **1 024
  is a memory budget, not a measurement** (20 000 retained lines at that size is 20 MB, far under the Pi's RAM), and no
  firmware's real line length has been measured here. State changes are published as `console.link`.
- **A byte cursor, as the spec said.** `console.since(link, cursor)` returns the retained lines at or after that byte
  offset, the next cursor (the offset of the first byte not yet delivered, so a half-received line is delivered whole
  later), and `lost`, how many bytes fell out of retention since the cursor. It needs the service (the log is its
  memory).
- **`console.send` sends one line.** The text may not contain a newline, a carriage return or a NUL (`usage`): one
  call is one command, and a block is a macro. The link's `eol` is appended. **With no `bound_s` it returns as soon as
  the bytes are written**, which is what an interactive terminal wants (the reply arrives on the feed); **with
  `bound_s` and a declared `reply_marker` it returns on the reply line, and `no_reply` when the bound passes**. There is
  no default bound: it is the caller's, derived from the firmware.
- **Macros are the bench's, not the browser's.** A macro is a name and some lines, kept in `<paths.console>/macros.json`
  (written atomically), so the CLI, the MCP and every browser see the same ones. `console.macro_run` sends the lines
  in order **through the same send path, so every guard applies to every line**, and stops at the first refusal. A
  macro's text is at most 4 096 bytes (four times the line cap: a macro is a few commands) and its name a slug.
- **History is the activity log.** Every send is a write command and so is already recorded with its link and text, from
  every caller; the page's up and down arrows walk the sends for the selected link. **A consequence, stated:** whatever
  is typed into a console is in the activity log in clear text, as every command argument is, and a typed secret is
  not redacted unless its argument is named like one.
- **Only `nrf_workbench/console/` opens a serial port for reading and writing**, enforced by a gate test (seen RED
  first). **Two older, named exceptions are allowed and listed in the test, not hidden:** `devices/entry.py` opens the
  board's own port at 1200 baud for the bootloader touch (that is the touch), and `programmer/flash_file.py` hands a
  port to the uploader tool. The spec's earlier "only module" wording did not account for them.

## TOOL sleep and silence

- The DUT may tear its UART down for a whole sleep, so a reader opened before a sleep sees nothing
  after it. A multi-beat capture takes a `reopen_after`: close and reopen after that long with no
  byte. **[prior art]** It is **off by default** and **required for a capture longer than one
  silent gap**, because the failure is silent.
- **`reopen_after` must exceed the longest legitimate silent gap, or it trips on every beat.** It is
  derived from the device's own measured numbers, not chosen. The old tool's worked example, for
  one firmware: beat 60 000 ms, awake 7 342 to 12 220 ms measured, so the longest legitimate gap is
  60 000 minus 7 342, about 53 s, and 90 s is about 1.7 times that. **[prior art, one firmware]**
  Those figures belong to that firmware; the service reads the measured beat and awake time from
  recorded measurements (see `api.md`) and refuses a wait with none (`no_bound`).
- **`rx=never` is evidence only if something was transmitted during the capture.** That mistake was
  made once and reported as a defect that did not exist. **[prior art]** `console` therefore
  reports whether anything was sent to the DUT during the window.

## TOOL the acceptance use

`console` is how the programmer proves "exactly one reset per flash": read the boot counter from the
heartbeat line before and after the flash and require it to advance by exactly 1 (brief rules 2
and 5). The line format is firmware-defined, so the profile (or link) records which field is the
boot counter. A flash that leaves the counter advanced by 2 is the double-tap bug.

## TOOL new codes (to be added to `core.md`)

| code | class | when |
|---|---|---|
| `link_absent` | refused | the declared adapter is not attached |
| `not_a_declared_link` | refused | the node is not a declared link adapter, or belongs to a DUT |
| `baud_refused` | refused | a baud other than the link's declared one, or 1200 |
| `link_rx_only` | refused | a send on a link whose host TX is not wired to the DUT |
| `voltage_unverified` | refused | the link's voltage domain has not been measured |
| `dtr_rts_unsafe` | refused | DTR or RTS reach the DUT's reset or boot line |
| `no_reply` | failed | a sent verb got no reply within the caller's bound |

`port_held` and `no_bound` already exist. The old tool's exit codes (0 answered, 1 the device did
not answer, 2 could not try) become `ok`, `no_reply` and the refusals.

## DECIDE derived (Claude; Peter may overrule)

1. **The default console is a separate channel, never the DUT's CDC.** Constraint: Peter says CDC
   is often unsuitable, and the prior art and brief give four independent reasons (bootloader
   entry on open, renumbering, DTR gating and absence in sleep, collision with a flash).
2. **`console` is its own area, not part of `programmer` or `swd`.** Constraint: it shares no
   function with flashing, and RTT and NUS feed the same abstraction, so one owner avoids three
   copies of cursor and reply logic.
3. **Console is in the first release.** Constraint: the acceptance criterion needs the boot counter
   and only this link can read it safely (GAP: the v0.1 acceptance run cannot be measured without this
   area).
4. **Refuse, do not warn, on the two electrical hazards** (receive-only wiring, unverified voltage).
   Constraint: the twelve resets in five minutes came from a caution in a comment that nobody read.
5. **One module opens serial ports, enforced by a gate test.** Constraint: the whole point of the
   rules above dies the first time another module opens a port.
6. **`reopen_after` is a required argument for a long capture, with no default.** Constraint: it
   is a derived number, and the failure it prevents is silent.

## VERIFY

- **Probes:** `probe_host.py` gains a `uart` section (aliases `serial0` and `serial1`, `ttyAMA*`,
  the `enable_uart` lines, and whether a getty holds the UART) to run on a Pi. **[unknown]** until
  run.
- **Fake link:** a pseudo-terminal pair stands in for the adapter and a DUT; the guard tests prove
  each refusal without hardware (each seen RED then GREEN).
- **Reply by event:** `send` returns within the reply's arrival, not at the bound.
- **A capture across a sleep** with `reopen_after` returns data; without it, the harness shows the
  silent-empty failure (so the reason for the rule is itself tested).
- **Hardware:** the existing dongle on the Proxmox host reads the RAK4631's heartbeat; the boot
  counter is read before and after a sanctioned flash. Needs Peter's go-ahead on the board.
- **The gate test** that no other module opens a serial device is seen to fail.
