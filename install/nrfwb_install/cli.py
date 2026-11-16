"""Orchestration: parse arguments, detect, choose, plan, confirm, apply, verify.

Exit codes (shared with install.sh): 0 done, 1 failed, 2 usage, 3 needs a person (the plan was
shown, nothing was applied), 4 a prerequisite is missing.
"""
import argparse
import os
import shutil
import sys

from . import FAILED, MARKER, MIN_PYTHON, NEEDS_PERSON, OK, PREREQ, USAGE
from . import apply as applymod
from . import features as featmod
from . import host as hostmod
from . import plan as planmod
from . import prefix as prefixmod
from . import verify as verifymod
from .ui import NeedsPerson, Ui

REPO = os.environ.get("NRF_WORKBENCH_REPO", "pacmac/nrf-workbench")
WEB = os.environ.get("NRF_WORKBENCH_WEB", "https://github.com")


class UsageError(Exception):
    pass


def make_parser():
    p = argparse.ArgumentParser(prog="install.py", description="nrf-workbench installer (see docs/specs/install.md)")
    p.add_argument("--check", action="store_true", help="show the preflight and the plan, change nothing")
    p.add_argument("--yes", action="store_true", help="with no terminal, accept only the SAFE defaults")
    p.add_argument("--prefix", help="install into this folder (no folder is assumed; required with no terminal)")
    p.add_argument("--features", help="comma-separated: core,flash,console,build (default: core,flash,console)")
    p.add_argument("--ref", default="main", help="the commit, tag or branch (install.sh resolves it)")
    p.add_argument("--git", action="store_true", help="make a developer git checkout at the prefix")
    p.add_argument("--wheelhouse", help="install packages from this folder with no network")
    p.add_argument("--add-user", dest="add_user", help="add this user to the group that owns serial devices")
    p.add_argument("--uninstall", action="store_true", help="remove the venv and the record; keeps config and logs")
    p.add_argument("--no-verify", dest="no_verify", action="store_true", help="skip the verify step")
    p.add_argument("--source", required=True, help="(internal) the project tree to install from")
    p.add_argument("--staged", action="store_true", help="(internal) the source is a throwaway download")
    p.add_argument("--commit", default="local", help="(internal) the commit being installed")
    return p


def host_lines(h):
    pi = "  Raspberry Pi: %s" % h["pi_model"] if h["pi_model"] else ""
    return [
        "Host: %s, %s, libc: %s, Python %s%s" % (h["os"], h["arch"], h["libc"], h["python"], pi),
        "      root: %s, terminal: %s, apt: %s, systemd: %s" % (
            "yes" if h["root"] else "no", "yes" if h["terminal"] else "no",
            "yes" if h["apt"] else "no", "yes" if h["systemd"] else "no"),
    ]


def choose_prefix(args, h, ui):
    wanted = args.prefix or os.environ.get("NRF_WORKBENCH_PREFIX")
    if wanted:
        ok, why, path = prefixmod.validate(wanted)
        if not ok:
            raise UsageError(why)
        return path
    existing = prefixmod.existing_installs(h)
    if not ui.terminal:
        if len(existing) == 1:
            return existing[0]
        more = " (several existing installs were found: %s)" % ", ".join(existing) if existing else ""
        raise NeedsPerson("where to install: pass --prefix DIR%s" % more)
    cands = prefixmod.candidates(h)
    ui.say("")
    ui.say("Where should nrf-workbench be installed? No folder is assumed.")
    for i, (path, why) in enumerate(cands, 1):
        ui.say("  %d) %s   (%s)" % (i, path, why))
    ui.say("  or type any absolute path")
    while True:
        try:
            reply = ui.ask("Choice:")
        except NeedsPerson:
            raise NeedsPerson("where to install (no folder is assumed): choose one, or pass --prefix DIR")
        if not reply:
            continue
        choice = cands[int(reply) - 1][0] if reply.isdigit() and 1 <= int(reply) <= len(cands) else reply
        ok, why, path = prefixmod.validate(choice)
        if ok:
            return path
        ui.say("  not usable: " + why)


