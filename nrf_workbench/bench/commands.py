"""Registers bench.overview (docs/specs/dashboard-redesign.md): the whole bench in one read."""
from ..core.registry import Command
from ..core.result import Result
from . import overview as ov


def _overview(ctx):
    data = ov.overview(ctx.host)
    c = data["counts"]
    return Result.success(data, "%d of %d devices connected, %d needs attention, %d of %d links listening"
                          % (c["connected"], c["devices"], c["attention"], c["listening"], c["links"]))


def register(registry, host):
    registry.add(Command("bench.overview", "Everything on the bench in one read: the devices (nRF, ESP32, adapters) and whether each is "
                         "connected, the wired connections and their state, the I2C bus, the Pi header and who holds each pin, and the "
                         "Pi itself. Opens nothing and probes nothing.", _overview, requires=("sysfs",),
                         result="host, devices (state: connected|absent|attention), connections, i2c, header, counts, absent, errors"),
                 module=__name__)
