#!/usr/bin/env python3
"""The Projects page fills the window however many projects there are; only its detail panel may scroll, and only inside itself (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_projects_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench with a package store (verify_firmware_layout.make_bench) and fifteen projects written through the page's own session, one with a purpose of 150 characters
with no space and one with forty lines of notes. In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing outside the detail panel scrolls; the page uses the width beside the nav and ends at the foot of the window; each panel's contents fill it;
  list     the projects that fit are shown, each inside the list, the list filled, no row's content cut off on any page, with a pager that pages;
  detail   choosing a project shows it in the panel; the one with the long notes scrolls inside the panel and its last button is reachable; a new project made at the foot of the list is chosen.
At phone width the page flows and never scrolls sideways, however long a purpose is. Exit 0 only if every check passed.
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
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))

from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
import verify_firmware_layout as fw  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
PLAIN = 13                       # projects p00 to p12
LONG = "x" * 150
NOTES = "\n".join("  note line %02d: a sentence of notes that takes a line of the detail" % i for i in range(40))
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def seed(context, bench):
    root = bench.url.rstrip("/")
    paths = dict((c["name"], c["path"]) for c in context.request.get(root + "/api/commands").json()["commands"])
    csrf = context.request.get(root + "/api/session").json().get("csrf")
    headers = dict({"Content-Type": "application/octet-stream"}, **({"X-CSRF-Token": csrf} if csrf else {}))
    manifests = dict(("p%02d" % i, 'name: p%02d\npurpose: "project number %d"\ndevices: {}\n' % (i, i)) for i in range(PLAIN))
    manifests["wide-purpose"] = 'name: wide-purpose\npurpose: "%s"\ndevices: {}\n' % LONG
    manifests["tall-notes"] = 'name: tall-notes\npurpose: "a project with long notes"\nnotes: |\n%s\ndevices: {}\n' % NOTES
    for name, text in manifests.items():
        r = context.request.post(root + paths["project.put"], params={"project": name}, data=text.encode(), headers=headers)
        assert r.ok, (name, r.status, r.text())


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
    check("%s%s the list's panel is filled down to its foot (%s px spare)" % (tag, what, gaps), gaps[0] <= 24, gaps)


def pager(page):
    el = page.get_by_label("Projects", exact=True).locator("xpath=..").get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def the_list(page, tag):
    lst = page.get_by_label("Projects", exact=True)
    lst.wait_for()
    n = lst.locator(":scope > li").count()
    p = pager(page)
    check("%s the list shows the rows that fit, fewer than all (%d of %s)" % (tag, n, p and p[2]), p is not None and 1 <= n < p[2] and p[2] >= PLAIN + 2, (n, p))
    outside = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Projects]').getBoundingClientRect();
        return [...document.querySelectorAll('ul[aria-label=Projects] > li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    check("%s every row lies inside the list" % tag, outside == 0, outside)
    free = page.evaluate("""() => { const l = document.querySelector('ul[aria-label=Projects]'); const items = [...l.querySelectorAll(':scope > li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the list is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    first = [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0]
    box = lst.locator("xpath=..")
    box.get_by_role("button", name="Next Projects").click()
    q = pager(page)
    check("%s Next pages to the later projects (%s)" % (tag, q), q is not None and q[0] == p[1] + 1, q)
    box.get_by_role("button", name="Previous Projects").click()
    check("%s Previous returns to the first" % tag, pager(page) == p and [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0] == first)
    cut = 0
    while True:                                                             # the long purpose is on a later page: every page's rows must fit
        cut += page.evaluate("() => [...document.querySelectorAll('ul[aria-label=Projects] > li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length")
        nxt = box.get_by_role("button", name="Next Projects")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    check("%s no row on any page has its content cut off by its fixed height (%d)" % (tag, cut), cut == 0, cut)
    while box.get_by_role("button", name="Previous Projects").count() and not box.get_by_role("button", name="Previous Projects").is_disabled():
        box.get_by_role("button", name="Previous Projects").click()


def choose(page, text):
    box = page.get_by_label("Projects", exact=True).locator("xpath=..")
    while page.get_by_label("Projects", exact=True).locator(":scope > li").filter(has_text=text).count() == 0:
        nxt = box.get_by_role("button", name="Next Projects")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    page.get_by_label("Projects", exact=True).locator(":scope > li").filter(has_text=text).first.get_by_role("button").click()


def the_detail(page, tag):
    work = page.get_by_test_id("work")
    choose(page, "tall-notes")
    page.get_by_label("Project tall-notes").wait_for()
    check("%s choosing a project shows its detail in the panel" % tag, work.get_by_label("Project tall-notes").count() == 1)
    shell(page, tag, " with a project chosen:")
    tall = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return [w.scrollHeight > w.clientHeight + 1, getComputedStyle(w).overflowY, w.scrollHeight, w.clientHeight]; }")
    check("%s a detail taller than its panel scrolls inside it (%d of %d px)" % (tag, tall[2], tall[3]), tall[0] and tall[1] in ("auto", "scroll"), tall)
    page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); w.scrollTop = w.scrollHeight; }")
    last = page.evaluate("""() => { const w = document.querySelector('[data-testid=work]').getBoundingClientRect(); const bs = [...document.querySelectorAll('[data-testid=work] button')];
        const r = bs[bs.length - 1].getBoundingClientRect(); return r.bottom <= w.bottom + 1 && r.top >= w.top - 1; }""")
    check("%s scrolled to its end, the last action is in view" % tag, last)
    work.get_by_role("button", name="Edit manifest").click()
    work.get_by_label("Manifest").wait_for()
    shell(page, tag, " with the manifest editor open:")
    work.get_by_role("button", name="Cancel").click()
    page.get_by_label("New project name").fill("made-%d" % len(results))
    page.get_by_role("button", name="Create project").click()
    page.get_by_label("Project made-%d" % (len(results) - 1)).or_(page.locator("[aria-label^='Project made-']")).first.wait_for()
    check("%s a project made at the foot of the list is chosen" % tag, work.locator("[aria-label^='Project made-']").count() == 1)
    shell(page, tag, " after making a project:")


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the list flows, with no pager" % tag, page.get_by_test_id("pager").count() == 0 and page.get_by_label("Projects", exact=True).locator("> li").count() >= PLAIN + 2)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        page.get_by_role("navigation", name="Pages").get_by_role("button", name="Projects", exact=True).or_(page.get_by_role("navigation", name="Pages").get_by_role("button", name="More", exact=True)).first.wait_for()
        go_to(page, "Projects")
        page.get_by_label("Projects", exact=True).wait_for()
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(shots, "projects-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        shell(page, tag)
        the_list(page, tag)
        the_detail(page, tag)
        page.screenshot(path=os.path.join(shots, "projects-%s-%dx%d-detail.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-projects-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = fw.make_bench()
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
