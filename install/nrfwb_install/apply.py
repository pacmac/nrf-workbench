"""Do the plan. Each step is idempotent and logged to <prefix>/install.log.

Never touches a serial device, never flashes, never edits boot configuration, never changes the
system Python, and never runs pip outside the virtual environment.
"""
import json
import os
import shutil
import subprocess
import sys
import time

from . import MARKER
from .plan import (SEEDS, SYSTEM_UNIT_DIR, UNIT_NAME, link_state, service_owner, sets_firmware, store_path,
                   unit_path, unit_text, venv_python)

def host_yaml_text(prefix):
    """The host configuration a fresh install starts with: it names the package store, so the service turns
    the store on (docs/specs/install.md, "the core system's own folder"). The path is a double-quoted
    JSON string, which is also a valid YAML scalar, so no character in it can break the file."""
    return ("# nrf-workbench host configuration. Created by the installer, never overwritten.\n"
            "# The package store: where uploaded firmware lives, one folder per project (docs/specs/firmware.md).\n"
            "# Other settings: docs/specs/core.md.\n"
            "paths:\n"
            "  firmware: %s\n" % json.dumps(store_path(prefix)))


CONFIG_FILES = {
    "units.yaml": ("# Per-serial exceptions only (for example protected: true). A board not named here\n"
                   "# is flashable by default; this is not a roster.\nunits: {}\n"),
    "links.yaml": ("# Debug links. NONE is declared: nothing is wired to the board yet, and declaring a\n"
                   "# link would claim a wire that is not there. See docs/specs/console.md.\nlinks: []\n"),
}
# Top-level names never copied over an existing install: the user's, or generated.
KEEP = frozenset([".git", "venv", "venv.old", "config", "probes", ".venv-try", "__pycache__", "install.log", MARKER])


class ApplyError(Exception):
    pass


