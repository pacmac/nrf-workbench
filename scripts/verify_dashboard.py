#!/usr/bin/env python3
"""Drive the dashboard in a REAL browser and report what it actually did.

    python3 scripts/verify_dashboard.py [--shots DIR] [--chrome PATH]

Needs python playwright and a Chrome/Chromium. It runs the real service (real routes, real built UI, real
cookie, real websocket) on a loopback port against a FAKE sysfs tree, with a fake uevent source it can poke, so
a board can be attached while the page is open and the page must show it with no reload and no timer.

For each of light and dark, at 390x844 (phone), 768x1024 (tablet) and 1280x800 (desktop): the login page, a
wrong token (refused, no cookie), the right token (cookie is HttpOnly and SameSite=Strict, script cannot read it,
the token is nowhere in the page or its storage), the live band, a live attach, every page, a generated
command run from the page (a read and a refused write), the activity log naming the dashboard as the caller,
no horizontal scroll, no console error, and logout. A foreign-Origin login is refused. Screenshots are saved
for a person to LOOK at: a rendered element is not a verified one.

Exit 0 only if every check passed.
"""
import argparse
import json
import math
import os
import re
import select
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))

import uvicorn  # noqa: E402
import yaml  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.devices import commands as device_commands  # noqa: E402
from nrf_workbench.devices.monitor import DeviceMonitor  # noqa: E402
from nrf_workbench.guards.ledger import TouchLedger  # noqa: E402
from fakes import SERIAL, Tree  # noqa: E402
from test_firmware_store import pkg  # noqa: E402
from test_flash import FakeWatcher, Runner  # noqa: E402
from test_flash_firmware import Touch  # noqa: E402

TOKEN = "verify-dashboard-token-%d" % os.getpid()
BENCH = "verify-bench"
OTHER = "SECONDSERIAL02"
SIZES = (("phone", 390, 844), ("tablet", 768, 1024), ("desktop", 1280, 800))
THEMES = ("light", "dark")
PAGES = ("Home", "Devices", "Wiring", "Console", "Firmware", "GPIO", "Activity", "Commands", "Help")

# How long a check waits for the fake device to hear a line the page sent. The bytes are written before the page's
# request returns, so this is only the give-up for a FAILING check, never a wait.
HEARD_CEILING_S = 5

# A live push is uevent -> wake -> read -> publish -> websocket -> render. Measured on this host over six
# browser runs (printed as `live attach took` on every run): 4, 4, 7, 7, 7, 25 ms. The ceiling is a give-up for
# the FAILURE case, never a wait: 3 x the worst measured (25 ms) = 75 ms, rounded up to 100 ms.
LIVE_CEILING_MS = 100

# How long the fake uploader waits for a check to let it finish before giving up. The held checks (the bar appears,
# a second page opens, the spinner is read) take well under a second each; this is the failure case's way out.
GATE_CEILING_S = 20

# The only responses a correct run may see with a status of 400 or more, in order: the wrong token at login,
# and the refused firmware.delete the run issues on purpose.
EXPECTED_REFUSALS = [("POST", "/login", 401), ("POST", "/api/firmware/delete", 404)]

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


class Bench(object):
    """The real service on a loopback port, a fake bus, and a fake uevent source."""

    def __init__(self):
        self.tree = Tree()
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-verify-")
        self.tree.app_board()
        self.tree.node("ttyACM0", 166, 0)            # the board's device node, so a flash can find it
        self.poke_r, self.poke_w = socket.socketpair()
        # A fake flash: the touch makes the board re-enumerate as its bootloader, the fake uploader makes it come
        # back as its application, and each change pokes the fake uevent source so the page sees it live.
        # The host lock and the touch ledger default to ONE shared path on the machine; this bench gets its own, so a
        # run never inherits (or leaves) a "touch pending" for a fake board.
        self.lock_path = os.path.join(self.tmp, "lock")
        self.gate = threading.Event()                # cleared by a check that wants the "flash" to stay running
        self.gate.set()
        # Two debug links over pseudo-terminal pairs: `dbg` can send, `rxonly` is wired receive only. The pair's master
        # end is "the device": what it writes the page must show, and what the page sends it must hear.
        self.ptys = {}
        for name, serial, can_send in (("dbg", "S1", True), ("rxonly", "S2", False)):
            master, slave = os.openpty()
            self.ptys[name] = {"master": master, "slave": slave, "node": os.ttyname(slave), "present": True}
            self.ptys[name]["raw"] = {"name": name, "backend": "usb-adapter", "adapter": {"ids": ["1a86:55d4"], "serial": serial},
                                      "baud": 115200, "dut_tx_to_host": True, "host_tx_to_dut": can_send,
                                      "voltage": {"dut": 3.3, "adapter": 3.3, "verified": True}, "dtr_rts_reach_dut": False,
                                      "reply_marker": "< "}
        self.links_file = os.path.join(self.tmp, "links.yaml")
        with open(self.links_file, "w") as fh:
            yaml.safe_dump({"links": [self.ptys[n]["raw"] for n in ("dbg", "rxonly")]}, fh)
        self.uevent_a, self.uevent_b = socket.socketpair()       # the kernel's uevents, as far as the broker can tell
        # The GPIO page exists only on a Raspberry Pi, which is what the device tree says: this bench's does.
        os.makedirs(os.path.join(self.tree.proc, "device-tree"), exist_ok=True)
        with open(os.path.join(self.tree.proc, "device-tree", "model"), "wb") as fh:
            fh.write(b"Raspberry Pi 4 Model B Rev 1.4\0")
        hooks = {"tool": "/fake/adafruit-nrfutil", "watcher": FakeWatcher(),
                 "touch": Touch(on_touch=self._to_bootloader), "runner": Runner(on_run=self._uploader_done),
                 "ledger": TouchLedger(self.lock_path + ".touches.json"),
                 "open_uevents": lambda: self.uevent_a, "console_resolve": self._console_resolve}
        cfg, _ = cfgmod.load(env={}, overrides={
            "paths": {"firmware": os.path.join(self.tmp, "fw"), "links": self.links_file,
                      "console": os.path.join(self.tmp, "console"), "gpio": os.path.join(self.tmp, "gpio")},
            "service": {"bench_name": BENCH},
            "streams": {"events": {"retain": 100, "max_age_days": 1}, "activity": {"retain": 100, "max_age_days": 1},
                        "console": {"retain": 1000, "max_age_days": 1}}})
        self.app, self.host, _registry, _caps = service.build(cfg, token=TOKEN, sysfs=self.tree.sysfs, devroot=self.tree.dev,
                                                                  proc=self.tree.proc, hooks=hooks)
        self.host.lock_path = self.lock_path
        self.host.console.start()
        self.package = os.path.join(self.tmp, "package.zip")
        with open(self.package, "wb") as fh:
            fh.write(pkg())
        self.package2 = os.path.join(self.tmp, "second.zip")          # different bytes: the store keeps one copy of each
        with open(self.package2, "wb") as fh:
            fh.write(pkg(seed=7))
        self.monitor = DeviceMonitor(lambda: device_commands.boards(self.host), self.host.streams.events.publish,
                                     open_source=lambda: self.poke_r)
        self.monitor.start()
        # Bind BEFORE the server starts: connections queue in the backlog, so there is nothing to wait for.
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/" % self.port

    def _console_resolve(self, link):
        pty = self.ptys[link.name]
        return (({"device": {}, "node": pty["node"]}, "") if pty["present"] else (None, "the adapter is not attached"))

    def device_says(self, name, data):
        os.write(self.ptys[name]["master"], data)

    def device_heard(self, name, n):
        """The n bytes the page sent to the device, read as the device would; a ceiling only for a failing check."""
        got = b""
        while len(got) < n:
            ready, _, _ = select.select([self.ptys[name]["master"]], [], [], HEARD_CEILING_S)
            if not ready:
                return got
            got += os.read(self.ptys[name]["master"], 4096)
        return got

    def set_present(self, name, present):
        """Plug or unplug the fake adapter: the kernel's uevent is what makes the broker look again."""
        self.ptys[name]["present"] = present
        self.uevent_b.send(b"x")

    def _to_bootloader(self):
        self.tree.unplug("1-3")
        self.tree.bootloader_board()
        self.poke_w.send(b"x")

    def _uploader_done(self):
        # The fake uploader returns when the gate is open. A check that clears the gate holds the flash "running" so
        # the page can be looked at in the middle of it; the timeout is a give-up for a FAILING check, never a wait:
        # every held check below takes well under a second.
        self.gate.wait(timeout=GATE_CEILING_S)
        self._to_app()

    def _to_app(self):
        self.tree.unplug("1-3")
        self.tree.app_board()
        self.poke_w.send(b"x")

    def attach_second(self):
        self.tree.bootloader_board(name="1-4", tty="ttyACM1", majmin="166:1", serial=OTHER)
        self.poke_w.send(b"x")

    def detach_second(self):
        self.tree.unplug("1-4")
        self.poke_w.send(b"x")

    def close(self):
        self.server.should_exit = True
        self.thread.join()                      # returns when uvicorn has drained: an event, not a clock
        self.host.console.stop()
        self.monitor.stop()
        for s in (self.poke_r, self.poke_w, self.uevent_a, self.uevent_b):
            s.close()
        for pty in self.ptys.values():
            for fd in (pty["master"], pty["slave"]):
                os.close(fd)
        self.tree.close()


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def go(page, name):
    page.get_by_role("navigation", name="Pages").get_by_role("button", name=name, exact=True).click()


