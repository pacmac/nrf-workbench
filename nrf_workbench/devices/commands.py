"""Registers devices.list (docs/specs/firmware.md): which boards are attached, by identity.

A caller cannot name a serial to flash without being able to read which boards exist. This reads sysfs
only: it never opens a node and never touches a board. `host` is the contract described in
programmer/commands.py (here: host.sysfs and host.profiles).
"""
from ..core.registry import Command
from ..core.result import Result
from . import identity

NEEDS = ("sysfs",)


def boards(host):
    """The attached boards, by identity: what `devices.list` returns AND what the live feed publishes, one
    function, so the two cannot disagree. Reads sysfs only."""
    inv = identity.inventory(host.sysfs)
    found = []
    for prof in host.profiles:
        for dev in identity.board_devices(prof, inv):
            found.append({"profile": prof["name"], "mode": dev["mode"], "state": identity.state(prof, dev["serial"], inv),
                          "id": dev["id"], "serial": dev["serial"], "product": dev["product"],
                          "sysfs": dev["name"], "enum": dev["enum"]})
    found.sort(key=lambda b: (b["profile"], b["serial"] or "", b["mode"]))
    return found


def _list(ctx):
    found = boards(ctx.host)
    return Result.success({"boards": found}, "%d attached" % len(found))


def register(registry, host):
    registry.add(Command("devices.list", "List the attached boards by identity (profile, mode, USB ids, serial).",
                         _list, requires=NEEDS, result="boards: profile, mode, state, id, serial, product, sysfs, enum"),
                 module=__name__)
