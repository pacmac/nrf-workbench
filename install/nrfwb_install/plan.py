"""Turn the choices into an ordered, printable plan. Pure: it changes nothing."""
import json
import os
import pwd
import re

from . import MARKER

UNIT_NAME = "nrf-workbench.service"
SYSTEM_UNIT_DIR = "/etc/systemd/system"   # where `systemctl link` puts the link


def unit_path(prefix):
    return os.path.join(prefix, "systemd", UNIT_NAME)


# Packages the repo carries that a fresh install puts in the store: (project, name, path in the install).
# Only what this project owns: mt-alarm is another repository's firmware and is never bundled (install.md).
SEEDS = (("testpartner", "parking", os.path.join("testpartner", "parking", "prebuilt", "parking.zip")),)


def sets_firmware(text):
    """Whether a host.yaml appears to set paths.firmware. The installer is standard library only and has no
    YAML parser, so this is a line match, and a miss only costs an unneeded note, never an edit."""
    return re.search(r"^\s+firmware\s*:\s*\S", text, re.M) is not None


def store_path(prefix):
    """The package store: the core system's own folder for what the service writes (docs/specs/install.md)."""
    return os.path.join(prefix, "data", "firmware")


def wiring_path(prefix):
    """The wiring store's folder: which adapter or pin is wired to which device (docs/specs/wiring.md). It replaces the
    hand-edited config/links.yaml, which a fresh install no longer creates."""
    return os.path.join(prefix, "data", "wiring")


def console_path(prefix):
    """The console's own folder: its macros (docs/specs/console.md)."""
    return os.path.join(prefix, "data", "console")


def gpio_path(prefix):
    """The GPIO pin map's folder (docs/specs/gpio.md). Written into host.yaml always; the commands exist only on a Pi."""
    return os.path.join(prefix, "data", "gpio")


def unit_blocker(prefix):
    """Why this folder cannot be written into a unit file, or None. `%` and `$` are escaped instead."""
    if any(c in '"\\' or ord(c) < 32 or ord(c) == 127 for c in prefix):
        return ("the install folder has a double quote, a backslash or a control character, which cannot be "
                "written safely into a systemd unit; choose another folder")
    return None


def service_owner(prefix):
    """The user the service runs as: the owner of the install folder, or the user running the installer
    when the folder does not exist yet. None means root (no User= line). No user is invented."""
    try:
        uid = os.stat(prefix).st_uid
    except OSError:
        uid = os.geteuid()
    if uid == 0:
        return None
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)      # systemd accepts a numeric User=


def _esc(text):
    """systemd expands % in every value and $ in ExecStart; both are doubled."""
    return text.replace("%", "%%").replace("$", "$$")


def unit_text(prefix, user, serial_group, bind_capability=False):
    """The whole unit, derived in docs/specs/install.md ("the unit"). The service reads every setting
    from config/host.yaml, so the command line names only that file. `bind_capability` adds
    CAP_NET_BIND_SERVICE for a non-root service whose recorded port is below the unprivileged limit."""
    py = os.path.join(prefix, "venv", "bin", "python")
    cfg = os.path.join(prefix, "config", "host.yaml")
    lines = [
        "[Unit]",
        "Description=nrf-workbench service (dashboard and HTTP API)",
        "Wants=network-online.target",
        "After=network-online.target",
        # 5 starts, each ~2.7 s apart (RestartSec 2 s + ~0.7 s to start, measured on the bench Pi) = 13.5 s;
        # a 30 s window is 2.2x that, so a slower start still trips it. systemd's default window is 10 s,
        # which a 2 s restart never fills (measured: 16 starts in 33 s, never stopped).
        "StartLimitIntervalSec=30s",
        "StartLimitBurst=5",
        "",
        "[Service]",
        "Type=simple",
        "WorkingDirectory=" + prefix.replace("%", "%%"),
        'ExecStart="%s" -m nrf_workbench.service --config "%s"' % (_esc(py), _esc(cfg)),
        "Restart=on-failure",
        "RestartSec=2s",
    ]
    if user:
        lines.append("User=" + user)
        if serial_group:
            lines.append("SupplementaryGroups=" + serial_group)
    if bind_capability:
        # Only the capability to bind a low port, and only on the ambient set so the unprivileged service gets it
        # at exec; the bounding set is limited to that one capability so nothing else can be gained.
        lines += ["AmbientCapabilities=CAP_NET_BIND_SERVICE", "CapabilityBoundingSet=CAP_NET_BIND_SERVICE"]
    lines += ["", "[Install]", "WantedBy=multi-user.target", ""]
    return "\n".join(lines)


