"""DuckDB warehouse loader + semantic layer helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "dms_demo.duckdb"
DEFAULT_SEMANTIC = ROOT / "packs" / "dms" / "semantic_layer.yaml"
DEFAULT_CLEAN_CSV = ROOT / "data" / "samples" / "warehouse_clean.csv"


def load_semantic_layer(path: Path | str | None = None) -> dict[str, Any]:
    path = Path(path or DEFAULT_SEMANTIC)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_inventory_csv(
    csv_path: Path | str | None = None,
    db_path: Path | str | None = None,
) -> Path:
    import duckdb

    csv_path = Path(csv_path or DEFAULT_CLEAN_CSV)
    db_path = Path(db_path or DEFAULT_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        con.execute("DROP TABLE IF EXISTS inventory")
        con.execute(
            f"""
            CREATE TABLE inventory AS
            SELECT * FROM read_csv_auto('{csv_path.as_posix()}', header=true)
            """
        )
        con.execute(
            """
            ALTER TABLE inventory
            ALTER COLUMN quantity_kg TYPE DOUBLE
            """
        )
        con.execute(
            """
            ALTER TABLE inventory
            ALTER COLUMN reorder_level TYPE INTEGER
            """
        )
    finally:
        con.close()
    return db_path


def get_connection(db_path: Path | str | None = None):
    import duckdb

    return duckdb.connect(str(db_path or DEFAULT_DB))


def main() -> None:
    load_inventory_csv()
    print(f"DuckDB loaded at {DEFAULT_DB}")


if __name__ == "__main__":
    main()
