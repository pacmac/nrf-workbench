"""check, show and probe (docs/specs/projects.md, "Check and expect", "project.show").

`check` compares the manifest with the live bench and the project's own state, and WAITS FOR NOTHING and sends nothing. `probe` is the one
call here that touches the bus: a one-byte read at the address the manifest expects, through `i2c.transfer`, so its pin-claim gate applies.
"""
from ..core.result import Result
from . import plan as planmod
from . import power as powermod

OK_STATES = ("ok", "seen", "not_applied")


_link_for = powermod.link_of          # one rule for which link a role is read on: the profile needs it too


def _expect_console(host, link, text):
    if host.console is None:
        return "not_seen", "the console is not running"
    if link is None:
        return "not_seen", "no connection to read this role on"
    page = host.console.since(link, 0)
    if page is None:
        return "not_seen", "no link named %s" % link
    for line in page["lines"]:
        if text in line["text"]:
            return "seen", line["text"]
    return "not_seen", "not in the %d lines held (the log is in memory: a restart empties it)" % len(page["lines"])


def check(host, m, plan, entry):
    """[{section, name, status, why}] and the summary the callers show."""
    applied = bool(entry and not entry.get("pending"))
    owned = entry.get("owns", {}) if entry else {}
    findings = []
    for i in plan["items"]:
        sec, name, act = i["section"], i["name"], i["action"]
        if sec == "requires":
            findings.append({"section": sec, "name": name, "status": "ok" if act == "have" else "unmet", "why": i["why"]})
        elif sec == "firmware":
            findings.append({"section": sec, "name": name, "status": "ok" if act == "have" else "missing", "why": i["why"]})
        elif sec == "power":                                     # information: an absent board is already the role's own problem, counted once
            findings.append({"section": sec, "name": name, "status": "ok" if act == "have" else "not_applied", "why": i["why"]})
        elif act in ("have", "use"):
            findings.append({"section": sec, "name": name, "status": "ok", "why": i["why"]})
        elif act == "create":
            key = planmod.OWNS[sec]
            was_ours = name in owned.get(key, [])
            findings.append({"section": sec, "name": name, "status": "missing" if was_ours else "not_applied",
                             "why": "this project made it and it is gone" if was_ours else "not applied yet"})
        else:                                                    # conflict or unresolved
            key = planmod.OWNS.get(sec)
            was_ours = key and name in owned.get(key, [])
            findings.append({"section": sec, "name": name, "status": "changed" if (was_ours and act == "conflict") else "not_applied" if not applied else "missing",
                             "why": i["why"]})
    for r in plan["roles"]:
        findings.append({"section": "roles", "name": r["role"], "status": "ok" if r["attached"] else "absent",
                         "why": "attached" if r["attached"] else (r["why"] or "not attached")})
    for role, text in sorted(m["expect"]["console"].items()):
        status, why = _expect_console(host, _link_for(m, role), text)
        findings.append({"section": "expect", "name": role, "status": status, "why": why})
    if m["expect"]["i2c"]:
        e = m["expect"]["i2c"]
        findings.append({"section": "expect", "name": "i2c %d 0x%02x" % (e["bus"], e["address"]), "status": "not_applied",
                         "why": "not probed by check: project.probe sends the probe"})
    if m["expect"]["meter"]:
        e = m["expect"]["meter"]
        findings.append({"section": "expect", "name": "meter %s %g to %g" % (e["function"], e["min"], e["max"]), "status": "not_applied",
                         "why": "not measured by check: project.measure reads the meter"})
    drift = [f for f in findings if f["status"] in ("missing", "changed")]
    problems = [f for f in findings if f["status"] in ("missing", "changed", "unmet", "absent")]
    return findings, {"applied": applied, "pending": bool(entry and entry.get("pending")), "drift": len(drift), "problems": len(problems),
                      "ready": applied and not problems}


def state_word(summary, entry):
    """The one line Home shows."""
    if summary["pending"]:
        return "attention", "a previous apply did not finish: release it and apply again"
    if not summary["applied"]:
        return "absent", "not applied"
    if summary["problems"] == 0:
        return "connected", "ready"
    return "attention", "%d thing%s to look at" % (summary["problems"], "" if summary["problems"] == 1 else "s")


def measure(call, m):
    """One reading of the meter against the manifest's expect.meter (docs/specs/meter.md). The reading is judged here, by the project, never by
    the meter command: `meter.read` is asked with the expected function, so a meter set to something else is refused and nothing is read. Any
    result that is not a reading (refused, failed, suspect) is returned as it came: a project is not held to a number nobody measured."""
    e = m["expect"]["meter"]
    if not e:
        return Result.refused("bad_manifest", "this project declares no expect.meter", {})
    res = call("meter.read", {"expect_function": e["function"]})
    if not res.ok:
        return res
    value, unit = res.data["value"], res.data["unit"]
    inside = e["min"] <= value <= e["max"]
    data = {"function": e["function"], "unit": unit, "value": value, "min": e["min"], "max": e["max"], "met": inside}
    return Result.success(data, "%g %s is %s %g to %g %s" % (value, unit, "inside" if inside else "OUTSIDE", e["min"], e["max"], unit))


def probe(call, m):
    e = m["expect"]["i2c"]
    if not e:
        return Result.refused("bad_manifest", "this project declares no expect.i2c", {})
    res = call("i2c.transfer", {"bus": e["bus"], "address": e["address"], "read": 1})
    if res.ok:
        return Result.success({"bus": e["bus"], "address": e["address"], "answered": True, "read": res.data["read"]},
                              "0x%02x answered on bus %d" % (e["address"], e["bus"]))
    if res.code == "no_reply":
        return Result.success({"bus": e["bus"], "address": e["address"], "answered": False},
                              "0x%02x did not answer on bus %d. A device that sleeps and wakes on SCL misses its first probe: probe it again" % (e["address"], e["bus"]))
    return res
