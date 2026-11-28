#!/usr/bin/env python3
"""The wakes a Watch kept can be looked at on the Meter page (docs/specs/meter-page.md, "Bench layout"; docs/specs/ppk2-stages.md).

    python3 scripts/verify_watch_wakes.py [--shots DIR] [--chrome PATH]

A Watch writes nothing, but the unit keeps every wake it sees at the full 100,000 samples a second for the session (meter.captures with no recording), and looking at one before deciding to record is
what a Watch is for. A real service over a fake PPK2 unit paced at the real rate, in light and dark at 1280x900:
  kept     a Watch of 8 s of a unit that wakes for 0.6 s every 2 s offers the Stages drawer when it has ended, which lists the wakes with their length and peak, draws one, says stages are kept with a
           recording and offers no way to name a stage, and still lists them while a second Watch is running;
  none     a Watch of a steady load kept no wake, so it offers nothing to look at;
  safety   no failing response, no page or console error.
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
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def watch(page, bench, seconds, profile):
    bench.unit.profile = profile
    bench.unit.index = bench.unit.counter = 0
    page.get_by_label("Length", exact=True).fill(seconds)
    page.get_by_label("Unit", exact=True).select_option("s")
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, ")).wait_for()


def ended(page):
    page.get_by_text(re.compile(r"^ended: ")).wait_for(timeout=30000)


def the_kept(page, bench, tag, shot):
    # A wake of 0.6 s starting at 0.6 s, 2.6 s, 4.6 s and 6.6 s. The first 4 s are spent finding the sleep level (MIN_SLEEP_BLOCKS, 20 blocks of 0.2 s), so the wakes kept are the ones at 4.6 s
    # (closed by 5.4 s) and 6.6 s (closed by 7.4 s): 8 s holds both with 0.6 s over.
    watch(page, bench, "8", lambda i: (30000 if (i // 20000) % 10 == 3 else 1000, 0))
    ended(page)
    stages = page.get_by_role("button", name="Stages", exact=True)
    check("%s a Watch that kept wakes offers the Stages drawer when it has ended" % tag, stages.count() == 1, stages.count())
    stages.click()
    drawer = page.get_by_role("dialog", name=re.compile(r"^Stages across the wakes"))
    drawer.wait_for()
    pick = drawer.get_by_label("Wake", exact=True)
    pick.locator("option").nth(1).wait_for(state="attached")
    options = [o.inner_text() for o in pick.locator("option").all()]
    check("%s it lists the wakes with their length and peak" % tag, len(options) >= 2 and all(re.match(r"^wake \d+, 600 ms, peak [0-9.]+ [nµm]A$", o) for o in options), options)
    drawer.get_by_role("img", name=re.compile(r"^Current of wake [0-9]+, from -200 ms to 400 ms$")).wait_for()
    check("%s and draws one of them" % tag, drawer.get_by_test_id("wake-start").count() == 1)
    check("%s it says stages are kept with a recording, and offers no way to name one" % tag,
          "Stages are kept with a recording" in drawer.inner_text() and drawer.get_by_role("button", name="Add as a stage").count() == 0 and drawer.get_by_label("Stage name").count() == 0, drawer.inner_text()[-300:])
    shot("watch-wakes")
    page.keyboard.press("Escape")
    check("%s Escape closes it" % tag, page.get_by_role("dialog", name=re.compile(r"^Stages across the wakes")).count() == 0)


def the_none(page, bench, tag):
    watch(page, bench, "3", lambda i: (1000 + (i % 7), 0))
    ended(page)
    check("%s a Watch of a steady load kept no wake, so there is nothing to look at" % tag, page.get_by_role("button", name="Stages", exact=True).count() == 0)


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
    shot = lambda name: page.screenshot(path=os.path.join(shots, "%s-%s.png" % (name, theme)))
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.get_by_role("region", name="Capture").wait_for()
        the_kept(page, bench, tag, shot)
        the_none(page, bench, tag)
        check("%s no failing response" % tag, bad == [], bad)
        check("%s no page or console errors" % tag, not errors, errors)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
        page.screenshot(path=os.path.join(shots, "watch-wakes-%s-failed.png" % theme))
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-watch-wakes-")
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
