from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(slots=True)
class NodeExecutionRecord:
    run_id: str
    node_id: str
    tier: str
    model: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_myr: float
    cache_hit: bool
    started_at: datetime
    ended_at: datetime
    status: str
    ceiling_myr: float | None = None  # effective cap snapshot for tuning / analytics
    error: str | None = None


class CostLedger:
    """
    Async persistence to Postgres (node_executions) when `engine` is set.
    In-process `_run_totals` caches cumulative MYR per run for fast ceiling checks within a worker.

    Before each spend, call ``enforce_ceiling(..., projected_additional_myr=...)``.
    Across workers/restarts call ``hydrate_run_total`` at run start so `_run_totals` matches DB.
    """

    _INSERT_SQL = """
        INSERT INTO node_executions (
            run_id, node_id, tier, model, latency_ms,
            prompt_tokens, completion_tokens, cost_myr, ceiling_myr,
            cache_hit, started_at, ended_at, status, error
        ) VALUES (
            :run_id, :node_id, :tier, :model, :latency_ms,
            :prompt_tokens, :completion_tokens, :cost_myr, :ceiling_myr,
            :cache_hit, :started_at, :ended_at, :status, :error
        )
    """

    def __init__(self, engine: "AsyncEngine | None" = None) -> None:
        self._engine = engine
        self._records: list[NodeExecutionRecord] = []
        self._run_totals: dict[str, float] = defaultdict(float)

    def total_cost(self, run_id: str) -> float:
        return round(self._run_totals.get(run_id, 0.0), 6)

    def enforce_ceiling(
        self,
        run_id: str,
        ceiling_myr: float,
        *,
        projected_additional_myr: float = 0.0,
    ) -> bool:
        return (self.total_cost(run_id) + projected_additional_myr) <= ceiling_myr + 1e-12

    async def add(self, record: NodeExecutionRecord) -> None:
        if self._engine is not None:
            from sqlalchemy import text

            async with self._engine.begin() as conn:
                await conn.execute(
                    text(self._INSERT_SQL),
                    {
                        "run_id": record.run_id,
                        "node_id": record.node_id,
                        "tier": record.tier,
                        "model": record.model,
                        "latency_ms": record.latency_ms,
                        "prompt_tokens": record.prompt_tokens,
                        "completion_tokens": record.completion_tokens,
                        "cost_myr": record.cost_myr,
                        "ceiling_myr": record.ceiling_myr,
                        "cache_hit": record.cache_hit,
                        "started_at": record.started_at,
                        "ended_at": record.ended_at,
                        "status": record.status,
                        "error": record.error,
                    },
                )
        self._records.append(record)
        self._run_totals[record.run_id] = round(
            self._run_totals[record.run_id] + record.cost_myr, 6
        )

    async def hydrate_run_total(self, run_id: str) -> float:
        if self._engine is None:
            return self.total_cost(run_id)
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            row = await conn.execute(
                text(
                    "SELECT COALESCE(SUM(cost_myr), 0) AS s FROM node_executions WHERE run_id = :r"
                ),
                {"r": run_id},
            )
            total = float(row.scalar_one() or 0)
        self._run_totals[run_id] = round(total, 6)
        return self.total_cost(run_id)

    def records(self) -> list[NodeExecutionRecord]:
        return list(self._records)

    def records_for_run(self, run_id: str) -> list[NodeExecutionRecord]:
        return [record for record in self._records if record.run_id == run_id]

    def has_node_record(self, run_id: str, node_id: str) -> bool:
        return any(r.run_id == run_id and r.node_id == node_id for r in self._records)

    async def ensure_node_record(
        self,
        run_id: str,
        node_id: str,
        *,
        tier: str,
        cost_myr: float = 0.0,
        ceiling_myr: float | None = None,
    ) -> None:
        """Write a ledger row when a node finished without ``invoke_routed_completion``."""
        if self.has_node_record(run_id, node_id):
            return
        ts = now_utc()
        await self.add(
            NodeExecutionRecord(
                run_id=run_id,
                node_id=node_id,
                tier=tier,
                model="none",
                latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
                cost_myr=round(cost_myr, 6),
                cache_hit=False,
                started_at=ts,
                ended_at=ts,
                status="ok",
                ceiling_myr=ceiling_myr,
                error=None,
            )
        )

    _SELECT_RUN_SQL = """
        SELECT run_id, node_id, tier, model, latency_ms,
               prompt_tokens, completion_tokens, cost_myr, ceiling_myr,
               cache_hit, started_at, ended_at, status, error
        FROM node_executions
        WHERE run_id = :r
        ORDER BY started_at ASC
    """

    async def _load_records_from_db(self, run_id: str) -> list[NodeExecutionRecord]:
        if self._engine is None:
            return []
        from sqlalchemy import text

        async with self._engine.connect() as conn:
            result = await conn.execute(text(self._SELECT_RUN_SQL), {"r": run_id})
            rows = result.mappings().all()
        out: list[NodeExecutionRecord] = []
        for row in rows:
            out.append(
                NodeExecutionRecord(
                    run_id=str(row["run_id"]),
                    node_id=str(row["node_id"]),
                    tier=str(row["tier"]),
                    model=str(row["model"]),
                    latency_ms=int(row["latency_ms"] or 0),
                    prompt_tokens=int(row["prompt_tokens"] or 0),
                    completion_tokens=int(row["completion_tokens"] or 0),
                    cost_myr=float(row["cost_myr"] or 0),
                    cache_hit=bool(row["cache_hit"]),
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    status=str(row["status"]),
                    ceiling_myr=float(row["ceiling_myr"]) if row["ceiling_myr"] is not None else None,
                    error=str(row["error"]) if row["error"] is not None else None,
                )
            )
        return out

    async def fetch_records_for_run(self, run_id: str) -> list[NodeExecutionRecord]:
        """Merge Postgres rows (when configured) with this worker's in-process cache."""
        db_records = await self._load_records_from_db(run_id)
        seen = {(r.node_id, r.started_at) for r in db_records}
        merged = list(db_records)
        for record in self.records_for_run(run_id):
            key = (record.node_id, record.started_at)
            if key not in seen:
                merged.append(record)
                seen.add(key)
        return merged

    def export_dicts(self) -> list[dict[str, Any]]:
        return [asdict(record) for record in self._records]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
