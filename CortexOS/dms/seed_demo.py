"""Seed demo warehouse locations (run via python -m CortexOS.dms.seed_demo)."""

from __future__ import annotations

import sqlite3

from packs.dms.vision import locations


def seed_demo_warehouse(db_path: str | None = None) -> None:
    kwargs = {"db_path": db_path} if db_path else {}
    try:
        locations.build_location(kind="zone", code="Z-DEMO", **kwargs)
        locations.build_location(kind="rack", code="R-D01", parent_code="Z-DEMO", **kwargs)
        locations.build_location(
            kind="bin", code="B-D01-A", parent_code="R-D01", capacity_volume=50.0, **kwargs
        )
        locations.build_location(
            kind="bin", code="B-D01-B", parent_code="R-D01", capacity_volume=50.0, **kwargs
        )
        print("Warehouse demo locations seeded (Z-DEMO / B-D01-A / B-D01-B)")
    except sqlite3.IntegrityError:
        print("Warehouse locations already seeded")


def main() -> None:
    seed_demo_warehouse()


if __name__ == "__main__":
    main()
