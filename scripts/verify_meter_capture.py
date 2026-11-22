#!/usr/bin/env python3
"""The Meter page's watch, record and recordings, in a real browser (docs/specs/meter-page.md, "Bench layout" and "Sessions: watch and record").

    python3 scripts/verify_meter_capture.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs with a PPK2 and a FAKE UNIT that speaks the protocol, paced at the real 100,000 samples a second. In light and dark at 1280x900:
  watch       Watch shows the profiler's block mean with an SI prefix and a session line that counts samples; the chart is drawn with a labelled axis from zero; while it runs the Supply strip is
              held; Stop ends it and the readouts say lowest, average, highest and the charge used; Watch keeps nothing, so the recordings list stays empty;
  lost        a run with a gap in the unit's counter counts the lost samples and says so, and a clean run says nothing;
  record      Record writes a named recording that is listed (name, size, average) and the session line names the file; the list says which one is recording now;
  csv         Save CSV downloads the recording with min, max and n after the old columns, and n is the 20,000 samples of a block;
  delete      Delete asks first and says what goes with the recording; Keep it changes nothing; Yes, delete takes that one off and leaves the others; deleting the recording on the scope
              puts the scope back on the live session;
  safety      no sideways scroll, no failing response, no page or console error, and the shell port is never opened.
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


def session_line(page):
    return page.get_by_label("Session", exact=True)


def samples_arrive(page):
    page.wait_for_function("(() => { const el = document.querySelector('p[aria-label=\"Session\"]'); return el && /[1-9][0-9,]* samples/.test(el.innerText); })()")


def watching(page):
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, ")).wait_for()


def stopped(page):
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()


def the_watch(page, bench, tag, shot):
    listing = page.get_by_role("region", name="Recordings")
    listing.get_by_text("Nothing recorded yet", exact=False).wait_for()          # the list is read first ("Reading the recordings…")
    check("%s before anything has run the recordings list says there is nothing" % tag, True)
    watching(page)
    page.get_by_label("Latest reading").filter(has_text=re.compile(r"^-?[0-9.]+ [nµm]?A$")).wait_for()
    check("%s Watch shows the profiler's block mean with an SI prefix" % tag, True)
    samples_arrive(page)
    check("%s the session line counts samples" % tag, re.search(r"[1-9][0-9,]* samples", session_line(page).inner_text()) is not None, session_line(page).inner_text())
    page.get_by_role("img", name=re.compile(r"^Readings from")).wait_for()
    check("%s the chart is drawn, with a labelled axis from zero and times along the bottom" % tag,
          page.get_by_test_id("y-axis").inner_text().startswith("0 A") and page.get_by_test_id("x-axis").inner_text().strip() != "")
    supply = page.get_by_role("region", name="Supply")
    check("%s while it runs the Supply strip is held" % tag, "Held by the running capture" in supply.inner_text(), supply.inner_text()[:120])
    check("%s and there is a Stop and no Watch or Record" % tag, page.get_by_role("button", name="Stop").count() == 1 and page.get_by_role("button", name="Watch", exact=True).count() == 0 and page.get_by_role("button", name="Record", exact=True).count() == 0)
    shot("watching")
    stopped(page)
    readings = page.get_by_role("group", name="Readings")
    text = readings.inner_text()
    check("%s after Stop the readouts say lowest, average, highest and the charge used" % tag, all(w in text for w in ("Lowest", "Average", "Highest", "Charge used")), text)
    cells = [line for line in text.split("\n") if line.strip()]
    figures = dict(zip(cells[0::2], cells[1::2]))                                # label, value pairs; Battery life is "—" until a capacity is typed
    check("%s and each of those four has a figure" % tag, all(figures.get(k, "—") != "—" for k in ("Lowest", "Average", "Highest", "Charge used")), figures)
    check("%s the Supply strip is free again" % tag, "Held by the running capture" not in supply.inner_text() and not supply.get_by_role("button", name="Ampere mode").is_disabled())
    check("%s a clean run says nothing about lost samples" % tag, "were lost between" not in page.inner_text("body"))
    check("%s Watch kept nothing: the recordings list still says there is nothing" % tag, "Nothing recorded yet" in listing.inner_text(), listing.inner_text()[:100])


def the_loss(page, bench, tag, shot):
    bench.unit.lost_at = {6000: 5}
    bench.unit.index = bench.unit.counter = 0
    watching(page)
    page.get_by_text(re.compile(r"5 samples were lost between the unit and the Pi")).wait_for()
    check("%s lost samples are counted and the page says so" % tag, True)
    shot("lost")
    stopped(page)
    bench.unit.lost_at = {}


def the_recordings(page, bench, tag, shot):
    listing = page.get_by_role("region", name="Recordings")
    for name in ("profile-1", "second-1"):
        page.get_by_label("Name for the recording").fill(name)
        page.get_by_role("button", name="Record", exact=True).click()
        page.get_by_text(re.compile(r"^recording, .*to %s\.csv" % name)).wait_for()
        if name == "profile-1":
            listing.get_by_text("recording now").wait_for()
            check("%s the session line names the file and the list says which one is recording now" % tag, "profile-1.csv" in listing.inner_text(), listing.inner_text()[:200])
            check("%s a recording cannot be deleted while it is being recorded" % tag, listing.get_by_role("button", name="Delete profile-1", exact=True).is_disabled())
        samples_arrive(page)
        stopped(page)
        listing.get_by_text("%s.csv" % name).wait_for()
    text = listing.inner_text()
    check("%s both finished recordings are listed with their name, size and average" % tag,
          "profile-1.csv" in text and "second-1.csv" in text and len(re.findall(r"\d+ KB", text)) >= 2 and "average" in text and "recording now" not in text, text)
    check("%s each has Replay, Save CSV and Delete" % tag, all(listing.get_by_role("button", name=n, exact=True).count() == 1 for n in ("Replay profile-1", "Save profile-1 as a CSV file", "Delete profile-1", "Replay second-1")))
    shot("recorded")


def the_csv(page, bench, tag):
    listing = page.get_by_role("region", name="Recordings")
    with page.expect_download() as dl:
        listing.get_by_role("button", name="Save profile-1 as a CSV file").click()
    with open(dl.value.path()) as fh:
        lines = fh.read().splitlines()
    cells = lines[1].split(",")
    check("%s Save CSV gives a file named profile-1.csv" % tag, dl.value.suggested_filename == "profile-1.csv", dl.value.suggested_filename)
    check("%s with min, max and n after the old columns, the average between them, and the 20,000 samples of a block" % tag,
          lines[0] == "t_s,wall,value,unit,function,range,nplc,overload,min,max,n" and cells[3] == "A" and cells[10] == "20000" and float(cells[8]) <= float(cells[2]) <= float(cells[9]), lines[:2])


def the_delete(page, bench, tag, shot):
    listing = page.get_by_role("region", name="Recordings")
    listing.get_by_role("button", name="Delete profile-1", exact=True).click()
    check("%s Delete asks first, and says what goes with the recording" % tag, "Delete it, with its saved wakes?" in listing.inner_text() and listing.get_by_role("button", name="Yes, delete profile-1").count() == 1, listing.inner_text())
    listing.get_by_role("button", name="Keep it").click()
    check("%s Keep it changes nothing" % tag, "Delete it, with its saved wakes?" not in listing.inner_text() and "profile-1.csv" in listing.inner_text() and "second-1.csv" in listing.inner_text())
    listing.get_by_role("button", name="Delete profile-1", exact=True).click()
    listing.get_by_role("button", name="Yes, delete profile-1").click()
    page.wait_for_function("!document.querySelector('section[aria-label=\"Recordings\"]').innerText.includes('profile-1.csv')")
    check("%s Yes, delete takes that one off the list and leaves the other" % tag, "profile-1.csv" not in listing.inner_text() and "second-1.csv" in listing.inner_text(), listing.inner_text())
    check("%s and the file is gone from the Pi" % tag, not os.path.exists(os.path.join(bench.tmp, "meter", "profile-1.csv")) and os.path.exists(os.path.join(bench.tmp, "meter", "second-1.csv")), os.listdir(os.path.join(bench.tmp, "meter")))

    listing.get_by_role("button", name="Replay second-1", exact=True).click()
    scope = page.get_by_role("group", name="What the scope shows")
    scope.get_by_role("button", name="Replay: second-1").wait_for()
    check("%s Replay puts the recording on the scope" % tag, scope.get_by_role("button", name="Replay: second-1").get_attribute("aria-pressed") == "true" and scope.get_by_role("button", name="Live", exact=True).get_attribute("aria-pressed") == "false")
    shot("replay")
    listing.get_by_role("button", name="Delete second-1", exact=True).click()
    listing.get_by_role("button", name="Yes, delete second-1").click()
    page.wait_for_function("!document.querySelector('section[aria-label=\"Recordings\"]').innerText.includes('second-1.csv')")
    check("%s deleting the recording that is on the scope puts the scope back on the live session" % tag,
          scope.get_by_role("button", name=re.compile(r"^Replay: ")).count() == 0 and scope.get_by_role("button", name="Live", exact=True).get_attribute("aria-pressed") == "true")
    check("%s and the list is empty again" % tag, "Nothing recorded yet" in listing.inner_text(), listing.inner_text()[:100])


def run(browser, theme, shots):
    tag = "%s 1280x900" % theme
    bench = base.Bench(with_meter=False)
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=theme, accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(30000)
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("response", lambda r: bad.append((r.status, "/".join(r.url.split("?")[0].rsplit("/", 2)[-2:]))) if r.status >= 400 else None)
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    shot = lambda name: page.screenshot(path=os.path.join(shots, "capture-%s-%s.png" % (theme, name)))
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.get_by_role("region", name="Capture").wait_for()
        the_watch(page, bench, tag, shot)
        the_loss(page, bench, tag, shot)
        the_recordings(page, bench, tag, shot)
        check("%s no sideways scroll" % tag, base.no_sideways_scroll(page))
        the_csv(page, bench, tag)
        the_delete(page, bench, tag, shot)
        check("%s no failing response" % tag, bad == [], bad)
        check("%s no page or console errors" % tag, not errors, errors)
        check("%s the shell port was never opened" % tag, all(n.endswith("ttyACM1") for n in bench.opened), bench.opened)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
        page.screenshot(path=os.path.join(shots, "capture-%s-failed.png" % theme))
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-meter-capture-")
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
