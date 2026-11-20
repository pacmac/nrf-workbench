#!/usr/bin/env python3
"""Drive the power profiler in a real browser (docs/specs/ppk2.md).

    python3 scripts/verify_ppk2_live.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs that has a PPK2 with its two serial ports, a FAKE UNIT that speaks the protocol read from the real one
(tests/instruments/test_ppk2.py, paced at the real 100,000 samples a second) and a FAKE multimeter on a real socket. In light and dark, at a desktop and at a phone width:
the cards on Home and Devices, the source switch on the Meter page (only with both instruments), a watch and a recording from the profiler with the figures, the chart
and the CSV, the lost-sample note, the mode and supply controls with their tick, and nothing the page did write a byte that is not one of the six shapes.
Exit 0 only if every check passed.
"""
import argparse
import os
import re
import socket
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
from test_meter import CURRENT, FakeMeter  # noqa: E402
from test_ppk2 import FakeUnit  # noqa: E402

THEMES = ("light", "dark")
SIZES = (("desktop", 1280, 900), ("phone", 390, 844))
STREAM_BYTES_PER_S = 400000
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


def cards(page, bench, tag, shot):
    go_to(page, "Home")
    page.get_by_role("button", name="Check the profiler").wait_for()
    check("%s Home has the power profiler card, and nothing has asked the unit yet" % tag, bench.unit.writes == [], bench.unit.writes)
    page.get_by_role("button", name="Check the profiler").click()
    status = page.get_by_role("status").filter(has_text="Power profiler E1C5F15B15A3").first
    status.wait_for()
    check("%s Check on Home says what the unit is set to, in words, and that its output is not known" % tag,
          "source mode" in status.inner_text() and "output state unknown (4000 mV when on)" in status.inner_text(), status.inner_text())
    frame = page.get_by_role("button", name="Check the profiler").locator("xpath=ancestor::section[1]").bounding_box()
    head = status.bounding_box()
    check("%s the headline stays inside the card" % tag, head["x"] + head["width"] <= frame["x"] + frame["width"] + 0.5, (head, frame))
    shot("0-home")
    go_to(page, "Devices")
    page.get_by_role("button", name="Check the profiler").wait_for()
    check("%s the Devices page has the same card" % tag, page.get_by_text("Power profiler", exact=True).first.is_visible())
    check("%s no sideways scroll on Devices" % tag, no_sideways_scroll(page))


