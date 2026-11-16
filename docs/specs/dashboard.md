# Dashboard — functional spec (DRAFT, not agreed)

The web dashboard served by the nrf-workbench service. Peter, 2026-11-16: "you know we need a
dashboard right?" Inputs: the other specs, upstream's UI (read-only), and the rules recorded in the
mesh-gw dashboard docs (read-only, another project: `dashboard_tells_the_truth.md`,
`plugin_declared_streams.md`). Tags: **[stated]** Peter said it; **[prior art]** read in the
mesh-gw dashboard docs; **[upstream]** read in upstream `portal.py` at `db6a093`; **[unknown]**
nobody has looked.

Area: `dashboard` (service). Depends on `core` and `api`. Release: v0.2, because it needs the
service.

---

## Files for the first server slice (added 2026-11-16, Peter: "nothing is being served")

`nrf_workbench/dashboard/__init__.py` and `dashboard/server.py` (the page at `/`, the bench name put into the
title **on the server before any JavaScript runs**, the plain page that says the UI has not been built when it has
not, built assets served under `/assets/` when they exist, and `GET /api/dashboard/status`), a small change in
`nrf_workbench/api/app.py` (`build_app` accepts extra routes, so `api` and `dashboard` never import each other),
the wiring in `nrf_workbench/service.py` (the composition root: the one module allowed to import both) and
`nrf_workbench/core/config.py` (`service.bench_name`), and `tests/dashboard/`. **Decided while building:** the page
at `/` is public in every access mode because it is static and carries no data (a page load cannot carry a bearer
header, and every byte of data still comes through the authenticated API and feed); the login, the session cookie
and the CSRF header are the next slice. The bench name defaults to the machine's own host name, which is a fact
about the machine and not an invented number.

**Built and tested, 2026-11-16: the page, the assets and the status** (`dashboard/server.py`; 39 tests; 27
mutants caught). The bench name is in the page **before any script**, HTML-escaped (a hostile name cannot inject
markup, and a name containing the placeholder does not confuse the substitution). The page that says the UI has
not been built has **no script**, both colour schemes, a phone-width viewport and a single base font size, and links
the real API routes. Asset paths cannot leave their folder: literal and encoded `..`, an absolute path, a NUL
byte, a **symlink that points outside**, and a **sibling folder whose name merely starts with the assets path**
are all 404 (the last was a mutant that survived until a test used the encoded form, because an HTTP client
normalises a literal `../` away before it is sent). The page is public in every access mode and the API stays
behind its token. **The status route's "no write route" claim is checked three ways:** every route's methods are
reads, a write to every route is a 405, and the source is scanned for a `Route` with another method. A test also
checks this package never imports `api`. **Not built:** the login page, the session cookie, the CSRF header, the
cookie-authenticated websocket, and the UI itself.

## Files for browser login (added 2026-11-16, Peter: "get this completed")

`nrf_workbench/core/sessions.py` (the session store: both `api` and `dashboard` need it, so it lives in `core`),
`nrf_workbench/api/auth.py` (accepts a session cookie as well as a bearer token; a write by cookie needs the
per-session `X-CSRF-Token` and a same-host `Origin`), `nrf_workbench/dashboard/server.py` (`POST /login`,
`POST /logout`, `GET /api/session`), `nrf_workbench/service.py` (wiring), and tests. **Derived limits, written
beside the code that uses them:** a session lasts **12 h** (a working day of 8 h at 1.5x, so a session survives
the day it was opened in and is gone by the next morning) and at most **16** sessions are held (one operator on
a phone, a laptop and a tablet is 3 live; each re-login before expiry leaves one stale; 3 x (1 live + 1 stale) =
6, at 2.5x so a busy week never evicts a live one). The id is 256 bits from the OS random source; the CSRF
value is separate from the id so a page that can read one learns nothing of the other. The cookie is
`HttpOnly; SameSite=Strict; Path=/` (and `Secure` only when the service is itself behind TLS, which it is not
yet). There is **no login rate limit** in this slice: the token is 256 bits, so guessing is not a path; a
lockout would only give an attacker a way to lock the operator out.

