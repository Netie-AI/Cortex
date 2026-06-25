"""SQLite warehouse store for V0 (locations, items, movements)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packs.dms.audit.ledger import default_db_path, init_ledger_schema


def _resolve_db_path(db_path: Path | str | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    import os

    env = os.environ.get("DMS_OPS_DB")
    if env:
        return Path(env)
    return default_db_path()


class RLSViolationError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Location:
    id: str
    parent_id: str | None
    kind: str
    code: str
    qr_token: str
    capacity_volume: float | None
    tenant_id: str


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    sku: str
    label: str
    current_location_id: str | None
    photo_uri: str | None
    dims: dict[str, Any] | None
    tenant_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Movement:
    id: str
    item_id: str
    from_location_id: str | None
    to_location_id: str
    actor: str
    method: str
    tenant_id: str
    created_at: str


def _connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_warehouse_schema(con: sqlite3.Connection) -> None:
    init_ledger_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dms_locations (
            id TEXT PRIMARY KEY,
            parent_id TEXT REFERENCES dms_locations(id),
            kind TEXT NOT NULL CHECK(kind IN ('zone','rack','bin')),
            code TEXT NOT NULL UNIQUE,
            qr_token TEXT NOT NULL UNIQUE,
            capacity_volume REAL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dms_items (
            id TEXT PRIMARY KEY,
            sku TEXT NOT NULL,
            label TEXT NOT NULL,
            current_location_id TEXT REFERENCES dms_locations(id),
            photo_uri TEXT,
            dims TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dms_movements (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL REFERENCES dms_items(id),
            from_location_id TEXT REFERENCES dms_locations(id),
            to_location_id TEXT NOT NULL REFERENCES dms_locations(id),
            actor TEXT NOT NULL,
            method TEXT NOT NULL CHECK(method IN ('scan','manual')),
            tenant_id TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL
        );
        """
    )
    con.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_dims(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        sku=row["sku"],
        label=row["label"],
        current_location_id=row["current_location_id"],
        photo_uri=row["photo_uri"],
        dims=_parse_dims(row["dims"]),
        tenant_id=row["tenant_id"],
        created_at=row["created_at"],
    )


def _check_tenant(row_tenant: str, actor_tenant: str) -> None:
    if row_tenant != actor_tenant:
        raise RLSViolationError(f"tenant mismatch: {actor_tenant!r} cannot access {row_tenant!r}")


