# nrf-workbench

A host-agnostic bench service that gives an AI (or you) hands and eyes on Nordic nRF hardware:
flash firmware, read the device's debug output, identify what is plugged in, and watch it all on a
dashboard, over a CLI, an HTTP API and an MCP server that share one set of commands. It is the nRF
counterpart of the ESP32 testbench in
[SensorsIot/Embedded-AI-Harness](https://github.com/SensorsIot/Embedded-AI-Harness).

It runs on any Linux host (a Raspberry Pi, a PC, a server, a VM, a container). It does not assume a
Pi, a particular distribution, a particular folder, or that anything is wired to the board.

> **Status: the installer works; the rest of the product is not built yet.** The commands below
> were run against GitHub itself on Debian 13: a full install, verify and re-run on x86_64, and
> on a freshly flashed Raspberry Pi 4B (Raspberry Pi OS Lite, aarch64, no `git`): installed and
> verified in about a minute, with a second run skipping what was already built. That Pi run used
> the non-interactive form (`--yes --prefix`); the interactive prompts are tested with a
> pseudo-terminal and not yet in a real terminal on a Pi. Today the installer sets up the
> environment and the tools; the service, CLI and dashboard are specified in `docs/specs/` and
> arrive in later releases.

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
