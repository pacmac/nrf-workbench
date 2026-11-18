#!/usr/bin/env python3
"""Drive the redesigned dashboard in a real browser (docs/specs/dashboard-redesign.md).

    python3 scripts/verify_redesign.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI, real broker) over a FAKE sysfs, /dev and /proc holding a named nRF board, a board the bench
knows and cannot see, an ESP32 declared and one not, a free USB adapter, the Pi's UART wired to the RAK, an I2C bus and claimed pins.
For light and dark at phone and desktop width, on every page: the nav never wraps, the page never scrolls sideways, Home answers in a
sentence and every row on it is a link, Devices says connected versus not attached by shape and by tab, Commands searches and offers the
bench's own values, Help is a list of short topics with a ? on each page, and no page prints an error. Exit 0 only if every check passed.
"""
import argparse
import json
import os
import re
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from fakes import SERIAL, Tree  # noqa: E402

SIZES = (("phone", 390, 844), ("desktop", 1280, 900))
THEMES = ("light", "dark")
GONE = "GONEBOARD0009"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class Bench(object):
    def __init__(self):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-redesign-")
        t = self.tree
        t.app_board()
        t.node("ttyACM0", 166, 0)
        t.node("ttyAMA0", 204, 64)
        t.usb("1-2", ("0403", "6001"), "M5stack", serial="5D52ADF916", manufacturer="FTDI", tty="ttyUSB0", majmin="188:0")
        t.node("ttyUSB0", 188, 0)
        t.usb("1-4", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8:33:85:4A:A4", manufacturer="Espressif", tty="ttyACM2", majmin="166:2")
        t.node("ttyACM2", 166, 2)
        t.usb("1-5", ("1a86", "55d4"), "USB Single Serial", serial="5B1F007437", manufacturer="wch", tty="ttyACM1", majmin="166:1")
        t.node("ttyACM1", 166, 1)
        os.makedirs(os.path.join(t.proc, "device-tree"))
        with open(os.path.join(t.proc, "device-tree", "model"), "wb") as fh:
            fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")
        with open(os.path.join(t.proc, "uptime"), "w") as fh:
            fh.write("12345.67 99999.0\n")
        open(os.path.join(t.dev, "i2c-1"), "w").close()
        self.ptys = {}
        for name in ("rak-uart0", "timercam"):
            master, slave = os.openpty()
            self.ptys[name] = (master, slave, os.ttyname(slave))
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console"),
                      "gpio": os.path.join(self.tmp, "gpio"), "firmware": os.path.join(self.tmp, "fw")},
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1},
                        "console": {"retain": 500, "max_age_days": 1}}})
        assert problems == [], problems
        self.uev_a, self.uev_b = socket.socketpair()
        hooks = {"tool": "/fake/uploader", "esp_tool": "/fake/esptool", "open_uevents": lambda: self.uev_a,
                 "console_resolve": lambda link: ({"device": {}, "node": self.ptys[link.name][2]}, "") if link.name in self.ptys else (None, "not attached")}
        self.app, self.host, _r, _c = service.build(cfg, sysfs=t.sysfs, devroot=t.dev, proc=t.proc, hooks=hooks)
        self.host.console.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True).start()
        self.setup()

    def api(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            return json.load(exc)

    def setup(self):
        ok = lambda r: r.get("ok") or sys.exit("setup failed: %s" % r)  # noqa: E731
        ok(self.api("api/devices/name", {"serial": SERIAL, "name": "left-rak"}))
        ok(self.api("api/wiring/connect", {"device": SERIAL, "intent": "log", "adapter": "uart:ttyAMA0", "baud": 115200, "send": False, "name": "rak-uart0"}))
        ok(self.api("api/esp32/declare", {"name": "timercam", "serial": "5D52ADF916"}))
        for g, n in ((2, "i2c-sda"), (3, "i2c-scl")):
            ok(self.api("api/gpio/assign", {"name": n, "gpio": g, "direction": "input", "service": "i2c", "action": "bus", "purpose": "Pi I2C bus 1",
                                            "active_low": False, "voltage_v": 3.3, "voltage_verified": False, "pull": "up"}))
        wiring = os.path.join(self.tmp, "wiring", "device-names.json")
        with open(wiring) as fh:
            names = json.load(fh)
        names["names"][GONE] = "spare-rak"                                    # a board the bench knows and cannot see
        with open(wiring, "w") as fh:
            json.dump(names, fh)
        os.write(self.ptys["rak-uart0"][0], b"boots=13 prevRst=0x0\nsite: HOME (configured) role=bench\n")
        self.api("api/devices/list", {}) if False else None

    def close(self):
        self.server.should_exit = True
        self.host.console.stop()
        for s in (self.uev_a, self.uev_b):
            s.close()
        for m, s, _n in self.ptys.values():
            os.close(m)
            os.close(s)
        self.tree.close()


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def nav_one_row(page):
    tops = page.evaluate("[...document.querySelectorAll('nav[aria-label=Pages] button')].map(b => b.offsetTop)")
    return len(set(tops)) == 1, tops


def run(browser, bench, theme, size, shots):
    label, width, height = size
    tag = "%s-%s" % (theme, label)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)
    nav = page.get_by_role("navigation", name="Pages")

    def shot(name):
        y = page.evaluate("window.scrollY")
        page.screenshot(path=os.path.join(shots, "%s-%s.png" % (tag, name)), full_page=True)
        page.evaluate("(y) => { window.scrollTo(0, y); return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }", y)

    def open_page(name):
        nav.get_by_role("button", name=name, exact=True).click()
        page.wait_for_timeout(250)

    try:
        page.goto(bench.url)
        nav.wait_for()
        # ---- the shell
        one, tops = nav_one_row(page)
        check("%s the nav is one row and never wraps" % tag, one, tops)
        check("%s the nav has every page, daily ones first" % tag,
              page.evaluate("[...document.querySelectorAll('nav[aria-label=Pages] button')].map(b => b.textContent)") ==
              ["Home", "Devices", "Wiring", "Console", "Firmware", "GPIO", "Activity", "Commands", "Help"])
        # ---- Home
        status = page.get_by_role("status").filter(has_text="devices connected")
        status.first.wait_for()
        text = status.first.inner_text()
        check("%s Home says how the bench is in one sentence" % tag, "of 5 devices connected" in text and "1 of 1 link listening" in text and "I2C ready" in text, text)
        devs = page.get_by_role("list", name="Devices").first
        check("%s every kind of device is a row, connected ones before absent ones" % tag,
              devs.locator("li").count() == 5 and "spare-rak" in devs.locator("li").last.inner_text(), devs.inner_text())
        lamps = devs.locator("svg[role=img]").evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
        check("%s state is a labelled shape on every row (connected, needs attention, not attached)" % tag,
              {"connected", "not attached", "needs attention"} <= set(lamps) and lamps[-1] == "not attached", lamps)
        check("%s the Pi header is 40 pins, each a link" % tag, page.get_by_role("group", name=re.compile("Pi header")).get_by_role("button").count() == 40)
        check("%s Home carries no explanatory paragraphs" % tag, page.locator("main p, #root p").evaluate_all("els => els.filter(e => e.textContent.length > 110).length") == 0)
        check("%s no horizontal scroll on Home" % tag, overflow(page) <= 0, overflow(page))
        shot("1-home")
        page.get_by_role("button", name="Connections: open the page").click()
        page.get_by_role("heading", name="Wiring").wait_for()
        check("%s a Home heading opens its page" % tag, nav.get_by_role("button", name="Wiring").get_attribute("aria-current") == "page")
        open_page("Home")
        page.get_by_role("list", name="Devices").first.get_by_role("button", name=re.compile("left-rak")).click()
        check("%s a Home nRF row opens Devices" % tag, nav.get_by_role("button", name="Devices").get_attribute("aria-current") == "page")
        open_page("Home")
        page.get_by_role("list", name="Devices").first.get_by_role("button", name=re.compile("nobody has declared")).click()
        check("%s a Home ESP32 row opens Firmware, where it is declared" % tag, nav.get_by_role("button", name="Firmware").get_attribute("aria-current") == "page")
        open_page("Devices")
        # ---- Devices
        tabs = page.get_by_role("tablist", name="Show devices")
        check("%s Devices has Connected, Not attached and All with counts" % tag,
              re.search(r"Connected\s*4", tabs.inner_text()) and re.search(r"Not attached\s*1", tabs.inner_text()) and re.search(r"All\s*5", tabs.inner_text()), tabs.inner_text())
        shot("2-devices")
        tabs.get_by_role("tab", name=re.compile("^Not attached")).click()
        check("%s the Not attached tab lists the board the bench knows and cannot see" % tag, "spare-rak" in page.get_by_role("list", name="Devices").inner_text())
        tabs.get_by_role("tab", name=re.compile("^Connected")).click()
        page.get_by_role("list", name="Devices").get_by_role("button", name=re.compile("left-rak")).click()
        page.get_by_role("button", name="Rename %s" % SERIAL).click()
        field = page.get_by_label("Name for %s" % SERIAL)
        field.fill("Right-RAK")
        check("%s a board is renamed in place, lower-cased as typed" % tag, field.input_value() == "right-rak")
        page.get_by_role("button", name="Save name").click()
        page.get_by_text("right-rak").first.wait_for()
        shot("3-device-open")
        page.get_by_role("button", name="Rename %s" % SERIAL).click()
        page.get_by_label("Name for %s" % SERIAL).fill("left-rak")
        page.get_by_role("button", name="Save name").click()
        # ---- Wiring, Console, Firmware, GPIO, Activity
        for name, key in (("Wiring", "4-wiring"), ("Console", "5-console"), ("Firmware", "6-firmware"), ("GPIO", "7-gpio"), ("Activity", "8-activity")):
            open_page(name)
            page.wait_for_timeout(300)
            check("%s %s has no horizontal scroll" % (tag, name), overflow(page) <= 0, overflow(page))
            shot(key)
        # ---- Commands
        open_page("Commands")
        search = page.get_by_label("Search commands")
        search.fill("i2c")
        listing = page.get_by_role("group", name="Commands") if False else page.locator("[aria-label=Commands]")
        names = listing.locator("button").all_inner_texts()
        check("%s Commands search narrows the list to what matches" % tag, 0 < len(names) <= 6 and all(any(w in n for w in ("scan", "transfer", "buses", "overview")) for n in names)
              and sum(1 for n in names if "scan" in n or "transfer" in n or "buses" in n) == 3, names)
        search.fill("")
        search.fill("devices")
        listing.get_by_role("button", name=re.compile(r"^name")).click()
        page.get_by_role("heading", name="devices.name").wait_for()
        picks = page.get_by_role("group", name="Choose serial").get_by_role("button")
        check("%s a serial is offered as a pick from the boards the bench has, not typed blind" % tag, picks.count() == 1 and picks.first.get_attribute("title") == SERIAL, picks.all_inner_texts())
        picks.first.click()
        check("%s one press on a pick fills the box" % tag, page.get_by_label(re.compile("^serial")).input_value() == SERIAL)
        shot("9-commands")
        search.fill("")
        listing.get_by_role("button", name=re.compile(r"^buses")).click()
        page.get_by_role("button", name="Read", exact=True).click()
        page.get_by_text("i2c_enabled").first.wait_for()
        check("%s a command runs from the page and the whole result shows" % tag, True)
        # ---- Help
        open_page("Help")
        topics = page.get_by_role("navigation", name="Help topics")
        check("%s Help is a list of short topics" % tag, topics.locator("button").count() == 9, topics.locator("button").count())
        topics.get_by_role("button", name=re.compile("^Devices")).click()
        art = page.get_by_role("article", name="Devices")
        art.wait_for()
        check("%s a topic has what you see, do this and if it does not work" % tag, all(h in art.inner_text() for h in ("What you see", "Do this", "If it does not work")))
        if label == "phone":
            check("%s on a phone the list gives way to the topic and Back returns" % tag, not topics.is_visible())
            page.get_by_role("button", name="Back to topics").click()
            check("%s Back shows the topics again" % tag, topics.is_visible())
            topics.get_by_role("button", name=re.compile("^Devices")).click()
        shot("10-help")
        open_page("Devices")
        page.get_by_role("button", name="Help: Devices").click()
        page.get_by_role("article", name="Devices").wait_for()
        check("%s the ? on a page opens that page's topic" % tag, True)
        open_page("Home")
        page.get_by_role("button", name="Help: Home").click()
        page.get_by_role("article", name="Start here").wait_for()
        one, tops = nav_one_row(page)
        check("%s the nav is still one row after moving about" % tag, one, tops)
        check("%s no page or console errors" % tag, not errors, errors)
    finally:
        context.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-redesign-shots-")
    os.makedirs(shots, exist_ok=True)
    bench = Bench()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
            try:
                for theme in THEMES:
                    for size in SIZES:
                        run(browser, bench, theme, size, shots)
            finally:
                browser.close()
    finally:
        bench.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