def meter_page(page, bench, fake, tag, shot):
    go_to(page, "Meter")
    page.get_by_role("heading", name="Meter", exact=True).wait_for()
    switch = page.get_by_role("group", name="Instrument")
    switch.wait_for()
    check("%s with both instruments there is a switch between them" % tag, switch.get_by_role("button").count() == 2, switch.inner_text())
    check("%s the multimeter is the default and its card shows" % tag, page.get_by_text("The meter", exact=True).is_visible() and page.get_by_text("The power profiler", exact=True).count() == 0)
    switch.get_by_role("button", name="Power profiler").click()
    page.get_by_label("Profiler").wait_for()
    check("%s choosing the profiler shows its card with what it is set to" % tag,
          "Power profiler E1C5F15B15A3: source mode" in page.get_by_label("Profiler").inner_text() and page.get_by_text("The meter", exact=True).count() == 0,
          page.get_by_label("Profiler").inner_text())
    shot("1-profiler")

    # a watch from the profiler
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, .* samples")).wait_for()
    now = page.get_by_label("Latest reading")
    now.filter(has_text=re.compile(r"^-?[0-9.]+ [nµ]A$")).wait_for()
    check("%s Watch shows the profiler's block mean with an SI prefix, and the session says samples" % tag, True)
    page.get_by_role("img", name=re.compile(r"^Readings over")).wait_for()
    check("%s the chart is drawn, with zero on its axis" % tag, "bottom of the chart:" in page.inner_text("body"))
    check("%s while it runs the switch and the profiler's own controls are locked" % tag,
          page.get_by_role("group", name="Instrument").get_by_role("button", name="Multimeter").is_disabled()
          and "cannot be asked or changed until it ends" in page.get_by_label("Profiler").inner_text() and page.get_by_role("button", name="Ampere mode").count() == 0)
    page.wait_for_function("document.querySelector('[aria-label=\"Session\"]').innerText.match(/[1-9][0-9,]* samples/)")
    shot("2-watching")
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    figures = page.get_by_label("Figures")
    check("%s after Stop the figures say lowest, highest, average and the charge used" % tag,
          all(w in figures.inner_text() for w in ("Lowest", "Highest", "Average", "Charge used")), figures.inner_text())
    check("%s the note says what a point on the chart is" % tag, "Each point on the chart is 0.2 s" in page.inner_text("body"))
    check("%s the unit is asked again once the session is over (the card is back)" % tag,
          page.get_by_label("Profiler").inner_text().startswith("Power profiler E1C5F15B15A3"), page.get_by_label("Profiler").inner_text())
    check("%s a clean run says nothing about lost samples" % tag, "were lost between" not in page.inner_text("body"))

    # lost samples are said
    bench.unit.lost_at = {6000: 5}
    bench.unit.index = bench.unit.counter = 0
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, ")).wait_for()
    page.get_by_text(re.compile(r"5 samples were lost between the unit and the Pi")).wait_for()
    check("%s lost samples are counted and the page says so" % tag, True)
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    shot("3-lost")

    # a recording and its download
    page.get_by_label("Name for the recording").fill("profile-1")
    page.get_by_role("button", name="Record", exact=True).click()
    page.get_by_text(re.compile(r"^recording, .*to profile-1\.csv")).wait_for()
    page.get_by_text("recording now").wait_for()
    page.wait_for_function("document.querySelector('[aria-label=\"Session\"]').innerText.match(/[1-9][0-9,]* samples/)")
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    listing = page.get_by_label("Recordings")
    listing.get_by_text(re.compile(r"[1-9][0-9,]* samples")).wait_for()
    check("%s the finished recording is listed as the power profiler's, with its samples" % tag, "power profiler" in listing.inner_text() and "profile-1.csv" in listing.inner_text(), listing.inner_text())
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download profile-1").click()
    with open(dl.value.path()) as fh:
        text = fh.read().splitlines()
    cells = text[1].split(",")
    check("%s Download gives the CSV with min, max and n after the old columns" % tag,
          text[0] == "t_s,wall,value,unit,function,range,nplc,overload,min,max,n" and cells[3] == "A" and cells[10] == "20000" and float(cells[8]) <= float(cells[2]) <= float(cells[9]), text[:2])
    check("%s no sideways scroll on the Meter page" % tag, no_sideways_scroll(page))
    shot("4-recorded")

    # back to the multimeter
    page.get_by_role("group", name="Instrument").get_by_role("button", name="Multimeter").click()
    page.get_by_text("The meter", exact=True).wait_for()
    check("%s switching back shows the multimeter's card and not the profiler's" % tag, page.get_by_text("The power profiler", exact=True).count() == 0)
    page.get_by_role("group", name="Instrument").get_by_role("button", name="Power profiler").click()
    page.get_by_label("Profiler").wait_for()


