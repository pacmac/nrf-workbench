"""USB serial adapters the host can see (docs/specs/wiring.md, "Backend slice 1"). READ-ONLY: it reads sysfs and stat()s a
node, and never opens one. An adapter is any USB device with a serial-port node that is not a board."""
from ..devices import identity


def board_ids(profiles):
    """Every USB id any board profile lists: an adapter with one of these ids is a board, and is never offered."""
    ids = set()
    for prof in profiles:
        for mode in prof["modes"].values():
            ids.update(mode.get("ids") or [])
    return sorted(ids)


def adapters(sysfs, devroot, board_ids):
    """[{serial, ids, name, node, problem}] for each USB device that has a tty node and whose ids are not a board's. A device
    with no serial number is listed with the reason it cannot be connected: it cannot be told from another one."""
    out = []
    for dev in identity.inventory(sysfs):
        if dev["id"] in board_ids:
            continue
        node, why = identity.node_for(dev, devroot)
        if node is None and why.startswith("no tty interface"):
            continue                                   # a hub, a keyboard, a probe: not a serial adapter
        out.append({"serial": dev["serial"], "ids": dev["id"], "name": dev["product"] or dev["manufacturer"] or dev["id"],
                    "node": node, "problem": "" if dev["serial"] else
                    "this adapter has no serial number, so it cannot be told from another one and cannot be connected"})
    out.sort(key=lambda a: (a["ids"], a["serial"] or ""))
    return out
