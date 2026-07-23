"""Audit for the crm pack — the F1 hash-chained ledger is pack-agnostic,
so this REUSES packs/dms/audit/ledger.py rather than copying it.
"""
from packs.dms.audit.ledger import (  # noqa: F401 — re-export the shared spine
    append,
    default_db_path,
    list_entries,
    verify,
)
