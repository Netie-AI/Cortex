"""OpenDMS ingest (L1) — Auto-Loader-analog file intake into the bronze lake.

Exactly-once by content hash, quarantine-on-failure, no partial commits. See
docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md Feature L1.
"""
from packs.dms.ingest.loader import (
    IngestResult,
    ingest_folder,
    ledger_entries,
    load_one,
    scan,
)

__all__ = ["IngestResult", "ingest_folder", "ledger_entries", "load_one", "scan"]
