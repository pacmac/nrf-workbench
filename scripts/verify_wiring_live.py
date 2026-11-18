#!/usr/bin/env python3
"""Drive the LIVE Wiring page in a real browser (docs/specs/wiring.md, "The page is live where the commands are").

    python3 scripts/verify_wiring_live.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI, real broker) on a loopback port over a FAKE sysfs with one board and one USB
serial adapter, and a pseudo-terminal as the adapter's port. For light and dark at phone and desktop width: the page is live
(no preview banner), a plugged-in adapter asks which device it is wired to, the sheet builds a debug-log connection, the
console sees it at once, a change and a removal reach the console with no restart, an unbuilt intent is listed and
disabled, and an adapter plugged in later appears on "Look again". Exit 0 only if every check passed.
"""
import argparse
import os
import re
import socket
import sys
import tempfile
import threading

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from fakes import SERIAL, Tree  # noqa: E402

ADAPTER = "5B1F007437"
SECOND = "8C2D1A9907"
SIZES = (("phone", 390, 844), ("desktop", 1280, 800))
THEMES = ("light", "dark")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class Bench(object):
    def __init__(self):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-wiring-")
        self.tree.app_board()
        self.tree.node("ttyACM0", 166, 0)
        self.add_adapter("1-4", ADAPTER, "ttyACM1", "166:1", 1)
        self.master, self.slave = os.openpty()
        self.node = os.ttyname(self.slave)
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console")},
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1},
                        "console": {"retain": 500, "max_age_days": 1}}})
        assert problems == [], problems
        self.uev_a, self.uev_b = socket.socketpair()
        hooks = {"tool": "/fake/uploader", "open_uevents": lambda: self.uev_a,
                 "console_resolve": lambda link: ({"device": {}, "node": self.node}, "")}
        self.app, self.host, _r, _c = service.build(cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, hooks=hooks)
        self.host.console.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True).start()

    def add_adapter(self, name, serial, tty, majmin, minor):
        self.tree.usb(name, ("1a86", "55d4"), "USB Single Serial", serial=serial, manufacturer="QinHeng", tty=tty, majmin=majmin)
        self.tree.node(tty, 166, minor)

    def close(self):
        self.server.should_exit = True
        self.host.console.stop()
        for s in (self.uev_a, self.uev_b):
            s.close()
        for fd in (self.master, self.slave):
            os.close(fd)
        self.tree.close()


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def run(browser, bench, theme, size, shots, first):
    label, width, height = size
    tag = "%s-%s" % (theme, label)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)

    def shot(name):
        y = page.evaluate("window.scrollY")
        page.screenshot(path=os.path.join(shots, "%s-%s.png" % (tag, name)), full_page=True)
        page.evaluate("(y) => { window.scrollTo(0, y); return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }", y)

    def links():
        return bench.host.console.states()

    page.goto(bench.url)
    nav = page.get_by_role("navigation", name="Pages")
    nav.wait_for()
    go_to(page, "Wiring")
    page.get_by_role("group", name="Devices").wait_for()
    check("%s live: no sample banner, and it says what is built" % tag,
          page.get_by_role("note").filter(has_text="Preview.").count() == 0 and page.get_by_role("button", name="Help: Wiring").count() == 1
          and "Built so far" not in page.inner_text("body"))
    cards = page.get_by_role("group", name="Devices").get_by_role("button")
    check("%s live: the real board is the one device card" % tag, cards.count() == 1 and SERIAL[:4] in cards.first.inner_text(), cards.all_inner_texts())
    box = page.get_by_role("group", name=re.compile("USB Single Serial is plugged in but not connected"))
    box.wait_for()
    check("%s live: the real adapter is asking which device it is wired to" % tag, ADAPTER[:4] in box.inner_text() and "Which device is it wired to?" in box.inner_text())
    check("%s live: no horizontal scroll" % tag, overflow(page) <= 0, overflow(page))
    shot("1-start")

    # set it up through the sheet
    box.get_by_label(re.compile("^Device wired to")).select_option(index=1)
    box.get_by_role("button", name="Set up the connection").click()
    sheet = page.get_by_role("region", name=re.compile("^Connect something to"))
    sheet.wait_for()
    sheet.locator("label", has_text=re.compile(r"^115200$")).click()
    sheet.get_by_role("radio", name=re.compile("^No, only watch")).click()
    sentence = sheet.get_by_test_id("sentence")
    sentence.wait_for()
    check("%s live: the sentence is the connection" % tag, "at 115200 baud, receive only." in sentence.inner_text(), sentence.inner_text())
    shot("2-sheet")
    sheet.get_by_role("button", name="Connect", exact=True).click()
    wires = page.get_by_role("list", name=re.compile("^Connections of"))
    wires.wait_for()
    wires.get_by_text("listening").wait_for()
    check("%s live: the wire is listed and says it is listening (the service's own state)" % tag, True)
    st = links()
    check("%s live: the console has the link with the device, at once, no restart" % tag,
          len(st) == 1 and st[0]["device"] == SERIAL and st[0]["baud"] == 115200 and st[0]["state"] == "open" and st[0]["rx_only"], st)
    check("%s live: the adapter is no longer asking" % tag, page.get_by_role("group", name=re.compile("plugged in but not connected")).count() == 0)
    shot("3-connected")

    # change it: the sheet opens on the answers, a new baud reaches the console without a restart
    wires.get_by_role("button", name="Edit").click()
    sheet = page.get_by_role("region", name="Change this connection")
    sheet.wait_for()
    check("%s live: editing starts from what is stored" % tag, "115200 baud" in sheet.inner_text() and "Receive only" in sheet.inner_text())
    sheet.get_by_role("button", name="Change: What baud rate does its firmware use?").click()
    sheet.locator("label", has_text=re.compile(r"^9600$")).click()
    sheet.get_by_role("button", name="Save changes").click()
    wires.get_by_text("at 9600 baud").wait_for()
    check("%s live: the change reached the console with no restart" % tag, links()[0]["baud"] == 9600 and links()[0]["state"] == "open", links())

    # something that is not built is listed and cannot be chosen
    page.get_by_role("button", name="Connect something").click()
    sheet = page.get_by_role("region", name=re.compile("^Connect something to"))
    control = sheet.get_by_role("radio", name=re.compile("^Reset, boot or power it"))
    control.wait_for()
    check("%s live: an intent that is not built is listed, disabled, and says so" % tag,
          control.is_disabled() and "Not built yet." in sheet.inner_text() and sheet.get_by_role("radio", name=re.compile("^Read its debug log")).is_enabled())
    shot("4-not-built")
    sheet.get_by_role("button", name="Cancel").click()

    # remove it: the console stops, and the adapter asks again
    wires.get_by_role("button", name="Remove").click()
    page.get_by_role("group", name=re.compile("^Remove this connection")).get_by_role("button", name="Yes").click()
    page.get_by_role("group", name=re.compile("USB Single Serial is plugged in but not connected")).wait_for()
    check("%s live: removing it stops the console and frees the adapter at once" % tag, links() == [])

    # an adapter plugged in later is found on "Look again"
    if first:
        bench.add_adapter("1-5", SECOND, "ttyACM2", "166:2", 2)
        page.get_by_role("button", name="Look again").click()
        page.get_by_role("group", name=re.compile("USB Single Serial is plugged in but not connected")).nth(1).wait_for()
        check("%s live: an adapter plugged in later appears on Look again" % tag,
              page.get_by_role("group", name=re.compile("plugged in but not connected")).count() == 2)
        bench.tree.unplug("1-5")
    shot("5-end")
    check("%s no page or console errors" % tag, not errors, errors)
    context.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-wiring-shots-")
    os.makedirs(shots, exist_ok=True)
    bench = Bench()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
            try:
                first = True
                for theme in THEMES:
                    for size in SIZES:
                        run(browser, bench, theme, size, shots, first)
                        first = False
            finally:
                browser.close()
    finally:
        bench.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
