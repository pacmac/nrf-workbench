#!/usr/bin/env python3
"""The Firmware page fills the window and never scrolls (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_firmware_layout.py [--shots DIR] [--chrome PATH]

A real service over the fake PPK2 bench of verify_ppk2_live.py with a package store, 14 projects and 14 packages in one of them (seeded through the page's own session, as a person would).
In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing inside the page scrolls; the page uses the width beside the nav and ends at the foot of the window;
  lists    Projects and the packages of the chosen project each show the rows that fit, inside their list, filled, with a pager that pages; a package row's content fits its fixed height;
  drawers  the ESP32 boards open in a drawer and Escape closes it; creating a project leaves a one-line result at the foot and Details opens the whole result in a drawer.
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
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))

from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
import verify_ppk2_live as base  # noqa: E402
from test_firmware_store import pkg  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
PROJECTS = 14                    # more than the Projects list has room for
PACKAGES = 14                    # in the project `alpha`: more than its list has room for
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def make_bench():
    """The PPK2 bench with a package store switched on (a folder for it)."""
    from nrf_workbench.core import config as cfgmod
    tmp = tempfile.mkdtemp(prefix="nrfwb-firmware-layout-")
    real = cfgmod.load

    def load(env=None, overrides=None, **kw):
        overrides = dict(overrides or {})
        overrides["paths"] = dict(overrides.get("paths", {}), firmware=os.path.join(tmp, "fw"))
        return real(env=env, overrides=overrides, **kw)
    cfgmod.load = load
    try:
        return base.Bench(with_meter=False)
    finally:
        cfgmod.load = real


def seed(context, bench):
    """Projects and packages made through the service as the page makes them: the session's own cookie and CSRF token, the paths the service lists."""
    root = bench.url.rstrip("/")
    paths = dict((c["name"], c["path"]) for c in context.request.get(root + "/api/commands").json()["commands"])
    csrf = context.request.get(root + "/api/session").json().get("csrf")         # an open bench has none; a bench with a login sends it on every write

    def headers(kind):
        return dict({"Content-Type": kind}, **({"X-CSRF-Token": csrf} if csrf else {}))
    for name in ["p%02d" % i for i in range(PROJECTS - 1)] + ["alpha"]:
        r = context.request.post(root + paths["firmware.project_new"], data=json.dumps({"project": name}), headers=headers("application/json"))
        assert r.ok, (name, r.status, r.text())
    for i in range(PACKAGES):
        name = "package-%02d" % i if i else "x" * 150                    # one name that takes three lines if allowed to wrap: it must be cut to its line, not grow the row
        r = context.request.post(root + paths["firmware.put"], params={"project": "alpha", "name": name}, data=pkg(seed=i + 1), headers=headers("application/octet-stream"))
        assert r.ok, (i, r.status, r.text())


SCROLLERS = """() => {
  const main = document.querySelector('main');
  const out = [];
  for (const el of main.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1 && el.tagName !== 'TEXTAREA') out.push(el.tagName + '.' + String(el.className).slice(0, 60));
  }
  return out;
}"""


def shell(page, tag, what=""):
    doc = page.evaluate("() => [document.scrollingElement.scrollHeight, document.scrollingElement.clientHeight]")
    check("%s%s the document does not scroll (%d of %d px)" % (tag, what, doc[0], doc[1]), doc[0] <= doc[1] + 1, doc)
    inner = page.evaluate(SCROLLERS)
    check("%s%s nothing inside the page scrolls" % (tag, what), not inner, inner)
    box = page.evaluate("() => { const r = document.querySelector('main').getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom, innerWidth, innerHeight]; }")
    check("%s%s the page uses the width beside the nav (%d of %d px)" % (tag, what, box[1] - box[0], box[4]), box[1] - box[0] >= box[4] - NAV_W - 2, box)
    check("%s%s the page ends at the foot of the window" % (tag, what), box[5] - 24 <= box[3] <= box[5] + 1, box)
    over = page.evaluate("""() => { const m = document.querySelector('main').getBoundingClientRect();
        return [...document.querySelectorAll('main section')].filter((s) => { const r = s.getBoundingClientRect(); return r.bottom > m.bottom + 1 || r.right > m.right + 1; }).length; }""")
    check("%s%s every panel lies inside the page" % (tag, what), over == 0, over)


