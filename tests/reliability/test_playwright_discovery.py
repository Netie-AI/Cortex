"""Playwright reliability checks for Cortex discovery + health.

Starts an in-process uvicorn against create_app() and uses Playwright's
request / browser APIs so CI does not need a pre-running Next.js demo.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def api_base(tmp_path_factory):
    os.environ["PACK"] = "dms"
    os.environ["DMS_AUTH_DISABLED"] = "1"
    home = tmp_path_factory.mktemp("pw_api")
    os.chdir(home)

    import uvicorn
    from CortexOS.api.app import create_app

    app = create_app()
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    with sync_playwright() as p:
        req = p.request.new_context()
        while time.time() < deadline:
            try:
                r = req.get(f"{base}/health")
                if r.ok:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            server.should_exit = True
            req.dispose()
            pytest.fail("uvicorn failed to become healthy")
        req.dispose()

    yield base
    server.should_exit = True
    thread.join(timeout=5)


def test_playwright_health(api_base):
    with sync_playwright() as p:
        req = p.request.new_context()
        res = req.get(f"{api_base}/health")
        assert res.ok
        body = res.json()
        assert body["status"] == "ok"
        assert body["pack"] == "dms"
        req.dispose()


def test_playwright_find_skills_api(api_base):
    payload = json.dumps({"goal": "playwright e2e stress testing", "top_k": 5, "evolve": False})
    with sync_playwright() as p:
        req = p.request.new_context()
        res = req.post(
            f"{api_base}/api/discovery/find-skills",
            headers={"Content-Type": "application/json"},
            data=payload,
        )
        assert res.ok, res.text()
        body = res.json()
        assert body["ok"] is True
        assert body["best"]["name"]
        assert len(body["matches"]) >= 1
        req.dispose()


def test_playwright_mcp_find_skills(api_base):
    payload = json.dumps(
        {"name": "find_skills", "arguments": {"goal": "security audit skill", "top_k": 3}}
    )
    with sync_playwright() as p:
        req = p.request.new_context()
        res = req.post(
            f"{api_base}/mcp/call",
            headers={"Content-Type": "application/json"},
            data=payload,
        )
        assert res.ok, res.text()
        body = res.json()
        assert body["ok"] is True
        assert body["result"]["matches"]
        req.dispose()


def test_playwright_browser_renders_inline_finder(api_base):
    """Browser smoke: same-origin playground page calls Find Skills."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{api_base}/api/discovery/playground")
        page.click("#go")
        page.wait_for_function(
            "document.getElementById('out').textContent.length > 0",
            timeout=15_000,
        )
        text = page.locator("#out").inner_text()
        assert text and text != "NONE"
        browser.close()
