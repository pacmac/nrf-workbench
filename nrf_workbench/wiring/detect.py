"""USB serial adapters the host can see (docs/specs/wiring.md, "Backend slice 1"). READ-ONLY: it reads sysfs and stat()s a
node, and never opens one. An adapter is any USB device with a serial-port node that is not a board."""
import os
import stat

from ..console import links as linkmod
from ..devices import identity
from ..guards import checks


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
                    "slot": dev["name"],            # where it is plugged: how an ESP32 target is found when the serial is not unique
                    "node": node, "problem": "" if dev["serial"] else
                    "this adapter has no serial number, so it cannot be told from another one and cannot be connected"})
    out.sort(key=lambda a: (a["ids"], a["serial"] or ""))
    return out


PI_IDS = "pi-uart"
PI_PREFIX = "uart:"


def pi_uarts(devroot, proc, pin_services=None):
    """The Pi's own UARTs as adapter rows (docs/specs/pi-uart.md), so the Wiring page offers them like any adapter. `pin_services` is
    {BCM number: the service the pin map gives it}. READ-ONLY: it stat()s the node and reads /proc, and opens nothing. A `problem` says why
    a row cannot be connected: the UART is not enabled (no node), something else holds it (the login console), or its pins belong to
    another service."""
    pin_services = pin_services or {}
    rows = []
    for port, pins in sorted(linkmod.PI_UART_PINS.items()):
        node = os.path.join(devroot, port)
        problem, code = "", None
        try:
            is_char = stat.S_ISCHR(os.stat(node).st_mode)
        except OSError:
            is_char = False
        if not is_char:
            node, problem, code = None, "the Pi's UART is not enabled (wiring.uart_status says how)", "adapter_absent"
        else:
            held = checks.holders(node, proc)
            other = sorted(set(pin_services.get(g) for g in pins if pin_services.get(g) not in (None, "console")))
            if held:
                problem = "it is held by %s (is the serial login on it? wiring.uart_status says what to switch off)" % "; ".join(
                    "pid %d (%s)" % h for h in held)
                code = "port_held"
            elif other:
                problem = "its header pins are assigned to another service in the pin map: %s" % ", ".join(other)
                code = "pin_taken"
        rows.append({"serial": PI_PREFIX + port, "ids": PI_IDS, "name": "Raspberry Pi serial port (header pins 8 and 10)",
                     "slot": "pins 8, 10", "node": node, "problem": problem, "problem_code": code})
    return rows
