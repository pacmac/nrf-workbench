# Install — functional spec (DRAFT, not agreed)

The installer. Peter, 2026-11-16: "is an install / deploy script needed, because you can't expect
someone to manually install dependencies." Yes. Inputs: the probe results on the Raspberry Pi 4B,
the Proxmox host and the dev container (`probes.md`), and the stack checks in `api.md`.
`/usr/share/pac` is **Peter's own preferred install folder, not a rule of the product** (Peter,
2026-11-16: "is MY preferred install folder, other people may want a different folder or just
/root"), so the installer assumes no folder. Tags: **[stated]** Peter said it;
**[verified]** measured this session on the named host; **[recalled]** from memory;
**[unknown]** nobody has looked.

Area: `install` (tool). Files: `install/` (the installer and its feature manifest), outside the
`nrf_workbench` package.

---

## GAP defects first

1. **There is no installer.** Today every dependency is installed by hand: `pio`, `adafruit-nrfutil`,
   the service stack. v0.1 cannot even be run on the Pi without it, which is why this area moves
   from v0.2 into v0.1 (the service unit itself stays v0.2).
2. **`core.md` put the host configuration in `/etc/nrf-workbench/`, apart from the install.** One
   self-contained folder (code, venv, configuration, log) is easier to move, back up and delete, so
   configuration defaults to `<prefix>/config/` (see the edit in `core.md`), wherever the user
   chose `<prefix>`.
3. **My draft treated Peter's folder as the product's default, and it is not.** The folder is
   chosen by whoever installs; see "where it installs".
4. **`adafruit-nrfutil` has no binary wheel on PyPI** **[verified: `pip download --only-binary`
   found none for aarch64]**. It installs from source. It is pure Python with pure-Python
   dependencies (`pyserial`, `click`, `ecdsa`, no native extension) **[verified: package metadata
   on the dev host]**, so no compiler is needed, but pip must fetch build tools, and an offline bench
   needs a pre-built wheel.
5. **A PlatformIO build downloads a toolchain on first use**, of a size and on an architecture
   nobody has measured here **[unknown]**. PlatformIO itself is a plain wheel and resolves for
   aarch64 **[verified]**; flashing a prebuilt DFU zip does not need it. So it is an optional
   feature, never a default.
6. **The Pi probed has no system `pip`** (`No module named pip`) **[verified]**, but `venv` works and
   `ensurepip` is present. The installer uses a venv and never installs `python3-pip` or runs `pip`
   against the system Python.
7. **The bench is USB only.** Peter, 2026-11-16: nothing is wired to the board. So the installer
   cannot verify hardware beyond what USB shows, and it must not claim a debug link works because
   the dongle is plugged in.
8. **My first draft assumed things, and Peter said not to.** It assumed `git`, `python3`, a Python
   of at least 3.10, a downloader, `tar`, `apt`, root, a terminal to ask on, a reachable network and
   GitHub API, and a writable prefix. **Peter was right about `git`, and the Pi's own logs prove
   it [verified, read-only, 2026-11-16]:** the Pi is Raspberry Pi OS **Lite** (`pi-gen` stage2,
   reference 2026-10-06, Debian 13), and `dpkg.log` and `apt/history.log` show **`git` was installed
   after first boot by `apt install -y git`**, while `curl`, `wget`, `tar`, `gzip`, `python3` and
   `python3-venv` were in the stock image with no install event (cloud-init added only
   `avahi-daemon`). So a fresh Pi OS Lite has no `git`, and the earlier "git is present" reading
   was that earlier manual step. That is true of **this one image only**; other images are
   **[unknown]**. Every prerequisite is now detected with a stated outcome (see the bootstrap).
9. **This spec had drifted Pi-shaped, and Peter said not to assume a Pi.** The first brief says
   any Linux host; the installer spec then leaned on a Raspberry Pi, Debian and `apt`, systemd, a
   user `pi`, a group `dialout`, root, and Peter's own `/usr/share/pac`. See "any Linux host".
10. **`curl ... | sh` has two traps.** The script's standard input is the pipe, so a prompt that reads
   stdin would swallow the script or hang; and a download cut short would execute a partial script.
   The design below closes both. A non-interactive SSH command also has no terminal **[verified]**.

---

## Files this spec allows

