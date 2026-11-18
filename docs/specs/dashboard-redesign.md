# Dashboard redesign: a bench you can read at a glance (DRAFT)

Peter, 2026-11-18: "the functionality is starting to look good, but the dashboard is a mess ... the home page is just a load of text, the
menus at the top wrap when you use a phone, the commands page is just another bunch of text and is non functional. devices is confusing,
it's difficult to tell which devices are connected and which are not ... it should show the status of all devices, gpio mapping, etc so at 1
glance I can see what is happening. clicking on one of those should take me to the details ... I cannot spend the time to identify all of
these issues." This supersedes the page layouts in `dashboard.md`; its rules (no timers, no absolute URLs, writes only to server-listed
paths, a `dark:` partner for every light colour, Radix on the Firmware page only, the built UI committed with its source stamp) stay.

## What was wrong (read from the code and from Peter)

1. **No home.** `Bench.jsx` is five cards of prose about the service. Nothing says what is attached or whether it works.
2. **The nav wraps on a phone.** `flex-wrap` on eight buttons, then the theme button on a second line, then the band, then the page.
3. **"Attached" is a nRF-only box above every page**, with ESP32 rows bolted on, and Devices lists only nRF boards. An adapter, an ESP32
   and a wired connection are three different places, and a device that is not plugged in is invisible, so *connected versus not* cannot be
   read anywhere.
4. **Commands is a list of collapsed rows**: no search, no grouping you can scan, and every argument is a blank text box (a USB serial typed
   by hand), so it is correct and unusable.
5. **Every section is the same white card**, so nothing is more important than anything else.

## Principles

- **The first screen answers "is the bench alright?"** in one sentence and then shows the evidence. Everything on it is a link to the page that
  has the detail; nothing on it needs scrolling to learn the state of a device.
- **State is a shape, then a colour.** Connected is a filled disc, not attached is a hollow ring, needs attention is a diamond, and a fault
  is a square. Colour repeats it. (A colour-blind reader and a phone in sunlight both read the shape.)
- **Rows, not cards.** Sections are plain lists with hairline dividers under a heading that is itself a link. One emphasis per screen.
- **A device is one thing, wherever it hangs.** One inventory holds nRF boards, ESP32 boards and USB or Pi serial adapters; each row says
  what it is connected to and whether it is there now.
