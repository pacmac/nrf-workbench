#!/usr/bin/env python3
"""Drive the multimeter section of the Devices page in a real browser (docs/specs/meter.md).

    python3 scripts/verify_meter_live.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs, with a FAKE SCPI meter on a real socket (tests/instruments/test_meter.py).
In light and dark, at a desktop and at a phone width: the section is there only when a meter address is configured, Check says what the
meter is and how it is set (and that it is in series), Read once shows the value in words, Take N draws a chart and the statistics, a silent
meter is shown as a failure with its code, and nothing the page did put a line on the meter that is not a query.
Exit 0 only if every check passed.
"""
import argparse
import json
import os
import re
import socket
import sys
import tempfile
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "instruments"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from fakes import Tree  # noqa: E402
from test_meter import CURRENT, FakeMeter  # noqa: E402

THEMES = ("light", "dark")
SIZES = (("desktop", 1280, 900), ("phone", 390, 844))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class Bench(object):
    def __init__(self, meter_address):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-meter-")
        self.tree.app_board()
        instruments = {"meter": meter_address} if meter_address else {}
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console")},
            "instruments": instruments,
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1},
                        "console": {"retain": 500, "max_age_days": 1}}})
        assert problems == [], problems
        self.uev_a, self.uev_b = socket.socketpair()
        hooks = {"tool": "/fake/uploader", "open_uevents": lambda: self.uev_a}
        self.app, self.host, _r, _c = service.build(cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, proc=self.tree.proc, hooks=hooks)
        self.host.console.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True).start()

    def close(self):
        self.server.should_exit = True
        self.host.console.stop()
        for s in (self.uev_a, self.uev_b):
            s.close()
        self.tree.close()


def no_sideways_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


RAIL = {"*IDN?": CURRENT["*IDN?"], "FUNC?": '"VOLT"', "VOLT:DC:RANG?": "+6.0E+00", "VOLT:DC:RANG:AUTO?": "1", "VOLT:DC:NPLC?": "+2.0E-02",
        "READ?": "+3.31E+00", "SYST:ERR?": '0,"No error"'}


def project_measure(page, fake, tag, shot):
    """A project that expects a 3.1 to 3.5 V rail: Measure reads the meter once and says inside or OUTSIDE; a meter set to current is refused."""
    name = "rail-" + tag.replace(" ", "-")
    fake.table.clear()
    fake.table.update(RAIL)
    go_to(page, "Projects")
    page.get_by_label("New project name").fill(name)
    page.get_by_role("button", name="Create project").click()
    detail = page.get_by_label("Project %s" % name)
    detail.wait_for()
    check("%s a project without expect.meter has no Measure button" % tag, detail.get_by_role("button", name="Measure").count() == 0)
    detail.get_by_role("button", name="Edit manifest").click()
    detail.get_by_label("Manifest").fill("name: %s\npurpose: the 3.3 V rail\nexpect:\n  meter: {function: dcv, min: 3.1, max: 3.5}\n" % name)
    detail.get_by_role("button", name="Save manifest").click()
    detail.get_by_role("button", name="Edit manifest").wait_for()
    detail.get_by_role("button", name="Measure").click()
    detail.get_by_text("3.31 V is inside 3.1 to 3.5 V").wait_for()
    check("%s Measure says the rail is inside its range, with the number" % tag, True)
    shot("5-measured")
    fake.table["READ?"] = "+2.8E+00"
    detail.get_by_role("button", name="Measure").click()
    detail.get_by_text("2.8 V is OUTSIDE 3.1 to 3.5 V").wait_for()
    check("%s a reading outside the range says OUTSIDE" % tag, True)
    fake.table["FUNC?"] = '"CURR"'
    fake.table.update({"CURR:DC:RANG?": "+6.0E-01", "CURR:DC:RANG:AUTO?": "0", "CURR:DC:NPLC?": "+2.0E-02"})
    detail.get_by_role("button", name="Measure").click()
    detail.get_by_text("meter_function").wait_for()
    check("%s a meter set to current is refused and nothing is read from it" % tag, "set at the meter's front panel" in detail.inner_text())
    shot("6-refused")


