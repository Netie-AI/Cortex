"""Crew research tools: search then fetch, never guess the GitHub owner."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from CortexOS.crew.github import search_public
from CortexOS.crew.research import PublicRedirectHandler, as_tool_text, github_search


def test_search_public_uses_gh_and_never_clones() -> None:
    def run(argv, timeout=20):
        assert argv[:3] == ["gh", "search", "repos"]
        assert "impeccable" in argv
        payload = [
            {
                "name": "impeccable",
                "fullName": "pbakaus/impeccable",
                "url": "https://github.com/pbakaus/impeccable",
                "description": "Design rules",
            }
        ]
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    blob = search_public("impeccable", runner=run)
    assert blob["ok"] is True
    assert blob["repos"][0]["full_name"] == "pbakaus/impeccable"
    assert "clone every repo" in blob["law"]


def test_github_search_falls_back_to_web_when_gh_is_dark(monkeypatch) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")

    def fake_web(query, max_results=6):
        assert "github.com" in query
        return {
            "ok": True,
            "query": query,
            "results": [
                {
                    "title": "pbakaus/impeccable",
                    "url": "https://github.com/pbakaus/impeccable",
                    "snippet": "skill",
                }
            ],
        }

    monkeypatch.setattr("CortexOS.crew.research.web_search", fake_web)
    blob = github_search("impeccable")
    assert blob["web"][0]["url"].endswith("pbakaus/impeccable")
    text = as_tool_text(blob)
    assert "pbakaus/impeccable" in text


def test_web_fetch_refuses_loopback_without_calling_fetch(monkeypatch) -> None:
    from CortexOS.crew import research

    def boom(*_a, **_k):
        raise AssertionError("loopback must not reach web_tools.fetch")

    monkeypatch.setattr("CortexOS.execution.web_tools.fetch", boom)
    blob = research.web_fetch("http://127.0.0.1:5000/api/keys")
    assert blob["ok"] is False
    assert "not a public analog" in blob["error"]
    assert research.refuse_nonpublic_url("https://www.igloo.inc/") is None
    assert research.refuse_nonpublic_url("http://169.254.169.254/") is not None
    assert research.refuse_nonpublic_url("http://localhost/crew/health") is not None
    assert research.refuse_nonpublic_url("http://100.64.0.1/") is not None
    assert research.refuse_nonpublic_url("http://100.100.100.200/latest/meta-data/") is not None
    assert research.refuse_nonpublic_url("http://8.8.8.8/") is None
    assert research.refuse_nonpublic_url("http://[::1]/") is not None
    assert research.refuse_nonpublic_url("http://[::ffff:127.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://[::ffff:10.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://10.0.0.8/") is not None
    assert research.refuse_nonpublic_url("http://192.168.1.8/") is not None
    assert research.refuse_nonpublic_url("https://metadata.google.internal/") is not None
    assert research.refuse_nonpublic_url("http://instance-data/") is not None
    assert research.refuse_nonpublic_url("http://printer.local/") is not None
    assert research.refuse_nonpublic_url("http://192.0.0.192/") is not None
    # inet_aton shortcuts without DNS (127.1 is 127.0.0.1 on this stack).
    assert research.refuse_nonpublic_url("http://127.1/") is not None
    assert research.refuse_nonpublic_url("http://0/") is not None
    assert research.refuse_nonpublic_url("http://2130706433/") is not None
    assert research.refuse_nonpublic_url("http://0x7f000001/") is not None
    assert research.refuse_nonpublic_url("http://localhost.localdomain/") is not None
    # IPv6 encodings of loopback / RFC1918. is_global misses these prefixes.
    assert research.refuse_nonpublic_url("http://[::127.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://[::7f00:1]/") is not None
    assert research.refuse_nonpublic_url("http://[::10.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://[::ffff:0:127.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://[::ffff:0:7f00:1]/") is not None
    assert research.refuse_nonpublic_url("http://[64:ff9b::127.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://[64:ff9b::10.0.0.1]/") is not None
    assert research.refuse_nonpublic_url("http://localhost6/") is not None
    assert research.refuse_nonpublic_url("http://ip6-localhost/") is not None
    assert research.refuse_nonpublic_url("http://ip6-loopback/") is not None
    assert research.refuse_nonpublic_url("http://broadcasthost/") is not None
    assert research.refuse_nonpublic_url("http://local/") is not None
    # Public IPv4 still allowed, including the same embeddings of 8.8.8.8.
    assert research.refuse_nonpublic_url("http://[::8.8.8.8]/") is None
    assert research.refuse_nonpublic_url("http://[64:ff9b::8.8.8.8]/") is None
    # Rebinding names stay parked: no DNS on purpose.
    assert research.refuse_nonpublic_url("http://localtest.me/") is None
    assert research.refuse_nonpublic_url("http://127.0.0.1.nip.io/") is None


def test_redirect_to_loopback_is_refused() -> None:
    handler = PublicRedirectHandler()
    req = urllib.request.Request("https://example.com/x")
    with pytest.raises(urllib.error.URLError) as exc:
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://127.0.0.1/secret"
        )
    assert "not a public analog" in str(exc.value)
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://10.0.0.8/internal"
        )
    with pytest.raises(urllib.error.URLError) as exc2:
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://[::127.0.0.1]/secret"
        )
    assert "not a public analog" in str(exc2.value)

