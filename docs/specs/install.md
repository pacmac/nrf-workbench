# Install — functional spec (DRAFT, not agreed)

The installer. Peter, 2026-11-16: "is an install / deploy script needed, because you can't expect
someone to manually install dependencies." Yes. Inputs: the probe results on the Raspberry Pi 4B,
the Proxmox host and the dev container (`probes.md`), the stack checks in `api.md`, and Peter's
rule that `/usr/share/pac` is the working and install folder. Tags: **[stated]** Peter said it;
**[verified]** measured this session on the named host; **[recalled]** from memory;
**[unknown]** nobody has looked.

Area: `install` (tool). Files: `install/` (the installer and its feature manifest), outside the
`nrf_workbench` package.

---

## GAP defects first

1. **There is no installer.** Today every dependency is installed by hand: `pio`, `adafruit-nrfutil`,
   the service stack. v0.1 cannot even be run on the Pi without it, which is why this area moves
   from v0.2 into v0.1 (the service unit itself stays v0.2).
2. **`core.md` puts the host configuration in `/etc/nrf-workbench/`, against Peter's rule** that
   `/usr/share/pac` is the working and install folder. The default becomes `<prefix>/config/`
   (see the edit in `core.md`).
3. **`adafruit-nrfutil` has no binary wheel on PyPI** **[verified: `pip download --only-binary`
   found none for aarch64]**. It installs from source. It is pure Python with pure-Python
   dependencies (`pyserial`, `click`, `ecdsa`, no native extension) **[verified: package metadata
   on the dev host]**, so no compiler is needed, but pip must fetch build tools, and an offline bench
   needs a pre-built wheel.
4. **A PlatformIO build downloads a toolchain on first use**, of a size and on an architecture
   nobody has measured here **[unknown]**. PlatformIO itself is a plain wheel and resolves for
   aarch64 **[verified]**; flashing a prebuilt DFU zip does not need it. So it is an optional
   feature, never a default.
5. **The Pi has no system `pip`** (`No module named pip`) **[verified]**, but `venv` works and
   `ensurepip` is present. The installer uses a venv and never installs `python3-pip` or runs `pip`
   against the system Python.
6. **The bench is USB only.** Peter, 2026-11-16: nothing is wired to the board. So the installer
   cannot verify hardware beyond what USB shows, and it must not claim a debug link works because
   the dongle is plugged in.

---

## TOOL what it is

One command, run from a checkout, that makes a host ready to run nrf-workbench and **proves it**:

```
install/install.sh                      # install the default features, then verify
install/install.sh --check              # plan and preflight only; changes nothing
install/install.sh --features flash,console,build
install/install.sh --uninstall
```

`install.sh` is a short shell wrapper that finds a Python of at least 3.10 and hands over to
`install/install.py`, which is stdlib-only so it runs before anything is installed. The logic is in
Python so it can be tested; the shell only gets Python started.

## TOOL features, not one big blob

What a host needs depends on what it will do. Each area declares its requirements in the feature
manifest (`install/features.yaml`); the installer reads that, so adding an area never means editing
the installer.

| feature | installs | needed for |
|---|---|---|
| `core` | a virtual environment and the pinned service stack: `mcp` 2.3.0, `uvicorn` 0.54.0, `websockets` 17.4, `starlette` 1.10.0, `PyYAML` | everything |
| `flash` | `adafruit-nrfutil` (built from source) with `pyserial`, `click`, `ecdsa` | writing a prebuilt DFU zip |
| `console` | `pyserial` (or the standard library alone, decided when the area is built) | the debug link |
| `build` | `platformio` 6.2.0 (a wheel; its toolchain downloads on first build) | building firmware |
| `swd` | `pyocd`, or system `openocd` | SWD (later) |
| `ble` | `bleak` and, where needed, BlueZ | BLE (later) |
| `gpio` | **Pi only:** system `python3-libgpiod` and `gpiod`, as the Pi already has them | pin I/O (later) |

**The default is `core`, `flash` and `console`**: what v0.1 needs. `build` is opt-in because of the
toolchain. The pins are the versions **verified together** (`api.md`); a pin changes only when it
is re-verified.

## TOOL how it runs: preflight, plan, apply, verify

1. **Preflight, read-only.** Operating system family, architecture, Python version, whether it
   is root, whether the prefix is writable, free disk, whether PyPI answers (or a wheelhouse is
   given), and a capability summary from the same code as `probe_host.py`. It reports what is
   missing and **stops with the exact reason** if it cannot proceed (for example, a Python older
   than 3.10, with what to install).
2. **Plan.** Every step is printed with its exact command and what it changes. `--check` stops here.
3. **Apply.** Each step is idempotent and logged to `<prefix>/install.log`. The venv is built in a
   temporary directory inside the prefix and renamed into place, so a failure never leaves a
   half-built environment. It stops at the first failed step, names it and says how to retry.
4. **Verify.** In the real venv it runs the probe and the smoke tests (`scripts/smoke_*.py`), and
   prints a PASS or FAIL line per feature. The exit code is the verdict. A host is not "installed"
   until verify passes.

## TOOL where things go

