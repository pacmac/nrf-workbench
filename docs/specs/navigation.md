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
A page joins the list when it has been laid out to fit, one page at a time. **Meter is the first** (`meter-page.md`, "Bench layout"). The others (Home, Devices, Projects, Console, Firmware, Wiring, GPIO, Commands, Help) are not yet, and still scroll. Measured on the real bench at 1280x720 on 2026-11-21 (page height against the 720 px window): Activity 11,205, Home 1,293, Console 1,204, GPIO 1,158, Firmware 964, Help 834, Commands a 432 px scroller holding 3,654 px; Devices, Projects and Wiring fit as they stand.

**Activity (built 2026-11-21, task `gap-activity-page-fits`)** is the second. From 768 px up it is a full-height page: the header, the class filter, and a list of the entries that **fit** the height, newest first, with a pager (`N to M of T`, Previous and Next entries). A row is fixed at two lines: the class, the code (when it differs), the command, its arguments in one short line (the whole of them in the row's tooltip) and the time; then the message and who ran it and how long it took. The count that fits is measured from the list's own height (`FitList`, a ResizeObserver, no timer), so a taller window shows more. On a phone the list flows as before, with Show more. Browser check: `scripts/verify_activity_layout.py`.

## Checks

Browser, both themes, at 390 px and 1280 px: no horizontal scroll; the nav does not scroll (`scrollWidth <= clientWidth` and height fits);
phone: four daily buttons plus More visible in the bar, the other pages reachable by More, the current page marked; desktop: every page a
button in the sidebar, the content starts at the top of the window (no band above it), the sidebar stays while the page scrolls.
