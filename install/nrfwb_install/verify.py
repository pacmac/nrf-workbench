"""Prove the install. A host is not 'installed' until this passes.

Each result is PASS, FAIL or 'not verified: <reason>'. A check that could not run is never reported
as a pass, and nothing here touches hardware.
"""
import os
import subprocess

from .plan import venv_python


def _run(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, cwd=cwd)
        return p.returncode, p.stdout
    except OSError as exc:
        return 127, str(exc)


_STORE_CHECK = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from nrf_workbench.core import config\n"
    "cfg, problems = config.load(required_files=[sys.argv[2]])\n"
    "if problems:\n"
    "    print('; '.join(problems)); sys.exit(1)\n"
    "store = cfg['paths']['firmware']\n"
    "if not store:\n"
    "    print('host.yaml sets no paths.firmware'); sys.exit(3)\n"
    "if not os.path.isdir(store):\n"
    "    print('%s is not a folder' % store); sys.exit(1)\n"
    "print(store)\n"
)


def store_result(prefix):
    """The package store, read the way the service reads it: through the product's own configuration loader.
    A host.yaml that sets no store is not a failure (a user may have turned it off), but it is not verified."""
    label = "store: paths.firmware is set and its folder exists"
    cfg = os.path.join(prefix, "config", "host.yaml")
    if not os.path.exists(cfg):
        return (label, "not verified: there is no config/host.yaml", "")
    rc, out = _run([venv_python(prefix), "-I", "-c", _STORE_CHECK, prefix, cfg])
    if rc == 0:
        return (label, "PASS", out.strip())
    if rc == 3:
        return (label, "not verified: " + out.strip() + ", so the package store is off", "")
    return (label, "FAIL", out.strip())


def unit_state(unit_name):
    """systemd's own word for the unit: active, inactive, activating, failed ... ('unknown' if it cannot ask)."""
    rc, out = _run(["systemctl", "is-active", unit_name])
    return out.strip().splitlines()[0] if out.strip() else "unknown"


def unit_result(linked, unit_name):
    """The service unit's line. `linked` is None when this host has no unit step. systemd's own answer decides,
    whoever started the service: 'active' is a PASS, 'inactive' means nobody started it (not verified), and
    anything else ('activating' is a restart loop, 'failed') is a FAIL."""
    if linked is None:
        return None
    label = "service: %s is active" % unit_name
    if not linked:
        return (label, "not verified: the unit is not linked, so the service was not started", "")
    state = unit_state(unit_name)
    if state == "active":
        return (label, "PASS", "")
    if state == "inactive":
        return (label, "not verified: the service was not started", "")
    rc, status = _run(["systemctl", "status", "--no-pager", "-n", "15", unit_name])
    return (label, "FAIL", ("state: %s\n%s" % (state, status.strip())).strip())


def http_result(prefix, wait=None, get=None):
    """GET / on the address host.yaml records, once the unit is active. The dashboard page is served without a login,
    so a 200 proves the service is bound where the installer said and is answering, not just running. `wait` and `get`
    are seams for tests."""
    from . import listen as listenmod
    wait = wait or listenmod.wait_for_bind
    cfg = os.path.join(prefix, "config", "host.yaml")
    try:
        with open(cfg, "r") as fh:
            where = listenmod.recorded(fh.read()).get("listen") or listenmod.DEFAULT
    except OSError:
        where = listenmod.DEFAULT
    try:
        host, port = listenmod.parse(where)
    except ValueError as exc:
        return ("service: GET / answers", "FAIL", "host.yaml records %r, which is not usable: %s" % (where, exc))
    url = "http://%s/" % listenmod.join(listenmod.client_address(host), port)
    label = "service: GET %s answers" % url
    if not wait(host, port):
        return (label, "FAIL", "nothing accepted a connection within %.0f s of the unit being active" % listenmod.BIND_CEILING_S)
    try:
        if get is None:
            import urllib.request
            with urllib.request.urlopen(url, timeout=listenmod.HTTP_TIMEOUT_S) as resp:        # noqa: S310 (our own loopback URL)
                status = resp.status
        else:
            status = get(url)
    except Exception as exc:                                  # the answer is the evidence; any failure is a FAIL with it
        return (label, "FAIL", "%s: %s" % (type(exc).__name__, exc))
    return (label, "PASS", "") if status == 200 else (label, "FAIL", "HTTP %s" % status)


def run(prefix, features, manifest, linked=None, unit_name=None):
    """[(label, status, detail)]. `linked` and `unit_name` add the service unit's line (see unit_result)."""
    results = []
    py = venv_python(prefix)
    for name in features:
        spec = manifest["features"][name].get("verify", {})
        mods = spec.get("imports")
        if mods:
            rc, out = _run([py, "-I", "-c", "import " + ", ".join(mods)])
            results.append(("%s: imports %s" % (name, ", ".join(mods)), "PASS" if rc == 0 else "FAIL", out.strip()))
        for script in spec.get("smoke", []):
            path = os.path.join(prefix, script)
            if not os.path.exists(path):
                results.append(("%s: %s" % (name, script), "not verified: the script is not in the install", ""))
                continue
            rc, out = _run([py, "-I", path], cwd=prefix)
            results.append(("%s: %s" % (name, script), "PASS" if rc == 0 else "FAIL", out.strip()))
        for cmd in spec.get("commands", []):
            exe = os.path.join(prefix, "venv", "bin", cmd[0])
            if not os.path.exists(exe):
                results.append(("%s: %s" % (name, " ".join(cmd)), "FAIL", "%s was not installed" % exe))
                continue
            rc, out = _run([exe] + cmd[1:])
            results.append(("%s: %s" % (name, " ".join(cmd)), "PASS" if rc == 0 else "FAIL", out.strip()))
    results.append(store_result(prefix))
    unit = unit_result(linked, unit_name)
    if unit:
        results.append(unit)
        if unit[1] == "PASS":                                 # only a running service can answer; otherwise the line above says why
            results.append(http_result(prefix))
    results.append(("hardware", "not verified: the installer never touches a board or a serial device", ""))
    return results
