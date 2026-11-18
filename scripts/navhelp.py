"""The dashboard's page navigation, for the browser scripts (docs/specs/navigation.md).

On a phone the nav is a bottom bar of the first four pages and More; the other pages are in a list More opens. On a desktop every page is
a button in the sidebar. These helpers work at either width: go_to() opens More first when the page's button is not shown, and
current_pages() reads which buttons are marked as the current page, shown or not (a hidden button is not in the accessibility tree, so
get_by_role cannot see it)."""


def nav(page):
    return page.get_by_role("navigation", name="Pages")


def reveal(page, button):
    """Make a nav button visible: on a phone the pages after the fourth, Theme and Log out are in the list More opens."""
    if not button.is_visible():
        nav(page).get_by_role("button", name="More", exact=True).click()
    return button


def go_to(page, name):
    """Open a page by its label, whatever the width."""
    reveal(page, nav(page).get_by_role("button", name=name, exact=True)).click()


def current_pages(page):
    return page.evaluate("[...document.querySelectorAll('nav[aria-label=Pages] [aria-current=page]')].map((b) => b.textContent)")


def is_current(page, name):
    return name in current_pages(page)
