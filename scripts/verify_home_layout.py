#!/usr/bin/env python3
"""The Home page is one screen and never scrolls (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_home_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench of verify_ppk2_live.py, made a Raspberry Pi with a pin map, with ten USB devices attached (more than the Devices list has room for) and a few commands run.
In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing inside the page scrolls; the page uses the width beside the nav and ends at the foot of the window;
  lists    Devices shows the rows that fit, each inside its list, the list filled, with a pager that pages; Recent activity is newest first;
  the Pi   the 40-pin header is a strip with all 40 pins inside its tile;
  drawers  the power profiler check opens over the page and Escape closes it.
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
import verify_ppk2_live as base  # noqa: E402
from nrf_workbench.core.result import Result  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
ADAPTERS = 16                    # on top of the profiler and the RAK board: eighteen devices, more than the list has room for at 1440x900 (fourteen rows)
RAN = 30                         # commands run: more than Recent activity has room for, so the column's two lists have to share its height
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def make_bench():
    """The PPK2 bench, with the Pi's pin map switched on (a folder for the pin map, and a device tree that says Raspberry Pi) and extra adapters plugged in."""
    import fakes
    from nrf_workbench.core import config as cfgmod
    tmp = tempfile.mkdtemp(prefix="nrfwb-home-layout-")
    real_load, real_init = cfgmod.load, fakes.Tree.__init__

    def load(env=None, overrides=None, **kw):
        overrides = dict(overrides or {})
        overrides["paths"] = dict(overrides.get("paths", {}), gpio=os.path.join(tmp, "gpio"))
        return real_load(env=env, overrides=overrides, **kw)

    def init(self, *a, **kw):                      # the Pi capability is decided when the service starts, so the device tree must already say Raspberry Pi
        real_init(self, *a, **kw)
        os.makedirs(os.path.join(self.proc, "device-tree"), exist_ok=True)
        with open(os.path.join(self.proc, "device-tree", "model"), "wb") as fh:
            fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")
    cfgmod.load, fakes.Tree.__init__ = load, init
    try:
        bench = base.Bench(with_meter=False)
    finally:
        cfgmod.load, fakes.Tree.__init__ = real_load, real_init
    t = bench.tree
    for i in range(ADAPTERS):
        t.usb("1-%d" % (10 + i), ("0403", "6001"), "FT232R USB UART %d" % i, serial="ADAPTER%02d" % i, manufacturer="FTDI", tty="ttyUSB%d" % i, majmin="188:%d" % i)
        t.node("ttyUSB%d" % i, 188, i)
    for i in range(RAN):
        bench.host.streams.activity.record("seeder", "seed.%02d" % i, {"index": i}, Result.success({}, "ran %d" % i), 0.01)
    return bench


SCROLLERS = """() => {
  const main = document.querySelector('main');
  const out = [];
  for (const el of main.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1 && el.tagName !== 'TEXTAREA') out.push(el.tagName + '.' + String(el.className).slice(0, 60));
  }
  return out;
}"""


def shell(page, tag):
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    inner = page.evaluate(SCROLLERS)
    check("%s nothing inside the page scrolls" % tag, not inner, inner)
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s the page uses the width beside the nav (%d of %d px)" % (tag, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s the page ends at the foot of the window" % tag, box[5] - 24 <= box[3] <= box[5] + 1, box)
    over = page.evaluate("""() => { const m = document.querySelector('main').getBoundingClientRect();
        return [...document.querySelectorAll('main section')].filter((s) => { const r = s.getBoundingClientRect(); return r.bottom > m.bottom + 1 || r.right > m.right + 1; }).length; }""")
    check("%s every section lies inside the page" % tag, over == 0, over)


def pager_of(page, label):
    """(first, last, total) of a list's pager, or None when its rows all fit."""
    el = page.get_by_label(label, exact=True).locator("xpath=..").get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def rows_of(page, label):
    return [t.strip() for t in page.get_by_label(label, exact=True).locator("li").all_inner_texts()]


