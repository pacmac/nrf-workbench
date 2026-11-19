"""Registers the project commands (docs/specs/projects.md) in the core registry.

A project composes the commands that already exist: every change goes through `registry.call`, so every guard of the command that makes it
still runs. Nothing here opens a port. `host` is the contract described in programmer/commands.py, plus `host.capabilities` and the console.
"""
from ..core.registry import Arg, Command, UnknownCommand, UsageError
from ..core.result import Result
from ..devices import commands as device_commands
from ..programmer import commands as programmer_commands
from . import apply as applymod
from . import check as checkmod
from . import manifest as mf
from . import plan as planmod
from .store import ProjectStore, StoreError

WIRING = ("wiring",)


def _store(host):
    return host.service("project_store", lambda: ProjectStore(host.config["paths"]["wiring"]))


def _failed(exc):
    return Result.failed("write_failed", str(exc))


def _caller(registry, ctx):
    """call(name, args) -> Result, through the registry (so every guard runs). A command this host does not have is a refusal, and a bad
    argument the manifest made is `bad_manifest`, never an exception."""
    caps = getattr(ctx.host, "capabilities", None)

    def call(name, args):
        try:
            return registry.call(name, args, ctx, capabilities=set(caps) if caps is not None else None)
        except UnknownCommand:
            return Result.refused("capability_missing", "%s is not available on this host" % name, {"needs": name})
        except UsageError as exc:
            return Result.refused("bad_manifest", "%s: %s" % (name, exc), {"arg": exc.arg})
    return call


def _firmware_names(host):
    if not host.config["paths"].get("firmware"):
        return []
    return programmer_commands._store(host).projects()


def _load(host, name):
    """(manifest, None) or (None, a refusal). An unknown name is project_unknown; text that does not validate is bad_manifest."""
    if not mf.SLUG.match(name or ""):
        raise UsageError("a project name is 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit", "project")
    text = _store(host).read(name)
    if text is None:
        return None, Result.refused("project_unknown", "there is no manifest for project %s" % name, {"project": name})
    m, errors = mf.validate(text, name)
    if errors:
        return None, Result.refused("bad_manifest", "the manifest of %s does not validate: %s" % (name, errors[0]), {"errors": errors})
    return m, None


def _plan_of(host, call, m):
    st = _store(host)
    return planmod.make_plan(host, call, m, st.owners(), st.state(m["name"]))


def _guard(fn):
    def wrapped(ctx, *a, **kw):
        try:
            return fn(ctx, *a, **kw)
        except StoreError as exc:
            return _failed(exc)
        except OSError as exc:
            return _failed(exc)
    return wrapped


# -- list and show ---------------------------------------------------------------------------------
def _summary(host, call, name):
    """One row of project.list."""
    st = _store(host)
    row = {"name": name, "purpose": "", "manifest": False, "firmware": name in _firmware_names(host), "applied": False, "state": "absent",
           "word": "firmware only" if name in _firmware_names(host) else "", "drift": 0, "page": "projects"}
    text = st.read(name)
    if text is None:
        return row
    m, errors = mf.validate(text, name)
    row["manifest"] = True
    if errors:
        row.update(state="attention", word="the manifest does not validate: %s" % errors[0])
        return row
    entry = st.state(name)
    findings, summary = checkmod.check(host, m, _plan_of(host, call, m), entry)
    state, word = checkmod.state_word(summary, entry)
    row.update(purpose=m["purpose"], applied=summary["applied"], state=state, word=word, drift=summary["drift"])
    return row


def summaries(host, registry, caller="overview"):
    """Every project's row, for project.list and for bench.overview (which has no request of its own, so it makes a context)."""
    from ..core.registry import Context
    call = _caller(registry, Context(host, caller, None))
    names = sorted(set(_store(host).names()) | set(_firmware_names(host)))
    return [_summary(host, call, n) for n in names]


