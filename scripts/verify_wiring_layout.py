#!/usr/bin/env python3
"""The Wiring page fills the window however many boards, adapters and wires there are; only its wires panel may scroll, and only inside itself (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_wiring_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench made a Raspberry Pi (verify_home_layout.make_bench) with twelve nRF boards and sixteen USB adapters: four adapters are connected to one board as debug logs
(through the page's own session, as a person would) and twelve are plugged in but not connected. In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing outside the wires panel scrolls; the page uses the width beside the nav and ends at the foot of the window;
  devices  the boards that fit are shown, each inside the list, the list filled, no card cut off on any page, with a pager that pages;
  adapters a one-line notice says how many adapters are plugged in but not connected and its button opens every one of them in a drawer, closed by Escape;
  wires    the board with four wires shows them in the panel and Connect something opens its form there; at a short window the panel scrolls inside itself.
At phone width the page flows and never scrolls sideways. Exit 0 only if every check passed.
"""
import argparse
import json
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
from fakes import SERIAL  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (1024, 440), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
BOARDS = 12                      # nRF boards: the fake bench's own and eleven more
CONNECTED = 4                    # boards with a debug log wired to an adapter (a board has one: connecting a second to it changes the first)
UNCONNECTED = home.ADAPTERS - CONNECTED
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def make_bench():
    bench = home.make_bench()
    t = bench.tree
    for i in range(BOARDS - 1):
        t.app_board(name="1-%d" % (40 + i), tty="ttyACM%d" % (10 + i), majmin="166:%d" % (10 + i), serial="XB%02dBOARD" % i)         # a connection is named from the first five characters of its board's serial: these differ
        t.node("ttyACM%d" % (10 + i), 166, 10 + i)
    return bench


def seed(context, bench):
    """Four boards each given a debug log on an adapter, through the service as the page does it."""
    root = bench.url.rstrip("/")
    paths = dict((c["name"], c["path"]) for c in context.request.get(root + "/api/commands").json()["commands"])
    csrf = context.request.get(root + "/api/session").json().get("csrf")
    headers = dict({"Content-Type": "application/json"}, **({"X-CSRF-Token": csrf} if csrf else {}))
    for i, board in enumerate([SERIAL, "XB00BOARD", "XB01BOARD", "XB02BOARD"][:CONNECTED]):
        r = context.request.post(root + paths["wiring.connect"], data=json.dumps({"device": board, "intent": "log", "adapter": "ADAPTER%02d" % i, "baud": 115200, "send": False}), headers=headers)
        assert r.ok and r.json().get("ok"), (i, r.status, r.text())


# Everything that scrolls in the page except what is inside the wires panel (it may, inside itself) and an open drawer.
SCROLLERS = """() => {
  const main = document.querySelector('main');
  const work = document.querySelector('[data-testid=work]');
  const out = [];
  for (const el of main.querySelectorAll('*')) {
    if (work && work.contains(el)) continue;
    if (el.closest('[role=dialog]')) continue;
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1 && el.tagName !== 'TEXTAREA') out.push(el.tagName + '.' + String(el.className).slice(0, 60));
  }
  return out;
}"""


def shell(page, tag, what=""):
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s%s the document does not scroll (%d of %d px)" % (tag, what, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    inner = page.evaluate(SCROLLERS)
    check("%s%s nothing outside the wires panel scrolls" % (tag, what), not inner, inner)
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s%s the page uses the width beside the nav (%d of %d px)" % (tag, what, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s%s the page ends at the foot of the window" % (tag, what), box[5] - 24 <= box[3] <= box[5] + 1, box)
    over = page.evaluate("""() => { const m = document.querySelector('main').getBoundingClientRect(); const work = document.querySelector('[data-testid=work]');
        return [...document.querySelectorAll('main section')].filter((s) => { if (work && work.contains(s)) return false; const r = s.getBoundingClientRect(); return r.bottom > m.bottom + 1 || r.right > m.right + 1; }).length; }""")
    check("%s%s every panel lies inside the page" % (tag, what), over == 0, over)
    gaps = page.evaluate("""() => { const work = document.querySelector('[data-testid=work]'); return [...document.querySelectorAll('main > div > div > section, main section')].filter((s) => !(work && work.contains(s))).slice(0, 1).map((s) => { const kids = [...s.children].filter((c) => c.getBoundingClientRect().height > 0);
        return Math.round(s.getBoundingClientRect().bottom - kids[kids.length - 1].getBoundingClientRect().bottom); }); }""")
    check("%s%s the devices panel is filled down to its foot (%s px spare)" % (tag, what, gaps), all(g <= 24 for g in gaps), gaps)


