# Navigation: no scrolling tab row, no wasted header height

Peter, 2026-11-18: "you need to do something about the top nav menus, scrolling menus on a phone is a bad design, and on desktop version we
are wasting a lot of vertical space". Amends `dashboard-redesign.md` (which required only that the tabs not wrap).

## Defects

1. **Phone: ten tabs in a sideways-scrolling row.** The current page can be off screen and the rest are hidden until swiped.
2. **Desktop: about 130 px of sticky header** (name and buttons, host line, tab row) above every page, on every page.

## Decisions [derived]

- **One `<nav aria-label="Pages">`, restyled by width** (one DOM, so there is one set of buttons for tests and for assistive technology).
- **Desktop (768 px and up): a left sidebar**, sticky, full height: the bench name, the host line, every page as a vertical list, and at
  the foot the Theme and Log out buttons. The content has the whole height. Nothing scrolls sideways.
- **Phone: a bottom bar** (thumb reach) with the four daily pages and **More**. More opens a list above the bar holding the other pages,
  then Theme and Log out. Nothing scrolls: ten pages are four buttons and a list. A slim top strip shows the lamp, the name and the host.
  - Daily pages are the **first four of the pages this host has**, in the order Home, Devices, Projects, Console, Firmware, Wiring, GPIO,
    Activity, Commands, Help. Everything after the fourth is under More. A page absent on this host (Console without the broker, GPIO off
    a Pi, Projects without the wiring folder) is left out, so the next one moves up and the bar never has a hole. The desktop sidebar lists
    the same order.
  - When the open page is under More, More is the current item.
  - The list closes on choosing a page, on Escape, and on a second press of More. Opened and closed by the user only: no timer.
- A touch target is at least 44 px tall in both layouts. The bar sits on the safe-area inset (notches, home indicators).
- No new dependency; the list is a plain disclosure (Radix stays in Firmware.jsx only).

## Full-height pages (2026-11-21)

Peter, 2026-11-21: "firstly it's a dashboard and dashboards dont scroll", and the whole dashboard "needs to be redesigned to match that format which is full width and full height". `App.jsx` holds a list, `FULL_PAGES`; a page on it, from 768 px up:
- is not held to `max-w-6xl`: it fills the width beside the sidebar (the sidebar 208 px, 16 px of padding either side, a 16 px gap);
- owns the window's height (`h-dvh`) and `overflow-hidden`, so the document cannot scroll; the page lays itself out in that height (a flex column, `min-h-0`), and a list shows the rows that fit and pages;
- on a phone keeps the page flow (it scrolls) until it has its own narrow layout.
A page joins the list when it has been laid out to fit, one page at a time. **Meter is the first** (`meter-page.md`, "Bench layout"). The others (Projects, Wiring) are not yet formally full-height pages: they fit with today's data on the real bench and have no layout that keeps them fitting as it grows. Measured on the real bench at 1280x720 on 2026-11-21 (page height against the 720 px window): Activity 11,205, Home 1,293, Console 1,204, GPIO 1,158, Firmware 964, Help 834, Commands a 432 px scroller holding 3,654 px; Devices, Projects and Wiring fit as they stand.

