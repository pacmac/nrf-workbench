#!/usr/bin/env python3
"""The Meter page's replay scope and its events, in a real browser (docs/specs/meter-page.md, "Live and replay" and "Events").

    python3 scripts/verify_meter_replay.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs with a PPK2 and a FAKE UNIT paced at the real 100,000 samples a second, which wakes for one 0.2 s block in every ten. A recording of about
five seconds is made through the page and replayed. In light and dark at 1280x900:
  whole       a replay shows the whole recording from 0 s, with nowhere to go back to or zoom out to; the axes are labelled from zero; the pointer reads the time and the lowest, average and highest
              of the point under it, and a cursor line follows it;
  move        zoom in halves the stretch and says how much of the recording it is; the overview lights that part and follows Forward and Back; the keys move it and 0 shows all of it again;
  events      Mark an event, two clicks and a name: the first click puts a line, Escape drops it, the second asks for a name with a default; the saved event is listed with its length, average, peak
              and charge, shaded between two lines and named on the trace, in the place that was clicked; it can be renamed, its end moved by a click, shown (zoomed to), and it is kept with the
              recording; deleting it takes it off the trace and the list;
  safety      no sideways scroll, no failing response, no page or console error.
Exit 0 only if every check passed.
"""
import argparse
import os
import re
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "instruments"))

from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
import verify_ppk2_live as base  # noqa: E402

