"""Act 4 — Cortex brain on the mesh."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from _common import CX, OV, finish, launch, new_page, pause


def main() -> None:
    with sync_playwright() as p:
        browser = launch(p)
        page = new_page(browser, "act4_cortex_brain")
        page.goto(f"{CX}/health", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1400)
        page.goto(f"{OV}/#mesh", wait_until="domcontentloaded", timeout=20000)
        pause(page, 800)
        page.locator('button[data-tab="mesh"]').click(timeout=4000)
        pause(page, 1000)
        try:
            page.get_by_role("button", name="Approve Cortex").click(timeout=2000)
        except Exception:
            pass
        pause(page, 800)
        page.locator('button[data-tab="engine"]').click(timeout=4000)
        pause(page, 1600)
        path = finish(page)
        browser.close()
        print("wrote", path)


if __name__ == "__main__":
    main()
