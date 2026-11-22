#!/usr/bin/env python3
"""The Meter page's supply controls and their guards, in a real browser (docs/specs/meter-page.md, "Bench layout"; docs/specs/ppk2.md, "Source mode and the supply switch are gated").

    python3 scripts/verify_supply_controls.py [--shots DIR] [--chrome PATH]

A real service (real routes, real built UI) over a fake sysfs with a PPK2 and a FAKE UNIT that speaks the protocol and records every byte it is sent. In light and dark at 1280x720, the unit in
source mode with its output not known (it does not report it):
  confirm   Switch on and Leave source mode each ask in a sentence and send the unit nothing until it is accepted; Cancel sends nothing; pressing the mode it is in does nothing;
  writes    accepting Switch on sends the voltage and then the output; Switch off asks nothing; a mode change with the output off asks nothing; the unit is told what the page chose;
  guard     the SERVICE refuses an unconfirmed mode change and an unconfirmed switch-on by itself (409 supply_unconfirmed, nothing sent), and a voltage outside 800 to 5000 (422), whatever the page does;
  locked    while a session holds the unit every control is disabled and the strip says why;
  safety    nothing the page did made the service write a byte outside the six shapes, and the shell port was never opened.
Exit 0 only if every check passed.
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

from playwright.sync_api import sync_playwright  # noqa: E402

from navhelp import go_to  # noqa: E402
import verify_ppk2_live as base  # noqa: E402
from nrf_workbench.instruments import ppk2  # noqa: E402

THEMES = ("light", "dark")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + str(detail)) if detail and not ok else ""), flush=True)
    return bool(ok)


def supply(page):
    return page.get_by_role("region", name="Supply")


def sent_since(bench, mark):
    return bench.unit.writes[mark:]


def kinds(writes):
    return [w[0] for w in writes if w[0] in (ppk2.MODE, ppk2.VOLTAGE, ppk2.POWER)]


def the_confirms(page, bench, tag):
    sup = supply(page)
    check("%s the unit is in source mode with its output not known" % tag, sup.get_by_role("button", name="Source mode").get_attribute("aria-pressed") == "true" and "Output state unknown" in sup.inner_text(), sup.inner_text()[:120])
    mark = len(bench.unit.writes)
    sup.get_by_role("button", name="Source mode").click()
    check("%s pressing the mode it is already in sends the unit nothing" % tag, kinds(sent_since(bench, mark)) == [], sent_since(bench, mark))

    sup.get_by_role("button", name="Switch on").click()
    dialog = sup.get_by_role("alertdialog", name="Confirm the supply change")
    dialog.wait_for()
    check("%s Switch on asks in a sentence that names the voltage (%s)" % (tag, dialog.inner_text().split("\n")[0][:40]), re.search(r"Apply \d\.\d{3} V to the board\?", dialog.inner_text()) is not None, dialog.inner_text())
    check("%s and has sent the unit nothing yet" % tag, kinds(sent_since(bench, mark)) == [] and bench.unit.power is None, sent_since(bench, mark))
    dialog.get_by_role("button", name="Cancel").click()
    dialog.wait_for(state="detached")
    check("%s Cancel sends the unit nothing and the output is still not known" % tag, kinds(sent_since(bench, mark)) == [] and bench.unit.power is None)

    sup.get_by_role("button", name="Ampere mode").click()
    dialog.wait_for()
    check("%s leaving source mode with the output unknown asks, and says it can cut the supply" % tag, "can cut the board's supply" in dialog.inner_text(), dialog.inner_text())
    check("%s and has sent the unit nothing yet" % tag, kinds(sent_since(bench, mark)) == [])
    dialog.get_by_role("button", name="Cancel").click()
    dialog.wait_for(state="detached")
    check("%s Cancel leaves it in source mode and sends nothing" % tag, kinds(sent_since(bench, mark)) == [] and sup.get_by_role("button", name="Source mode").get_attribute("aria-pressed") == "true")


def the_guard(page, bench, tag):
    """The service's own refusals, made without the page: the page's sentence is a courtesy, never the guard."""
    root = bench.url.rstrip("/")
    mark = len(bench.unit.writes)
    r = page.request.post(root + "/api/ppk2/mode", data=json.dumps({"mode": "ampere"}), headers={"Content-Type": "application/json"})
    body = r.json()
    check("%s the service refuses an unconfirmed mode change by itself (%s, %s)" % (tag, r.status, body.get("code")), r.status == 409 and body.get("code") == "supply_unconfirmed", body)
    r = page.request.post(root + "/api/ppk2/supply", data=json.dumps({"on": True, "mv": 3300}), headers={"Content-Type": "application/json"})
    body = r.json()
    check("%s the service refuses an unconfirmed switch-on by itself (%s, %s)" % (tag, r.status, body.get("code")), r.status == 409 and body.get("code") == "supply_unconfirmed", body)
    r = page.request.post(root + "/api/ppk2/supply", data=json.dumps({"on": True, "mv": 12, "accept_supply": True}), headers={"Content-Type": "application/json"})
    body = r.json()
    check("%s and a voltage outside 800 to 5000 even when accepted (%s, %s)" % (tag, r.status, body.get("code")), r.status == 422 and body.get("code") == "voltage_unsafe", body)
    check("%s none of the three sent the unit anything" % tag, kinds(sent_since(bench, mark)) == [], sent_since(bench, mark))


def the_writes(page, bench, tag):
    sup = supply(page)
    sup.get_by_label("Source voltage in millivolts").fill("3300")
    mark = len(bench.unit.writes)
    sup.get_by_role("button", name="Switch on").click()
    sup.get_by_role("alertdialog", name="Confirm the supply change").get_by_role("button", name="Switch on").click()
    sup.get_by_text("Output on").first.wait_for()
    check("%s accepting Switch on sends the voltage and then the output" % tag, kinds(sent_since(bench, mark)) == [ppk2.VOLTAGE, ppk2.POWER] and bench.unit.power is True, sent_since(bench, mark))
    readout = sup.get_by_label("Source voltage", exact=True).inner_text()
    check("%s the strip says output on at 3.300 V" % tag, readout.replace(" ", "").startswith("3.300"), readout)

    mark = len(bench.unit.writes)
    sup.get_by_role("button", name="Ampere mode").click()
    dialog = sup.get_by_role("alertdialog", name="Confirm the supply change")
    dialog.wait_for()
    check("%s leaving source mode with the output on asks" % tag, kinds(sent_since(bench, mark)) == [])
    dialog.get_by_role("button", name="Leave source mode").click()
    sup.get_by_text("Monitoring").first.wait_for()
    check("%s accepting it sends the mode change and then there is no voltage or output to offer" % tag,
          kinds(sent_since(bench, mark)) == [ppk2.MODE] and sup.get_by_label("Source voltage in millivolts").count() == 0 and sup.get_by_role("group", name="Output").count() == 0, sent_since(bench, mark))

    mark = len(bench.unit.writes)
    sup.get_by_role("button", name="Source mode").click()
    sup.get_by_role("group", name="Output").wait_for()
    check("%s going back to source mode asks nothing and sends the mode" % tag, kinds(sent_since(bench, mark)) == [ppk2.MODE] and sup.get_by_role("alertdialog").count() == 0, sent_since(bench, mark))

    mark = len(bench.unit.writes)
    sup.get_by_role("button", name="Switch off").click()
    sup.get_by_text("Output off").first.wait_for()
    check("%s Switch off asks nothing and sends the output off" % tag, kinds(sent_since(bench, mark))[-1:] == [ppk2.POWER] and bench.unit.power is False and sup.get_by_role("alertdialog").count() == 0, sent_since(bench, mark))

    mark = len(bench.unit.writes)
    sup.get_by_role("button", name="Ampere mode").click()
    sup.get_by_text("Monitoring").first.wait_for()
    check("%s with the output off, leaving source mode asks nothing" % tag, kinds(sent_since(bench, mark)) == [ppk2.MODE] and sup.get_by_role("alertdialog").count() == 0, sent_since(bench, mark))
    sup.get_by_role("button", name="Source mode").click()
    sup.get_by_role("group", name="Output").wait_for()

    sup.get_by_label("Source voltage in millivolts").fill("12")
    check("%s a voltage outside 800 to 5000 cannot be switched on from the page" % tag, sup.get_by_role("button", name="Switch on").is_disabled())
    sup.get_by_label("Source voltage in millivolts").fill("3300")


def the_lock(page, bench, tag):
    sup = supply(page)
    page.get_by_role("button", name="Watch", exact=True).click()
    page.get_by_text(re.compile(r"^watching, .* samples")).wait_for()
    controls = ["Switch off", "Switch on", "Source mode", "Ampere mode", "Lower by 100 millivolts", "Raise by 100 millivolts"]
    enabled = [n for n in controls if sup.get_by_role("button", name=n).count() and not sup.get_by_role("button", name=n).is_disabled()]
    check("%s while a session holds the unit every supply control is disabled (%s enabled)" % (tag, enabled), enabled == [] and sup.get_by_label("Source voltage in millivolts").is_disabled(), enabled)
    check("%s and the strip says why" % tag, "Held by the running capture" in sup.inner_text(), sup.inner_text()[:160])
    page.get_by_role("button", name="Stop").click()
    page.get_by_text(re.compile(r"^ended: stopped")).wait_for()
    sup.get_by_role("button", name="Switch off").wait_for(state="visible")
    page.wait_for_function("() => !document.querySelector('[aria-label=\"Switch off\"]').disabled")
    check("%s when it ends the controls are back" % tag, not sup.get_by_role("button", name="Switch off").is_disabled())


def run(browser, theme, shots):
    tag = "%s 1280x720" % theme
    bench = base.Bench(with_meter=False)
    context = browser.new_context(viewport={"width": 1280, "height": 720}, color_scheme=theme)
    page = context.new_page()
    page.set_default_timeout(20000)
    errors, bad = [], []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" and not m.text.startswith("Failed to load resource") else None)
    try:
        page.goto(bench.url)
        page.get_by_role("navigation", name="Pages").wait_for(state="attached")
        go_to(page, "Meter")
        supply(page).get_by_role("button", name="Source mode").wait_for()
        supply(page).get_by_text("Output state unknown").first.wait_for()           # the state and the unit's own sentence both say it
        page.screenshot(path=os.path.join(shots, "supply-%s.png" % theme))
        the_confirms(page, bench, tag)
        the_guard(page, bench, tag)
        the_writes(page, bench, tag)
        the_lock(page, bench, tag)
        wrong = [w for w in bench.unit.writes if not (w in base.ALLOWED or (len(w) == 3 and w[0] == ppk2.VOLTAGE))]
        check("%s nothing the page did made the service write a byte outside the six shapes" % tag, not wrong, wrong)
        check("%s the shell port was never opened" % tag, all(n.endswith("ttyACM1") for n in bench.opened), bench.opened)
        check("%s no page or console errors" % tag, not errors, errors)
    except Exception as exc:                                    # a missing control is a failed check, not a crash
        check("%s the page could be driven" % tag, False, str(exc).splitlines()[0])
    finally:
        context.close()
        bench.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = ap.parse_args(argv)
    shots = args.shots or tempfile.mkdtemp(prefix="nrfwb-supply-controls-")
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
