"""G2.5 — forget-recovery: things the user said they'd do, and never closed.

People make commitments inside messages and then lose them. This module reads
text the engine has already seen ("I'll send the pricing deck", "we need to
chase the invoice by Friday"), keeps them as open loops, and lets the seeker
raise them again with provenance — *you said this, here, on this date*.

Three rules make this safe rather than creepy:

**Provenance is mandatory.** A commitment with no source is not stored. A
reminder you cannot trace back is indistinguishable from the engine making
something up, and the whole value here is trust.

**Contact-shaped commitments never become contact.** "Email John about the
renewal" arms a *draft*, flagged `needs_contact`, and the seeker maps it to a
propose-only action. `no_unconsented_contact` is a baseline constraint and this
is the exact path that would erode it.

**The snippet lives here and nowhere else.** Commitments necessarily hold the
user's own words — that is what makes the reminder useful. But that text must
not leak sideways into the F1 ledger or telemetry, which stay identifiers-only.
Tested in both directions.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from typing import Any

from CortexOS.paths import data_path

DB_PATH = data_path("engine", "commitments.db")

MAX_SNIPPET = 300
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_DISMISSED = "dismissed"

_lock = threading.Lock()

# Deterministic and conservative: a missed commitment is recoverable, a wrong
# one trains the user to ignore the feature.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("promise", re.compile(r"\b(?:i|we)\s*(?:'ll|’ll|\s+will)\s+([^.!?\n]{4,160})", re.I)),
    ("promised", re.compile(r"\b(?:i|we)\s+promised\s+(?:to\s+)?([^.!?\n]{4,160})", re.I)),
    ("need_to", re.compile(r"\b(?:we|i)\s+(?:need to|should|must)\s+([^.!?\n]{4,160})", re.I)),
    ("remind", re.compile(r"\bremind me to\s+([^.!?\n]{4,160})", re.I)),
    ("follow_up", re.compile(r"\b(?:follow up|circle back)\s+(?:on\s+|with\s+)?([^.!?\n]{4,160})", re.I)),
    ("todo", re.compile(r"\bTODO:?\s+([^.!?\n]{4,160})")),
]

_CONTACT = re.compile(
    r"\b(email|e-mail|send|reply|respond|call|phone|message|text|notify|tell|contact|ping|invite)\b",
    re.I,
)

_DUE = re.compile(
    r"\b(?:by|before|on|due)\s+"
    r"(today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|next week|this week|end of (?:the )?(?:day|week|month)|eod|eow)\b",
    re.I,
)


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    from CortexOS.packaging import require_extra

    require_extra("agentic", feature="commitments")
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS commitments (
              id TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL UNIQUE,
              snippet TEXT NOT NULL,
              pattern TEXT DEFAULT '',
              source TEXT NOT NULL,
              source_id TEXT DEFAULT '',
              needs_contact INTEGER DEFAULT 0,
              due_hint TEXT DEFAULT '',
              status TEXT DEFAULT 'open',
              said_at REAL,
              created_at REAL,
              updated_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status, said_at);
            """
        )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).strip(" ,;:-")


def _fingerprint(snippet: str, source_id: str) -> str:
    key = f"{source_id}|{re.sub(r'[^a-z0-9 ]', '', snippet.lower())}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def extract(text: str) -> list[dict[str, Any]]:
    """Pull commitment-shaped clauses out of text. Pure — reads nothing, stores nothing."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, pattern in _PATTERNS:
        for match in pattern.finditer(text or ""):
            snippet = _clean(match.group(1))[:MAX_SNIPPET]
            if len(snippet) < 4:
                continue
            key = snippet.lower()
            if key in seen:
                continue
            seen.add(key)
            due = _DUE.search(snippet) or _DUE.search(text or "")
            found.append(
                {
                    "snippet": snippet,
                    "pattern": name,
                    "needs_contact": bool(_CONTACT.search(snippet)),
                    "due_hint": _clean(due.group(1)).lower() if due else "",
                }
            )
    return found


def record_from_text(
    text: str,
    *,
    source: str,
    source_id: str = "",
    said_at: float | None = None,
) -> dict[str, Any]:
    """Store new commitments found in text. Provenance is required, not optional."""
    init()
    if not (source or "").strip():
        return {"ok": False, "error": "provenance_required"}

    now = time.time()
    said = said_at if said_at is not None else now
    stored: list[dict[str, Any]] = []
    for item in extract(text):
        fingerprint = _fingerprint(item["snippet"], source_id)
        cid = "cmt-" + fingerprint[:8]
        with _lock, _conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO commitments (
                  id, fingerprint, snippet, pattern, source, source_id,
                  needs_contact, due_hint, status, said_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (
                    cid,
                    fingerprint,
                    item["snippet"],
                    item["pattern"],
                    source,
                    source_id,
                    int(item["needs_contact"]),
                    item["due_hint"],
                    said,
                    now,
                    now,
                ),
            )
            if cur.rowcount:
                stored.append({"id": cid, **item})
    return {"ok": True, "found": len(extract(text)), "stored": stored}


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["needs_contact"] = bool(out.get("needs_contact"))
    said = float(out.get("said_at") or 0.0)
    out["age_days"] = int((time.time() - said) // 86400) if said else 0
    out["said_on"] = time.strftime("%d %b", time.localtime(said)) if said else ""
    out["provenance"] = (
        f"You said this on {out['said_on']}" + (f" ({out['source']})" if out.get("source") else "")
    )
    return out


def list_commitments(status: str = STATUS_OPEN, limit: int = 50) -> list[dict[str, Any]]:
    init()
    sql = "SELECT * FROM commitments"
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY said_at ASC LIMIT ?"
    with _conn() as conn:
        return [_row(r) for r in conn.execute(sql, (*params, int(limit))).fetchall()]


def get(cid: str) -> dict[str, Any] | None:
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM commitments WHERE id = ?", (cid,)).fetchone()
    return _row(row) if row else None


def set_status(cid: str, status: str) -> dict[str, Any] | None:
    if status not in (STATUS_OPEN, STATUS_CLOSED, STATUS_DISMISSED):
        return None
    init()
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE commitments SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), cid),
        )
    return get(cid)


def close(cid: str) -> dict[str, Any] | None:
    return set_status(cid, STATUS_CLOSED)


def dismiss(cid: str) -> dict[str, Any] | None:
    return set_status(cid, STATUS_DISMISSED)


def as_proposals(limit: int = 3) -> list[dict[str, Any]]:
    """Shape open commitments as seeker candidates — always propose-only.

    A commitment that mentions contacting someone still maps to ``propose``,
    never to ``send_message``. The seeker's gate would confirm-gate a contact
    action anyway; keeping it propose-only means the engine never even offers
    to do it on its own.
    """
    out: list[dict[str, Any]] = []
    for item in list_commitments(STATUS_OPEN, limit=limit):
        age = item["age_days"]
        when = f"{age} day{'s' if age != 1 else ''} ago" if age else "recently"
        why = f"You said you'd {item['snippet']} — {when}, and it's still open."
        if item["due_hint"]:
            why += f" You mentioned {item['due_hint']}."
        if item["needs_contact"]:
            why += " I'll draft it; sending stays with you."
        out.append(
            {
                "title": f"Close the loop: {item['snippet'][:60]}",
                "why": why,
                "action": "propose",
                "source": "commitment",
                "next_step": {
                    "commitment_id": item["id"],
                    "provenance": item["provenance"],
                    "needs_contact": item["needs_contact"],
                },
            }
        )
    return out