def create_location(
    *,
    kind: str,
    code: str,
    parent_id: str | None = None,
    capacity_volume: float | None = None,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Location:
    if kind not in ("zone", "rack", "bin"):
        raise ValueError(f"invalid kind: {kind}")
    loc_id = str(uuid.uuid4())
    qr_token = str(uuid.uuid4())
    created_at = _now_iso()
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        con.execute(
            """
            INSERT INTO dms_locations
            (id, parent_id, kind, code, qr_token, capacity_volume, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (loc_id, parent_id, kind, code, qr_token, capacity_volume, tenant_id, created_at),
        )
        con.commit()
    finally:
        con.close()
    return Location(
        id=loc_id,
        parent_id=parent_id,
        kind=kind,
        code=code,
        qr_token=qr_token,
        capacity_volume=capacity_volume,
        tenant_id=tenant_id,
    )


def get_location_by_id(
    location_id: str,
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Location | None:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        row = con.execute(
            "SELECT * FROM dms_locations WHERE id = ? AND tenant_id = ?",
            (location_id, tenant_id),
        ).fetchone()
        if row is None:
            return None
        return Location(
            id=row["id"],
            parent_id=row["parent_id"],
            kind=row["kind"],
            code=row["code"],
            qr_token=row["qr_token"],
            capacity_volume=row["capacity_volume"],
            tenant_id=row["tenant_id"],
        )
    finally:
        con.close()


def get_location_by_code(
    code: str,
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Location | None:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        row = con.execute(
            "SELECT * FROM dms_locations WHERE code = ? AND tenant_id = ?",
            (code, tenant_id),
        ).fetchone()
        if row is None:
            return None
        return Location(
            id=row["id"],
            parent_id=row["parent_id"],
            kind=row["kind"],
            code=row["code"],
            qr_token=row["qr_token"],
            capacity_volume=row["capacity_volume"],
            tenant_id=row["tenant_id"],
        )
    finally:
        con.close()


def get_location_by_qr_token(
    qr_token: str,
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Location | None:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        row = con.execute(
            "SELECT * FROM dms_locations WHERE qr_token = ? AND tenant_id = ?",
            (qr_token, tenant_id),
        ).fetchone()
        if row is None:
            return None
        return Location(
            id=row["id"],
            parent_id=row["parent_id"],
            kind=row["kind"],
            code=row["code"],
            qr_token=row["qr_token"],
            capacity_volume=row["capacity_volume"],
            tenant_id=row["tenant_id"],
        )
    finally:
        con.close()


def list_location_tree(
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        rows = con.execute(
            "SELECT * FROM dms_locations WHERE tenant_id = ? ORDER BY code",
            (tenant_id,),
        ).fetchall()
        nodes = [
            {
                "id": r["id"],
                "parent_id": r["parent_id"],
                "kind": r["kind"],
                "code": r["code"],
                "qr_token": r["qr_token"],
                "capacity_volume": r["capacity_volume"],
            }
            for r in rows
        ]
        by_id = {n["id"]: {**n, "children": []} for n in nodes}
        roots: list[dict[str, Any]] = []
        for n in by_id.values():
            pid = n["parent_id"]
            if pid and pid in by_id:
                by_id[pid]["children"].append(n)
            else:
                roots.append(n)
        return roots
    finally:
        con.close()


def list_items_at_location(
    location_id: str,
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> list[Item]:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        rows = con.execute(
            """
            SELECT * FROM dms_items
            WHERE current_location_id = ? AND tenant_id = ?
            ORDER BY created_at DESC
            """,
            (location_id, tenant_id),
        ).fetchall()
        return [_row_to_item(r) for r in rows]
    finally:
        con.close()


def create_item(
    *,
    sku: str,
    label: str,
    location_id: str,
    photo_uri: str | None,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Item:
    item_id = str(uuid.uuid4())
    created_at = _now_iso()
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        loc = con.execute(
            "SELECT tenant_id FROM dms_locations WHERE id = ?", (location_id,)
        ).fetchone()
        if loc is None:
            raise ValueError(f"unknown location: {location_id}")
        _check_tenant(loc["tenant_id"], tenant_id)
        con.execute(
            """
            INSERT INTO dms_items
            (id, sku, label, current_location_id, photo_uri, dims, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (item_id, sku, label, location_id, photo_uri, tenant_id, created_at),
        )
        con.commit()
    finally:
        con.close()
    return Item(
        id=item_id,
        sku=sku,
        label=label,
        current_location_id=location_id,
        photo_uri=photo_uri,
        dims=None,
        tenant_id=tenant_id,
        created_at=created_at,
    )


def get_item_by_id_or_qr(
    item_ref: str,
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Item | None:
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        row = con.execute(
            "SELECT * FROM dms_items WHERE id = ? AND tenant_id = ?",
            (item_ref, tenant_id),
        ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT * FROM dms_items WHERE sku = ? AND tenant_id = ?",
                (item_ref, tenant_id),
            ).fetchone()
        if row is None:
            return None
        return _row_to_item(row)
    finally:
        con.close()


def update_item_dims(
    item_id: str,
    dims: dict[str, Any],
    *,
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Item:
    for key in ("l", "w", "h"):
        if key not in dims:
            raise ValueError(f"dims missing required key: {key}")
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        row = con.execute("SELECT * FROM dms_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown item: {item_id}")
        _check_tenant(row["tenant_id"], tenant_id)
        con.execute(
            "UPDATE dms_items SET dims = ? WHERE id = ?",
            (json.dumps(dims, sort_keys=True), item_id),
        )
        con.commit()
        updated = con.execute("SELECT * FROM dms_items WHERE id = ?", (item_id,)).fetchone()
        assert updated is not None
        return _row_to_item(updated)
    finally:
        con.close()


def record_movement(
    *,
    item_id: str,
    to_location_id: str,
    actor: str,
    method: str = "scan",
    tenant_id: str = "default",
    db_path: Path | str | None = None,
) -> Movement:
    if method not in ("scan", "manual"):
        raise ValueError(f"invalid method: {method}")
    move_id = str(uuid.uuid4())
    created_at = _now_iso()
    path = _resolve_db_path(db_path)
    con = _connect(path)
    try:
        init_warehouse_schema(con)
        item = con.execute("SELECT * FROM dms_items WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            raise ValueError(f"unknown item: {item_id}")
        _check_tenant(item["tenant_id"], tenant_id)
        dest = con.execute(
            "SELECT tenant_id FROM dms_locations WHERE id = ?", (to_location_id,)
        ).fetchone()
        if dest is None:
            raise ValueError(f"unknown location: {to_location_id}")
        _check_tenant(dest["tenant_id"], tenant_id)
        from_loc = item["current_location_id"]
        con.execute(
            """
            INSERT INTO dms_movements
            (id, item_id, from_location_id, to_location_id, actor, method, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (move_id, item_id, from_loc, to_location_id, actor, method, tenant_id, created_at),
        )
        con.execute(
            "UPDATE dms_items SET current_location_id = ? WHERE id = ?",
            (to_location_id, item_id),
        )
        con.commit()
    finally:
        con.close()
    return Movement(
        id=move_id,
        item_id=item_id,
        from_location_id=from_loc,
        to_location_id=to_location_id,
        actor=actor,
        method=method,
        tenant_id=tenant_id,
        created_at=created_at,
    )


def photos_dir(db_path: Path | str | None = None) -> Path:
    base = _resolve_db_path(db_path).parent
    path = base / "dms_photos"
    path.mkdir(parents=True, exist_ok=True)
    return path
