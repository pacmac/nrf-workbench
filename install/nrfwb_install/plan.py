"""Turn the choices into an ordered, printable plan. Pure: it changes nothing."""
import json
import os

from . import MARKER


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
    missing = [n for n in ("host.yaml", "units.yaml", "links.yaml") if not os.path.exists(os.path.join(cfg, n))]
    steps.append({"id": "config", "title": "Create host configuration in %s (only files that are missing)" % cfg,
                  "lines": ["missing: " + ", ".join(missing)] if missing else [],
                  "skip": not missing, "why_skip": "all present; never overwritten", "needs_person": False})

    # 5. the record
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
