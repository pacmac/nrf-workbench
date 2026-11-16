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
7. **My first draft assumed things, and Peter said not to.** It assumed `git`, `python3`, a Python
   of at least 3.10, a downloader, `tar`, `apt`, root, a terminal to ask on, a reachable network and
   GitHub API, and a writable prefix. **Peter was right about `git`, and the Pi's own logs prove
   it [verified, read-only, 2026-11-16]:** the Pi is Raspberry Pi OS **Lite** (`pi-gen` stage2,
   reference 2026-10-06, Debian 13), and `dpkg.log` and `apt/history.log` show **`git` was installed
   after first boot by `apt install -y git`**, while `curl`, `wget`, `tar`, `gzip`, `python3` and
   `python3-venv` were in the stock image with no install event (cloud-init added only
   `avahi-daemon`). So a fresh Pi OS Lite has no `git`, and the earlier "git is present" reading
   was that earlier manual step. That is true of **this one image only**; other images are
   **[unknown]**. Every prerequisite is now detected with a stated outcome (see the bootstrap).
8. **`curl ... | sh` has two traps.** The script's standard input is the pipe, so a prompt that reads
   stdin would swallow the script or hang; and a download cut short would execute a partial script.
   The design below closes both. A non-interactive SSH command also has no terminal **[verified]**.

---

## Files this spec allows

The only files its build task may create: `install/install.sh` (the POSIX bootstrap),
`install/install.py` (the installer), `install/features.yaml` (the feature manifest),
`tests/install/**` (tests and the simulated-absence harness), and a top-level `README.md` that
carries the one-liner and the careful download-then-read path.

## TOOL what it is

One command, **fetched straight from GitHub** **[stated]**, that downloads the project, makes the
host ready to run it, asks the user where a decision is genuinely theirs, and **proves the result**:

```
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh -s -- --check
curl -fsSL ... | sh -s -- --features flash,console,build --ref <commit>
```

and from a checkout: `install/install.sh [--check | --features ... | --uninstall]`. The same file
serves both: run from a checkout it uses that checkout; piped from `curl` it downloads one.

The work is split so the part that must run on a bare host is as small as possible:

- **`install/install.sh` is POSIX `sh` and does only the bootstrap**: detect what the host has,
  fetch the project, make sure a usable Python exists, and hand over.
- **`install/install.py` does everything else** (stdlib only, so it runs before anything is
  installed). The logic is in Python so it can be tested.

## TOOL the bootstrap: detect, never assume

Peter, 2026-11-16: "the Pi will NOT have git when it's a fresh install, will it?" and then "do NOT
assume." No tool, terminal, privilege or network is assumed. **Each prerequisite is detected, and
each has a stated outcome if it is missing.** Nothing here has been tested on a fresh image
(**[unknown]**; see Verify); the table is the design, not a claim about any image.

| prerequisite | how it is detected | if absent |
|---|---|---|
| a downloader | `curl`, else `wget` | stop: name both and how to get one (without one the one-liner could not have started) |
| `tar` and `gzip` | looked up | stop, naming the packages |
| **`git`** | looked up | **not required.** Used only if present and the user asks for a developer checkout (`--git`). Never installed by default. |
| `python3` | looked up, then asked for its version | root and `apt`: **offer** to install it, then continue; otherwise stop with the exact command |
| Python at least 3.10 (the floor, `api.md`) | `python3 -c` on its version | stop with the version found and what is needed; it does not build Python from source |
| the `venv` module and `ensurepip` | `python3 -m venv --help` and an `ensurepip` import | root and `apt`: offer the venv package; otherwise stop with the command |
| a package manager | `apt-get` | any other: stop with the list of packages needed, never guess a distribution's names |
| privilege | `id -u`, and `sudo` if not root | not root and a step needs it: ask to re-run under `sudo`, or to choose a prefix the user can write; non-interactive: stop with the command |
| a terminal to ask on | whether a controlling terminal can be opened for reading | none: the non-interactive rules below |
| the network and GitHub | the API call below | fall back as below; both failing: stop |
| the prefix | exists, writable, free disk | create it if privileged; otherwise stop. The disk a venv needs is **[unknown]** (a measurement task), so the free space is printed, and nothing is refused on a guessed figure. |
| the architecture | `uname -m` | recorded, not a gate: aarch64 and x86_64 are verified (`api.md`); any other is tried and reported |

**How it fetches, with no git [verified on the Pi, 2026-11-16]:**

1. Ask GitHub for the exact commit the requested ref points at
   (`api.github.com/repos/pacmac/nrf-workbench/commits/<ref>`, header
   `Accept: application/vnd.github.sha`). The reply was a 40-character SHA.
2. Download the **tarball of that exact commit** (`github.com/pacmac/nrf-workbench/archive/<sha>.tar.gz`)
   and unpack it into the prefix. The listing had 20 entries under one
   `nrf-workbench-<sha>/` directory.
3. **Print the commit it is about to install**, and record it in `<prefix>/.installed`.
4. If the API is unreachable or refuses, fall back to the branch tarball
   (`archive/refs/heads/<ref>.tar.gz`), record the commit as **unpinned**, and say so. The API's
   unauthenticated limit and its behaviour from a Pi were not measured (**[unknown]**; the one
   attempt to read the limit returned 404), which is why the fallback exists.

Pinning to a commit also avoids a skew: raw GitHub files are cached for up to five minutes
(`cache-control: max-age=300`, **[verified]**), so a freshly pushed fix may not reach the `curl`
one-liner at once, but the installer then fetches the commit the API names, not a cached copy.

**Updates without git:** run the same command. It fetches the new commit, replaces the code, and
**leaves `config/`, `venv/` pins it does not change, and the log alone**. A checkout made with
`--git` updates with `git pull` instead, and a dirty one is never overwritten.