def lists(page, tag):
    for heading in ("Devices", "Connections", "Recent activity", "The Pi"):
        check("%s the %s section is on screen" % (tag, heading), page.get_by_role("button", name="%s: open the page" % heading).is_visible())
    lst = page.get_by_label("Devices", exact=True)
    n = lst.locator("li").count()
    p = pager_of(page, "Devices")
    check("%s Devices shows the rows that fit, fewer than all (%d of %s)" % (tag, n, p and p[2]), p is not None and 1 <= n < p[2] and p[2] >= 10, (n, p))
    outside = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Devices]').getBoundingClientRect();
        return [...document.querySelectorAll('ul[aria-label=Devices] > li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    check("%s every device row lies inside the list" % tag, outside == 0, outside)
    free = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Devices]'); const items = [...l.querySelectorAll(':scope > li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the Devices list is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    cut = page.evaluate("() => [...document.querySelectorAll('ul[aria-label=Devices] > li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length")
    check("%s no device row's content is cut off by its fixed height" % tag, cut == 0, cut)
    first = rows_of(page, "Devices")[0]
    nxt = page.get_by_label("Devices", exact=True).locator("xpath=..").get_by_role("button", name="Next Devices")
    nxt.click()
    q = pager_of(page, "Devices")
    check("%s Next pages to the later devices (%s)" % (tag, q), q is not None and q[0] == p[1] + 1 and rows_of(page, "Devices")[0] != first, q)
    page.get_by_label("Devices", exact=True).locator("xpath=..").get_by_role("button", name="Previous Devices").click()
    check("%s Previous returns to the first (%s)" % (tag, pager_of(page, "Devices")), pager_of(page, "Devices") == p and rows_of(page, "Devices")[0] == first)
    ratio = page.evaluate("""() => { const h = (l) => document.querySelector('ul[aria-label="' + l + '"]').closest('section').getBoundingClientRect().height; return h('Recent activity') / h('Connections'); }""")
    check("%s Recent activity has 3 shares of its column to Connections' 2 (ratio %.2f)" % (tag, ratio), 1.4 <= ratio <= 1.6, ratio)
    pr = pager_of(page, "Recent activity")
    check("%s Recent activity pages too (%s)" % (tag, pr), pr is not None and pr[2] >= RAN and pr[1] < pr[2], pr)
    recent = [r for r in rows_of(page, "Recent activity") if "seed." in r]
    check("%s Recent activity is newest first (%s)" % (tag, [r.split()[0] for r in recent][:3]), [r.split()[0] for r in recent] == ["seed.%02d" % i for i in range(RAN - 1, RAN - 1 - len(recent), -1)] and len(recent) >= 2, recent)


def the_pi(page, tag):
    strip = page.get_by_role("group", name=re.compile(r"^Pi header"))
    check("%s the header is a strip of 40 pins" % tag, strip.locator("button").count() == 40, strip.locator("button").count())
    out = page.evaluate("""() => { const t = document.querySelector('[aria-label^="Pi header"]').closest('section').getBoundingClientRect();
        return [...document.querySelectorAll('[aria-label^="Pi header"] button')].filter((b) => { const r = b.getBoundingClientRect(); return r.right > t.right + 1 || r.left < t.left - 1; }).length; }""")
    check("%s every pin lies inside The Pi's tile" % tag, out == 0, out)
    h = strip.bounding_box()["height"]
    check("%s the strip is two rows high (%d px), not a column of twenty" % (tag, h), h <= 90, h)


def the_drawer(page, tag):
    page.get_by_role("button", name="Power profiler", exact=True).click()
    page.get_by_role("dialog", name="Power profiler").wait_for()
    check("%s the power profiler check opens over the page" % tag, True)
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="Power profiler").wait_for(state="detached")
    check("%s Escape closes it" % tag, True)
    shell(page, tag + " with the drawer closed:")


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the sections flow: Devices and Recent activity are there" % tag, page.get_by_role("button", name="Devices: open the page").count() == 1 and page.get_by_role("button", name="Recent activity: open the page").count() == 1)
    check("%s there is no pager on a phone" % tag, page.get_by_test_id("pager").count() == 0)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Home")
        page.get_by_role("button", name="Devices: open the page").wait_for()
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(shots, "home-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        shell(page, tag)
        lists(page, tag)
        the_pi(page, tag)
        the_drawer(page, tag)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-home-layout-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = make_bench()
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
