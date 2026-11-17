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

**Built and checked in a real browser, 2026-11-16: the UI** (`ui/`, built into `nrf_workbench/dashboard/static/`,
committed). `python3 scripts/verify_dashboard.py` runs the real service against a fake sysfs and a pokeable uevent
source and drives Chrome through login (wrong token, right token), the live band, a board attached and removed
while the page is open, all four pages, a generated read and a refused write from the Commands page, the
activity log naming the dashboard, logout, and a foreign-Origin login, in light and dark at 390, 768 and 1280
wide: **225 checks, 0 failed**, screenshots looked at. A live attach showed in 4 to 26 ms. The checks found and
this commit fixes: a `/favicon.ico` 404 on every page load, a duplicated command summary, "took 0.000 s", a
wrapped phone nav. **Not yet checked on the Pi with a real board** (the verification uses a fake bus), and a flash
from the browser has not been run. **That first flash form was unusable (a generated form of raw fields): see
"The flash flow", which replaces it.**
The development container's fontconfig maps `system-ui` to a monospace face, so named sans fonts come first.

## The flash flow (added 2026-11-16, after Peter tried it: "a modal that has lots of fields to manually complete, completely unusable")

**The defect.** The first UI opened the generated command form for `programmer.flash`: eight raw fields (serial,
a firmware id typed by hand, two bounds, a product string). A person flashing a board knows three things: which
board, which package, and whether they are sure. Nothing else should be asked. The Commands page keeps the
generated form (it is the escape hatch); **Flash on a board is a purpose-built flow** in `ui/src/FlashDialog.jsx`:

1. **The board is fixed** (serial, product, mode, profile shown, not editable). A board already in its bootloader
   says so; the flash goes on regardless.
2. **The package is chosen, never typed**: a list of what the store holds (name, size, short id, when uploaded)
   from `firmware.list`, **with nothing preselected** (a stray click must not flash the newest image), or **a file
   from the person's computer**, sent with `programmer.flash_stream` (one call, stored and flashed). With no store
   the upload is the only choice; with no flash command the dialog says why.
3. **The product check is one optional line**: "the board must come back as" the string the new image announces.
   Empty means no check. It is **not prefilled from the board's current product**: that is the OLD image's string.
4. **The two bounds are derived, shown, and hidden under Advanced.** The service requires them (no default, by
   design); the page computes them from the package, writes the arithmetic beside them, and lets a person change
   them:
   `flash_bound_s = 2 x image_bytes / 12 350 B/s` (slowest measured throughput, 12.35 kB/s; 2x: one missed
   packet-ack burst or a slow USB host; for the 332 092-byte image 53.8 s, 2.5x the 21.554 s measured, inside the
   2 to 3x the programmer spec asks for once a baseline exists);
   `enter_bound_s = 2 s` (worst measured handover 0.589 s x 3 = 1.77 s, rounded up).
   An uploaded zip's own size stands in for `image_bytes` (it is larger by the manifest, 0.24% on the real
   image), so the bound is a hair long, never short.
5. **It says what it will do before it does it**: "Erases <serial> (<product>) and writes <package> (<bytes>).
   The board enters its bootloader by itself; no button. Expect about <lo> to <hi> s (12.35 to 15.42 kB/s
   measured)." The button reads "Erase and flash". There is **no timer on the page**, so there is no ticking
   progress: it shows when the flash started and the expected range, **the band updates live as the board
   re-enumerates**, and the dialog cannot be closed while the request is open. (A progress feed is not built.)
6. **The result is the whole Result** (class, code, message, every byte of data and evidence), with a one-line
   verdict first: duration against the expected range, packets, whether the bootloader was entered, and what the
   board came back as.

Files: `ui/src/FlashDialog.jsx`, `ui/src/format.js` (the constants, each with its derivation), `ui/src/App.jsx`,
`ui/src/Commands.jsx` (the result view is shared), `scripts/verify_dashboard.py`, `tests/dashboard/test_ui_rules.py`,
and the rebuilt `nrf_workbench/dashboard/static/`. The browser check flashes a fake board through the dialog on the
fake bench (the service's real routes with the programmer tests' fake runner and touch): choose from the store,
upload a file, refuse with nothing chosen, a refused flash shown with its code. **No flash on the real board
without Peter.**

**Built and checked, 2026-11-16.** The browser check now flashes the fake board through the dialog three ways (an
uploaded file, a stored package, a refusal shown by code), and in every viewport opens the dialog and checks: the
board is named, nothing is preselected, the button is disabled until a package is chosen, no raw serial or firmware
field is offered, the product check starts empty, no horizontal scroll: **269 checks, 0 failed**. 9 mutants of the
dialog (preselect the newest, prefill the old product, wrong bound factor, wrong enter bound, no verdict, a literal
write path, an upload with no body, a hidden result) were caught by the unit checks, the browser check or both; the one
survivor (the button also stays disabled while the derived bound is empty) is equivalent. The check found and fixed:
"Expect about 0 to 0 s" on a small package (now "under a second"). **Not exercised: a flash of the real board from the
browser, and the dialog being unclosable while a request is open** (the fake flash answers at once, and there is no
timer to hold it); both need a real flash, which waits for Peter. The service's own refusal text for `flash_suspect`
reads "...was written The board was sent..." with a missing full stop: that string is the programmer's, not the UI's,
and is reported to its owner here, not changed.

