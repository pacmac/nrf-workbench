"""The Pi's pin registers through `pinctrl` (docs/specs/line-release.md): read what a pin is doing, float it, give it back.

`pinctrl` writes the GPIO registers directly and ignores the driver, so the state is never remembered here: it is read from the pins
each time. `host.hooks["pinctrl"]` is the test seam: a callable (args list) -> (returncode, stdout), standing in for the binary.
"""
import re
import shutil
import subprocess

from . import piuart

# A give-up ceiling for one call, not a wait: pinctrl took 2 to 3 ms over 20 runs on the bench Pi 4; a busy 4-core Pi may take
# 50 ms to start a process, and 5 times that (0.25 s) is where something is wrong.
CEILING_S = 0.25

# What a pin is when its UART owns it (function, pull, the name pinctrl prints) is in piuart.ENGAGED, one row per UART pin: uart0's was measured on the Pi
# 2026-11-18 (`pinctrl get 14,15`: `a0 pn` TXD0, `a0 pu` RXD0), the others follow it (docs/specs/pi-uart-move.md).
FLOATING_FUNCTION = "ip"           # input
FLOATING_PULL = "pn"               # no pull

# `14: a0    pn | hi // GPIO14 = TXD0`: the pin, its function code, its pull, its level, and (after the //) the name of what the function is.
LINE = re.compile(r"^\s*(\d+):\s+(\S+)\s+(\S+)\s*\|\s*(\S+)\s*//(?:\s*GPIO\d+\s*=\s*(\S+))?")


class PinctrlError(Exception):
    pass


def find():
    return shutil.which("pinctrl")


def _run(host, args):
    hook = host.hooks.get("pinctrl")
    if hook is not None:
        return hook(list(args))
    exe = find()
    if exe is None:
        raise PinctrlError("pinctrl is not installed")
    try:
        done = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=CEILING_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PinctrlError("pinctrl did not run: %s" % exc)
    return done.returncode, done.stdout + (("\n" + done.stderr) if done.returncode else "")


def available(host):
    return host.hooks.get("pinctrl") is not None or find() is not None


def parse(text):
    """{gpio: {function, pull, level, name}} from `pinctrl get` output; a line that is not a pin line is ignored. `name` is what pinctrl calls the
    function (TXD5, or `input`), or None when the line does not say."""
    pins = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            pins[int(m.group(1))] = {"gpio": int(m.group(1)), "function": m.group(2), "pull": m.group(3), "level": m.group(4), "name": m.group(5)}
    return pins


def get(host, gpios):
    """[{gpio, function, pull, level}] for each pin, in order, read now. PinctrlError if a pin cannot be read."""
    rc, out = _run(host, ["get", ",".join(str(g) for g in gpios)])
    pins = parse(out)
    missing = [g for g in gpios if g not in pins]
    if rc != 0 or missing:
        raise PinctrlError("pinctrl get %s said %s" % (",".join(str(g) for g in gpios), (out.strip() or "nothing")[:200]))
    return [pins[g] for g in gpios]


def _engaged(pin):
    """True when the pin is what its UART makes it: the function code AND, where pinctrl names the function, the name (TXD5 and not just `a4`)."""
    want = piuart.ENGAGED.get(pin["gpio"])
    if want is None:
        return False
    return pin["function"] == want[0] and (pin.get("name") is None or pin["name"] == want[2])


def state_word(pins):
    """driven (every pin is its UART's), floating (every pin an input with no pull), else mixed."""
    if all(_engaged(p) for p in pins):
        return "driven"
    if all(p["function"] == FLOATING_FUNCTION and p["pull"] == FLOATING_PULL for p in pins):
        return "floating"
    return "mixed"


def _set(host, gpio, *words):
    rc, out = _run(host, ["set", str(gpio)] + list(words))
    if rc != 0:
        raise PinctrlError("pinctrl set %d %s said %s" % (gpio, " ".join(words), (out.strip() or "nothing")[:200]))


def float_pins(host, gpios):
    """Make each pin an input with no pull. Raises on the first one that pinctrl refuses."""
    for g in gpios:
        _set(host, g, FLOATING_FUNCTION, FLOATING_PULL)


def engage_pins(host, gpios):
    """Give each pin back to its UART, with the function and pull a UART gives it (piuart.ENGAGED). A pin that is not a known UART pin is refused."""
    for g in gpios:
        want = piuart.ENGAGED.get(g)
        if want is None:
            raise PinctrlError("GPIO %d is not a pin any Pi UART uses, so there is no UART state to give it back" % g)
        _set(host, g, want[0], want[1])