The only files its build task may create: `install/install.sh` (the POSIX bootstrap),
`install/install.py` (the thin entry point), `install/nrfwb_install/*.py` (the installer, split by
responsibility: `ui`, `host`, `prefix`, `features`, `plan`, `apply`, `verify`, and `cli` for the
orchestration),
`install/features.json` (the feature manifest), `tests/install/**` (tests and the
simulated-absence harness), and a top-level `README.md` that carries the one-liner and the careful
download-then-read path.

**The manifest is JSON, not YAML** (this corrects the first draft, which said `features.yaml`).
The installer is stdlib-only because it must run before anything is installed, and the standard
library has no YAML parser; PyYAML is one of the things being installed.

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
- **It asks only what is genuinely the user's:** the install location (no folder is assumed; see
  "where it installs"); which optional features (the default set is shown, `build` is
  offered with its warning); installing system packages, after showing the exact command; re-running
  under `sudo`; adding a user to the serial-device group; what to do about a dirty checkout; and,
  once the service exists, **where it listens and who may reach it** (see "the service"). It does
  not ask about anything evidence settles.
- **No terminal:** it never blocks. With `--yes` it accepts only the **safe defaults** and still
  refuses the choices above that need a person (a group change, a boot-configuration edit). Without
  `--yes` it prints the plan, applies nothing, says why, and exits with a distinct code.
  A non-interactive SSH command is exactly this case: **[verified]** it had no terminal.
- **The plan is always printed before anything is applied**, and `--check` stops there.

## TOOL the service: where it listens (arrives with the service, v0.2)

Peter, 2026-11-16: the dashboard on port 80; and "nothing listens on port 80, or you can make that
one of the user choices?" It is a choice. This is the installer's part of `api.md` ("listening").

```
Where should the dashboard and API listen?
  1) this machine only:  127.0.0.1:8080     port 8080 is free now
  2) the whole network:  0.0.0.0:80         port 80 is free now; needs root or a capability
  or type ADDRESS:PORT
```

- **Each offer shows, read from the host, whether its port is free right now**, and if not, what
  holds it. The check is read-only: it reads `/proc/net/tcp` and `/proc/net/tcp6` for listeners
  and matches socket inodes against the process table. A holder this user cannot see is reported
  as "in use, holder not visible to this user", never as free.
