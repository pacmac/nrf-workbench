#!/usr/bin/env python3
"""The Console page fills the window and never scrolls (docs/specs/navigation.md, "Full-height pages").

    python3 scripts/verify_console_layout.py [--shots DIR] [--chrome PATH]

A real service over two pseudo-terminal links (`dbg` can send, `rxonly` cannot) with 150 lines already logged, one of them 300 characters long. In light and dark at 1280x720 and 1440x900:
  shell    the document does not scroll; nothing inside the page scrolls; the page uses the width beside the nav and ends at the foot of the window;
  log      the lines that fit are shown, the newest last, each inside the log, the log filled; a pager says which of how many; Older pages back and the page stays put when a line arrives;
           Latest follows again; the whole of a long line is shown when it is picked;
  input    a typed line is sent, the device hears it and the log shows it; a link that cannot send says why and the page still fits;
  drawers  Macros opens over the page and Escape closes it.
At phone width the page flows and never scrolls sideways. Exit 0 only if every check passed.
"""
import argparse
import os
import re
import select
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

import uvicorn  # noqa: E402
import yaml  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from fakes import Tree  # noqa: E402

THEMES = ("light", "dark")
SIZES = ((1280, 720), (1440, 900), (390, 844))
NAV_W = 256                      # the chrome beside the page: the sidebar 208, 16 left padding, 16 gap and 16 right padding
LINES = 150
LONG = "LONG " + "0123456789" * 30                        # 305 characters: the SLEEPNOW line of a real board is of this kind
CEILING_S = 10                   # only ever reached by a failing check
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class Bench(object):
    def __init__(self):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-console-layout-")
        self.ptys = {}
        raws = []
        for name, serial, can_send in (("dbg", "S1", True), ("rxonly", "S2", False)):
            master, slave = os.openpty()
            raw = {"name": name, "backend": "usb-adapter", "adapter": {"ids": ["1a86:55d4"], "serial": serial}, "baud": 115200, "dut_tx_to_host": True,
                   "host_tx_to_dut": can_send, "voltage": {"dut": 3.3, "adapter": 3.3, "verified": True}, "dtr_rts_reach_dut": False, "reply_marker": "< "}
            self.ptys[name] = {"master": master, "slave": slave, "node": os.ttyname(slave)}
            raws.append(raw)
        links_file = os.path.join(self.tmp, "links.yaml")
        with open(links_file, "w") as fh:
            yaml.safe_dump({"links": raws}, fh)
        self.uevent_a, self.uevent_b = socket.socketpair()
        hooks = {"open_uevents": lambda: self.uevent_a,
                 "console_resolve": lambda link: ({"device": {}, "node": self.ptys[link.name]["node"]}, "")}
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"links": links_file, "console": os.path.join(self.tmp, "console")},
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 200, "max_age_days": 1}, "console": {"retain": 1000, "max_age_days": 1}}})
        assert problems == [], problems
        self.app, self.host, _r, _c = service.build(cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, proc=self.tree.proc, hooks=hooks)
        self.host.console.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.thread.start()

    def says(self, name, text):
        self.newest = text                                # the newest line anything put in the log: a later run starts after the earlier runs' lines
        os.write(self.ptys[name]["master"], (text + "\n").encode())

    def wait_lines(self, name, n):
        deadline = time.monotonic() + CEILING_S
        while self.host.console.state(name)["lines"] < n:
            if time.monotonic() > deadline:
                raise AssertionError("the broker never read %d lines of %s" % (n, name))
            time.sleep(0.02)

    def heard(self, name, n):
        got = b""
        while len(got) < n:
            ready, _, _ = select.select([self.ptys[name]["master"]], [], [], CEILING_S)
            if not ready:
                break
            got += os.read(self.ptys[name]["master"], 4096)
        return got

    def seed(self):
        for i in range(LINES):
            self.says("dbg", LONG if i == LINES - 2 else "line %03d boot ok" % i)
        self.wait_lines("dbg", LINES)

    def close(self):
        self.server.should_exit = True
        self.thread.join()
        self.host.console.stop()
        for s in (self.uevent_a, self.uevent_b):
            s.close()
        for pty in self.ptys.values():
            for fd in (pty["master"], pty["slave"]):
                os.close(fd)
        self.tree.close()


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


def log(page):
    return page.get_by_label("Console log", exact=True)


def pager(page):
    el = page.get_by_test_id("pager")
    if not el.count():
        return None
    m = re.match(r"^(\d+) to (\d+) of (\d+)$", el.inner_text().strip())
    return tuple(int(x) for x in m.groups()) if m else None


def texts(page):
    return [t.strip() for t in log(page).locator("li").all_inner_texts()]