### The Firmware page (added 2026-11-17, Peter: "this needs a project browser tab")

Peter, 2026-11-17: "I can ONLY select a firmware from the flash modal, I cannot browse by project, I cannot add a
new project, I cannot upload or delete a file / folder etc etc etc." The store has folders (`firmware.md`,
"projects"); this page is where they are looked after. The flash dialog stays, for flashing; it is no longer the
only way to see the store.

- **Two panes, stacked on a phone.** Left: the projects, each with its package count, and a "New project" box. Right:
  the chosen project. **Nothing is chosen for you**: with no project chosen the right pane says to choose one.
- **A project's pane:** its packages, newest first; an **Upload** box (a file, and an optional name; the file's
  name is used if none is given) that stores into this project; and **Delete project**, enabled only when the
  project is empty, with the count and the reason shown when it is not (the service refuses a non-empty one as
  well, `project_not_empty`).
- **A package row** shows its name, short id (the full id in its tooltip), image size, packets, the SoftDevice it
  needs, when it was uploaded and by whom, and three things to do: **Flash…**, **Move to** (a menu of the other
  projects; a move is safe and reversible, so it asks nothing) and **Delete…**.
- **Delete asks first, in a confirm dialog that names the package and its project and says it cannot be undone**
  (the old image may be the only way back to a known state: `firmware.md`). The safe button, Cancel, has the focus.
  A package a unit names as its way back is refused by the service (`in_use`) and the refusal is shown.
  Deleting a project asks too.
- **Flash… is the same flash dialog** opened with this package already chosen **by the person, who pressed it on this
  package**: the board is named in the dialog and the erase still needs its own button. With no board attached the
  buttons are disabled and say why; with several, a board is chosen first and none is chosen for you.
- **Every write shows its whole Result** at the top of the page (class, code, message, data, evidence), as on the
  Commands page, and the list refreshes without being swapped for "Loading". The page adds no rule of its own: every
  refusal is the service's.
- **Where Radix is used, and where it is not.** Two Radix primitives, pinned exactly, **only on this page**: the
  **alert dialog** for the delete confirm (it traps focus, names itself to a screen reader as an alert, and needs a
  deliberate answer) and the **dropdown menu** for the row's Move/Delete actions (menus are the hard part to get
  right by hand). The native `<dialog>`, `<select>` and file input stay everywhere else: they work, they are
  accessible, and the native select is better on a phone. Radix is unstyled, so our Tailwind classes and the rule
  that every light colour has a dark partner are unchanged. **Measured cost: +30 kB gzip** (80 to 110 kB) for the two
  packages, which Radix's own code and its helpers account for. If Radix stalls as a dependency, Base UI is the way
  out: the use here is thin.

**Built and checked, 2026-11-17.** In the real browser on the fake bench (light and dark, three widths, the page also
in the navigation checks): projects with counts and nothing chosen for you; a project's packages; a bad project name
refused before anything is sent, a good one made and opened; an upload into the chosen project under the name given;
**uploading bytes the store already holds says which project has them and copies nothing**; Flash opens the dialog on
the package pressed, with the erase still behind its own button; with two boards none is chosen and Flash waits; Move
lists the other projects only; Delete asks first (Cancel and Escape delete nothing, the confirm does, and the focus
starts on Cancel); a non-empty project cannot be deleted and says why, an empty one can, after asking: **343 checks,
0 failed**. Six mutants were caught in the browser (Cancel deletes, delete a non-empty project, Flash with no board, a
board picked for the person, delete without the confirm, an upload into the wrong project) and the delete-without-
confirm one by a unit rule as well. **A defect the screenshots found:** the "More ▾" glyph rendered as an empty box on a
machine without the font, so it is now a drawn chevron. **Not exercised: a flash of the real board from this page.**

### The in-progress indicator (added 2026-11-17, Peter: "an activity is actually in progress")

A flash takes about 20 seconds and the board is erased from the first second, so the dashboard must say so to
everyone looking at it, whoever started the flash (the CLI, the MCP, another session, this page).

- **A bar at the top of every page, above the bench band, whenever a write command has started and not finished.**
  It names what is happening ("Flashing D953162EF5806B4B", "Uploading a package"), who started it (`caller`) and
  when it started, and carries a spinner. It is one row per running command, so two at once are both shown. It is a
  `role="status"` region, so a screen reader hears it appear and clear.
- **It comes from the live feed, not from the button that was pressed.** The activity feed carries
  `command.started` and the matching `command.result` (`core.md`). The page keeps the started commands that have
  no result yet; a page opened in the middle of a flash replays the retained events and shows it at once. A new
  feed epoch (a service restart) clears it: nothing is running across a restart.
- **The tab title says it too** ("Working · <bench>"), because the person watching is often in another tab.
- **The spinner is a CSS animation, switched off for a visitor who asks for reduced motion** (a static marker
  stays). **There is no elapsed-time counter:** the page has no timers (see "no polling"), so a ticking clock is not
  available, and the start time is the honest figure. A counter that stopped when the page lost its feed would be
  worse than none.
