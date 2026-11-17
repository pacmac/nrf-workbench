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
import math
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))

import uvicorn  # noqa: E402
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
PAGES = ("Bench", "Devices", "Activity", "Commands")

# A live push is uevent -> wake -> read -> publish -> websocket -> render. Measured on this host over six
# browser runs (printed as `live attach took` on every run): 4, 4, 7, 7, 7, 25 ms. The ceiling is a give-up for
# the FAILURE case, never a wait: 3 x the worst measured (25 ms) = 75 ms, rounded up to 100 ms.
LIVE_CEILING_MS = 100

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
        hooks = {"tool": "/fake/adafruit-nrfutil", "watcher": FakeWatcher(),
                 "touch": Touch(on_touch=self._to_bootloader), "runner": Runner(on_run=self._to_app),
                 "ledger": TouchLedger(self.lock_path + ".touches.json")}
        cfg, _ = cfgmod.load(env={}, overrides={
            "paths": {"firmware": os.path.join(self.tmp, "fw")}, "service": {"bench_name": BENCH},
            "streams": {"events": {"retain": 100, "max_age_days": 1}, "activity": {"retain": 100, "max_age_days": 1}}})
        self.app, self.host, _registry, _caps = service.build(cfg, token=TOKEN, sysfs=self.tree.sysfs, devroot=self.tree.dev,
                                                                  proc=self.tree.proc, hooks=hooks)
        self.host.lock_path = self.lock_path
        self.package = os.path.join(self.tmp, "package.zip")
        with open(self.package, "wb") as fh:
            fh.write(pkg())
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

    def _to_bootloader(self):
        self.tree.unplug("1-3")
        self.tree.bootloader_board()
        self.poke_w.send(b"x")

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
        self.monitor.stop()
        for s in (self.poke_r, self.poke_w):
            s.close()
        self.tree.close()


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def open_flash(page, band):
    band.get_by_role("button", name="Flash…").first.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    return dialog


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
    band.locator("ul").get_by_text(SERIAL).first.wait_for()
    check("%s flash by upload: the board is back in the live band" % tag, band.locator("ul").get_by_text("app", exact=True).count() >= 1)

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
        page.screenshot(path=os.path.join(shots, "%s-%s.png" % (tag, name)), full_page=True)

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

    # the band: the board attached at start, from the live feed
    band = page.locator("section", has=page.get_by_role("heading", name="Attached"))
    band.get_by_text(SERIAL).first.wait_for()
    check("%s band: the attached board is shown" % tag, True)
    band.get_by_text("live", exact=True).first.wait_for()
    check("%s band: the feed says live" % tag, True)
    check("%s band: it does not claim ready" % tag, "ready" not in band.inner_text().lower(), band.inner_text())
    shot("3-band")

    # a board attached while the page is open: it must appear with no reload
    boards = band.locator("ul")                  # the board list; the ticker is an <ol> and keeps history
    started = time.monotonic()
    bench.attach_second()
    boards.get_by_text(OTHER).first.wait_for(timeout=LIVE_CEILING_MS)
    took = (time.monotonic() - started) * 1000
    print("INFO  %s live attach took %.0f ms (ceiling %d ms)" % (tag, took, LIVE_CEILING_MS), flush=True)
    check("%s live: a board attached later appears without a reload" % tag, True)
    check("%s live: the ticker names the change" % tag, band.get_by_text(re.compile("attached:.*" + OTHER)).count() >= 1)
    shot("4-live-attach")
    started = time.monotonic()
    bench.detach_second()
    boards.get_by_text(OTHER).first.wait_for(state="detached", timeout=LIVE_CEILING_MS)
    print("INFO  %s live detach took %.0f ms" % (tag, (time.monotonic() - started) * 1000), flush=True)
    check("%s live: a board removed disappears from the list" % tag, True)

    # every page
    for name in PAGES:
        page.get_by_role("navigation", name="Pages").get_by_role("button", name=name).click()
        check("%s page %s: marked as current" % (tag, name),
              page.get_by_role("navigation", name="Pages").get_by_role("button", name=name).get_attribute("aria-current") == "page")
        check("%s page %s: no horizontal scroll" % (tag, name), overflow(page) <= 0, overflow(page))
        shot("5-" + name.lower())

    # a generated command, run from the page: a read, then a refused write
    page.get_by_role("navigation", name="Pages").get_by_role("button", name="Commands").click()
    page.get_by_role("button", name=re.compile(r"^devices\.list")).click()
    page.get_by_role("button", name="Read", exact=True).click()
    page.get_by_role("status").filter(has_text="attached").first.wait_for()
    check("%s commands: devices.list ran and returned the boards" % tag, SERIAL in page.get_by_role("status").first.inner_text())
    page.get_by_role("button", name=re.compile(r"^firmware\.delete")).click()
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
        flash_flows(page, bench, band, shot, tag)

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
