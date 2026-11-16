# nrf-workbench

A host-agnostic bench service that gives an AI (or you) hands and eyes on Nordic nRF hardware:
flash firmware, read the device's debug output, identify what is plugged in, and watch it all on a
dashboard, over a CLI, an HTTP API and an MCP server that share one set of commands. It is the nRF
counterpart of the ESP32 testbench in
[SensorsIot/Embedded-AI-Harness](https://github.com/SensorsIot/Embedded-AI-Harness).

It runs on any Linux host (a Raspberry Pi, a PC, a server, a VM, a container). It does not assume a
Pi, a particular distribution, a particular folder, or that anything is wired to the board.

> **Status: the installer, the service (HTTP API), the command line and the MCP server work; the
> dashboard is not built yet.** Proved on a real RAK4631 on a Raspberry Pi 4B: from another machine,
> over HTTP, a firmware package was uploaded, stored, and flashed to the board by id and in one call,
> and the board came back running the new image (checked from the board's own USB identity and from
> its debug console). The install commands were run against GitHub itself on Debian 13 (x86_64) and
> on a freshly flashed Raspberry Pi 4B (aarch64, no `git`); that Pi run used the non-interactive form
> (`--yes --prefix`), and the interactive prompts are tested with a pseudo-terminal, not yet in a real
> terminal on a Pi. **Not built:** the dashboard, a service unit (it runs in the foreground), TLS, the
> debug-console reader inside the service, and SWD, BLE and GPIO. Only the RAK4631 has a profile.

## Install

Copy and paste. The installer asks you for the few decisions that are yours (below) and then proves
the result.

**The one-liner**, which downloads the installer from GitHub and runs it:

```sh
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh
```

No `curl`? Use `wget`:

```sh
wget -qO- https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh
```

**The careful way: download it, read it, then run it.** This runs a script from the internet,
possibly as root, so reading it first is reasonable:

```sh
curl -fsSLo install.sh https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh
less install.sh
sh install.sh
```

**Look first, change nothing.** Prints what it would do and stops:

```sh
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh -s -- --check
```

**No terminal to ask on** (a script, a non-interactive SSH command, automation). You must say where
to install, and `--yes` accepts only the safe defaults:

```sh
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh -s -- --yes --prefix /root/nrf-workbench
```

**A particular version.** Pin an exact commit so the install is repeatable:

```sh
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh -s -- --ref <commit>
```

**Choose features.** The default is the core, the flasher and the console. Add the build
tooling (PlatformIO, whose toolchain downloads on first build) only if you need it:

```sh
curl -fsSL https://raw.githubusercontent.com/pacmac/nrf-workbench/main/install/install.sh | sh -s -- --features flash,console,build
```

**Upgrade:** run the same command again. Your configuration is kept.

**Uninstall** (keeps your configuration and logs, and says so):

```sh
<install folder>/install/install.sh --uninstall
```

## Use it

The commands run from inside the install folder (the installer builds the virtual environment
there; the package is not installed into it). Below, `<install folder>` is the folder you chose.

**1. Run the service** on the machine the board is plugged into:

```sh
cd <install folder>
venv/bin/python -m nrf_workbench.service --firmware-dir <a folder for the stored firmware>
```

It listens on `127.0.0.1:8080`, this machine only, with no token. To reach it from another machine
you must give it a token (it refuses to start without one) and say where to listen. The traffic is
plain HTTP, so the token crosses the network unencrypted: use it only on a network you trust.

```sh
( umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(32))" > token )
venv/bin/python -m nrf_workbench.service --listen 0.0.0.0:8080 --token-file token --firmware-dir <folder>
```

It checks the port before it starts and tells you who holds it, or that a low port needs `root`.
Nothing here assumes port 80 or any folder.

**2. Use it from the command line**, on that machine (no service needed) or from another
(`--host`). Every command is generated from one list, so `python -m nrf_workbench commands` is the
whole menu and `--help` on any command shows its arguments, their units, and which are required:

```sh
python -m nrf_workbench devices list
python -m nrf_workbench firmware put --file firmware.zip --name my-build
python -m nrf_workbench programmer flash --serial <serial> --firmware <id> \
        --enter-bound-s 5 --flash-bound-s 12
python -m nrf_workbench programmer flash-stream --file firmware.zip --serial <serial> \
        --enter-bound-s 5 --flash-bound-s 12 --expect-product "my product string"
python -m nrf_workbench --host http://pi.local:8080 --token-file token devices list
```

`--json` prints the same result the API sends. Exit codes: 0 ok, 1 refused (nothing was done), 2 bad
usage, 3 failed, 4 suspect (it finished but the evidence says do not trust it). **No wait has a
default timeout:** you give a ceiling for each, derived from measurement. On a RAK4631 the
bootloader handover took about 0.4 to 0.5 s and a flash ran at 12 to 15 kB/s.

**3. Use it from an AI** by adding the MCP server to its configuration. It is a client of the
service, so it runs where the firmware is built; an upload tool takes a `path` on that machine and
streams the file, so the AI never has to read or encode the bytes:

```json
{"mcpServers": {"nrf-workbench": {
  "command": "<install folder>/venv/bin/python",
  "args": ["-m", "nrf_workbench.api.mcp_server", "--host", "http://pi.local:8080"],
  "env": {"PYTHONPATH": "<install folder>", "NRFWB_TOKEN_FILE": "<path to the token file>"}}}}
```

The token is read from a file or the environment, never from a command-line value, and is never
printed.

## What it asks you

Only what is yours to decide, and only on a terminal:

- **Where to install.** No folder is assumed. It offers a few (an existing install it finds, a
  folder under your home such as `/root/nrf-workbench`, `/opt/nrf-workbench` if you have the rights,
  a per-user folder otherwise) and accepts any path you type. Everything lives in that one folder,
  so you can move, back up or delete it.
- **Which optional features** to install.
- **Whether to install system packages**, after it shows you the exact command (only on `apt`
  systems; elsewhere it tells you what is missing).
- **Whether to re-run under `sudo`**, if a step needs it.

It never changes your boot configuration, adds a user to a group, or touches a serial device or
flashes anything on its own.

## What it needs

Nothing is assumed: each of these is detected, and if one is missing the installer says what, and
how to get it, instead of failing in the middle.

- `sh`, and `curl` or `wget`, and `tar`
- Python 3.10 or newer (it never builds Python from source)
- `git` is **not** needed. (It is used only if you ask for a developer checkout with `--git`.)
- Network access to GitHub and PyPI, or a pre-built wheelhouse (`--wheelhouse DIR`)

**Tested so far:** a full install on Debian 13 x86_64, and on a freshly flashed Raspberry Pi 4B
(Raspberry Pi OS Lite, aarch64). Other distributions, 32-bit ARM, musl, and hosts without systemd are
**untested**; the installer says "untested on this class" and tries, and it will not claim a pass
for something it did not run.

## Where to read more

- `nrf-workbench.md`: the original brief
- `docs/specs/`: the functional specs (`install.md`, `devices.md`, `console.md`, `core.md`,
  `api.md`, `dashboard.md`, `programmer.md`, `architecture.md`, `probes.md`)
- `scripts/`: read-only probes you can run on any host to see what it has
