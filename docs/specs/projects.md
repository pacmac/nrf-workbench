# Projects: a named bench setup an agent can show, plan, apply, check and release (DRAFT, not agreed)

Peter, 2026-11-18: "I am now considering whether we should include projects, projects can have assets / resources and uart / i2c / gpio
etc can be associated with a project so that by calling the project those assets / resources are all configured, and there may also be more
benefits to this especially when being used by an agent." Then: "yes write the spec." Tags: **[stated]** Peter said it; **[derived]**
worked out from a constraint, Peter may overrule; **[unknown]** nobody has measured it. **No code is written under this spec.**

Area `project` (new). It composes what exists (`wiring`, `gpio`, `esp32`, `console` macros, `firmware`, `i2c`, `devices`) and adds no
new way to touch hardware.

## The idea in one paragraph

Today the bench is a pile of global facts: connections, pin assignments, ESP32 boards, macros, packages. Which of them belong to the work
in hand lives in somebody's head, so an agent starts every session by guessing. A **project** is a named, written-down setup: *these roles
are these boards, wired like this, with these pins, these macros and this firmware, and this is how you know it is working.* One call
(`project.show`) tells an agent all of that, and one call (`project.apply`) makes the bench match it.

## GAP defects first

1. **"Project" already means something here.** The Firmware page's projects are folders of packages (`firmware.project_new`, the `unsorted`
   default). Two meanings would be a defect. **Decision [derived]: they are the same thing.** A project is identified by its name; its
   firmware folder is the part of it that holds packages, and a folder with no manifest is a project that holds firmware only (an *implicit*
   project). Nothing in the firmware store changes: the manifest is **not** stored in the folder (see "Where it lives"), because the store
   lists every `*.json` in a folder as a package record and refuses to remove a folder holding files it does not know.
2. **There is no cross-store transaction.** Connections, pins, ESP32 targets and macros are four files. A crash between two writes leaves a
   half-applied project. **Decision [derived]:** `apply` first checks everything (the plan), then writes in dependency order, undoes what it
   did in this call if a later step fails, and writes the project's state file last. A crash can still leave a part; every object a project
   creates is **tagged with the project**, so `project.check` shows the drift and `project.release` removes exactly those objects.
3. **Every resource is named in one global namespace.** A connection name is a console link name, a pin assignment is a slug, an ESP32 target
   a slug, a macro a slug. Two projects (or a project and a hand-made object) can want the same name. **Decision [derived]:** an object a
   project creates is tagged `project: <name>` in its own record; a name already used by an object the project does not own is
   `project_conflict`, never an overwrite. Macros are namespaced instead (`<project>.<macro>`), because the macro list is a flat list the
   console page shows.
