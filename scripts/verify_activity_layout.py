#!/usr/bin/env python3
"""The Activity page fills the window and never scrolls (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_activity_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake bench of verify_ppk2_live.py, with 40 entries in the activity log (more than any window shows, in four classes). In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing inside the page scrolls; the page uses the width beside the nav and ends at the foot of the window;
  rows     the entries shown are the ones that fit (every row lies inside the list), newest first, with a pager that says which of how many, and that pages;
  filter   a class filter narrows the list and the pager's total with it.
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
from nrf_workbench.core.result import Result  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
TOTAL = 40
CLASS_OF = ("ok", "refused", "failed", "suspect")         # entry i has class CLASS_OF[i % 4]
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def seed(bench):
    """TOTAL entries, oldest first: command seed.00 to seed.39, so the newest is seed.39."""
    make = {"ok": lambda i: Result.success({}, "all good %d" % i),
            "refused": lambda i: Result.refused("lock_held", "the lock is held %d" % i),
            "failed": lambda i: Result.failed("flash_failed", "the flash failed %d" % i),
            "suspect": lambda i: Result("suspect", "flash_suspect", "do not trust it %d" % i)}
    for i in range(TOTAL):
        bench.host.streams.activity.record("seeder", "seed.%02d" % i, {"index": i, "why": "x" * 40}, make[CLASS_OF[i % 4]](i), 0.01 * (i + 1))


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


def pager(page):
    """(first, last, total) from 'N to M of T', or None when there is no pager."""
    el = page.get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def first_command(page):
    return page.get_by_label("Entries", exact=True).locator("li").first.locator("[data-command]").inner_text().strip()


def rows(page, tag):
    lst = page.get_by_label("Entries", exact=True)
    lst.wait_for(timeout=3000)
    n = lst.locator("li").count()
    inside = page.evaluate("""() => { const l = document.querySelector('[aria-label=Entries]').getBoundingClientRect();
        return [...document.querySelectorAll('[aria-label=Entries] li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    p = pager(page)
    total = p[2] if p else n
    check("%s rows are shown, fewer than all %d (%d)" % (tag, total, n), 1 <= n < total and total >= TOTAL, (n, total))
    check("%s every row lies inside the list" % tag, inside == 0, inside)
    cut = page.evaluate("() => [...document.querySelectorAll('[aria-label=Entries] li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length")
    check("%s no row's content is cut off by its fixed height" % tag, cut == 0, cut)
    free = page.evaluate("""() => { const l = document.querySelector('[aria-label=Entries]'); const items = [...l.querySelectorAll('li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the list is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    check("%s the pager says which of how many (%s)" % (tag, p), p == (1, n, total), p)
    # Walk every page, newest first. The dashboard logs its own calls beside the seeded ones, so the seeded entries are checked as a subsequence.
    seen, firsts = [], []
    for _ in range(total):
        cmds = page.get_by_label("Entries", exact=True).locator("li [data-command]").all_inner_texts()
        seen += [c.strip() for c in cmds]
        q = pager(page)
        firsts.append(q[0] if q else 1)
        nxt = page.get_by_role("button", name="Next entries")
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    check("%s paging reaches every entry once (%d of %d)" % (tag, len(seen), total), len(seen) == total, (len(seen), total))
    check("%s each page starts after the one before (%s)" % (tag, firsts[:4]), firsts == [1 + i * n for i in range(len(firsts))], firsts)
    mine = [c for c in seen if c.startswith("seed.")]
    check("%s the seeded entries are newest first (%d)" % (tag, len(mine)), mine == ["seed.%02d" % i for i in range(TOTAL - 1, -1, -1)], mine[:3])
    while page.get_by_role("button", name="Previous entries").count() and not page.get_by_role("button", name="Previous entries").is_disabled():
        page.get_by_role("button", name="Previous entries").click()
    check("%s previous returns to the newest" % tag, pager(page) == (1, n, total), pager(page))


def filtering(page, tag):
    page.get_by_label("Show").select_option("refused")
    p = pager(page)
    want = TOTAL // 4                                                  # the seeded refusals; the dashboard's own calls may add a few
    shown = page.get_by_label("Entries", exact=True).locator("li").count()
    total = p[2] if p else shown
    check("%s the refused filter narrows the list to the refusals (%d of at least %d)" % (tag, total, want), want <= total < TOTAL, (p, shown))
    badges = page.get_by_label("Entries", exact=True).locator("li").all_inner_texts()
    check("%s every row shown is refused" % tag, badges and all("refused" in b for b in badges), badges[:2])
    page.get_by_label("Show").select_option("all")


def phone(page, tag):
    """A phone keeps the flowing list (the page may scroll down, never sideways): every seeded entry is in it and nothing is cut off at the side."""
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    commands = [c.strip() for c in page.locator("main li [data-command]").all_inner_texts()]
    check("%s the flowing list holds every entry (%d)" % (tag, len(commands)), sum(1 for c in commands if c.startswith("seed.")) == TOTAL, len(commands))
    check("%s there is no pager on a phone" % tag, page.get_by_test_id("pager").count() == 0)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Activity")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(shots, "activity-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            phone(page, tag)
            return
        shell(page, tag)
        rows(page, tag)
        filtering(page, tag)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-activity-layout-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = base.Bench(with_meter=False)
        try:
            seed(bench)
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