**Built and tested, 2026-11-16: browser login** (`core/sessions.py`, the cookie path in `api/auth.py`, the
login routes in `dashboard/server.py`, the wiring in `service.py`): 69 new tests; 44 mutants caught, the one
survivor equivalent. **The token is entered once and never held by script:** `POST /login` answers with an
`HttpOnly; SameSite=Strict; Path=/; Max-Age=43200` cookie (and `Secure` exactly when the request arrived over
HTTPS, so it is never a cookie the browser would silently refuse to send back over plain HTTP); the body has
the CSRF value and never the session id or the token. **A write by cookie needs the session's own CSRF value
in `X-CSRF-Token`** (another session's value, or the session id, does not work) **and an `Origin` that is this
host**, so a foreign page cannot ride the cookie for a read, a write, a login, a logout or the websocket
(the Origin is checked before anything else). A login **never inherits an old session** (fixation); a logout
without the CSRF header logs nobody out; the CSRF value is given only to a request that already holds the
session. The session limits are **derived and written beside the constants** (12 h; 16), and a test pins them.
**The status route's write-route claim was changed, and the test that watches it caught the change first:** the
dashboard now has exactly two POST routes of its own, `/login` and `/logout`, which are session handling only
and touch no hardware; the claim is checked three ways (source, mounted routes, status). **Not exercised in a
real browser yet** (that is the next verification, with the UI). **Not built:** a login rate limit (by design,
above), the UI.

## GAP defects first

1. **The live feed does not exist yet.** `api.md` has request and response and a websocket per
   stream command, but no bench-wide event feed, and `core.md` has no event bus. The dashboard
   needs both: state changes, console lines, flash progress and the activity of every caller. They
   are added to those specs (see Folded into other specs).
2. **There is no record of what the CLI, API and AI did.** Nothing in the specs keeps an activity
   log. For an AI that flashes hardware this is the page the human most needs, so it is required,
   and it must be bounded (see Retention).
3. **Upstream's UI is an inline HTML string inside a 258 KB `portal.py`** (`_UI_HTML`, served by
   `_serve_ui`, ~line 4947). **[upstream]** It cannot be tested or built on its own, and the
   modular rule rejects it. What is worth keeping from it: the bench name is substituted into the
   tab title on the server **before any JavaScript runs**, so it is right even when the API is
   wedged, which is exactly when someone with two tabs open needs to know which bench they are on.
4. **Sizes actually used are unknown.** The Proxmox host shows a 3.5-inch USB display
   (`1a86:5722`, "UsbMonitor") **[probe]**. Whether it is meant to show a bench status is
   **[unknown]**; see the open question.
5. **My first draft had no always-visible list of what is attached, and the ESP workbench's
   dashboard has one.** Peter, 2026-11-16: it auto-detects connections to the USB ports,
   identifies them, and lists them at the top of the dashboard. He then said he is **not asking for
   a copy** and a better way may exist; my first reaction was to copy it, which was the wrong
   reading. The **requirement** kept: detected automatically, identified, at the top, live. The
   **layout** is this spec's own (the bench band, below). I had read upstream's hotplug and slot
   code but not its UI. **[upstream: read `_UI_HTML` afterwards]** Not carried over: one card per
   USB port, its identification table hardcoded in browser JavaScript (it lists the CH343 as
   `1a86:55d3`, while this bench's dongle is `1a86:55d4`, so it would fall through to the raw
   product string), and polling every 5 s (devices) and 1.5 s (log), against Peter's no-polling
   rule.

---

## TOOL what it is for

Two audiences, one page set: the person at the bench, and the person watching what an AI does to
hardware. The AI drives through the CLI, API and MCP; the dashboard is how a human **sees** it
happen and **stops** it going wrong. So it is read-mostly, and everything it shows is something the
service already knows.

## TOOL pages

Each page appears only if its area is present on this host. A page for an absent area is not drawn
as empty or disabled; it is absent from the navigation, and `host.capabilities` explains why. This is
the same rule as the commands. **[prior art: "absent, not broken"]**

