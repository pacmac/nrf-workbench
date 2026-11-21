#!/usr/bin/env python3
"""The Commands page fills the window; only its form panel may scroll, and only inside itself (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_commands_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench of verify_ppk2_live.py (about a hundred commands). In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing outside the form panel scrolls; the page uses the width beside the nav and ends at the foot of the window;
  list     the commands that fit are shown, each inside the list, the list filled, with a pager that pages; search narrows the list; the area choice limits it to one area;
  form     choosing a command shows its form in the panel; a read with a long result leaves the document still (the panel scrolls inside itself).
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

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
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


def listing(page):
    return page.get_by_label("Commands", exact=True)


def pager(page):
    el = listing(page).locator("xpath=..").get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def names(page):
    return [t.split()[0] for t in listing(page).locator("li").all_inner_texts() if t.strip()]


def the_list(page, tag):
    listing(page).wait_for()
    p = pager(page)
    n = listing(page).locator("li").count()
    check("%s rows are shown, fewer than all (%d of %s)" % (tag, n, p and p[2]), p is not None and 1 <= n < p[2], (n, p))
    outside = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Commands]').getBoundingClientRect();
        return [...document.querySelectorAll('ul[aria-label=Commands] > li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    check("%s every row lies inside the list" % tag, outside == 0, outside)
    free = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Commands]'); const items = [...l.querySelectorAll(':scope > li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the list is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    cut = page.evaluate("() => [...document.querySelectorAll('ul[aria-label=Commands] > li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length")
    check("%s no row's content is cut off by its fixed height" % tag, cut == 0, cut)
    first = names(page)
    box = listing(page).locator("xpath=..")
    box.get_by_role("button", name="Next Commands").click()
    q = pager(page)
    check("%s Next pages to the later commands (%s)" % (tag, q), q is not None and q[0] == p[1] + 1 and names(page) != first, q)
    box.get_by_role("button", name="Previous Commands").click()
    check("%s Previous returns to the first" % tag, pager(page) == p and names(page) == first)
    total = p[2]

    page.get_by_label("Search commands").fill("wake_table")
    s = pager(page)
    shown = names(page)
    check("%s search narrows the list (%s of %d)" % (tag, shown, total), len(shown) >= 1 and (s is None or s[2] < total) and any("wake_table" in x for x in shown), (shown, s))
    page.get_by_label("Search commands").fill("")

    page.get_by_label("Area", exact=True).select_option("ppk2")
    a = pager(page)
    mine = names(page)
    check("%s the area choice limits the list to that area (%d shown, %s)" % (tag, len(mine), a), len(mine) >= 1 and all(x.startswith("ppk2.") for x in mine), mine[:3])
    page.get_by_label("Area", exact=True).select_option("all")
    check("%s All areas brings the whole list back" % tag, (pager(page) or (0, 0, 0))[2] == total)


def the_form(page, tag):
    page.get_by_label("Search commands").fill("bench.overview")
    listing(page).get_by_role("button", name=re.compile(r"^bench\.overview")).first.click()
    work = page.get_by_test_id("work")
    page.get_by_role("heading", name="bench.overview", exact=True).wait_for()
    check("%s choosing a command shows its form in the panel" % tag, work.get_by_role("button", name="Read").count() == 1)
    work.get_by_role("button", name="Read").click()
    work.get_by_role("status").first.wait_for()
    shell(page, tag, " with a result shown:")
    inside = page.evaluate("() => { const w = document.querySelector('[data-testid=work]').getBoundingClientRect(); const r = document.querySelector('[data-testid=work] [role=status]').getBoundingClientRect(); return r.left >= w.left - 1 && r.right <= w.right + 1; }")
    check("%s the result stays inside the panel's width" % tag, inside)
    page.get_by_label("Search commands").fill("meter.record")
    listing(page).get_by_role("button", name=re.compile(r"^meter\.record")).first.click()
    page.get_by_role("heading", name="meter.record", exact=True).wait_for()
    shell(page, tag, " with a long form shown:")
    tall = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return [w.scrollHeight > w.clientHeight + 1, getComputedStyle(w).overflowY, w.scrollHeight, w.clientHeight]; }")
    check("%s a form taller than the panel can be scrolled inside it (%d of %d px)" % (tag, tall[2], tall[3]), tall[0] and tall[1] in ("auto", "scroll"), tall)
    page.get_by_label("Search commands").fill("")


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the search and the list are there" % tag, page.get_by_label("Search commands").count() == 1 and page.get_by_test_id("pager").count() == 0)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        page.get_by_role("navigation", name="Pages").get_by_role("button", name="Commands", exact=True).or_(page.get_by_role("navigation", name="Pages").get_by_role("button", name="More", exact=True)).first.wait_for()
        go_to(page, "Commands")
        page.get_by_label("Search commands").wait_for()
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(shots, "commands-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        shell(page, tag)
        the_list(page, tag)
        the_form(page, tag)
        page.screenshot(path=os.path.join(shots, "commands-%s-%dx%d-form.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-commands-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = base.Bench(with_meter=False)
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
