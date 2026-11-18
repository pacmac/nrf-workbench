"""Registers the wiring commands (docs/specs/wiring.md, "Backend slice 1") in the core registry.

wiring.show reads; connect, change and disconnect write the bench's own connection file and then hand the console its new
links, so a change takes effect at once. Every refusal is made here, by the service, with a code the caller shows by name.
"""
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from ..devices import commands as device_commands
from ..esp32 import targets as esp_targets
from . import detect
from .store import BUILT, INTENTS, Invalid, Refused, StoreError, WiringStore, build_log, holder_of, to_link

WIRING = ("wiring",)


def _store(host):
    return host.service("wiring_store", lambda: WiringStore(host.config["paths"]["wiring"]))


def _failed(exc):
    return Result.failed("write_failed", str(exc))


def links_for(host, records):
    """The console Links for the stored connections (only the debug logs are links)."""
    ids = detect.board_ids(host.profiles)
    links = [to_link(r, ids) for r in records if r["intent"] == "log"]
    # The declared ESP32 targets each get the console link of their own USB bridge (docs/specs/esp32.md).
    try:
        taken = [l.name for l in links]
        links += [esp_targets.to_link(r, taken) for r in esp_targets.TargetStore(host.config["paths"]["wiring"]).list()]
    except esp_targets.jsonstore.StoreError:
        pass            # esp32.targets reports an unreadable targets file; it must not take the wiring links down with it
    return links


def _apply(host, records):
    """Give the console its links, now. In local mode (no broker) the list is only kept for `console.links`."""
    links = links_for(host, records)
    host.console_links = links
    if host.console is not None:
        host.console.set_links(links)


def _esp_holder(host, serial):
    """The name of the declared ESP32 target whose own bridge is this adapter, or None (docs/specs/esp32.md)."""
    if not serial:
        return None
    try:
        for r in esp_targets.TargetStore(host.config["paths"]["wiring"]).list():
            if r["key_kind"] == "serial" and r["key"] == serial:
                return r["name"]
    except esp_targets.jsonstore.StoreError:
        pass
    return None


def _show(ctx):
    host = ctx.host
    try:
        records = _store(host).list()
    except StoreError as exc:
        return _failed(exc)
    ids = detect.board_ids(host.profiles)
    detected = detect.adapters(host.sysfs, host.devroot, ids)
    for a in detected:
        h = holder_of(records, a["serial"], a["ids"]) if a["serial"] else None
        a["held_by"] = h["name"] if h else _esp_holder(host, a["serial"])
    devices = {}
    for b in device_commands.boards(host):
        if b["serial"] and b["serial"] not in devices:
            devices[b["serial"]] = {"serial": b["serial"], "model": b["profile"], "mode": b["mode"], "product": b["product"]}
    conns = []
    for r in records:
        c = dict(r)
        st = host.console.state(r["name"]) if host.console is not None and r["intent"] == "log" else None
        c["status"] = {k: st[k] for k in ("state", "reason", "node", "lines", "bytes", "last_byte_at")} if st else None
        c["can_send"] = bool(r.get("send")) and r.get("voltage_measured") is True
        conns.append(c)
    return Result.success({"devices": sorted(devices.values(), key=lambda d: d["serial"]), "adapters": detected, "connections": conns,
                           "intents": dict((i, "built" if i in BUILT else "not built") for i in INTENTS)},
                          "%d connection%s" % (len(conns), "" if len(conns) == 1 else "s"))


def _connect(ctx, device, intent, adapter=None, baud=None, send=None, voltage_measured=None, name=None):
    host = ctx.host
    if intent not in INTENTS:
        raise UsageError("intent is one of %s" % ", ".join(INTENTS), "intent")
    if intent not in BUILT:
        return Result.refused("intent_not_built", "connections of kind %s are not built yet (built: %s)" % (intent, ", ".join(BUILT)),
                              {"intent": intent})
    try:
        store = _store(host)
        records = store.list()
        ids = detect.board_ids(host.profiles)
        esp = _esp_holder(host, adapter)
        if esp is not None:
            raise Refused("adapter_taken", "that adapter is the USB bridge of the ESP32 target %s" % esp,
                          {"adapter": adapter, "held_by": esp})
        record = build_log({"device": device, "name": name, "adapter": adapter, "baud": baud, "send": send,
                            "voltage_measured": voltage_measured},
                           detect.adapters(host.sysfs, host.devroot, ids), records, ids)
        record, created = store.put(record, by=ctx.caller)
        _apply(host, store.list())
    except Invalid as exc:
        raise UsageError(str(exc), exc.arg)
    except Refused as exc:
        return Result.refused(exc.code, str(exc), exc.evidence)
    except (StoreError, OSError) as exc:
        return _failed(exc)
    return Result.success(dict(record, created=created), "connected")