**Activity (built 2026-11-21, task `gap-activity-page-fits`)** is the second. From 768 px up it is a full-height page: the header, the class filter, and a list of the entries that **fit** the height, newest first, with a pager (`N to M of T`, Previous and Next entries). A row is fixed at two lines: the class, the code (when it differs), the command, its arguments in one short line (the whole of them in the row's tooltip) and the time; then the message and who ran it and how long it took. The count that fits is measured from the list's own height (`FitList`, a ResizeObserver, no timer), so a taller window shows more. On a phone the list flows as before, with Show more. Browser check: `scripts/verify_activity_layout.py`.

**Console (built 2026-11-21, task `gap-console-page-fits`)** is the third. From 768 px up: the link chips and the link choice in one row; the log as the newest lines that fit, **one row each** (a long line is cut to the row and its whole text is in the strip below when the row is picked, since a line like a board's SLEEPNOW is wanted whole); the input at the foot. The log **follows live** ("Following") until the person pages back with Older lines; a page back **stays put** when lines arrive (its place is a line index from the start, not from the end), and Latest follows again. The count is `N to M of T`. **Macros** and **Decode a crash** open in drawers over the page, closed by Escape: a drawer is the one place a page may scroll (it holds an editable form). A phone keeps the flowing page. Browser check: `scripts/verify_console_layout.py`, over two pseudo-terminal links with 150 lines logged.

**Home (built 2026-11-21, task `gap-home-page-fits`)** is the fourth: one screen, from 768 px up. The sentence ("is the bench alright?") on top, then three columns. **Devices and Projects**; **Connections and Recent activity**; **The Pi and Instruments**. Each list is a `FitList` with fixed two-line rows (a name and what it is, then what it is wired to or why it needs attention, each cut to its line) and a pager, so the section's height is shared out by weight (Devices 3 to Projects 2; Connections 2 to Recent activity 3) and a list shows the rows that fit. The 40-pin header is a **strip**, two rows of twenty (odd pins above, even below, pin 1 at the left), with the key to the colours in a line under it. **Instruments** holds a button for each quick check (the power profiler, the multimeter): its result is tall, so it opens in a drawer over the page, closed by Escape. "N commands are not available on this host" opens in a drawer too. A phone keeps the flowing page. Browser check: `scripts/verify_home_layout.py`, over the fake PPK2 bench made a Pi with eighteen devices and thirty commands run.

**Commands (built 2026-11-21, task `gap-commands-page-fits`)** is the fifth. From 768 px up: a left panel (19 rem) with the search box, All / Reads / Changes, an **Area** choice and the commands as a `FitList` of one-line rows (the full name, `meter.wake_table`, and whether it reads or changes), so a list of a hundred commands pages instead of scrolling inside a 60 vh box; a right panel for the chosen command's form and its whole result. **The form panel is the one place on this page that may scroll, and only inside itself**: the page is generated from the registry, so a command can have any number of arguments and any size of result, and the whole result is always shown (a project rule); the page and the list never scroll. A phone keeps the flowing page. Browser check: `scripts/verify_commands_layout.py`.

**GPIO (built 2026-11-21, task `gap-gpio-page-fits`)** is the sixth. From 768 px up: the 40-pin header in a left panel as **twenty rows that share the panel's height** (a row is never under 22 px, so all forty pins are on screen at any window the page is wide enough for, and none needs scrolling to), odd pins left and even right as on the board; in a right panel the assignment form and its result, **which may scroll inside itself** (the form has up to twelve fields and the result is shown whole), while the page never scrolls. A phone keeps the flowing page. Browser check: `scripts/verify_gpio_layout.py`, over the fake bench made a Pi.

**Help (built 2026-11-21, task `gap-help-page-fits`)** is the seventh. From 768 px up: the search and every topic in a left list of compact rows (they fit the window; in one too short for them the list scrolls inside itself), and the chosen topic in a right **reader panel that scrolls only inside itself**, since a topic is text of any length, starting at its top whenever a topic is chosen; the page never scrolls. A phone keeps the list-then-topic flow with its Back button. Browser check: `scripts/verify_help_layout.py`.

**Firmware (built 2026-11-21, task `gap-firmware-page-fits`)** is the eighth. From 768 px up nothing on it scrolls: a left **Projects** panel (the projects as a `FitList`, a row to choose one, and the new-project form at its foot), and beside it the chosen project's panel (the upload in **one row**, the flash-to selectors when there is more than one board, the packages as a `FitList` of fixed three-line rows with Flash and More, and Delete project at the foot). The **ESP32 boards** card opens in a drawer from a button in the page header, and the **last result** is a one-line strip at the foot with Details, which opens the whole result in a drawer (the whole result is always shown: a project rule). A phone keeps the flowing page. Browser check: `scripts/verify_firmware_layout.py`, over the PPK2 bench with a package store, 14 projects and 14 packages in one.

**Devices (built 2026-11-21, task `gap-devices-page-fits`)** is the ninth, and the first made full-height before it overflowed: on the real bench it fitted, but only because few things were attached. From 768 px up: the Connected / Not attached / All tabs; the devices as a `FitList` of fixed two-line rows on the left (a row **selects** the device instead of opening inline); on the right the chosen device's detail and actions (serial, product, mode, port, Flash, Wiring, Console, the name editor) in a panel that may scroll inside itself; the **power profiler and multimeter quick checks** in drawers from buttons in the header. A phone keeps the flowing page with its inline detail. Browser check: `scripts/verify_devices_layout.py`, over the fake bench made a Pi with eighteen devices.

## Checks

Browser, both themes, at 390 px and 1280 px: no horizontal scroll; the nav does not scroll (`scrollWidth <= clientWidth` and height fits);
phone: four daily buttons plus More visible in the bar, the other pages reachable by More, the current page marked; desktop: every page a
button in the sidebar, the content starts at the top of the window (no band above it), the sidebar stays while the page scrolls.
