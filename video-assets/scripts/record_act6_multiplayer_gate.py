"""Act 6 — multiplayer session + gate/bypass denial."""

from __future__ import annotations

import json
import urllib.request

from playwright.sync_api import sync_playwright

from _common import IDE, OV, finish, launch, new_page, pause


def _post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode() or "{}")


def main() -> None:
    # API proof before camera (truth table: bypass/force denied)
    deny = _post(f"{OV}/api/cloud/firewall/check", {"action": "share_lan", "bypass": True})
    gate = _post(f"{OV}/api/gate/check", {"action": "deploy", "force": True})
    assert deny.get("allowed") is False, deny
    assert gate.get("allowed") is False, gate

    with sync_playwright() as p:
        browser = launch(p)
        page = new_page(browser, "act6_multiplayer_gate")
        page.goto(f"{IDE}/#openide", wait_until="domcontentloaded", timeout=20000)
        pause(page, 1000)
        page.get_by_role("button", name="Join session").click(timeout=4000)
        pause(page, 1800)
        try:
            page.locator("#modalClose").click(timeout=1500)
        except Exception:
            pass
        # Show denials as JSON in a blank tab title strip via data URL-ish navigation to OV health then mesh
        page.goto(f"{OV}/#mesh", wait_until="domcontentloaded", timeout=20000)
        pause(page, 800)
        page.evaluate(
            """([deny, gate]) => {
              const pre = document.createElement('pre');
              pre.id = 'yc-deny-proof';
              pre.style.cssText = 'position:fixed;inset:auto 24px 24px 24px;z-index:99;background:#111a;color:#9fe;padding:16px;border-radius:12px;max-height:40vh;overflow:auto;font:12px ui-monospace';
              pre.textContent = 'bypass allowed=' + deny.allowed + '\\nforce allowed=' + gate.allowed + '\\n' + JSON.stringify({deny, gate}, null, 2);
              document.body.appendChild(pre);
            }""",
            [deny, gate],
        )
        pause(page, 2200)
        path = finish(page)
        browser.close()
        print("wrote", path)
        print("deny_ok", deny.get("allowed") is False, "gate_ok", gate.get("allowed") is False)


if __name__ == "__main__":
    main()
