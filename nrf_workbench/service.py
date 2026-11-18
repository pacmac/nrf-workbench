"""The composition root (docs/specs/architecture.md): the ONE module that wires the service together.

It builds the concrete Host, loads the board profiles and the per-unit exceptions, derives which
capabilities this host has, lets each area register its commands, applies the listen rules, checks the
port, and runs uvicorn. Run it:  python -m nrf_workbench.service [--listen ADDR:PORT] ...

Safety, in the order it is applied before anything listens:
  * a non-loopback address with no token refuses to start (token_missing), unless access is `open` (a trusted LAN);
  * a port below the unprivileged limit, without root, refuses (port_privileged), the limit read from /proc;
  * a port another process holds refuses (port_in_use) and names the holder, read from /proc.
The token is never printed, logged or echoed; only whether one is required.

Exit codes: 0 ran and stopped, 1 refused to start, 2 bad configuration or usage.
"""
import argparse
import errno
import os
import socket
import sys
import threading

import yaml

from .api.app import build_app
from .api.auth import Auth
from .core import config as cfgmod
from .core.activity import ActivityLog
from .core.events import Bus, Streams
from .core.registry import Registry
from .core.result import Result
from .core.sessions import SessionStore
from .console import commands as console_commands
from .console import links as console_links
from .console.broker import Broker
from .console.port import Port
from .dashboard import server as dashboard_server
from .bench import commands as bench_commands
from .devices import commands as device_commands
from .devices import identity
from .devices import profile as profilemod
from .devices.monitor import DeviceMonitor
from .esp32 import commands as esp32_commands
from .esp32 import flash as esp32_flash
from .gpio import commands as gpio_commands
from .i2c import commands as i2c_commands
from .project import commands as project_commands
from .wiring import commands as wiring_commands
from .wiring import detect as wiring_detect
from .programmer import commands as programmer_commands
from .programmer import flash_file

# The profiles ship with the product (they are data about boards, not a host path).
PACKAGE_PROFILES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles")
# Where the built dashboard UI lives. It may not exist yet: the dashboard then serves a plain page saying so.
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "static")
UNPRIVILEGED_LIMIT = "/proc/sys/net/ipv4/ip_unprivileged_port_start"

# Browser session limits (docs/specs/dashboard.md, "Files for browser login"), each DERIVED:
#   age   a working day is 8 h; x1.5 = 12 h, so a session lives through the day it was opened in and is gone by
#         the next morning. (1/1000th test: 43 s would log the operator out mid-flash, so 12 h is not a safe extreme.)
SESSION_MAX_AGE_S = 12 * 3600
#   count one operator on a phone, a laptop and a tablet = 3 live; each re-login before expiry leaves at most one
#         stale session = 3 x (1 live + 1 stale) = 6; x2.5 = 16, so a busy week never evicts a live one.
MAX_SESSIONS = 16


class Host(object):
    """The contract programmer/commands.py documents, made concrete."""

    def __init__(self, config, profiles, units, sysfs="/sys", devroot="/dev", proc="/proc", hooks=None):
        self.config, self.profiles, self.units = config, profiles, units
        self.sysfs, self.devroot, self.proc = sysfs, devroot, proc
        self.lock_path = config["paths"]["lock"]
        self.hooks = hooks or {}
        # the console (docs/specs/console.md): the declared links, the file's own problems, and the broker that owns
        # the adapters, which only a running service has (build() sets it; the CLI's local mode leaves it None)
        self.console, self.console_links, self.console_problems = None, [], []
        self._services, self._guard = {}, threading.Lock()

    def service(self, name, factory):
        with self._guard:                       # handlers run in worker threads
            if name not in self._services:
                self._services[name] = factory()
            return self._services[name]


# -- data the host loads -------------------------------------------------------------------------
def load_profiles(directory):
    if not os.path.isdir(directory):
        raise cfgmod.ConfigError("the profile directory %s does not exist" % directory)
    profiles = [profilemod.load(os.path.join(directory, n)) for n in sorted(os.listdir(directory))
                if n.endswith((".yaml", ".yml")) and not n.startswith("_") and n != "usb_ids.yaml"]
    if not profiles:
        raise cfgmod.ConfigError("no board profile was found in %s" % directory)
    return profiles


