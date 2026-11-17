# Wiring — functional spec (DRAFT; the UI is built first, on sample data)

Peter, 2026-11-17: serial could be a USB dongle or the Pi's GPIOs, "this is a feature that should be available to all
users"; a connection must be **associated with a specific connected nRF** ("there may be multiple NRF's attached, and the
GPIO might be used as GPIOs for any of those devices, it might be I2C, it might be serial, it might be serial debugging,
One Wire, A2D / D2A"); and, on how it is presented, "that needs to be intuitive and user friendly instead of just being
the easiest and fastest method you can think of". He agreed the model below and said "I can only tell you when I see it",
so the page was built first as a clickable preview on in-browser sample data. Tags: **[stated]** Peter said it;
**[decided]** a design decision, with why.

Area: `wiring` (replaces `links.yaml` in `console.md` and the flat pin map in `gpio.md` once built). Today only the UI
preview exists: **it touches nothing on the bench**, and the page says so.

## The model

- A **device** is an nRF, identified by its USB serial, as flashing and `protected` already do (`devices.md`).
- A **connection** belongs to exactly one device and has: an **intent** (what it is for), the **Pi side** (a USB adapter
  or Pi pins), the **device side** (the signal on the board, named from the board profile where it is known: the RAK19003's
  `TX0 / RX0`), and the **electrical facts** (voltage, whether the Pi may send, a pulse limit).
- Intents: **debug log** (a UART), **control line** (reset, boot or power, a Pi pin), **sensor or bus** (I2C, SPI,
  1-Wire, with the Pi as controller), **SWD** (a debug probe), **analog** (the Pi has no ADC or DAC, so this is an ADC
  chip on a bus, said on screen).
- **A pin or an adapter is owned by one connection.** A UART on pins 4 and 5, or I2C on 2 and 3, claims them; the plain
  pin list and every other connection see them as taken, and say by whom.

## The UI [decided]

1. **Intent first, not protocol first.** People think "read its log" or "reset it", not "UART" or "GPIO line". The
   protocol appears in the second question, as the ways that are available.
2. **One page, the device is the unit.** Device cards across the top (each with a stable identity colour that follows it
   to every wire and pin); under the chosen device, its connections; under that, the Pi header coloured by device.
3. **A connection is a wire sentence,** the same in the list and in the sheet, so what you build is what you see:
   `Pi end ── signal ── device end`, with what it is doing now and whether the Pi may send.
4. **One sheet that collapses as you answer, not a wizard.** Each answered question becomes a sentence and can be
   changed; the next open question is shown; the finished sentence is the connection. Numbered steps are not used
   because the content is a conversation, not a sequence.
5. **Only what is possible is offered, and the rest says why not.** A claimed adapter or pin stays visible, greyed, naming
   its holder. The page never lets you pick a pin another connection owns.
6. **Safe by default, in plain words.** A serial connection is receive-only. Sending is a separate tick that asks the one
   safety question with its consequence; "I haven't measured it" is an honest answer and keeps sending (or driving) off.
   There are no hidden defaults: a baud, a pulse limit and a voltage are stated by the person.
7. **The system notices.** A newly plugged-in adapter appears in a "plugged in, not connected" box, already asking which
   device it is wired to. (In the preview a button simulates plugging one in.)
8. **The Console and GPIO pages become views onto a connection** ("Open console", a coloured header map), not separate
   places to configure.
9. **Every action is one command** (`wiring.connect`, `wiring.change`, `wiring.disconnect`), so the CLI and MCP are the
   same operations and the UI holds no rule of its own: the service says what is available and refuses what is unsafe.

## Backend slice 1: the debug log (built 2026-11-17, after "continue with what you were doing")

Why this one first: it is the one that was blocking the `device` session, and it replaces the hand-edited `links.yaml`.
The other intents are refused by name (`intent_not_built`) until their slice lands; the page shows them as not built yet.

- **A connection is stored on the bench** in `<paths.wiring>/connections.json` (atomic, `0700`, the shared JSON store of the
  console macros and the pin map), keyed by its **name**, a slug. The name is **the console link's name**, so the console's
  API does not change (`rak-uart0` stays `rak-uart0`). With no name given it is `debug-<first four of the device serial>`.
- **Fields of a debug-log connection** (all stated, none defaulted except the name): `device` (the nRF's USB serial),
  `adapter` (the serial of a detected USB adapter), `baud`, `send` (may the bench send to the device) and, when `send` is
  true, `voltage_measured`. Anything not given is a usage error. The stored record also holds the adapter's USB ids, read
  from the detection at connect time, so the console finds it again by ids and serial and never by a `/dev` name.
- **Adapters are detected, not typed.** `wiring.show` lists every USB device with a serial-port node that is **not a
  board**, each with its ids, serial, product name, node, and which connection holds it. An adapter with no serial number
  is listed and cannot be connected (it cannot be told from another one), and says so.
- **Refusals, by name, made by the service:** `adapter_absent` (not detected), `adapter_taken` (another connection holds it;
  the holder is named), `name_taken`, `not_a_declared_link` (the adapter's ids belong to a board profile: the console never
  opens a board's own port), `baud_refused` (not a standard rate, or 1200), `intent_not_built`. A send needs `voltage_measured`:
  a connection with `send` true and the voltage not measured is **stored and receive-only**, said in its description.
- **The console reads its links from the connections** when `paths.wiring` is set, and `links.yaml` is then not read (the
  start-up line says so). A change takes effect **at once, without restarting the service**: the broker replaces its links
  on its own thread, closes the port of a removed or changed link and opens a new one. **A removed link's byte offsets are
  not reused:** a link of the same name created later continues from the old offset, so a cursor never points at two
  different lines. (A service restart still empties the log; it lives in memory.)
- **Commands:** `wiring.show`, `wiring.connect`, `wiring.change` (baud, send, voltage only; moving to another adapter or
  device is a disconnect and a connect), `wiring.disconnect`. Writes record the caller in the activity log like any command.
- **`wiring` is a capability that needs only `paths.wiring`**; it is not tied to a Pi, because a USB adapter works on any host.

## Not yet built

Control lines, buses, SWD and analog connections; the migration of the flat GPIO pin map into control connections; the
header map fed by real claims. The preview's sample data in `ui/src/wiring/model.js` is the contract they must satisfy.

## Files this spec allows

`docs/specs/wiring.md`, `ui/src/wiring/*` (the preview), `ui/src/App.jsx` (the page), `scripts/verify_dashboard.py`.