def choose_features(args, manifest, ui):
    if args.features:
        requested = [f.strip() for f in args.features.split(",") if f.strip()]
    else:
        requested = list(manifest["default"])
        if ui.terminal and ui.confirm("Also install the build tooling (PlatformIO)? Its toolchain downloads on the "
                                      "first build and its size has not been measured."):
            requested.append("build")
    return featmod.resolve(manifest, requested)


def do_uninstall(args, h, ui):
    wanted = args.prefix or os.environ.get("NRF_WORKBENCH_PREFIX")
    existing = prefixmod.existing_installs(h)
    prefix = os.path.normpath(os.path.expanduser(wanted)) if wanted else (existing[0] if len(existing) == 1 else None)
    if not prefix:
        raise NeedsPerson("which install to remove: pass --prefix DIR")
    if not os.path.isfile(os.path.join(prefix, MARKER)):
        raise UsageError("%s is not an nrf-workbench install (no %s)" % (prefix, MARKER))
    ui.say("This removes %s/venv and the install record. It KEEPS config/, install.log and the project files;" % prefix)
    ui.say("delete the folder yourself to remove everything.")
    if not ui.confirm("Remove the virtual environment?", safe=True):
        ui.say("nothing changed")
        return OK
    if h["systemd"]:
        log = applymod.Log(prefix)
        ui.say("service unit: " + applymod.unlink_unit(prefix, h, log, ui))
    for sub in ("venv", "venv.old", "systemd"):
        shutil.rmtree(os.path.join(prefix, sub), ignore_errors=True)
    os.remove(os.path.join(prefix, MARKER))
    ui.say("removed. Configuration and logs were kept.")
    return OK