- **Privilege is checked, not assumed.** `/proc/sys/net/ipv4/ip_unprivileged_port_start` is read
  (1024 on the dev host **[verified]**). A port below it needs the service to run as root, or, for
  a non-root service under systemd, `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the unit
  **[recalled]**; otherwise the installer says so and offers a higher port. It never changes the
  sysctl, never grants a capability to the interpreter, never installs a proxy, and **never edits a
  firewall**.
- **The safe choice needs no person; the exposed choice does.** With no terminal, `--yes` takes
  loopback on 8080 only. Any non-loopback address needs an explicit `--listen` or a terminal answer,
  and so does `open-read` access: `--yes` never exposes the service to the network.
- **A non-loopback choice creates the token**: a random value written to `<prefix>/config/token`
  with mode 0600 and created exclusively, never overwritten on an upgrade. The installer prints the
  path, **never the value**, so it does not reach a log or scrollback.
- **The choice is recorded in `config/host.yaml`** (`service.listen`, `service.token_file` for a
  non-loopback address, `service.access` only when `--access open-read` was given). **Built
  2026-11-17 (afk), with these decisions:** the flags are `--listen ADDRESS:PORT` and `--access
  token|open-read`; on a terminal with no flag the menu above is shown (each offer with its port's
  real state); with no terminal and no `--listen` the answer is the product's own default (this
  machine only) and **nothing is recorded**, so `--yes` never exposes the service; `--access` with no
  address and no terminal is a usage error. **An existing `host.yaml` is never edited** (this is the
  rule the file has always had, and the one that keeps a hand-tuned bench file safe), so against one a
  `--listen` that differs from what it records, or an `--access` that differs, is **refused before any
  plan, with the lines to put in the file**, and one that matches is accepted and changes nothing. A
  file that records no address means the default, `127.0.0.1:8080`. The installer reads `service:` by
  line match (no YAML parser); a file written in flow style (`service: {listen: …}`) reads as nothing
  recorded. A port that is already in use is **reported** in the plan, not refused: the service
  refuses to start and names the holder (`api.md`), and the installer never kills another process.
  Reverse any of this by editing `host.yaml`: nothing else holds the choice.
- **The token** is 32 random bytes, url-safe (43 characters, 256 bits), created with an exclusive
  `open(..., "x")` and `chmod 0600` **before** the secret is written, so the file is never readable
  with the value in it; an existing file or link is left alone (`kept`), so an upgrade never logs a
  client out. The value is never printed, logged or put in a note; only the path is.
- **The unit** lives at `<prefix>/systemd/nrf-workbench.service` and is linked into place
  (`systemctl link`), only where systemd is detected; elsewhere the installer prints how to run the
  service in the foreground. **Enabling it at boot and starting it are asked on a terminal**, and
  `--yes` does neither, because starting a network service is not a safe default. What the unit
  contains, and why, is under "the unit".
- **Verify** requests `/` on the recorded address once `systemctl is-active` says the unit is
  active (a wildcard address is asked through loopback), and the unit line says "not verified: the
  service was not started" when it was not. **`is-active` is not enough:** the unit is `Type=simple`,
  so it is active when the process starts, before the port is bound. The request therefore retries a
  refused connection every 0.1 s (a seventh of the 0.7 s start measured on the bench Pi) and gives up
  at 3 s (4x that start); an already-bound port answers at once with no wait. A 200 is a PASS; any
  other status, a refusal at 3 s or an unusable recorded address is a FAIL with the evidence.

## TOOL the unit: what it contains, and who it runs as

Peter, 2026-11-16: the dashboard is on the bench Pi's port 80 under a transient `systemd-run` unit
that does not survive a reboot. The installer writes the permanent one. Every line is decided from
what the service needs, not copied from the transient unit.

- **One source of settings.** `ExecStart` is `<prefix>/venv/bin/python -m nrf_workbench.service
  --config <prefix>/config/host.yaml` and nothing else. The listen address, the token file, the
  firmware folder and the stream bounds live in `host.yaml` (`core.md`), so changing one never
  means regenerating the unit and the two can never disagree. With a `host.yaml` that sets nothing
  the service listens on `127.0.0.1:8080` only (`api.md`), which is the safe state a fresh install
  starts in.
- **Who it runs as: the owner of the install folder. No user is invented and none is assumed.**
  The service needs to write `data/` and its lock, read `config/token` (mode 0600, so only its
  owner can), and open serial nodes. The owner of `<prefix>` already holds the first two, because
  the installer made them. The unit therefore says `User=<owner>` when the owner is not root, and
  no `User=` line when it is root (the bench Pi's case, where the transient unit is root too).
- **Serial access for a non-root owner is `SupplementaryGroups=<group>`**, with the group read
  from an existing serial node (`any Linux host`), never named. With no serial node to read the
  installer says it cannot tell and writes no group line; it does not guess `dialout`.
- **`AmbientCapabilities=CAP_NET_BIND_SERVICE` is written only when it is needed (built 2026-11-17).**
  A non-root service needs it only to bind below `ip_unprivileged_port_start`. The port is the one
  `host.yaml` records (the config step runs before the unit step; with none recorded it is 8080), and
  the limit is read from the machine at that moment. The unit then also says
  `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`, so that one capability is all the service can ever
  hold. A root service, a port at or above the limit, or a limit that cannot be read gets neither
  line, and the file is rewritten (and `daemon-reload`ed) whenever the answer changes. Least privilege
  stands: a capability is added when a port needs it, not before.
- **`Restart=on-failure`, `RestartSec=2s`, `StartLimitIntervalSec=30s`, `StartLimitBurst=5`.** A
  crash comes back in 2 s, the value the transient unit has run with; **[measured on the bench Pi:
  kill -9, serving again 2.7 s later]**. The first draft of this spec leaned on systemd's default
  limit of 5 starts in 10 s and said a configuration error would stop after five tries. **That was
  wrong [measured: a unit that always fails, restarted every 2 s, made 16 starts in 33 s and was
  never stopped]**: 2 s spacing puts exactly five starts in any 10 s, which never exceeds the
  limit. With the window set explicitly the same probe stopped after 5 starts **[measured]**. The
  numbers: one cycle is 2 s plus about 0.7 s to start, so five cycles are 13.5 s, and a 30 s window
  is 2.2 times that, so a slower start still trips it. Five tries is systemd's own burst and covers
  a service waiting 13 s for the network or a device node; a configuration error then stays
  stopped and visible instead of looping for ever.
- **`After=` and `Wants=network-online.target`.** A `host.yaml` may name one address, which exists
  only once the network is up. With `0.0.0.0` this costs nothing.
- **`[Install] WantedBy=multi-user.target`**, the ordinary place for a service that is not a
  desktop component. The installer does not enable it unless asked on a terminal.
- **Quoting is a rule, not a hope.** `%` is doubled and `$` is doubled (systemd expands both in
  `ExecStart`), and the path is double-quoted. A prefix containing `"`, `\` or a control character
  is a blocker with the reason, never a malformed unit. (Whitespace is already refused by
  "where it installs".)