def pager(page):
    el = page.get_by_label("Devices", exact=True).locator("xpath=..").get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def the_devices(page, tag):
    lst = page.get_by_label("Devices", exact=True)
    lst.wait_for()
    n = lst.locator(":scope > li").count()
    p = pager(page)
    check("%s the list shows the boards that fit, fewer than all (%d of %s)" % (tag, n, p and p[2]), p is not None and 1 <= n < p[2] and p[2] == BOARDS, (n, p))
    outside = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Devices]').getBoundingClientRect();
        return [...document.querySelectorAll('ul[aria-label=Devices] > li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    check("%s every board lies inside the list" % tag, outside == 0, outside)
    free = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Devices]'); const items = [...l.querySelectorAll(':scope > li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the list is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    box = lst.locator("xpath=..")
    first = [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0]
    box.get_by_role("button", name="Next Devices").click()
    q = pager(page)
    check("%s Next pages to the later boards (%s)" % (tag, q), q is not None and q[0] == p[1] + 1, q)
    box.get_by_role("button", name="Previous Devices").click()
    check("%s Previous returns to the first" % tag, pager(page) == p and [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0] == first)
    cut = 0
    while True:
        cut += page.evaluate("() => [...document.querySelectorAll('ul[aria-label=Devices] > li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length")
        nxt = box.get_by_role("button", name="Next Devices")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    check("%s no board on any page has its card cut off by its fixed height (%d)" % (tag, cut), cut == 0, cut)
    while box.get_by_role("button", name="Previous Devices").count() and not box.get_by_role("button", name="Previous Devices").is_disabled():
        box.get_by_role("button", name="Previous Devices").click()


def choose_wired(page):
    """Select a board that has a wire (its card says `1 connection`), paging to it."""
    box = page.get_by_label("Devices", exact=True).locator("xpath=..")
    card = page.get_by_label("Devices", exact=True).locator(":scope > li").filter(has_text=re.compile(r"(?<!\d)1 connection(?!s)"))       # the chip and the count are separate spans: no word boundary before the 1
    while card.count() == 0:
        nxt = box.get_by_role("button", name="Next Devices")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    card.first.get_by_role("button").click()


def the_adapters(page, tag):
    note = page.get_by_test_id("adapters-notice")
    note.wait_for()
    said = int(re.match(r"^(\d+) ", note.inner_text().strip()).group(1))              # the Pi's own UART is offered too, beside the sixteen USB adapters
    check("%s a one-line notice counts the adapters not connected (%d, at least the %d USB ones)" % (tag, said, UNCONNECTED), said >= UNCONNECTED, note.inner_text())
    h = note.bounding_box()["height"]
    check("%s the notice is one line high (%d px)" % (tag, h), h <= 60, h)
    note.get_by_role("button").click()
    page.get_by_role("dialog", name="Adapters plugged in").wait_for()
    groups = page.get_by_role("dialog", name="Adapters plugged in").get_by_role("group").count()
    check("%s the drawer holds every adapter the notice counts (%d of %d)" % (tag, groups, said), groups == said, groups)
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="Adapters plugged in").wait_for(state="detached")
    check("%s Escape closes it" % tag, True)


