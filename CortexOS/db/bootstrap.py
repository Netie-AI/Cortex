"""Postgres bootstrap: async engine + DDL from ``node_executions.sql``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


def migration_sql_paths() -> list[Path]:
    root = Path(__file__).resolve().parent / "sql"
    paths = sorted(root.glob("*.sql"))
    return [p for p in paths if p.is_file()]


def split_sql_migration_script(script: str) -> list[str]:
    """Split a simple Postgres DDL file into executable statements."""
    lines: list[str] = []
    for line in script.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    statements: list[str] = []
    for block in cleaned.split(";"):
        stmt = block.strip()
        if stmt:
            statements.append(stmt + ";")
    return statements


async def apply_node_executions_schema(conn: AsyncConnection) -> None:
    from sqlalchemy import text

    for path in migration_sql_paths():
        raw = path.read_text(encoding="utf-8")
        for stmt in split_sql_migration_script(raw):
            await conn.execute(text(stmt))


def create_async_engine_from_url(database_url: str, **kw: Any) -> AsyncEngine:
    """Build a SQLAlchemy async engine; normalizes ``postgres://`` to asyncpg dialect."""
    from sqlalchemy.ext.asyncio import create_async_engine

    url = _normalize_database_url(database_url.strip())
    return create_async_engine(url, pool_pre_ping=True, **kw)


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    raise ValueError(
        "DATABASE_URL must use postgresql://, postgres://, or postgresql+asyncpg:// "
        "(other drivers are unsupported for Cortex async Postgres)."
    )


async def init_database_engine(database_url: str | None) -> AsyncEngine | None:
    """
    Create engine and apply bundled migrations.

    Returns ``None`` when ``database_url`` is empty so callers can degrade to an
    in-memory ledger without failing startup.
    """
    if not database_url or not database_url.strip():
        return None

    engine = create_async_engine_from_url(database_url)
    async with engine.begin() as conn:
        await apply_node_executions_schema(conn)
    return engine


async def dispose_engine(engine: AsyncEngine | None) -> None:
    if engine is not None:
        await engine.dispose()
