#!/usr/bin/env python3
"""Drive the ESP32 parts of the dashboard in a real browser (docs/specs/esp32.md, step "VERIFY the Firmware page ...").

    python3 scripts/verify_esp32_live.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI, real broker, real store) on a loopback port over a FAKE sysfs with one CP2102-style
adapter and the bench's own dongle, a pseudo-terminal as the ESP32's port, and a FAKE esptool (the real one needs a chip, and
no ESP32 is attached to this bench yet). For light and dark at phone and desktop width: nothing is an ESP32 until it is declared;
the declared board gets a receive-only console; a whole-flash image and a bundle with its ELF are uploaded and told apart; the
flash dialog derives its bound and sends the command esptool would get; Identify and Reset run; the console is released for a run and
retaken; and a crash dump printed by the board is decoded to function names. Exit 0 only if every check passed.
"""
import argparse
import os
import re
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))
sys.path.insert(0, os.path.join(ROOT, "tests", "esp32"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from nrf_workbench import service  # noqa: E402
from nrf_workbench.core import config as cfgmod  # noqa: E402
from nrf_workbench.devices import commands as device_commands  # noqa: E402
from nrf_workbench.devices.monitor import DeviceMonitor  # noqa: E402
from fakes import SERIAL, Tree  # noqa: E402
from test_decode import make_elf  # noqa: E402
from test_esp32 import bundle, merged  # noqa: E402

BRIDGE = ("10c4", "ea60")
DONGLE = ("1a86", "55d4")
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
        self.tmp = tempfile.mkdtemp(prefix="nrfwb-esp32-")
        self.tree.usb("1-2", BRIDGE, "CP2102 USB to UART Bridge Controller", serial="0001", manufacturer="Silicon Labs", tty="ttyUSB0", majmin="188:0")
        self.tree.node("ttyUSB0", 188, 0)
        # a second adapter of the same kind reporting the SAME serial, as cheap bridge chips do: neither can be told apart by it
        self.tree.usb("1-3", BRIDGE, "CP2102 USB to UART Bridge Controller", serial="0001", manufacturer="Silicon Labs", tty="ttyUSB1", majmin="188:1")
        self.tree.node("ttyUSB1", 188, 1)
        self.tree.usb("1-4", DONGLE, "USB Single Serial", serial="5B1F007437", manufacturer="QinHeng", tty="ttyACM1", majmin="166:1")
        self.tree.node("ttyACM1", 166, 1)
        self.tree.app_board("1-6", "ttyACM3", "166:3", serial=SERIAL)        # an nRF board, to be named on the Devices page
        self.master, self.slave = os.openpty()
        self.node = os.ttyname(self.slave)
        self.runs = []
        cfg, problems = cfgmod.load(env={}, overrides={
            "paths": {"firmware": os.path.join(self.tmp, "fw"), "wiring": os.path.join(self.tmp, "wiring"),
                      "console": os.path.join(self.tmp, "console"), "lock": os.path.join(self.tmp, "lock")},
            "streams": {"events": {"retain": 50, "max_age_days": 1}, "activity": {"retain": 50, "max_age_days": 1},
                        "console": {"retain": 500, "max_age_days": 1}}})
        assert problems == [], problems
        self.uev_a, self.uev_b = socket.socketpair()
        hooks = {"tool": "/fake/uploader", "esp_tool": "/fake/esptool", "esp_runner": self.runner, "open_uevents": lambda: self.uev_a,
                 "console_resolve": lambda link: ({"device": {}, "node": self.node}, "")}
        self.app, self.host, _r, _c = service.build(cfg, sysfs=self.tree.sysfs, devroot=self.tree.dev, proc=self.tree.proc, hooks=hooks)
        self.host.console.start()
        # what service.main starts: the monitor that publishes every USB change on the events stream (its own uevent socket)
        self.mon_a, self.mon_b = socket.socketpair()
        self.monitor = DeviceMonitor(lambda: device_commands.boards(self.host), self.host.streams.events.publish,
                                     lambda: self.mon_a, read_usb=lambda: service.usb_level(self.host.sysfs, self.host.devroot))
        self.monitor.start()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.url = "http://127.0.0.1:%d/" % self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True).start()

    def runner(self, cmd, bound):
        """The fake esptool: what the real one prints for a good write and a good chip-id."""
        during = self.host.console.state("bench-esp")
        self.runs.append((cmd, bound, during["state"] if during else None))
        head = "esptool v5.4.0\nChip type:          ESP32-D0WD-V3 (revision v3.1)\nMAC: 24:6f:28:aa:bb:cc\n"
        if "write-flash" in cmd:
            pairs = (len(cmd) - (cmd.index("write-flash") + 3)) // 2
            return 0, head + "Hash of data verified.\n" * pairs + "Hard resetting via RTS pin...\n", 1.5, False
        return 0, head + "Hard resetting via RTS pin...\n", 1.0, False

    def close(self):
        self.server.should_exit = True
        self.monitor.stop()
        self.host.console.stop()
        for s in (self.uev_a, self.uev_b, self.mon_b):
            s.close()
        for fd in (self.master, self.slave):
            os.close(fd)
        self.tree.close()


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def run(browser, theme, size, shots):
    label, width, height = size
    tag = "%s-%s" % (theme, label)
    bench = Bench()
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)

    def shot(name):
        y = page.evaluate("window.scrollY")
        page.screenshot(path=os.path.join(shots, "%s-%s.png" % (tag, name)), full_page=True)
        page.evaluate("(y) => { window.scrollTo(0, y); return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); }", y)

    try:
        page.goto(bench.url)
        nav = page.get_by_role("navigation", name="Pages")
        nav.wait_for()
        nav.get_by_role("button", name="Firmware").click()
        card = page.locator("section", has=page.get_by_role("heading", name="ESP32 boards"))
        card.wait_for()
        card.get_by_text("No ESP32 declared yet").wait_for()
        check("%s nothing is an ESP32 until it is declared" % tag, bench.host.console.states() == [])
        adapter = card.get_by_label("Adapter")
        options = adapter.locator("option").all_inner_texts()
        check("%s the adapters sharing a serial are offered by their USB ports, the one with its own serial by that" % tag,
              any("CP2102" in o and "USB port 1-2" in o for o in options) and any("CP2102" in o and "USB port 1-3" in o for o in options)
              and any("USB Single Serial (5B1F007437)" in o for o in options), options)
        check("%s no horizontal scroll" % tag, overflow(page) <= 0, overflow(page))
        shot("1-start")

        # declare
        adapter.select_option(index=[i for i, o in enumerate(options) if "CP2102" in o][0])
        card.get_by_label("Board name").fill("Bench-ESP")
        check("%s the name is lower-cased as it is typed" % tag, card.get_by_label("Board name").input_value() == "bench-esp")
        card.get_by_role("button", name="Declare board").click()
        row = card.get_by_role("list", name="ESP32 boards")
        row.wait_for()
        row.get_by_text("attached").wait_for()
        check("%s the declared board is listed and attached" % tag, "bench-esp" in row.inner_text() and "USB port 1-2" in row.inner_text(), row.inner_text())
        st = bench.host.console.state("bench-esp")
        check("%s its console is a receive-only link on its own bridge, open" % tag,
              st and st["state"] == "open" and st["rx_only"] and st["own_bridge"] and st["adapter"]["slot"] == "1-2", st)
        shot("2-declared")
        nav.get_by_role("button", name="Home", exact=True).click()
        home = page.get_by_role("list", name="Devices").first
        home.get_by_text("bench-esp").first.wait_for()
        check("%s Home lists the declared board as an ESP32 board, connected, and not as an nRF board" % tag,
              "ESP32 board" in home.inner_text() and "bench-esp" in home.inner_text()
              and home.get_by_role("img", name="connected").count() >= 1, home.inner_text())
        nav.get_by_role("button", name="Firmware", exact=True).click()
        card.get_by_role("list", name="ESP32 boards").wait_for()

        # packages: a whole-flash image and a bundle with its ELF are told apart
        page.get_by_label("New project name").fill("esp")
        page.get_by_role("button", name="Create project").click()
        page.get_by_role("heading", name="esp").wait_for()
        for name, data, mime in (("whole.bin", merged(), "application/octet-stream"),
                                 ("app.zip", bundle(elf_bytes=make_elf()), "application/zip")):
            page.get_by_label("Package file").set_input_files({"name": name, "mimeType": mime, "buffer": data})
            page.get_by_role("button", name="Upload", exact=True).click()
            page.get_by_role("list", name="Packages in esp").get_by_text(name).wait_for()
        pkgs = page.get_by_role("list", name="Packages in esp").inner_text()
        check("%s the image and the bundle are described as what they are" % tag,
              "ESP32 whole-flash image" in pkgs and "ESP32 bundle, 3 parts, esp32, with its ELF" in pkgs and "packets" not in pkgs, pkgs)
        check("%s an ESP32 package offers the ESP32 flash, not the nRF one" % tag,
              page.get_by_role("button", name="Flash whole.bin to an ESP32").is_enabled() and page.get_by_role("button", name="Flash whole.bin", exact=True).count() == 0)
        shot("3-packages")

        # flash: the dialog derives its bound; the command is what esptool would get; the console is let go and retaken
        page.get_by_role("button", name="Flash whole.bin to an ESP32").click()
        dialog = page.get_by_role("alertdialog")
        dialog.wait_for()
        bound = dialog.get_by_label("ESP32 flash bound in seconds").input_value()
        check("%s the flash dialog derives the bound: 2 x 32832 x 10 / 115200 + 12 = 17.7, so 18" % tag, bound == "18", bound)
        check("%s nothing is erased by merely opening it" % tag, bench.runs == [])
        shot("4-flash-dialog")
        dialog.get_by_role("button", name="Erase and flash bench-esp").click()
        page.get_by_text("written, every part verified by esptool").wait_for()
        cmd, ceiling, console_during = bench.runs[0]
        check("%s the command is esptool write-flash of the whole image at 0x0, with the derived ceiling" % tag,
              "write-flash" in cmd and cmd[cmd.index("write-flash") + 3] == "0x0" and ceiling == 18.0 and "--baud" in cmd and "115200" in cmd, (cmd, ceiling))
        check("%s the console let the adapter go for the run and has it again" % tag,
              console_during == "held" and bench.host.console.state("bench-esp")["state"] == "open", (console_during, bench.host.console.state("bench-esp")["state"]))
        shot("5-flashed")

        # rename: the name is the person's label, changed in place, and the console link follows it
        card.get_by_role("button", name="Rename bench-esp").click()
        field = card.get_by_label("New name for bench-esp")
        field.fill("TimerCam")
        check("%s the new name is lower-cased as it is typed" % tag, field.input_value() == "timercam")
        card.get_by_role("button", name="Save name").click()
        card.get_by_role("list", name="ESP32 boards").get_by_text("timercam").wait_for()
        names = [s["name"] for s in bench.host.console.states()]
        check("%s renaming moved the console link to the new name and kept the adapter" % tag,
              names == ["timercam"] and bench.host.console.state("timercam")["adapter"]["slot"] == "1-2", names)
        card.get_by_role("button", name="Rename timercam").click()
        card.get_by_label("New name for timercam").fill("bench-esp")
        card.get_by_role("button", name="Save name").click()
        card.get_by_role("list", name="ESP32 boards").get_by_text("bench-esp").wait_for()

        # identify and reset
        row = card.get_by_role("list", name="ESP32 boards")
        row.get_by_role("button", name="Identify bench-esp").click()
        page.get_by_text("the chip answered").wait_for()
        check("%s Identify reads the chip and the mac" % tag, "ESP32-D0WD-V3" in page.inner_text("body") and "24:6f:28:aa:bb:cc" in page.inner_text("body"))
        row.get_by_role("button", name="Hold bench-esp in bootloader").click()
        page.get_by_text("waiting").first.wait_for()
        cmd = bench.runs[-1][0]
        check("%s Hold in bootloader leaves the chip waiting (--after no-reset)" % tag, cmd[cmd.index("--after") + 1] == "no-reset", cmd)
        row.get_by_role("button", name="Reset bench-esp").click()
        page.get_by_text("the board was reset and is running its application").wait_for()
        cmd = bench.runs[-1][0]
        check("%s Reset runs the application (--after hard-reset)" % tag, cmd[cmd.index("--after") + 1] == "hard-reset", cmd)

        # the console and the crash decoder
        nav.get_by_role("button", name="Console").click()
        page.get_by_text("The ESP32's own USB adapter").wait_for()
        check("%s the console says in one line that opening this port can reset some boards" % tag, "opening it can reset some boards" in page.inner_text("body"))
        os.write(bench.master, b"Guru Meditation Error: Core 0 panic'ed (LoadProhibited)\nBacktrace: 0x400d1004:0x3ffb0000 0x400d1044:0x3ffb0000\n")
        page.get_by_text("Backtrace: 0x400d1004").wait_for()
        page.get_by_role("button", name="Open").click()
        decode = page.locator("section", has=page.get_by_role("heading", name="Decode a crash"))
        select = decode.get_by_label("Firmware to decode against")
        select.wait_for()
        decode.get_by_role("option").nth(1).wait_for(state="attached")
        select.select_option(index=1)
        check("%s the dump starts as the board's last lines" % tag, "0x400d1004" in decode.get_by_label("Crash dump").input_value())
        family = page.evaluate("(el) => getComputedStyle(el).fontFamily", decode.get_by_label("Crash dump").element_handle())
        check("%s the crash dump is in a monospace face (an unlayered textarea rule once beat the utility)" % tag, "mono" in family.lower(), family)
        shot("6-decode")
        decode.get_by_role("button", name="Decode").click()
        decode.get_by_role("status").first.wait_for()
        body = decode.inner_text()
        check("%s the addresses come back as functions with the offset, from the ELF alone" % tag,
              "app_main" in body and "do_crash +0x4" in body and "No file and line" in body, body)
        check("%s no horizontal scroll on the console" % tag, overflow(page) <= 0, overflow(page))
        shot("7-decoded")

        # forgetting takes the console link away
        nav.get_by_role("button", name="Firmware").click()
        page.locator("section", has=page.get_by_role("heading", name="ESP32 boards")).get_by_role("button", name="Forget bench-esp").click()
        page.get_by_text("No ESP32 declared yet").wait_for()
        check("%s forgetting the board takes its console link with it" % tag, bench.host.console.states() == [], bench.host.console.states())
        shot("8-forgotten")

        # an nRF board is named by its serial, in a JSON file, from the Devices page
        nav.get_by_role("button", name="Devices", exact=True).click()
        devs = page.get_by_role("list", name="Devices")
        devs.get_by_role("button", name=re.compile(SERIAL)).click()
        page.get_by_role("button", name="Name %s" % SERIAL).click()
        field = page.get_by_label("Name for %s" % SERIAL)
        field.fill("Left-RAK")
        check("%s the board's name is lower-cased as it is typed" % tag, field.input_value() == "left-rak")
        page.get_by_role("button", name="Save name").click()
        devs.get_by_text("left-rak").first.wait_for()
        path = os.path.join(bench.tmp, "wiring", "device-names.json")
        with open(path) as fh:
            saved = fh.read()
        check("%s the name is kept against the serial in a plain JSON file" % tag, '"%s": "left-rak"' % SERIAL in saved, saved)
        shot("8b-named")
        page.get_by_role("button", name="Rename %s" % SERIAL).click()
        page.get_by_label("Name for %s" % SERIAL).fill("right-rak")
        page.get_by_role("button", name="Save name").click()
        devs.get_by_text("right-rak").first.wait_for()
        page.get_by_role("button", name="Remove the name of %s" % SERIAL).click()
        devs.get_by_text("right-rak").first.wait_for(state="detached")
        with open(path) as fh:
            check("%s removing the name takes it out of the file" % tag, SERIAL not in fh.read())

        # a built-in-USB ESP32 plugged in later is found on "Look again" and is not an ESP32 until declared
        bench.tree.usb("1-5", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8:33:85:4A:A4", manufacturer="Espressif", tty="ttyACM2", majmin="166:2")
        bench.tree.node("ttyACM2", 166, 2)
        nav.get_by_role("button", name="Home", exact=True).click()
        home = page.get_by_role("list", name="Devices").first
        bench.mon_b.send(b"x")                                          # the kernel's word that something was plugged: no click, no reload
        home.get_by_text("nobody has declared").first.wait_for()
        check("%s plugging it in shows it on Home at once, with no refresh, as an ESP32 that needs attention" % tag,
              home.get_by_role("img", name="needs attention").count() == 1)
        bench.tree.unplug("1-5")
        bench.mon_b.send(b"x")
        home.get_by_text("nobody has declared").first.wait_for(state="detached")
        check("%s unplugging it takes it away at once, with no refresh" % tag, True)
        bench.tree.usb("1-5", ("303a", "1001"), "USB JTAG/serial debug unit", serial="64:E8:33:85:4A:A4", manufacturer="Espressif", tty="ttyACM2", majmin="166:2")
        bench.mon_b.send(b"x")
        home.get_by_text("nobody has declared").first.wait_for()
        nav.get_by_role("button", name="Firmware", exact=True).click()
        page.get_by_label("Adapter").locator("option", has_text="built-in USB").wait_for(state="attached")      # the list loads when the page opens
        check("%s the declare form offers it too" % tag,
              any("64:E8:33:85:4A:A4" in o and "built-in USB" in o for o in page.get_by_label("Adapter").locator("option").all_inner_texts()),
              page.get_by_label("Adapter").locator("option").all_inner_texts())
        shot("9-undeclared")
        check("%s no page or console errors" % tag, not errors, errors)
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-esp32-shots-")
    os.makedirs(shots, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chrome, headless=True, args=["--no-sandbox"])
        try:
            for theme in THEMES:
                for size in SIZES:
                    run(browser, theme, size, shots)
        finally:
            browser.close()
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed. screenshots: %s" % (len(results), len(bad), shots))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