- **Linking is checked first.** There is one unit name per host. If `/etc/systemd/system/
  nrf-workbench.service` already exists and does not point at this prefix, the installer says which
  install owns it and does not replace it. Linking needs root; a non-root run prints the exact
  `sudo systemctl link` command and applies nothing, like a system package.
- **Starting the unit is the user's, and a running service is not disturbed.** Enable and start
  are asked on a terminal and `--yes` does neither. If something already listens on the chosen port
  (the transient unit, on the bench Pi), the service refuses to start and says what holds it
  (`api.md`), so the installer never kills another process.
- **Linking is part of the plan and is not a "needs a person" step**, because a link starts and
  enables nothing and uninstall removes it (it stops and disables the unit first, and touches only a
  link that points at its own prefix). **Verify** reads systemd's own answer
  (`systemctl is-active`): `active` is PASS, anything else (`activating` is a restart loop) is FAIL,
  and a unit nobody started reads "not verified: the service was not started". The HTTP request
  to `/` follows an active unit (see "where it listens").
- **A second run rewrites the file only if its text changed**, so an upgrade does not touch the
  mtime of a unit that is right, and reports `unchanged`.

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

## TOOL any Linux host, not only a Pi

Peter, 2026-11-16: "DO NOT ASSUME that it will always be installed on a PI." The project is
host-agnostic from the first brief; this spec had drifted Pi-shaped. Every place it assumed a Pi,
a Debian system, systemd, a `pi` user or Peter's own folder is replaced by detection.

| dimension | the first draft assumed | now | tested on |
|---|---|---|---|
| hardware | a Raspberry Pi | nothing about the hardware. Pi-only features (pin I/O, the header UART) are offered only if the device-tree model says Raspberry Pi (`core.md`). A host with no board attached is a normal install, not a failure. | Pi 4B (aarch64); bare-metal Proxmox host and a dev container (x86_64) |
| distribution | Debian family, `apt` | read `/etc/os-release` (`ID`, `ID_LIKE`). Everything that needs no system package (a venv and wheels) works wherever the prerequisites exist. A step that needs a system package runs only on `apt`; on any other family it stops with what is missing and never guesses package names. | Debian 13 on all three; **Ubuntu, Fedora, Alpine and others: [unknown]** |
| architecture | aarch64 | x86_64 and aarch64 wheels are verified (`api.md`). **32-bit ARM (`armv7l`, which a 32-bit Pi OS is) is [unknown]**: compiled wheels may not exist. Detected, tried, and reported, never claimed. | x86_64, aarch64 |
| C library | glibc | musl (Alpine) cannot load manylinux wheels **[recalled]**; detected and reported as unverified | glibc only |
| init system | systemd | detected (`/run/systemd/system`, `systemctl`); the service unit is offered only if present, otherwise it prints how to run the service in the foreground. A container may or may not have systemd. | systemd on all three |
| users and groups | a user `pi`, the group `dialout` | no user is assumed. The group that owns serial nodes is **read from an existing serial node**, not named (it is `dialout` on Debian and may be another elsewhere **[recalled]**); with no serial node to read it says it cannot tell. `--add-user NAME` only on request. | Debian: `dialout` |
| install location | `/usr/share/pac/nrf-workbench` | **no folder is assumed.** Candidates are offered and the user chooses, including a folder under `/root` or their home; non-interactive needs `--prefix`. See "where it installs". | `/usr/share/pac/...` on Peter's three; no other layout run yet |
| privilege | root | a rootless install (a venv in a user-writable prefix, no system packages) is supported; a feature that needs a system package or device access says what it needs | root only so far |
| Python | the distribution's, new enough | whatever the host has, checked against the floor of 3.10 (`api.md`). An older one stops the install with the version found; Python is never built from source. | 3.13.5 on all three |
| network | direct to PyPI and GitHub | the standard proxy environment variables are honoured by `curl` and `pip`; a wheelhouse covers a bench with no network | direct only |

