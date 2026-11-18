"""The Pi's pin registers through `pinctrl` (docs/specs/line-release.md): read what a pin is doing, float it, give it back.

`pinctrl` writes the GPIO registers directly and ignores the driver, so the state is never remembered here: it is read from the pins
each time. `host.hooks["pinctrl"]` is the test seam: a callable (args list) -> (returncode, stdout), standing in for the binary.
"""
import re
import shutil
import subprocess

# A give-up ceiling for one call, not a wait: pinctrl took 2 to 3 ms over 20 runs on the bench Pi 4; a busy 4-core Pi may take
# 50 ms to start a process, and 5 times that (0.25 s) is where something is wrong.
CEILING_S = 0.25

# What the Pi booted with on its UART pins, measured 2026-11-18 (`pinctrl get 14,15`): the engaged state. 14 is TXD0 (no pull), 15 is
# RXD0 (the Pi's pull-up). The function is `a0` on both.
PI_UART_DEFAULT = {14: "pn", 15: "pu"}
ENGAGED_FUNCTION = "a0"
FLOATING_FUNCTION = "ip"           # input
FLOATING_PULL = "pn"               # no pull

LINE = re.compile(r"^\s*(\d+):\s+(\S+)\s+(\S+)\s*\|\s*(\S+)\s*//")


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
    """{gpio: {function, pull, level}} from `pinctrl get` output; a line that is not a pin line is ignored."""
    pins = {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            pins[int(m.group(1))] = {"gpio": int(m.group(1)), "function": m.group(2), "pull": m.group(3), "level": m.group(4)}
    return pins


def get(host, gpios):
    """[{gpio, function, pull, level}] for each pin, in order, read now. PinctrlError if a pin cannot be read."""
    rc, out = _run(host, ["get", ",".join(str(g) for g in gpios)])
    pins = parse(out)
    missing = [g for g in gpios if g not in pins]
    if rc != 0 or missing:
        raise PinctrlError("pinctrl get %s said %s" % (",".join(str(g) for g in gpios), (out.strip() or "nothing")[:200]))
    return [pins[g] for g in gpios]


def state_word(pins):
    """driven (every pin on the UART function), floating (every pin an input with no pull), else mixed."""
    if all(p["function"] == ENGAGED_FUNCTION for p in pins):
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
    """Give each pin back to the UART, with the pull the Pi booted with."""
    for g in gpios:
        _set(host, g, ENGAGED_FUNCTION, PI_UART_DEFAULT.get(g, "pn"))
