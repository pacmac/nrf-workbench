#!/usr/bin/env python3
"""The GPIO page fills the window; only its form panel may scroll, and only inside itself (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_gpio_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench made a Raspberry Pi with a pin map (verify_home_layout.make_bench). In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing outside the form panel scrolls; the page uses the width beside the nav and ends at the foot of the window;
  map      all forty pins of the header are on screen inside their panel, none under 22 px high, odd pins left and even right; a reserved pin stays disabled;
  form     picking a GPIO selects it and shows its assignment form in the panel; choosing an output opens its extra fields and the window still does not scroll.
At phone width the page flows and never scrolls sideways. Exit 0 only if every check passed.
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
import verify_home_layout as home  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (1024, 500), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
MIN_ROW = 22                     # px: a pin row never smaller than this
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


# Everything that scrolls in the page except what is inside the form panel (it may, inside itself).
SCROLLERS = """() => {
  const main = document.querySelector('main');
  const work = document.querySelector('[data-testid=work]');
  const out = [];
  for (const el of main.querySelectorAll('*')) {
    if (work && work.contains(el)) continue;
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1 && el.tagName !== 'TEXTAREA') out.push(el.tagName + '.' + String(el.className).slice(0, 60));
  }
  return out;
}"""


def shell(page, tag, what=""):
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s%s the document does not scroll (%d of %d px)" % (tag, what, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    inner = page.evaluate(SCROLLERS)
    check("%s%s nothing outside the form panel scrolls" % (tag, what), not inner, inner)
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s%s the page uses the width beside the nav (%d of %d px)" % (tag, what, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s%s the page ends at the foot of the window" % (tag, what), box[5] - 24 <= box[3] <= box[5] + 1, box)
    over = page.evaluate("""() => { const m = document.querySelector('main').getBoundingClientRect();
        return [...document.querySelectorAll('main section')].filter((s) => { const r = s.getBoundingClientRect(); return r.bottom > m.bottom + 1 || r.right > m.right + 1; }).length; }""")
    check("%s%s both panels lie inside the page" % (tag, what), over == 0, over)


def the_map(page, tag):
    group = page.get_by_role("group", name="40-pin header")
    group.wait_for()
    cells = page.evaluate("""() => { const g = document.querySelector('[role=group][aria-label="40-pin header"]').getBoundingClientRect();
        const cs = [...document.querySelectorAll('[role=group][aria-label="40-pin header"] .contents > *')];
        return {n: cs.length, outside: cs.filter((c) => { const r = c.getBoundingClientRect(); return r.bottom > g.bottom + 1 || r.top < g.top - 1 || r.right > g.right + 1 || r.left < g.left - 1; }).length,
                min: Math.min(...cs.map((c) => c.getBoundingClientRect().height)), left: cs.filter((c, i) => i % 2 === 0).every((c, i) => c.textContent.trim().startsWith(String(2 * i + 1))),
                right: cs.filter((c, i) => i % 2 === 1).every((c, i) => c.textContent.trim().startsWith(String(2 * i + 2))) }; }""")
    check("%s the map holds all forty pins (%d)" % (tag, cells["n"]), cells["n"] == 40, cells)
    check("%s every pin lies inside the map" % tag, cells["outside"] == 0, cells)
    check("%s no pin row is under %d px high (the smallest is %d px)" % (tag, MIN_ROW, cells["min"]), cells["min"] >= MIN_ROW, cells)
    foot = page.evaluate("""() => { const g = document.querySelector('[role=group][aria-label="40-pin header"]').getBoundingClientRect();
        const cs = [...document.querySelectorAll('[role=group][aria-label="40-pin header"] .contents > *')]; return g.bottom - cs[cs.length - 1].getBoundingClientRect().bottom; }""")
    check("%s the twenty rows share the panel's height: the last ends at the foot of the map (%d px short)" % (tag, foot), foot <= 4, foot)
    check("%s odd pins are on the left and even pins on the right, 1 at the top" % tag, cells["left"] and cells["right"], cells)
    reserved = page.get_by_role("button", name=re.compile(r"GPIO 0, reserved"))
    check("%s a reserved pin stays disabled" % tag, reserved.count() == 1 and reserved.is_disabled(), reserved.count())


def the_form(page, tag):
    pin = page.get_by_role("button", name=re.compile(r"^Pin 7, GPIO 4,"))
    pin.click()
    check("%s picking a GPIO selects it" % tag, pin.get_attribute("aria-pressed") == "true")
    page.get_by_role("heading", name="Assignment for GPIO 4").wait_for()
    work = page.get_by_test_id("work")
    check("%s its assignment form is in the panel" % tag, work.get_by_label("Direction").count() == 1)
    work.get_by_label("Direction").select_option("output")
    work.get_by_label("Longest pulse (ms)").wait_for()
    check("%s choosing an output opens its extra fields" % tag, work.get_by_label("State when taken").count() == 1)
    shell(page, tag, " with an output form open:")
    inside = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return w.getBoundingClientRect().bottom <= w.closest('section').getBoundingClientRect().bottom + 1; }")
    check("%s the form's panel lies inside its card, not past it" % tag, inside)
    tall = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return [w.scrollHeight > w.clientHeight + 1, getComputedStyle(w).overflowY, w.scrollHeight, w.clientHeight]; }")
    check("%s a form taller than the panel can be scrolled inside it (%d of %d px)" % (tag, tall[2], tall[3]), (not tall[0]) or tall[1] in ("auto", "scroll"), tall)


def a_short_window(page, tag):
    """A window too short for twenty rows of MIN_ROW: the document still does not scroll and no pin is lost, because the map scrolls inside its own panel."""
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    panel = page.evaluate("""() => { const p = document.querySelector('[role=group][aria-label="40-pin header"]').closest('section');
        return [p.scrollHeight > p.clientHeight + 1, getComputedStyle(p).overflowY]; }""")
    check("%s the map's panel scrolls inside itself (%s)" % (tag, panel), panel[0] and panel[1] in ("auto", "scroll"), panel)
    page.evaluate("() => { const p = document.querySelector('[role=group][aria-label=\"40-pin header\"]').closest('section'); p.scrollTop = p.scrollHeight; }")
    last = page.evaluate("""() => { const p = document.querySelector('[role=group][aria-label="40-pin header"]').closest('section').getBoundingClientRect();
        const cs = [...document.querySelectorAll('[role=group][aria-label="40-pin header"] .contents > *')]; const r = cs[cs.length - 1].getBoundingClientRect();
        return r.bottom <= p.bottom + 1 && r.top >= p.top - 1; }""")
    check("%s scrolled to the end, pin 40 is in view" % tag, last)
    page.evaluate("() => { const p = document.querySelector('[role=group][aria-label=\"40-pin header\"]').closest('section'); p.scrollTop = 0; }")


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the map is there" % tag, page.get_by_role("group", name="40-pin header").count() == 1)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        page.get_by_role("navigation", name="Pages").get_by_role("button", name="GPIO", exact=True).or_(page.get_by_role("navigation", name="Pages").get_by_role("button", name="More", exact=True)).first.wait_for()
        go_to(page, "GPIO")
        page.get_by_role("group", name="40-pin header").wait_for()
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(shots, "gpio-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        if height < 580:
            a_short_window(page, tag)
            return
        shell(page, tag)
        the_map(page, tag)
        the_form(page, tag)
        page.screenshot(path=os.path.join(shots, "gpio-%s-%dx%d-form.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-gpio-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = home.make_bench()
        try:
            context = browser.new_context(viewport={"width": SIZES[0][0], "height": SIZES[0][1]})
            page = context.new_page()
            page.set_default_timeout(20000)
            for theme in THEMES:
                for width, height in SIZES:
                    run(page, bench, theme, width, height, shots)
            context.close()
        finally:
            bench.close()
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