- **Say it in the words of the bench**, not the service: "not attached", "listening", "receive only", "needs a reboot".
- **It must be intuitive without reading.** (Peter, 2026-11-18: "it MUST be intuitive, probably it needs a dedicated and structured help
  page(s) rather than dumping loads of unnecessary text all over the place.") A working page carries **no explanatory paragraphs**: a title,
  the state, the actions, and at most one line when a list is empty ("Nothing is wired yet. Connect something."). Anything that needs more
  than a line lives in **Help**, and each page header has a **?** that opens the topic for that page. A refusal still says what to do, in
  its own message; that is the result, not decoration. Tooltips (`title`) carry a short reason for a disabled control.

## Tokens

- **Colour** (Tailwind classes, each light with its `dark:` partner): page `slate-100` / `slate-950`; a panel `white` / `slate-900` with a
  `slate-200` / `slate-800` hairline; text `slate-900` / `slate-100`, secondary `slate-600` / `slate-400`; **connected** `emerald-600` /
  `emerald-400`; **not attached** `slate-400` / `slate-500` (the ring); **attention** `amber-600` / `amber-400`; **fault** `red-600` /
  `red-400`; **action** `blue-600` / `blue-400`. No gradient, no shadow, radius `rounded-md` on panels and `rounded` on controls.
- **Type**: the existing named system sans stack (no web font: the UI may not fetch one, and a bundled one is a dependency for a bench tool),
  with `tabular-nums` on every number. Scale 13 / 15 / 17 / 24 px: row text 15, secondary 13, section heading 17 semibold, the home
  sentence 24. Identifiers (serials, paths, addresses) are monospace and nothing else is.
- **Layout**: `max-w-5xl`, left aligned, 16 px gutter; line length under 70 characters in prose.

## The shell

- A sticky header: the bench's name, a lamp and one line ("Raspberry Pi 4 Model B, 192.168.0.214, live"), the theme button.
- **The nav is one `nav[aria-label=Pages]` that never wraps**: a single row of tabs that scrolls sideways when it is wider than the screen,
  the current tab scrolled into view, a fade at the clipped edge, 44 px tall touch targets. Home, Devices, Wiring, Console, Firmware, GPIO,
  Activity, Commands, in that order (the daily ones first).
- The old "Attached" band is removed from every page: its job moves to Home and Devices.

## Home

A sentence, then four sections. Data comes from **one** call, `bench.overview` (below), read on load, on a USB change, on a link's state
change and after any command, never on a timer.

1. **The sentence**: "4 of 6 devices connected. 2 links listening. I2C ready. 1 needs attention." It is built from the counts, and the
   attention part names the thing and links to it.
2. **Devices** (heading links to Devices): one row each: lamp, name (or product), kind, one line of what it is connected to. Not-attached
   devices that the bench knows (a named board, a declared ESP32, a wired connection's board) are listed after, ringed.
3. **Connections** (heading links to Wiring): each wire as "RAK4631 left-rak -> Pi UART, 115200, listening (13 lines)", with its lamp, and the
   I2C bus and the pins it holds.
4. **The Pi** (heading links to GPIO): the 40-pin header drawn as it is on the board, two columns, each pin a dot coloured by who holds it
   (free, I2C, UART, pin map, reserved, power, ground); tap a dot for its name. Under it: the model, address, uptime, which capabilities exist.
5. **Recent activity** (heading links to Activity): the last five commands, who ran them, how they ended.

Two columns from 768 px wide (Devices and Connections left; the Pi and Activity right), one column below.

## bench.overview (area `bench`, capability `sysfs`)

A read that gathers what the pages need, from the same functions the other commands use, so it cannot disagree with them. It opens no port
and probes nothing. Sections whose capability is absent are `null`, with the reason in `absent`.

- `host`: `model` (device-tree model or null), `uptime_s` (`/proc/uptime` or null), `capabilities`.
- `devices`: one list. Each `{id, kind (nrf|esp32|adapter), name, label, product, serial, state (connected|absent|attention), why,
  mode, node, slot, connections: [names], page}`. Built from: attached nRF boards; nRF serials a wiring connection names that are not
  attached (absent); declared ESP32 targets, attached or not; undeclared built-in-USB ESP32 devices (attention: "not declared"); USB serial
  adapters not claimed by a connection or a target; the Pi's own UART when the host has a pin map.
- `connections`: wiring connections with their console state: `{name, device, device_name, via, baud, state, rx_only, lines, last_byte_at,
  pins}`; the ESP32 targets' own console links are shown on their device row, not here.
- `i2c`: `{enabled, buses: [{bus, clock_hz}], pins_claimed}` or null.
- `header`: the 40 pins with `{pin, label, kind, gpio, class, holder, holder_kind (pin-map|connection|None), service}`, or null.
- `activity`: nothing here; the page already holds the activity feed.
- `counts`: `{devices, connected, absent, attention, links, listening}` and `sentence`'s parts.

`state` rules: **connected** = attached now and nothing wrong; **absent** = known and not attached; **attention** = attached with a problem
(an undeclared ESP32, a nRF in `two` modes, a port held by another process, a link that is `held` or in error, an adapter with no serial).

## Devices page

One inventory, filtered by three tabs: **Connected**, **Not attached**, **All**; default Connected, with the counts on the tabs. Each row
is the same as on Home with its detail beneath on tap: serial, ids, port, mode, USB slot, what it is wired to, and its actions (name,
rename, flash, declare, forget, identify, reset), the existing commands reused.

## Commands page

Two panes from 768 px (one stacked below): a **search box and the list** (grouped by area, a filter for read or write), and the **form**
for the chosen command. The form is generated from the registry as it is now, and in addition **an argument the bench can list is a
picker**: `serial` (attached nRF boards, named), `target` (ESP32 targets), `link` and `name` of a link (console links), `firmware` and `id`
(stored packages), `device` (nRF serials), `adapter` (adapters). The typed value is still allowed. A write says what it will do before it
runs (the summary and the path), and the whole Result stays, as does the rule that a bound is always typed.

## Help (a page of its own, and the only place for explanation)

The last tab. A **topic list** (left on desktop; the whole screen on a phone, with a back arrow from a topic) and one topic at a time, each
topic **short, structured and scannable**: a one-sentence purpose, then **What you see** (a labelled legend, e.g. the four lamp shapes),
**Do this** (numbered steps only where the content is a sequence), **If it does not work** (symptom, cause, fix, one line each) and **Related**
(links to other topics and to the page). No topic is longer than a screen on a phone. The content is **data** (`ui/src/help.js`: an array of
`{id, title, page, summary, legend, steps, problems, related}`), so a test can check that every page has a topic, every `related` link
resolves, and every refusal code a page can show has a one-line fix. Topics:

1. **Start here**: what the bench is, reading Home (the sentence, the lamps), where things are.
2. **Devices**: connected, not attached, needs attention; naming a board.
3. **Wiring a debug log**: connecting a board to a USB adapter or the Pi's UART; receive only versus sending.
4. **The console**: reading, sending, history, macros, crash decoding.
5. **Flashing**: an nRF package, an ESP32 image or bundle, the project folders, what a good flash looks like.
6. **ESP32 boards**: declaring, renaming, identify, reset, hold in bootloader.
7. **The Pi's pins, I2C and UART**: the header map, claiming pins, enabling I2C and the UART (the boot steps).
8. **Commands, the API and the AI tools**: the Commands page, `/api/guide`, the CLI, the MCP server.
9. **Troubleshooting**: by symptom ("a board is not listed", "a port is held", "a send is refused", "nothing prints", "the page is stale").

## Files this spec allows

`nrf_workbench/bench/{__init__,overview,commands}.py`, `nrf_workbench/service.py` (registration), `docs/specs/{dashboard-redesign,api,
dashboard}.md`, `tests/bench/`, `ui/src/{App,Home,Shell,Devices,Commands,Help,help,Band,Bench,ui,format,index.css}.jsx|js|css` and the files they
import, `ui/src/wiring/*`, `nrf_workbench/dashboard/static/*` (the build), `scripts/verify_*.py`, `tests/dashboard/`.
