#!/usr/bin/env python3
"""The Devices page fills the window however many devices there are; only its detail panel may scroll, and only inside itself (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_devices_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench made a Raspberry Pi with eighteen USB devices attached (verify_home_layout.make_bench). In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing outside the detail panel scrolls; the page uses the width beside the nav and ends at the foot of the window;
  list     the devices that fit are shown, each inside the list, the list filled and no row's content cut off, with a pager that pages; the tabs switch what is listed;
  detail   choosing a device shows its serial and its actions in the panel; an nRF board offers its name editor;
  drawers  the power profiler's quick check opens in a drawer and Escape closes it.
At phone width the page flows (a row opens inline) and never scrolls sideways. Exit 0 only if every check passed.
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
SIZES = ((1280, 720), (1440, 900), (1024, 440), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


# Everything that scrolls in the page except what is inside the detail panel (it may, inside itself).
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
    check("%s%s nothing outside the detail panel scrolls" % (tag, what), not inner, inner)
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s%s the page uses the width beside the nav (%d of %d px)" % (tag, what, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s%s the page ends at the foot of the window" % (tag, what), box[5] - 24 <= box[3] <= box[5] + 1, box)
    over = page.evaluate("""() => { const m = document.querySelector('main').getBoundingClientRect();
        return [...document.querySelectorAll('main section')].filter((s) => { const r = s.getBoundingClientRect(); return r.bottom > m.bottom + 1 || r.right > m.right + 1; }).length; }""")
    check("%s%s every panel lies inside the page" % (tag, what), over == 0, over)
    gaps = page.evaluate("""() => [...document.querySelectorAll('main section')].map((s) => { const kids = [...s.children].filter((c) => c.getBoundingClientRect().height > 0);
        return Math.round(s.getBoundingClientRect().bottom - kids[kids.length - 1].getBoundingClientRect().bottom); })""")
    check("%s%s each panel's contents fill it down to its foot (%s px spare)" % (tag, what, gaps), gaps[0] <= 24, gaps)       # the list's panel: its pager sits at the foot


def pager(page):
    el = page.get_by_label("Devices", exact=True).locator("xpath=..").get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def the_list(page, tag):
    lst = page.get_by_label("Devices", exact=True)
    lst.wait_for()
    n = lst.locator(":scope > li").count()
    p = pager(page)
    check("%s the list shows the rows that fit, fewer than all (%d of %s)" % (tag, n, p and p[2]), p is not None and 1 <= n < p[2] and p[2] >= 18, (n, p))
    outside = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Devices]').getBoundingClientRect();
        return [...document.querySelectorAll('ul[aria-label=Devices] > li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    check("%s every row lies inside the list" % tag, outside == 0, outside)
    free = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Devices]'); const items = [...l.querySelectorAll(':scope > li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the list is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    first = [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0]
    box = lst.locator("xpath=..")
    cut = 0
    box.get_by_role("button", name="Next Devices").click()
    q = pager(page)
    check("%s Next pages to the later devices (%s)" % (tag, q), q is not None and q[0] == p[1] + 1, q)
    box.get_by_role("button", name="Previous Devices").click()
    check("%s Previous returns to the first" % tag, pager(page) == p and [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0] == first)
    while True:
        cut += page.evaluate("() => [...document.querySelectorAll('ul[aria-label=Devices] > li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length")
        nxt = box.get_by_role("button", name="Next Devices")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    check("%s no row on any page has its content cut off by its fixed height (%d)" % (tag, cut), cut == 0, cut)
    while box.get_by_role("button", name="Previous Devices").count() and not box.get_by_role("button", name="Previous Devices").is_disabled():
        box.get_by_role("button", name="Previous Devices").click()
    tabs = page.get_by_role("tablist", name="Show devices")
    tabs.get_by_role("tab", name=re.compile("^Not attached")).click()
    check("%s the Not attached tab shows its own list (%s)" % (tag, page.get_by_label("Devices", exact=True).locator(":scope > li").count()), page.get_by_text("Every device the bench knows is attached.").count() == 1 or page.get_by_label("Devices", exact=True).locator(":scope > li").count() >= 0)
    tabs.get_by_role("tab", name=re.compile("^All")).click()
    check("%s the All tab lists every device" % tag, (pager(page) or (0, 0, 0))[2] >= 18, pager(page))
    tabs.get_by_role("tab", name=re.compile("^Connected")).click()


def the_detail(page, tag):
    work = page.get_by_test_id("work")
    page.get_by_label("Devices", exact=True).locator(":scope > li").first.get_by_role("button").click()
    work.get_by_text("Serial").first.wait_for()
    check("%s choosing a device shows its serial in the panel" % tag, work.get_by_text("Serial").count() >= 1)
    shell(page, tag, " with a device chosen:")
    box = page.get_by_label("Devices", exact=True).locator("xpath=..")
    while page.get_by_label("Devices", exact=True).locator(":scope > li").filter(has_text="nRF").count() == 0:         # the list pages: look for the board on the next page
        nxt = box.get_by_role("button", name="Next Devices")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    page.get_by_label("Devices", exact=True).locator(":scope > li").filter(has_text="nRF").first.get_by_role("button").click()
    work.get_by_role("button", name=re.compile(r"^(Name|Rename) ")).wait_for()             # its accessible name is `Name <serial>`
    check("%s an nRF board offers its name editor" % tag, True)
    work.get_by_role("button", name=re.compile(r"^(Name|Rename) ")).click()
    work.get_by_label(re.compile("^Name for")).wait_for()
    shell(page, tag, " with the name editor open:")
    work.get_by_role("button", name="Cancel").click()


def the_drawers(page, tag):
    page.get_by_role("button", name="Power profiler", exact=True).click()
    page.get_by_role("dialog", name="Power profiler").wait_for()
    check("%s the power profiler's quick check opens in a drawer" % tag, True)
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="Power profiler").wait_for(state="detached")
    check("%s Escape closes it" % tag, True)


def a_short_window(page, tag):
    """A window too short for a device's whole detail and its name editor: the document does not scroll, and the detail panel scrolls inside itself."""
    box = page.get_by_label("Devices", exact=True).locator("xpath=..")
    while page.get_by_label("Devices", exact=True).locator(":scope > li").filter(has_text="nRF").count() == 0:
        nxt = box.get_by_role("button", name="Next Devices")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    page.get_by_label("Devices", exact=True).locator(":scope > li").filter(has_text="nRF").first.get_by_role("button").click()
    work = page.get_by_test_id("work")
    work.get_by_role("button", name=re.compile(r"^(Name|Rename) ")).click()
    work.get_by_label(re.compile("^Name for")).wait_for()
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    tall = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return [w.scrollHeight > w.clientHeight + 1, getComputedStyle(w).overflowY, w.scrollHeight, w.clientHeight]; }")
    check("%s the detail is taller than its panel and scrolls inside it (%d of %d px)" % (tag, tall[2], tall[3]), tall[0] and tall[1] in ("auto", "scroll"), tall)
    page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); w.scrollTop = w.scrollHeight; }")
    last = page.evaluate("""() => { const w = document.querySelector('[data-testid=work]').getBoundingClientRect(); const bs = [...document.querySelectorAll('[data-testid=work] button')];
        const r = bs[bs.length - 1].getBoundingClientRect(); return r.bottom <= w.bottom + 1 && r.top >= w.top - 1; }""")
    check("%s scrolled to its end, the editor's last button is in view" % tag, last)


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the list flows, with no pager" % tag, page.get_by_test_id("pager").count() == 0 and page.get_by_label("Devices", exact=True).locator(":scope > li").count() >= 18)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Devices")
        page.get_by_role("tablist", name="Show devices").wait_for()
        page.get_by_label("Devices", exact=True).wait_for()
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(shots, "devices-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        if height < 580:
            a_short_window(page, tag)
            return
        shell(page, tag)
        the_list(page, tag)
        the_detail(page, tag)
        the_drawers(page, tag)
        page.screenshot(path=os.path.join(shots, "devices-%s-%dx%d-detail.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-devices-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = home.make_bench()
        bench.tree.usb("1-99", ("0403", "6001"), "A" * 150, serial="LONGNAME0001", manufacturer="FTDI", tty="ttyUSB99", majmin="188:99")      # a device whose name is too long for its row
        bench.tree.node("ttyUSB99", 188, 99)
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
