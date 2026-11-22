#!/usr/bin/env python3
"""The Meter page's battery life drawer and wake/sleep cycle table, in a real browser (docs/specs/meter-page.md, "The cycle table" and "Bench layout").

    python3 scripts/verify_meter_life.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs with a PPK2 and a FAKE UNIT paced at the real 100,000 samples a second that sleeps and wakes for one 0.2 s block every 2 s. A watch of
8.5 s (a length typed into the Capture card, so it ends by itself and the number of wakes it saw cannot depend on when a button was pressed) is looked at in the Battery life drawer, in light and
dark at 1280x900:
  life        the drawer opens from the Battery life readout; the choice starts on the whole recording; a capacity gives "it lasts"; the readout's own figure follows the capacity;
  cycle       choosing Wake/sleep cycle presses that button and not the other and gives the period, the cycles counted, the average over them and the life; the chart marks and shades each wake it found;
  table       Sleep and wake has Wake, Sleep and Cycle rows; a wake of one block says its length is under two points and gives its exact peak; the sleep row says over where the wake row says under;
              it says why there is no energy;
  back        returning to the whole recording drops the marks, the shading and the table;
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
WATCH_S = "8.5"                                     # 8.5 s: blocks 0 to 42, a wake in blocks 3, 13, 23 and 33 (at 0.6, 2.6, 4.6 and 6.6 s), so four wakes and three whole cycles
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def hours(text):
    """How long a sentence says it lasts, in hours: 'it lasts 41 days' is 984."""
    m = re.search(r"it lasts ([0-9.,]+) (hours|days|years)", text)
    return float(m.group(1).replace(",", "")) * {"hours": 1, "days": 24, "years": 8766}[m.group(2)] if m else 0.0


def amps(text):
    """The current a sentence gives after 'Average over them', in amperes: '112.8 µA' is 1.128e-4."""
    m = re.search(r"Average over them ([0-9.]+) ([nµm]?)A", text)
    return float(m.group(1)) * {"n": 1e-9, "µ": 1e-6, "m": 1e-3, "": 1.0}[m.group(2)] if m else 0.0


def watch(page, bench):
    bench.unit.profile = lambda i: (30000 if (i // 20000) % 10 == 3 else 1000, 0)
    bench.unit.index = bench.unit.counter = 0
    page.get_by_label("Length", exact=True).fill(WATCH_S)
    page.get_by_label("Unit", exact=True).select_option("s")
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, ")).wait_for()
    page.get_by_text(re.compile(r"^ended: ")).wait_for(timeout=30000)


def the_life(page, tag, shot):
    readout = page.get_by_role("button", name=re.compile(r"^Battery life: "))
    check("%s without a capacity the Battery life readout has no figure and opens the drawer" % tag, readout.inner_text().replace("\n", " ").endswith("—") or "—" in readout.inner_text(), readout.inner_text())
    readout.click()
    drawer = page.get_by_role("dialog", name="Battery life")
    drawer.wait_for()
    check("%s the drawer opens from the readout" % tag, True)
    drawer.get_by_label("Battery capacity in milliamp hours").fill("1000")
    life = drawer.get_by_label("Battery life", exact=True)
    whole = drawer.get_by_role("button", name="Whole recording", exact=True)
    cycle = drawer.get_by_role("button", name="Wake/sleep cycle", exact=True)
    check("%s the choice starts on the whole recording and a capacity gives how long it lasts" % tag,
          whole.get_attribute("aria-pressed") == "true" and "whole recording it lasts" in life.inner_text(), life.inner_text())
    said = life.inner_text()
    before = hours(said)
    drawer.get_by_label("Battery capacity in milliamp hours").fill("2000")
    page.wait_for_function("(b => { const m = document.querySelector('[role=dialog] p[aria-label=\"Battery life\"]').innerText.match(/it lasts ([0-9.,]+) (hours|days|years)/); return m && m[0] !== b; })", arg=re.search(r"it lasts [0-9.,]+ (hours|days|years)", said).group(0))
    after = hours(life.inner_text())
    check("%s twice the capacity lasts twice as long (%.0f h, then %.0f h)" % (tag, before, after), before > 0 and 1.8 < after / before < 2.2, (before, after))
    drawer.get_by_label("Battery capacity in milliamp hours").fill("1000")
    check("%s the readout carries the figure too, once there is a capacity" % tag, page.get_by_role("button", name=re.compile(r"^Battery life: .*[0-9]")).count() == 1)
    cycle.click()
    check("%s choosing the cycle presses that button and not the other" % tag, cycle.get_attribute("aria-pressed") == "true" and whole.get_attribute("aria-pressed") == "false")
    life.filter(has_text=re.compile(r"^Wakes every 2 s \(3 cycles counted")).wait_for()
    check("%s the cycle line gives the period, the cycles, the average over them and the life" % tag,
          re.match(r"^Wakes every 2 s \(3 cycles counted\)\. Average over them [0-9.]+ [nµm]A\. It lasts [0-9.]+ (hours|days|years)\.$", life.inner_text()) is not None, life.inner_text())
    said = life.inner_text()
    mean_ma = amps(said) * 1000
    lasts = hours(said.replace("It lasts", "it lasts"))
    check("%s and the life is the capacity over that stated average (1000 mAh / %.4g mA = %.0f h; the page says %.0f h)" % (tag, mean_ma, 1000 / mean_ma if mean_ma else 0, lasts),
          mean_ma > 0 and abs(lasts - 1000 / mean_ma) / (1000 / mean_ma) < 0.015, said)               # the text rounds to 3 figures (0.2 %); the whole recording's average is about 4 % off the cycles'
    check("%s the chart marks each wake it found" % tag, page.get_by_test_id("wake-mark").count() == 4, page.get_by_test_id("wake-mark").count())
    check("%s and shades the four wakes" % tag, page.get_by_test_id("wake-span").count() == 4, page.get_by_test_id("wake-span").count())
    return drawer, life, whole


def the_table(page, tag, drawer):
    table = drawer.get_by_role("table", name="Sleep and wake")
    table.wait_for()
    rows = [table.get_by_role("row").nth(i).inner_text().replace("\n", " ") for i in range(4)]
    check("%s the table has Wake, Sleep and Cycle rows under its headings" % tag, "Time per cycle" in rows[0] and rows[1].startswith("Wake") and rows[2].startswith("Sleep") and rows[3].startswith("Cycle"), rows)
    check("%s a wake of one 0.2 s block says its length is under two points, and gives its exact peak" % tag,
          "under 0.4 s" in rows[1] and "at least" in rows[1] and re.search(r"peak [0-9.]+ [nµm]A", rows[1]) is not None, rows[1])
    check("%s the sleep row says over, where the wake row says under, so the shares never exceed the whole" % tag, "over 1.6 s" in rows[2] and "over 80 %" in rows[2], rows[2])
    check("%s the cycle row says three cycles counted and the period" % tag, "3 counted" in rows[3] and "2 s" in rows[3], rows[3])
    text = drawer.inner_text()
    check("%s the table says why there is no energy, and the note about one-point wakes is there" % tag,
          "No energy: the supply voltage is not known here" in text and "Each wake fills a single point of the chart" in text, text[-300:])


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
    shot = lambda name: page.screenshot(path=os.path.join(shots, "life-%s-%s.png" % (theme, name)))
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.get_by_role("region", name="Capture").wait_for()
        watch(page, bench)
        drawer, life, whole = the_life(page, tag, shot)
        the_table(page, tag, drawer)
        check("%s no sideways scroll with the cycle line and the table" % tag, base.no_sideways_scroll(page))
        shot("cycle")
        whole.click()
        life.filter(has_text="whole recording it lasts").wait_for()
        check("%s going back to the whole recording drops the marks, the shading and the table, and says so" % tag,
              page.get_by_test_id("wake-mark").count() == 0 and page.get_by_test_id("wake-span").count() == 0 and drawer.get_by_role("table", name="Sleep and wake").count() == 0 and "whole recording it lasts" in life.inner_text(), life.inner_text())
        page.keyboard.press("Escape")
        check("%s Escape closes the drawer" % tag, page.get_by_role("dialog", name="Battery life").count() == 0)
        check("%s no failing response" % tag, bad == [], bad)
        check("%s no page or console errors" % tag, not errors, errors)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
        page.screenshot(path=os.path.join(shots, "life-%s-failed.png" % theme))
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-meter-life-")
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
