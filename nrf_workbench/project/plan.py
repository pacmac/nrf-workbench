"""The plan: what applying a manifest would do, worked out against the LIVE bench (docs/specs/projects.md, "Plan and apply").

READ-ONLY. It reads the four stores (ESP32 targets, wiring connections, pin map, macros), the USB inventory and the stored packages, and
writes and opens nothing. Every resource becomes one item:

    create     it is not there: apply makes it, and the project will own it
    use        it is there, identical, and not made by this project: the project depends on it and will not remove it
    have       it is there, identical, and this project owns it
    conflict   it is there and different (or its name, pin or adapter is taken): apply refuses and says who has it
    unresolved it cannot be made on this host now (an adapter is not attached, a package is not in the store)

An item is `blocking` when apply must refuse because of it. A role whose board is not attached is information, not a blocker.
"""
from ..console import commands as console_commands
from ..console import macros as macromod
from ..devices import commands as device_commands
from ..devices import identity
from ..esp32 import targets as esp_targets
from ..gpio import commands as gpio_commands
from ..gpio import pinmap
from ..i2c import bus as i2cbus
from ..programmer import commands as programmer_commands
from ..wiring import commands as wiring_commands
from ..wiring import detect, piuart
from . import manifest as mf

SECTION_ORDER = ("requires", "esp32", "wiring", "pins", "macros", "power", "firmware")
# the key of `owns` in state.json for each manifest section
OWNS = {"esp32": "targets", "wiring": "connections", "pins": "pins", "macros": "macros"}


def item(section, name, action, why="", blocking=False, code=None, args=None, **extra):
    d = {"section": section, "name": name, "action": action, "why": why, "blocking": blocking, "code": code, "args": args}
    d.update(extra)
    return d


def stored_macro(project, name):
    return "%s.%s" % (project, name)


def _owner_text(owner):
    return "project %s" % owner if owner else "made by hand"


def _norm_pin(p):
    """A pin as the pin map stores it, reduced to the fields that decide whether two are the same assignment."""
    voltage = p.get("voltage") or {"signal": p.get("voltage_v"), "verified": p.get("voltage_verified")}
    keys = ("pull", "initial", "max_pulse_ms", "drives_dut")
    return tuple([p["name"], p["gpio"], p["direction"], p["service"], p["action"], (p["purpose"] or "").strip(), p["active_low"],
                  float(voltage["signal"]), voltage["verified"]] + [p.get(k) for k in keys])


def requires_items(host, call, wanted):
    caps = getattr(host, "capabilities", None)
    out = []
    for need in wanted:
        if need == "i2c":
            ok = bool(i2cbus.buses(host.devroot, host.sysfs, host.proc))
            why = "" if ok else "no /dev/i2c-*: add %s to /boot/firmware/config.txt and reboot" % i2cbus.CONFIG_LINE
        elif need == "pi-uart":
            res = call("wiring.uart_status", {})
            d = res.data if res.ok else {}
            ok = bool(d.get("enabled") and not any(u["holders"] for u in d.get("uarts", [])) and not d.get("kernel_console_on_it"))
            why = "" if ok else (res.message if not res.ok else "the Pi's UART is not ready: run wiring.uart_status for the steps")
        else:
            ok = caps is None or need in caps
            why = "" if ok else "this host does not have %s" % need
        out.append(item("requires", need, "have" if ok else "unmet", why, blocking=not ok, code=None if ok else "capability_missing"))
    return out


def live(host):
    """The live stores, by name. A store that cannot be read raises StoreError."""
    wiring = host.config["paths"].get("wiring")
    gpio = host.config["paths"].get("gpio")
    console = host.config["paths"].get("console")
    return {
        "targets": dict((r["name"], r) for r in esp_targets.TargetStore(wiring).list()) if wiring else {},
        "connections": dict((r["name"], r) for r in wiring_commands._store(host).list()) if wiring else {},
        "pins": dict((p["name"], p) for p in gpio_commands._store(host).list()) if gpio else {},
        "macros": dict((m["name"], m) for m in console_commands._macros(host).list()) if console else {},
    }