def load_units(path):
    """Per-serial exceptions (`protected`, a declared `return_path`). A board not named is flashable."""
    if not path:
        return {}
    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError as exc:
        raise cfgmod.ConfigError("the units file %s cannot be read: %s" % (path, exc))
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise cfgmod.ConfigError("%s: expected a mapping of serial to settings" % path)
    return dict((str(k), v) for k, v in data.items())


def capabilities(host):
    caps = set()
    if os.path.isdir(os.path.join(host.sysfs, "bus", "usb", "devices")):
        caps.add("sysfs")
    if host.config["paths"]["firmware"]:
        caps.add("firmware_store")
    if host.hooks.get("tool") or flash_file.find_tool():
        caps.add("uploader")
    # The console needs its links file, a folder for its macros and a bounded log: with any of the three unset it is
    # absent, and GET /api/streams says which (no default is assumed for any of them).
    # Its links come from the wiring store when `paths.wiring` is set, else from links.yaml (docs/specs/wiring.md).
    if ((host.config["paths"]["links"] or host.config["paths"]["wiring"]) and host.config["paths"]["console"]
            and cfgmod.stream_bounds(host.config, "console") is not None):
        caps.add("console")
    # The wiring commands need only their folder: a USB adapter works on any host.
    if host.config["paths"]["wiring"]:
        caps.add("wiring")
        # esptool is what writes an ESP32; declaring a target and listing them need only the folder.
        if host.hooks.get("esp_tool") or esp32_flash.find_tool():
            caps.add("esp32")
    # The pin map needs its folder AND a Raspberry Pi: a gpiochip is not evidence of a Pi header (core.md; the Proxmox
    # host has one and is not a Pi), so the device tree's own model decides. Elsewhere the commands are absent.
    if host.config["paths"]["gpio"] and is_raspberry_pi(host.proc):
        caps.add("gpio")
    return caps


def is_raspberry_pi(proc="/proc"):
    """True only if the device tree says Raspberry Pi. Absent on x86 and on any board that is not one."""
    try:
        with open(os.path.join(proc, "device-tree", "model"), "rb") as fh:
            return b"Raspberry Pi" in fh.read()
    except OSError:
        return False


def usb_level(sysfs, devroot):
    """Every USB device sysfs lists, for the monitor: a hub and a root hub too, since a change in any is a change worth a look.
    The node is part of the level: a usb-serial adapter's tty appears a moment AFTER the device does, and a page that read the
    list in between would show it with no port and never be told otherwise."""
    return [{"slot": d["name"], "id": d["id"], "product": d["product"], "serial": d["serial"],
             "node": identity.node_for(d, devroot)[0]} for d in identity.inventory(sysfs)]


board_ids = wiring_detect.board_ids     # every USB id a board profile lists: a link on one of them is refused, never opened


def build_host(cfg, sysfs="/sys", devroot="/dev", proc="/proc", hooks=None):
    """(host, registry, capabilities): everything except the HTTP app. The CLI's local mode uses this, so
    it runs the very same commands in-process with no service."""
    profiles = load_profiles(cfg["paths"]["profiles"] or PACKAGE_PROFILES)
    host = Host(cfg, profiles, load_units(cfg["paths"]["units"]), sysfs, devroot, proc, hooks)
    registry = Registry()
    programmer_commands.register(registry, host)
    device_commands.register(registry, host)
    console_commands.register(registry, host)
    gpio_commands.register(registry, host)
    wiring_commands.register(registry, host)
    esp32_commands.register(registry, host)
    i2c_commands.register(registry, host)
    bench_commands.register(registry, host)
    project_commands.register(registry, host)
    caps = capabilities(host)
    host.capabilities = caps                      # bench.overview reports sections by what the host has
    if "console" in caps:
        if cfg["paths"]["wiring"]:
            # The connections are the declaration. links.yaml is not read, and the start-up line says so.
            try:
                host.console_links = wiring_commands.links_for(host, wiring_commands.WiringStore(cfg["paths"]["wiring"]).list())
            except wiring_commands.StoreError as exc:
                host.console_links, host.console_problems = [], [str(exc)]
            if cfg["paths"]["links"]:
                host.console_problems.append("links.yaml is not read: the wiring store holds the connections (docs/specs/wiring.md)")
        else:
            host.console_links, host.console_problems = console_links.load(cfg["paths"]["links"], board_ids(profiles))
    return host, registry, caps


