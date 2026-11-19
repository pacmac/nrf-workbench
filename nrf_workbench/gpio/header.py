"""The Raspberry Pi 40-pin header as read-only data (docs/specs/gpio.md, "the pin map").

A pin is named by its BCM number, which is its line offset on `gpiochip0`: verified on the bench Pi 4B, where lines 0
to 27 are `ID_SDA`, `ID_SCL`, `GPIO2` ... `GPIO27` and 28 and up are not on the header. The physical pin numbers and
the alternate functions are the header's published layout and are NOT verified here.
"""

FREE, BUS, RESERVED = "free", "bus", "reserved"
HEADER_GPIOS = range(0, 28)                      # the BCM numbers on the header; gpiochip0 has 58 lines, the rest are not wired out

# BCM 0 and 1 are the HAT ID EEPROM's bus: a HAT's identity lives there, so they are never offered.
RESERVED_GPIOS = frozenset([0, 1])
# Ordinary GPIO until the matching overlay claims them, so flagged and not refused (docs/specs/gpio.md, decision 3).
BUS_GPIOS = frozenset([2, 3, 7, 8, 9, 10, 11, 14, 15])

# (physical pin, label, BCM number or None). The labels of power and ground pins are the pin's own name.
_PINS = [
    (1, "3V3", None), (2, "5V", None), (3, "GPIO2", 2), (4, "5V", None), (5, "GPIO3", 3), (6, "GND", None),
    (7, "GPIO4", 4), (8, "GPIO14", 14), (9, "GND", None), (10, "GPIO15", 15), (11, "GPIO17", 17), (12, "GPIO18", 18),
    (13, "GPIO27", 27), (14, "GND", None), (15, "GPIO22", 22), (16, "GPIO23", 23), (17, "3V3", None), (18, "GPIO24", 24),
    (19, "GPIO10", 10), (20, "GND", None), (21, "GPIO9", 9), (22, "GPIO25", 25), (23, "GPIO11", 11), (24, "GPIO8", 8),
    (25, "GND", None), (26, "GPIO7", 7), (27, "ID_SDA", 0), (28, "ID_SCL", 1), (29, "GPIO5", 5), (30, "GND", None),
    (31, "GPIO6", 6), (32, "GPIO12", 12), (33, "GPIO13", 13), (34, "GND", None), (35, "GPIO19", 19), (36, "GPIO16", 16),
    (37, "GPIO26", 26), (38, "GPIO20", 20), (39, "GND", None), (40, "GPIO21", 21),
]

ALT = {0: "ID_SD (HAT EEPROM)", 1: "ID_SC (HAT EEPROM)", 2: "SDA1 (I2C)", 3: "SCL1 (I2C)", 4: "GPCLK0",
       7: "SPI0 CE1", 8: "SPI0 CE0", 9: "SPI0 MISO", 10: "SPI0 MOSI", 11: "SPI0 SCLK", 12: "PWM0", 13: "PWM1",
       14: "TXD0 (UART)", 15: "RXD0 (UART)", 18: "PCM CLK / PWM0", 19: "PCM FS / PWM1", 20: "PCM DIN", 21: "PCM DOUT"}


def physical(gpio):
    """The physical pin number (1 to 40) of a header GPIO, or None."""
    for pin, _label, bcm in _PINS:
        if bcm == gpio:
            return pin
    return None


def gpio_class(gpio, reserved=()):
    """free, bus or reserved for a header GPIO; None for a number that is not on the header. `reserved` is the bench's own list (config `pins.reserved`)."""
    if gpio not in HEADER_GPIOS:
        return None
    if gpio in RESERVED_GPIOS or gpio in reserved:
        return RESERVED
    return BUS if gpio in BUS_GPIOS else FREE


def table(reserved=()):
    """One row per physical pin, 1 to 40: {pin, label, kind (power|ground|gpio), gpio, class, alt}. `reserved` is the bench's own list (config `pins.reserved`)."""
    rows = []
    for pin, label, gpio in _PINS:
        kind = "gpio" if gpio is not None else ("ground" if label == "GND" else "power")
        rows.append({"pin": pin, "label": label, "kind": kind, "gpio": gpio,
                     "class": gpio_class(gpio, reserved) if gpio is not None else None, "alt": ALT.get(gpio, "") if gpio is not None else ""})
    return rows
