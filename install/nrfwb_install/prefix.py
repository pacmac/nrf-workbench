"""Where it installs. NO folder is assumed: candidates are offered, the user chooses.

/usr/share/pac is one user's preference, not the product's default; it is offered only where it
already exists on the host (docs/specs/install.md, "where it installs").
"""
import os

from . import MARKER

# Whole system directories are never an install location.
FORBIDDEN = frozenset([
    "/", "/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/var", "/boot", "/dev", "/proc",
    "/sys", "/run", "/tmp", "/home", "/root", "/opt", "/usr/share", "/usr/local", "/srv", "/mnt",
])


def _candidate_paths(host):
    home = host["home"]
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    out = []
    if os.path.isdir("/usr/share/pac"):
        out.append(("/usr/share/pac/nrf-workbench", "/usr/share/pac exists on this host"))
    out.append((os.path.join(home, "nrf-workbench"), "a folder under your home"))
    if host["root"]:
        out.append(("/opt/nrf-workbench", "system add-on location (you have the rights)"))
    else:
        out.append((os.path.join(xdg, "nrf-workbench"), "your per-user data folder"))
    return out


def existing_installs(host):
    """Candidates that already hold an install record: an upgrade target."""
    return [p for p, _why in _candidate_paths(host) if os.path.isfile(os.path.join(p, MARKER))]


def candidates(host):
    """[(path, reason)] in the order they are offered: existing installs first."""
    out, seen = [], set()
    for p in existing_installs(host):
        out.append((p, "an existing install: upgrade it"))
        seen.add(p)
    for p, why in _candidate_paths(host):
        if p not in seen:
            out.append((p, why))
    return out


def validate(path):
    """(ok, reason, normalised path). Absolute, writable, a folder that is empty or already ours."""
    if not path or not os.path.isabs(os.path.expanduser(path)):
        return False, "the install folder must be an absolute path", path
    p = os.path.normpath(os.path.expanduser(path))
    if p in FORBIDDEN:
        return False, "%s is a whole system or home directory; choose a folder inside it" % p, p
    if any(c.isspace() for c in p):
        return False, "the path must not contain spaces (a virtual environment's scripts break on them)", p
    if os.path.exists(p):
        if not os.path.isdir(p):
            return False, "%s exists and is not a folder" % p, p
        if os.listdir(p) and not os.path.isfile(os.path.join(p, MARKER)):
            return False, "%s is not empty and is not an nrf-workbench install; refusing to mix with other files" % p, p
        if not os.access(p, os.W_OK | os.X_OK):
            return False, "%s is not writable by this user" % p, p
    else:
        parent = p
        while parent and not os.path.exists(parent):
            parent = os.path.dirname(parent)
        if not parent or not os.access(parent, os.W_OK | os.X_OK):
            return False, "%s cannot be created: %s is not writable by this user" % (p, parent or "/"), p
    return True, "", p
