"""USB serial adapters the host can see (docs/specs/wiring.md, "Backend slice 1"). READ-ONLY: it reads sysfs and stat()s a
node, and never opens one. An adapter is any USB device with a serial-port node that is not a board."""
import os
import stat

from ..console import links as linkmod
from ..devices import identity
from ..guards import checks
from . import piuart


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


def pi_ports(sysfs, devroot):
    """[port] for each `ttyAMAn` character node whose PL011 the bench knows (wiring/piuart.py), in name order. A PL011 that is not a header UART (or a node
    sysfs cannot place) is not listed: the bench never guesses which pins a port is on."""
    try:
        names = sorted(n for n in os.listdir(devroot) if linkmod.PI_PORT.match(n))
    except OSError:
        return []
    ports = []
    for port in names:
        try:
            is_char = stat.S_ISCHR(os.stat(os.path.join(devroot, port)).st_mode)
        except OSError:
            is_char = False
        if is_char and piuart.uart_of(sysfs, port):
            ports.append(port)
    return ports


def pi_uarts(sysfs, devroot, proc, pin_services=None):
    """The Pi's own UARTs as adapter rows (docs/specs/pi-uart.md, pi-uart-move.md), so the Wiring page offers them like any adapter. One row per UART that is
    enabled and known (no node, no row: `wiring.uart_status` says how to enable one). `pin_services` is {BCM number: the service the pin map gives it}. Each row
    carries its `pins` (TX, RX in BCM numbers). READ-ONLY: it stat()s nodes, reads sysfs and /proc, and opens nothing. A `problem` says why a row cannot be
    connected: something else holds the node (the login console), or its pins belong to another service."""
    pin_services = pin_services or {}
    rows = []
    for port in pi_ports(sysfs, devroot):
        node = os.path.join(devroot, port)
        uart = piuart.uart_of(sysfs, port)
        pins = (uart["tx"], uart["rx"])
        problem, code = "", None
        held = checks.holders(node, proc)
        other = sorted(set(pin_services.get(g) for g in pins if pin_services.get(g) not in (None, "console")))
        if held:
            problem = "it is held by %s (is the serial login on it? wiring.uart_status says what to switch off)" % "; ".join(
                "pid %d (%s)" % h for h in held)
            code = "port_held"
        elif other:
            problem = "its header pins are assigned to another service in the pin map: %s" % ", ".join(other)
            code = "pin_taken"
        where = piuart.header_text(*pins)
        rows.append({"serial": PI_PREFIX + port, "ids": PI_IDS, "name": "Raspberry Pi serial port %s (%s)" % (uart["name"], where),
                     "slot": where.replace("header ", ""), "node": node, "problem": problem, "problem_code": code, "pins": list(pins)})
    return rows