def pager_of(page, label):
    el = page.get_by_label(label, exact=True).locator("xpath=..").get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def list_checks(page, tag, label, total):
    lst = page.get_by_label(label, exact=True)
    n = lst.locator(":scope > li").count()
    p = pager_of(page, label)
    check("%s %s shows the rows that fit, fewer than all (%d of %s)" % (tag, label, n, p and p[2]), p is not None and 1 <= n < p[2] and p[2] >= total, (n, p))
    sel = "ul[aria-label=\"%s\"]" % label
    outside = page.evaluate("""(s) => { const l = document.querySelector(s).getBoundingClientRect();
        return [...document.querySelectorAll(s + ' > li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""", sel)
    check("%s every %s row lies inside its list" % (tag, label), outside == 0, outside)
    free = page.evaluate("""(s) => { const l = document.querySelector(s); const items = [...l.querySelectorAll(':scope > li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""", sel)
    check("%s the %s list is filled: less than one more row of room is left (%d of %d px)" % (tag, label, free[0], free[1]), free[0] < free[1], free)
    first = [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0]
    box = lst.locator("xpath=..")
    cut = 0
    cut_js = "(s) => [...document.querySelectorAll(s + ' > li')].filter((li) => li.scrollHeight > li.clientHeight + 1).length"
    box.get_by_role("button", name="Next %s" % label).click()
    q = pager_of(page, label)
    check("%s Next pages %s (%s)" % (tag, label, q), q is not None and q[0] == p[1] + 1, q)
    box.get_by_role("button", name="Previous %s" % label).click()
    check("%s Previous returns to the first" % tag, pager_of(page, label) == p and [t.strip() for t in lst.locator(":scope > li").all_inner_texts()][0] == first)
    while True:                                                             # every page: a long name on a later page must not grow its row either
        cut += page.evaluate(cut_js, sel)
        nxt = box.get_by_role("button", name="Next %s" % label)
        if nxt.count() == 0 or nxt.is_disabled():
            break
        nxt.click()
    check("%s no %s row on any page has its content cut off by its fixed height (%d)" % (tag, label, cut), cut == 0, cut)
    while box.get_by_role("button", name="Previous %s" % label).count() and not box.get_by_role("button", name="Previous %s" % label).is_disabled():
        box.get_by_role("button", name="Previous %s" % label).click()


def the_lists(page, tag):
    page.get_by_label("Projects", exact=True).wait_for()
    list_checks(page, tag, "Projects", PROJECTS)
    page.get_by_label("Projects", exact=True).get_by_role("button", name=re.compile(r"^alpha")).or_(page.get_by_role("button", name=re.compile(r"^alpha\s*\d+"))).first.click()
    page.get_by_role("heading", name="alpha", exact=True).wait_for()
    check("%s choosing a project shows its panel" % tag, page.get_by_label("Package file").count() == 1)
    page.get_by_label("Packages in alpha", exact=True).wait_for()
    list_checks(page, tag, "Packages in alpha", PACKAGES)
    check("%s the upload is one row in the panel" % tag, page.evaluate("""() => { const f = document.querySelector('input[aria-label="Package file"]').getBoundingClientRect(); const n = document.querySelector('input[aria-label="Package name"]').getBoundingClientRect();
        return Math.abs(f.top - n.top) < 40; }"""))
    gaps = page.evaluate("""() => [...document.querySelectorAll('main section')].map((s) => { const kids = [...s.children].filter((c) => c.getBoundingClientRect().height > 0);
        return Math.round(s.getBoundingClientRect().bottom - kids[kids.length - 1].getBoundingClientRect().bottom); })""")
    check("%s each panel's contents fill it down to its foot (%s px spare)" % (tag, gaps), all(g <= 24 for g in gaps), gaps)


def the_drawers(page, tag):
    page.get_by_role("button", name=re.compile(r"^ESP32 boards")).click()
    page.get_by_role("dialog", name="ESP32 boards").wait_for()
    check("%s the ESP32 boards open in a drawer" % tag, True)
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="ESP32 boards").wait_for(state="detached")
    check("%s Escape closes it" % tag, True)
    page.get_by_label("New project name").fill("made-%d" % len(results))                # a name no earlier run used: each run makes one more project
    page.get_by_role("button", name="Create project").click()
    line = page.get_by_test_id("result-line")
    page.get_by_role("button", name="Details").wait_for()                                # the line says "Working…" until the answer is in
    check("%s a one-line result is left at the foot (%s)" % (tag, line.inner_text().strip()[:40]), line.inner_text().strip().lower().startswith("ok"), line.inner_text())
    shell(page, tag, " with a result shown:")
    page.get_by_role("button", name="Details").click()
    page.get_by_role("dialog", name="Last result").wait_for()
    check("%s Details opens the whole result in a drawer" % tag, True)
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="Last result").wait_for(state="detached")


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s there is no pager on a phone" % tag, page.get_by_test_id("pager").count() == 0)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        page.get_by_role("navigation", name="Pages").get_by_role("button", name="Firmware", exact=True).or_(page.get_by_role("navigation", name="Pages").get_by_role("button", name="More", exact=True)).first.wait_for()
        go_to(page, "Firmware")
        page.get_by_label("New project name").wait_for()
        page.wait_for_timeout(700)
        page.screenshot(path=os.path.join(shots, "firmware-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        shell(page, tag)
        the_lists(page, tag)
        shell(page, tag, " with a project open:")
        page.screenshot(path=os.path.join(shots, "firmware-%s-%dx%d-project.png" % (theme, width, height)))
        the_drawers(page, tag)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-firmware-layout-shots-")
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