def _change(ctx, name, baud=None, send=None, voltage_measured=None):
    host = ctx.host
    try:
        store = _store(host)
        records = store.list()
        existing = next((r for r in records if r["name"] == name), None)
        if existing is None:
            return Result.refused("connection_unknown", "no connection has that name", {"name": name})
        new_send = existing["send"] if send is None else send
        if voltage_measured is not None:
            measured = voltage_measured
        elif new_send is False:
            measured = None                                    # turning sending off drops the voltage question
        else:
            measured = existing.get("voltage_measured")
        ids = detect.board_ids(host.profiles)
        record = build_log({"device": existing["device"], "name": name, "adapter": existing["adapter"]["serial"],
                            "baud": existing["baud"] if baud is None else baud, "send": new_send, "voltage_measured": measured},
                           detect.adapters(host.sysfs, host.devroot, ids), [r for r in records if r["name"] != name], ids,
                           existing=existing)
        record, _created = store.put(record, by=ctx.caller)
        _apply(host, store.list())
    except Invalid as exc:
        raise UsageError(str(exc), exc.arg)
    except Refused as exc:
        return Result.refused(exc.code, str(exc), exc.evidence)
    except (StoreError, OSError) as exc:
        return _failed(exc)
    return Result.success(dict(record, created=False), "changed")


def _disconnect(ctx, name):
    host = ctx.host
    try:
        store = _store(host)
        if not store.delete(name):
            return Result.refused("connection_unknown", "no connection has that name", {"name": name})
        _apply(host, store.list())
    except (StoreError, OSError) as exc:
        return _failed(exc)
    return Result.success({"name": name}, "disconnected")


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("wiring.show", "The devices, the detected USB adapters, and the connections between them.", _show,
                requires=WIRING, result="devices, adapters (with who holds each), connections (with their live state), intents"))
    add(Command(
        "wiring.connect", "Connect a device to something and say what for. Built so far: the debug log, through a USB serial adapter.",
        _connect,
        args=(Arg("device", doc="the nRF's USB serial number (see wiring.show)"),
              Arg("intent", doc="what the connection is for: log (built); control, bus, swd, analog (not built yet)"),
              Arg("adapter", required=False, doc="log: the serial number of a detected USB adapter"),
              Arg("baud", type="int", required=False, unit="baud", doc="log: the rate the device's firmware uses; the bootloader-touch rate is always refused"),
              Arg("send", type="bool", required=False, doc="log: true to let the bench send to the device, false to only read"),
              Arg("voltage_measured", type="bool", required=False,
                  doc="log, only when send is true: true if someone measured the adapter's signal voltage; until then nothing is sent"),
              Arg("name", required=False, doc="a slug; it is the console link's name. Default: debug- and the first four of the serial")),
        requires=WIRING, effect="write", result="the connection, and whether it was new"))
    add(Command(
        "wiring.change", "Change a debug-log connection's baud, whether the bench may send, or its voltage statement.", _change,
        args=(Arg("name", doc="the connection's name"),
              Arg("baud", type="int", required=False, unit="baud", doc="the new rate"),
              Arg("send", type="bool", required=False, doc="true or false: may the bench send"),
              Arg("voltage_measured", type="bool", required=False, doc="true if the voltage has been measured")),
        requires=WIRING, effect="write", result="the connection"))
    add(Command("wiring.disconnect", "Remove a connection. The console stops reading its adapter at once.", _disconnect,
                args=(Arg("name", doc="the connection's name"),), requires=WIRING, effect="write", result="the name removed"))
