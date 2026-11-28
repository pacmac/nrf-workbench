#!/usr/bin/env python3
"""The Meter page's wake scope and stages, in a real browser (docs/specs/ppk2-stages.md; docs/specs/meter-page.md, "Bench layout").

    python3 scripts/verify_meter_stages.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs with a PPK2 and a FAKE UNIT paced at the real 100,000 samples a second that wakes for 0.6 s in every 2 s. A recording is made through
the page and its wakes are looked at in the Stages drawer, in light and dark at 1280x900:
  wakes       the wakes the unit kept are listed with their length and peak; a wake is drawn from 200 ms before it started with its start marked once, and its figures say how long, the average, the
              peak and the charge; the axis starts linear at zero and the log switch changes it and says where its floor is, leaving the live scope alone; dragging shows the stretch being chosen
              and zooms to about 15 percent of the wake, and Show all of it returns;
  stages      a stage is a named stretch of every wake: it cannot be added without a name, it appears in a table with its stretch, average, peak, charge and share, is shaded on the trace and named in
              the colour legend, and is removed again; a stage for the odd wakes beside one for all wakes is grouped, shown on an odd wake only, and the legend follows the wake on show;
  recordings  a finished recording's wakes open again from the Recordings list;
  long wake   (light only, it takes about a minute at the real rate) a burst, a 20 s receive window at a tenth of the current and a burst is ONE wake of 21.4 s, drawn whole, and its receive window can
              be zoomed into, where the peak is the window's and not the burst's;
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
DRAWER = re.compile(r"^Stages across the wakes")
LONG = True
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def record(page, bench, name, profile):
    bench.unit.profile = profile
    bench.unit.index = bench.unit.counter = 0
    page.get_by_label("Name for the recording").fill(name)
    page.get_by_role("button", name="Record", exact=True).click()
    page.get_by_text(re.compile(r"^recording, .*to %s\.csv" % name)).wait_for()


def open_stages(page):
    page.get_by_role("button", name="Stages", exact=True).click()
    drawer = page.get_by_role("dialog", name=DRAWER)
    drawer.wait_for()
    return drawer


def drag(page, drawer, a, b):
    trace = drawer.get_by_role("img", name=re.compile(r"^Current of wake"))
    trace.scroll_into_view_if_needed()
    box = trace.bounding_box()
    page.mouse.move(box["x"] + box["width"] * a, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * b, box["y"] + box["height"] / 2)
    page.mouse.up()


def the_wakes(page, tag, drawer, shot):
    pick = drawer.get_by_label("Wake", exact=True)
    pick.locator("option").nth(2).wait_for(state="attached", timeout=40000)
    options = [o.inner_text() for o in pick.locator("option").all()]
    check("%s the wakes the unit kept are listed, each with its length and peak" % tag, len(options) >= 3 and all(re.match(r"^wake \d+, 600 ms, peak [0-9.]+ [nµm]A$", o) for o in options), options)
    drawer.get_by_role("img", name=re.compile(r"^Current of wake [0-9]+, from -200 ms to 400 ms$")).wait_for()
    check("%s the wake is drawn from 200 ms before it started, with its start marked once" % tag, drawer.get_by_test_id("wake-start").count() == 1)
    figures = drawer.get_by_label("Wake figures")
    check("%s the wake's figures say how long, the average, the peak and the charge" % tag, all(w in figures.inner_text() for w in ("Shown", "Average", "Peak", "Charge")) and "600 ms" in figures.inner_text(), figures.inner_text())
    linear = drawer.get_by_text(re.compile(r"^bottom of the chart: 0 A$"))
    live_scale = page.get_by_text(re.compile(r"^The scale starts at 0 A\."))
    check("%s the wake's axis starts linear, with zero at the bottom (the live scope's says the same)" % tag, linear.count() == 1 and live_scale.count() == 1 and page.get_by_text(re.compile(r"^log scale")).count() == 0)
    drawer.get_by_label("Log current axis").check()
    drawer.get_by_text(re.compile(r"^log scale, bottom [0-9.]+ [nµ]A$")).wait_for()
    check("%s the log switch changes the wake's axis and says where its floor is; the live scope is untouched" % tag, linear.count() == 0 and live_scale.count() == 1)
    drawer.get_by_label("Log current axis").uncheck()
    trace = drawer.get_by_role("img", name=re.compile(r"^Current of wake"))
    box = trace.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.30, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.37, box["y"] + box["height"] / 2)
    check("%s dragging shows the stretch being chosen" % tag, drawer.get_by_test_id("zoom-selection").count() == 1)
    page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] / 2)
    page.mouse.up()
    drawer.get_by_role("button", name="Show all of it").wait_for()
    page.wait_for_function("(() => { const t = document.querySelector('[aria-label=\"Wake figures\"]').innerText; return !t.includes('600 ms') && /Shown\\s+[0-9.]+ ms/.test(t); })()")
    shown = re.search(r"Shown\s+([0-9.]+) ms", figures.inner_text()).group(1)
    check("%s dragging across the chart zooms to about 15 percent of the wake (90 ms)" % tag, 70 <= float(shown) <= 110, shown)
    drawer.get_by_role("button", name="Show all of it").click()
    page.wait_for_function("document.querySelector('[aria-label=\"Wake figures\"]').innerText.includes('600 ms')")
    check("%s Show all of it returns to the whole wake" % tag, drawer.get_by_role("button", name="Show all of it").count() == 0)
    check("%s no sideways scroll with the wake scope" % tag, base.no_sideways_scroll(page))
    shot("wake")


def the_stages(page, tag, drawer, shot):
    check("%s with a recording behind the wakes the drawer says how to name a stage, and there are none yet" % tag,
          "Drag across the chart to select a stretch, then name it" in drawer.inner_text() and drawer.get_by_role("table", name="Stages across the wakes").count() == 0)
    drag(page, drawer, 0.30, 0.45)
    drawer.get_by_text(re.compile(r"^Selected: ")).wait_for()
    sel = drawer.get_by_label("Selected stretch").inner_text()
    check("%s the selected stretch is shown in milliseconds from the wake's start" % tag, re.match(r"^Selected: -?[0-9.]+ ms to -?[0-9.]+ ms$", sel) is not None, sel)
    check("%s a stage cannot be added without a name" % tag, drawer.get_by_role("button", name="Add as a stage").is_disabled())
    drawer.get_by_label("Stage name").fill("burst")
    drawer.get_by_role("button", name="Add as a stage").click()
    table = drawer.get_by_role("table", name="Stages across the wakes")
    table.wait_for()
    text = table.inner_text().replace("\n", " ")
    check("%s the stage appears in a table with its stretch, average, peak, charge and share" % tag, "burst" in text and re.search(r"[0-9.]+ ms to [0-9.]+ ms", text) is not None and "of the named stages" in text and "100 %" in text, text)
    check("%s the stage is shaded on the trace and named in the colour legend" % tag, drawer.get_by_test_id("stage-span").count() == 1 and drawer.get_by_label("Stage colours").inner_text().strip() == "burst")
    check("%s no sideways scroll with the stage table" % tag, base.no_sideways_scroll(page))
    shot("stages")
    drawer.get_by_role("button", name="Show all of it").click()
    drawer.get_by_role("button", name="Remove the stage burst").click()
    table.wait_for(state="detached")
    check("%s removing the stage takes it off the table and the trace" % tag, drawer.get_by_test_id("stage-span").count() == 0)

    # a stage for only the odd wakes, beside one for all of them (docs/specs/ppk2-stages.md, "Wakes of different kinds")
    drag(page, drawer, 0.30, 0.45)
    drawer.get_by_text(re.compile(r"^Selected: ")).wait_for()
    drawer.get_by_label("Stage name").fill("burst")
    drawer.get_by_role("button", name="Add as a stage").click()
    table.wait_for()
    drawer.get_by_role("button", name="Show all of it").click()
    page.wait_for_function("document.querySelector('[aria-label=\"Wake figures\"]').innerText.includes('600 ms')")          # the whole wake is back before a stretch is chosen on it
    drag(page, drawer, 0.60, 0.70)
    drawer.get_by_text(re.compile(r"^Selected: ")).wait_for()
    drawer.get_by_label("Stage name").fill("late")
    drawer.get_by_label("Applies to").select_option("odd")
    drawer.get_by_role("button", name="Add as a stage").click()
    table.get_by_text(re.compile(r"^Odd wakes")).wait_for()
    grouped = table.inner_text().replace("\n", " ")
    check("%s the table groups the stages by the wakes they are for, each group naming its wakes" % tag, re.search(r"All wakes\s*\(1, 2, 3", grouped) is not None and re.search(r"Odd wakes\s*\(1, 3", grouped) is not None, grouped)
    drawer.get_by_label("Wake", exact=True).select_option("1")
    page.wait_for_function("document.querySelectorAll('[data-testid=\"stage-span\"]').length === 2", timeout=10000)
    check("%s an odd wake shows both stages on its trace" % tag, drawer.get_by_test_id("stage-span").count() == 2, drawer.get_by_test_id("stage-span").count())
    drawer.get_by_label("Wake", exact=True).select_option("2")
    page.wait_for_function("document.querySelectorAll('[data-testid=\"stage-span\"]').length === 1")
    check("%s an even wake shows only the stage that is for all wakes" % tag, drawer.get_by_test_id("stage-span").count() == 1)
    check("%s the colour legend follows the wake on show" % tag, drawer.get_by_label("Stage colours").inner_text().strip() == "burst")
    check("%s no sideways scroll with the grouped table" % tag, base.no_sideways_scroll(page))
    shot("kinds")
    drawer.get_by_role("button", name="Remove the stage late").click()
    drawer.get_by_role("button", name="Remove the stage burst").click()
    table.wait_for(state="detached")


def the_recording(page, tag):
    page.keyboard.press("Escape")                                              # the drawer covers the Recordings list
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    page.get_by_role("region", name="Recordings").get_by_role("button", name="Replay stage-1", exact=True).click()
    drawer = open_stages(page)
    drawer.get_by_label("Wake", exact=True).locator("option").nth(2).wait_for(state="attached")
    check("%s a finished recording's wakes open from the Recordings list" % tag, drawer.get_by_label("Wake", exact=True).locator("option").count() >= 3)
    page.keyboard.press("Escape")
    page.get_by_role("group", name="What the scope shows").get_by_role("button", name="Live", exact=True).click()


def the_long_wake(page, bench, tag, shot):
    """A wake like the bench RAK's: a burst, a 20 s receive window at a tenth of the current, a burst. 125 quiet blocks first so the sleep level (the median block) is asleep through the wake."""
    record(page, bench, "long-1", lambda i: (30000 if (125 <= i // 20000 < 128 or 228 <= i // 20000 < 230) else 3000 if 128 <= i // 20000 < 228 else 1000, 0))
    drawer = open_stages(page)
    pick = drawer.get_by_label("Wake", exact=True)
    try:
        pick.locator("option").filter(has_text=re.compile(r"^wake \d+, 21\.4 s, peak")).wait_for(state="attached", timeout=90000)
    except Exception:                                                              # say what the list held, then go on to the end
        pass
    options = [o.inner_text() for o in pick.locator("option").all()] if pick.count() else []
    if not check("%s the awake period is one wake of 21.4 s, not a burst and another 21 s later" % tag, len(options) == 1 and re.match(r"^wake 1, 21\.4 s, peak [0-9.]+ [nµm]A$", options[0]) is not None, options):
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Stop").click()
        return
    drawer.get_by_role("img", name=re.compile(r"^Current of wake 1, from -0\.2 s to 21\.2 s$")).wait_for()
    figures = drawer.get_by_label("Wake figures")
    whole = figures.inner_text()
    check("%s the figures say 21.4 s is shown" % tag, "21.4 s" in whole, whole)
    shot("long-wake")
    drag(page, drawer, 0.45, 0.50)
    drawer.get_by_role("button", name="Show all of it").wait_for()
    page.wait_for_function("(() => { const t = document.querySelector('[aria-label=\"Wake figures\"]').innerText; return !t.includes('21.4 s') && /Shown\\s+[0-9.]+ s/.test(t); })()")
    zoomed = figures.inner_text()
    shown = float(re.search(r"Shown\s+([0-9.]+) s", zoomed).group(1))
    peak_whole = re.search(r"Peak\s+([0-9.]+ [nµm]A)", whole).group(1)
    peak_zoom = re.search(r"Peak\s+([0-9.]+ [nµm]A)", zoomed).group(1)
    check("%s dragging across the receive window zooms to about a second of it, whose peak is the window's and not the burst's" % tag, 0.9 <= shown <= 1.4 and peak_zoom != peak_whole, (shown, peak_whole, peak_zoom))
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()


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
    shot = lambda name: page.screenshot(path=os.path.join(shots, "stages-%s-%s.png" % (theme, name)))
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.get_by_role("region", name="Capture").wait_for()
        record(page, bench, "stage-1", lambda i: (30000 if (i // 20000) % 10 == 3 else 1000, 0))
        drawer = open_stages(page)
        the_wakes(page, tag, drawer, shot)
        the_stages(page, tag, drawer, shot)
        the_recording(page, tag)
        if theme == "light" and LONG:
            the_long_wake(page, bench, tag, shot)
        check("%s no failing response" % tag, bad == [], bad)
        check("%s no page or console errors" % tag, not errors, errors)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
        page.screenshot(path=os.path.join(shots, "stages-%s-failed.png" % theme))
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    ap.add_argument("--no-long", action="store_true", help="leave out the 21 s wake (about a minute at the real rate); the default runs it")
    args = ap.parse_args(argv)
    global LONG
    LONG = not args.no_long
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-meter-stages-")
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
