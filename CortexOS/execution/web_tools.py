"""Keyless web search + fetch for research subagents.

Stdlib only and no API key, so a research workflow works on a fresh install
with nothing configured. Both calls fail soft — a subagent that gets an empty
result says it found nothing, which is a better outcome than a run that dies
because a search endpoint was unreachable.
"""

from __future__ import annotations

import http.client
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


def non_public_reason(url: str) -> str:
    """Why ``url`` is not a public internet address, or "" when it is.

    An agent reading untrusted pages can be told to fetch loopback services
    (the engine, OpenVault's key store), the LAN or a cloud metadata endpoint.
    Every address the host resolves to must be global; a name that does not
    resolve is refused rather than assumed public.
    """
    import ipaddress
    import socket

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "only http/https urls may be fetched"
    try:
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        return f"bad url ({exc})"
    if not host:
        return "url has no host"
    if host.lower() in {"localhost", "localhost.localdomain"} or host.lower().endswith(
        (".localhost", ".local", ".internal")
    ):
        return f"host '{host}' is local, not public"
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError) as exc:
        return f"host '{host}' does not resolve ({exc})"
    for info in infos:
        addr = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        if not addr.is_global:
            return f"host '{host}' resolves to non-public address {addr}"
    return ""


class _PublicOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        why = non_public_reason(newurl)
        if why:
            raise urllib.error.URLError(f"redirect refused: {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _PeerCheck:
    """Re-check the address actually connected to.

    :func:`non_public_reason` resolves the name once; the socket resolves it
    again. A DNS answer that flips to 127.0.0.1 in between (rebinding) would
    pass the first check, so the connected peer is verified before any request
    bytes are sent. Skipped only when urllib routed the request through an
    operator-configured proxy (the socket is then to the proxy, which may
    itself be on loopback); the target was still checked by name first.
    """

    host: str
    sock: Any

    def connect(self) -> None:
        import ipaddress

        super().connect()  # type: ignore[misc]
        if getattr(self, "_tunnel_host", None):
            return  # https through a CONNECT proxy
        peer = str(self.sock.getpeername()[0]).split("%", 1)[0]
        if not ipaddress.ip_address(peer).is_global:
            self.close()  # type: ignore[attr-defined]
            raise OSError(f"refused: connected to non-public address {peer}")


class _PublicHTTPConnection(_PeerCheck, http.client.HTTPConnection):
    pass


class _PublicHTTPSConnection(_PeerCheck, http.client.HTTPSConnection):
    pass


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # type: ignore[no-untyped-def]
        # plain http through a proxy: the socket is to the proxy, not the target
        cls = http.client.HTTPConnection if req.has_proxy() else _PublicHTTPConnection
        return self.do_open(cls, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


def _get_public(url: str, *, timeout: float) -> bytes:
    opener = urllib.request.build_opener(
        _PublicHTTPHandler(), _PublicHTTPSHandler(), _PublicOnlyRedirects()
    )
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(_MAX_BYTES)


def fetch(url: str, *, max_chars: int = 12000, public_only: bool = False) -> dict[str, Any]:
    """Fetch one page and return readable text. http/https only.

    ``public_only`` (the agent broker) refuses loopback / private / link-local /
    metadata targets, including ones reached through a redirect.
    """
    target = (url or "").strip()
    parsed = urllib.parse.urlparse(target)
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": "only http/https urls may be fetched", "url": target}
    if not parsed.netloc:
        return {"ok": False, "error": "url has no host", "url": target}
    if public_only:
        why = non_public_reason(target)
        if why:
            return {"ok": False, "error": f"refused: {why}", "url": target}
    try:
        if public_only:
            raw = _get_public(target, timeout=_TIMEOUT + 5)
        else:
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
