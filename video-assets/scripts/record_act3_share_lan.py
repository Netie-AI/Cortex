"""Act 3 — AirGPT create app + Share LAN."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from _common import IDE, finish, launch, new_page, pause


def main() -> None:
    with sync_playwright() as p:
        browser = launch(p)
        page = new_page(browser, "act3_share_lan")
        page.goto(f"{IDE}/#openide", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1200)
        page.get_by_role("button", name="Create app").click(timeout=4000)
        pause(page, 1000)
        page.keyboard.press("Escape")
        try:
            page.locator("#modalClose").click(timeout=1500)
        except Exception:
            pass
        pause(page, 400)
        page.get_by_role("button", name="Share LAN").click(timeout=4000)
        pause(page, 1800)
        try:
            page.locator("#modalClose").click(timeout=1500)
        except Exception:
            pass
        pause(page, 800)
        path = finish(page)
        browser.close()
        print("wrote", path)


if __name__ == "__main__":
    main()