def build_streams(cfg):
    """The retained streams. A stream with no configured bound is NOT started (it would be unbounded): it is
    absent, with the reason, and the service says so."""
    absent, events, activity, console = {}, None, None, None
    bounds = cfgmod.stream_bounds(cfg, "events")
    if bounds is None:
        absent["events"] = "no bound configured: set streams.events.retain and streams.events.max_age_days"
    else:
        events = Bus(*bounds)
    bounds = cfgmod.stream_bounds(cfg, "activity")
    if bounds is None:
        absent["activity"] = "no bound configured: set streams.activity.retain and streams.activity.max_age_days"
    else:
        activity = ActivityLog(*bounds)
    bounds = cfgmod.stream_bounds(cfg, "console")
    if bounds is None:
        absent["console"] = "no bound configured: set streams.console.retain (lines) and streams.console.max_age_days"
    else:
        console = Bus(*bounds)
    return Streams(events, activity, absent, console)


def bench_name(cfg):
    """What the dashboard calls this bench: the configured name, else this machine's own host name."""
    return cfg["service"]["bench_name"] or socket.gethostname()


def build(cfg, token=None, sysfs="/sys", devroot="/dev", proc="/proc", hooks=None, static_dir=STATIC_DIR):
    """(app, host, registry, capabilities) from a loaded configuration."""
    host, registry, caps = build_host(cfg, sysfs, devroot, proc, hooks)
    host.streams = build_streams(cfg)
    if "console" in caps:
        # The broker owns the adapters; the caller (main) starts and stops it. `hooks` lets a test supply the port and
        # the kernel's uevents without a real device.
        hooks = hooks or {}
        extra = {"open_uevents": hooks["open_uevents"]} if hooks.get("open_uevents") else {}
        resolve = hooks.get("console_resolve") or (lambda link: console_links.resolve(link, host.sysfs, host.devroot))
        host.console = Broker(host.console_links, resolve, hooks.get("open_port", Port), host.streams.console, **extra)
    sessions = SessionStore(MAX_SESSIONS, SESSION_MAX_AGE_S)
    host.sessions = sessions
    auth = Auth(token, cfg["service"]["access"], sessions)
    # In `open` mode nobody logs in, whether or not a token file is also configured: the page shows no login.
    login = dashboard_server.Login(token is not None and cfg["service"]["access"] != "open", auth.token_matches, sessions,
                                   cfg["service"]["access"])
    pages = dashboard_server.routes(bench_name(cfg), static_dir, auth.authorise, login)
    app = build_app(registry, host, auth, caps, host.streams, pages)
    return app, host, registry, caps


def local(config_path=None, env=None):
    """(registry, host, capabilities) for the CLI's local mode. Raises ConfigError on a bad configuration."""
    cfg, problems = cfgmod.load(required_files=[config_path] if config_path else [], env=env)
    if problems:
        raise cfgmod.ConfigError("; ".join(problems))
    host, registry, caps = build_host(cfg)
    return registry, host, caps


