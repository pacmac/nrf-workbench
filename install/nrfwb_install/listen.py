"""Where the service listens (docs/specs/install.md, "the service: where it listens").

Standard library only: the installer runs before anything is installed, so it cannot import the product's own
configuration loader. The address rules below are therefore a small copy of the product's (core/config.py), and a
test holds the two together. Every read here is read-only: /proc and the files named.
"""
import ipaddress
import os
import re
import secrets
import socket
import time

DEFAULT = "127.0.0.1:8080"                  # the product's own default: this machine only (api.md, "listening")
SYSCTL_UNPRIVILEGED = "/proc/sys/net/ipv4/ip_unprivileged_port_start"

# 32 random bytes is 256 bits: far past what can be guessed over a network, and the same size a session id uses.
TOKEN_BYTES = 32

# After `systemctl is-active` says active the service may not have bound its port yet (Type=simple counts as started
# when the process is). A start was measured at about 0.7 s on the bench Pi (install.md, "the unit"); 3 s is 4x that, so
# a slower start still passes, and a service that never binds is a FAIL in 3 s, not a hang. The 0.1 s step is a seventh
# of the start time: it ends the wait within a tenth of a second of the bind.
BIND_CEILING_S = 3.0
BIND_STEP_S = 0.1
HTTP_TIMEOUT_S = 3.0                         # a loopback GET of one static page is milliseconds; this is a give-up


def parse(text):
    """(host, port) from 'addr:port' or '[v6addr]:port'; ValueError says what is wrong (as core/config.parse_listen)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a listen address is ADDRESS:PORT")
    text = text.strip()
    if text.startswith("["):
        host, sep, rest = text[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise ValueError("a listen address is [ADDRESS]:PORT")
        port_text = rest[1:]
    else:
        host, sep, port_text = text.rpartition(":")
        if not sep:
            raise ValueError("a listen address is ADDRESS:PORT")
    if not host:
        raise ValueError("the listen address has no host part")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError("the port must be 1 to 65535")
    return host, int(port_text)


def is_loopback(host):
    """True only for an address that cannot be reached from another machine; a host NAME is never assumed to be."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def join(host, port):
    return "[%s]:%d" % (host, port) if ":" in host else "%s:%d" % (host, port)


def unprivileged_start(path=SYSCTL_UNPRIVILEGED):
    """The lowest port an unprivileged process may bind on this machine, or None when it cannot be read."""
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def needs_bind_capability(port, runs_as_root, limit):
    """A non-root service needs CAP_NET_BIND_SERVICE to bind below the unprivileged limit. Root does not, and a limit
    that cannot be read is not guessed at."""
    return (not runs_as_root) and limit is not None and port < limit


def port_state(port, proc="/proc"):
    """{'listening': bool, 'holder': 'pid N: command' or None, 'visible': bool}. Reads /proc/net/tcp and tcp6 for
    listeners on the port and matches socket inodes against the process table. A holder this user cannot see is
    reported as listening with no visible holder, never as free."""
    inodes = set()
    for name in ("net/tcp", "net/tcp6"):
        try:
            with open(os.path.join(proc, name)) as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:       # 0A is LISTEN
                inodes.add(f[9])
    if not inodes:
        return {"listening": False, "holder": None, "visible": True}
    wanted = set("socket:[%s]" % i for i in inodes)
    try:
        pids = sorted(int(p) for p in os.listdir(proc) if p.isdigit())
    except OSError:
        pids = []
    for pid in pids:
        try:
            names = os.listdir(os.path.join(proc, str(pid), "fd"))
        except OSError:
            continue
        for name in names:
            try:
                target = os.readlink(os.path.join(proc, str(pid), "fd", name))
            except OSError:
                continue
            if target in wanted:
                try:
                    with open(os.path.join(proc, str(pid), "cmdline"), "rb") as fh:
                        cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                except OSError:
                    cmd = ""
                return {"listening": True, "holder": "pid %d: %s" % (pid, cmd or "?"), "visible": True}
    return {"listening": True, "holder": None, "visible": False}


def state_text(port, state, runs_as_root, limit):
    """One line a person reads next to an offer: whether the port is free now, and what it needs."""
    if state["listening"]:
        who = state["holder"] if state["holder"] else "holder not visible to this user"
        text = "port %d is IN USE (%s)" % (port, who)
    else:
        text = "port %d is free now" % port
    if needs_bind_capability(port, runs_as_root, limit):
        text += "; below %d, so the service unit gets CAP_NET_BIND_SERVICE" % limit
    return text


def recorded(text):
    """What a host.yaml already says under `service:`: {'listen', 'access', 'token_file'} with only the keys found.
    A line match, because the installer has no YAML parser; a file written in flow style (service: {listen: x})
    is not read, and the caller treats that as 'nothing recorded'."""
    found, inside = {}, False
    for line in text.splitlines():
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            inside = re.match(r"service\s*:\s*(#.*)?$", line) is not None
            continue
        m = re.match(r"\s+(listen|access|token_file)\s*:\s*(.*)$", line)
        if inside and m:
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            found[m.group(1)] = value
    return found


def create_token(path):
    """Create the token file, owner-only, exclusively. Returns 'created' or 'kept'. An existing token is NEVER
    overwritten (an upgrade must not log every client out), and the value is never returned, printed or logged."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fh = open(path, "x")                                 # exclusive: fails if the file exists, even as a link
    except FileExistsError:
        return "kept"
    with fh:
        os.fchmod(fh.fileno(), 0o600)                        # owner-only BEFORE the secret is written: the file is empty until then
        fh.write(secrets.token_urlsafe(TOKEN_BYTES) + "\n")
    return "created"


def client_address(host):
    """The address a request from this machine uses to reach a service bound to `host`."""
    if host in ("0.0.0.0", ""):
        return "127.0.0.1"
    if host in ("::", "[::]"):
        return "::1"
    return host.strip("[]")


def wait_for_bind(host, port, now=time.monotonic, sleep=time.sleep):
    """True once something accepts a connection on the address, False at BIND_CEILING_S. Returns at once if it is
    already bound: the step is a retry on 'refused', not a wait for the success case."""
    end = now() + BIND_CEILING_S
    while True:
        try:
            with socket.create_connection((client_address(host), port), timeout=HTTP_TIMEOUT_S):
                return True
        except OSError:
            if now() >= end:
                return False
            sleep(BIND_STEP_S)