| page | shows | source |
|---|---|---|
| **Bench band** (on every page, at the top) | everything attached, identified and grouped into rigs, with a readiness verdict per board, live; see the next section | `devices`, `guards` |
| **Bench** | bench name, host (architecture, Pi or not, container), service status, the capability list with what is **missing and what it blocks** | `host.capabilities` |
| **Devices** | one card per slot: state (`app`, `bootloader`, `two`, `unrecognised`, `absent`, `attached_no_node`, `handover`), identity (serial, ids, product), the node and **how it was resolved**, `node_joined`, who holds it, `protected` | `devices` |
| **Flash** | pick a device and a package; the guard verdict as it happens; progress; the **whole** tool output; duration against the recorded healthy duration; every refusal with its code and evidence | `programmer`, `guards` |
| **Console** | per link, a live log with a byte cursor, a send box (disabled with the reason if the link is receive-only), reply correlation, the boot counter, and the hints when a capture is silent | `console` |
| **Activity** | every command from every caller (CLI, API, MCP, dashboard): who, what, class, code, time. Refusals are prominent. | the activity log |
| **Measurements** | the recorded measurements a caller derives bounds from, each with what, when, which unit, how many samples; and which are **missing** | `api.md` measurements |
| **Commands** | a form for every present command, generated from the registry (typed args, units, `effect`) and the same `Result` shown back | `GET /api/commands` |
| **later** | BLE scan and GATT, pins and edge capture, power profile | `ble`, `gpio`, `instruments` |

The **Commands** page is what makes the dashboard complete on the day a new area lands: a command
added to one area appears in the CLI, the API, the MCP, and here, with no dashboard edit. The
curated pages above are the workflows worth drawing by hand.

## TOOL the bench band

At the top of every page, always: **everything attached over USB, detected automatically,
identified, and updating the moment something is plugged in or pulled out.** **[stated]** That is
the requirement. This is a proposed design for it, deliberately not the ESP workbench's.

**Why not a card per USB port.** An ESP is one cable and one card. An nRF rig is several devices
serving one board: the board, a debug link (a UART adapter), possibly an SWD probe, a BLE radio and
a power profiler. A card per port splits the rig into unrelated cards and shows none of the
relations an operator needs, and it answers "what is plugged in" when the operator's question is
**"can I flash this now?"**

**The design:**

1. **One row per board (a rig), not one card per port.** A row shows the board's identity and mode,
   a **readiness verdict**, and the **instruments attached to it as chips on the same row**: its
   debug link, its SWD probe, a BLE radio, a power profiler, each with its own state. The relations
   come from the declared per-slot wiring (`devices.md`, `console.md`).
