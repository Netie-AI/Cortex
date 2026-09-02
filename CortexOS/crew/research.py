"""Keyless web + GitHub lookup for crew ingest and analog work.

Reuses ``CortexOS.execution.web_tools`` (stdlib DuckDuckGo) and ``gh search``.
Crew must not guess a GitHub owner: search, then fetch, then save_skill.
Loopback and RFC1918 hosts are refused here so analog_clone / web_fetch cannot
pull estate HTML into a transcript (R-0011: a private fetch that looks like a
public analog is a lie).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_MAX_OUT = 8000
_MAX_BYTES = 900_000
_FETCH_TIMEOUT = 12.0

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "localhost6",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "local",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)

# IPv6 encodings of an IPv4 host. ipv4_mapped already folds into is_global;
# these three prefixes do not (Python 3.13 dropped ipv4_compatible).
_IPV4_COMPAT = ipaddress.IPv6Network("::/96")
_SIIT = ipaddress.IPv6Network("::ffff:0:0:0/96")
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse a hop onto loopback / RFC1918. Engine fetch follows any 3xx."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        hop = newurl.decode("latin-1") if isinstance(newurl, bytes) else str(newurl)
        why = refuse_nonpublic_url(hop)
        if why:
            raise urllib.error.URLError(why)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


def refuse_nonpublic_url(url: str) -> str | None:
    """Reason to skip the fetch, or None when the host looks public.

    Literal IPs and well-known local names only. No DNS lookup -- resolving
    would hang tests and would still lose to DNS rebinding.
    """
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return "only public http(s) urls may be fetched"
    if parsed.scheme not in ("http", "https"):
        return "only public http(s) urls may be fetched"
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        return "url has no host"
    if (
        host in _BLOCKED_HOSTS
        or host.endswith(".localhost")
        or host.endswith(".internal")
        or host.endswith(".local")
    ):
        return f"host {host!r} is not a public analog"
    ip = _literal_ip(host)
    if ip is None:
        # Hostname, not a literal. No DNS -- localtest.me / nip.io stay parked.
        return None
    # is_global is false for RFC1918, loopback, link-local, multicast,
    # reserved, unspecified, and CGNAT/shared 100.64/10 (Alibaba
    # metadata sits at 100.100.100.200). The flag list missed shared.
    if not ip.is_global:
        return f"host {host!r} is not a public analog"
    embedded = _embedded_ipv4(ip)
    if embedded is not None and not embedded.is_global:
        return f"host {host!r} is not a public analog"
    return None


def _embedded_ipv4(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 hidden in IPv4-compatible, SIIT translated, or NAT64 well-known form."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    mapped = ip.ipv4_mapped
    if mapped is not None:
        return mapped
    if ip in _IPV4_COMPAT or ip in _SIIT or ip in _NAT64:
        return ipaddress.IPv4Address(ip.packed[-4:])
    return None


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a literal IP, including inet_aton shortcuts (127.1, 0x7f000001).

    No DNS. Hostnames return None so rebinding names stay parked.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def web_search(query: str, *, max_results: int = 6) -> dict[str, Any]:
    from CortexOS.execution.web_tools import search

    return search(query, max_results=max_results)


def web_fetch(url: str, *, max_chars: int = 12000) -> dict[str, Any]:
    """Fetch one public page. Does not follow a 3xx onto loopback / RFC1918."""
    from CortexOS.execution import web_tools

    target = (url or "").strip()
    reason = refuse_nonpublic_url(target)
    if reason:
        return {"ok": False, "error": reason, "url": target}
    try:
        raw, final = _get_public(target)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {"ok": False, "error": str(exc)[:200], "url": target}
    why = refuse_nonpublic_url(final)
    if why:
        return {"ok": False, "error": why, "url": target}
    html = raw.decode("utf-8", errors="ignore")
    title_match = web_tools._TITLE.search(html)
    text = web_tools._text_of(html)
    truncated = len(text) > max_chars
    return {
        "ok": True,
        "url": final,
        "title": web_tools._text_of(title_match.group(1))[:200] if title_match else "",
        "text": text[:max_chars],
        "chars": len(text),
        "truncated": truncated,
    }


def _get_public(url: str, *, timeout: float = _FETCH_TIMEOUT) -> tuple[bytes, str]:
    opener = urllib.request.build_opener(PublicRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Netie-Crew/research", "Accept-Language": "en"},
    )
    with opener.open(req, timeout=timeout) as resp:
        final = resp.geturl()
        why = refuse_nonpublic_url(final)
        if why:
            raise urllib.error.URLError(why)
        return resp.read(_MAX_BYTES), final


def github_search(query: str, *, limit: int = 8) -> dict[str, Any]:
    """Public repo search. ``gh`` first; web fallback if gh is dark."""
    from CortexOS.crew import github

    blob = github.search_public(query, limit=limit)
    if blob.get("ok") and blob.get("repos"):
        return blob
    web = web_search(f"site:github.com {query}", max_results=limit)
    hits = web.get("results") or []
    return {
        "ok": bool(hits) or bool(blob.get("ok")),
        "query": query,
        "repos": list(blob.get("repos") or []),
        "web": hits,
        "detail": str(blob.get("detail") or "")[:400],
        "law": "Search then fetch. Do not guess the owner. Do not clone every repo.",
    }


def as_tool_text(blob: dict[str, Any]) -> str:
    return json.dumps(blob, ensure_ascii=True)[:_MAX_OUT]
