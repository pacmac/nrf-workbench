"""apply and release (docs/specs/projects.md, "Plan and apply" and "Ownership").

Every change is the EXISTING command, called through the registry, so every guard it has still runs. apply never flashes, resets or sends.

There is no transaction across the four stores, so: the plan is made first and apply refuses on anything blocking; then the project's
state is written with `pending: true` and the names it is ABOUT to create (an intent log: a crash leaves a trail `release` can follow, and
the plan only lists as `create` what did not exist, so release can never remove something that was not made by this project); then each
object is made in dependency order; if one fails, what this call made is undone in reverse and the previous state is put back; only on
success is `pending` cleared.
"""
from ..core.result import Result
from . import plan as planmod
from .store import SECTIONS

# create/undo for each manifest section, in the order things are made
STEPS = (
    ("esp32", "targets", "esp32.declare", "esp32.forget", lambda a: {"name": a["name"]}),
    ("wiring", "connections", "wiring.connect", "wiring.disconnect", lambda a: {"name": a["name"]}),
    ("pins", "pins", "gpio.assign", "gpio.unassign", lambda a: {"name": a["name"]}),
    ("macros", "macros", "console.macro_set", "console.macro_delete", lambda a: {"name": a["name"]}),
)
# a delete that finds nothing is not a failure: the object is already gone
GONE = ("target_unknown", "connection_unknown", "pin_unknown", "macro_unknown")


def _owns(entry):
    return dict((s, list((entry.get("owns") or {}).get(s, []))) for s in SECTIONS)


def refusal_for(plan):
    """The Result apply returns when the plan has something blocking: the first blocker's own code, and the whole list as evidence."""
    first = plan["blocking"][0]
    code = first["code"] or "project_conflict"
    return Result.refused(code, "%s %s: %s. Nothing was changed." % (first["section"], first["name"], first["why"]),
                          {"blocking": [{"section": b["section"], "name": b["name"], "code": b["code"], "why": b["why"]} for b in plan["blocking"]],
                           "counts": plan["counts"]})


def apply(store, call, project, plan, who):
    """A Result. `plan` has no blocking item (the caller checked)."""
    old = store.state(project)
    creates = [i for i in plan["items"] if i["action"] == "create"]
    uses = {}
    for i in plan["items"]:
        if i["action"] == "use" and i["section"] in planmod.OWNS:
            uses.setdefault(planmod.OWNS[i["section"]], []).append(i["name"])
    if not creates:
        if old is None or old.get("pending"):
            store.set_state(project, {"pending": False, "owns": _owns(old or {}), "uses": uses, "applied_at": store.stamp(), "applied_by": who})
        else:
            store.set_state(project, dict(old, uses=uses))
        return Result.success({"created": [], "counts": plan["counts"], "project": project}, "nothing to do: the bench already matches the project")
    owns = _owns(old or {})
    for section, key, _c, _u, _n in STEPS:
        owns[key] += [i["name"] for i in creates if i["section"] == section and i["name"] not in owns[key]]
    store.set_state(project, {"pending": True, "owns": owns, "uses": uses, "applied_at": store.stamp(), "applied_by": who, "prior": old})
    done = []
    for section, key, make, undo, ident in STEPS:
        for i in creates:
            if i["section"] != section:
                continue
            res = call(make, i["args"])
            if res.ok:
                done.append((undo, ident(i["args"]), section, i["name"]))
                continue
            undone, undo_errors = _undo(call, done)
            store.set_state(project, old)                       # the previous entry, or none
            msg = "%s %s failed (%s): %s. Undid %d change%s made by this call." % (
                section, i["name"], res.code, res.message, len(undone), "" if len(undone) == 1 else "s")
            if undo_errors:
                msg += " %d could not be undone: run project.release, then check." % len(undo_errors)
            evidence = {"step": {"section": section, "name": i["name"], "code": res.code}, "undone": undone, "undo_errors": undo_errors,
                        "result": res.to_dict()}
            return (Result.failed("write_failed", msg, evidence) if res.cls != "refused" else Result.refused(res.code, msg, evidence))
    store.set_state(project, {"pending": False, "owns": owns, "uses": uses, "applied_at": store.stamp(), "applied_by": who})
    return Result.success({"created": [{"section": s, "name": n} for _u, _i, s, n in done], "counts": plan["counts"], "project": project},
                          "applied: %d created, the rest already there" % len(done))


def _undo(call, done):
    undone, errors = [], []
    for undo, args, section, name in reversed(done):
        res = call(undo, args)
        if res.ok or res.code in GONE:
            undone.append({"section": section, "name": name})
        else:
            errors.append({"section": section, "name": name, "code": res.code, "message": res.message})
    return undone, errors


def release(store, call, project):
    """A Result. Removes what the project owns, in reverse order, and leaves what it only uses."""
    entry = store.state(project)
    if entry is None:
        return Result.refused("project_not_applied", "project %s is not applied, so there is nothing to release" % project, {"project": project})
    owns = _owns(entry)
    removed, errors = [], []
    for section, key, _make, undo, _ident in reversed(STEPS):
        left = []
        for name in owns[key]:
            res = call(undo, {"name": name})
            if res.ok or res.code in GONE:
                removed.append({"section": section, "name": name})
            else:
                errors.append({"section": section, "name": name, "code": res.code, "message": res.message})
                left.append(name)
        owns[key] = left
    if errors:
        store.set_state(project, dict(entry, owns=owns, pending=True))
        return Result.failed("write_failed", "%d object%s could not be removed; the project stays applied for them: %s" % (
            len(errors), "" if len(errors) == 1 else "s", "; ".join("%s %s (%s)" % (e["section"], e["name"], e["code"]) for e in errors)),
            {"removed": removed, "errors": errors})
    store.set_state(project, None)
    return Result.success({"removed": removed, "project": project}, "released: %d removed" % len(removed))