def the_wires(page, tag):
    work = page.get_by_test_id("work")
    box = page.get_by_label("Devices", exact=True).locator("xpath=..")
    bare = page.get_by_label("Devices", exact=True).locator(":scope > li").filter(has_text="nothing connected")
    while bare.count() == 0:                                           # a board with no wire: choosing it must change what the panel shows
        nxt = box.get_by_role("button", name="Next Devices")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    bare.first.get_by_role("button").click()
    work.get_by_text("Nothing is wired to this device yet").wait_for()
    check("%s choosing a board with no wire shows that in the panel" % tag, work.locator("ul[aria-label^='Connections of']").count() == 0)
    while box.get_by_role("button", name="Previous Devices").count() and not box.get_by_role("button", name="Previous Devices").is_disabled():
        box.get_by_role("button", name="Previous Devices").click()
    choose_wired(page)
    wires = work.locator("ul[aria-label^='Connections of'] > li")
    wires.first.wait_for()
    check("%s the chosen board's wire is in the panel (%d)" % (tag, wires.count()), wires.count() == 1, wires.count())
    shell(page, tag, " with a board's wires shown:")
    page.get_by_role("button", name="Connect something").click()
    work.get_by_role("heading").or_(work.get_by_text("What do you want", exact=False)).first.wait_for()
    check("%s Connect something opens its form inside the panel" % tag, work.get_by_role("button", name="Cancel").count() >= 1)
    shell(page, tag, " with the form open:")
    inside = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return w.getBoundingClientRect().bottom <= w.closest('section').getBoundingClientRect().bottom + 1; }")
    check("%s the panel lies inside its card" % tag, inside)
    work.get_by_role("button", name="Cancel").first.click()


def a_short_window(page, tag):
    """A window too short for four wires and the add form: the document does not scroll and the wires panel scrolls inside itself."""
    work = page.get_by_test_id("work")
    choose_wired(page)
    work.locator("ul[aria-label^='Connections of'] > li").first.wait_for()
    page.get_by_role("button", name="Connect something").click()
    work.get_by_role("button", name="Cancel").first.wait_for()
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    tall = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return [w.scrollHeight > w.clientHeight + 1, getComputedStyle(w).overflowY, w.scrollHeight, w.clientHeight]; }")
    check("%s the wires and the form are taller than the panel and scroll inside it (%d of %d px)" % (tag, tall[2], tall[3]), tall[0] and tall[1] in ("auto", "scroll"), tall)
    page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); w.scrollTop = w.scrollHeight; }")
    last = page.evaluate("""() => { const w = document.querySelector('[data-testid=work]').getBoundingClientRect(); const bs = [...document.querySelectorAll('[data-testid=work] button')];
        const r = bs[bs.length - 1].getBoundingClientRect(); return r.bottom <= w.bottom + 1 && r.top >= w.top - 1; }""")
    check("%s scrolled to its end, the form's last button is in view" % tag, last)


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    n = page.get_by_role("group", name=re.compile("is (plugged in|built into the Pi) but not connected|is available but not connected")).count()
    check("%s the adapters are listed on the page, with no pager (%d)" % (tag, n), page.get_by_test_id("pager").count() == 0 and n >= UNCONNECTED, n)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Wiring")
        page.get_by_role("button", name="Look again").wait_for()
        page.wait_for_timeout(900)
        page.screenshot(path=os.path.join(shots, "wiring-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        if height < 580:
            a_short_window(page, tag)
            return
        shell(page, tag)
        the_devices(page, tag)
        the_adapters(page, tag)
        the_wires(page, tag)
        page.screenshot(path=os.path.join(shots, "wiring-%s-%dx%d-wires.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-wiring-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = make_bench()
        try:
            context = browser.new_context(viewport={"width": SIZES[0][0], "height": SIZES[0][1]})
            page = context.new_page()
            page.set_default_timeout(20000)
            page.goto(bench.url)
            seed(context, bench)
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