def controls(page, bench, tag, shot):
    """Mode and supply: the tick gates them, and a refusal says why. The unit is in source mode with its output unknown."""
    check("%s source mode is the current one and cannot be pressed again" % tag, page.get_by_role("button", name="Source mode").is_disabled())
    check("%s Switch on is locked until the tick is given" % tag, page.get_by_role("button", name="Switch on").is_disabled())
    page.get_by_role("button", name="Ampere mode").click()
    page.get_by_text("supply_unconfirmed").wait_for()
    check("%s leaving source mode with the output unknown is refused and says why, and the unit was not told" % tag,
          "may be powering the board" in page.inner_text("body") and (ppk2.MODE, 1) not in bench.unit.writes)
    shot("5-refused")
    page.get_by_label("I mean to change what powers the board").check()
    page.get_by_role("button", name="Ampere mode").click()
    page.get_by_label("Profiler").filter(has_text="ampere mode").wait_for()
    check("%s with the tick it goes to ampere mode, and then there is no voltage or switch to offer" % tag,
          (ppk2.MODE, 1) in bench.unit.writes and page.get_by_role("button", name="Switch on").count() == 0)
    page.get_by_role("button", name="Source mode").click()
    page.get_by_label("Profiler").filter(has_text="source mode").wait_for()
    page.get_by_label("Source voltage in millivolts").fill("3300")
    page.get_by_role("button", name="Switch on").click()
    page.get_by_label("Profiler").filter(has_text="output on at 3300 mV").wait_for()
    check("%s Switch on sets the voltage first and then the output, and the page says on at 3300 mV" % tag,
          [w[0] for w in bench.unit.writes if w[0] in (ppk2.VOLTAGE, ppk2.POWER)][-2:] == [ppk2.VOLTAGE, ppk2.POWER] and bench.unit.power is True)
    shot("6-on")
    page.get_by_role("button", name="Switch off").click()
    page.get_by_label("Profiler").filter(has_text="output off").wait_for()
    check("%s Switch off needs no tick and says off" % tag, bench.unit.power is False)
    page.get_by_label("Source voltage in millivolts").fill("12")
    check("%s a voltage outside 800 to 5000 cannot be switched on" % tag, page.get_by_role("button", name="Switch on").is_disabled())
    check("%s no sideways scroll with the controls" % tag, no_sideways_scroll(page))
    bad = [w for w in bench.unit.writes if not (w in ALLOWED or (len(w) == 3 and w[0] == ppk2.VOLTAGE))]
    check("%s nothing the page did made the service write a byte outside the six shapes" % tag, not bad, bad)


def run(browser, theme, size, width, height, shots):
    tag = "%s %s" % (theme, size)
    fake = FakeMeter(CURRENT)
    bench = Bench(meter_address="127.0.0.1:%d" % fake.port)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("response", lambda r: bad.append((r.status, "/".join(r.url.split("?")[0].rsplit("/", 2)[-2:]))) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    shot = lambda name: page.screenshot(path=os.path.join(shots, "%s-%s-%s.png" % (theme, size, name)), full_page=True)
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        cards(page, bench, tag, shot)
        meter_page(page, bench, fake, tag, shot)
        controls(page, bench, tag, shot)
        check("%s the only failing response was the deliberate refusal (409 from ppk2/mode)" % tag, bad == [(409, "ppk2/mode")], bad)
        check("%s no page or console errors" % tag, not errors, errors)
        check("%s the shell port was never opened" % tag, all(n.endswith("ttyACM1") for n in bench.opened), bench.opened)
    finally:
        context.close()
        bench.close()
        fake.close()


def run_alone(browser):
    """A bench with only the profiler: no switch, the Meter page is its page."""
    bench = Bench(with_meter=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.get_by_label("Profiler").wait_for()
        check("alone: the Meter page is there and has the profiler's card and no switch", page.get_by_role("group", name="Instrument").count() == 0)
        page.get_by_role("button", name="Watch", exact=True).click()
        page.get_by_text(re.compile(r"^watching, .* samples")).wait_for()
        page.get_by_role("button", name="Stop").click()
        page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
        check("alone: a watch runs from the profiler alone", True)
        go_to(page, "Devices")
        check("alone: no multimeter card, only the profiler's", page.get_by_role("button", name="Check the meter").count() == 0 and page.get_by_role("button", name="Check the profiler").count() == 1)
    finally:
        context.close()
        bench.close()


def run_without(browser):
    bench = Bench(with_meter=False, with_ppk2=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Devices")
        page.get_by_role("heading", name="Devices").wait_for()
        check("without the profiler configured there is no card and no Meter page",
              page.get_by_role("button", name="Check the profiler").count() == 0 and page.get_by_role("navigation", name="Pages").get_by_text("Meter", exact=True).count() == 0)
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-ppk2-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        try:
            for theme in THEMES:
                for size, width, height in SIZES:
                    run(browser, theme, size, width, height, shots)
            run_alone(browser)
            run_without(browser)
        finally:
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