4. **There is no per-caller identity.** `caller` is a label (`open`, `local`, `dashboard`, `token`); there is one token. So **agent scoping** (an
   agent may only touch its project's boards and pins) cannot be enforced today. **Decision:** scoping is deferred to a later slice that
   needs per-agent tokens (a separate decision); slice 1 gives the agent the *list* of what is in scope, which a well-behaved client honours.
   This spec does not claim enforcement.
5. **A project cannot change the Pi's boot configuration.** I2C and the UART need a boot-file edit and a reboot, which the bench never does
   by itself. A project **declares** them (`requires`) and `check` reports what is missing, with the steps; it never applies them.
6. **A manifest and the live bench can disagree** (someone edits a connection on the Wiring page). **Decision [derived]:** the live bench
   wins and is never blocked; the difference is reported (`project.check`, and Home), not fought.
7. **Flashing is destructive and slow**, and the bounds are derived per image. `apply` therefore **never flashes**. `project.flash` is its
   own call, one role at a time, with the bounds the underlying commands already require.

## Built differently from the first draft (2026-11-18, slices 1 and 2)

- **Ownership is kept in `state.json`, not as a tag in each store's records.** An apply writes `pending: true` and the names it is about to
  create (an intent log) before it creates anything, and clears `pending` last. The plan only lists as `create` what did not exist, so a
  release that follows a crash can only remove what the project made. This leaves the pin map, connections, targets and macros untouched, and
  is why no store schema changed. Macros are still namespaced `<project>.<name>`.
- **`project.probe` is its own command** (write). `project.check` stays a read that sends nothing, so it can run on every Home refresh;
  the `expect.i2c` probe is `project.probe`, through `i2c.transfer`, so its pin-claim gate applies.
- **An unseen `expect.console` line is reported (`not_seen`) and is not held against the project**: the log is in memory and empty after any
  restart, so counting it would make every project "not ready" after one. `ready` means: applied, nothing missing or changed, every
  requirement met, every role's board attached.
- A manifest's `wiring` item that uses a USB adapter to send needs `voltage_measured` stated (as `wiring.connect` does); the Pi's UART does
  not (3.3 V by design).
- **Tests:** 71 (manifest 20, behaviour 51), 33 mutants of the plan, apply, release, check and the commands, all caught after five tests
  were added for the survivors.

## Where it lives

- `<paths.wiring>/projects/<name>/project.yaml`: the manifest (YAML, the project's own file; hand-editable). The folder is created with
  the same atomic, 0700 care as the other stores. A host with no wiring folder has no manifests (the section is absent, not failing).
- `<paths.wiring>/projects/state.json`: what is applied: `{"<name>": {applied_at, applied_by, owns: {connections, pins, targets, macros},
  uses: {...}}}`, written atomically last.
- Firmware stays where it is, `<paths.firmware>/<name>/`.
- The name is the project name rule (1 to 64 of `a-z 0-9 . _ -`, starting with a letter or a digit), shared with firmware folders.

## The manifest

```yaml
name: timercam-i2c                      # must equal the folder name
purpose: develop the TimerCAM I2C-slave transport      # one line, shown on Home and in project.show
requires: [i2c, pi-uart, esp32]         # host capabilities and setup this project needs: checked, never changed

devices:                                # roles: a project-local name for a physical board
  camera: {esp32: timercam}             # an ESP32 target, by its declared name
  alarm:  {nrf: D953162EF5806B4B}       # an nRF board, by its USB serial

esp32:                                  # targets to create if they are not already declared
  - {name: timercam, adapter: "5D52ADF916", baud: 115200}

wiring:                                 # debug-log connections (the only intent built so far)
  - {device: alarm, adapter: "uart:ttyAMA0", baud: 115200, send: false, name: alarm-log}

pins:                                   # Pi header assignments, as gpio.assign takes them
  - {gpio: 2, name: i2c-sda, service: i2c, action: sda, direction: input, pull: up, purpose: "Pi I2C bus 1", voltage_v: 3.3, active_low: false, voltage_verified: false}
  - {gpio: 3, name: i2c-scl, service: i2c, action: scl, direction: input, pull: up, purpose: "Pi I2C bus 1", voltage_v: 3.3, active_low: false, voltage_verified: false}

macros:                                 # created as <project>.<name>
  ping: ["ping"]

firmware:                               # which stored package each role should be running
  camera: {id: 761f7205}
  alarm:  {id: b6bdfbb3}

expect:                                 # how to tell it is working
  camera: {console: "i2c slave 0x62"}   # a line that means it booted, found in the role's console log
  i2c:    {bus: 1, address: 0x62}       # an address that must answer a probe (check only; never writes)

notes: |                                # free text for a person or an agent; shown by project.show
  The camera sleeps with its I2C slave ended and wakes on SCL low; probe twice.
```

Every section is optional. **Every field a command needs is stated; none is defaulted** (the same rule as `gpio.assign`): a pin missing a
field is `bad_manifest`, not a guess. Unknown keys are `bad_manifest` (a typo is not silently ignored). A role that is used in `wiring`,
`firmware` or `expect` must be in `devices`. `firmware.<role>.id` is a stored package id (8 or more hex characters) and must be of the
right kind for the role (a DFU package for an nRF role, an ESP package for an ESP32 role).

## Ownership: what apply may touch and release may remove

A record a project creates carries `project: <name>` (connections, pins, targets) or the `<project>.` prefix (macros). **Release removes
only what the project owns.**

- **Absent:** apply creates it and the project owns it.
- **Present and identical** (same adapter, baud and device; same pin fields): apply records it under `uses` and does **not** take ownership.
  Release leaves it. (Someone else made it; the project merely depends on it.)
- **Present and different, or owned by another project:** `project_conflict`, naming the object, who owns it and the difference. Nothing is
  changed.
- Device names (`devices.name`) are labels of physical boards and are **not** project-owned; a role is the project's own name for a board.

## Commands

| command | effect | what it does |
|---|---|---|
| `project.list` | read | every project: name, purpose, whether it has a manifest, a firmware folder, whether it is applied, and `drift` (a count) |
| `project.show(project)` | read | the **agent's orientation call** (see below) |
| `project.put(project)` | write, raw body | store a manifest (the YAML is the body); validated before it is kept; replaces the file atomically |
| `project.delete(project)` | write | remove the manifest; refused while the project is applied (`project_applied`); never touches the firmware folder |
| `project.plan(project)` | read | the diff apply would make: per section, `create`, `use`, `conflict` or `unresolved`, with the reason. Opens and writes nothing |
| `project.apply(project)` | write | does the plan, or refuses with the first conflict. **Never flashes, resets or sends.** |
| `project.check(project)` | read | does reality match? requires met, roles attached, owned objects present and unchanged, `expect` seen. Waits for nothing |
| `project.release(project)` | write | removes what the project owns, in reverse order; leaves `uses` and the firmware |
| `project.flash(project, role, ...)` | write | flashes the role's declared firmware to its board by the existing `programmer.flash` or `esp32.flash`, with the same required bounds |

`project.list` and `project.show` need nothing but the wiring folder; the others add the capabilities of the sections they touch (a
manifest with `pins` needs `gpio`, and so on), and `plan` says which part cannot run on this host instead of failing.

### project.show: the orientation call

One answer, built from the manifest and the live bench, so an agent need not call six commands to learn the setup:

```
{name, purpose, notes, applied, requires: [{item, met, how}],
 roles: [{role, kind, board: {name, serial, state: connected|absent|attention}, console: {link, state, rx_only, baud},
          firmware: {id, name, in_store}, running: {mode}}],
 resources: {connections, pins, i2c: {bus, pins_claimed}, macros},
 expect: [...], scope: {boards: [...], pins: [...], links: [...]},      # what is in this project; a client should stay inside it
 drift: [...]}
```
`scope` is advice, not enforcement (GAP 4). `drift` lists live differences from the manifest.

### Plan and apply, in order

1. **Resolve roles.** `esp32:` roles must be (or become, if the manifest has an `esp32:` entry) a declared target; an nRF role is by serial
   and may be absent (a connection to an absent board is allowed; an absent **adapter** is `adapter_absent`, as `wiring.connect` says).
2. **Check requires.** I2C needs `/dev/i2c-N`; `pi-uart` needs `wiring.uart_status` ready; `esp32` needs esptool. An unmet `requires` makes
   `plan` say so and `apply` refuse with `capability_missing` and the steps. It changes nothing.
3. **Compare** each resource with the live store: create / use / conflict (above).
4. **Apply in dependency order:** ESP32 targets, then connections, then pins, then macros. Each uses the existing command, so every
   guard it has still runs (a pin above 3.3 V is still `voltage_unsafe`; a UART's pins still conflict with a pin-map claim).
5. **On a failure**, undo what this call created, in reverse, and report what was and was not done. Then **state.json is written last** on
   success only.
6. **Idempotent:** applying an applied project with no change is a plan of only `use` and reports `nothing to do`.

### Check and `expect`

`expect.<role>.console` is a string looked for in the role's console log **as retained**; `check` reads it and does not wait. `expect.i2c`
sends one probe (a one-byte read; never a write) to the address on the named bus, only on a project that has applied the pins; a sleeping
device may miss the first probe, so the result says *no answer* and `check` reports it, not an error. A check with no console line seen is
`not seen in the lines held` (the log is in memory and a restart empties it), not `failed`.

### Flash by role

`project.flash(project, role, enter_bound_s?, flash_bound_s, baud?)` resolves the role's board and package from the manifest and calls
`programmer.flash` (nRF) or `esp32.flash` (ESP32) with **the arguments those commands require**: the caller derives the bounds, as everywhere.
One role per call: the bounds are per image. The result is the underlying command's Result, whole, with `project` and `role` added to its
data.

## Agents

What a project buys an agent, in order of cost to build:

1. **Orientation in one call** (`show`) and **a way to confirm it** (`check`), replacing guesswork at the start of a session.
2. **A reproducible bench.** The manifest can live in the firmware repo and be uploaded with `project.put`; the repo then says what bench it
   needs, and an agent calls `apply`.
3. **No silent collisions.** Two projects cannot take the same pin, adapter or name; the refusal names the owner.
4. **A cleanup it can trust.** `release` removes exactly what the project made.
5. **Later, and not promised here:** scoping by project (GAP 4), and tagging every activity row with the project it ran for (the caller would
   pass `X-Nrfwb-Project`; the activity record would store it). Both need an identity decision first.

The generated `/api/guide` and the MCP tools gain these commands with no hand edit (they come from the registry). The guide's "A first
session" gains one step: *if the work has a project, `project.show` first*.

## Dashboard

- **Home** gains a section, **Projects**, listing active ones: name, purpose, one line of state (`ready`, `drifted: 2`, `a board is not
  attached`), each a link. It is part of `bench.overview` (`projects`), not a new fetch.
- A **Projects** page: the list; a project opens to its roles, resources and `expect`, with **Plan**, **Apply**, **Check** and **Release**
  as buttons that show the whole Result (a plan is shown as a diff before Apply is offered). A manifest is edited as text with the service's
  own validation message beside it; the folder's firmware is the existing Firmware page, linked.
- **Help** gains a topic, *Projects*, in the same short structure. No explanatory prose is added to a working page.

## Result codes added

`bad_manifest` (refused, 422: a manifest that does not validate; the message names the field), `project_conflict` (refused, 409: the named
object is used by something the project does not own), `project_applied` (refused, 409: a delete or apply that needs it released first),
`project_not_applied` (refused, 409: `release` or `flash` of a project that has not been applied). `project_unknown` (404) already exists and
is reused. The table in `core.md` gains them.

## Slices

1. **Read-only and safe:** the manifest, `list`, `show`, `put`, `delete`, `plan`, `check`; the implicit projects from firmware folders;
   `bench.overview` and Home show them. No bench state changes beyond the manifest file.
2. **Apply and release:** ownership tags in the four stores, conflicts, rollback, the state file; `project.flash`.
3. **Dashboard and Help:** the Projects page and topic.
4. **Deferred, needs its own decision:** per-agent tokens and scoping; activity tagging by project; wiring intents other than the debug log
   (SWD, control lines) as manifest sections when they are built; a `bench` section of Pi boot requirements applied by a person-run helper.

## Open questions (one, recommendation first)

**May several projects be applied at once?** Recommendation: **yes, when they claim different resources**, and a clash is refused naming the
owner. The bench has several boards, so a one-at-a-time rule would block work for no reason; the ownership tags already make the check cheap.
The cost is a little more state. If Peter prefers one active project, the same checks apply with an extra "another project is applied"
refusal, and nothing else in this spec changes.

## Files this spec allows

This spec, `docs/specs/projects.md`, and, when code follows Peter's agreement: `nrf_workbench/project/{__init__,manifest,plan,apply,
commands}.py`; the ownership field in `wiring/store.py`, `gpio/pinmap.py`, `esp32/targets.py` and `console/macros.py`; `bench/overview.py`;
`service.py` (registration); `docs/specs/{core,api,dashboard-redesign,wiring,gpio,esp32}.md`; `tests/project/`; and the UI files for slice 3.