def run(browser, theme, size, width, height, shots):
    tag = "%s %s" % (theme, size)
    fake = FakeMeter(CURRENT)
    bench = Bench("127.0.0.1:%d" % fake.port)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    # Chrome logs every 4xx/5xx response as a console error. A failed Result is HTTP 500 by design (core.md), so the one deliberate failure
    # below is expected; the console line is dropped and the response is checked by URL and status instead.
    bad = []
    page.on("response", lambda r: bad.append((r.status, "/".join(r.url.split("?")[0].rsplit("/", 2)[-2:]))) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text))
            if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    shot = lambda name: page.screenshot(path=os.path.join(shots, "%s-%s-%s.png" % (theme, size, name)), full_page=True)
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Devices")
        page.get_by_role("button", name="Check the meter").wait_for()
        check("%s the Devices page has a multimeter section" % tag, page.get_by_text("Multimeter", exact=True).is_visible())
        shot("1-section")
        page.get_by_role("button", name="Check the meter").click()
        status = page.get_by_role("status").filter(has_text="Siglent").first
        status.wait_for()
        check("%s Check says what the meter is and how it is set, in words" % tag,
              "SDM3045X" in status.inner_text() and "set to CURR" in status.inner_text() and "range 600 mA" in status.inner_text(), status.inner_text())
        check("%s it says the meter is in series if the leads are in the current jacks" % tag,
              page.get_by_text("Set to current: this is in series with the circuit").is_visible())
        shot("2-checked")
        page.get_by_role("button", name="Read once").click()
        page.get_by_role("status").filter(has_text=re.compile(r"^1\.717 mA$")).wait_for()
        check("%s Read once shows the value with its SI prefix" % tag, True)
        fake.answers["READ?"] = ["+1.0E-03", "+2.0E-03", "+3.0E-03", "+2.0E-03", "+1.0E-03"]
        page.get_by_label("How many readings to take").fill("5")
        page.get_by_role("button", name="Take 5").click()
        stat = page.get_by_role("status").filter(has_text="5 readings").first
        stat.wait_for()
        check("%s Take 5 gives the mean, lowest and highest" % tag,
              "mean 1.8 mA" in stat.inner_text() and "from 1 mA to 3 mA" in stat.inner_text(), stat.inner_text())
        chart = page.get_by_role("img", name=re.compile(r"^5 readings, from 1 mA to 3 mA"))
        chart.wait_for()
        check("%s the readings are drawn as a chart with its range stated" % tag,
              chart.locator("polyline").get_attribute("points").count(" ") == 4, chart.inner_html())
        pts = chart.locator("polyline").get_attribute("points").split(" ")
        check("%s the chart runs the whole width, first reading to last (not a line stacked at one x)" % tag,
              pts[0].startswith("4.0,") and pts[-1].startswith("476.0,"), pts)
        box = chart.locator("svg").bounding_box() if chart.evaluate("e => e.tagName") != "svg" else chart.bounding_box()
        line_box = chart.locator("polyline").bounding_box()
        check("%s and it is drawn across the width of its box on screen" % tag, line_box["width"] > 0.9 * box["width"], (line_box, box))
        page.get_by_text("The whole result").click()
        check("%s the whole result is one click away" % tag, page.get_by_text('"points"').is_visible())
        shot("3-sampled")
        check("%s no sideways scroll" % tag, no_sideways_scroll(page))
        del fake.table["READ?"]
        page.get_by_role("button", name="Read once").click()
        page.get_by_text("no_reply").wait_for()
        check("%s a silent meter is shown as a failure with its code and the meter's words" % tag,
              page.get_by_text("failed", exact=True).first.is_visible() and "did not answer" in page.inner_text("body"))
        shot("4-silent")
        check("%s a failed read has no made-up headline (no 'undefined' anywhere)" % tag, "undefined" not in page.inner_text("body"), page.inner_text("body"))
        check("%s everything the page made the service send to the meter was a query" % tag,
              fake.sent and all(s.endswith("?") for s in fake.sent), [s for s in fake.sent if not s.endswith("?")])
        project_measure(page, fake, tag, shot)
        check("%s the only failing responses were the two deliberate ones: the silent meter (500 from meter/read) and the meter set to "
              "another function (409 from project/measure)" % tag, bad == [(500, "meter/read"), (409, "project/measure")], bad)
        check("%s no page or console errors" % tag, not errors, errors)
    finally:
        context.close()
        bench.close()
        fake.close()


def run_without(browser, shots):
    bench = Bench(None)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Devices")
        page.get_by_role("heading", name="Devices").wait_for()
        check("without an address there is no multimeter section", page.get_by_role("button", name="Check the meter").count() == 0)
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-meter-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        try:
            for theme in THEMES:
                for size, width, height in SIZES:
                    run(browser, theme, size, width, height, shots)
            run_without(browser, shots)
        finally:
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