# -- the port --------------------------------------------------------------------------------------
def _read_int(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _listening_inodes(port, proc):
    inodes = set()
    for name in ("net/tcp", "net/tcp6"):
        try:
            with open(os.path.join(proc, name)) as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:     # 0A is LISTEN
                inodes.add(f[9])
    return inodes


def port_holder(port, proc="/proc"):
    """{pid, cmdline} of the process listening on `port`, or None when nobody is, or when the owner is
    not visible to this user. Read-only: /proc/net/tcp and the process table."""
    inodes = _listening_inodes(port, proc)
    if not inodes:
        return None
    wanted = set("socket:[%s]" % i for i in inodes)
    try:
        pids = sorted(int(p) for p in os.listdir(proc) if p.isdigit())
    except OSError:
        return None
    for pid in pids:
        fd_dir = os.path.join(proc, str(pid), "fd")
        try:
            names = os.listdir(fd_dir)
        except OSError:
            continue
        for name in names:
            try:
                target = os.readlink(os.path.join(fd_dir, name))
            except OSError:
                continue
            if target in wanted:
                try:
                    with open(os.path.join(proc, str(pid), "cmdline"), "rb") as fh:
                        cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                except OSError:
                    cmd = ""
                return {"pid": pid, "cmdline": cmd}
    return None


def check_port(host, port, euid=None, proc="/proc", limit_path=UNPRIVILEGED_LIMIT):
    """None if the service can bind (host, port); else the refusal. Nothing is assumed: the privilege
    limit and the holder are read from the machine, and the bind is actually tried."""
    euid = os.geteuid() if euid is None else euid
    limit = _read_int(limit_path)
    if limit is not None and port < limit and euid != 0:
        return Result.refused("port_privileged",
                              "port %d is below this machine's unprivileged limit (%d) and the service is not "
                              "root; choose a higher port" % (port, limit), {"port": port, "limit": limit})
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)        # as uvicorn does
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            holder = port_holder(port, proc)
            return Result.refused("port_in_use", "port %d is already in use" % port,
                                  {"port": port, "holder": holder or "not visible to this user"})
        if exc.errno == errno.EACCES:
            return Result.refused("port_privileged", "this user may not bind port %d" % port,
                                  {"port": port, "limit": limit})
        if exc.errno == errno.EADDRNOTAVAIL:
            return Result.refused("capability_missing", "this machine has no address %s" % host, {"address": host})
        raise
    finally:
        sock.close()
    return None


# -- the entry point -----------------------------------------------------------------------------------
def _parser():
    p = argparse.ArgumentParser(prog="python -m nrf_workbench.service",
                                description="Run the nrf-workbench service (HTTP API).")
    p.add_argument("--config", help="a configuration file that must exist (YAML)")
    p.add_argument("--listen", help="addr:port; with none set the service listens on 127.0.0.1:8080 only")
    p.add_argument("--access", choices=cfgmod.ACCESS_MODES, help="token (default), open-read, or open (no token at all: a trusted LAN)")
    p.add_argument("--token-file", dest="token_file", help="a file holding the token (readable only by its owner)")
    p.add_argument("--bench-name", dest="bench_name", help="what the dashboard calls this bench (default: this machine's host name)")
    p.add_argument("--firmware-dir", dest="firmware_dir", help="the firmware store directory")
    p.add_argument("--lock", help="the host lock file")
    p.add_argument("--profiles", help="a directory of board profiles (default: the ones shipped with the product)")
    p.add_argument("--units", help="a units file: per-serial settings such as protected")
    p.add_argument("--links", help="links.yaml: the declared debug links (the console needs this, --console-dir and a bounded log)")
    p.add_argument("--console-dir", dest="console_dir", help="a folder for the console's own data: the macros")
    p.add_argument("--wiring-dir", dest="wiring_dir", help="a folder for the wiring store: which adapter is wired to which device (the console reads its links from it)")
    p.add_argument("--gpio-dir", dest="gpio_dir", help="a folder for the GPIO pin map (the gpio commands exist only on a Raspberry Pi)")
    for stream, what in (("events", "the live device feed"), ("activity", "the activity log"), ("console", "the console log, in lines")):
        p.add_argument("--%s-retain" % stream, dest="%s_retain" % stream, type=int,
                       help="how many %s entries to keep. REQUIRED with --%s-max-age-days, and has no default: "
                            "without both, %s is off and says why" % (stream, stream, what))
        p.add_argument("--%s-max-age-days" % stream, dest="%s_max_age_days" % stream, type=float,
                       help="how many days to keep %s entries (see --%s-retain)" % (stream, stream))
    return p


