#!/usr/bin/env python3
"""Drive the power profiler in a real browser (docs/specs/ppk2.md; docs/specs/meter-page.md): the one command that runs every browser check of it, and the bench the others share.

    python3 scripts/verify_ppk2_live.py [--quick] [--shots DIR] [--chrome PATH]

It runs, in this order, and exits 0 only if every one passed:
  1. the profiler's quick check on Home and Devices, in light and dark at a desktop and at a phone width: nothing has asked the unit until the button is pressed, the answer says what the unit is
     set to in words and that its output is not known, the headline stays inside what holds it, no sideways scroll;
  2. a bench with no profiler configured has no profiler check and no Meter page;
  3. the five checks of the Meter page, each its own script with its own mutants: verify_supply_controls.py, verify_meter_capture.py, verify_meter_replay.py, verify_meter_life.py and
     verify_meter_stages.py. `--quick` leaves the 21 s wake out of the last (about a minute).

This module is also the bench the other browser scripts import (`Bench`: a real service, real routes and built UI over a fake sysfs with a PPK2 and a FAKE UNIT that speaks the protocol read from the
real one and is paced at the real 100,000 samples a second; `ALLOWED`: the six shapes the service may write to the unit; `no_sideways_scroll`). It once drove the Meter page's old layout itself;
those checks now live in the five scripts above.
"""
import argparse
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "instruments"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.instruments import ppk2  # noqa: E402
from fakes import Tree  # noqa: E402
from test_ppk2 import FakeUnit  # noqa: E402

THEMES = ("light", "dark")
SIZES = (("desktop", 1280, 900), ("phone", 390, 844))
STREAM_BYTES_PER_S = 400000
SCRIPTS = ("verify_supply_controls.py", "verify_meter_capture.py", "verify_meter_replay.py", "verify_meter_life.py", "verify_meter_stages.py")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class PacedUnit(FakeUnit):
    """The fake unit at the real rate: a block of 80,000 bytes takes 0.2 s to arrive, as it does from the real one."""

    def read(self, size):
        out = FakeUnit.read(self, size)
        if self.measuring and out:
            time.sleep(len(out) / float(STREAM_BYTES_PER_S))
        return out


class Bench(object):
    def __init__(self, with_meter=True, with_ppk2=True, meter_address=None):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-ppk2-")
        self.tree.app_board()
        self.unit = PacedUnit()
        if with_ppk2:
            self.tree.node("ttyACM1", 166, 1)
            self.tree.node("ttyACM2", 166, 2)
            self.tree.ppk2()
        instruments = {}
        if with_meter:
            instruments["meter"] = meter_address
        if with_ppk2:
            instruments["ppk2"] = "auto"
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console"), "meter": os.path.join(self.tmp, "meter")},
            "instruments": instruments,
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1},
                        "console": {"retain": 500, "max_age_days": 1}, "meter": {"retain": 5000, "max_age_days": 1}}})
        assert problems == [], problems
        self.uev_a, self.uev_b = socket.socketpair()
        self.opened = []
        hooks = {"tool": "/fake/uploader", "open_uevents": lambda: self.uev_a, "ppk2_open": self._open}
        self.app, self.host, _r, _c = service.build(cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, proc=self.tree.proc, hooks=hooks)
        self.host.console.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True).start()

    def _open(self, node):
        self.opened.append(node)
        self.unit.closed = False
        return self.unit

    def close(self):
        sampler = self.host.started("meter_sampler")
        if sampler is not None:
            sampler.stop()
        self.server.should_exit = True
        self.host.console.stop()
        for s in (self.uev_a, self.uev_b):
            s.close()
        self.tree.close()


def no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


ALLOWED = ((ppk2.META,), (ppk2.START,), (ppk2.STOP,), (ppk2.MODE, 1), (ppk2.MODE, 2), (ppk2.POWER, 0), (ppk2.POWER, 1))


