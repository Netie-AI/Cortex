"""Working (Redis), episodic (Qdrant), semantic (Postgres) — Phase 3 wiring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


WORKING_KEY_PREFIX = "working:"
DEFAULT_WORKING_TTL_S = 2 * 60 * 60
MAX_WORKING_TURNS = 10


class WorkingMemoryStore(Protocol):
    """Last N conversational turns keyed by CRM session."""

    async def append_turn(self, session_id: str, text: str) -> None: ...

    async def get_recent_turns(self, session_id: str, limit: int = MAX_WORKING_TURNS) -> list[str]: ...


@dataclass(slots=True)
class InMemoryWorkingStore:
    """Test double / degraded mode when Redis is unavailable."""

    _sessions: dict[str, list[str]] = field(default_factory=dict)
    maxlen: int = MAX_WORKING_TURNS

    async def append_turn(self, session_id: str, text: str) -> None:
        lst = self._sessions.setdefault(session_id, [])
        lst.append(text)
        overflow = len(lst) - self.maxlen
        if overflow > 0:
            del lst[0:overflow]

    async def get_recent_turns(self, session_id: str, limit: int = MAX_WORKING_TURNS) -> list[str]:
        return list(self._sessions.get(session_id, [])[-limit:])


try:
    from redis.asyncio import Redis as AsyncRedis

    class RedisWorkingStore:
        def __init__(self, redis: AsyncRedis, *, ttl_seconds: int = DEFAULT_WORKING_TTL_S) -> None:
            self._r = redis
            self._ttl = ttl_seconds

        def _key(self, session_id: str) -> str:
            return f"{WORKING_KEY_PREFIX}{session_id}"

        async def append_turn(self, session_id: str, text: str) -> None:
            k = self._key(session_id)
            await self._r.rpush(k, text)
            await self._r.ltrim(k, -MAX_WORKING_TURNS, -1)
            await self._r.expire(k, self._ttl)

        async def get_recent_turns(self, session_id: str, limit: int = MAX_WORKING_TURNS) -> list[str]:
            raw = await self._r.lrange(self._key(session_id), -limit, -1)
            out: list[str] = []
            for b in raw:
                if isinstance(b, bytes):
                    out.append(b.decode("utf-8", errors="replace"))
                else:
                    out.append(str(b))
            return out

except ImportError:  # pragma: no cover — optional redis extra
    RedisWorkingStore = None  # type: ignore[misc, assignment]


async def fetch_semantic_facts(engine: AsyncEngine, user_id: str) -> dict[str, str]:
    from sqlalchemy import text

    facts: dict[str, str] = {}
    async with engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT key, value FROM user_facts WHERE user_id = :u ORDER BY key"
            ),
            {"u": user_id},
        )
        for row in res.mappings():
            facts[str(row["key"])] = str(row["value"])
    return facts


async def build_context_window(
    session_id: str,
    user_id: str,
    *,
    working: WorkingMemoryStore,
    semantic_engine: AsyncEngine | None = None,
) -> str:
    """Text block injected ahead of routed prompts — working transcript + Postgres facts."""
    turns = await working.get_recent_turns(session_id)
    parts: list[str] = []
    if turns:
        parts.append("[Recent dialogue]\n" + "\n".join(f"- {t}" for t in turns))
    if semantic_engine is not None:
        facts = await fetch_semantic_facts(semantic_engine, user_id)
        if facts:
            lines = "\n".join(f"{k}: {v}" for k, v in facts.items())
            parts.append("[Known facts about user]\n" + lines)
    return "\n\n".join(parts) if parts else ""


def episodic_collection_name() -> str:
    return "episodic_memory"


def episodic_payload_schema() -> dict[str, Any]:
    """Stored on Qdrant payload for TTL / weekly rollup (not queried per inbound request yet)."""
    return {
        "lead_id": "",
        "text": "",
        "created_at": "",
        "ttl_days_default": 30,
    }


def update_preference_vec(
    previous_vec: list[float], clicked_vec: list[float], alpha: float = 0.2
) -> list[float]:
    """EMA blend for personalization (§3.4 architecture)."""
    if not previous_vec:
        return clicked_vec
    return [((1 - alpha) * p) + (alpha * c) for p, c in zip(previous_vec, clicked_vec, strict=False)]