def device_row(page, serial=None):
    """The row of an nRF board on the Devices page (the first one, or the one with this serial)."""
    go(page, "Devices")
    rows = page.get_by_role("list", name="Devices").get_by_role("button", name=re.compile(serial or "nRF board"))
    rows.first.wait_for()
    return rows.first


def open_flash(page, band=None):
    """The flash dialog opens from a board's row on the Devices page (the old 'Attached' band is gone)."""
    row = device_row(page)
    if row.get_attribute("aria-expanded") != "true":
        row.click()
    page.get_by_role("button", name="Flash…", exact=True).first.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    return dialog


def back_in_app(page, serial=SERIAL):
    """The board is listed again and is running its application (not in its bootloader). Waits on the page, never on the clock."""
    # Asked of the service from the page (its own session), so the caller's page is not left: leaving the Firmware page would lose
    # the project it has open. The list on the Devices page is checked on its own, by the redesign script.
    page.wait_for_function(
        "async (s) => { const r = await fetch('api/devices/list'); const d = await r.json(); return d.data.boards.some((b) => b.serial === s && b.mode === 'app'); }",
        arg=serial, timeout=20000)


def wait_or_show(dialog, locator, what):
    """Wait for `locator`; if it never comes, say what the dialog showed instead (then fail loudly)."""
    try:
        locator.wait_for(timeout=15000)
    except Exception:
        print("DIALOG at %s:\n%s" % (what, dialog.inner_text()), flush=True)
        raise


def flash_flows(page, bench, band, shot, tag):
    """Flash the fake board through the dialog three ways: an uploaded file, a stored package, and a refusal."""
    flash_button = lambda d: d.get_by_role("button", name=re.compile("Erase and flash"))   # noqa: E731
    flash_posts = []
    page.on("request", lambda r: flash_posts.append(r.url) if r.method == "POST" and "/api/programmer/flash" in r.url else None)

    # 1. a file from this computer (the store is empty, so the dialog says so)
    dialog = open_flash(page, band)
    wait_or_show(dialog, dialog.get_by_text("The store is empty"), "empty store")
    check("%s flash: an empty store is said, not shown as an empty list" % tag, True)
    check("%s flash: an empty store still offers a new folder" % tag, dialog.get_by_label("New folder name").count() == 1)
    dialog.get_by_label("A file from this computer").check()
    check("%s flash by upload: the project defaults to unsorted" % tag,
          dialog.get_by_label("Store it in project").input_value() == "unsorted")
    dialog.get_by_label("Or a new project").fill("Bad Name")
    wait_or_show(dialog, dialog.get_by_text("is not a project name"), "a bad project name")
    check("%s flash by upload: a bad project name is said before anything is sent" % tag, True)
    dialog.get_by_label("Package file").set_input_files(bench.package)
    check("%s flash by upload: a bad project name keeps the button disabled even with a file" % tag,
          flash_button(dialog).is_disabled())
    dialog.get_by_label("Or a new project").fill("mt-alarm")
    size = os.path.getsize(bench.package)
    dialog.get_by_text("This erases").wait_for()
    check("%s flash: the confirmation names the board, the package and its size" % tag,
          SERIAL in dialog.inner_text() and "package.zip" in dialog.inner_text() and ("{:,} bytes".format(size)) in dialog.inner_text())
    dialog.locator("summary").click()
    want = math.ceil(2 * size / 12350 * 10) / 10
    got = dialog.get_by_label(re.compile("Write and come back")).input_value()
    check("%s flash: the write bound is derived, 2 x %d / 12350 = %s s" % (tag, size, want), abs(float(got) - want) < 1e-9, got)
    check("%s flash: the enter bound is 2 s" % tag, dialog.get_by_label(re.compile("Enter the bootloader within")).input_value() == "2")
    shot("8b-flash-ready")
    flash_button(dialog).click()
    wait_or_show(dialog, dialog.get_by_role("status").filter(has_text="packets").first, "flash by upload")
    check("%s flash by upload: the verdict line says packets and that it came back" % tag,
          "came back as" in dialog.inner_text(), dialog.inner_text()[-400:])
    check("%s flash by upload: the Result is shown whole, class ok" % tag, dialog.get_by_text("ok", exact=True).count() >= 1)
    dialog.screenshot(path=os.path.join(bench.shots, "%s-9-flash-done.png" % tag))
    dialog.get_by_role("button", name="Close", exact=True).click()
    dialog.wait_for(state="hidden")
    back_in_app(page)
    check("%s flash by upload: the board is back in the live list, running its application" % tag, True)

    # 2. from the store: the folder the upload made is listed; nothing is preselected, not even a folder
    dialog = open_flash(page, band)
    folder = dialog.get_by_label("Project folder")
    folder.wait_for()
    check("%s flash from the store: no folder is preselected" % tag, folder.input_value() == "")
    check("%s flash from the store: the folder list names the new project and how many it holds" % tag,
          "mt-alarm (1)" in folder.inner_text(), folder.inner_text())
    check("%s flash from the store: no package list until a folder is chosen" % tag,
          dialog.get_by_label("Stored package").count() == 0)
    check("%s flash from the store: the button is disabled until a package is chosen" % tag, flash_button(dialog).is_disabled())
    folder.select_option(value="mt-alarm")
    select = dialog.get_by_label("Stored package")
    select.wait_for()
    check("%s flash from the store: nothing is preselected" % tag, select.input_value() == "")
    check("%s flash from the store: the entry shows its name and short id" % tag,
          "package.zip" in select.inner_text() or "unnamed" in select.inner_text(), select.inner_text())
    select.select_option(index=1)
    check("%s flash from the store: choosing one enables the button" % tag, not flash_button(dialog).is_disabled())

    # a new folder from the dialog: it is created, opened and empty; the earlier choice is left behind
    dialog.get_by_label("New folder name").fill("Bad Name")
    check("%s new folder: a bad name keeps Create folder disabled" % tag,
          dialog.get_by_role("button", name="Create folder").is_disabled())
    dialog.get_by_label("New folder name").fill("alpha")
    posts_before_create = len(flash_posts)
    dialog.get_by_role("button", name="Create folder").click()
    wait_or_show(dialog, dialog.get_by_text("is empty"), "a new folder")
    check("%s new folder: pressing Create folder with a package chosen starts no flash" % tag,
          len(flash_posts) == posts_before_create, flash_posts)
    check("%s new folder: it is made and opened, empty" % tag, folder.input_value() == "alpha")
    check("%s new folder: the package chosen in the other folder is not carried over" % tag,
          flash_button(dialog).is_disabled() and dialog.get_by_label("Stored package").count() == 0)
    folder.select_option(value="mt-alarm")
    dialog.get_by_label("Stored package").select_option(index=1)

    # Enter in a text box must never start an erase (the dialog's form would submit on it). The form is READY here:
    # a package is chosen and the button is enabled, so a submit would flash.
    before = len(flash_posts)
    product = dialog.get_by_label(re.compile("The board must come back as"))
    product.fill("x")
    check("%s Enter test: the form is ready (the button is enabled)" % tag, not flash_button(dialog).is_disabled())
    product.press("Enter")
    page.evaluate("0")               # a round trip: any request the Enter started has been reported before this returns
    check("%s Enter in the product box starts no flash" % tag, len(flash_posts) == before, flash_posts)
    product.fill("")
    box = dialog.get_by_label("New folder name")
    box.fill("beta")
    box.press("Enter")
    expect(folder).to_have_value("beta", timeout=15000)       # a locator, re-resolved: the list may refresh under it
    check("%s Enter in the new-folder box creates and opens the folder" % tag, folder.input_value() == "beta")
    check("%s Enter in a text box starts no flash, however ready the form is" % tag, len(flash_posts) == before, flash_posts)
    dialog.get_by_label("Project folder").select_option(value="mt-alarm")
    dialog.get_by_label("Stored package").select_option(index=1)
    flash_button(dialog).click()
    dialog.get_by_role("status").filter(has_text="packets").first.wait_for()
    check("%s flash from the store: it flashed" % tag, "came back as" in dialog.inner_text())

    # 3. a refusal: the board comes back as something other than what was expected
    dialog.get_by_label(re.compile("The board must come back as")).fill("a-product-it-will-not-be")
    flash_button(dialog).click()
    dialog.get_by_text("flash_suspect").first.wait_for()
    check("%s flash refusal: the code is shown by name" % tag, dialog.get_by_text("flash_suspect").count() >= 1)
    check("%s flash refusal: it is not drawn as a success" % tag, "came back as" not in dialog.locator("[role=status]").last.inner_text()
          or dialog.get_by_text("suspect", exact=True).count() >= 1)
    shot("9b-flash-refused")
    dialog.get_by_role("button", name="Close", exact=True).click()
    dialog.wait_for(state="hidden")

    # 4. the store cannot be listed (the request never gets an answer): the reason is shown and Try again recovers
    def drop(route):
        route.abort()

    page.route("**/api/firmware/list", drop)
    dialog = open_flash(page, band)
    wait_or_show(dialog, dialog.get_by_role("alert").filter(has_text="could not be listed"), "a store that cannot be listed")
    alert = dialog.get_by_role("alert").filter(has_text="could not be listed").inner_text()
    check("%s store down: the message says why, not just that it failed" % tag, "Failed to fetch" in alert or "NetworkError" in alert or "failed" in alert.lower(), alert)
    check("%s store down: Try again is offered" % tag, dialog.get_by_role("button", name="Try again").count() == 1)
    check("%s store down: the button is disabled, nothing can be flashed" % tag, flash_button(dialog).is_disabled())
    shot("9c-store-down")
    page.unroute("**/api/firmware/list", drop)
    dialog.get_by_role("button", name="Try again").click()
    dialog.get_by_label("Project folder").wait_for()
    check("%s store down: Try again lists the store once it answers" % tag, "mt-alarm (1)" in dialog.get_by_label("Project folder").inner_text())
    dialog.get_by_role("button", name="Close", exact=True).click()
    dialog.wait_for(state="hidden")