**Support is only ever claimed for what was run.** The installer detects a host class it has not
been tested on, says "untested on this class", and tries; it does not refuse a class on a guess, and
it does not print `ok` for one on a guess.

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
| `gpio` | **only where the device-tree model says Raspberry Pi:** system `python3-libgpiod` and `gpiod` (the Pi probed already has them **[verified on one Pi]**) | pin I/O (later) |

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

## TOOL where it installs

**The installer assumes no folder.** `/usr/share/pac` is Peter's preference on his hosts; someone
else may want another path or a folder under `/root`. The folder is the `<prefix>`, and it is
chosen, not defaulted.

**Candidates, offered in this order, only those the user's privilege allows, each with its reason:**

1. **an existing install** found (a candidate containing `.installed`): the upgrade target;
2. **`/usr/share/pac/nrf-workbench`**, if `/usr/share/pac` already exists on this host (someone made
   that choice here);
3. **`$HOME/nrf-workbench`**, which is `/root/nrf-workbench` when root;
4. **`/opt/nrf-workbench`**, when privileged;
5. **`~/.local/share/nrf-workbench`**, for an unprivileged user;
6. **any path the user types.**

- **Interactive:** the list is shown and the user picks or types one. **Non-interactive:** `--prefix`
  (or `NRF_WORKBENCH_PREFIX`) is required; with exactly one existing install found, an upgrade uses
  it; otherwise it prints the plan and stops, naming the missing choice.
- **Checks on the chosen path:** absolute, writable, and either empty or already an install. A
  whole system directory (`/`, `/usr`, `/etc`, `/bin`) is refused. **Installing directly into a
  non-empty home directory such as `/root` is refused** (it would mix with the user's own files);
  a folder under it is the practical form of "just /root".
- **Everything lives under the prefix:** the code, the venv (`<prefix>/venv`), the host
  configuration (`<prefix>/config/`), the data the service writes while running (`<prefix>/data/`),
  the unit (`<prefix>/systemd/`), the log (`<prefix>/install.log`) and the record
  (`<prefix>/.installed`), so one folder can be moved, backed up or deleted. All are gitignored.
- **`<prefix>/data/` is the core system's own folder and the installer creates its structure
  (Peter, 2026-11-17: "the core system needs its own folder structure").** Today it holds one thing,
  the package store `data/firmware/`, whose project folders are described in `firmware.md`
  ("projects"). The installer creates `data/firmware` (mode 0700, as the store itself does) **and
  writes it into `config/host.yaml` as `paths.firmware`**, so a fresh install has a working store.
  Before this, `host.yaml` was written empty, the service left the store off, and the dashboard had
  nothing to list. **[verified 2026-11-17: `capabilities()` registers `firmware_store` only when
  `paths.firmware` is set.]** The path written is absolute, so moving the install folder means
  changing that one line.
- **A fresh `host.yaml` also turns the console and every live log on (2026-11-17).** It writes `paths.links`
  (`config/links.yaml`, created with `links: []`: nothing is wired yet, and declaring a link would claim a wire that
  is not there), `paths.console` (`data/console`, mode 0700, which the installer creates and hands to the install's
  owner like `data/firmware`) and the three stream bounds, because a stream with no bound is silently OFF
  (`core.md`). Each bound states its budget in the file: events 1 000 entries / 7 days (an event is under 1 KB, so
  about 1 MB), activity 5 000 / 30 days (an entry is under 2 KB, so about 10 MB), console 20 000 lines / 7 days (a
  line is capped at 1 024 bytes, so 20 MB). **These are budgets, not measurements:** nothing has measured the real
  rates on a bench. An existing `host.yaml` is not edited for any of this. It also writes `paths.gpio`
  (`data/gpio`, mode 0700, created like the others) for the GPIO pin map (`gpio.md`); the key is written on every
  host, and the `gpio` commands exist only where the device tree says Raspberry Pi.
- **An existing `host.yaml` is never edited to add it.** On an upgrade that finds a `host.yaml` with
  no `paths.firmware`, the installer creates the folder anyway, changes nothing in the file, and
  prints the two lines to add, so a user's own configuration is never rewritten under them.
- **The parking package is seeded into the store** so a fresh install has something to flash. The
  repo carries the built package (`testpartner/parking/prebuilt/`, not `dist/`: a common global
  gitignore hides any folder of that name and the file would silently miss the commit) with the source
  commit it was built from and its SHA-256, and a step after the venv is built puts it into the project `testpartner`
  through the product's own CLI in local mode (so it is validated like any upload and is idempotent:
  a second install says "already stored"). It is added only if the store is on. **No other project's
  firmware is bundled**: `mt-alarm` is another repository's product, and its owner uploads it into the
  `mt-alarm` project.