class Log(object):
    def __init__(self, prefix):
        os.makedirs(prefix, exist_ok=True)
        self.path = os.path.join(prefix, "install.log")

    def write(self, text):
        with open(self.path, "a") as fh:
            fh.write(text + "\n")

    def stamp(self, text):
        self.write("%s  %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), text))


def run_logged(cmd, log, ui, cwd=None, env=None):
    """Run a command, stream its WHOLE output to the screen and the log, return (rc, output)."""
    log.write("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True, cwd=cwd, env=env)
    chunks = []
    for line in proc.stdout:
        chunks.append(line)
        log.write(line.rstrip("\n"))
        ui.say("      " + line.rstrip("\n"))
    rc = proc.wait()
    log.write("(exit %d)" % rc)
    return rc, "".join(chunks)


def copy_code(source, prefix, log):
    os.makedirs(prefix, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in sorted(os.listdir(source)):
        if name in KEEP:
            continue
        src, dst = os.path.join(source, name), os.path.join(prefix, name)
        if os.path.isdir(dst) and not os.path.islink(dst):
            shutil.rmtree(dst)
        elif os.path.lexists(dst):
            os.remove(dst)
        if os.path.isdir(src):
            shutil.copytree(src, dst, ignore=ignore, symlinks=True)
        else:
            shutil.copy2(src, dst)
    log.stamp("copied the project into " + prefix)


def git_checkout(prefix, repo_url, commit, ref, log, ui):
    if os.path.isdir(os.path.join(prefix, ".git")):
        rc, out = run_logged(["git", "-C", prefix, "status", "--porcelain"], log, ui)
        if out.strip():
            raise ApplyError("the checkout at %s has local changes; not touching it" % prefix)
        steps = [["git", "-C", prefix, "fetch", "--quiet", "origin"]]
    else:
        parent = os.path.dirname(prefix.rstrip("/"))
        os.makedirs(parent, exist_ok=True)
        steps = [["git", "clone", "--quiet", repo_url, prefix]]
    target = commit if commit not in ("local", "unpinned") else (ref or "main")
    steps.append(["git", "-C", prefix, "checkout", "--quiet", target])
    for cmd in steps:
        rc, _out = run_logged(cmd, log, ui)
        if rc != 0:
            raise ApplyError("`%s` failed (exit %d); its output is above" % (" ".join(cmd), rc))


def system_packages(log, ui):
    for cmd in (["apt-get", "update"], ["apt-get", "install", "-y", "python3-venv"]):
        rc, _out = run_logged(cmd, log, ui)
        if rc != 0:
            raise ApplyError("`%s` failed (exit %d); its output is above" % (" ".join(cmd), rc))


def build_venv(prefix, pins, wheelhouse, log, ui):
    """Build into the final path, keeping the old one aside so a failure puts it back. A venv's
    scripts name their own path, so a finished venv is never renamed into place."""
    final, old = os.path.join(prefix, "venv"), os.path.join(prefix, "venv.old")
    if os.path.exists(old):
        shutil.rmtree(old)
    if os.path.exists(final):
        os.rename(final, old)
    try:
        rc, out = run_logged([sys.executable, "-m", "venv", final], log, ui)
        if rc != 0:
            raise ApplyError("could not create the virtual environment (exit %d). If the output above says "
                             "ensurepip is not available, install the venv package for this Python." % rc)
        env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
        cmd = [venv_python(prefix), "-m", "pip", "install", "--progress-bar", "off"]
        if wheelhouse:
            cmd += ["--no-index", "--find-links", wheelhouse]
        rc, out = run_logged(cmd + list(pins), log, ui, env=env)
        if rc != 0:
            raise ApplyError("pip could not install the pinned packages (exit %d); its whole output is above" % rc)
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        if os.path.exists(old):
            os.rename(old, final)
        raise
    if os.path.exists(old):
        shutil.rmtree(old)
    log.stamp("built the virtual environment with %d pinned packages" % len(pins))


def _owner_of(path):
    st = os.stat(path)
    return st.st_uid, st.st_gid


def ensure_store(prefix, log):
    """Create <prefix>/data/firmware (mode 0700, as the store itself creates it). The service runs as the
    owner of the install folder (plan.service_owner), so a folder made by root inside a user's install is
    handed to that user, or the service could not write to its own store."""
    store = store_path(prefix)
    created = not os.path.isdir(store)
    os.makedirs(store, mode=0o700, exist_ok=True)
    if created:
        log.stamp("created " + store)
        uid, gid = _owner_of(prefix)
        if os.geteuid() == 0 and uid != 0:
            for path in (os.path.dirname(store), store):
                os.chown(path, uid, gid)


def seed_store(prefix, log, ui, run=run_logged):
    """Put the packages the repo carries into the store, with the product's own CLI in local mode, so each is
    validated like any upload and a second install says "already stored". Never touches hardware. A package that
    could not be added is reported, not fatal: the product works without it."""
    cfg = os.path.join(prefix, "config", "host.yaml")
    for project, name, rel in SEEDS:
        path = os.path.join(prefix, rel)
        if not os.path.isfile(path):
            ui.say("      %s is not in this install, so it was not added" % rel)
            continue
        cmd = [venv_python(prefix), "-m", "nrf_workbench", "--config", cfg, "firmware", "put",
               "--file", path, "--project", project, "--name", name]
        rc, _out = run(cmd, log, ui, cwd=prefix)
        if rc != 0:
            ui.warn("      %s was NOT added to the package store (exit %d, output above); the install is otherwise fine"
                    % (name, rc))
            log.stamp("seed %s failed (exit %d)" % (name, rc))
        else:
            log.stamp("seeded %s into project %s" % (name, project))


def write_config(prefix, log):
    """Create what is missing, never overwrite. Returns notes for the person: things left as they were that
    they may want to change."""
    cfg = os.path.join(prefix, "config")
    os.makedirs(cfg, exist_ok=True)
    notes = []
    files = dict(CONFIG_FILES, **{"host.yaml": host_yaml_text(prefix)})
    for name, text in sorted(files.items()):
        path = os.path.join(cfg, name)
        if not os.path.exists(path):          # never overwritten
            with open(path, "w") as fh:
                fh.write(text)
            log.stamp("created " + path)
    ensure_store(prefix, log)
    with open(os.path.join(cfg, "host.yaml"), "r") as fh:
        if not sets_firmware(fh.read()):
            notes.append("config/host.yaml sets no paths.firmware, so the package store is OFF. It was left as it is. "
                         "To turn it on add:   paths:  /  firmware: %s" % json.dumps(store_path(prefix)))
    return notes


def write_unit(prefix, host, log):
    """Write <prefix>/systemd/nrf-workbench.service. Returns 'created', 'updated' or 'unchanged'; a unit
    that is already right is not rewritten, so its mtime does not move on an upgrade."""
    text = unit_text(prefix, service_owner(prefix), host["serial_group"])
    path = unit_path(prefix)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r") as fh:
            have = fh.read()
    except OSError:
        have = None
    if have == text:
        return "unchanged"
    with open(path, "w") as fh:
        fh.write(text)
    log.stamp("%s %s" % ("created" if have is None else "updated", path))
    return "created" if have is None else "updated"


def link_unit(prefix, host, log, ui, link_dir=SYSTEM_UNIT_DIR, run=run_logged, changed=False):
    """Link the unit into place. Returns True if it is linked afterwards. Linking needs root: otherwise the
    exact command is printed and nothing is applied, like a system package. `changed` says the unit file was
    just rewritten: systemd keeps the old text until it is told to reload, so an already-linked unit reloads."""
    state = link_state(prefix, link_dir)
    if state == "ours":
        ui.say("      already linked")
        if changed:
            if not host["root"]:
                ui.say("      the unit changed; as root run:  systemctl daemon-reload")
            else:
                rc, _out = run(["systemctl", "daemon-reload"], log, ui)
                if rc != 0:
                    raise ApplyError("`systemctl daemon-reload` failed (exit %d)" % rc)
                ui.say("      reloaded; a running service keeps its old unit until it is restarted")
        return True
    if state != "absent":
        raise ApplyError("%s is linked to another install (%s); not replaced" % (UNIT_NAME, state[len("other:"):]))
    cmd = ["systemctl", "link", unit_path(prefix)]
    if not host["root"]:
        ui.say("      not root, so not linking. As root run:  " + " ".join(cmd))
        return False
    rc, _out = run(cmd, log, ui)
    if rc != 0:
        raise ApplyError("`%s` failed (exit %d)" % (" ".join(cmd), rc))
    return True


def enable_and_start(host, log, ui, run=run_logged):
    """Enable at boot and start now. Only ever called after a person said yes on a terminal. A failure to
    start is shown, not raised: systemd's own answer is what verify then reports. Needs root."""
    cmd = ["systemctl", "enable", "--now", UNIT_NAME]
    if not host["root"]:
        ui.say("      not root, so not enabling. As root run:  " + " ".join(cmd))
        return False
    rc, _out = run(cmd, log, ui)
    if rc != 0:
        ui.warn("      `%s` failed (exit %d); its output is above" % (" ".join(cmd), rc))
    return True


def unlink_unit(prefix, host, log, ui, link_dir=SYSTEM_UNIT_DIR, run=run_logged):
    """Uninstall: stop, disable and unlink the unit, but only if it is THIS prefix's. Returns what happened."""
    state = link_state(prefix, link_dir)
    if state == "absent":
        return "no unit was linked"
    if state != "ours":
        return "left alone: %s is linked to another install (%s)" % (UNIT_NAME, state[len("other:"):])
    if not host["root"]:
        return "not root, so the unit stays linked. As root run:  systemctl disable --now %s" % UNIT_NAME
    rc, _out = run(["systemctl", "disable", "--now", UNIT_NAME], log, ui)
    if rc != 0:
        raise ApplyError("`systemctl disable --now %s` failed (exit %d)" % (UNIT_NAME, rc))
    link = os.path.join(link_dir, UNIT_NAME)
    if os.path.lexists(link):                 # `disable` removes the link itself (checked on the Pi); this is a guard
        os.remove(link)
    rc, _out = run(["systemctl", "daemon-reload"], log, ui)
    if rc != 0:
        raise ApplyError("`systemctl daemon-reload` failed (exit %d)" % rc)
    return "stopped, disabled and unlinked"


def write_record(prefix, ctx, log):
    rec = {
        "commit": ctx["commit"], "ref": ctx["args"].ref, "features": ctx["features"],
        "pins_hash": ctx["pins_hash"], "python_minor": ".".join(ctx["host"]["python"].split(".")[:2]),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "how": "git" if ctx["args"].git else ("tarball" if ctx["staged"] else "checkout copy"),
    }
    with open(os.path.join(prefix, MARKER), "w") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
        fh.write("\n")
    log.stamp("wrote the install record")


def add_user(name, group, host, log, ui):
    """Only when asked for with --add-user. A group change is a privilege change."""
    import pwd
    try:
        pwd.getpwnam(name)
    except KeyError:
        raise ApplyError("there is no user '%s'" % name)
    cmd = ["usermod", "-aG", group, name]
    if not host["root"]:
        ui.say("not root, so not changing groups. As root run:  " + " ".join(cmd))
        return
    rc, _out = run_logged(cmd, log, ui)
    if rc != 0:
        raise ApplyError("`%s` failed (exit %d)" % (" ".join(cmd), rc))
    ui.say("added %s to %s; they must log in again for it to apply" % (name, group))
