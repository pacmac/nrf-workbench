"""Which role's power profile is in force on the profiler (docs/specs/project-power-profile.md).

A profile hangs off a ROLE of a project (manifest `power`). It is in force only while its project is applied and that role's board is attached; the
plan already works out "attached" for every role, so there is no second idea of it here. The profiler is one instrument and nothing on a USB link says
which board is wired to it, so when more than one attached role has a profile a person names the board under test (`meter.project_use`), and the
choice is kept as `<paths.meter>/under-test.json` = {project, role, key}. It counts only while that board, by its key, is attached: a reading
command writes nothing, so a choice that has stopped counting is ignored and the next `project_use` or `project.release` removes it.

READ-ONLY apart from the choice file. `roles_of(m)` is the plan's `roles` for a manifest (commands.py passes it, so this file needs no plan of its own).
"""
import json
import os
import tempfile

from . import manifest as mf

UNDER_TEST = "under-test.json"


def link_of(m, role):
    """The console link a role is read on: an ESP32 role's own target link, or the manifest's connection for an nRF role. None if it has none."""
    spec = m["devices"][role]
    if spec["kind"] == "esp32":
        return spec["key"]
    for w in m["wiring"]:
        if w["device"] == role:
            return w["name"]
    return None


def role_attached(host, m, row):
    """(attached, why) of a plan role FOR THE PROFILE: its board is on USB, or the console link the project wires for it is configured and open. The bench RAK is powered by the
    profiler and has no USB cable while it is measured, so USB alone would leave nothing in force for ever; and the unit is silent for most of its beat, so the link counts when
    it is open, not when a line was seen lately. The plan's own `attached` (what flashing needs) is not changed."""
    if row["attached"]:
        return True, ""
    link = link_of(m, row["role"])
    state = host.console.state(link) if host.console is not None and link else None
    if state and state.get("state") == "open":
        return True, "console link %s is open" % link
    return False, row["why"] or "not attached"


def _path(host):
    folder = host.config["paths"].get("meter")
    return os.path.join(folder, UNDER_TEST) if folder else None


def read_choice(host):
    """{project, role, key} as saved, or None (no file, no folder, or a file that is not that)."""
    path = _path(host)
    if path is None:
        return None
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and all(isinstance(data.get(k), str) for k in ("project", "role", "key")) else None


def write_choice(host, project, role, key):
    path = _path(host)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".under-test-", suffix=".part")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"project": project, "role": role, "key": key}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def clear_choice(host, project=None):
    """Remove the choice (of `project` only, when given). True if a file was removed."""
    path = _path(host)
    if path is None or not os.path.exists(path):
        return False
    if project is not None and (read_choice(host) or {}).get("project") != project:
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def candidates(host, store, roles_of):
    """Every role that has a profile, of every APPLIED project whose manifest validates: {project, role, kind, key, attached, why, profile}."""
    out = []
    for name in store.names():
        entry = store.state(name)
        if not entry or entry.get("pending"):
            continue
        m, errors = mf.validate(store.read(name) or "", name)
        if errors or not m["power"]:
            continue
        roles = dict((r["role"], r) for r in roles_of(m))
        for role in sorted(m["power"]):
            r = roles[role]
            here, why = role_attached(host, m, r)
            out.append({"project": name, "role": role, "kind": r["kind"], "key": r["key"], "attached": here, "why": why, "profile": m["power"][role]})
    return out


def resolve(host, store, roles_of):
    """{in_force, candidates, under_test}. `in_force` is {project, role, kind, key, trigger, events} or None."""
    cands = candidates(host, store, roles_of)
    attached = [c for c in cands if c["attached"]]
    choice = read_choice(host)
    chosen = next((c for c in attached if choice and (c["project"], c["role"], c["key"]) == (choice["project"], choice["role"], choice["key"])), None)
    picked = chosen or (attached[0] if len(attached) == 1 else None)
    in_force = None
    if picked is not None:
        p = picked["profile"]
        in_force = {"project": picked["project"], "role": picked["role"], "kind": picked["kind"], "key": picked["key"],
                    "trigger": p["trigger"], "events": p["events"]}
    return {"in_force": in_force,
            "candidates": [dict((k, v) for k, v in c.items() if k != "profile") for c in cands],
            "under_test": {"project": chosen["project"], "role": chosen["role"]} if chosen else None}


def refusal_reason(host, store, roles_of, project, role):
    """(reason, None) when `role` of `project` cannot be named the board under test, or (None, the board's key) when it can."""
    entry = store.state(project)
    if not entry or entry.get("pending"):
        return "project %s is not applied" % project, None
    m, errors = mf.validate(store.read(project) or "", project)
    if errors:
        return "the manifest of %s does not validate" % project, None
    if role not in m["devices"]:
        return "project %s has no role %s" % (project, role), None
    if role not in m["power"]:
        return "role %s of project %s has no profile: its manifest's power section does not give it one" % (role, project), None
    row = next(r for r in roles_of(m) if r["role"] == role)
    here, how = role_attached(host, m, row)
    if not here:
        why = how if how != "not attached" else ""
        return "role %s of project %s is not attached%s" % (role, project, ": %s" % why if why else ""), None
    return None, row["key"]