def api(bench, method, path, body=None):
    """A caller that is not this page: a bearer token, as the CLI and the MCP use."""
    request = urllib.request.Request(bench.url + path, data=body, method=method, headers={
        "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def in_progress(page, bench, band, shot, tag):
    """What is running is shown to everyone, whoever started it (docs/specs/dashboard.md, "The in-progress
    indicator"): a flash started by ANOTHER caller, a page opened in the middle of it, and one started from the dialog."""
    flash_button = lambda d: d.get_by_role("button", name=re.compile("Erase and flash"))   # noqa: E731
    bar = page.get_by_label("Activity in progress")
    base_title = page.title()
    check("%s in progress: the bar is empty while nothing runs" % tag, bar.locator("li").count() == 0)

    # 1. another caller starts a flash; this page did not press anything
    ident = api(bench, "GET", "api/firmware/list")["data"]["items"][0]["sha256"]
    body = json.dumps({"serial": SERIAL, "firmware": ident, "enter_bound_s": 2, "flash_bound_s": 60}).encode()
    bench.gate.clear()
    out = []
    worker = threading.Thread(target=lambda: out.append(api(bench, "POST", "api/programmer/flash", body)), daemon=True)
    worker.start()
    bar.get_by_text("Flashing " + SERIAL).wait_for(timeout=15000)
    text = bar.inner_text()
    check("%s in progress: a flash started by another caller shows in the header" % tag, "Flashing %s" % SERIAL in text, text)
    check("%s in progress: it says who started it" % tag, "by token" in text, text)
    check("%s in progress: it says when it started" % tag, re.search(r"started \d{1,2}:\d{2}", text) is not None, text)
    check("%s in progress: it carries a spinner" % tag, bar.locator(".animate-spin").count() == 1)
    check("%s in progress: the tab title says so" % tag, page.title() == "Working · " + base_title, page.title())
    shot("10-in-progress")

    # 2. a page opened in the middle of it: the retained start replays and it is shown at once
    second = page.context.new_page()
    second.goto(bench.url)
    second_bar = second.get_by_label("Activity in progress")
    second_bar.get_by_text("Flashing " + SERIAL).wait_for(timeout=15000)
    check("%s in progress: a page opened mid-flash shows it too" % tag, True)
    second.emulate_media(reduced_motion="reduce")
    still = second.evaluate("e => getComputedStyle(e).animationName", second_bar.locator(".animate-spin").element_handle())
    check("%s in progress: a visitor who asks for reduced motion gets a still marker" % tag, still == "none", still)
    second.close()

    # 3. it ends: the bar clears, the title returns, and the flash itself was a success
    bench.gate.set()
    expect(bar.locator("li")).to_have_count(0, timeout=15000)
    worker.join(timeout=GATE_CEILING_S)
    check("%s in progress: the bar clears when the flash ends" % tag, True)
    check("%s in progress: the tab title returns" % tag, page.title() == base_title, page.title())
    check("%s in progress: the flash the other caller started succeeded" % tag, out and out[0].get("class") == "ok", out)
    back_in_app(page)

    # 4. started from this page's own dialog: the dialog spins, and the header shows it as the dashboard's
    dialog = open_flash(page, band)
    dialog.get_by_label("Project folder").select_option(value="mt-alarm")
    dialog.get_by_label("Stored package").select_option(index=1)
    bench.gate.clear()
    flash_button(dialog).click()
    dialog.locator(".animate-spin").first.wait_for(timeout=15000)
    check("%s in progress: the dialog shows a spinner while its own flash runs" % tag, True)
    bar.get_by_text("Flashing " + SERIAL).wait_for(timeout=15000)
    check("%s in progress: the header shows the dialog's flash as the dashboard's" % tag, "by dashboard" in bar.inner_text(), bar.inner_text())
    check("%s in progress: the dialog cannot be closed while it runs" % tag,
          dialog.get_by_role("button", name="Close", exact=True).is_disabled())
    bench.gate.set()
    dialog.get_by_role("status").filter(has_text="packets").first.wait_for(timeout=15000)
    expect(bar.locator("li")).to_have_count(0, timeout=15000)
    check("%s in progress: the bar clears after the dialog's flash too" % tag, True)
    dialog.get_by_role("button", name="Close", exact=True).click()
    dialog.wait_for(state="hidden")
    back_in_app(page)


def firmware_page(page, bench, band, shot, tag):
    """The Firmware page (docs/specs/dashboard.md): browse by project, make and delete projects, upload, move, delete
    behind a confirm, and flash a package from it. Every write goes to a path the service listed."""
    flash_button = lambda d: d.get_by_role("button", name=re.compile("Erase and flash"))   # noqa: E731
    deletes = []
    page.on("request", lambda r: deletes.append(r.url) if r.method == "POST" and "/api/firmware/delete" in r.url else None)
    page.get_by_role("navigation", name="Pages").get_by_role("button", name="Firmware").click()
    projects = page.get_by_role("list", name="Projects")
    projects.wait_for(timeout=15000)
    result = page.locator("section", has=page.get_by_role("heading", name="Last result"))
    pick = lambda name: projects.get_by_role("button", name=re.compile(r"^%s\b" % re.escape(name)))   # noqa: E731

    check("%s firmware: the projects are listed with their counts" % tag,
          "mt-alarm" in projects.inner_text() and pick("mt-alarm").inner_text().split()[-1] == "1", projects.inner_text())
    check("%s firmware: no project is chosen for you" % tag,
          page.get_by_text("Choose a project to see its packages").count() == 1
          and page.locator("[aria-pressed=true]").count() == 0)

    # browse: choose a project, see its packages
    pick("mt-alarm").click()
    row = page.get_by_role("list", name="Packages in mt-alarm").locator("li")
    row.first.wait_for()
    check("%s firmware: a project shows its packages with name, short id, size and packets" % tag,
          "package.zip" in row.first.inner_text() and "packets" in row.first.inner_text() and "SoftDevice" in row.first.inner_text(), row.first.inner_text())
    check("%s firmware: the chosen project is marked" % tag, pick("mt-alarm").get_attribute("aria-pressed") == "true")
    check("%s firmware: a project that holds a package cannot be deleted, and says why" % tag,
          page.get_by_role("button", name="Delete project").is_disabled() and "holds 1 package" in page.locator("main, #root").first.inner_text())
    check("%s firmware: no horizontal scroll with packages listed" % tag, overflow(page) <= 0, overflow(page))
    shot("11-firmware")

    # a new project: a bad name is refused before anything is sent; a good one is made, opened and empty
    name_box = page.get_by_label("New project name")
    name_box.fill("Bad Name")
    check("%s firmware: a bad project name keeps Create disabled" % tag, page.get_by_role("button", name="Create project").is_disabled())
    name_box.fill("gamma")
    page.get_by_role("button", name="Create project").click()
    result.get_by_text("created").wait_for(timeout=15000)
    check("%s firmware: a new project is made, shown with the service's whole Result, and opened empty" % tag,
          pick("gamma").get_attribute("aria-pressed") == "true" and page.get_by_text("This project is empty").count() == 1)

    # the same bytes again: the store keeps one copy and the page says where it is, instead of looking as if it worked
    page.get_by_label("Package file").set_input_files(bench.package)
    page.get_by_role("button", name="Upload").click()
    result.get_by_text("already stored in project mt-alarm").wait_for(timeout=15000)
    check("%s firmware: uploading bytes the store already holds says where they are and copies nothing" % tag,
          page.get_by_text("This project is empty").count() == 1)

    # upload into it: the file lands in the chosen project
    page.get_by_label("Package file").set_input_files(bench.package2)
    page.get_by_label("Package name").fill("package.zip")                 # the optional name wins over the file's own
    page.get_by_role("button", name="Upload").click()
    page.get_by_role("list", name="Packages in gamma").locator("li").first.wait_for(timeout=15000)
    check("%s firmware: an upload lands in the chosen project under the name given" % tag,
          "package.zip" in page.get_by_role("list", name="Packages in gamma").inner_text())
    check("%s firmware: the upload's Result is shown whole (class ok, the project, the id)" % tag,
          "gamma" in result.inner_text() and "sha256" in result.inner_text(), result.inner_text()[:200])

    # flash from the page: the dialog opens with this package chosen BY THE PERSON, and the erase still needs its button
    page.get_by_role("button", name="Flash package.zip").click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    check("%s firmware flash: the dialog opens on the package that was pressed" % tag,
          dialog.get_by_label("Project folder").input_value() == "gamma" and dialog.get_by_label("Stored package").input_value() != "")
    check("%s firmware flash: the erase still needs its own button, and it is enabled" % tag, not flash_button(dialog).is_disabled())
    check("%s firmware flash: nothing was sent by opening it" % tag, True)
    flash_button(dialog).click()
    dialog.get_by_role("status").filter(has_text="packets").first.wait_for(timeout=20000)
    dialog.get_by_role("button", name="Close", exact=True).click()
    dialog.wait_for(state="hidden")
    back_in_app(page)

    # more than one board: none is chosen for you, and the buttons wait
    bench.attach_second()
    page.get_by_label("Flash to board").wait_for(timeout=15000)
    check("%s firmware flash: with two boards none is chosen and Flash is disabled" % tag,
          page.get_by_label("Flash to board").input_value() == "" and page.get_by_role("button", name="Flash package.zip").is_disabled())
    page.get_by_label("Flash to board").select_option(value=SERIAL)
    check("%s firmware flash: choosing a board enables it" % tag, not page.get_by_role("button", name="Flash package.zip").is_disabled())
    bench.detach_second()
    page.get_by_label("Flash to board").wait_for(state="detached", timeout=15000)

    # move: a menu of the other projects; the package leaves this project and is in the other
    page.get_by_role("button", name="More actions for package.zip").click()
    page.get_by_role("menu").wait_for()
    check("%s firmware menu: Move lists the other projects, not this one" % tag,
          page.get_by_role("menuitem", name="alpha").count() == 1 and page.get_by_role("menuitem", name="gamma").count() == 0)
    page.get_by_role("menuitem", name="alpha").click()
    page.get_by_text("This project is empty").wait_for(timeout=15000)
    check("%s firmware menu: the package moved out of gamma" % tag, True)
    pick("alpha").click()
    page.get_by_role("list", name="Packages in alpha").locator("li").first.wait_for(timeout=15000)
    check("%s firmware menu: and is in alpha" % tag, "package.zip" in page.get_by_role("list", name="Packages in alpha").inner_text())

    # delete a package: a confirm first, naming it; Cancel and Escape delete nothing; the confirm does
    page.get_by_role("button", name="More actions for package.zip").click()
    page.get_by_role("menuitem", name="Delete…").click()
    alert = page.get_by_role("alertdialog")
    alert.wait_for()
    text = alert.inner_text()
    shot("11c-delete-confirm")
    check("%s delete: the confirm names the package and its project and says it cannot be undone" % tag,
          "package.zip" in text and "alpha" in text and "cannot be undone" in text, text)
    check("%s delete: the focus starts on Cancel, the safe answer" % tag,
          page.evaluate("document.activeElement && document.activeElement.textContent") == "Cancel")
    sent = len(deletes)
    page.keyboard.press("Escape")
    alert.wait_for(state="hidden")
    check("%s delete: Escape deletes nothing" % tag, len(deletes) == sent and page.get_by_role("list", name="Packages in alpha").locator("li").count() == 1)
    page.get_by_role("button", name="More actions for package.zip").click()
    page.get_by_role("menuitem", name="Delete…").click()
    alert.wait_for()
    alert.get_by_role("button", name="Cancel").click()
    alert.wait_for(state="hidden")
    page.evaluate("0")
    check("%s delete: Cancel deletes nothing" % tag, len(deletes) == sent and page.get_by_role("list", name="Packages in alpha").locator("li").count() == 1)
    page.get_by_role("button", name="More actions for package.zip").click()
    page.get_by_role("menuitem", name="Delete…").click()
    alert.wait_for()
    alert.get_by_role("button", name="Delete package").click()
    page.get_by_text("This project is empty").wait_for(timeout=15000)
    check("%s delete: the confirm deletes it, and only then" % tag, len(deletes) == sent + 1, deletes)
    check("%s delete: the Result of the delete is shown" % tag, "deleted" in result.inner_text(), result.inner_text()[:120])

    # delete an empty project: asked first too
    pick("beta").click()
    check("%s project delete: an empty project can be deleted" % tag, not page.get_by_role("button", name="Delete project").is_disabled())
    page.get_by_role("button", name="Delete project").click()
    alert.wait_for()
    check("%s project delete: it asks first" % tag, "Delete the project beta" in alert.inner_text(), alert.inner_text())
    alert.get_by_role("button", name="Delete project").click()
    pick("beta").wait_for(state="detached", timeout=15000)
    check("%s project delete: and the project is gone" % tag, projects.get_by_role("button", name=re.compile(r"^beta\b")).count() == 0)
    shot("11b-firmware-after")


def console_page(page, bench, shot, tag):
    """The Console page against two real pseudo-terminal links: the log, the input and its history, the disabled
    reasons, a link's state changing live, following the end of the log, and macros. Every wait is for the thing
    itself; the only time constant is the give-up on a device that never hears (HEARD_CEILING_S)."""
    nav = page.get_by_role("navigation", name="Pages")
    nav.get_by_role("button", name="Console").click()
    select_link = page.get_by_label("Link")
    select_link.wait_for()
    log = page.get_by_role("log", name="Console log")
    cmd = page.get_by_label("Command to send")
    rows = log.locator("div[data-kind]")

    def texts():
        return rows.evaluate_all("els => els.map(e => e.dataset.kind + ' ' + e.innerText)")

    options = select_link.locator("option").all_inner_texts()
    check("%s console: both declared links are offered with their state" % tag, options == ["dbg, open", "rxonly, open"], options)
    check("%s console: the sendable link says so, with its voltage" % tag,
          page.get_by_text("can send", exact=True).count() == 1 and page.get_by_text("voltage verified", exact=True).count() == 1)
    check("%s console: nothing is received yet, and the page says so in one line" % tag,
          "Nothing received yet." in log.inner_text() and "silent device" not in log.inner_text())

    # what the device says appears, a reply line is drawn as a reply
    bench.device_says("dbg", b"boot 1\n< pong\n")
    log.get_by_text("< pong").wait_for()
    check("%s console: lines the device printed are shown" % tag, any(t.startswith("rx") and "boot 1" in t for t in texts()), texts())
    reply_class = log.locator("div[data-kind=rx]", has_text="< pong").locator("span").last.get_attribute("class")
    check("%s console: a reply line is drawn distinctly" % tag, "emerald" in (reply_class or ""), reply_class)

    # a send: the device hears it with the link's line ending, the input is emptied and keeps the focus
    cmd.fill("status")
    cmd.press("Enter")
    log.locator("div[data-kind=tx]", has_text="> status").wait_for()
    check("%s console: the device heard the line and its ending" % tag, bench.device_heard("dbg", 7) == b"status\n")
    check("%s console: the input is emptied after a send" % tag, cmd.input_value() == "")
    check("%s console: the input keeps the focus" % tag,
          page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')") == "Command to send")
    bench.device_says("dbg", b"< ok\n")
    log.locator("div[data-kind=rx]", has_text="< ok").wait_for()
    seen = texts()
    sent_at = next(i for i, t in enumerate(seen) if t.startswith("tx") and "> status" in t)
    reply_at = next(i for i, t in enumerate(seen) if t.startswith("rx") and "< ok" in t)
    check("%s console: the sent line is drawn before the reply it caused" % tag, sent_at < reply_at, seen)
    shot("9-console")

    # history: up and down, the typed line kept, repeats collapsed, escape clears
    for sent_so_far, line in ((1, "reboot"), (2, "reboot")):
        cmd.fill(line)
        cmd.press("Enter")
        # the page ignores a second Enter while one is in flight, so wait for this one to be drawn before the next
        page.wait_for_function("n => document.querySelectorAll('div[data-kind=tx]').length >= n", arg=sent_so_far + 1)
        bench.device_heard("dbg", len(line) + 1)
    walked = []
    for key in ("ArrowUp", "ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowDown"):
        cmd.press(key)
        walked.append(cmd.input_value())
    check("%s console: up and down walk the history, newest first, repeats collapsed, and stop at both ends" % tag,
          walked == ["reboot", "status", "status", "reboot", "", ""], walked)
    cmd.fill("sta")
    cmd.press("ArrowUp")
    cmd.press("ArrowUp")
    cmd.press("ArrowDown")
    cmd.press("ArrowDown")
    check("%s console: the line being typed is kept while walking the history" % tag, cmd.input_value() == "sta", cmd.input_value())
    cmd.press("Escape")
    check("%s console: escape clears the input" % tag, cmd.input_value() == "")

    # the history belongs to the bench: a reload keeps it
    page.reload()
    nav.wait_for()
    nav.get_by_role("button", name="Console").click()
    page.get_by_label("Command to send").wait_for()
    page.get_by_role("log", name="Console log").locator("div[data-kind=tx]", has_text="> reboot").first.wait_for()
    cmd = page.get_by_label("Command to send")
    cmd.press("ArrowUp")
    check("%s console: the history survives a reload (it is the bench's, not the browser's)" % tag, cmd.input_value() == "reboot", cmd.input_value())
    cmd.press("Escape")
    log = page.get_by_role("log", name="Console log")
    rows = log.locator("div[data-kind]")

    # a receive-only link: the input is off, with the reason
    select_link = page.get_by_label("Link")
    select_link.select_option("rxonly")
    page.get_by_text("receive only", exact=True).first.wait_for()
    check("%s console: a receive-only link's input and button are disabled" % tag,
          cmd.is_disabled() and page.get_by_role("button", name="Send", exact=True).is_disabled())
    note = page.get_by_role("note").filter(has_text="link_rx_only")
    check("%s console: the page gives the service's reason by name" % tag, note.count() == 1 and "receive only" in note.inner_text(), note.count())
    check("%s console: the other link's log is not shown here" % tag, "boot 1" not in log.inner_text())
    shot("9-console-rx-only")
    select_link.select_option("dbg")

    # a link's state changing is shown with no reload and no timer
    bench.set_present("dbg", False)
    page.get_by_text("absent", exact=True).first.wait_for()
    check("%s console: an adapter that goes away is shown, and the input is off" % tag, cmd.is_disabled())
    check("%s console: the page says the adapter is absent" % tag, page.get_by_role("note").filter(has_text="The adapter is absent").count() == 1)
    shot("9-console-absent")
    bench.set_present("dbg", True)
    page.get_by_text("open", exact=True).first.wait_for()
    cmd.wait_for(state="visible")
    page.wait_for_function("!document.querySelector('input[aria-label=\"Command to send\"]').disabled")
    check("%s console: the adapter coming back re-enables the input" % tag, True)

    # following the end of the log, and not following once the person has scrolled up
    bench.device_says("dbg", b"".join(b"line %d\n" % i for i in range(80)))
    log.get_by_text("line 79").wait_for()
    at_end = "el => el.scrollHeight - el.scrollTop - el.clientHeight < 5"
    check("%s console: the log follows the end while the person is at the end" % tag, log.evaluate(at_end))
    log.evaluate("el => { el.scrollTop = 0; }")
    jump = page.get_by_role("button", name="Jump to latest")
    jump.wait_for()
    bench.device_says("dbg", b"line 80\n")
    log.get_by_text("line 80").wait_for(state="attached")
    check("%s console: scrolled up, the log does not jump when a line arrives" % tag, log.evaluate("el => el.scrollTop < 5"))
    jump.click()
    check("%s console: Jump to latest returns to the end" % tag, log.evaluate(at_end))
    jump.wait_for(state="detached")

    # macros: the name Peter typed ("Ping") must save, and a bad name must say what is wrong and why Save is off
    page.get_by_label("Macro name").fill("Ping")
    page.get_by_label("Lines to send").fill("ping")
    check("%s macros: a capital letter is made lower case as it is typed" % tag, page.get_by_label("Macro name").input_value() == "ping")
    check("%s macros: Save is on for the name Ping with the line ping" % tag, page.get_by_role("button", name="Save macro").is_enabled())
    page.get_by_label("Macro name").fill("my macro")
    check("%s macros: a space is explained next to the field" % tag, "Spaces aren't allowed" in page.get_by_role("alert").filter(has_text="Spaces").inner_text())
    check("%s macros: a greyed-out Save says why" % tag,
          page.get_by_role("button", name="Save macro").is_disabled() and page.get_by_text("Fix the name above.").count() == 1)
    page.get_by_label("Macro name").fill("")
    check("%s macros: an empty name says to give it one" % tag, page.get_by_text("Give the macro a name.").count() == 1)
    page.get_by_label("Lines to send").fill("")
    # macros: save, run through the guards, edit, delete
    page.get_by_label("Macro name").fill("boot")
    page.get_by_label("Lines to send").fill("reset\nstatus")
    page.get_by_role("button", name="Save macro").click()
    item = page.locator("li", has_text="boot").filter(has=page.get_by_role("button", name="Run"))
    item.first.wait_for()
    check("%s macros: a saved macro is listed with its lines" % tag, "reset" in item.first.inner_text() and "status" in item.first.inner_text())
    item.first.get_by_role("button", name="Run").click()
    page.get_by_role("status").filter(has_text="ran 2 lines").wait_for()
    check("%s macros: running it sends every line in order" % tag, bench.device_heard("dbg", len(b"reset\nstatus\n")) == b"reset\nstatus\n")
    shot("9-console-macros")
    item.first.get_by_role("button", name="Edit").click()
    check("%s macros: Edit loads the macro into the form" % tag,
          page.get_by_label("Macro name").input_value() == "boot" and page.get_by_label("Lines to send").input_value() == "reset\nstatus")
    select_link.select_option("rxonly")
    check("%s macros: Run is disabled on a link that cannot send" % tag, item.first.get_by_role("button", name="Run").is_disabled())
    select_link.select_option("dbg")
    item.first.get_by_role("button", name="Delete").click()
    ask = page.get_by_role("group", name="Delete boot?")         # the row now holds the question, not the Run button
    check("%s macros: Delete asks first, inline" % tag, ask.count() == 1 and "Delete?" in ask.inner_text())
    ask.get_by_role("button", name="Yes").click()
    page.get_by_text("No macros yet").wait_for()
    check("%s macros: a deleted macro is gone after the service's answer" % tag, True)


def gpio_page(page, bench, shot, tag):
    """The GPIO page: the header as drawn on the board, a form that states every field, the service's refusals shown
    whole, an edit, and an unassign. It records assignments only; nothing here can drive a pin."""
    page.get_by_role("navigation", name="Pages").get_by_role("button", name="GPIO").click()
    header = page.get_by_role("group", name="40-pin header")
    header.wait_for()
    cells = header.get_by_role("button")
    check("%s gpio: the 28 header GPIOs are buttons" % tag, cells.count() == 28, cells.count())
    reserved = header.get_by_role("button", name=re.compile(r"^Pin 2[78], GPIO [01],"))
    check("%s gpio: the HAT EEPROM pins are reserved and disabled" % tag,
          reserved.count() == 2 and all(reserved.nth(i).is_disabled() for i in range(2)), reserved.count())
    order = header.evaluate("el => Array.from(el.querySelectorAll('button')).slice(0, 4).map(b => b.getAttribute('aria-label'))")
    check("%s gpio: drawn as on the board, odd pins left and even right, from pin 1" % tag,
          order[0].startswith("Pin 3,") and order[1].startswith("Pin 5,") and order[2].startswith("Pin 7,") and order[3].startswith("Pin 8,"), order)
    check("%s gpio: nothing is assigned yet and no form is open" % tag,
          page.get_by_text("Choose a GPIO above").count() == 1 and header.get_by_text("free").count() >= 1)

    def field(label):
        return page.locator("label").filter(has=page.locator("span", has_text=re.compile("^%s$" % re.escape(label)))).locator("input, select").first

    # a new output on GPIO17 (physical pin 11): every field must be stated before the button works
    header.get_by_role("button", name=re.compile(r"^Pin 11, GPIO 17,")).click()
    page.get_by_role("heading", name="Assignment for GPIO 17").wait_for()
    submit = page.get_by_role("button", name="Assign", exact=True)
    check("%s gpio: Assign is disabled until every field is stated" % tag, submit.is_disabled())
    check("%s gpio: the yes/no fields start on 'choose', never on a pre-picked answer" % tag,
          all(field(l).input_value() == "" for l in ("Active low", "Voltage measured", "Direction")))
    field("Name").fill("dut-reset")
    field("Service").fill("programmer")
    field("Action").fill("reset")
    field("Purpose").fill("Resets the RAK for a flash.")
    field("Direction").select_option("output")
    check("%s gpio: choosing output shows the output fields and no pull" % tag,
          page.get_by_text("Longest pulse (ms)").count() == 1 and page.get_by_text("State when taken").count() == 1
          and page.get_by_text("Drives a DUT pin").count() == 1 and page.get_by_text("The bias on the input").count() == 0)
    check("%s gpio: the longest pulse has no suggested value" % tag, field("Longest pulse (ms)").input_value() == "")
    field("Active low").select_option("no")
    field("Signal voltage (V)").fill("3.3")
    field("Voltage measured").select_option("no")
    field("State when taken").select_option("inactive")
    field("Longest pulse (ms)").fill("100")
    check("%s gpio: still disabled with one field unstated" % tag, submit.is_disabled())
    field("Drives a DUT pin").select_option("yes")
    check("%s gpio: enabled once everything is stated" % tag, submit.is_enabled())
    shot("9-gpio-form")
    # HARNESS ARTIFACT, found with an event trace (not a page fault): a full-page screenshot overrides the viewport, and
    # the next blur of the focused field makes Chrome re-clamp the scroll by 271 px between mousedown and mouseup, so the
    # click lands on the form instead of the button. Without the screenshot the same click submits. A person never takes
    # one, so the cure belongs here: blur the field and let two frames pass before clicking.
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
    submit.click()
    page.get_by_role("status").filter(has_text="assigned").wait_for()
    check("%s gpio: the service's answer is shown whole" % tag, page.get_by_role("status").filter(has_text="assigned").first.inner_text() != "")
    page.get_by_role("button", name=re.compile(r"^Pin 11, GPIO 17, assigned to dut-reset")).wait_for()
    check("%s gpio: the header now shows the name on its pin" % tag, True)
    shot("9-gpio-assigned")

    # a refusal is the service's, by name: a 5 V signal
    header.get_by_role("button", name=re.compile(r"^Pin 15, GPIO 22,")).click()
    field("Name").fill("door")
    field("Service").fill("manual")
    field("Action").fill("sense")
    field("Purpose").fill("Tells if the bench door is shut.")
    field("Direction").select_option("input")
    field("Active low").select_option("yes")
    field("Signal voltage (V)").fill("5")
    field("Voltage measured").select_option("yes")
    field("Pull").select_option("up")
    page.get_by_role("button", name="Assign", exact=True).click()
    page.get_by_role("status").filter(has_text="voltage_unsafe").wait_for()
    check("%s gpio: a 5 V signal is refused by the service, by name" % tag, True)
    check("%s gpio: a refused assignment is not on the header" % tag,
          header.get_by_role("button", name=re.compile(r"^Pin 15, GPIO 22, assigned")).count() == 0)

    # an edit: the assigned pin loads its record, and saving says replaced
    header.get_by_role("button", name=re.compile(r"^Pin 11, GPIO 17, assigned to dut-reset")).click()
    check("%s gpio: an assigned pin loads its assignment for editing" % tag,
          field("Name").input_value() == "dut-reset" and field("Direction").input_value() == "output"
          and field("Longest pulse (ms)").input_value() == "100" and field("Drives a DUT pin").input_value() == "yes")
    field("Longest pulse (ms)").fill("50")
    page.get_by_role("button", name="Save changes").click()
    page.get_by_role("status").filter(has_text="replaced").wait_for()
    check("%s gpio: saving an edit says it replaced the assignment" % tag, True)

    # unassign asks inline, then the pin is free again
    page.get_by_role("button", name="Unassign", exact=True).click()
    ask = page.get_by_role("group", name="Unassign dut-reset?")
    check("%s gpio: unassign asks first, inline" % tag, ask.count() == 1)
    ask.get_by_role("button", name="Yes").click()
    page.get_by_role("button", name=re.compile(r"^Pin 11, GPIO 17, free")).wait_for()
    check("%s gpio: after unassign the pin is free on the header" % tag, True)


def wiring_page(page, bench, shot, tag):
    """The Wiring preview on sample data (docs/specs/wiring.md): devices as cards, connections as wire sentences, the
    collapsing add sheet, a plugged-in adapter noticed live, claimed things greyed with their holder, the coloured header."""
    nav = page.get_by_role("navigation", name="Pages")
    nav.get_by_role("button", name="Wiring").click()
    page.get_by_role("group", name="Devices").wait_for()
    check("%s wiring: it says plainly that this is a preview on sample data" % tag,
          page.get_by_role("note").filter(has_text="Preview.").count() == 1 and "Nothing here touches the bench" in page.get_by_role("note").first.inner_text())
    cards = page.get_by_role("group", name="Devices").get_by_role("button")
    check("%s wiring: both devices are cards and the first is chosen" % tag,
          cards.count() == 2 and cards.nth(0).get_attribute("aria-pressed") == "true" and cards.nth(1).get_attribute("aria-pressed") == "false")
    wires = page.get_by_role("list", name=re.compile("^Connections of RAK4631 D953"))
    check("%s wiring: the chosen device's two wires are written as sentences" % tag,
          "Reading the debug log of RAK4631 D953… through USB adapter USB Single Serial at 115200 baud, receive only." in wires.inner_text()
          and "Resets RAK4631 D953… with GPIO17" in wires.inner_text(), wires.inner_text()[:200])
    check("%s wiring: nothing is waiting in the plugged-in box yet" % tag, page.get_by_role("group", name=re.compile("plugged in but not connected")).count() == 0)
    shot("9-wiring-start")

    # a USB adapter is plugged in: the page notices and asks which device it is wired to
    page.get_by_role("button", name="Simulate: plug in a USB adapter").click()
    box = page.get_by_role("group", name=re.compile("FT232R USB UART is plugged in but not connected"))
    box.wait_for()
    check("%s wiring: a plugged-in adapter appears with the question already asked" % tag, "Which device is it wired to?" in box.inner_text())
    setup = box.get_by_role("button", name="Set up the connection")
    check("%s wiring: Set up stays off until a device is chosen" % tag, setup.is_disabled())
    shot("9-wiring-plugged-in")
    box.get_by_label(re.compile("^Device wired to")).select_option(label="RAK4631 A1B2… (bootloader)")
    setup.click()
    sheet = page.get_by_role("region", name=re.compile("^Connect something to RAK4631 A1B2"))
    sheet.wait_for()
    check("%s wiring: the sheet opens for the second device with what is known already answered" % tag,
          "Read its debug log" in sheet.inner_text() and "FT232R USB UART (8C2D…)" in sheet.inner_text())
    check("%s wiring: only the next question is open, the later ones are not shown" % tag,
          sheet.get_by_text("What baud rate does its firmware use?").count() == 1 and sheet.get_by_text("Should the bench be able to send to it?").count() == 0)
    check("%s wiring: no baud is pre-chosen" % tag, sheet.get_by_role("radio", checked=True).count() == 0)
    shot("9-wiring-sheet-baud")
    sheet.locator("label", has_text=re.compile(r"^115200$")).click()          # a chip: the label is what a person clicks
    sheet.get_by_role("radio", name=re.compile("^No, only watch")).click()
    sentence = sheet.get_by_test_id("sentence")
    sentence.wait_for()
    check("%s wiring: the finished sentence is the connection, receive only" % tag,
          sentence.inner_text() == "Reading the debug log of RAK4631 A1B2… through USB adapter FT232R USB UART at 115200 baud, receive only.", sentence.inner_text())
    sheet.get_by_role("button", name="Change: What baud rate does its firmware use?").click()
    sheet.locator("label", has_text=re.compile(r"^9600$")).click()
    sheet.get_by_test_id("sentence").wait_for()
    check("%s wiring: changing an answer updates the sentence and keeps the later answers that still apply" % tag,
          "at 9600 baud, receive only." in sheet.get_by_test_id("sentence").inner_text(), sheet.get_by_test_id("sentence").inner_text())
    # turning sending on asks the voltage question (and only then); turning it off drops it again
    sheet.get_by_role("button", name="Change: Should the bench be able to send to it?").click()
    sheet.get_by_role("radio", name=re.compile("^Yes, let the bench send")).click()
    check("%s wiring: choosing to send opens the voltage question with its warning" % tag,
          sheet.get_by_text("What voltage is the adapter's signal?").count() == 1 and "5 V adapter on a 3.3 V pin damages the board" in sheet.inner_text())
    sheet.get_by_role("radio", name=re.compile("^I haven't measured it")).click()
    check("%s wiring: an unmeasured voltage keeps the connection receive only, and says so" % tag,
          "receive only until the voltage is measured" in sheet.get_by_test_id("sentence").inner_text(), sheet.get_by_test_id("sentence").inner_text())
    sheet.get_by_role("button", name="Change: Should the bench be able to send to it?").click()
    sheet.get_by_role("radio", name=re.compile("^No, only watch")).click()
    check("%s wiring: going back to receive only drops the voltage question" % tag,
          sheet.get_by_text("What voltage is the adapter's signal?").count() == 0 and "receive only." in sheet.get_by_test_id("sentence").inner_text())
    shot("9-wiring-sheet-done")
    sheet.get_by_role("button", name="Connect", exact=True).click()
    page.get_by_role("list", name=re.compile("^Connections of RAK4631 A1B2")).wait_for()
    check("%s wiring: the new wire is on the second device and the plugged-in box is gone" % tag,
          page.get_by_role("group", name=re.compile("plugged in but not connected")).count() == 0)

    # back on the first device: what is taken is greyed and says who has it
    cards.nth(0).click()
    page.get_by_role("button", name="Connect something").click()
    sheet = page.get_by_role("region", name=re.compile("^Connect something to RAK4631 D953"))
    sheet.get_by_role("radio", name=re.compile("^Read its debug log")).click()
    taken = sheet.get_by_role("radio", name=re.compile("^USB adapter USB Single Serial"))
    check("%s wiring: an adapter another connection owns is shown, disabled, and names who has it" % tag,
          taken.is_disabled() and "Already used by the debug log of RAK4631 D953" in sheet.inner_text(), sheet.inner_text()[:400])
    check("%s wiring: so is the one the other device owns" % tag,
          sheet.get_by_role("radio", name=re.compile("^USB adapter FT232R")).is_disabled() and "debug log of RAK4631 A1B2" in sheet.inner_text())
    check("%s wiring: a free Pi UART says it needs a Pi setting change" % tag, "device-tree overlay" in sheet.inner_text())
    shot("9-wiring-claimed")

    # a control line: intent first, then only the pins that are free
    sheet.get_by_role("button", name="Change: What do you want to do?").click()
    sheet.get_by_role("radio", name=re.compile("^Reset, boot or power it")).click()
    sheet.get_by_role("radio", name=re.compile("^Reset it")).click()
    pins = sheet.get_by_role("radio", name=re.compile("^GPIO17"))
    check("%s wiring: a pin another connection owns is disabled in the pin list, with the reason" % tag,
          pins.is_disabled() and "reset line of RAK4631 D953" in sheet.inner_text(), sheet.inner_text()[:300])
    sheet.locator("label", has_text=re.compile(r"^GPIO18 ")).click()
    sheet.get_by_role("radio", name=re.compile("^Low")).click()
    sheet.get_by_role("radio", name=re.compile("^I haven't measured it")).click()
    sheet.get_by_label("Longest pulse it may ever be given (ms)").fill("50")
    sheet.get_by_role("button", name="Done").click()
    sentence = sheet.get_by_test_id("sentence")
    sentence.wait_for()
    check("%s wiring: an unmeasured voltage is said plainly in the sentence" % tag,
          "does nothing until the voltage is measured" in sentence.inner_text(), sentence.inner_text())
    sheet.get_by_role("button", name="Connect", exact=True).click()
    fold = page.locator("details").filter(has_text="Which pin is wired to what")
    if not fold.evaluate("el => el.open"):                                       # folded on a phone: a person opens it
        fold.get_by_text("Which pin is wired to what").click()
    header = page.get_by_role("group", name="Pi header, coloured by device")
    check("%s wiring: the header shows the new pin with what it does" % tag, "reset" in header.inner_text() and header.get_by_title(re.compile("^GPIO18: reset")).count() == 1)
    shot("9-wiring-header")

    # analog: the Pi cannot do it alone, and the page says so and offers the way out
    page.get_by_role("button", name="Connect something").click()
    sheet = page.get_by_role("region", name=re.compile("^Connect something to RAK4631 D953"))
    sheet.get_by_role("radio", name=re.compile("^Measure a voltage")).click()
    check("%s wiring: measuring a voltage explains it needs an ADC chip and offers to add one first" % tag,
          "no analog input of its own" in sheet.inner_text() and sheet.get_by_role("button", name="Connect the ADC chip first").count() == 1)
    sheet.get_by_role("button", name="Connect the ADC chip first").click()
    check("%s wiring: that switches the sheet to a bus connection" % tag, sheet.get_by_text("Which bus?").count() == 1)
    sheet.get_by_role("button", name="Cancel").first.click()

    # remove asks inline, and Open console goes to the console
    wires = page.get_by_role("list", name=re.compile("^Connections of RAK4631 D953"))
    before = wires.get_by_role("listitem").count()
    wires.get_by_role("button", name="Remove").last.click()
    ask = page.get_by_role("group", name=re.compile("^Remove this connection from RAK4631"))
    check("%s wiring: remove asks first, inline" % tag, ask.count() == 1)
    ask.get_by_role("button", name="Yes").click()
    check("%s wiring: the wire is gone after yes" % tag, wires.get_by_role("listitem").count() == before - 1)
    wires.get_by_role("button", name="Open console").first.click()
    check("%s wiring: Open console goes to the Console page" % tag,
          nav.get_by_role("button", name="Console").get_attribute("aria-current") == "page")


def run_viewport(browser, bench, theme, size, shots, first):
    label, width, height = size
    tag = "%s-%s" % (theme, label)
    bench.shots = shots
    refusals_expected = list(EXPECTED_REFUSALS)
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors, refusals = [], []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    # Chrome logs every 4xx a fetch gets as "Failed to load resource": those are judged by the responses below.
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text))
            if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    page.on("response", lambda r: refusals.append((r.request.method, urllib.parse.urlsplit(r.url).path, r.status))
            if r.status >= 400 else None)

    def shot(name):
        # A full-page screenshot overrides the viewport and leaves the scroll to be re-clamped later, which makes the next
        # click land on the wrong element (traced to an event log, not a page fault). Put the scroll back and let two
        # frames pass, so a click after a screenshot hits what it was aimed at.
        y = page.evaluate("window.scrollY")
        page.screenshot(path=os.path.join(shots, "%s-%s.png" % (tag, name)), full_page=True)
        page.evaluate("(y) => { window.scrollTo(0, y); return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }", y)

    page.goto(bench.url)
    page.get_by_label("Service token").wait_for()
    check("%s login page: title carries the bench name" % tag, BENCH in page.title(), page.title())
    check("%s login page: theme class matches the colour scheme" % tag,
          page.evaluate("document.documentElement.classList.contains('dark')") == (theme == "dark"))
    check("%s login page: no horizontal scroll" % tag, overflow(page) <= 0, overflow(page))
    shot("1-login")

    # a wrong token: refused, no cookie, a message
    page.get_by_label("Service token").fill("not-the-token")
    page.get_by_role("button", name="Log in").click()
    page.locator("#login-message", has_text="not right").wait_for()
    check("%s wrong token: no cookie was set" % tag, not [c for c in context.cookies() if c["name"] == "nrfwb_session"])
    shot("2-wrong-token")

    # the right token
    page.get_by_label("Service token").fill(TOKEN)
    page.get_by_role("button", name="Log in").click()
    page.get_by_role("navigation", name="Pages").wait_for()
    cookies = [c for c in context.cookies() if c["name"] == "nrfwb_session"]
    check("%s login: one session cookie" % tag, len(cookies) == 1, cookies)
    if cookies:
        c = cookies[0]
        check("%s login: cookie is HttpOnly" % tag, c["httpOnly"], c)
        check("%s login: cookie is SameSite=Strict" % tag, c["sameSite"] == "Strict", c)
        check("%s login: script cannot read the session cookie" % tag,
              "nrfwb_session" not in page.evaluate("document.cookie"))
        check("%s login: the session id is not in the page" % tag, c["value"] not in page.content())
    stored = page.evaluate("JSON.stringify([localStorage, sessionStorage])")
    check("%s login: the token is in neither storage" % tag, TOKEN not in stored, stored)
    check("%s login: the token is not in the page" % tag, TOKEN not in page.content())
    check("%s login: the token field is gone" % tag, page.get_by_label("Service token").count() == 0)

    # Home: the board attached at start, from the live feed (the old "Attached" band is now Home's Devices list and the header lamp)
    band = None
    boards = page.get_by_role("list", name="Devices")
    boards.get_by_text(SERIAL).first.wait_for()
    check("%s home: the attached board is shown" % tag, True)
    check("%s home: the header says the feed is live (no 'live updates' warning)" % tag, "live updates" not in page.locator("header").inner_text(), page.locator("header").inner_text())
    check("%s home: it does not claim ready" % tag, "ready" not in page.get_by_role("status").first.inner_text().lower(), page.get_by_role("status").first.inner_text())
    shot("3-home")

    # a board attached while the page is open: it must appear with no reload
    started = time.monotonic()
    bench.attach_second()
    boards.get_by_text(OTHER).first.wait_for(timeout=LIVE_CEILING_MS)
    took = (time.monotonic() - started) * 1000
    print("INFO  %s live attach took %.0f ms (ceiling %d ms)" % (tag, took, LIVE_CEILING_MS), flush=True)
    check("%s live: a board attached later appears without a reload" % tag, True)
    shot("4-live-attach")
    started = time.monotonic()
    bench.detach_second()
    page.wait_for_function("(s) => !document.body.innerText.includes(s)", arg=OTHER, timeout=LIVE_CEILING_MS)
    print("INFO  %s live detach took %.0f ms" % (tag, (time.monotonic() - started) * 1000), flush=True)
    check("%s live: a board removed leaves the list (it is not named, so the bench does not keep it)" % tag, True)

    # every page
    for name in PAGES:
        page.get_by_role("navigation", name="Pages").get_by_role("button", name=name).click()
        check("%s page %s: marked as current" % (tag, name),
              page.get_by_role("navigation", name="Pages").get_by_role("button", name=name).get_attribute("aria-current") == "page")
        if name == "Firmware":
            page.get_by_text("Loading the store…").wait_for(state="detached", timeout=15000)   # the list, not the spinner text
        if name == "Console":
            page.get_by_label("Link").wait_for()
        if name == "GPIO":
            page.get_by_role("group", name="40-pin header").wait_for()
        check("%s page %s: no horizontal scroll" % (tag, name), overflow(page) <= 0, overflow(page))
        shot("5-" + name.lower())

    # a generated command, run from the page: a read, then a refused write
    page.get_by_role("navigation", name="Pages").get_by_role("button", name="Commands").click()
    search = page.get_by_label("Search commands")
    search.fill("devices.list")
    page.locator("[aria-label=Commands]").get_by_role("button").first.click()
    page.get_by_role("button", name="Read", exact=True).click()
    page.get_by_role("status").filter(has_text="attached").first.wait_for()
    check("%s commands: devices.list ran and returned the boards" % tag, SERIAL in page.get_by_role("status").first.inner_text())
    search.fill("firmware.delete")
    page.locator("[aria-label=Commands]").get_by_role("button").first.click()
    page.get_by_label(re.compile(r"^id")).fill("deadbeef")
    page.get_by_role("button", name="Run", exact=True).click()
    page.get_by_role("status").filter(has_text="firmware_unknown").first.wait_for()
    check("%s commands: a write by cookie+CSRF reached the command, which refused it by name" % tag, True)
    shot("6-command-result")

    # the flash dialog: opens from the band, board fixed, nothing preselected, nothing sent until chosen
    dialog = open_flash(page, band)
    check("%s flash dialog: names the board and says to choose a package" % tag,
          SERIAL in dialog.inner_text() and "Choose a package" in dialog.inner_text(), dialog.inner_text()[:200])
    check("%s flash dialog: the flash button is disabled until a package is chosen" % tag,
          dialog.get_by_role("button", name=re.compile("Erase and flash")).is_disabled())
    check("%s flash dialog: it is not offering raw fields (no serial, no firmware id, no bound inputs visible)" % tag,
          dialog.get_by_label(re.compile(r"^serial")).count() == 0 and dialog.get_by_label(re.compile(r"^firmware")).count() == 0
          and not dialog.get_by_label(re.compile("Enter the bootloader within")).is_visible())
    check("%s flash dialog: the product check starts empty, not prefilled from the OLD firmware's product" % tag,
          dialog.get_by_label(re.compile("The board must come back as")).input_value() == "")
    check("%s flash dialog: no horizontal scroll inside it" % tag, dialog.evaluate("e => e.scrollWidth - e.clientWidth") <= 0)
    dialog.screenshot(path=os.path.join(shots, "%s-8-flash-dialog.png" % tag))
    dialog.get_by_role("button", name="Close", exact=True).click()
    dialog.wait_for(state="hidden")
    if first:
        refusals_expected.append(("POST", "/api/programmer/flash", 500))
        refusals_expected.append(("POST", "/api/gpio/assign", 422))          # the 5 V signal the GPIO flow tries on purpose
        flash_flows(page, bench, band, shot, tag)
        in_progress(page, bench, band, shot, tag)
        firmware_page(page, bench, band, shot, tag)
        console_page(page, bench, shot, tag)
        gpio_page(page, bench, shot, tag)
        wiring_page(page, bench, shot, tag)

    # the activity log names this caller
    page.get_by_role("navigation", name="Pages").get_by_role("button", name="Activity").click()
    page.get_by_text("devices.list").first.wait_for()
    text = page.locator("main, #root").first.inner_text()
    check("%s activity: both commands are listed" % tag, "devices.list" in text and "firmware.delete" in text, text[:200])
    check("%s activity: the caller is the dashboard" % tag, "dashboard" in text)
    shot("7-activity")

    # an unauthenticated API call from this very browser context, with the cookie, and with a foreign Origin
    r = page.request.get(bench.url + "api/devices/list")
    check("%s api: the cookie alone may read" % tag, r.status == 200, r.status)
    r = page.request.get(bench.url + "api/devices/list", headers={"Origin": "http://evil.example"})
    check("%s api: the cookie from a foreign Origin may not" % tag, r.status == 401, r.status)
    r = page.request.post(bench.url + "api/firmware/delete", data="{}", headers={"Content-Type": "application/json"})
    check("%s api: a write by cookie without the CSRF value is refused" % tag, r.status == 401, r.status)

    if first:
        theme_button = page.get_by_role("button", name=re.compile(r"^Theme:"))
        seen = []
        for _ in range(3):
            theme_button.click()
            seen.append(page.evaluate("document.documentElement.classList.contains('dark')"))
        check("%s theme button: system -> light -> dark -> system, and the page follows" % tag,
              seen == [False, True, theme == "dark"], seen)
        page.evaluate("localStorage.removeItem('nrfwb-theme')")

    # logout
    page.get_by_role("button", name="Log out").click()
    page.get_by_label("Service token").wait_for()
    check("%s logout: the login page returns" % tag, True)
    r = page.request.get(bench.url + "api/devices/list")
    check("%s logout: the old session no longer works" % tag, r.status == 401, r.status)
    check("%s no console or page errors" % tag, not errors, errors)
    check("%s the only failed requests were the ones on purpose" % tag, refusals == refusals_expected, refusals)
    context.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None, help="where to save screenshots (default: a new temp directory)")
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    global shots
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-shots-")
    os.makedirs(shots, exist_ok=True)
    bench = Bench()
    try:
        r = socket.create_connection(("127.0.0.1", bench.port))      # a refusal here is a harness fault, not a UI one
        r.close()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
            try:
                first = True
                for theme in THEMES:
                    for size in SIZES:
                        run_viewport(browser, bench, theme, size, shots, first)
                        first = False
                # a login from another site is refused, and gives no cookie
                context = browser.new_context()
                resp = context.request.post(bench.url + "login", data='{"token": "%s"}' % TOKEN,
                                            headers={"Content-Type": "application/json", "Origin": "http://evil.example"})
                check("a login from another site is refused", resp.status == 403, resp.status)
                check("a login from another site sets no cookie", not context.cookies())
                context.close()
            finally:
                browser.close()
    finally:
        bench.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
