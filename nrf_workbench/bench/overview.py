"""bench.overview: the whole bench in one read (docs/specs/dashboard-redesign.md, "bench.overview").

It GATHERS: every fact comes from the function the other commands use (`devices.boards`, the wiring store and its detection, the ESP32
targets, the I2C bus list, the pin map), so it cannot disagree with them. It opens no port and probes nothing; a section whose capability
is absent is None, with the reason in `absent`. A store that cannot be read costs its own section, never the whole answer.
"""
import os

from ..core import jsonstore
from ..devices import commands as device_commands
from ..devices import names as device_names
from ..esp32 import targets as esp_targets
from ..gpio import commands as gpio_commands
from ..gpio import header
from ..i2c import bus as i2cbus
from ..i2c import commands as i2c_commands
from ..wiring import commands as wiring_commands
from ..wiring import detect, piuart
from . import bluetooth

CONNECTED, ABSENT, ATTENTION = "connected", "absent", "attention"
LISTENING = "listening"


def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read().strip().strip("\0")
    except (OSError, UnicodeDecodeError):
        return None


def host_facts(host):
    up = _read(os.path.join(host.proc, "uptime"))
    try:
        uptime = float(up.split()[0]) if up else None
    except (ValueError, IndexError):
        uptime = None
    return {"model": _read(os.path.join(host.proc, "device-tree", "model")), "uptime_s": uptime,
            "capabilities": sorted(getattr(host, "capabilities", None) or [])}


def _have(host, cap):
    caps = getattr(host, "capabilities", None)
    return cap in caps if caps is not None else bool(host.config["paths"].get({"wiring": "wiring", "gpio": "gpio"}.get(cap, cap)))


def _row(kind, key, name, label, state, why="", **extra):
    row = {"id": "%s:%s" % (kind, key), "kind": kind, "name": name, "label": label, "state": state, "why": why,
           "product": None, "serial": None, "mode": None, "node": None, "slot": None, "connections": [], "page": "devices"}
    row.update(extra)
    return row


def _connections(host, errors):
    if not _have(host, "wiring"):
        return None, {}
    try:
        records = wiring_commands._store(host).list()
    except jsonstore.StoreError as exc:
        errors["connections"] = str(exc)
        return [], {}
    adapters = dict((a["serial"], a) for a in wiring_commands._detected(host, detect.board_ids(host.profiles)))
    labels = device_names.names_for(host)
    out, by_device = [], {}
    for r in records:
        if r.get("intent") != "log":
            continue
        st = host.console.state(r["name"]) if host.console is not None else None
        raw = (st or {}).get("state")
        state = LISTENING if raw == "open" else ABSENT if raw in (None, "absent") else ATTENTION
        pin_state = wiring_commands.lines_of(host, r)           # a Pi UART connection's pins, read now (docs/specs/line-release.md)
        why = (st or {}).get("reason") or ("" if st else "the console is not running")
        if pin_state is not None and pin_state["state"] in ("floating", "mixed"):
            state, why = ATTENTION, "the Pi's lines are released (%s): engage them to read again" % pin_state["state"]
        serial = r["adapter"]["serial"]
        a = adapters.get(serial)
        pi_pins = r.get("pins") or ()
        via = ("Pi UART (%s)" % piuart.header_text(*pi_pins[:2]) if len(pi_pins) >= 2 else "Pi UART") if r["adapter"]["ids"][0] == detect.PI_IDS \
            else (a["name"] if a else serial)
        out.append({"name": r["name"], "device": r["device"], "device_name": labels.get(r["device"]), "via": via, "adapter_serial": serial,
                    "baud": r["baud"],
                    "state": state, "why": why,
                    "rx_only": not r.get("send"), "lines": (st or {}).get("lines"), "last_byte_at": (st or {}).get("last_byte_at"),
                    "pins": r.get("pins") or [], "pin_state": pin_state["state"] if pin_state else None, "page": "wiring"})
        by_device.setdefault(r["device"], []).append(r["name"])
    return out, by_device


