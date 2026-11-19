"""Is the Pi's Bluetooth there? (docs/specs/pi-uart-move.md) READ-ONLY: it lists the adapters the kernel has registered and the radio's block state.

`/sys/class/bluetooth/hciN` exists once the kernel has an adapter, which on a Pi means the boot overlays have left the Bluetooth chip its UART (not
`dtoverlay=disable-bt`). That is all this says: whether BlueZ has powered the adapter, or any device is in range, is the BLE slice's business. Nothing is opened.
"""
import os


def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except OSError:
        return None


def status(host):
    """{present, adapters: [hciN], blocked: True/False/None, why}. `blocked` is the rfkill soft or hard block of a Bluetooth radio (None: no such radio)."""
    base = os.path.join(host.sysfs, "class", "bluetooth")
    try:
        adapters = sorted(n for n in os.listdir(base) if n.startswith("hci") and n[3:].isdigit())
    except OSError:
        adapters = []
    blocked = None
    rf = os.path.join(host.sysfs, "class", "rfkill")
    try:
        for entry in sorted(os.listdir(rf)):
            if _read(os.path.join(rf, entry, "type")) == "bluetooth":
                blocked = bool(_read(os.path.join(rf, entry, "soft")) == "1" or _read(os.path.join(rf, entry, "hard")) == "1")
    except OSError:
        pass
    if adapters:
        why = "blocked by rfkill" if blocked else "adapter %s" % ", ".join(adapters)
    else:
        why = "no Bluetooth adapter: on a Raspberry Pi that is dtoverlay=disable-bt in the boot configuration (wiring.uart_status says what to change)"
    return {"present": bool(adapters), "adapters": adapters, "blocked": blocked, "why": why}
