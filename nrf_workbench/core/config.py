"""Layered configuration and the listen rules (docs/specs/core.md "configuration", api.md "listening").

Later layers override earlier ones: built-in defaults, the host file(s), a user file, the environment,
then explicit overrides (command-line flags). YAML. **No folder is assumed**: a path that is not set is
None and the area that needs it says so (or uses its own documented default); nothing here names
/usr/share/pac, /etc or a home directory. No timeout, retry count or tolerance has a default.

A key that is not in the schema is reported as a problem, not silently ignored: a typo in a safety
setting must not look like it worked.
"""
import ipaddress
import os
import stat

import yaml

from .result import Result

DEFAULTS = {
    "service": {
        "listen": "127.0.0.1:8080",     # loopback on an unprivileged port; leaving it needs a token (api.md)
        "access": "token",              # token | open-read | open (no token at all: a trusted LAN, api.md)
        "token_file": None,
        "bench_name": None,             # shown in the dashboard's title; unset means this machine's host name
    },
    # `links` is links.yaml (the declared debug links) and `console` a folder for the console's own data (the macros):
    # with either unset there is no console (docs/specs/console.md, "slice 1").
    # `gpio` is a folder for the pin map (docs/specs/gpio.md): with it unset there are no gpio commands. `wiring` is a
    # folder for the wiring store (docs/specs/wiring.md): with it set the console reads its links from the connections.
    # `meter` is a folder for the multimeter's recordings (CSV files, docs/specs/meter-page.md): with it unset the meter can be watched but not recorded.
    "paths": {"lock": None, "profiles": None, "firmware": None, "units": None, "links": None, "console": None,
              "gpio": None, "wiring": None, "meter": None},
    # `meter` is the LAN multimeter, host or host:port (docs/specs/meter.md): unset means there are no meter commands. The address is
    # configuration and never an argument, so no caller can aim the service at another machine.
    # `ppk2` is the Nordic Power Profiler on a USB port of this host (docs/specs/ppk2.md): `auto` (the one that is attached) or its USB serial. Unset means no ppk2 commands.
    "instruments": {"meter": None, "ppk2": None},
    # `reserved` is a list of BCM pin numbers this bench never hands to anything (docs/specs/pi-uart-move.md): the pins of a UART it gave up, whose old wire
    # may still be connected to a board. gpio.assign refuses them. Empty by default: another bench may still wire those pins.
    "pins": {"reserved": []},
    # Each retained stream needs BOTH bounds and has NO default: an unbounded log is retained for ever by
    # oversight (core.md, "the event bus"). With neither set, the stream is absent and says why. The console's
    # `retain` counts LINES.
    "streams": {
        "events": {"retain": None, "max_age_days": None},
        "activity": {"retain": None, "max_age_days": None},
        "console": {"retain": None, "max_age_days": None},
        "meter": {"retain": None, "max_age_days": None},     # the multimeter's readings, counted in READINGS (docs/specs/meter-page.md)
    },
    # The console's lines kept on disk (docs/specs/power-workbench.md, "Console persistence"): megabytes kept PER LINK, no default. Unset
    # means the lines are not kept past a restart and `console.history` says so. It is its own section because the streams above count
    # lines and days and this one counts bytes.
    "console_log": {"max_mb": None},
}

SECONDS_PER_DAY = 86400

ENV = {
    "NRFWB_LISTEN": ("service", "listen"),
    "NRFWB_ACCESS": ("service", "access"),
    "NRFWB_TOKEN_FILE": ("service", "token_file"),
    "NRFWB_BENCH_NAME": ("service", "bench_name"),
    "NRFWB_LOCK": ("paths", "lock"),
    "NRFWB_PROFILES": ("paths", "profiles"),
    "NRFWB_FIRMWARE_DIR": ("paths", "firmware"),
    "NRFWB_UNITS": ("paths", "units"),
    "NRFWB_LINKS": ("paths", "links"),
    "NRFWB_CONSOLE_DIR": ("paths", "console"),
    "NRFWB_GPIO_DIR": ("paths", "gpio"),
    "NRFWB_WIRING_DIR": ("paths", "wiring"),
    "NRFWB_METER_DIR": ("paths", "meter"),
    "NRFWB_METER": ("instruments", "meter"),
    "NRFWB_PPK2": ("instruments", "ppk2"),
}

ACCESS_MODES = ("token", "open-read", "open")


class ConfigError(Exception):
    pass


def _merge(base, extra, path=""):
    """Deep-merge `extra` into a copy of `base`; returns (merged, [unknown key paths])."""
    out, unknown = dict(base), []
    for key, value in (extra or {}).items():
        where = path + key
        if key not in base:
            unknown.append(where)
            continue
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                unknown.append(where + " (expected a mapping)")
                continue
            out[key], inner = _merge(base[key], value, where + ".")
            unknown.extend(inner)
        else:
            out[key] = value
    return out, unknown


def _copy(d):
    return dict((k, _copy(v) if isinstance(v, dict) else v) for k, v in d.items())


def _read_yaml(path):
    with open(path, "r") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("%s: the top level must be a mapping" % path)
    return data