2. **The headline is readiness, not presence.** `ready`, or the first refusal with its code and
   evidence: `attached_no_node`, `protected`, `port_held` (and by whom), `node_joined: false`,
   `link_rx_only`. It is the dry-run of the guards that a flash would run, so what the band says and
   what a flash does cannot disagree. (The old tool's `--status` already says "whether a flash is
   ready". **[prior art]**)
3. **Only attached things take space.** Empty configured slots collapse into one "N free ports"
   chip. A problem expands itself: a node that is not a character device, a board attached with no
   node, a device nothing recognises, a port held by a process.
4. **An "other devices" group holds everything attached that is not in a rig:** an undeclared
   serial adapter (listed and **never opened**), an unassigned probe, an unrelated device. Nothing
   attached is invisible; unrelated devices are one click away, not on the headline.
5. **A mode change is one transition, not a flicker.** App to handover to bootloader to app is
   drawn as a single row moving through states with the elapsed time, because the gap while the
   device is `absent` is the handover and not a fault (`devices.md`). A real flap is shown as a flap.
6. **A short live ticker beside the band:** plugged, unplugged, mode changed, refused. The full
   record is the Activity page.
7. **Every chip is a link** to the page of its area (the console for a link, the programmer for a
   board).

Rules carried from the requirement and the other specs:

- **Identification is server-side data, not UI code.** A `usb_ids.yaml` maps VID:PID to a class and
  a name, and board profiles extend it, so the CLI, API, MCP and dashboard identify a device
  identically (`devices.md`). Unknown ids fall back to the product string, labelled `unrecognised`,
  never guessed.
- **It updates by event** (`devices.changed` on the feed), never by a timer. A device appearing is
  a chip appearing.
- **The band never hides a refusal.** A board that cannot be flashed says why in its verdict, not by
  being greyed out.
- **Without declared wiring, it degrades to what is known:** one row per detected board with the
  other devices in the "other" group. That is also the shape of the ESP workbench's strip, so the
  baseline is covered while the relations are not yet declared.

## TOOL it tells the truth

The rules from the mesh-gw dashboard, adopted because they answer defects that dashboard already
had. **[prior art]**

- **A check that could not run does not look like one that passed.** Each guard is shown as
  `ran and passed`, `ran and refused`, or `could not run` with the reason. `attached_no_node` is not
  drawn as `absent`. A serial interlock with `node_joined: false` says so.
- **Absent is not broken.** A missing optional dependency, an area that failed to import, or a Pi-only
  panel on an x86 host shows as absent with its reason. A module that exists and raises is
  reported as broken, with the exception. Neither is swallowed.
- **The dashboard's own status states its write routes.** It reports what it can write and checks
  that claim against its source, so a "read only" label cannot outlive a write route.
- **No cached state is shown as live.** Every value carries the age of its last event; a stale
  link is drawn stale.
- **A silent console is not an error.** It shows `0 bytes` and the likely causes
  (`console.md`), never a red banner on the tool.

## TOOL how it stays live: no polling

Peter's rule from the other dashboard: "the ONLY thing allowed to GET is the raw HTML and the nav
menus ... no GET polling for any data whatsoever." **[prior art]** `setInterval` is banned. The
same holds here.

- **One websocket, `/ws/events`, carries everything live.** The client sends a `want` message
  naming the feeds it wants (devices, a link's console, activity, flash progress) and the server
  sends only those. A page that is not open asks for nothing.
- **A read made because a person acted is allowed** (open a chart, press load), as one request each
  time, and the allowed reads are a **closed list** a check enforces.
- **Reconnect is event-driven.** A dropped feed is shown as dropped, with when, and resumes from a
  cursor so no events are lost or duplicated.

## TOOL how it writes

- **The dashboard has no write route of its own.** Every action (flash, send, reopen, set
  `protected`) calls the same command through the same API, so it passes every guard exactly as a
  CLI call does, and a guard refusal looks the same wherever it came from. It can do nothing the
  CLI cannot.
- **Writes go only to addresses the server declared** (the registry's command routes), never to a
  hardcoded URL in the UI. A check scans the UI source for a write URL that is not server-declared
  and for `setInterval`. **[prior art]**
- **Dangerous actions say what they will do before they do it.** A flash shows the device, the
  serial, the package and the guards that will run. A send shows the link and whether its TX is
  wired. Nothing reaches hardware on a single stray click: a flash needs the same arguments the CLI
  requires, including the caller-supplied bound.
- **A browser logs in; the token is never held by page script.** (This **replaces** the first
  draft's "the token is not stored in the page or in browser storage ... the dashboard sends it as
  the same bearer header", which gave a browser on another machine no way in: a page load cannot
  carry a bearer header.) The dashboard has a login page; the token is entered once and the server
  answers with an `HttpOnly`, `SameSite=Strict` session cookie that script cannot read. Every write
  also sends a per-session value in a custom header, and the websocket checks `Origin`, so another
  site cannot drive it (`api.md`, "listening").
- **It is served on the port the user chose.** Peter wants port 80; that is configuration
  (`service.listen`), never a default and never in the UI code. The dashboard makes no assumption
  about its own address: it uses relative URLs, so it works on any port and behind any path.
- **It is honest about exposure.** Beyond loopback without TLS, the Bench page shows
  "unencrypted". In `open-read` mode it shows "read access is open on this network". The login and
  these notices are the only places a token or the access mode is mentioned.

## TOOL retention: every retained stream has a bound

The activity log and each console log are retained. **A stream with no bound is retained for ever
by oversight, which is the failure the other dashboard's config block exists to prevent.** **[prior
art]** So each one declares `retain` (an entry count) and `max_age_days`, both required, with **no
default**. The values are not chosen here: they are derived from the measured event rate of the
thing (bytes a second a console actually emits, commands a day on a real bench) and the history
someone needs, and the derivation is written beside the value. Until measured, the bound is a
`MEASURE` task, and the log refuses to start unbounded.

## TOOL stack

- **React, Vite and Tailwind, as the mesh-gw dashboard**, so the conventions, tooling and checks
  carry over. It lives in `ui/`, outside the Python package.
- **Built off the Pi, shipped as built assets.** The Pi never needs Node. The built files are part
  of the release artefact (the wheel), not committed to git; the brief's principle is to flash
  and deploy what CI built. On a development checkout, the service serves the built assets if
  present and otherwise a minimal page saying plainly that the UI has not been built, with the
  bench name already in the title.
- **Served from the service at `/`**, as upstream and the other dashboard do. A thin Python package
  (`nrf_workbench/dashboard/`) mounts the static assets and the title substitution. The composition
  root (the service's entry point) is the one module allowed to import both `api` and `dashboard`
  and wire them, so neither imports the other.
- **One base font size** and both themes, per the other dashboard's rule. **[prior art]**

## TOOL folded into other specs

- `core.md`: an **event bus** (typed events from every area, each with a monotonic and a wall
  timestamp and a cursor) and an **activity log** built on it (every `Result`, with caller, class
  and code).
- `api.md`: `GET /api/activity`, the `/ws/events` feed with its `want` channel, static serving, and
  the dashboard's status route.
- `architecture.md`: the `dashboard` area and the composition root.

## DECIDE derived (Claude; Peter may overrule)

1. **Generate the Commands page from the registry, hand-draw only the workflow pages.** Constraint:
   the single-source rule; a hand-written form per command would drift the first time a command
   changes.
2. **React, Vite and Tailwind, built off the Pi, shipped in the wheel.** Constraint: Peter already
   runs that stack and its checks; a Pi should not need a Node toolchain. Runner-up: no-build
   ES modules, rejected because it throws away the tooling and checks that already exist.
3. **No polling, a `want`-driven websocket.** Constraint: Peter's recorded rule, and the derive
   rule; a poll is a clock standing in for an event.
4. **No dashboard-only write route.** Constraint: every action must meet the guards; a separate
   write path would be a second place to forget one. (The mesh-gw dashboard keeps one send route
   of its own; here every write already has a command.)
5. **Every retained stream declares its bound, with no default.** Constraint: the other
   dashboard's recorded failure, and the derive rule against a plucked retention.
6. **The bench name is in the tab title on the server.** Constraint: upstream found it matters
   when the API is wedged and two tabs are open.
7. **Release v0.2.** Constraint: it needs the service, and v0.1 is proven from the CLI.
8. **The top of every page is a bench band of rigs with a readiness verdict, not a card per
   port.** Constraint: Peter asked for auto-detected, identified USB devices at the top and said
   a better way than the ESP workbench's may exist; an nRF board is served by several devices and
   the operator's question is whether it can be flashed now, which port cards split and do not
   answer. Runner-up: upstream-style slot cards, which remain the fallback until wiring is
   declared. This is a proposal; Peter may overrule it.
9. **USB identification is server-side data, shared by every front end.** Constraint: upstream's
   table lives in browser JavaScript and already misses this bench's own dongle; a second
   identification table in the CLI would drift from it.
10. **No polling, even though upstream polls.** Constraint: Peter's recorded rule, and the `devices`
    area already emits the events.

## Open question (one, recommendation first)

**Is the 3.5-inch USB display on the Proxmox host meant to show a bench status?** Recommendation:
build the dashboard responsive from a phone width up now, and decide a compact status view for a
320 by 480 screen only if you say that display is for this. I would otherwise be guessing a size.

## VERIFY

- **A real browser, both themes, at the sizes in use** (the `playwright` rule): every page rendered
  and looked at, not merely served. A rendered element is not a verified one.
- **On the port the user chose,** including a privileged one such as 80 (as root): the page, the
  feed and the API answer from one origin, and the UI source has no hardcoded host or port.
- **The login in a real browser:** a wrong token is refused; the right one gives a session; the
  token is nowhere script can read it (not in storage, not in the DOM); a write without the
  per-session header is refused; a foreign `Origin` on the websocket is refused.
- **The exposure notices appear** ("unencrypted", "read access is open") exactly when they apply.
- **The checks go red then green:** a hardcoded write URL, an unlisted GET, a `setInterval`, and a
  page named by a key each turn the UI check red; the control is green. (Mutation-tested, as the
  other dashboard's is.)
- **An absent area is absent:** on the x86_64 host with no Pi, the Pi-only pages are not in the
  navigation, and the Bench page says why.
- **A refusal looks the same** from the dashboard, the CLI and the API.
- **A dropped feed resumes without loss or duplication** from its cursor.
- **The status claim holds:** the dashboard's reported write routes equal the routes in its source.