- **The flash dialog shows a spinner too** while its own request is open, in addition to the bar.
- **A command that never ends stays shown.** The bar does not time out: a flash that has hung is the thing the
  person most needs to see, and the service's own bound is what ends it.

**Built and checked, 2026-11-17.** The service announces the start of every write command (`core.md`; 10 mutants
caught). In the real browser, on the fake bench with the uploader held so the flash stays "running": a flash started
by another caller (a bearer token, as the CLI or MCP would) shows in the bar with its serial, who and when, a
spinner and the tab title; a second page opened mid-flash shows it at once; a visitor who asks for reduced motion gets
a still marker; the bar and title clear when the flash ends; and a flash started from the dialog spins in the dialog,
shows in the bar as the dashboard's, and cannot be closed while it runs: **303 checks, 0 failed**, three runs in a
row. Four page-side mutants were caught (ignore the start, never clear, never change the title, ignore reduced
motion). **Not exercised: a real flash of the real board**, which takes about 20 seconds and needs Peter.

### The store in the dialog: project folders (added 2026-11-17, Peter: "the ability to browse and add a new folder")

The store is a folder per project (`firmware.md`, "projects"), so the dialog browses it the same way.

- **From the store is two choices, in order: a project folder, then a package in it.** Nothing is preselected,
  neither the folder nor the package. Each folder shows how many packages it holds. A package chosen in one folder
  is not carried into another: the choice is read as "the package, in the folder being browsed", so changing folder
  needs no reset to write (a reset would be code that selects).
- **"New folder" is in the dialog,** a text box and a button, shown only when the service lists
  `firmware.project_new`. The name is checked against the service's rule first (the service is the authority and
  checks again). A made folder opens, empty, and says so. The empty store also offers it.
- **An upload names its project:** a selector of the folders (`unsorted` first), or a new name typed beside it,
  which wins. A bad name is said, with the rule, and keeps the button disabled; nothing is sent for it.
- **A listing that cannot be fetched shows why** (the request's own failure text, or the HTTP status and the
  service's message) **and offers "Try again"**, which asks again and recovers without closing the dialog. A
  refresh after a folder was made or an upload keeps the list on screen: it is not swapped for "Loading".
- **Pressing Enter in any text box never starts a flash.** A form submits on Enter, and in this dialog the submit is
  an erase. The form ignores Enter in a box, and the shared `Button` is `type="button"` unless a caller says
  `submit`, because the browser's default inside a form is submit. **[found while building this: a button added to
  the form without a type would have started a flash when pressed. Checked in the browser with a package chosen and
  the button enabled, so a submit would have flashed.]**

**Built and checked, 2026-11-17.** The browser check covers all of it, in the real browser on the fake bench:
**288 checks, 0 failed**, three runs in a row. Three mutants were caught: the old `Button` default (pressing "Create
folder" with a package chosen started a flash), no Enter guard (Enter in the product box started a flash), and the
choice carried across folders.

## Files for the UI (added 2026-11-16, Peter: "get this completed")

`ui/` (React 19, Vite 8, Tailwind 4, **exact versions pinned**, `package-lock.json` committed), the **built
output committed** at `nrf_workbench/dashboard/static/` (**overriding this spec's earlier "not committed to
git"**: the installer fetches a GitHub tarball of a commit, so assets that are not in the commit never reach
the Pi; they are text and rebuilt from `ui/`), `tests/dashboard/test_ui_rules.py` (the UI source checks, each
mutation-tested), `scripts/build_ui.py` (runs the build, then writes `nrf_workbench/dashboard/static/source.sha256`,
the digest of every file the build reads, and the test recomputes it: **a committed build that is older than its
source fails a test** instead of shipping a dashboard that is not the one in `ui/`), `scripts/verify_dashboard.py` (the real-browser check: both themes, three widths, the login),
and `.gitignore` (`ui/node_modules/`), and `tests/service/test_service.py` (its "UI is not built" banner test supplied no static
directory of its own and so depended on whether the repo had a build; it now supplies an empty one).
**Design rules the checks enforce:** no `setInterval` and no
`setTimeout` anywhere (so there are no ticking "ages": every value shows the TIME of its last event, which is
the same information with no clock), no literal host, port or URL, and **no write URL in the source except
`login` and `logout`**: every other write goes to a path the server listed in `GET /api/commands`. A dropped
feed is shown as dropped, with when, and reconnects on the browser's own `online` and `visibilitychange`
events or a button, never on a timer. **What the band does and does not claim:** it shows each board's state
and mode from the live feed; it does **not** claim "ready", because the readiness verdict is a dry run of the
flash guards that no command exposes yet. The Commands page is generated from the registry, so every present
command, including both flashes, can be run from the browser with its guards.

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
| **Firmware** | the package store as folders: the projects with how many packages each holds, the packages of the chosen project, and every store command from the page: new and delete project, upload, move, delete, and flash a package to an attached board | `firmware.*`, `programmer` |
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