- **Seeding never touches hardware** and never flashes anything.
- **A system integration that needs a path elsewhere** (a systemd unit) keeps its file in the
  prefix and is linked into place (`systemctl link`), so removing the prefix removes the install.
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

## TOOL the README carries the copy-paste commands

Peter, 2026-11-16: the README needs instructions the user can copy and paste to start the
installer. It has them: the one-liner (`curl`, and `wget` for a host without it), the careful
download-read-run path, `--check`, the no-terminal form with `--yes --prefix`, `--ref`,
`--features`, upgrade and uninstall, what it asks, what it needs, and what was tested.

- **The README must not outrun the code.** Until the installer exists it says so at the top, and
  the notice is removed only when the commands are tested.
- **The commands in the README are tested, not trusted.** A check extracts every command block from
  the README and runs it in `--check` mode in the harness, so the instructions cannot drift from
  the installer's real options (`doc` area rule: prose that governs work matches the code it cites).
- **It contains nothing private**: no hostnames, addresses or serials, and no folder presented as
  the default.

## TOOL what it never does

- **Never touches a serial device**, never flashes, never requests a mode change, never writes
  DTR or RTS. It installs software; it does not drive hardware.
- **Never edits boot configuration on any host** (a Pi's `config.txt` and `cmdline.txt`, a PC's
  bootloader), enables a UART, or
  removes the kernel console. It reports the lines needed and that a reboot follows
  (`console.md`); changing them is a hardware decision and an explicit `--enable-uart` step of the
  console area, with a backup, later.
- **Never changes the system Python** and never runs `pip` outside the venv.
- **Never adds a user to a group by itself.** Access to serial nodes needs the group that owns
  them, read from an existing node rather than assumed (on the probed Pi it is `dialout` and the
  users `pi` and `root` are in it **[verified, one host]**); the installer reports membership and
  offers `--add-user NAME`, because a group change is a privilege change.
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
   the Pi probed has no system `pip` (a venv needs none, on any host), a venv is self-contained and
   removable, and each host's `/usr/share/pac` layout already differs (`py` on the dev host, `venv`
   on the Proxmox host).
4. **Default features are `core`, `flash`, `console`; `build` is opt-in.** Constraint: v0.1's needs,
   and an unmeasured toolchain download on first build.
5. **Plan first, always; ask on the terminal only what is the user's to decide; never block without
   one.** (Revises my first draft, which said "not a prompt".) Constraint: Peter wants user
   interaction where needed, but a non-interactive SSH command has no terminal **[verified]** and a
   blocked prompt there hangs, so the no-terminal path prints the plan and exits, or takes only the
   safe defaults under `--yes`.
6. **Download the tarball of an exact commit, not a git clone.** Constraint: `git` is not
   guaranteed on a fresh host and Peter said not to assume it; the API plus a tarball needs only
   `curl` and `tar`, was verified to work from the Pi, and pins a commit. Departs from Peter's
   word "clones": a clone stays available as `--git` for development.
7. **Wrap the script in a function and call it last.** Constraint: a truncated `curl | sh` must run
   nothing.
8. **Detect everything, with a stated outcome for each absence.** Constraint: Peter, "do NOT
    assume."
9. **Everything under one chosen prefix, with configuration never overwritten.** Constraint: a
   self-contained folder is the one a user can move, back up and delete, whichever folder they
   chose; and an upgrade must not erase a host's wiring declarations.
10. **No default install folder.** Constraint: Peter, "/usr/share/pac is MY preferred install
    folder, other people may want a different folder or just /root"; a default would be Peter's
    preference imposed on everyone.
11. **Nothing about boot configuration or groups happens implicitly.** Constraint: both change
   what the hardware or the host allows, which is Peter's call.

## VERIFY

