#!/usr/bin/env python3
"""The Help page fills the window; only its topic reader may scroll, and only inside itself (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_help_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench of verify_ppk2_live.py. In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing outside the topic reader scrolls; the page uses the width beside the nav and ends at the foot of the window;
  topics   every topic is in the list, inside it; choosing each shows its article, with the reader back at its top, and the window never scrolls; the topics taller than the reader
           scroll inside it, and scrolled to the end their last button is in view; search narrows the list.
At phone width the page flows and never scrolls sideways. Exit 0 only if every check passed.
"""
import argparse
import os
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
SIZES = ((1280, 720), (1440, 900), (1024, 500), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


# Everything that scrolls in the page except what is inside the reader (it may, inside itself).
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
    check("%s%s nothing outside the reader scrolls" % (tag, what), not inner, inner)
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s%s the page uses the width beside the nav (%d of %d px)" % (tag, what, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s%s the page ends at the foot of the window" % (tag, what), box[5] - 24 <= box[3] <= box[5] + 1, box)
    inside = page.evaluate("() => { const m = document.querySelector('main').getBoundingClientRect(); const w = document.querySelector('[data-testid=work]').getBoundingClientRect(); return w.bottom <= m.bottom + 1 && w.right <= m.right + 1; }")
    check("%s%s the reader lies inside the page" % (tag, what), inside)


def topics_nav(page):
    return page.get_by_role("navigation", name="Help topics")


def the_topics(page, tag):
    nav = topics_nav(page)
    titles = [t.strip() for t in nav.locator("ul button").all_inner_texts()]
    check("%s the list holds the topics (%d)" % (tag, len(titles)), len(titles) >= 8, titles)
    outside = page.evaluate("""() => { const n = document.querySelector('nav[aria-label="Help topics"] ul').getBoundingClientRect();
        return [...document.querySelectorAll('nav[aria-label="Help topics"] ul button')].filter((b) => { const r = b.getBoundingClientRect(); return r.bottom > n.bottom + 1 || r.top < n.top - 1; }).length; }""")
    check("%s every topic button lies inside the list" % tag, outside == 0, outside)
    work = page.get_by_test_id("work")
    scrolled = 0
    for title in titles:
        nav.locator("ul button", has_text=title).first.click()
        art = page.get_by_role("article", name=title, exact=True)
        art.wait_for()
        tops = page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); return [w.scrollTop, w.scrollHeight > w.clientHeight + 1, getComputedStyle(w).overflowY]; }")
        if not (tops[0] == 0):
            check("%s choosing %s starts the reader at its top" % (tag, title), False, tops)
        doc = page.evaluate("() => document.scrollingElement.scrollHeight - document.scrollingElement.clientHeight")
        if doc > 1:
            check("%s the window scrolls with %s open (%d px)" % (tag, title, doc), False, doc)
        if tops[1]:
            scrolled += 1
            if tops[2] not in ("auto", "scroll"):
                check("%s %s is taller than the reader and the reader does not scroll" % (tag, title), False, tops)
        page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); w.scrollTop = w.scrollHeight; }")       # read to the end, so the next topic shows whether it starts at its top again
    check("%s every topic opens with the reader at its top and the window still" % tag, not [r for r in results if not r[1] and r[0].startswith(tag) and ("starts the reader" in r[0] or "window scrolls" in r[0])])
    check("%s some topics are taller than the reader, so it scrolls inside itself (%d of %d)" % (tag, scrolled, len(titles)), scrolled >= 1, scrolled)
    # the longest one scrolled to its end: the last button is in view
    nav.locator("ul button").first.click()
    page.evaluate("() => { const w = document.querySelector('[data-testid=work]'); w.scrollTop = w.scrollHeight; }")
    last = page.evaluate("""() => { const w = document.querySelector('[data-testid=work]').getBoundingClientRect();
        const bs = [...document.querySelectorAll('[data-testid=work] article button')]; if (!bs.length) return true; const r = bs[bs.length - 1].getBoundingClientRect();
        return r.bottom <= w.bottom + 1 && r.top >= w.top - 1; }""")
    check("%s scrolled to its end, the reader's last button is in view" % tag, last)
    page.get_by_label("Search help").fill("wiring")
    narrowed = nav.locator("ul button").count()
    check("%s search narrows the list (%d of %d)" % (tag, narrowed, len(titles)), 1 <= narrowed < len(titles), narrowed)
    page.get_by_label("Search help").fill("")
    check("%s clearing the search brings every topic back" % tag, nav.locator("ul button").count() == len(titles))
    work.hover()


def a_short_window(page, tag):
    """A window too short for the whole list of topics: the document still does not scroll and no topic is lost, because the list scrolls inside itself."""
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s the document does not scroll (%d of %d px)" % (tag, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    lst = page.evaluate("""() => { const u = document.querySelector('nav[aria-label="Help topics"] ul'); return [u.scrollHeight > u.clientHeight + 1, getComputedStyle(u).overflowY, u.scrollHeight, u.clientHeight]; }""")
    check("%s the topic list scrolls inside itself (%d of %d px)" % (tag, lst[2], lst[3]), lst[0] and lst[1] in ("auto", "scroll"), lst)
    page.evaluate("() => { const u = document.querySelector('nav[aria-label=\"Help topics\"] ul'); u.scrollTop = u.scrollHeight; }")
    last = page.evaluate("""() => { const u = document.querySelector('nav[aria-label="Help topics"] ul').getBoundingClientRect();
        const bs = [...document.querySelectorAll('nav[aria-label="Help topics"] ul button')]; const r = bs[bs.length - 1].getBoundingClientRect(); return r.bottom <= u.bottom + 1 && r.top >= u.top - 1; }""")
    check("%s scrolled to the end, the last topic is in view" % tag, last)


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the topic list is the screen, with no reader beside it" % tag, topics_nav(page).locator("ul button").count() >= 8 and page.get_by_role("article").count() == 0 or not page.get_by_role("article").first.is_visible())


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Help")
        topics_nav(page).wait_for()
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(shots, "help-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        if height < 580:
            a_short_window(page, tag)
            return
        shell(page, tag)
        the_topics(page, tag)
        shell(page, tag, " after reading every topic:")
        page.screenshot(path=os.path.join(shots, "help-%s-%dx%d-read.png" % (theme, width, height)))
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-help-layout-")
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
