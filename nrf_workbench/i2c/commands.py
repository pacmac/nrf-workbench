"""Registers the I2C commands (docs/specs/i2c.md): i2c.buses, i2c.transfer, i2c.scan.

`host.hooks["i2c_transfer"]` is the test seam for the one function that opens a node (`i2cbus.transfer`); `host.devroot`,
`host.sysfs` and `host.proc` are the trees to read.
"""
from ..core.registry import Arg, Command, UsageError
from ..core.result import Result
from ..gpio import commands as gpio_commands
from ..gpio.pinmap import PinStoreError
from . import bus as i2cbus

I2C = ("gpio",)                    # a pin-map folder on a Raspberry Pi: the same capability the pin map has
# The SoC pins each known bus uses, SDA then SCL (BCM numbers). Bus 1 is the 40-pin header's pins 3 and 5.
BUS_PINS = {1: (2, 3)}
SERVICE = "i2c"


def _failed(exc):
    return Result.failed("write_failed", str(exc))


def _node(host, n):
    found = [b for b in i2cbus.buses(host.devroot, host.sysfs, host.proc) if b["bus"] == n]
    return found[0]["node"] if found else None


def _absent(n):
    return Result.refused("node_absent", "there is no /dev/i2c-%d (is I2C enabled? add %s to the boot config and reboot)"
                          % (n, i2cbus.CONFIG_LINE), {"bus": n})


def _gate(host, n):
    """None if the pin map says the bus's pins are claimed for I2C, else the refusal. (docs/specs/i2c.md)

    No MEASURED voltage is asked: the Pi header is 3.3 V by design (and the pin map refuses a signal above 3.3 V), and the bus's
    pull-ups are the Pi's own, to 3.3 V. That is unlike a USB serial adapter, whose 3.3 or 5 V is a jumper nobody can read."""
    pins = BUS_PINS.get(n)
    if pins is None:
        return Result.refused("i2c_pins_unclaimed", "bus %d has no known pins, so there is no claim to check" % n, {"bus": n})
    try:
        held = dict((p["gpio"], p) for p in gpio_commands._store(host).list())
    except PinStoreError as exc:
        return _failed(exc)
    missing = [g for g in pins if not (held.get(g) and held[g]["service"] == SERVICE)]
    if missing:
        return Result.refused("i2c_pins_unclaimed", "bus %d uses BCM %s: each must be assigned (gpio.assign) with service %s "
                              "before the bench talks on it; unclaimed: %s"
                              % (n, " and ".join(str(g) for g in pins), SERVICE, ", ".join(str(g) for g in missing)),
                              {"bus": n, "pins": list(pins), "unclaimed": missing})
    return None


def _buses(ctx):
    host = ctx.host
    found = i2cbus.buses(host.devroot, host.sysfs, host.proc)
    if not found:
        return Result.success({"buses": [], "i2c_enabled": False, "to_enable": i2cbus.CONFIG_LINE},
                              "no /dev/i2c-* on this host: I2C is off. Add %s to the boot config and reboot." % i2cbus.CONFIG_LINE)
    return Result.success({"buses": found, "i2c_enabled": True, "to_enable": None},
                          "%d bus%s" % (len(found), "" if len(found) == 1 else "es"))


def _address(value, arg="address"):
    if not (i2cbus.ADDR_FIRST <= value <= i2cbus.ADDR_LAST):
        raise UsageError("%s is a 7-bit address, 0x%02x to 0x%02x (the others are reserved)"
                         % (arg, i2cbus.ADDR_FIRST, i2cbus.ADDR_LAST), arg)
    return value


def _transfer(ctx, bus, address, write=None, read=None):
    host = ctx.host
    _address(address)
    read = 0 if read is None else read
    if not 0 <= read <= i2cbus.MAX_BYTES:
        raise UsageError("read is 0 to %d bytes" % i2cbus.MAX_BYTES, "read")
    try:
        data = i2cbus.parse_hex(write) if write else b""
    except ValueError as exc:
        raise UsageError(str(exc), "write")
    if not data and not read:
        raise UsageError("a transfer writes, reads, or both: give write, read, or both", "write")
    node = _node(host, bus)
    if node is None:
        return _absent(bus)
    refusal = _gate(host, bus)
    if refusal is not None:
        return refusal
    do = (host.hooks or {}).get("i2c_transfer", i2cbus.transfer)
    try:
        got, secs = do(node, address, data, read)
    except i2cbus.Nak as exc:
        return Result.failed("no_reply", str(exc), {"bus": bus, "address": address, "part": exc.part})
    except OSError as exc:
        return Result.failed("write_failed", "the bus transfer failed: %s" % exc, {"bus": bus, "address": address})
    return Result.success({"bus": bus, "address": address, "written": data.hex(), "read": got.hex(), "bytes_written": len(data),
                           "bytes_read": len(got), "duration_s": round(secs, 4)}, "transferred")


def _scan(ctx, bus, first, last):
    host = ctx.host
    _address(first, "first")
    _address(last, "last")
    if first > last:
        raise UsageError("first is not after last", "first")
    node = _node(host, bus)
    if node is None:
        return _absent(bus)
    refusal = _gate(host, bus)
    if refusal is not None:
        return refusal
    do = (host.hooks or {}).get("i2c_transfer", i2cbus.transfer)
    found = []
    try:
        for address in range(first, last + 1):
            try:
                do(node, address, b"", 1)
                found.append(address)
            except i2cbus.Nak:
                continue
    except OSError as exc:
        return Result.failed("write_failed", "the scan stopped at a bus error: %s" % exc, {"bus": bus, "found": found})
    return Result.success({"bus": bus, "first": first, "last": last, "found": found, "found_hex": ["0x%02x" % a for a in found]},
                          "%d of %d addresses acknowledged" % (len(found), last - first + 1))


def register(registry, host):
    def add(command):
        registry.add(command, module=__name__)

    add(Command("i2c.buses", "List the host's I2C buses (/dev/i2c-N) and the clock each runs at; says how to turn I2C on if there "
                "are none. Opens nothing.", _buses, requires=I2C, result="buses (bus, node, name, clock_hz), i2c_enabled, to_enable"))
    add(Command("i2c.transfer", "One I2C transaction as master: write the bytes, then read N bytes after a repeated start. Either "
                "part may be left out. Needs the bus's pins assigned for i2c in the pin map.", _transfer,
                args=(Arg("bus", type="int", doc="the bus number: 1 is the header's pins 3 (SDA) and 5 (SCL)"),
                      Arg("address", type="int", doc="the 7-bit device address, 8 to 119 (0x08 to 0x77)"),
                      Arg("write", required=False, doc="hex bytes to write, such as '0a ff'; at most 256"),
                      Arg("read", type="int", required=False, doc="how many bytes to read back, 0 to 256")),
                requires=I2C, effect="write", result="bus, address, written, read (hex), bytes_written, bytes_read, duration_s"))
    add(Command("i2c.scan", "Try each address in a range with a one-byte read and list those that acknowledge. There is no default "
                "range: a probe can disturb some parts, so you say which addresses.", _scan,
                args=(Arg("bus", type="int", doc="the bus number"),
                      Arg("first", type="int", doc="the first address to try, 8 to 119"),
                      Arg("last", type="int", doc="the last address to try, 8 to 119")),
                requires=I2C, effect="write", result="bus, first, last, found, found_hex"))