def _devices(host, connections, by_device, errors):
    labels = device_names.names_for(host)
    rows, seen = [], set()
    for b in device_commands.boards(host) if _have(host, "sysfs") else []:
        if b["serial"] in seen:
            continue
        seen.add(b["serial"])
        two = b["state"] == "two"
        rows.append(_row("nrf", b["serial"], b["name"], b["name"] or b["product"] or b["id"],
                         ATTENTION if two else CONNECTED, "attached in both modes at once" if two else "",
                         product=b["product"], serial=b["serial"], mode=b["mode"], slot=b["sysfs"], model=b["profile"],
                         connections=by_device.get(b["serial"], [])))
    known = set(labels) | set(by_device)
    for serial in sorted(known - seen):                    # a board the bench has been told about and cannot see now
        rows.append(_row("nrf", serial, labels.get(serial), labels.get(serial) or serial, ABSENT, "not attached", serial=serial,
                         connections=by_device.get(serial, [])))
    if _have(host, "wiring"):
        try:
            declared = esp_targets.TargetStore(host.config["paths"]["wiring"]).list()
        except jsonstore.StoreError as exc:
            errors["esp32"] = str(exc)
            declared = []
        inv = device_commands.identity.inventory(host.sysfs)
        for rec in declared:
            v = esp_targets.view(rec, inv, host.devroot)
            link = host.console.state(rec["name"]) if host.console is not None else None
            if v["attached"] == 1 and v["node"]:
                state, why = CONNECTED, ""
                if link and link.get("state") == "error":
                    state, why = ATTENTION, link.get("reason") or "its console is in error"
            elif v["attached"] == 0:
                state, why = ABSENT, "not attached"
            else:
                state, why = ATTENTION, v["problem"]
            rows.append(_row("esp32", rec["name"], rec["name"], rec["name"], state, why, product=v.get("product"),
                             serial=rec["key"] if rec["key_kind"] == "serial" else None, node=v.get("node"), slot=v.get("slot"),
                             console=(link or {}).get("state"), lines=(link or {}).get("lines"), page="firmware"))
        for n in esp_targets.native(inv, declared):
            rows.append(_row("esp32", n["serial"], None, n["product"] or "ESP32", ATTENTION,
                             "an ESP32 nobody has declared: declare it on the Firmware page to flash it", product=n["product"],
                             serial=n["serial"], slot=n["slot"], page="firmware", undeclared=True))
        used = set(c["adapter_serial"] for c in connections or [])        # an adapter a connection uses is that connection, not a free one
        ids = detect.board_ids(host.profiles)
        held_by_esp = set(r["key"] for r in declared if r["key_kind"] == "serial")
        for a in wiring_commands._detected(host, ids):
            if a["ids"] == "pi-uart":
                if a["node"] is None:
                    continue
            elif a["ids"] in esp_targets.NATIVE_IDS or a["serial"] in held_by_esp:
                continue
            if a["serial"] in used:
                continue
            state, why = (ATTENTION, a["problem"]) if a["problem"] else (CONNECTED, "free: not wired to a device yet")
            rows.append(_row("adapter", a["serial"] or a["slot"], None, a["name"], state, why, product=a["name"], serial=a["serial"],
                             node=a["node"], slot=a["slot"], page="wiring"))
    order = {ATTENTION: 0, CONNECTED: 1, ABSENT: 2}
    rows.sort(key=lambda r: (order[r["state"]], r["kind"], r["label"].lower()))
    return rows


def _i2c(host):
    if not _have(host, "gpio"):
        return None
    found = i2cbus.buses(host.devroot, host.sysfs, host.proc)
    try:
        held = dict((p["gpio"], p["service"]) for p in gpio_commands._store(host).list())
    except jsonstore.StoreError:
        held = {}
    pins = i2c_commands.BUS_PINS[1]
    return {"enabled": bool(found), "buses": [{"bus": b["bus"], "name": b["name"], "clock_hz": b["clock_hz"]} for b in found],
            "pins_claimed": all(held.get(g) == i2c_commands.SERVICE for g in pins)}


def _header(host):
    if not _have(host, "gpio"):
        return None
    try:
        assigned = dict((p["gpio"], p) for p in gpio_commands._store(host).list())
    except jsonstore.StoreError:
        assigned = {}
    held = wiring_commands.pins_held(host)
    rows = []
    for r in header.table(host.config["pins"]["reserved"]):
        g = r["gpio"]
        row = dict(r, holder=None, holder_kind=None, service=None)
        if g in held:
            row.update(holder=held[g], holder_kind="connection", service="console")
        elif g in assigned:
            row.update(holder=assigned[g]["name"], holder_kind="pin-map", service=assigned[g]["service"])
        rows.append(row)
    return rows


def _projects(host, errors):
    """The projects that have a manifest (a firmware-only folder is not shown on Home), or None where the host has no wiring folder."""
    registry = getattr(host, "registry", None)
    if registry is None or not _have(host, "wiring"):
        return None
    from ..project import commands as project_commands
    try:
        return [p for p in project_commands.summaries(host, registry) if p["manifest"]]
    except jsonstore.StoreError as exc:
        errors["projects"] = str(exc)
        return []


def overview(host):
    errors, absent = {}, {}
    projects = _projects(host, errors)
    connections, by_device = _connections(host, errors)
    if connections is None:
        absent["connections"] = "no wiring folder is configured (paths.wiring)"
    devices = _devices(host, connections, by_device, errors)
    i2c, hdr = _i2c(host), _header(host)
    if i2c is None:
        absent["i2c"] = absent["header"] = "this host has no pin map (a Raspberry Pi with paths.gpio)"
    counts = {"devices": len(devices), "connected": sum(1 for d in devices if d["state"] == CONNECTED),
              "absent": sum(1 for d in devices if d["state"] == ABSENT),
              "attention": sum(1 for d in devices if d["state"] == ATTENTION),
              "links": len(connections or []), "listening": sum(1 for c in connections or [] if c["state"] == LISTENING),
              "links_attention": sum(1 for c in connections or [] if c["state"] == ATTENTION),
              "projects": len(projects or []), "projects_attention": sum(1 for p in projects or [] if p["state"] == ATTENTION)}
    return {"host": host_facts(host), "devices": devices, "connections": connections, "projects": projects, "i2c": i2c, "header": hdr,
            "bluetooth": bluetooth.status(host), "counts": counts, "absent": absent, "errors": errors}