def make_plan(host, call, m, owners, own_state):
    """The plan for manifest `m`. `owners` is {(section, name): project} over every project; `own_state` is this project's entry or None."""
    name = m["name"]
    items = requires_items(host, call, m["requires"])
    caps = getattr(host, "capabilities", None)
    have_cap = lambda c: caps is None or c in caps          # noqa: E731
    state = live(host)
    inv = identity.inventory(host.sysfs)
    attached = set(b["serial"] for b in device_commands.boards(host))
    mine = lambda sec, obj: owners.get((OWNS[sec], obj)) == name        # noqa: E731
    ids = detect.board_ids(host.profiles)
    adapters = dict((a["serial"], a) for a in wiring_commands._detected(host, ids)) if have_cap("wiring") else {}
    held_adapters = {}
    for rec in state["targets"].values():
        if rec["key_kind"] == "serial":
            held_adapters[rec["key"]] = ("target", rec["name"])
    for rec in state["connections"].values():
        held_adapters[rec["adapter"]["serial"]] = ("connection", rec["name"])
    wired_pins = wiring_commands.pins_held(host)

    # ---- ESP32 targets
    planned_targets = {}
    for e in m["esp32"]:
        key_kind, key = ("serial", e["serial"]) if "serial" in e else ("slot", e["slot"])
        existing = state["targets"].get(e["name"])
        if not have_cap("esp32") and not have_cap("wiring"):
            items.append(item("esp32", e["name"], "unresolved", "this host cannot declare ESP32 boards", True, "capability_missing"))
            continue
        if existing is not None:
            same = (existing["key"], existing["key_kind"], existing.get("baud")) == (key, key_kind, e["baud"])
            if same:
                items.append(item("esp32", e["name"], "have" if mine("esp32", e["name"]) else "use",
                                  "already declared" + ("" if mine("esp32", e["name"]) else ", by %s" % _owner_text(owners.get(("targets", e["name"]))))))
            else:
                owner = owners.get(("targets", e["name"]))
                items.append(item("esp32", e["name"], "conflict", "a target named %s exists with different settings (%s %s, baud %s), %s"
                                  % (e["name"], existing["key_kind"], existing["key"], existing.get("baud"), _owner_text(owner)),
                                  True, "project_conflict"))
            planned_targets[e["name"]] = key
            continue
        found = esp_targets.matching(inv, key_kind, key)
        taken = held_adapters.get(key) if key_kind == "serial" else None
        if taken:
            items.append(item("esp32", e["name"], "conflict", "adapter %s is already used by %s %s" % (key, taken[0], taken[1]), True, "adapter_taken"))
        elif not found:
            items.append(item("esp32", e["name"], "unresolved", "no attached USB device has %s %s" % (key_kind, key), True, "adapter_absent"))
        elif len(found) > 1:
            items.append(item("esp32", e["name"], "unresolved", "%d attached devices have %s %s: declare it by slot" % (len(found), key_kind, key),
                              True, "serial_unverifiable"))
        else:
            args = {"name": e["name"], "baud": e["baud"], key_kind: key}
            items.append(item("esp32", e["name"], "create", "declare it", args=args))
        planned_targets[e["name"]] = key

    # ---- roles
    roles = []
    for role, spec in sorted(m["devices"].items()):
        if spec["kind"] == "nrf":
            here = spec["key"] in attached
            roles.append({"role": role, "kind": "nrf", "key": spec["key"], "attached": here, "why": "" if here else "not attached"})
        else:
            rec = state["targets"].get(spec["key"])
            declared = rec is not None or spec["key"] in planned_targets
            if rec is not None:
                v = esp_targets.view(rec, inv, host.devroot)
                here, why = v["attached"] == 1 and bool(v["node"]), v["problem"]
            else:
                here, why = False, "declared by this project's apply" if declared else "no such target is declared"
            roles.append({"role": role, "kind": "esp32", "key": spec["key"], "attached": here, "why": "" if here else why})
            if not declared:
                items.append(item("esp32", spec["key"], "unresolved", "role %s names target %s, which is not declared and the manifest "
                                  "does not declare it" % (role, spec["key"]), True, "target_unknown"))

    # ---- wiring connections
    pi_claimed = set()
    for w in m["wiring"]:
        serial = m["devices"][w["device"]]["key"]
        existing = state["connections"].get(w["name"])
        pi = w["adapter"].startswith(mf.PI_PREFIX)
        if pi:
            pi_claimed.update(piuart.pins_of(host.sysfs, w["adapter"][len(mf.PI_PREFIX):]))      # from the hardware, not the name
        if existing is not None:
            same = (existing["device"], existing["adapter"]["serial"], existing["baud"], bool(existing["send"])) == (serial, w["adapter"], w["baud"], w["send"])
            owner = owners.get(("connections", w["name"]))
            if same:
                items.append(item("wiring", w["name"], "have" if mine("wiring", w["name"]) else "use", "already connected"))
            else:
                items.append(item("wiring", w["name"], "conflict", "a connection named %s exists with different settings, %s" % (w["name"], _owner_text(owner)),
                                  True, "project_conflict"))
            continue
        if not have_cap("wiring"):
            items.append(item("wiring", w["name"], "unresolved", "this host has no wiring folder", True, "capability_missing"))
            continue
        a = adapters.get(w["adapter"])
        holder = held_adapters.get(w["adapter"])
        if holder:
            items.append(item("wiring", w["name"], "conflict", "adapter %s is already used by %s %s" % (w["adapter"], holder[0], holder[1]), True, "adapter_taken"))
        elif a is None or a.get("problem"):
            items.append(item("wiring", w["name"], "unresolved", (a or {}).get("problem") or "adapter %s is not attached" % w["adapter"], True,
                              (a or {}).get("problem_code") or "adapter_absent"))
        else:
            args = {"device": serial, "intent": "log", "adapter": w["adapter"], "baud": w["baud"], "send": w["send"], "name": w["name"]}
            if w["send"] and not pi:
                args["voltage_measured"] = w["voltage_measured"]
            items.append(item("wiring", w["name"], "create", "connect %s to %s" % (w["device"], w["adapter"]), args=args))

    # ---- pins
    for p in m["pins"]:
        if not have_cap("gpio"):
            items.append(item("pins", p["name"], "unresolved", "this host has no pin map (a Raspberry Pi with paths.gpio)", True, "capability_missing"))
            continue
        try:
            pinmap.build(dict(p), {}, host.config["pins"]["reserved"])
        except pinmap.Invalid as exc:
            items.append(item("pins", p["name"], "unresolved", "%s: %s" % (exc.arg, exc), True, "bad_manifest"))
            continue
        except pinmap.Refused as exc:
            items.append(item("pins", p["name"], "unresolved", str(exc), True, exc.code))
            continue
        existing = state["pins"].get(p["name"])
        if existing is not None:
            owner = owners.get(("pins", p["name"]))
            if _norm_pin(existing) == _norm_pin(p):
                items.append(item("pins", p["name"], "have" if mine("pins", p["name"]) else "use", "already assigned"))
            else:
                items.append(item("pins", p["name"], "conflict", "a pin assignment named %s exists with different fields, %s" % (p["name"], _owner_text(owner)),
                                  True, "project_conflict"))
            continue
        clash = [o for o in state["pins"].values() if o["gpio"] == p["gpio"]]
        if clash:
            owner = owners.get(("pins", clash[0]["name"]))
            items.append(item("pins", p["name"], "conflict", "GPIO %d is already assigned to %s (%s)" % (p["gpio"], clash[0]["name"], _owner_text(owner)),
                              True, "pin_taken"))
        elif p["gpio"] in wired_pins and not (p["gpio"] in pi_claimed and p["service"] == "console"):
            items.append(item("pins", p["name"], "conflict", "GPIO %d is held by the wiring connection %s" % (p["gpio"], wired_pins[p["gpio"]]), True, "pin_taken"))
        elif p["gpio"] in pi_claimed:
            items.append(item("pins", p["name"], "conflict", "GPIO %d is used by this project's own Pi UART connection" % p["gpio"], True, "pin_taken"))
        else:
            items.append(item("pins", p["name"], "create", "assign GPIO %d for %s" % (p["gpio"], p["service"]), args=dict(p)))

    # ---- macros
    for short, text in sorted(m["macros"].items()):
        full = stored_macro(name, short)
        _lines, why = macromod.check_macro(full, text)
        if why:
            items.append(item("macros", full, "unresolved", why, True, "bad_manifest"))
            continue
        existing = state["macros"].get(full)
        if existing is None:
            items.append(item("macros", full, "create", "save it", args={"name": full, "text": text}))
        elif existing["text"].rstrip("\n") == text.rstrip("\n"):
            items.append(item("macros", full, "have" if mine("macros", full) else "use", "already saved"))
        else:
            items.append(item("macros", full, "conflict", "a macro named %s exists with other lines, %s" % (full, _owner_text(owners.get(("macros", full)))),
                              True, "project_conflict"))

    # ---- power profiles: informational, never blocks an apply. A profile is read from the manifest when needed (nothing to create or own);
    # it is in force while this project is applied and the role's board is attached, and the role list above already says whether it is.
    by_role = dict((r["role"], r) for r in roles)
    for role in sorted(m["power"]):
        here = by_role[role]["attached"]
        items.append(item("power", role, "have" if here else "unresolved",
                          "in force while the project is applied" if here else "no board attached, so this profile is not in force", False))

    # ---- firmware: informational, never blocks an apply
    store = programmer_commands._store(host) if host.config["paths"].get("firmware") else None
    for role, fw in sorted(m["firmware"].items()):
        if store is None:
            items.append(item("firmware", role, "unresolved", "this host has no firmware store", False, "capability_missing"))
            continue
        sha, refusal = store.resolve(fw["id"])
        if refusal is not None:
            items.append(item("firmware", role, "unresolved", refusal.message, False, refusal.code))
            continue
        rec = store._read_record(sha) or {}
        want = m["devices"][role]["kind"]
        is_esp = bool(rec.get("kind"))
        if (want == "esp32") != is_esp:
            items.append(item("firmware", role, "unresolved", "package %s is %s, and role %s is an %s board" % (
                sha[:8], "an ESP32 package" if is_esp else "an nRF package", role, "ESP32" if want == "esp32" else "nRF"), False, "bad_package"))
        else:
            items.append(item("firmware", role, "have", "package %s is in the store" % sha[:8], package=sha))
    blocking = [i for i in items if i["blocking"]]
    counts = {}
    for i in items:
        counts[i["action"]] = counts.get(i["action"], 0) + 1
    return {"project": name, "items": items, "roles": roles, "blocking": blocking, "counts": counts, "ok": not blocking,
            "applied": bool(own_state and not own_state.get("pending")), "pending": bool(own_state and own_state.get("pending"))}