def _list(registry):
    def handler(ctx):
        rows = summaries(ctx.host, registry, ctx.caller)
        return Result.success({"projects": rows}, "%d project%s" % (len(rows), "" if len(rows) == 1 else "s"))
    return _guard(handler)


def _role_view(host, m, plan, role):
    spec = m["devices"][role]
    base = next(r for r in plan["roles"] if r["role"] == role)
    link = checkmod._link_for(m, role)
    st = host.console.state(link) if host.console is not None and link else None
    fw = next((i for i in plan["items"] if i["section"] == "firmware" and i["name"] == role), None)
    boards = dict((b["serial"], b) for b in device_commands.boards(host))
    return {"role": role, "kind": spec["kind"], "board": {"key": spec["key"], "state": "connected" if base["attached"] else "absent", "why": base["why"]},
            "mode": (boards.get(spec["key"]) or {}).get("mode") if spec["kind"] == "nrf" else None,
            "console": {"link": link, "state": (st or {}).get("state"), "rx_only": (st or {}).get("rx_only"), "baud": (st or {}).get("baud")} if link else None,
            "firmware": {"id": (m["firmware"].get(role) or {}).get("id"), "in_store": bool(fw and fw["action"] == "have"), "why": fw["why"] if fw else ""}
            if role in m["firmware"] else None}


def _show(registry):
    def handler(ctx, project):
        host, call = ctx.host, _caller(registry, ctx)
        st = _store(host)
        if st.read(project) is None:
            if project in _firmware_names(host):
                return Result.success({"name": project, "manifest": False, "applied": False,
                                       "note": "this project holds firmware only; put a manifest to describe the rest"},
                                      "project %s has a firmware folder and no manifest" % project)
        m, refusal = _load(host, project)
        if refusal is not None:
            return refusal
        entry = st.state(project)
        plan = _plan_of(host, call, m)
        findings, summary = checkmod.check(host, m, plan, entry)
        state, word = checkmod.state_word(summary, entry)
        link_names = [l for l in (checkmod._link_for(m, r) for r in m["devices"]) if l]
        data = {"name": project, "purpose": m["purpose"], "notes": m["notes"], "manifest": True, "applied": summary["applied"],
                "pending": summary["pending"], "state": state, "word": word,
                "requires": [{"item": i["name"], "met": i["action"] == "have", "how": i["why"]} for i in plan["items"] if i["section"] == "requires"],
                "roles": [_role_view(host, m, plan, r) for r in sorted(m["devices"])],
                "resources": {"esp32": [e["name"] for e in m["esp32"]], "connections": [w["name"] for w in m["wiring"]],
                              "pins": [{"name": p["name"], "gpio": p["gpio"], "service": p["service"]} for p in m["pins"]],
                              "macros": [planmod.stored_macro(project, n) for n in sorted(m["macros"])], "i2c": m["expect"]["i2c"],
                              "meter": m["expect"]["meter"]},
                "expect": [f for f in findings if f["section"] == "expect"],
                "scope": {"boards": sorted(v["key"] for v in m["devices"].values()), "pins": sorted(p["gpio"] for p in m["pins"]),
                          "links": sorted(set(link_names))},
                "drift": [f for f in findings if f["status"] in ("missing", "changed", "unmet", "absent")], "plan": plan["counts"],
                "owns": (entry or {}).get("owns"), "text": _store(host).read(project)}
        return Result.success(data, "project %s: %s" % (project, word))
    return _guard(handler)


