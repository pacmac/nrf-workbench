#!/usr/bin/env python3
"""Drive the Pi-UART debug link in a real browser (docs/specs/pi-uart.md).

    python3 scripts/verify_pi_uart_live.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI, real broker) over a FAKE sysfs with one nRF board, a fake /dev with a `ttyAMA0` node, and a
pseudo-terminal standing in for the UART's port. In light and dark: the Pi's own serial port is offered on the Wiring page like an adapter,
the sheet connects the board to it (receive-only), the console has a pi-uart link and reads what the board prints, the header pins it uses
are refused to the pin map, and removing the connection frees them. Exit 0 only if every check passed.
"""
import argparse
import json
import os
import re
import socket
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "wiring"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from fakes import SERIAL, Tree  # noqa: E402
from test_release import FakePinctrl  # noqa: E402

THEMES = ("light", "dark")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class Bench(object):
    def __init__(self):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-piuart-")
        self.tree.app_board()
        self.tree.node("ttyACM0", 166, 0)
        self.tree.node("ttyAMA0", 204, 64)                       # the Pi's UART, as it is once enabled
        os.makedirs(os.path.join(self.tree.proc, "device-tree"))
        with open(os.path.join(self.tree.proc, "device-tree", "model"), "wb") as fh:      # what makes the host a Pi: the pin map exists
            fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")
        self.master, self.slave = os.openpty()
        self.node = os.ttyname(self.slave)
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"wiring": os.path.join(self.tmp, "wiring"), "console": os.path.join(self.tmp, "console"),
                      "gpio": os.path.join(self.tmp, "gpio")},
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1},
                        "console": {"retain": 500, "max_age_days": 1}}})
        assert problems == [], problems
        self.uev_a, self.uev_b = socket.socketpair()
        self.pin_log = []
        self.pins = FakePinctrl(self.pin_log)                    # the Pi's pin registers, kept in memory (tests/wiring/test_release.py)
        hooks = {"tool": "/fake/uploader", "open_uevents": lambda: self.uev_a, "pinctrl": self.pins,
                 "console_resolve": lambda link: ({"device": {}, "node": self.node}, "")}
        self.app, self.host, _r, _c = service.build(cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, proc=self.tree.proc, hooks=hooks)
        self.host.console.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True).start()

    def api(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            return json.load(exc)

    def close(self):
        self.server.should_exit = True
        self.host.console.stop()
        for s in (self.uev_a, self.uev_b):
            s.close()
        for fd in (self.master, self.slave):
            os.close(fd)
        self.tree.close()


def run(browser, theme, shots):
    bench = Bench()
    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)
    try:
        page.goto(bench.url)
        nav = page.get_by_role("navigation", name="Pages")
        nav.wait_for()
        go_to(page, "Wiring")
        box = page.get_by_role("group", name=re.compile("Raspberry Pi serial port.*is available but not connected"))
        box.wait_for()
        check("%s the Pi's own serial port is offered on the Wiring page like an adapter" % theme, "Which device is it wired to?" in box.inner_text(), box.inner_text())
        page.screenshot(path=os.path.join(shots, "%s-1-offered.png" % theme), full_page=True)
        box.get_by_label(re.compile("^Device wired to")).select_option(index=1)
        box.get_by_role("button", name="Set up the connection").click()
        sheet = page.get_by_role("region", name=re.compile("^Connect something to"))
        sheet.wait_for()
        sheet.locator("label", has_text=re.compile(r"^115200$")).click()
        sheet.get_by_role("radio", name=re.compile("^No, only watch")).click()
        page.screenshot(path=os.path.join(shots, "%s-2-sheet.png" % theme), full_page=True)
        sheet.get_by_role("button", name="Connect", exact=True).click()
        wires = page.get_by_role("list", name=re.compile("^Connections of"))
        wires.wait_for()
        wires.get_by_text("listening").wait_for()
        (st,) = bench.host.console.states()
        check("%s the console has a pi-uart link on ttyAMA0, open, receive-only, at 115200" % theme,
              (st["backend"], st["port"], st["state"], st["rx_only"], st["baud"]) == ("pi-uart", "ttyAMA0", "open", True, 115200), st)
        # release and engage the Pi's lines (docs/specs/line-release.md)
        row = wires.get_by_role("listitem").first
        row.get_by_text("Pi lines driven").wait_for()
        check("%s the connection says the Pi's lines are driven and offers to release them" % theme,
              row.get_by_role("button", name=re.compile("^Release the Pi lines")).is_visible())
        page.screenshot(path=os.path.join(shots, "%s-2b-driven.png" % theme), full_page=True)
        row.get_by_role("button", name=re.compile("^Release the Pi lines")).click()
        row.get_by_text("Pi lines released").wait_for()
        (st2,) = bench.host.console.states()
        check("%s release held the link and floated both pins (inputs, no pull), and the page says so" % theme,
              st2["state"] == "held" and bench.pins.pins[14][:2] == ["ip", "pn"] and bench.pins.pins[15][:2] == ["ip", "pn"]
              and row.get_by_role("button", name=re.compile("^Engage the Pi lines")).is_visible(), (st2["state"], bench.pins.pins))
        page.screenshot(path=os.path.join(shots, "%s-2c-released.png" % theme), full_page=True)
        go_to(page, "Home")
        conn = page.get_by_role("list", name="Connections")
        conn.get_by_text("released").first.wait_for()
        check("%s Home shows the released link as needing attention and says how to fix it" % theme,
              "engage them to read again" in conn.inner_text() and "needs attention" in conn.inner_text(), conn.inner_text())
        go_to(page, "Wiring")
        wires.get_by_role("button", name=re.compile("^Engage the Pi lines")).click()
        wires.get_by_text("Pi lines driven").wait_for()
        (st3,) = bench.host.console.states()
        check("%s engage gave the pins back (a0, the Pi's pulls) and the link reads again" % theme,
              st3["state"] == "open" and bench.pins.pins[14][:2] == ["a0", "pn"] and bench.pins.pins[15][:2] == ["a0", "pu"], (st3["state"], bench.pins.pins))
        os.write(bench.master, b"HB up=12345 heap=88000\n")
        go_to(page, "Console")
        page.get_by_text("HB up=12345 heap=88000").wait_for()
        check("%s what the board prints on the Pi's UART shows on the Console page" % theme, True)
        page.screenshot(path=os.path.join(shots, "%s-3-console.png" % theme), full_page=True)
        assign = {"name": "x", "gpio": 14, "direction": "input", "service": "power", "action": "x", "purpose": "p", "active_low": False,
                  "voltage_v": 3.3, "voltage_verified": False, "pull": "none"}
        res = bench.api("api/gpio/assign", assign)
        check("%s the pin map refuses header pin 8 (BCM14) while the connection holds it, and says which" % theme,
              res.get("code") == "pin_taken" and res["evidence"]["held_by"], res)
        res = bench.api("api/wiring/disconnect", {"name": st["name"]})
        check("%s removing the connection takes the link away and frees the pin" % theme,
              res.get("ok") and bench.host.console.states() == [] and bench.api("api/gpio/assign", assign).get("ok"), res)
        check("%s no page or console errors" % theme, not errors, errors)
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-piuart-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        try:
            for theme in THEMES:
                run(browser, theme, shots)
        finally:
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
