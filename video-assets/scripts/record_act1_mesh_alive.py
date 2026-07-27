"""Act 1 — three surfaces alive on the local mesh."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from _common import CX, IDE, OV, finish, launch, new_page, pause


def main() -> None:
    with sync_playwright() as p:
        browser = launch(p)
        page = new_page(browser, "act1_mesh_alive")
        page.goto(f"{OV}/#mesh", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1200)
        try:
            page.get_by_role("button", name="Local Mesh").click(timeout=3000)
        except Exception:
            page.locator('button[data-tab="mesh"]').click(timeout=3000)
        pause(page, 1400)
        try:
            page.get_by_role("button", name="Refresh").click(timeout=2000)
        except Exception:
            pass
        pause(page, 1200)
        page.goto(f"{IDE}/#openide", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1600)
        page.goto(f"{CX}/health", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1400)
        path = finish(page)
        browser.close()
        print("wrote", path)


if __name__ == "__main__":
    main()
