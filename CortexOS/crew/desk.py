"""One chat-facing snapshot: connectors, PRs, inbox, Cursor key. No buttons."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from CortexOS.crew import connectors, estate, github, inbox
from CortexOS.crew.openvault import cursor_key_status


def snapshot(*, uacc_enabled: bool = False, uacc_armed: bool = False) -> dict[str, Any]:
    """PRs, IMAP, plugins, estate catalog. Live gh org list is estate_status, not this GET.

    Peers run in parallel so hung gh cannot stack IMAP. GH_WAIT_S is 1.5s so
    GET /crew/desk finishes inside the UI apiGet 2.5s cap.
    """
    with ThreadPoolExecutor(max_workers=5) as pool:
        plugs_f = pool.submit(
            connectors.catalog, uacc_enabled=uacc_enabled, uacc_armed=uacc_armed
        )
        prs_f = pool.submit(github.list_prs)
        mail_f = pool.submit(inbox.status)
        cursor_f = pool.submit(cursor_key_status)
        estate_f = pool.submit(estate.snapshot, live=False)
        plugs = plugs_f.result()
        prs = prs_f.result()
        mail = mail_f.result()
        cursor = cursor_f.result()
        estate_snap = estate_f.result()
    return {
        "ok": True,
        "connectors": plugs,
        "connectors_connected": sum(1 for p in plugs if p.get("connected")),
        "prs": prs,
        "inbox": mail,
        "cursor": cursor,
        "estate": estate_snap,
        "law": (
            "Ask in chat to check PRs or mail. Drop files to import. "
            "Human is money and decision authority. Do not auto-merge or auto-send. "
            "Cursor chats use grok-4.6 (high), not fast. Ticket Runner seats existing writers. "
            "Before shipping, call ship_gate. File presence is not a compliance certificate."
        ),
    }


def render(snap: dict[str, Any] | None = None) -> str:
    data = snap or snapshot()
    lines = [str(data.get("law") or "")]
    cursor = data.get("cursor") or {}
    lines.append(
        f"Cursor key: {'set' if cursor.get('configured') else 'missing'} "
        f"model={cursor.get('model') or 'grok-4.6'} chars={cursor.get('chars') or 0}"
    )
    prs = (data.get("prs") or {}).get("prs") or []
    detail = (data.get("prs") or {}).get("detail") or ""
    lines.append(f"PRs: {len(prs)}" + (f" ({detail})" if detail else ""))
    for row in prs[:20]:
        lines.append(
            f"- {(row.get('repo') or '')}#{row.get('number')} "
            f"{row.get('title')} draft={row.get('draft')} review={row.get('review')}"
        )
    mail = data.get("inbox") or {}
    lines.append("Mail: " + str(mail.get("detail") or "unset"))
    for msg in (mail.get("messages") or [])[:8]:
        lines.append(f"- {msg.get('from')} | {msg.get('subject')}")
    lines.append("Connectors:")
    for plug in data.get("connectors") or []:
        state = "connected" if plug.get("connected") else "mapped"
        lines.append(f"- {plug.get('slug')}: {state} ({plug.get('layer')})")
    estate_snap = data.get("estate") or {}
    n_estate = estate_snap.get("n") or 0
    lines.append(
        f"Estate: {n_estate} {estate_snap.get('org') or 'Netie-AI'} repos. "
        "Call estate_status for surfaces. Call ship_gate before shipping."
    )
    return "\n".join(lines)
