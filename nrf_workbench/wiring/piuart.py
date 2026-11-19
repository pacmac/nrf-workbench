"""The Pi's own UARTs, told apart by hardware and not by name (docs/specs/pi-uart-move.md).

Which `ttyAMAn` is which PL011, and so which header pins it is on, depends on the boot overlays: `ttyAMA0` is uart0 on GPIO14/15 only when Bluetooth has been
given up (`dtoverlay=disable-bt`), and `dtoverlay=uart5` makes a PL011 on GPIO12/13 that is `ttyAMA1` or another number. So the port name decides nothing. The
device does: `/sys/class/tty/<port>/device` is a link whose name starts with the PL011's physical base address, and that address is looked up here.

READ-ONLY: it reads a link in sysfs and opens nothing.
"""
import os

from ..gpio import header

# PL011 base address (the start of the sysfs device name) -> (its name, TX pin, RX pin, pin function when claimed). Addresses are the device tree's
# (`serial@7e201a00` is uart5, seen on the bench Pi 4B 2026-11-19; the kernel names them fe20xxxx). The pins are the dtoverlay help text's (uart5 = GPIO
# 12-15, TX 12 and RX 13). The function codes are `a0` for uart0 and `a4` for uart2 to uart5 FROM THE BCM2711 PIN TABLE, not yet read off the Pi: engage
# checks the function NAME the pin reports (below), so a wrong code is a failure and not a silent success.
UARTS = {
    "fe201000": ("uart0", 14, 15, "a0"),
    "fe201400": ("uart2", 0, 1, "a4"),
    "fe201600": ("uart3", 4, 5, "a4"),
    "fe201800": ("uart4", 8, 9, "a4"),
    "fe201a00": ("uart5", 12, 13, "a4"),
}

# What each UART pin is when the UART owns it: (function, the pull it has then, the name `pinctrl get` prints after the `//`). TX has no pull and RX the
# pull-up, as uart0 was measured on the Pi 2026-11-18 (`a0 pn` / `a0 pu`); the others follow the overlay's pull setting [derived, to be read from the Pi
# after the reboot]. The name is how engage proves it really got the UART and not some other function that happens to share a code.
ENGAGED = {}
for _addr, (_name, _tx, _rx, _alt) in UARTS.items():
    suffix = _name[-1]
    ENGAGED[_tx] = (_alt, "pn", "TXD" + suffix)
    ENGAGED[_rx] = (_alt, "pu", "RXD" + suffix)


def uart_of(sysfs, port):
    """{name, tx, rx, alt, port} for the PL011 behind `port`, or None when the port is not a UART this table knows."""
    try:
        target = os.path.basename(os.path.realpath(os.path.join(sysfs, "class", "tty", port, "device")))
    except OSError:
        return None
    entry = UARTS.get(target.split(".")[0])
    if entry is None:
        return None
    name, tx, rx, alt = entry
    return {"name": name, "tx": tx, "rx": rx, "alt": alt, "port": port}


def pins_of(sysfs, port):
    """(TX, RX) in BCM numbers, or () for a port that is not a known UART."""
    u = uart_of(sysfs, port)
    return (u["tx"], u["rx"]) if u else ()


def header_text(tx, rx):
    """'header pins 32 and 33' for BCM pins, from the header's own table."""
    return "header pins %d and %d" % (header.physical(tx), header.physical(rx))
