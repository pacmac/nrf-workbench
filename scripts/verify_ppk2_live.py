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


def open_details(page, testid):
    """The Meter page folds away the instrument's settings and the stages section (docs/specs/meter-page.md, "Live and replay"): open one if it is shut."""
    d = page.get_by_test_id(testid)
    if d.get_attribute("open") is None:
        d.locator("summary").click()


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
    check("%s the instrument's settings are folded away until asked for" % tag, page.get_by_test_id("instrument-details").get_attribute("open") is None and not page.get_by_text("The meter", exact=True).is_visible())
    open_details(page, "instrument-details")
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
    page.get_by_role("img", name=re.compile(r"^Readings from")).wait_for()
    check("%s the chart is drawn, with a labelled axis from zero and times along the bottom" % tag,
          "The scale starts at 0 A" in page.inner_text("body") and page.get_by_test_id("y-axis").inner_text().startswith("0 A") and page.get_by_test_id("x-axis").inner_text().strip() != "")
    check("%s while it runs the switch and the profiler's own controls are locked" % tag,
          page.get_by_role("group", name="Instrument").get_by_role("button", name="Multimeter").is_disabled()
          and "cannot be asked or changed until it ends" in page.get_by_label("Profiler").inner_text() and page.get_by_role("button", name="Ampere mode").count() == 0)
    page.wait_for_function("document.querySelector('[aria-label=\"Session\"]').innerText.match(/[1-9][0-9,]* samples/)")
    held = page.get_by_label("Profiler").inner_text()
    check("%s while a session holds the unit the card still says what it is set to, with the source voltage in mV (Peter, 2026-11-20)" % tag,
          held.startswith("source mode, output state unknown (4000 mV when on). A session is using the unit, so it cannot be asked or changed until it ends."), held)
    check("%s and it names where that voltage came from, since this service did not set it" % tag,
          "4000 mV is the unit's own figure when it was read" in page.inner_text("body"))
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
    open_details(page, "stages-details")
    check("%s a steady load keeps no wake, and the stages section says so" % tag, page.get_by_text("No wake kept yet.").is_visible())

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
        page.get_by_role("button", name="Save profile-1 as a CSV file").click()
    with open(dl.value.path()) as fh:
        text = fh.read().splitlines()
    cells = text[1].split(",")
    check("%s Save CSV gives the CSV with min, max and n after the old columns" % tag,
          text[0] == "t_s,wall,value,unit,function,range,nplc,overload,min,max,n" and cells[3] == "A" and cells[10] == "20000" and float(cells[8]) <= float(cells[2]) <= float(cells[9]), text[:2])
    check("%s no sideways scroll on the Meter page" % tag, no_sideways_scroll(page))
    shot("4-recorded")

    # battery life from the wake/sleep cycle (docs/specs/meter-page.md): a unit that sleeps and wakes for one block (0.2 s) every 2 s
    bench.unit.profile = lambda i: (30000 if (i // 20000) % 10 == 3 else 1000, 0)
    bench.unit.index = bench.unit.counter = 0
    bench.unit.lost_at = {}
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, ")).wait_for()
    page.get_by_label("Battery capacity in milliamp hours").fill("1000")
    life = page.get_by_label("Battery life")
    page.wait_for_function("(() => { const m = document.querySelector('[aria-label=\"Session\"]').innerText.match(/([0-9,]+) samples/); return m && Number(m[1].replace(/,/g, '')) >= 850000; })()", timeout=30000)
    check("%s the choice starts on the whole recording" % tag,
          page.get_by_role("button", name="Whole recording", exact=True).get_attribute("aria-pressed") == "true" and "whole recording it lasts" in life.inner_text(), life.inner_text())
    page.get_by_role("button", name="Wake/sleep cycle").click()
    check("%s choosing the cycle presses that button and not the other" % tag,
          page.get_by_role("button", name="Wake/sleep cycle").get_attribute("aria-pressed") == "true" and page.get_by_role("button", name="Whole recording", exact=True).get_attribute("aria-pressed") == "false")
    page.get_by_label("Battery life").filter(has_text=re.compile(r"^Wakes every 2 s \(3 cycles counted")).wait_for()
    check("%s the cycle line gives the period, the cycles, the average over them and the life" % tag,
          re.match(r"^Wakes every 2 s \(3 cycles counted\)\. Average over them [0-9.]+ [nµm]A\. It lasts [0-9.]+ (hours|days|years)\.$", life.inner_text()) is not None, life.inner_text())
    check("%s the chart marks each wake it found" % tag, page.get_by_test_id("wake-mark").count() == 4, page.get_by_test_id("wake-mark").count())
    table = page.get_by_role("table", name="Sleep and wake")
    table.wait_for()
    rows = [table.get_by_role("row").nth(i).inner_text().replace("\n", " ") for i in range(4)]
    check("%s the table has Wake, Sleep and Cycle rows under its headings" % tag,
          "Time per cycle" in rows[0] and rows[1].startswith("Wake") and rows[2].startswith("Sleep") and rows[3].startswith("Cycle"), rows)
    check("%s a wake of one 0.2 s block says its length is under two points, and gives its exact peak" % tag,
          "under 0.4 s" in rows[1] and "at least" in rows[1] and re.search(r"peak [0-9.]+ [nµm]A", rows[1]) is not None, rows[1])
    check("%s the sleep row says over, where the wake row says under, so the shares never exceed the whole" % tag, "over 1.6 s" in rows[2] and "over 80 %" in rows[2], rows[2])
    check("%s the table says why there is no energy, and the note about one-point wakes is there" % tag,
          "No energy: the supply voltage is not known here" in page.inner_text("body") and "Each wake fills a single point of the chart" in page.inner_text("body"))
    check("%s the chart shades the four wakes" % tag, page.get_by_test_id("wake-span").count() == 4, page.get_by_test_id("wake-span").count())
    check("%s no sideways scroll with the cycle line and the table" % tag, no_sideways_scroll(page))

    # the wakes kept at full rate, as a scope trace (docs/specs/ppk2-stages.md, stage 1)
    page.get_by_text("The wake at full rate", exact=True).wait_for()
    pick = page.get_by_label("Wake", exact=True)
    pick.locator("option").nth(2).wait_for(state="attached")
    options = [o.inner_text() for o in pick.locator("option").all()]
    check("%s the wakes the unit kept are listed, each with its length and peak" % tag,
          len(options) >= 3 and all(re.match(r"^wake \d+, 600 ms, peak [0-9.]+ [nµm]A$", o) for o in options), options)
    page.get_by_role("img", name=re.compile(r"^Current of wake [0-9]+, from -200 ms to 400 ms$")).wait_for()
    check("%s the wake is drawn from 200 ms before it started, with its start marked once" % tag, page.get_by_test_id("wake-start").count() == 1)
    wf = page.get_by_label("Wake figures")
    check("%s the wake's figures say how long, the average, the peak and the charge" % tag,
          all(w in wf.inner_text() for w in ("Shown", "Average", "Peak", "Charge")) and "600 ms" in wf.inner_text(), wf.inner_text())
    stages_section = page.get_by_test_id("stages-details")
    linear = stages_section.get_by_text(re.compile(r"^bottom of the chart: 0 A$"))
    live_scale = page.get_by_text(re.compile(r"^The scale starts at 0 A\."))
    check("%s the wake's axis starts linear, with zero at the bottom (the live scope's says the same)" % tag,
          linear.count() == 1 and live_scale.count() == 1 and page.get_by_text(re.compile(r"^log scale")).count() == 0)
    stages_section.get_by_label("Log current axis").check()
    page.get_by_text(re.compile(r"^log scale, bottom [0-9.]+ [nµ]A$")).wait_for()
    check("%s the log switch changes the wake's axis and says where its floor is; the live scope is untouched" % tag, linear.count() == 0 and live_scale.count() == 1)
    box = page.get_by_role("img", name=re.compile(r"^Current of wake")).bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.37, box["y"] + box["height"] / 2)
    check("%s dragging shows the stretch being chosen" % tag, page.get_by_test_id("zoom-selection").count() == 1)
    page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] / 2)
    page.mouse.up()
    page.get_by_role("button", name="Show all of it").wait_for()
    page.wait_for_function("(() => { const t = document.querySelector('[aria-label=\"Wake figures\"]').innerText; return !t.includes('600 ms') && /Shown\\s+[0-9.]+ ms/.test(t); })()")
    shown = re.search(r"Shown\s+([0-9.]+) ms", wf.inner_text()).group(1)
    check("%s dragging across the chart zooms to about 15 percent of the wake (90 ms)" % tag, 70 <= float(shown) <= 110, shown)
    page.get_by_role("button", name="Show all of it").click()
    page.wait_for_function("document.querySelector('[aria-label=\"Wake figures\"]').innerText.includes('600 ms')")
    check("%s Show all of it returns to the whole wake" % tag, page.get_by_role("button", name="Show all of it").count() == 0)
    check("%s no sideways scroll with the scope" % tag, no_sideways_scroll(page))
    page.get_by_text("The wake at full rate", exact=True).scroll_into_view_if_needed()
    shot("5b-scope")
    shot("5-cycle")
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    page.get_by_role("button", name="Whole recording", exact=True).click()
    check("%s going back to the whole recording drops the marks and says so" % tag,
          page.get_by_test_id("wake-mark").count() == 0 and page.get_by_test_id("wake-span").count() == 0 and page.get_by_role("table", name="Sleep and wake").count() == 0
          and "whole recording it lasts" in life.inner_text(), life.inner_text())
    bench.unit.profile = lambda i: (1000 + (i % 7), 0)
    bench.unit.index = bench.unit.counter = 0

    # stages are named on a recording's wakes and kept with it (docs/specs/ppk2-stages.md, stage 2)
    bench.unit.profile = lambda i: (30000 if (i // 20000) % 10 == 3 else 1000, 0)
    bench.unit.index = bench.unit.counter = 0
    page.get_by_label("Name for the recording").fill("stage-1")
    page.get_by_role("button", name="Record", exact=True).click()
    page.get_by_text(re.compile(r"^recording, .*to stage-1\.csv")).wait_for()
    pick = page.get_by_label("Wake", exact=True)
    pick.locator("option").nth(2).wait_for(state="attached", timeout=40000)
    check("%s with a recording behind the wakes the page says how to name a stage, and there are none yet" % tag,
          "Drag across the chart to select a stretch, then name it" in page.inner_text("body") and page.get_by_role("table", name="Stages across the wakes").count() == 0)
    trace = page.get_by_role("img", name=re.compile(r"^Current of wake"))
    trace.scroll_into_view_if_needed()
    box = trace.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] / 2)
    page.mouse.up()
    page.get_by_text(re.compile(r"^Selected: ")).wait_for()
    sel = page.get_by_label("Selected stretch").inner_text()
    check("%s the selected stretch is shown in milliseconds from the wake's start" % tag, re.match(r"^Selected: -?[0-9.]+ ms to -?[0-9.]+ ms$", sel) is not None, sel)
    check("%s a stage cannot be added without a name" % tag, page.get_by_role("button", name="Add as a stage").is_disabled())
    page.get_by_label("Stage name").fill("burst")
    page.get_by_role("button", name="Add as a stage").click()
    stages = page.get_by_role("table", name="Stages across the wakes")
    stages.wait_for()
    st = stages.inner_text().replace("\n", " ")
    check("%s the stage appears in a table with its stretch, average, peak, charge and share" % tag,
          "burst" in st and re.search(r"[0-9.]+ ms to [0-9.]+ ms", st) is not None and "of the named stages" in st and "100 %" in st, st)
    check("%s the stage is shaded on the trace and named in the colour legend" % tag,
          page.get_by_test_id("stage-span").count() == 1 and page.get_by_label("Stage colours").inner_text().strip() == "burst")
    check("%s no sideways scroll with the stage table" % tag, no_sideways_scroll(page))
    page.get_by_text("The wake at full rate", exact=True).scroll_into_view_if_needed()
    shot("5c-stages")
    page.get_by_role("button", name="Show all of it").click()
    page.get_by_role("button", name="Remove the stage burst").click()
    page.get_by_role("table", name="Stages across the wakes").wait_for(state="detached")
    check("%s removing the stage takes it off the table and the trace" % tag, page.get_by_test_id("stage-span").count() == 0)

    # a stage for only the odd wakes, beside one for all of them (docs/specs/ppk2-stages.md, "Wakes of different kinds")
    def drag_stretch(a, b):
        trace.scroll_into_view_if_needed()
        bx = trace.bounding_box()
        page.mouse.move(bx["x"] + bx["width"] * a, bx["y"] + bx["height"] / 2)
        page.mouse.down()
        page.mouse.move(bx["x"] + bx["width"] * b, bx["y"] + bx["height"] / 2)
        page.mouse.up()
        page.get_by_text(re.compile(r"^Selected: ")).wait_for()

    drag_stretch(0.30, 0.45)
    page.get_by_label("Stage name").fill("burst")
    page.get_by_role("button", name="Add as a stage").click()
    page.get_by_role("table", name="Stages across the wakes").wait_for()
    page.get_by_role("button", name="Show all of it").click()
    page.wait_for_function("document.querySelector('[aria-label=\"Wake figures\"]').innerText.includes('600 ms')")          # the whole wake is back before a stretch is chosen on it
    drag_stretch(0.60, 0.70)
    page.get_by_label("Stage name").fill("late")
    page.get_by_label("Applies to").select_option("odd")
    page.get_by_role("button", name="Add as a stage").click()
    stage_table = page.get_by_role("table", name="Stages across the wakes")
    stage_table.get_by_text(re.compile(r"^Odd wakes")).wait_for()
    st2 = stage_table.inner_text().replace("\n", " ")
    check("%s the table groups the stages by the wakes they are for, each group naming its wakes" % tag,
          re.search(r"All wakes\s*\(1, 2, 3", st2) is not None and re.search(r"Odd wakes\s*\(1, 3", st2) is not None, st2)
    page.get_by_label("Wake", exact=True).select_option("1")
    page.wait_for_function("document.querySelectorAll('[data-testid=\"stage-span\"]').length === 2", timeout=10000)
    check("%s an odd wake shows both stages on its trace" % tag, page.get_by_test_id("stage-span").count() == 2, page.get_by_test_id("stage-span").count())
    page.get_by_label("Wake", exact=True).select_option("2")
    page.wait_for_function("document.querySelectorAll('[data-testid=\"stage-span\"]').length === 1")
    check("%s an even wake shows only the stage that is for all wakes" % tag, page.get_by_test_id("stage-span").count() == 1)
    check("%s the colour legend follows the wake on show" % tag, page.get_by_label("Stage colours").inner_text().strip() == "burst")
    check("%s no sideways scroll with the grouped table" % tag, no_sideways_scroll(page))
    page.get_by_text("The wake at full rate", exact=True).scroll_into_view_if_needed()
    shot("5d-kinds")
    page.get_by_role("button", name="Remove the stage late").click()
    page.get_by_role("button", name="Remove the stage burst").click()
    page.get_by_role("table", name="Stages across the wakes").wait_for(state="detached")
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    page.get_by_role("button", name="Replay stage-1", exact=True).click()
    page.get_by_label("Wake", exact=True).locator("option").nth(2).wait_for(state="attached")
    check("%s a finished recording's wakes open from the Recordings list" % tag, page.get_by_label("Wake", exact=True).locator("option").count() >= 3)
    bench.unit.profile = lambda i: (1000 + (i % 7), 0)
    bench.unit.index = bench.unit.counter = 0

    replay_scope(page, tag, shot)

    # a wake of 21 s like the bench RAK's (a burst, a 20 s receive window at a tenth of the current, a burst) is ONE wake on the scope, and can be zoomed into
    # (docs/specs/ppk2-stages.md, "The window"); 125 quiet blocks first so that the sleep level (the median block) is asleep through the wake. Desktop only: it takes 50 s at the real rate.
    if tag.endswith("desktop"):
        page.get_by_role("button", name="Live", exact=True).click()
        bench.unit.profile = lambda i: (30000 if (125 <= i // 20000 < 128 or 228 <= i // 20000 < 230) else 3000 if 128 <= i // 20000 < 228 else 1000, 0)
        bench.unit.index = bench.unit.counter = 0
        bench.unit.lost_at = {}
        page.get_by_role("button", name="Watch", exact=True).click()
        page.get_by_text(re.compile(r"^watching, ")).wait_for()
        pick = page.get_by_label("Wake", exact=True)
        try:
            pick.locator("option").filter(has_text=re.compile(r"^wake \d+, 21\.4 s, peak")).wait_for(state="attached", timeout=90000)
        except Exception:                                                              # say what the list held, then go on to the end of the scenario
            pass
        options = [o.inner_text() for o in pick.locator("option").all()]
        if not check("%s the awake period is one wake of 21.4 s, not a burst and another 21 s later" % tag,
                     len(options) == 1 and re.match(r"^wake 1, 21\.4 s, peak [0-9.]+ [nµm]A$", options[0]) is not None, (options, page.get_by_label("Session", exact=True).inner_text()[:200])):
            page.get_by_role("button", name="Stop").click()
            page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
            raise SystemExit(1)
        page.get_by_role("img", name=re.compile(r"^Current of wake 1, from -0\.2 s to 21\.2 s$")).wait_for()
        wf = page.get_by_label("Wake figures")
        whole = wf.inner_text()
        check("%s the figures say 21.4 s is shown" % tag, "21.4 s" in whole, whole)
        trace = page.get_by_role("img", name=re.compile(r"^Current of wake"))
        trace.scroll_into_view_if_needed()
        shot("5e-long-wake")
        bx = trace.bounding_box()
        page.mouse.move(bx["x"] + bx["width"] * 0.45, bx["y"] + bx["height"] / 2)
        page.mouse.down()
        page.mouse.move(bx["x"] + bx["width"] * 0.50, bx["y"] + bx["height"] / 2)
        page.mouse.up()
        page.get_by_role("button", name="Show all of it").wait_for()
        page.wait_for_function("(() => { const t = document.querySelector('[aria-label=\"Wake figures\"]').innerText; return !t.includes('21.4 s') && /Shown\\s+[0-9.]+ s/.test(t); })()")
        zoomed = wf.inner_text()
        shown = float(re.search(r"Shown\s+([0-9.]+) s", zoomed).group(1))
        peak_whole = re.search(r"Peak\s+([0-9.]+ [nµm]A)", whole).group(1)
        peak_zoom = re.search(r"Peak\s+([0-9.]+ [nµm]A)", zoomed).group(1)
        check("%s dragging across the receive window zooms to about a second of it, whose peak is the window's and not the burst's" % tag,
              0.9 <= shown <= 1.4 and peak_zoom != peak_whole, (shown, peak_whole, peak_zoom))
        page.get_by_role("button", name="Stop").click()
        page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
        bench.unit.profile = lambda i: (1000 + (i % 7), 0)
        bench.unit.index = bench.unit.counter = 0

    # back to the multimeter
    page.get_by_role("group", name="Instrument").get_by_role("button", name="Multimeter").click()
    page.get_by_text("The meter", exact=True).wait_for()
    check("%s switching back shows the multimeter's card and not the profiler's" % tag, page.get_by_text("The power profiler", exact=True).count() == 0)
    page.get_by_role("group", name="Instrument").get_by_role("button", name="Power profiler").click()
    page.get_by_label("Profiler").wait_for()


def replay_scope(page, tag, shot):
    """A recording on the scope (docs/specs/meter-page.md, "Live and replay" and "Events"): the whole of it, the axes, the pointer readout, zoom and pan, the overview, an event marked with two
    clicks and named, renamed and deleted, and the recording deleted. Starts with the recording stage-1 already replayed."""
    tabs = page.get_by_role("group", name="What the scope shows")
    check("%s the scope is on the replay of the recording, and the Live button is there to go back" % tag,
          tabs.get_by_role("button", name="Replay: stage-1").get_attribute("aria-pressed") == "true" and tabs.get_by_role("button", name="Live", exact=True).get_attribute("aria-pressed") == "false")
    trace = page.get_by_role("img", name=re.compile(r"^Current of stage-1, from 0 s to "))
    trace.wait_for()
    bar = page.get_by_label("Stretch shown", exact=True)
    check("%s a replay shows the whole recording: all of it, from 0 s" % tag, "all of it" in bar.inner_text() and bar.inner_text().startswith("0 s to "), bar.inner_text())
    check("%s with all of it shown there is nowhere to go back to or zoom out to, and zooming in is possible" % tag,
          page.get_by_role("button", name="Show the whole recording").is_disabled() and page.get_by_role("button", name="Back half a screen").is_disabled()
          and page.get_by_role("button", name="Forward half a screen").is_disabled() and page.get_by_role("button", name="Zoom out").is_disabled()
          and page.get_by_role("button", name="Zoom in").is_enabled())
    ylabels = page.get_by_test_id("y-axis").inner_text().split("\n")
    check("%s the vertical axis has labelled values in the unit, from zero" % tag, len(ylabels) >= 2 and ylabels[0].endswith("A") and re.match(r"^[0-9.]+ [nµm]?A$", ylabels[-1]) is not None, ylabels)
    check("%s the time axis is labelled" % tag, len([s for s in page.get_by_test_id("x-axis").inner_text().split("\n") if s.strip()]) >= 3, page.get_by_test_id("x-axis").inner_text())
    trace.scroll_into_view_if_needed()
    box = trace.bounding_box()
    read = page.get_by_label("Under the pointer")
    check("%s before the pointer is over it the readout says how to use the trace" % tag, "Move over the trace" in read.inner_text(), read.inner_text())
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    check("%s over the trace the pointer reads out the time and the lowest, average and highest of the point under it" % tag,
          re.match(r"^[0-9.:]+ (s|ms) · lowest [0-9.]+ [nµm]A · average [0-9.]+ [nµm]A · highest [0-9.]+ [nµm]A · each point 200 ms$", read.inner_text()) is not None, read.inner_text())
    check("%s and a cursor line follows it" % tag, page.get_by_test_id("cursor").count() == 1)
    page.mouse.move(box["x"] + box["width"] * 0.1, box["y"] + box["height"] / 2)
    first = read.inner_text()
    page.mouse.move(box["x"] + box["width"] * 0.9, box["y"] + box["height"] / 2)
    check("%s moving along the trace changes the time it reads" % tag, first.split(" · ")[0] != read.inner_text().split(" · ")[0], (first, read.inner_text()))
    page.mouse.move(box["x"] - 40, box["y"] - 40)
    shot("7-replay")
    page.get_by_role("button", name="Zoom in").click()
    page.wait_for_function("document.querySelector('[aria-label=\"Stretch shown\"]').innerText.includes(' of ')")
    zoomed = bar.inner_text()
    check("%s zooming in halves the stretch and says how much of the recording it is" % tag, "of" in zoomed and "all of it" not in zoomed, zoomed)
    check("%s once zoomed, Whole, Back, Forward and Zoom out are there" % tag, all(page.get_by_role("button", name=n).is_enabled() for n in
          ("Show the whole recording", "Back half a screen", "Forward half a screen", "Zoom out")))
    lit = page.get_by_test_id("overview-window").bounding_box()
    strip = page.get_by_test_id("overview").bounding_box()
    check("%s the overview lights the part shown, about half of the whole" % tag, 0.3 < lit["width"] / strip["width"] < 0.7, (lit, strip))
    page.get_by_role("button", name="Forward half a screen").click()
    page.wait_for_function("(o => !document.querySelector('[aria-label=\"Stretch shown\"]').innerText.startsWith(o))", arg=zoomed.split(" to ")[0] + " to ")
    moved = bar.inner_text()
    check("%s Forward moves the stretch later and keeps its length" % tag, moved.split(" to ")[0] != zoomed.split(" to ")[0] and moved.split(", ")[1] == zoomed.split(", ")[1], (zoomed, moved))
    lit2 = page.get_by_test_id("overview-window").bounding_box()
    check("%s the lit part of the overview moved right with it" % tag, lit2["x"] > lit["x"] + 1, (lit, lit2))
    page.get_by_role("button", name="Back half a screen").click()
    page.wait_for_function("(o => document.querySelector('[aria-label=\"Stretch shown\"]').innerText.startsWith(o))", arg=zoomed.split(" to ")[0] + " to ")
    check("%s Back returns it" % tag, bar.inner_text().startswith(zoomed.split(" to ")[0] + " to "), bar.inner_text())
    page.get_by_label("Scope trace, focus it to use the keys").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_function("(o => !document.querySelector('[aria-label=\"Stretch shown\"]').innerText.startsWith(o))", arg=zoomed.split(" to ")[0] + " to ")
    page.keyboard.press("0")
    page.wait_for_function("document.querySelector('[aria-label=\"Stretch shown\"]').innerText.includes('all of it')")
    check("%s the keys move it (right arrow) and show all of it again (0)" % tag, "all of it" in bar.inner_text())

    # an event by two clicks
    page.get_by_role("button", name="Mark an event").click()
    check("%s Mark an event says what the clicks do" % tag, "Click the trace where the event starts" in read.inner_text(), read.inner_text())
    trace.scroll_into_view_if_needed()
    box = trace.bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2)
    check("%s the first click puts a line where the event starts" % tag, page.get_by_test_id("pending-start").count() == 1 and page.get_by_label("Event name").count() == 0)
    page.get_by_label("Scope trace, focus it to use the keys").focus()
    page.keyboard.press("Escape")
    check("%s Escape drops it" % tag, page.get_by_test_id("pending-start").count() == 0)
    page.mouse.click(box["x"] + box["width"] * 0.55, box["y"] + box["height"] / 2)
    page.mouse.click(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2)           # the later click first: the earlier one is the start
    name = page.get_by_label("Event name")
    name.wait_for()
    check("%s the second click asks for a name, with a default selected" % tag, name.input_value() == "event 1", name.input_value())
    note = page.get_by_label("New event").inner_text()
    check("%s the new event says where it runs and how long" % tag, re.search(r"New event from [0-9.:]+ s to [0-9.:]+ s, ", note) is not None, note)
    name.fill("burst")
    page.get_by_role("button", name="Save the event").click()
    table = page.get_by_role("table", name="Events")
    table.wait_for()
    check("%s the saved event is listed with its length, average, peak and charge" % tag,
          all(w in table.inner_text() for w in ("burst", "Length", "Average current", "Peak", "Charge", "from the readings")) and re.search(r"[0-9.]+ [nµm]A", table.inner_text()) is not None, table.inner_text())
    check("%s it is shaded between two lines, named on the trace" % tag,
          page.get_by_test_id("event-span").count() == 1 and page.get_by_test_id("event-start").count() == 1 and page.get_by_test_id("event-end").count() == 1
          and page.get_by_test_id("event-name").inner_text() == "burst")
    span = page.get_by_test_id("event-span").bounding_box()
    check("%s and the shading is where it was clicked, about a quarter of the width" % tag, 0.15 < span["width"] / trace.bounding_box()["width"] < 0.35, span)
    shot("8-event")
    page.get_by_role("button", name="Rename burst").click()
    page.get_by_label("New name for burst").fill("tx burst")
    page.get_by_role("button", name="Save", exact=True).click()
    page.get_by_role("table", name="Events").get_by_text("tx burst").wait_for()
    check("%s an event can be renamed" % tag, page.get_by_test_id("event-name").inner_text() == "tx burst" and page.get_by_role("table", name="Events").get_by_text("burst", exact=True).count() == 0)
    page.get_by_role("button", name="Move the end of tx burst").click()
    check("%s Move end asks for a click" % tag, "Click the trace where tx burst should end" in page.inner_text("body"))
    trace.scroll_into_view_if_needed()
    box = trace.bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.80, box["y"] + box["height"] / 2)
    page.wait_for_function("document.querySelector('[data-testid=\"event-span\"]').getBoundingClientRect().width > %s" % (box["width"] * 0.35))
    check("%s the end line moved with the click, and the event is still the one event" % tag, page.get_by_test_id("event-span").count() == 1)
    page.get_by_role("button", name="Show tx burst").click()
    page.wait_for_function("!document.querySelector('[aria-label=\"Stretch shown\"]').innerText.includes('all of it')")
    check("%s Show zooms to the event" % tag, "of" in bar.inner_text() and "all of it" not in bar.inner_text(), bar.inner_text())
    page.get_by_role("button", name="Live", exact=True).click()
    page.get_by_role("button", name="Replay stage-1", exact=True).click()
    page.get_by_role("table", name="Events").wait_for()
    check("%s the event is kept with the recording: closing the replay and opening it again shows it" % tag, page.get_by_role("table", name="Events").get_by_text("tx burst").count() == 1)
    page.get_by_role("button", name="Delete the event tx burst").click()
    page.get_by_text(re.compile(r"^No events yet")).wait_for()
    check("%s deleting the event takes it off the trace and the table" % tag, page.get_by_test_id("event-span").count() == 0)
    check("%s no sideways scroll with the replay" % tag, no_sideways_scroll(page))

    # delete a recording
    listing = page.get_by_label("Recordings")
    check("%s each recording has Replay, Save CSV and Delete" % tag, all(listing.get_by_role("button", name=n).count() >= 1 for n in ("Replay stage-1", "Save stage-1 as a CSV file", "Delete stage-1")))
    page.get_by_role("button", name="Delete profile-1", exact=True).click()
    check("%s Delete asks first, and says what goes with the recording" % tag, "This cannot be undone" in listing.inner_text() and "profile-1.csv" in listing.inner_text())
    page.get_by_role("button", name="Keep it").click()
    check("%s Keep it changes nothing" % tag, "profile-1.csv" in listing.inner_text() and "This cannot be undone" not in listing.inner_text())
    page.get_by_role("button", name="Delete profile-1", exact=True).click()
    page.get_by_role("button", name="Yes, delete profile-1").click()
    page.wait_for_function("!document.querySelector('[aria-label=\"Recordings\"]').innerText.includes('profile-1.csv')")
    check("%s Yes, delete takes it off the list and leaves the others" % tag, "profile-1.csv" not in listing.inner_text() and "stage-1.csv" in listing.inner_text(), listing.inner_text())
    page.get_by_role("button", name="Delete stage-1", exact=True).click()
    page.get_by_role("button", name="Yes, delete stage-1").click()
    page.wait_for_function("!document.querySelector('[aria-label=\"Recordings\"]') || !document.querySelector('[aria-label=\"Recordings\"]').innerText.includes('stage-1.csv')")
    check("%s deleting the recording that is on the scope puts the scope back on the live session" % tag,
          page.get_by_role("group", name="What the scope shows").get_by_role("button", name=re.compile(r"^Replay: ")).count() == 0
          and page.get_by_role("group", name="What the scope shows").get_by_role("button", name="Live", exact=True).get_attribute("aria-pressed") == "true")
    shot("9-deleted")


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
        open_details(page, "instrument-details")
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
