"""Act 2 — OpenVault keys source of truth."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from _common import OV, finish, launch, new_page, pause


def main() -> None:
    with sync_playwright() as p:
        browser = launch(p)
        page = new_page(browser, "act2_vault_keys")
        page.goto(f"{OV}/", wait_until="domcontentloaded", timeout=20000)
        pause(page, 800)
        page.locator('button[data-tab="vault"]').click(timeout=4000)
        pause(page, 1600)
        try:
            page.get_by_role("button", name="Seed Cortex/AirGPT").click(timeout=2000)
            pause(page, 1000)
        except Exception:
            pass
        page.goto(f"{OV}/api/keyvault/snapshot", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1600)
        path = finish(page)
        browser.close()
        print("wrote", path)


if __name__ == "__main__":
    main()