- **`<prefix>` defaults to the checkout's own location, normally `/usr/share/pac/nrf-workbench`**
  **[stated: the install folder]**, and `--prefix` overrides it. The venv is `<prefix>/venv`,
  host configuration is `<prefix>/config/`, the log is `<prefix>/install.log`. All are gitignored.
- **System-wide paths are not used.** Peter's hosts keep everything under `/usr/share/pac`. Where a
  system integration needs a path elsewhere (a systemd unit), the file lives in the prefix and is
  linked into place (`systemctl link`), so removing the prefix removes the install.
- **Host configuration is created only if missing and never overwritten:** `config/host.yaml`,
  `config/units.yaml` (no unit is protected), `config/links.yaml` (**no debug link declared**,
  because none is wired), and a token file written with mode 0600 only when the service is asked
  to leave loopback. The installer prints nothing secret.

## TOOL system packages

The installer installs the system packages a chosen feature needs, itself, **when it is root and
the package manager is `apt`**, after printing the plan. **As a non-root user it prints the exact
`sudo` command and stops**; it never half-installs. Another package manager is refused with the
list of what to install. Today only `gpio` and `swd` need system packages; the default features need
none beyond `python3-venv`, which the Pi already has **[verified]**.

## TOOL offline and upgrade

- **Offline bench:** `--wheelhouse DIR` installs with `pip --no-index --find-links`. The wheelhouse
  is built once on a connected host by a documented command (`pip download` and `pip wheel` for the
  target architecture and Python), including the `adafruit-nrfutil` wheel built from source. Wheels
  for the service stack resolve for aarch64 on CPython 3.10, 3.11 and 3.13 **[verified, `api.md`]**.
- **Upgrade:** `git pull` then `install/install.sh`. It is idempotent: an unchanged pin does
  nothing, a changed pin rebuilds the venv atomically. Configuration is untouched.
- **A release** installs from the wheel (which carries the built dashboard assets), with the same
  command and the same verify step. From a checkout, the dashboard says plainly that the UI is not
  built (`dashboard.md`).
- **Uninstall** removes the venv and the linked unit. It **keeps configuration and logs**, and says so.

## TOOL what it never does

- **Never touches a serial device**, never flashes, never requests a mode change, never writes
  DTR or RTS. It installs software; it does not drive hardware.
- **Never edits the Pi's boot configuration** (`config.txt`, `cmdline.txt`), enables a UART, or
  removes the kernel console. It reports the lines needed and that a reboot follows
  (`console.md`); changing them is a hardware decision and an explicit `--enable-uart` step of the
  console area, with a backup, later.
- **Never changes the system Python** and never runs `pip` outside the venv.
- **Never adds a user to a group by itself.** Access to serial nodes needs `dialout` (`pi` and
  `root` are in it on the Pi **[verified]**); the installer reports membership and offers
  `--add-user NAME`, because a group change is a privilege change.
- **Never contains a sleep or a made-up timeout.** It waits on process exit. A network step is
  bounded by pip's own retry limits and the human's Ctrl-C, and says so.
- **Never reports PASS for a check that did not run.** A feature that could not be verified (no
  board attached) reads `not verified: reason`, never `ok`.

## DECIDE derived (Claude; Peter may overrule)

1. **An installer is required, and it is in v0.1.** Constraint: Peter's statement, and v0.1 needs
   tools that are installed by hand today.
2. **Python for the logic, a thin shell wrapper to start it.** Constraint: the logic needs tests;
   the shell is only there because Python may not be the right Python yet.
3. **A venv under the prefix, not the system Python and not a shared environment.** Constraint:
   the Pi has no system `pip`, a venv is self-contained and removable, and each host's `/usr/share/pac`
   layout already differs (`py` on the dev host, `venv` on the Proxmox host).
4. **Default features are `core`, `flash`, `console`; `build` is opt-in.** Constraint: v0.1's needs,
   and an unmeasured toolchain download on first build.
5. **Plan, then apply, with `--check` available; not a prompt.** Constraint: it must work from a
   script and over SSH, where a prompt blocks; the printed plan is the review.
6. **Configuration under the prefix, never overwritten.** Constraint: Peter's install-folder rule,
   and an upgrade must not erase a host's wiring declarations.
7. **Nothing about boot configuration or groups happens implicitly.** Constraint: both change
   what the hardware or the host allows, which is Peter's call.

## VERIFY

- **A fresh Pi, one command:** from an empty `/usr/share/pac`, `install/install.sh` ends with every
  default feature PASS. Same on the Proxmox host (x86_64) and the dev container.
- **Idempotent:** a second run changes nothing (the filesystem is identical before and after).
- **`--check` changes nothing:** compared by a filesystem listing before and after.
- **It fails cleanly:** with Python 3.9 on the path, with no network and no wheelhouse, and as a
  non-root user needing `apt`, it stops with the exact reason and leaves no half-built venv.
- **`adafruit-nrfutil` really installs from source on the Pi** and `adafruit-nrfutil version` runs.
  **[unknown]**: only the dev host has run it so far.
- **Uninstall keeps `config/`.**
- **No serial node is opened** during an install, checked by running it under a process that
  records opens.
