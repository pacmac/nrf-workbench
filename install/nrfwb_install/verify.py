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


def run(prefix, features, manifest):
    """[(label, status, detail)]"""
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
    results.append(("hardware", "not verified: the installer never touches a board or a serial device", ""))
    return results
