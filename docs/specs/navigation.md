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

## Checks

Browser, both themes, at 390 px and 1280 px: no horizontal scroll; the nav does not scroll (`scrollWidth <= clientWidth` and height fits);
phone: four daily buttons plus More visible in the bar, the other pages reachable by More, the current page marked; desktop: every page a
button in the sidebar, the content starts at the top of the window (no band above it), the sidebar stays while the page scrolls.