def main(argv=None, run=None, env=None, make_monitor=DeviceMonitor):
    args = _parser().parse_args(argv)
    overrides = {"service": {}, "paths": {}, "streams": {"events": {}, "activity": {}, "console": {}}}
    for flag, section, key in (("listen", "service", "listen"), ("access", "service", "access"),
                               ("token_file", "service", "token_file"), ("bench_name", "service", "bench_name"),
                               ("firmware_dir", "paths", "firmware"),
                               ("lock", "paths", "lock"), ("profiles", "paths", "profiles"),
                               ("units", "paths", "units"), ("links", "paths", "links"),
                               ("console_dir", "paths", "console"), ("gpio_dir", "paths", "gpio"), ("wiring_dir", "paths", "wiring")):
        if getattr(args, flag):
            overrides[section][key] = getattr(args, flag)
    for stream in ("events", "activity", "console"):
        for flag, key in (("%s_retain" % stream, "retain"), ("%s_max_age_days" % stream, "max_age_days")):
            if getattr(args, flag) is not None:
                overrides["streams"][stream][key] = getattr(args, flag)
    try:
        cfg, problems = cfgmod.load(required_files=[args.config] if args.config else [], env=env, overrides=overrides)
    except cfgmod.ConfigError as exc:
        print("configuration error: %s" % exc, file=sys.stderr)
        return 2
    if problems:
        for p in problems:
            print("configuration error: %s" % p, file=sys.stderr)
        return 2

    token, note = cfgmod.read_token(cfg["service"]["token_file"])
    if cfg["service"]["token_file"] and token is None:
        print("refused to start: %s" % (note or "the token file is empty"), file=sys.stderr)
        return 1
    if note:
        print("warning: %s" % note, file=sys.stderr)
    host_addr, port = cfgmod.parse_listen(cfg["service"]["listen"])
    for check in (lambda: cfgmod.check_listen(cfg["service"]["listen"], token, cfg["service"]["access"]), lambda: check_port(host_addr, port)):
        refusal = check()                       # in order: the cheap, no-I/O rule first
        if refusal is not None:
            print("refused to start (%s): %s" % (refusal.code, refusal.message), file=sys.stderr)
            return 1
    try:
        app, host, registry, caps = build(cfg, token=token)
    except (cfgmod.ConfigError, profilemod.ProfileError) as exc:
        print("configuration error: %s" % exc, file=sys.stderr)
        return 2

    loopback = cfgmod.is_loopback(host_addr)
    print("nrf-workbench listening on %s:%d (%s%s)" % (
        host_addr, port, "loopback only" if loopback else "NETWORK, UNENCRYPTED http",
        "; OPEN: no token is asked of anyone (a browser from another site is still refused)" if cfg["service"]["access"] == "open" else
        "; token required" if token and cfg["service"]["access"] == "token" else
        "; reads open, writes need the token" if token else "; no token"), flush=True)
    print("dashboard: bench %r, UI %s" % (bench_name(cfg), "built" if os.path.isfile(os.path.join(STATIC_DIR, "index.html"))
                                         else "NOT built (a plain page says so)"), flush=True)
    print("commands: %s" % ", ".join(c.name for c in registry.commands(caps)), flush=True)
    absent = [c.name for c in registry.commands() if registry.unmet(c, caps)]
    if absent:
        print("absent here (GET /api/capabilities says why): %s" % ", ".join(absent), flush=True)
    for name in ("events", "activity", "console"):
        reason = host.streams.absent.get(name)
        print("stream %s: %s" % (name, "OFF (%s)" % reason if reason else "on"), flush=True)
    if host.console is not None:
        for link in host.console_links:
            print("console link %s: %s" % (link.name, "declared" if link.usable else "NOT USABLE (%s)" % "; ".join(p.message for p in link.problems)), flush=True)
        for problem in host.console_problems:
            print("console links file: %s" % problem, flush=True)
    if run is None:
        import uvicorn
        run = uvicorn.run
    monitor = None
    if host.streams.events is not None:
        monitor = make_monitor(lambda: device_commands.boards(host), host.streams.events.publish,
                               read_usb=lambda: usb_level(host.sysfs, host.devroot))
        monitor.start()                       # blocks on the kernel's uevents, never a timer
    if host.console is not None:
        host.console.start()                  # opens a declared adapter that is attached; one thread, no timer
    try:
        run(app, host=host_addr, port=port, log_level="info")
    finally:
        if host.console is not None:
            host.console.stop()
        if monitor is not None:
            monitor.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