- **The listen choice (with the service):** the menu shows each port's real state; a port held by
  a test listener is reported with its holder; a privileged port as non-root without the
  capability is explained; `--yes` alone yields loopback on 8080 and nothing exposed; a
  non-loopback choice writes a 0600 token file and prints only its path; a re-run with a different
  `--listen` does not change the recorded choice without asking.

- **A fresh host, one command:** on a freshly imaged Pi, and on a clean x86_64 Debian host (a VM
  or container created for the test), the `curl` one-liner ends with every default feature PASS.
  **Done on a fresh Pi (2026-11-16), non-interactive form**: see "Results". Still to run: the
  interactive form in a real terminal on a Pi (it is covered by a pseudo-terminal test), and a clean
  x86_64 container.
- **Other folders work:** the same install into `/root/nrf-workbench`, into `/opt/nrf-workbench`,
  and, run unprivileged, into `~/.local/share/nrf-workbench`, each verifying, proving no folder or
  host default is load-bearing. Installing into a non-empty `/root` itself is refused.
- **Non-interactive without `--prefix` and with no existing install prints the plan and stops.**
- **Every command block in the README runs** (in `--check` mode) and none names an option the
  installer does not have.
- **Test order, because a fresh image is used up by its first install:** (1) the simulated-absence
  harness and a clean x86_64 container first, (2) only when those pass, a freshly imaged Pi, so a
  bug found on the Pi's first run does not cost a reflash.
- **A class that is not tested is reported as untested**: 32-bit ARM, musl, a non-`apt` family and
  a host with no systemd each produce "untested on this class" or a clear stop, never a silent `ok`.
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
- **`adafruit-nrfutil` really installs from SOURCE on aarch64** and `adafruit-nrfutil version` runs.
  **[still unproven]**: on the fresh Pi it installed and ran, but from a prebuilt wheel that the
  Pi's configured extra index supplied (see "Results"), so the source-build path has only been run
  on the x86_64 dev host. Prove it on aarch64 with that index disabled.
- **Uninstall keeps `config/`.**
- **No serial node is opened** during an install, checked by running it under a process that
  records opens.

## Results

### A freshly imaged Raspberry Pi 4B (2026-11-16)

The one-liner, run over SSH (so the non-interactive form: `--yes --prefix /usr/share/pac/nrf-workbench`,
Peter's own folder on his own host), on a **freshly flashed** Raspberry Pi OS Lite (reference
2026-10-06, Debian 13, aarch64, Python 3.13.5, root).

- **The stock image, read before installing [verified]:** `curl`, `wget`, `tar`, `gzip`, `python3`,
  `sudo` and `apt-get` present; **`git` absent**; `python3-venv` installed; no system `pip`; no
  `/usr/share/pac`; no apt installs since first boot; only SSH listening.
- **Outcome:** exit 0, **about 59 seconds** (from the install log's first and last stamps), the venv
  **63 MB** and the project **412 KB**. All five checks PASS (`mcp`, `starlette`, `uvicorn`,
  `websockets`, `yaml` import; both smoke tests; `adafruit-nrfutil version`; `serial` imports),
  and hardware reads "NOT VERIFIED" as designed. The installed versions are exactly the pins.
- **It changed nothing else [verified afterwards]:** `git` still absent, no apt installs, the
  system Python still has no `pip`, the listeners are unchanged (only SSH), and both USB devices'
  nodes are still there. The installer opened none of them.
- **Idempotent [verified]:** a second run skipped the venv and the configuration, re-copied the
  code and rewrote the record, exit 0.
- **The Pi OS image configures an extra pip index.** `/etc/pip.conf` contains
  `extra-index-url=https://www.piwheels.org/simple`, and pip's output (kept whole, as the spec
  requires) shows packages, including `adafruit-nrfutil`, coming from piwheels. Consequences:
  - **The source-build path of `adafruit-nrfutil` was not exercised on aarch64**: piwheels supplied
    a prebuilt wheel, so the gap "adafruit-nrfutil has no binary wheel on PyPI" stays unproven
    on aarch64.
  - **Our pins are exact versions but not hashes**, so whichever index the host configures can
    serve the bytes. This is a supply-chain fact to decide on, not a defect found in a run:
    hash-pinning (`--require-hashes`) and a way to say "PyPI only" are future options, and the
    installer already prints the indexes pip will use.