def main(argv=None):
    args = make_parser().parse_args(argv)
    ui = Ui(yes=args.yes)
    h = hostmod.detect()
    ui.say("nrf-workbench installer")
    for line in host_lines(h):
        ui.say(line)
    if not h["python_ok"]:
        ui.warn("This Python is %s; nrf-workbench needs %d.%d or newer." % (h["python"], MIN_PYTHON[0], MIN_PYTHON[1]))
        return PREREQ
    untested = hostmod.untested_reasons(h)
    if untested:
        ui.say("note: untested on this class (it will try, and will not claim more than it verifies): " + "; ".join(untested))

    try:
        if args.uninstall:
            return do_uninstall(args, h, ui)
        manifest = featmod.load(args.source)
        prefix = choose_prefix(args, h, ui)
        feats = choose_features(args, manifest, ui)
        pin_list = featmod.pins(manifest, feats)
        ctx = {"prefix": prefix, "features": feats, "pins": pin_list, "pins_hash": featmod.pins_hash(pin_list),
               "source": args.source, "staged": args.staged, "commit": args.commit, "args": args, "host": h}
        plan = planmod.build(ctx)

        ui.say("")
        ui.say("Plan for %s  (features: %s, commit: %s)" % (prefix, ", ".join(feats), args.commit))
        if h["systemd"]:
            notes = []
        else:
            notes = ["no systemd here, so no service unit. Run the service in the foreground:  %s -m nrf_workbench.service "
                     "--config %s/config/host.yaml" % (planmod.venv_python(prefix), prefix)]
        if args.commit == "unpinned":
            notes.append("the commit could not be pinned (the GitHub API was not reachable); this install is UNPINNED")
        for line in planmod.render(plan, notes):
            ui.say(line)
        if plan["blockers"]:
            ui.say("")
            for b in plan["blockers"]:
                ui.warn("cannot continue: " + b)
            return PREREQ
        if args.check:
            ui.say("")
            ui.say("--check: nothing was changed.")
            return OK

        todo = [s for s in plan["steps"] if not s.get("skip")]
        if any(s.get("needs_person") for s in todo):
            step = [s for s in todo if s.get("needs_person")][0]
            if not ui.confirm("Run this system command as part of the install?  " + step["lines"][0]):
                ui.say("declined; nothing was changed.")
                return NEEDS_PERSON
        if not ui.confirm("Apply this plan?", safe=True):
            ui.say("declined; nothing was changed.")
            return NEEDS_PERSON
    except UsageError as exc:
        ui.warn("error: %s" % exc)
        return USAGE
    except featmod.FeatureError as exc:
        ui.warn("error: %s" % exc)
        return USAGE
    except NeedsPerson as exc:
        ui.say("")
        ui.say("This needs a person, and nothing was changed: %s." % exc.what)
        ui.say("Re-run on a terminal, or pass the choice on the command line (add --yes to take the safe defaults).")
        return NEEDS_PERSON

    log = applymod.Log(prefix)
    log.stamp("install started: commit %s, features %s" % (args.commit, ",".join(feats)))
    ui.say("")
    linked, started = False, False
    try:
        for step in plan["steps"]:
            if step.get("skip"):
                ui.say("skip: %s" % step["title"])
                continue
            ui.say("do:   %s" % step["title"])
            sid = step["id"]
            if sid == "git":
                applymod.git_checkout(prefix, "%s/%s.git" % (WEB, REPO), args.commit, args.ref, log, ui)
            elif sid == "code":
                applymod.copy_code(args.source, prefix, log)
            elif sid == "system":
                applymod.system_packages(log, ui)
            elif sid == "venv":
                applymod.build_venv(prefix, pin_list, args.wheelhouse, log, ui)
            elif sid == "config":
                applymod.write_config(prefix, log)
            elif sid == "unit":
                ui.say("      unit file: " + applymod.write_unit(prefix, h, log))
                linked = applymod.link_unit(prefix, h, log, ui)
            elif sid == "record":
                applymod.write_record(prefix, ctx, log)
        if args.add_user:
            group = h["serial_group"]
            if not group:
                ui.say("cannot tell which group owns serial devices (no serial node is present to read), so no group was changed")
            else:
                applymod.add_user(args.add_user, group, h, log, ui)
    except applymod.ApplyError as exc:
        log.stamp("FAILED: %s" % exc)
        ui.warn("")
        ui.warn("FAILED: %s" % exc)
        ui.warn("Nothing is half-built; the log is %s. Fix the cause and run the same command again." % log.path)
        return FAILED

    unit_started = None                       # None: this host has no unit step
    if h["systemd"]:
        unit_started = False
        if linked and ui.terminal and ui.confirm("Enable the service at boot and start it now?"):
            unit_started = applymod.enable_and_start(h, log, ui)
        elif linked:
            ui.say("The service is linked but not enabled or started. To do both:  systemctl enable --now %s" % planmod.UNIT_NAME)
        else:
            ui.say("The unit file is written but not linked. Link it as root:  systemctl link %s" % planmod.unit_path(prefix))

    if args.no_verify:
        ui.say("verify skipped (--no-verify): this install is NOT proven")
        return OK
    ui.say("")
    ui.say("Verify:")
    results = verifymod.run(prefix, feats, manifest, started=unit_started, unit_name=planmod.UNIT_NAME)
    failed = False
    for label, status, detail in results:
        ui.say("  %-8s %s" % (status.split(":")[0].upper() if status.startswith("not verified") else status, label))
        if status.startswith("not verified"):
            ui.say("           " + status)
        if status == "FAIL":
            failed = True
            ui.say("           " + (detail or "no output").replace("\n", "\n           "))
    log.stamp("verify: %s" % ("FAILED" if failed else "passed"))
    ui.say("")
    if failed:
        ui.warn("The install is NOT proven: a check failed (above). The log is %s." % log.path)
        return FAILED
    ui.say("Installed and verified in %s." % prefix)
    ui.say("Run the service:  %s -m nrf_workbench.service --config %s/config/host.yaml" % (planmod.venv_python(prefix), prefix))
    return OK