def link_state(prefix, link_dir=SYSTEM_UNIT_DIR):
    """'absent', 'ours' (links to this prefix's unit) or 'other:<target>' (another install owns the name)."""
    link = os.path.join(link_dir, UNIT_NAME)
    if not os.path.lexists(link):
        return "absent"
    if os.path.realpath(link) == os.path.realpath(unit_path(prefix)):
        return "ours"
    return "other:" + os.path.realpath(link)


def read_record(prefix):
    try:
        with open(os.path.join(prefix, MARKER), "r") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def venv_python(prefix):
    return os.path.join(prefix, "venv", "bin", "python")


def _venv_is_current(prefix, pin_hash, python_version):
    rec = read_record(prefix)
    return bool(rec and rec.get("pins_hash") == pin_hash
                and rec.get("python_minor") == python_version
                and os.path.exists(venv_python(prefix)))


def build(ctx):
    """ctx keys: prefix, features, pins, pins_hash, source, staged, args, host.
    Returns {"steps": [...], "blockers": [...]}. A step is {id, title, lines, skip, needs_person}.
    A blocker is a prerequisite that makes the plan impossible as things are (exit 4)."""
    prefix, host, args = ctx["prefix"], ctx["host"], ctx["args"]
    steps, blockers = [], []
    minor = ".".join(host["python"].split(".")[:2])

    # 1. the code
    if args.git:
        has_git = os.path.isdir(os.path.join(prefix, ".git"))
        if has_git:
            lines = ["git -C %s fetch, then check out %s" % (prefix, ctx["commit"] if ctx["commit"] not in ("local", "unpinned") else args.ref or "main"),
                     "refuses to touch a checkout with local changes"]
        else:
            lines = ["git clone the project into %s" % prefix]
        steps.append({"id": "git", "title": "Make or update a developer git checkout", "lines": lines,
                      "skip": False, "needs_person": False})
    elif os.path.realpath(ctx["source"]) == os.path.realpath(prefix):
        steps.append({"id": "code", "title": "The project is already in the install folder",
                      "lines": [], "skip": True, "needs_person": False})
    else:
        steps.append({"id": "code", "title": "Copy the project (commit %s) into %s" % (ctx["commit"], prefix),
                      "lines": ["replaces the project's own folders; keeps config/, venv/ and install.log"],
                      "skip": False, "needs_person": False})

    # 2. a system package, only if the host cannot make a venv without it
    if not host["has_venv"]:
        cmd = "apt-get update && apt-get install -y python3-venv"
        if host["apt"] and host["root"]:
            steps.append({"id": "system", "title": "Install the system package that provides venv",
                          "lines": [cmd], "skip": False, "needs_person": True})
        else:
            sudo = "sudo " if (host["sudo"] and not host["root"]) else ""
            if host["apt"]:
                blockers.append("this Python cannot create a virtual environment (ensurepip is missing). "
                                "Run:  %sapt-get update && %sapt-get install -y python3-venv   then run this again." % (sudo, sudo))
            else:
                blockers.append("this Python cannot create a virtual environment (ensurepip is missing) and this "
                                "is not an apt system, so the package name is not guessed. Install the package "
                                "that provides venv for Python %s with your package manager, then run this again." % minor)

    # 3. the virtual environment and the pinned packages
    current = _venv_is_current(prefix, ctx["pins_hash"], minor)
    lines = ["packages (pinned, verified together): " + ", ".join(ctx["pins"])]
    if args.wheelhouse:
        lines.append("from the wheelhouse %s only (no network)" % args.wheelhouse)
    else:
        lines.append("downloaded from PyPI; adafruit-nrfutil is built from source (pure Python). Ctrl-C aborts.")
    steps.append({"id": "venv", "title": "Build the virtual environment %s/venv" % prefix,
                  "lines": lines, "skip": current,
                  "why_skip": "already built with these pins and this Python"})

    # 4. configuration, only what is missing
    cfg = os.path.join(prefix, "config")
    missing = [n for n in ("host.yaml", "units.yaml") if not os.path.exists(os.path.join(cfg, n))]
    if not os.path.isdir(store_path(prefix)):
        missing.append("data/firmware (the package store)")
    if not os.path.isdir(console_path(prefix)):
        missing.append("data/console (the console's macros)")
    if not os.path.isdir(gpio_path(prefix)):
        missing.append("data/gpio (the GPIO pin map)")
    if not os.path.isdir(wiring_path(prefix)):
        missing.append("data/wiring (which adapter is wired to which device)")
    store_on = True                            # a host.yaml that does not exist yet is created with the store set
    try:
        with open(os.path.join(cfg, "host.yaml"), "r") as fh:
            store_on = sets_firmware(fh.read())
        if not store_on:
            missing.append("(host.yaml sets no paths.firmware: it is left as it is and the lines to add are printed)")
    except OSError:
        pass
    config_lines = ["missing: " + ", ".join(missing)] if missing else []
    choice = ctx.get("listen")
    if choice and choice.get("listen"):
        config_lines.append(choice["summary"])
    steps.append({"id": "config", "title": "Create host configuration in %s and the package store (only what is missing)" % cfg,
                  "lines": config_lines,
                  "skip": not missing, "why_skip": "all present; never overwritten", "needs_person": False})

    # 4b. the packages this repo carries, into the store, through the product's own CLI (validated like any upload)
    steps.append({"id": "seed", "title": "Put the parking package in the package store (project testpartner)",
                  "lines": ["%s into project %s; already stored means nothing is added" % (rel, project) for project, _n, rel in SEEDS],
                  "skip": not store_on, "why_skip": "host.yaml sets no paths.firmware, so the package store is off",
                  "needs_person": False})

    # 5. the service unit, only where systemd is
    if host["systemd"]:
        bad = unit_blocker(prefix)
        owner = service_owner(prefix)
        state = link_state(prefix, ctx.get("link_dir", SYSTEM_UNIT_DIR))
        if bad:
            blockers.append(bad)
        elif state.startswith("other:"):
            blockers.append("the unit name %s is already linked to %s, another install; this one will not replace it. "
                            "Remove that link first (systemctl disable --now %s; systemctl daemon-reload) if it is stale."
                            % (UNIT_NAME, state[len("other:"):], UNIT_NAME))
        lines = ["file: %s" % unit_path(prefix),
                 "runs as: %s" % (owner or "root (the owner of the install folder)")]
        if owner:
            lines.append("serial group: %s" % (host["serial_group"] or "not written (no serial node here to read it from)"))
        lines.append("then: systemctl link (root only; otherwise the command is printed). Not enabled or started unless you say so.")
        steps.append({"id": "unit", "title": "Write the service unit and link it", "lines": lines,
                      "skip": False, "needs_person": False})

    # 6. the record
    steps.append({"id": "record", "title": "Write the install record %s/%s" % (prefix, MARKER),
                  "lines": ["features: " + ", ".join(ctx["features"])], "skip": False, "needs_person": False})
    return {"steps": steps, "blockers": blockers}


def render(plan, notes):
    out = []
    for i, s in enumerate(plan["steps"], 1):
        mark = "skip" if s.get("skip") else "do  "
        out.append("  %d. [%s] %s" % (i, mark, s["title"]))
        if s.get("skip") and s.get("why_skip"):
            out.append("            (%s)" % s["why_skip"])
        for line in s["lines"]:
            out.append("            " + line)
    for n in notes:
        out.append("  note: " + n)
    return out
