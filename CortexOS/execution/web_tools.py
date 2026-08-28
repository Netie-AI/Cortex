"""Keyless web search + fetch for research subagents.

Stdlib only and no API key, so a research workflow works on a fresh install
with nothing configured. Both calls fail soft — a subagent that gets an empty
result says it found nothing, which is a better outcome than a run that dies
because a search endpoint was unreachable.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

_UA = "Netie-Cortex/0.2 (+research subagent)"
_TIMEOUT = 10.0
_MAX_BYTES = 900_000

_TAG = re.compile(r"<[^>]+>")
_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|svg|nav|footer|header|form)\b.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_RESULT_A = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_RESULT_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _get(url: str, *, timeout: float = _TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed schemes checked below
        return resp.read(_MAX_BYTES)


def _text_of(html: str) -> str:
    stripped = _SCRIPT_STYLE.sub(" ", html)
    stripped = _TAG.sub("\n", stripped)
    stripped = unescape(stripped)
    stripped = _WS.sub(" ", stripped)
    lines = [ln.strip() for ln in stripped.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(ln for ln in lines if ln))


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo HTML results wrap targets in /l/?uddg=<encoded>."""
    if "duckduckgo.com/l/" not in href and not href.startswith("/l/"):
        return href
    query = urllib.parse.urlparse(href).query
    target = urllib.parse.parse_qs(query).get("uddg")
    return urllib.parse.unquote(target[0]) if target else href


def search(query: str, *, max_results: int = 6) -> dict[str, Any]:
    """Web search → ``{"ok", "query", "results": [{title, url, snippet}]}``.

    The Instant Answer API is tried first because it is stable and cheap, then
    the HTML endpoint, which is what actually returns ranked links.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query required", "results": []}

    results: list[dict[str, str]] = []
    try:
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "no_html": "1", "skip_disambig": "1"}
        )
        data = json.loads(_get(url))
        if data.get("AbstractText"):
            results.append(
                {
                    "title": str(data.get("Heading") or q),
                    "url": str(data.get("AbstractURL") or ""),
                    "snippet": str(data["AbstractText"])[:600],
                }
            )
        for topic in (data.get("RelatedTopics") or []):
            if len(results) >= max_results:
                break
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(
                    {
                        "title": str(topic.get("Text"))[:120],
                        "url": str(topic.get("FirstURL") or ""),
                        "snippet": str(topic.get("Text"))[:600],
                    }
                )
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        pass

    if len(results) < max_results:
        try:
            url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q})
            html = _get(url).decode("utf-8", errors="ignore")
            snippets = [_text_of(s)[:600] for s in _RESULT_SNIPPET.findall(html)]
            for idx, (href, title) in enumerate(_RESULT_A.findall(html)):
                if len(results) >= max_results:
                    break
                results.append(
                    {
                        "title": _text_of(title)[:160],
                        "url": _unwrap_ddg(unescape(href)),
                        "snippet": snippets[idx] if idx < len(snippets) else "",
                    }
                )
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            pass

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for r in results:
        key = r.get("url") or r.get("title", "")
        if key and key not in seen:
            seen.add(key)
            deduped.append(r)

    return {"ok": bool(deduped), "query": q, "results": deduped[:max_results]}


def fetch(url: str, *, max_chars: int = 12000) -> dict[str, Any]:
    """Fetch one page and return readable text. http/https only."""
    target = (url or "").strip()
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": "only http/https urls may be fetched", "url": target}
    if not parsed.netloc:
        return {"ok": False, "error": "url has no host", "url": target}
    try:
        raw = _get(target, timeout=_TIMEOUT + 5)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc)[:200], "url": target}

    html = raw.decode("utf-8", errors="ignore")
    title_match = _TITLE.search(html)
    text = _text_of(html)
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "url": target,
        "title": _text_of(title_match.group(1))[:200] if title_match else "",
        "text": text[:max_chars],
        "chars": len(text),
        "truncated": truncated,
    }


#: Name → callable, for the AGENT_TASK tool broker.
WEB_TOOLS: dict[str, Any] = {"web_search": search, "web_fetch": fetch}

SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "Search the web. Returns ranked {title, url, snippet}.",
        "params": {"query": "string", "max_results": "int (default 6)"},
    },
    {
        "name": "web_fetch",
        "description": "Fetch one http(s) URL and return its readable text.",
        "params": {"url": "string", "max_chars": "int (default 12000)"},
    },
]