THEMES = ("light", "dark")
RECORDING = "replay-1"
SAMPLES = 450000                                    # 4.5 s at 100,000 samples a second: nine blocks, a wake in the fourth
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def record(page, bench):
    bench.unit.profile = lambda i: (30000 if (i // 20000) % 10 == 3 else 1000, 0)
    bench.unit.index = bench.unit.counter = 0
    page.get_by_label("Name for the recording").fill(RECORDING)
    page.get_by_role("button", name="Record", exact=True).click()
    page.get_by_text(re.compile(r"^recording, .*to %s\.csv" % RECORDING)).wait_for()
    page.wait_for_function("(n => { const el = document.querySelector('p[aria-label=\"Session\"]'); const m = el && el.innerText.match(/([0-9,]+) samples/); return m && Number(m[1].replace(/,/g, '')) >= n; })", arg=SAMPLES, timeout=30000)
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    listing = page.get_by_role("region", name="Recordings")
    listing.get_by_role("button", name="Replay %s" % RECORDING, exact=True).click()


def stretch(page):
    return page.get_by_label("Stretch shown", exact=True)


def the_whole(page, tag, shot):
    tabs = page.get_by_role("group", name="What the scope shows")
    check("%s the scope is on the replay, and the Live button is there to go back" % tag,
          tabs.get_by_role("button", name="Replay: %s" % RECORDING).get_attribute("aria-pressed") == "true" and tabs.get_by_role("button", name="Live", exact=True).get_attribute("aria-pressed") == "false")
    trace = page.get_by_role("img", name=re.compile(r"^Current of %s, from 0 s to " % RECORDING))
    trace.wait_for()
    bar = stretch(page)
    check("%s a replay shows the whole recording: all of it, from 0 s" % tag, "all of it" in bar.inner_text() and bar.inner_text().startswith("0 s to "), bar.inner_text())
    check("%s with all of it shown there is nowhere to go back to or zoom out to, and zooming in is possible" % tag,
          all(page.get_by_role("button", name=n).is_disabled() for n in ("Show the whole recording", "Back half a screen", "Forward half a screen", "Zoom out")) and page.get_by_role("button", name="Zoom in").is_enabled())
    ylabels = page.get_by_test_id("y-axis").inner_text().split("\n")
    check("%s the vertical axis has labelled values in the unit, from zero" % tag, len(ylabels) >= 2 and ylabels[0].endswith("A") and re.match(r"^[0-9.]+ [nµm]?A$", ylabels[-1]) is not None, ylabels)
    check("%s the time axis is labelled" % tag, len([s for s in page.get_by_test_id("x-axis").inner_text().split("\n") if s.strip()]) >= 3, page.get_by_test_id("x-axis").inner_text())
    box = trace.bounding_box()
    read = page.get_by_label("Under the pointer")
    check("%s before the pointer is over it the readout says how to use the trace" % tag, "Move over the trace" in read.inner_text(), read.inner_text())
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    check("%s over the trace the pointer reads out the time and the lowest, average and highest of the point under it" % tag,
          re.match(r"^[0-9.:]+ (s|ms) · lowest [0-9.]+ [nµm]A · average [0-9.]+ [nµm]A · highest [0-9.]+ [nµm]A · each point [0-9.]+ ms$", read.inner_text()) is not None, read.inner_text())
    check("%s and a cursor line follows it" % tag, page.get_by_test_id("cursor").count() == 1)
    page.mouse.move(box["x"] + box["width"] * 0.1, box["y"] + box["height"] / 2)
    first = read.inner_text()
    page.mouse.move(box["x"] + box["width"] * 0.9, box["y"] + box["height"] / 2)
    check("%s moving along the trace changes the time it reads" % tag, first.split(" · ")[0] != read.inner_text().split(" · ")[0], (first, read.inner_text()))
    page.mouse.move(box["x"] - 40, box["y"] - 40)
    shot("replay")
    return trace


def the_moves(page, tag):
    bar = stretch(page)
    page.get_by_role("button", name="Zoom in").click()
    page.wait_for_function("document.querySelector('[aria-label=\"Stretch shown\"]').innerText.includes(' of ')")
    zoomed = bar.inner_text()
    check("%s zooming in halves the stretch and says how much of the recording it is" % tag, " of " in zoomed and "all of it" not in zoomed, zoomed)
    check("%s once zoomed, Whole, Back, Forward and Zoom out are there" % tag, all(page.get_by_role("button", name=n).is_enabled() for n in ("Show the whole recording", "Back half a screen", "Forward half a screen", "Zoom out")))
    lit = page.get_by_test_id("overview-window").bounding_box()
    strip = page.get_by_test_id("overview").bounding_box()
    check("%s the overview lights the part shown, about half of the whole" % tag, 0.3 < lit["width"] / strip["width"] < 0.7, (lit, strip))
    start = zoomed.split(" to ")[0] + " to "
    page.get_by_role("button", name="Forward half a screen").click()
    page.wait_for_function("(o => !document.querySelector('[aria-label=\"Stretch shown\"]').innerText.startsWith(o))", arg=start)
    moved = bar.inner_text()
    check("%s Forward moves the stretch later and keeps its length" % tag, moved.split(" to ")[0] != zoomed.split(" to ")[0] and moved.split(", ")[1] == zoomed.split(", ")[1], (zoomed, moved))
    check("%s the lit part of the overview moved right with it" % tag, page.get_by_test_id("overview-window").bounding_box()["x"] > lit["x"] + 1, lit)
    page.get_by_role("button", name="Back half a screen").click()
    page.wait_for_function("(o => document.querySelector('[aria-label=\"Stretch shown\"]').innerText.startsWith(o))", arg=start)
    check("%s Back returns it" % tag, bar.inner_text().startswith(start), bar.inner_text())
    page.get_by_label("Scope trace, focus it to use the keys").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_function("(o => !document.querySelector('[aria-label=\"Stretch shown\"]').innerText.startsWith(o))", arg=start)
    page.keyboard.press("0")
    page.wait_for_function("document.querySelector('[aria-label=\"Stretch shown\"]').innerText.includes('all of it')")
    check("%s the keys move it (right arrow) and show all of it again (0)" % tag, "all of it" in bar.inner_text())


def the_events(page, tag, shot, trace):
    bar = stretch(page)
    read = page.get_by_label("Under the pointer")
    page.get_by_role("button", name="Mark an event").click()
    check("%s Mark an event says what the clicks do" % tag, "Click the trace where the event starts" in read.inner_text(), read.inner_text())
    box = trace.bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2)
    check("%s the first click puts a line where the event starts and asks for no name yet" % tag, page.get_by_test_id("pending-start").count() == 1 and page.get_by_label("Event name").count() == 0)
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
    page.get_by_role("button", name="Events (1)").wait_for()              # saving closes the drawer: the count says it is kept, the shading shows it
    check("%s saving the event closes the form and the count says one" % tag, page.get_by_label("Event name").count() == 0 and page.get_by_role("dialog", name="Events").count() == 0)
    page.get_by_role("button", name="Events (1)").click()
    table = page.get_by_role("table", name="Events")
    table.wait_for()
    check("%s the saved event is listed with its length, average, peak and charge" % tag,
          all(w in table.inner_text() for w in ("burst", "Length", "Average current", "Peak", "Charge", "from the readings")) and re.search(r"[0-9.]+ [nµm]A", table.inner_text()) is not None, table.inner_text())
    check("%s it is shaded between two lines, named on the trace" % tag,
          page.get_by_test_id("event-span").count() == 1 and page.get_by_test_id("event-start").count() == 1 and page.get_by_test_id("event-end").count() == 1 and page.get_by_test_id("event-name").inner_text() == "burst")
    span = page.get_by_test_id("event-span").bounding_box()
    check("%s and the shading is where it was clicked, about a quarter of the width" % tag, 0.15 < span["width"] / trace.bounding_box()["width"] < 0.35, span)
    shot("event")
    page.get_by_role("button", name="Rename burst").click()
    page.get_by_label("New name for burst").fill("tx burst")
    page.get_by_role("button", name="Save", exact=True).click()
    table.get_by_text("tx burst").wait_for()
    check("%s an event can be renamed" % tag, page.get_by_test_id("event-name").inner_text() == "tx burst" and table.get_by_text("burst", exact=True).count() == 0)
    page.get_by_role("button", name="Move the end of tx burst").click()
    check("%s Move end asks for a click" % tag, "Click the trace where tx burst should end" in page.inner_text("body"))
    box = trace.bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.80, box["y"] + box["height"] / 2)
    page.wait_for_function("document.querySelector('[data-testid=\"event-span\"]').getBoundingClientRect().width > %s" % (box["width"] * 0.35))
    check("%s the end line moved with the click, and the event is still the one event" % tag, page.get_by_test_id("event-span").count() == 1)
    page.get_by_role("button", name="Show tx burst").click()
    page.wait_for_function("!document.querySelector('[aria-label=\"Stretch shown\"]').innerText.includes('all of it')")
    check("%s Show zooms to the event" % tag, " of " in bar.inner_text() and "all of it" not in bar.inner_text(), bar.inner_text())
    page.get_by_role("button", name="Live", exact=True).click()
    page.get_by_role("region", name="Recordings").get_by_role("button", name="Replay %s" % RECORDING, exact=True).click()
    page.get_by_role("button", name=re.compile(r"^Events \(1\)")).click()
    page.get_by_role("table", name="Events").wait_for()
    check("%s the event is kept with the recording: closing the replay and opening it again shows it" % tag, page.get_by_role("table", name="Events").get_by_text("tx burst").count() == 1)
    page.get_by_role("button", name="Delete the event tx burst").click()
    page.get_by_text(re.compile(r"^No events yet")).wait_for()
    check("%s deleting the event takes it off the trace and the list" % tag, page.get_by_test_id("event-span").count() == 0)


def run(browser, theme, shots):
    tag = "%s 1280x900" % theme
    bench = base.Bench(with_meter=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=theme)
    page = context.new_page()
    page.set_default_timeout(30000)
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("response", lambda r: bad.append((r.status, "/".join(r.url.split("?")[0].rsplit("/", 2)[-2:]))) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    shot = lambda name: page.screenshot(path=os.path.join(shots, "replay-%s-%s.png" % (theme, name)))
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.get_by_role("region", name="Capture").wait_for()
        record(page, bench)
        trace = the_whole(page, tag, shot)
        the_moves(page, tag)
        the_events(page, tag, shot, trace)
        check("%s no sideways scroll with the replay" % tag, base.no_sideways_scroll(page))
        check("%s no failing response" % tag, bad == [], bad)
        check("%s no page or console errors" % tag, not errors, errors)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
        page.screenshot(path=os.path.join(shots, "replay-%s-failed.png" % theme))
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-meter-replay-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        try:
            for theme in THEMES:
                run(browser, theme, shots)
        finally:
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