# -- manifest ---------------------------------------------------------------------------------------
def _put(ctx, project):
    if not mf.SLUG.match(project or ""):
        raise UsageError("a project name is 1 to 64 characters from a-z 0-9 . _ - , starting with a letter or a digit", "project")
    body = ctx.body
    if body is None:
        raise UsageError("this command takes the manifest as the request body")
    if body.length > mf.MAX_BYTES:
        return Result.refused("bad_manifest", "the manifest is at most %d bytes" % mf.MAX_BYTES, {"bytes": body.length})
    raw, left = b"", body.length
    while left > 0:
        chunk = body.read(min(8192, left))
        if not chunk:
            break
        raw, left = raw + chunk, left - len(chunk)
    if left:
        return Result.refused("bad_manifest", "the body ended before its declared length", {"expected": body.length, "received": len(raw)})
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Result.refused("bad_manifest", "the manifest is UTF-8 text", {})
    m, errors = mf.validate(text, project)
    if errors:
        return Result.refused("bad_manifest", "the manifest does not validate: %s" % errors[0], {"errors": errors})
    created = _store(ctx.host).read(project) is None
    _store(ctx.host).write(project, text)
    return Result.success({"project": project, "created": created, "bytes": len(raw)}, "saved" if created else "replaced")


def _delete(ctx, project):
    st = _store(ctx.host)
    if st.state(project) is not None:
        return Result.refused("project_applied", "project %s is applied: release it first" % project, {"project": project})
    if not st.delete(project):
        return Result.refused("project_unknown", "there is no manifest for project %s" % project, {"project": project})
    return Result.success({"project": project}, "manifest removed; the firmware folder is untouched")


# -- plan, apply, check, release ---------------------------------------------------------------------
def _plan(registry):
    def handler(ctx, project):
        m, refusal = _load(ctx.host, project)
        if refusal is not None:
            return refusal
        plan = _plan_of(ctx.host, _caller(registry, ctx), m)
        return Result.success(plan, "%s" % ("ready to apply: " if plan["ok"] else "blocked: ") + ", ".join(
            "%d %s" % (n, a) for a, n in sorted(plan["counts"].items())))
    return _guard(handler)


def _apply(registry):
    def handler(ctx, project):
        host, call = ctx.host, _caller(registry, ctx)
        m, refusal = _load(host, project)
        if refusal is not None:
            return refusal
        plan = _plan_of(host, call, m)
        if plan["blocking"]:
            return applymod.refusal_for(plan)
        return applymod.apply(_store(host), call, project, plan, ctx.caller)
    return _guard(handler)


def _check(registry):
    def handler(ctx, project):
        host = ctx.host
        m, refusal = _load(host, project)
        if refusal is not None:
            return refusal
        entry = _store(host).state(project)
        findings, summary = checkmod.check(host, m, _plan_of(host, _caller(registry, ctx), m), entry)
        state, word = checkmod.state_word(summary, entry)
        return Result.success(dict(summary, project=project, state=state, word=word, findings=findings), "project %s: %s" % (project, word))
    return _guard(handler)


def _release(registry):
    def handler(ctx, project):
        return applymod.release(_store(ctx.host), _caller(registry, ctx), project)
    return _guard(handler)


def _probe(registry):
    def handler(ctx, project):
        m, refusal = _load(ctx.host, project)
        if refusal is not None:
            return refusal
        return checkmod.probe(_caller(registry, ctx), m)
    return _guard(handler)


def _measure(registry):
    def handler(ctx, project):
        m, refusal = _load(ctx.host, project)
        if refusal is not None:
            return refusal
        return checkmod.measure(_caller(registry, ctx), m)
    return _guard(handler)


