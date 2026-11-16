# Architecture — module layout and area boundaries (DRAFT, not agreed)

Peter, 2026-11-16: define the areas and make it modular; no huge files. This spec fixes the shape
before any code. The areas are the ones defined in mcpp-plan project `rakbench`; each owns a
package directory and nothing outside it.

## TOOL the rule that keeps it modular

**One responsibility per module. A module that needs "and" to describe what it does is two modules.**
No size number is set, because a line count would be plucked; the test is the sentence, and a
module whose purpose cannot be stated without "and" is split before it merges. The upstream
`portal.py` (one 258 KB file) is the thing being avoided.

## TOOL package layout

Project and repo name `nrf-workbench` (Peter, 2026-11-16: "much better" than rakbench). Python
package `nrf_workbench`: an import name cannot contain a hyphen, so the underscore form is the
package and `nrf-workbench` is the distribution and repo name. The local directory is still
`rakbench` because the mcpp-plan project is bound to that path. One area, one subpackage, one
test directory.

```
nrf_workbench/
  core/          registry, results and refusal codes, config, host capability probe
  guards/        lock, holder, state, serial interlock, protected, verdict, package check
  devices/       profile loader, identity (sysfs, udev) and node resolution, hotplug and slot state, family driver interface
  console/       debug links (usb adapter, pi uart), broker, byte cursor, reply correlation
                 [the ONLY package that opens a serial device; a gate test enforces it]
  programmer/    serial DFU (adafruit, nordic), mcuboot serial/SMP, uf2     [drivers]
  swd/           probe access, program, erase, read-back, gdb server, rtt, recover  [drivers]
  ble/           scanner, gatt client, peripheral, bonding, nus, ble dfu / ota
  gpio/          pin api, wiring map, backends/, bus controllers, sequence, capture
  instruments/   ppk2, coulomb counter, dtm, sniffer, logic analyser, siggen
  cli/           console front end, generated from the registry
  api/           http + websocket + mcp front ends, generated from the registry
  dashboard/     mounts the built UI at the root, server-side title substitution, status route
  service.py     the composition root: the ONE module that imports api and dashboard and wires them
profiles/        device profiles and usb_ids.yaml, YAML data (no code)
ui/              the dashboard front end (React, Vite, Tailwind), built off the host, shipped built
install/ udev/ packaging/    host install, udev rules, service unit
harness/ tests/              fakes, pytest driver, one tests/<area>/ per package
testpartner/     DUT-side nRF test firmware (separate build, not part of the package)
docs/ skills/    specs and method docs
```

Inside an area, split by capability, not by layer. Example: `programmer/` holds one module per
bootloader type (`adafruit_serial.py`, `nordic_serial.py`, `mcuboot_serial.py`, `uf2.py`), each
registering itself as a driver; it does not hold a `utils.py`.

## TOOL dependency rule

Dependencies run one way, as declared on the areas in mcpp-plan:

```
core  <-  guards, devices, gpio, instruments, cli, api, install, harness
devices, guards  <-  programmer, swd
devices  <-  ble, gpio
core, devices  <-  console
```

- An area imports `core` and the areas it declares, nothing else. `programmer` never imports `ble`.
- **`dashboard` imports `core` and nothing else of ours; `api` and `dashboard` never import each
  other.** `service.py` (the composition root) is the one module allowed to import both and mount
  the dashboard on the API's application.
- **`cli` and `api` import only `core`.** They discover commands through the registry. This is what
  makes the CLI and the API the same commands (see the command surface in `programmer.md`), and it
  means a new area appears in both with no change to either.
- An area registers its commands with `core` through one `register(registry, host)` function.
  `core` calls it only if the host capability probe says the area can run here. A Pi-only area
  (`gpio` Pi backend) is absent on an x86 host, not failing.
- **Enforced, not hoped for:** a gate test reads the import graph and fails on any import outside
  the declared dependencies. Per the area types it must be seen to go RED once before it is trusted.

## TOOL what each area owns and proves

| area | type | proved by |
|---|---|---|
| core | library | host tests |
| guards | library | host tests against fake `/dev` and fake `/proc` (no hardware) |
| devices | library | host tests; profile files validated against a schema |
| console | library | host tests over pseudo-terminal pairs (no hardware), then the existing debug dongle on the Proxmox host |
| programmer | library | host tests with a fake bootloader, then the hardware acceptance run |
| swd | library | host tests with a fake probe, then hardware |
| ble | library | host tests with a fake radio, then hardware on the dongle |
| gpio | library | host tests with a fake backend, then wired loopback on a Pi |
| instruments | library | host tests with fakes, then hardware |
| cli | tool | run from the console, `--json` equals the API body |
| dashboard | service | up and answering; rendered and looked at in a real browser, both themes; UI source scanned for polling and hardcoded writes (`dashboard.md`) |
| api | service | up and answering; same commands as the CLI |
| install | tool | run on one arm64 and one x86_64 host |
| harness | library | used by every area's tests |
| testpartner | firmware | proved on hardware |
| skills_docs | doc | matches the code it cites |

## DECIDE derived

1. **`guards` is its own area, not inside `programmer`.** Constraint: SWD also flashes and must
   obey the same lock, holder and serial rules; guards inside `programmer` would be duplicated in
   `swd`, which is the drift the single-source rule exists to stop.
2. **Profiles are YAML data outside the package.** Constraint: board facts (id sets per mode,
   entry method, memory map, `protected`) must change without a code change (brief section 3).
3. **`testpartner` is outside the Python package.** Constraint: it is firmware with its own
   toolchain and is proved on hardware, not by host tests.
4. **Build order follows the dependency graph:** core, then guards, devices and console, then
   programmer. Console is in the first slice because the programmer's acceptance run reads the
   device's boot counter through it (`console.md`).
   The other areas do not start until the programmer is proven (Peter, 2026-11-16: first slice is
   the programmer).

## VERIFY

- The import-graph gate fails when a forbidden import is added, and passes when it is removed.
- A new command added to one area appears in the CLI help, the API and the MCP with no edit to
  `cli/` or `api/`.
- On an x86_64 host with no Pi hardware, the Pi-only commands are absent from all three.