def the_log(page, bench, tag):
    log(page).wait_for(timeout=5000)
    page.wait_for_function("document.querySelectorAll('[aria-label=\"Console log\"] li').length > 3")
    rows = texts(page)
    n = len(rows)
    p = pager(page)
    check("%s rows are shown, fewer than all %d (%d)" % (tag, LINES, n), 1 <= n < LINES, n)
    outside = page.evaluate("""() => { const l = document.querySelector('[aria-label="Console log"]').getBoundingClientRect();
        return [...document.querySelectorAll('[aria-label="Console log"] li')].filter((li) => { const r = li.getBoundingClientRect(); return r.bottom > l.bottom + 1 || r.top < l.top - 1; }).length; }""")
    check("%s every row lies inside the log" % tag, outside == 0, outside)
    free = page.evaluate("""() => { const l = document.querySelector('[aria-label="Console log"]'); const items = [...l.querySelectorAll('li')];
        return [l.getBoundingClientRect().bottom - items[items.length - 1].getBoundingClientRect().bottom, items[0].getBoundingClientRect().height]; }""")
    check("%s the log is filled: less than one more row of room is left (%d of %d px)" % (tag, free[0], free[1]), free[0] < free[1], free)
    check("%s the newest line is the last row" % tag, rows[-1].endswith(bench.newest), (rows[-1], bench.newest))
    check("%s the pager says which of how many and ends at the newest (%s)" % (tag, p), p is not None and p[1] == p[2] >= LINES and p[2] - p[0] + 1 == n, p)
    check("%s it says it is following" % tag, page.get_by_test_id("follow").inner_text().strip() == "Following")

    page.get_by_role("button", name="Older lines").click()
    q = pager(page)
    check("%s Older pages back by one screen (%s)" % (tag, q), q is not None and q[1] == p[1] - n and q[2] == p[2], q)
    check("%s it no longer says it is following" % tag, page.get_by_test_id("follow").inner_text().strip() != "Following")
    before = texts(page)
    bench.says("dbg", "line new while paged back")
    page.wait_for_function("(t) => /of %d$/.test(document.querySelector('[data-testid=pager]').textContent)" % (p[2] + 1))
    check("%s a page back stays put when a line arrives" % tag, texts(page) == before)
    page.get_by_role("button", name="Latest").click()
    check("%s Latest follows again and shows the new line last" % tag, texts(page)[-1].endswith("line new while paged back") and page.get_by_test_id("follow").inner_text().strip() == "Following", texts(page)[-1])


def the_detail(page, tag):
    rows = log(page).locator("li")
    row = rows.filter(has_text="LONG ")
    check("%s the long line is a row, cut to one line" % tag, row.count() == 1, row.count())
    row.first.click()
    full = page.get_by_test_id("line-detail").inner_text()
    check("%s picking it shows the whole of it (%d characters)" % (tag, len(full)), LONG in full, len(full))


def the_input(page, bench, tag):
    """Send `ping`. The bench's own `newest` follows: the sent line is the newest the log holds."""
    field = page.get_by_label("Command to send")
    check("%s the input is on screen" % tag, field.is_visible() and field.is_enabled())
    field.fill("ping")
    field.press("Enter")
    heard = bench.heard("dbg", 5)
    check("%s the device heard the line (%r)" % (tag, heard), heard.startswith(b"ping"), heard)
    page.wait_for_function("() => [...document.querySelectorAll('[aria-label=\"Console log\"] li')].some((li) => li.textContent.includes('> ping'))")
    check("%s the sent line is in the log" % tag, True)
    bench.newest = "> ping"
    shell(page, tag, " after a send:")


def the_drawer(page, tag):
    page.get_by_role("button", name="Macros", exact=True).click()
    page.get_by_role("dialog", name="Macros").wait_for()
    check("%s Macros opens over the page" % tag, True)
    page.keyboard.press("Escape")
    page.get_by_role("dialog", name="Macros").wait_for(state="detached")
    check("%s Escape closes it" % tag, True)
    shell(page, tag, " with the drawer closed:")


def the_receive_only_link(page, tag):
    page.get_by_label("Link", exact=True).select_option("rxonly")
    field = page.get_by_label("Command to send")
    page.wait_for_function("() => document.querySelector('[aria-label=\"Command to send\"]').disabled")
    check("%s a receive-only link disables the input" % tag, field.is_disabled())
    check("%s and says why" % tag, page.locator("form [role=note]").count() >= 1)
    shell(page, tag, " on a receive-only link:")
    page.get_by_label("Link", exact=True).select_option("dbg")


def the_phone(page, tag):
    wide = page.evaluate("() => [document.scrollingElement.scrollWidth, document.scrollingElement.clientWidth]")
    check("%s no horizontal scroll (%d of %d px)" % (tag, wide[0], wide[1]), wide[0] <= wide[1], wide)
    check("%s the log and the input are there" % tag, page.get_by_role("log", name="Console log").count() == 1 and page.get_by_label("Command to send").count() == 1)
    check("%s there is no pager on a phone" % tag, page.get_by_test_id("pager").count() == 0)


def run(page, bench, theme, width, height, shots):
    tag = "%s %dx%d" % (theme, width, height)
    try:
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({"width": width, "height": height})
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Console")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(shots, "console-%s-%dx%d.png" % (theme, width, height)))
        if width < 768:
            the_phone(page, tag)
            return
        shell(page, tag)
        the_log(page, bench, tag)
        the_detail(page, tag)
        the_input(page, bench, tag)
        the_drawer(page, tag)
        the_receive_only_link(page, tag)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-console-layout-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        bench = Bench()
        try:
            bench.seed()
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