def _flash(registry):
    def handler(ctx, project, role, flash_bound_s, enter_bound_s=None, baud=None):
        host, call = ctx.host, _caller(registry, ctx)
        m, refusal = _load(host, project)
        if refusal is not None:
            return refusal
        entry = _store(host).state(project)
        if not entry or entry.get("pending"):
            return Result.refused("project_not_applied", "project %s is not applied: apply it first" % project, {"project": project})
        if role not in m["devices"]:
            raise UsageError("role is one of %s" % ", ".join(sorted(m["devices"])) if m["devices"] else "this project has no roles", "role")
        fw = m["firmware"].get(role)
        if fw is None:
            return Result.refused("bad_manifest", "role %s declares no firmware" % role, {"role": role})
        spec = m["devices"][role]
        if spec["kind"] == "nrf":
            if enter_bound_s is None:
                raise UsageError("enter_bound_s is required to flash an nRF board", "enter_bound_s")
            res = call("programmer.flash", {"serial": spec["key"], "firmware": fw["id"], "enter_bound_s": enter_bound_s, "flash_bound_s": flash_bound_s})
        else:
            args = {"target": spec["key"], "firmware": fw["id"], "flash_bound_s": flash_bound_s}
            if baud is not None:
                args["baud"] = baud
            res = call("esp32.flash", args)
        res.data = dict(res.data or {}, project=project, role=role)
        res.evidence = dict(res.evidence or {}, project=project, role=role)
        return res
    return _guard(handler)


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    p = Arg("project", doc="the project's name")
    add(Command("project.list", "Every project: its purpose, whether it has a manifest and a firmware folder, whether it is applied, and "
                "whether the bench has drifted from it.", _list(registry), requires=WIRING,
                result="projects (name, purpose, manifest, firmware, applied, state, word, drift)"))
    add(Command("project.show", "Everything an agent needs to start work on a project: its roles and their boards and consoles, the resources it "
                "uses, what it expects, what is in scope, and where the bench differs from it.", _show(registry), args=(p,), requires=WIRING,
                result="name, purpose, notes, applied, requires, roles, resources, expect, scope, drift, plan, text"))
    add(Command("project.put", "Save a project's manifest (YAML); the file is the request body. It is checked first and kept only if it "
                "validates.", _guard(_put), args=(p,), requires=WIRING, effect="write", body="raw", result="project, created, bytes"))
    add(Command("project.delete", "Remove a project's manifest. Refused while it is applied. The firmware folder is untouched.",
                _guard(_delete), args=(p,), requires=WIRING, effect="write", result="project"))
    add(Command("project.plan", "What applying the project would do against the live bench: create, use, have, conflict or unresolved for each "
                "resource. Changes nothing.", _plan(registry), args=(p,), requires=WIRING,
                result="items, roles, blocking, counts, ok, applied, pending"))
    add(Command("project.apply", "Make the bench match the project: ESP32 targets, connections, pin assignments and macros, through the "
                "existing commands. Refuses on a conflict. Never flashes, resets or sends.", _apply(registry), args=(p,), requires=WIRING,
                effect="write", result="created, counts"))
    add(Command("project.check", "Does the bench match the project? Requirements, owned objects, roles attached, expected console lines. "
                "Waits for nothing and sends nothing.", _check(registry), args=(p,), requires=WIRING,
                result="applied, drift, problems, ready, state, word, findings"))
    add(Command("project.release", "Remove what the project made, in reverse order. What it only uses, and its firmware, stay.",
                _release(registry), args=(p,), requires=WIRING, effect="write", result="removed"))
    add(Command("project.probe", "Send the one probe the project's expect.i2c names: a one-byte read at that address, through i2c.transfer.",
                _probe(registry), args=(p,), requires=WIRING, effect="write", result="bus, address, answered"))
    add(Command("project.measure", "Read the multimeter once against the project's expect.meter (function, min, max) and say whether the reading is "
                "inside. Reads only: the meter's setting is never changed.", _measure(registry), args=(p,), requires=WIRING,
                result="function, unit, value, min, max, met"))
    add(Command("project.flash", "Flash a role's declared firmware to its board, with the same required time limits as the flash commands. "
                "One role per call.", _flash(registry),
                args=(p, Arg("role", doc="the role in the manifest's devices"),
                      Arg("flash_bound_s", type="float", unit="s", doc="ceiling for the write, derived from the image size, as for the flash commands"),
                      Arg("enter_bound_s", type="float", required=False, unit="s", doc="nRF only: ceiling for the bootloader handover"),
                      Arg("baud", type="int", required=False, doc="ESP32 only: 115200, 230400, 460800 or 921600")),
                requires=WIRING, effect="write", result="the flash command's result, with project and role"))