## TOOL asking the user

"With user interaction where needed" **[stated]**. A pipe from `curl` makes this non-trivial:

- **The script's standard input is the pipe, so prompts never read from it.** Questions are asked
  on the controlling terminal, opened explicitly. The whole script is wrapped in one function and
  called on the last line, so a download that is cut short runs **nothing**. Children get the
  terminal, or nothing, as their input, never the pipe.
- **It asks only what is genuinely the user's:** the install location (default
  `/usr/share/pac/nrf-workbench`); which optional features (the default set is shown, `build` is
  offered with its warning); installing system packages, after showing the exact command; re-running
  under `sudo`; adding a user to `dialout`; what to do about a dirty checkout. It does not ask about
  anything evidence settles.
- **No terminal:** it never blocks. With `--yes` it accepts only the **safe defaults** and still
  refuses the choices above that need a person (a group change, a boot-configuration edit). Without
  `--yes` it prints the plan, applies nothing, says why, and exits with a distinct code.
  A non-interactive SSH command is exactly this case: **[verified]** it had no terminal.
- **The plan is always printed before anything is applied**, and `--check` stops there.

## TOOL trust: running a remote script

Running something fetched from the network, possibly as root, is a trust decision, and the spec
says so rather than hide it.

- It is TLS only and certificate-checked, never `-k`, and it never pipes further downloads into a
  shell.
- It prints the commit SHA before applying, and `--ref <commit>` pins one, so a known-good commit can
  be installed repeatably.
- **The careful path is offered first in the docs:** download the script, read it, then run it
  (`curl -fsSLo install.sh ...; less install.sh; sh install.sh`). The bootstrap is kept small enough to read.
- The repository owner controls what the one-liner runs; that is inherent and is stated.

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
- **Upgrade:** run the same one-liner (or `git pull` then `install/install.sh` for a checkout made
  with `--git`). It is idempotent: an unchanged pin does nothing, a changed pin rebuilds the venv
  atomically. Configuration is untouched.
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
- **Never assumes a tool, a privilege, a terminal or the network.** Each is detected and has a
  stated outcome when absent. It does not install `git` unless the user asks for a developer
  checkout.
- **Never reads the pipe it was started from**, and never executes a partially downloaded script.

## DECIDE derived (Claude; Peter may overrule)

1. **An installer is required, and it is in v0.1.** Constraint: Peter's statement, and v0.1 needs
   tools that are installed by hand today.
2. **POSIX `sh` for the bootstrap only, Python for everything else.** Constraint: the logic needs
   tests, and the shell is only there because Python may be missing or the wrong version, so it
   must itself assume nothing beyond `sh`.
3. **A venv under the prefix, not the system Python and not a shared environment.** Constraint:
   the Pi has no system `pip`, a venv is self-contained and removable, and each host's `/usr/share/pac`
   layout already differs (`py` on the dev host, `venv` on the Proxmox host).
4. **Default features are `core`, `flash`, `console`; `build` is opt-in.** Constraint: v0.1's needs,
   and an unmeasured toolchain download on first build.
5. **Plan first, always; ask on the terminal only what is the user's to decide; never block without
   one.** (Revises my first draft, which said "not a prompt".) Constraint: Peter wants user
   interaction where needed, but a non-interactive SSH command has no terminal **[verified]** and a
   blocked prompt there hangs, so the no-terminal path prints the plan and exits, or takes only the
   safe defaults under `--yes`.
8. **Download the tarball of an exact commit, not a git clone.** Constraint: `git` is not
   guaranteed on a fresh host and Peter said not to assume it; the API plus a tarball needs only
   `curl` and `tar`, was verified to work from the Pi, and pins a commit. Departs from Peter's
   word "clones": a clone stays available as `--git` for development.
9. **Wrap the script in a function and call it last.** Constraint: a truncated `curl | sh` must run
   nothing.
10. **Detect everything, with a stated outcome for each absence.** Constraint: Peter, "do NOT
    assume."
6. **Configuration under the prefix, never overwritten.** Constraint: Peter's install-folder rule,
   and an upgrade must not erase a host's wiring declarations.
7. **Nothing about boot configuration or groups happens implicitly.** Constraint: both change
   what the hardware or the host allows, which is Peter's call.

## VERIFY

- **A fresh Pi, one command:** from an empty `/usr/share/pac`, the `curl` one-liner ends with every
  default feature PASS. Same on the Proxmox host (x86_64) and the dev container. **The Pi used so
  far is not a fresh image**, so this needs a freshly imaged card, which only Peter can provide.
- **Each absence, simulated:** the bootstrap is run with a restricted `PATH` that hides, one at a
  time, `git`, `curl` (leaving `wget`), `tar`, `python3`, and with Python 3.9 first, and reports
  the stated outcome every time. This needs no fresh image.
- **Piped, no terminal:** `curl ... | sh` over a non-interactive SSH command prints the plan and
  exits with its distinct code; with `--yes` it applies the safe defaults only.
- **Truncated:** the script cut at several points and piped runs nothing.
- **A re-run keeps `config/`**, and a dirty `--git` checkout is not overwritten.
- **Idempotent:** a second run changes nothing (the filesystem is identical before and after).
- **`--check` changes nothing:** compared by a filesystem listing before and after.
- **It fails cleanly:** with Python 3.9 on the path, with no network and no wheelhouse, and as a
  non-root user needing `apt`, it stops with the exact reason and leaves no half-built venv.
- **`adafruit-nrfutil` really installs from source on the Pi** and `adafruit-nrfutil version` runs.
  **[unknown]**: only the dev host has run it so far.
- **Uninstall keeps `config/`.**
- **No serial node is opened** during an install, checked by running it under a process that
  records opens.