def load(files=(), required_files=(), env=None, overrides=None):
    """(config, problems). `files` are optional layers (a host file, a user file) and are skipped if
    absent; `required_files` must exist. `env` is a mapping (os.environ by default); `overrides` is a
    nested dict, the command-line layer."""
    env = os.environ if env is None else env
    cfg, problems = _copy(DEFAULTS), []
    for path, required in [(p, False) for p in files] + [(p, True) for p in required_files]:
        if not os.path.exists(path):
            if required:
                raise ConfigError("the configuration file %s does not exist" % path)
            continue
        cfg, unknown = _merge(cfg, _read_yaml(path))
        problems.extend("%s: unknown setting %s" % (path, u) for u in unknown)
    layer = {}
    for var, (section, key) in ENV.items():
        if env.get(var):
            layer.setdefault(section, {})[key] = env[var]
    cfg, unknown = _merge(cfg, layer)
    cfg, unknown = _merge(cfg, overrides or {})
    problems.extend("overrides: unknown setting %s" % u for u in unknown)
    problems.extend(validate(cfg))
    return cfg, problems


def parse_listen(text):
    """(host, port) from 'addr:port' or '[v6addr]:port'. ValueError says what is wrong."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("listen must be addr:port")
    text = text.strip()
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise ValueError("listen must be [addr]:port")
        port_text = rest[1:]
    else:
        host, sep, port_text = text.rpartition(":")
        if not sep:
            raise ValueError("listen must be addr:port")
    if not host:
        raise ValueError("listen has no address")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError("the port must be 1 to 65535")
    return host, int(port_text)


def meter_address(text):
    """(host, port) from 'host' or 'host:port'; the port defaults to the instrument's own (instruments/scpi.py). ValueError says what is wrong."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("must be host or host:port")
    text = text.strip()
    host, sep, port_text = text.rpartition(":")
    if not sep:
        return text, None
    if not host or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError("must be host or host:port, the port 1 to 65535")
    return host, int(port_text)


def is_loopback(host):
    """True only for an address that cannot be reached from another machine. A host NAME other than
    localhost is not assumed to be loopback."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate(cfg):
    problems = []
    try:
        parse_listen(cfg["service"]["listen"])
    except ValueError as exc:
        problems.append("service.listen: %s" % exc)
    if cfg["service"]["access"] not in ACCESS_MODES:
        problems.append("service.access must be one of: %s" % ", ".join(ACCESS_MODES))
    name = cfg["service"]["bench_name"]
    if name is not None and (not isinstance(name, str) or not name.strip()):
        problems.append("service.bench_name must be a non-empty string (or unset: the host name is used)")
    reserved = cfg["pins"]["reserved"]
    if not isinstance(reserved, list) or not all(isinstance(g, int) and not isinstance(g, bool) and 0 <= g <= 27 for g in reserved) \
            or len(set(reserved)) != len(reserved):
        problems.append("pins.reserved must be a list of different header GPIO numbers, 0 to 27")
    meter = cfg["instruments"]["meter"]
    if meter is not None:
        try:
            meter_address(meter)
        except ValueError as exc:
            problems.append("instruments.meter: %s" % exc)
    ppk2 = cfg["instruments"]["ppk2"]
    if ppk2 is not None and (not isinstance(ppk2, str) or not (ppk2 == "auto" or (1 <= len(ppk2) <= 64 and ppk2.isalnum()))):
        problems.append("instruments.ppk2 must be auto or the PPK2's USB serial (letters and digits)")
    mb = cfg["console_log"]["max_mb"]
    if mb is not None and (isinstance(mb, bool) or not isinstance(mb, (int, float)) or not 0 < mb < float("inf")):
        problems.append("console_log.max_mb must be a positive number of megabytes (or unset: the console's lines are not kept past a restart)")
    for name, bounds in cfg["streams"].items():
        retain, days = bounds["retain"], bounds["max_age_days"]
        if retain is None and days is None:
            continue                                   # not configured: the stream is absent, with its reason
        if retain is None or days is None:
            problems.append("streams.%s needs BOTH retain and max_age_days (a retained stream has no default "
                            "bound), or neither" % name)
            continue
        if not isinstance(retain, int) or isinstance(retain, bool) or retain < 1:
            problems.append("streams.%s.retain must be a whole number of at least 1" % name)
        if isinstance(days, bool) or not isinstance(days, (int, float)) or not days > 0 or days == float("inf"):
            problems.append("streams.%s.max_age_days must be a positive number" % name)
    return problems


def stream_bounds(cfg, name):
    """(retain, max_age_seconds) for a retained stream, or None when it is not configured."""
    b = cfg["streams"][name]
    if b["retain"] is None or b["max_age_days"] is None:
        return None
    return b["retain"], b["max_age_days"] * SECONDS_PER_DAY


def read_token(path):
    """(token or None, note or None). The token file must be readable only by its owner; a file that
    others can read is a note, because a shared token is no token."""
    if not path:
        return None, None
    try:
        st = os.stat(path)
        with open(path, "r") as fh:
            token = fh.read().strip()
    except OSError as exc:
        return None, "the token file %s cannot be read: %s" % (path, exc)
    note = None
    if stat.S_IMODE(st.st_mode) & 0o077:
        note = "the token file %s is readable by other users (mode %o)" % (path, stat.S_IMODE(st.st_mode))
    return (token or None), note


def check_listen(listen, token, access="token"):
    """None if the service may start on `listen`, else the refusal. A non-loopback address with no token
    refuses (`token_missing`) in the `token` and `open-read` modes. The `open` mode is the operator's explicit
    statement that this network is trusted (api.md, "open"): it needs no token at any address."""
    host, _port = parse_listen(listen)
    if access != "open" and not is_loopback(host) and not token:
        return Result.refused("token_missing",
                              "the service would listen beyond this machine with no token; it flashes and "
                              "power-cycles hardware, so it will not start", {"address": host})
    return None
