"""Background weekly job: episodic (Qdrant) → condensed semantic facts (Postgres)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler


async def run_weekly_episodic_to_semantic_rollup(*, postgres_engine: Any = None, qdrant_client: Any = None) -> int:
    """
    Stub entry point until episodic ingestion + T1 condensation are wired.
    Intended flow: enumerate active leads, pull last-7-days episodic from Qdrant,
    distill to 3–5 rows in ``user_facts`` via routed T1."""
    del postgres_engine, qdrant_client
    _ = ()
    return 0


def register_weekly_summarizer(
    scheduler: "AsyncIOScheduler",
    *,
    job: Callable[[], Awaitable[int]] | None = None,
) -> None:
    """Attach Sunday 03:00 (server local TZ) rollup job."""
    from apscheduler.triggers.cron import CronTrigger

    runner = job or run_weekly_episodic_to_semantic_rollup

    async def _wrapped() -> None:
        await runner()

    scheduler.add_job(
        _wrapped,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="netie_weekly_episodic_rollup",
        replace_existing=True,
    )


def shutdown_scheduler(scheduler: "AsyncIOScheduler | None") -> None:
    """Best-effort stop for process teardown."""
    if scheduler is None:
        return
    scheduler.shutdown(wait=False)
