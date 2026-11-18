"""Registers devices.list (docs/specs/firmware.md): which boards are attached, by identity, and devices.name / devices.unname: the
label a person gave a board (docs/specs/devices.md, "Names").

A caller cannot name a serial to flash without being able to read which boards exist. Reading is sysfs only: it never opens a node
and never touches a board. `host` is the contract described in programmer/commands.py (here: host.sysfs, host.profiles and
host.config's paths.wiring, the folder the names are kept in).
"""
from ..core import jsonstore
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from . import identity, names

NEEDS = ("sysfs",)
NAMING = ("sysfs", "wiring")


def boards(host):
    """The attached boards, by identity: what `devices.list` returns AND what the live feed publishes, one
    function, so the two cannot disagree. Reads sysfs only, and the names file. `name` is the label, or None."""
    inv = identity.inventory(host.sysfs)
    labels = names.names_for(host)
    found = []
    for prof in host.profiles:
        for dev in identity.board_devices(prof, inv):
            found.append({"profile": prof["name"], "mode": dev["mode"], "state": identity.state(prof, dev["serial"], inv),
                          "id": dev["id"], "serial": dev["serial"], "product": dev["product"],
                          "sysfs": dev["name"], "enum": dev["enum"], "name": labels.get(dev["serial"])})
    found.sort(key=lambda b: (b["profile"], b["serial"] or "", b["mode"]))
    return found


def _list(ctx):
    found = boards(ctx.host)
    return Result.success({"boards": found}, "%d attached" % len(found))


def _store(host):
    return host.service("device_names", lambda: names.NameStore(host.config["paths"]["wiring"]))


def _name(ctx, serial, name):
    host = ctx.host
    if not names.NAME.match(name or ""):
        raise UsageError("a name is 1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit", "name")
    if serial not in [b["serial"] for b in boards(host)]:
        return Result.refused("node_absent", "no attached board has that serial", {"named": serial})
    try:
        outcome = _store(host).set(serial, name)
    except (jsonstore.StoreError, OSError) as exc:
        return Result.failed("write_failed", str(exc))
    if outcome == "taken":
        return Result.refused("name_taken", "another board already has the name %s" % name, {"name": name})
    return Result.success({"serial": serial, "name": name}, "named")


def _unname(ctx, serial):
    try:
        had = _store(ctx.host).clear(serial)
    except (jsonstore.StoreError, OSError) as exc:
        return Result.failed("write_failed", str(exc))
    if not had:
        return Result.refused("target_unknown", "that serial has no name", {"serial": serial})
    return Result.success({"serial": serial}, "name removed")


def register(registry, host):
    registry.add(Command("devices.list", "List the attached boards by identity (profile, mode, USB ids, serial) and the name a "
                         "person gave each.", _list, requires=NEEDS,
                         result="boards: profile, mode, state, id, serial, product, sysfs, enum, name"), module=__name__)
    registry.add(Command("devices.name", "Give an attached board a name, kept against its USB serial. Names are unique; the "
                         "serial stays what identifies the board.", _name,
                         args=(Arg("serial", doc="the USB serial of an attached board"),
                               Arg("name", doc="1 to 64 characters from a-z 0-9 . _ - starting with a letter or a digit")),
                         requires=NAMING, effect="write", result="serial, name"), module=__name__)
    registry.add(Command("devices.unname", "Remove a board's name. The board itself is untouched.", _unname,
                         args=(Arg("serial", doc="the USB serial the name is kept against"),),
                         requires=("wiring",), effect="write", result="serial"), module=__name__)