def reach_check(page):
    """The profiler's quick check: a button on the page, or behind the Power profiler button where the page is wide enough to keep it in a drawer."""
    button = page.get_by_role("button", name="Check the profiler")
    if button.count() == 0:
        page.get_by_role("button", name="Power profiler", exact=True).click()
    button.wait_for()
    return button


def cards(page, bench, tag, shot):
    go_to(page, "Home")
    button = reach_check(page)
    check("%s Home has the profiler's check, and nothing has asked the unit yet" % tag, bench.unit.writes == [], bench.unit.writes)
    button.click()
    status = page.get_by_role("status").filter(has_text="Power profiler E1C5F15B15A3").first
    status.wait_for()
    check("%s Check on Home says what the unit is set to, in words, and that its output is not known" % tag,
          "source mode" in status.inner_text() and "output state unknown (4000 mV when on)" in status.inner_text(), status.inner_text())
    holder = page.get_by_role("dialog", name="Power profiler")
    frame = (holder if holder.count() else button.locator("xpath=ancestor::section[1]")).bounding_box()
    head = status.bounding_box()
    check("%s the headline stays inside what holds it" % tag, head["x"] + head["width"] <= frame["x"] + frame["width"] + 0.5, (head, frame))
    shot("home")
    go_to(page, "Devices")
    reach_check(page)
    check("%s the Devices page has the same check" % tag, True)
    check("%s no sideways scroll on Devices" % tag, no_sideways_scroll(page))


def run_cards(browser, theme, size, width, height, shots):
    tag = "%s %s" % (theme, size)
    bench = Bench(with_meter=False)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    page.set_default_timeout(30000)
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("response", lambda r: bad.append((r.status, "/".join(r.url.split("?")[0].rsplit("/", 2)[-2:]))) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    shot = lambda name: page.screenshot(path=os.path.join(shots, "ppk2-%s-%s-%s.png" % (theme, size, name)))
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        cards(page, bench, tag, shot)
        check("%s no failing response, no page or console errors, and the shell port was never opened" % tag,
              bad == [] and not errors and all(n.endswith("ttyACM1") for n in bench.opened), (bad, errors, bench.opened))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
    finally:
        context.close()
        bench.close()


def run_without(browser):
    bench = Bench(with_meter=False, with_ppk2=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Devices")
        page.get_by_role("heading", name="Devices").wait_for()
        check("without the profiler configured there is no profiler check and no Meter page",
              page.get_by_role("button", name="Check the profiler").count() == 0 and page.get_by_role("button", name="Power profiler", exact=True).count() == 0
              and page.get_by_role("navigation", name="Pages").get_by_text("Meter", exact=True).count() == 0)
    except Exception as exc:
        check("without the profiler configured the page could be driven", False, str(exc).splitlines()[0])
    finally:
        context.close()
        bench.close()


def run_scripts(chrome, quick, shots):
    for name in SCRIPTS:
        extra = ["--no-long"] if quick and name == "verify_meter_stages.py" else []
        started = time.time()
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", name), "--chrome", chrome, "--shots", os.path.join(shots, name[:-3])] + extra, capture_output=True, text=True)
        lines = r.stdout.strip().splitlines()
        failed = [ln for ln in lines if ln.startswith("FAIL")]
        for ln in failed:
            print(ln, flush=True)
        check("%s: %s (%.0f s)" % (name, lines[-1].split(". screenshots")[0] if lines else "no output: " + r.stderr.strip()[-200:], time.time() - started), r.returncode == 0 and not failed)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--quick", action="store_true", help="leave out the 21 s wake of verify_meter_stages.py (about a minute)")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-ppk2-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        try:
            for theme in THEMES:
                for size, width, height in SIZES:
                    run_cards(browser, theme, size, width, height, shots)
            run_without(browser)
        finally:
            browser.close()                                     # one Chrome at a time on a 4 GB host: this one is gone before the scripts start theirs
    run_scripts(args.chrome, args.quick, shots)
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
