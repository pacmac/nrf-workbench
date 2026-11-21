#!/usr/bin/env python3
"""The Meter page is a bench instrument panel that fills the window and never scrolls (docs/specs/meter-page.md, "Bench layout"; docs/specs/navigation.md).

    python3 scripts/verify_meter_layout.py [--only shell|capture|running] [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench of verify_ppk2_live.py (no multimeter). In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing inside the page scrolls; the page uses the width beside the nav; the supply controls are on screen;
  capture  a length can be typed with its unit; Stop after offers Time and Never; there is no multimeter and no fixed list of durations.
  wakes    Stop after Wakes: the running session says how many whole wakes it has seen of how many it wants, and a stop by hand is named as one.
  running  with a watch under way: a trace is drawn, the supply says it is held, the latest reading is shown, and still nothing scrolls.
Exit 0 only if every selected check passed.
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
SIZES = ((1280, 720), (1440, 900))
NAV_W = 256                      # the chrome beside the page: the sidebar 208 (w-52), 16 left padding, 16 gap and 16 right padding. The page may not be narrower than the window minus this
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


SCROLLERS = """() => {
  const main = document.querySelector('main');
  const out = [];
  for (const el of main.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1 && el.tagName !== 'TEXTAREA') out.push(el.tagName + '.' + String(el.className).slice(0, 60));
  }
  return out;
}"""


CLIPPED = """() => {
  const main = document.querySelector('main').getBoundingClientRect();
  const out = [];
  for (const el of document.querySelectorAll('main button, main input, main select')) {
    if (!el.offsetParent) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom > main.bottom + 1 || r.right > main.right + 1) out.push((el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 30) + ' ends at ' + Math.round(r.bottom));
  }
  return out;
}"""


def fits(page, tag, what):
    """Overflow that is cut off rather than scrolled is the other way a page fails to fit: every control must lie inside the page, and the scope must keep a usable height."""
    clipped = page.evaluate(CLIPPED)
    check("%s %s: every control lies inside the page" % (tag, what), not clipped, clipped)
    supply = page.get_by_role("region", name="Supply").bounding_box()
    check("%s %s: the supply strip stays one slim row (%d px)" % (tag, what, supply["height"]), supply["height"] <= 110, supply)
    if page.get_by_test_id("y-axis").count():
        h = page.evaluate("() => document.querySelector('[data-testid=y-axis]').getBoundingClientRect().height")
        check("%s %s: the scope keeps a usable height (%d px)" % (tag, what, h), h >= 200, h)


def shell(page, tag):
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    inner = page.evaluate(SCROLLERS)
    check("%s nothing inside the page scrolls" % tag, not inner, inner)
    fits(page, tag, "idle")
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s the page uses the width beside the nav (%d of %d px)" % (tag, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s the page ends at the foot of the window, neither short of it nor past it" % tag, box[5] - 24 <= box[3] <= box[5] + 1, box)
    supply = page.get_by_role("region", name="Supply")
    check("%s the supply instrument is on screen" % tag, supply.count() == 1 and supply.is_visible() and supply.bounding_box()["y"] + supply.bounding_box()["height"] <= box[5])
    if supply.count() != 1:
        return
    check("%s the output switch and the mode are in the supply instrument" % tag,
          supply.get_by_role("group", name="Output").count() == 1 or supply.get_by_role("button", name="Switch on").count() + supply.get_by_role("button", name="Switch off").count() > 0)


def capture(page, tag):
    page.get_by_role("group", name="Stop after").wait_for(timeout=2000)
    stop = page.get_by_role("group", name="Stop after")
    check("%s Stop after offers Time and Never" % tag, stop.get_by_role("button", name="Time", exact=True).count() == 1 and stop.get_by_role("button", name="Never", exact=True).count() == 1)
    length = page.get_by_label("Length", exact=True)
    check("%s a length is typed, with its own unit" % tag, length.count() == 1 and page.get_by_label("Unit", exact=True).count() == 1)
    length.fill("90")
    page.get_by_label("Unit", exact=True).select_option("m")
    check("%s 90 minutes can be entered" % tag, length.input_value() == "90" and page.get_by_label("Unit", exact=True).input_value() == "m")
    check("%s there is no multimeter and no fixed list of durations" % tag,
          page.get_by_role("button", name="Multimeter").count() == 0 and page.get_by_label("How long to run").count() == 0)


def running(page, tag, shot):
    """A watch under way: the scope has a trace, the supply says it is held, and the page still does not scroll."""
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, .* samples")).wait_for()
    page.get_by_role("img", name=re.compile(r"^Readings from")).wait_for()
    page.wait_for_timeout(1200)
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s while a watch runs the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    inner = page.evaluate(SCROLLERS)
    check("%s while a watch runs nothing inside the page scrolls" % tag, not inner, inner)
    fits(page, tag, "while a watch runs")
    check("%s the scope draws the trace and says what it shows" % tag, page.get_by_test_id("y-axis").count() == 1 and page.get_by_test_id("x-axis").inner_text().strip() != "")
    check("%s the supply says the running capture holds it" % tag, "Held by the running capture" in page.get_by_role("region", name="Supply").inner_text())
    check("%s the latest reading is a current in SI units" % tag, re.match(r"^-?[0-9.]+ [nµm]?A$", page.get_by_label("Latest reading").inner_text()) is not None, page.get_by_label("Latest reading").inner_text())
    page.screenshot(path=shot)
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()


def wakes(page, tag):
    """Stop after Wakes: the card offers it, the running session says how many whole wakes it has seen of how many it wants (and that it gives up after 12 h), and a stop by hand is named as one."""
    stop = page.get_by_role("group", name="Stop after")
    check("%s Stop after offers Wakes, Time and Never" % tag, all(stop.get_by_role("button", name=n, exact=True).count() == 1 for n in ("Wakes", "Time", "Never")))
    stop.get_by_role("button", name="Wakes", exact=True).click()
    page.get_by_label("Number of wakes").fill("2")
    page.get_by_role("button", name="Watch", exact=True).click()
    text = re.compile(r"^watching, .* 0 of 2 wakes, gives up at 12 h")
    page.get_by_label("Session").filter(has_text=text).wait_for()
    check("%s a running session says 0 of 2 wakes and the give-up" % tag, True)
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped\b")).wait_for()
    check("%s a stop by hand is named as one" % tag, True)
    stop.get_by_role("button", name="Time", exact=True).click()


def run(page, bench, theme, width, height, only, shots):
    """One page for every theme and size: opening a page takes about 20 s on a loaded host, so it is opened once and only resized, re-themed and reloaded."""
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(shots, "%s-%dx%d.png" % (theme, width, height)))
        if only in (None, "shell"):
            shell(page, tag)
        if only in (None, "capture"):
            capture(page, tag)
        if only in (None, "wakes"):
            wakes(page, tag)
        if only in (None, "running"):
            running(page, tag, os.path.join(shots, "%s-%dx%d-running.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=("shell", "capture", "wakes", "running"), default=None)
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-meter-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = base.Bench(with_meter=False)
        try:
            context = browser.new_context(viewport={"width": SIZES[0][0], "height": SIZES[0][1]})
            page = context.new_page()
            page.set_default_timeout(20000)                     # a missing control fails within seconds; the 30 s default would make a red run take minutes
            for theme in THEMES:
                for width, height in SIZES:
                    run(page, bench, theme, width, height, args.only, shots)
            context.close()
        finally:
            bench.close()
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
