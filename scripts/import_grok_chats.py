"""Ingest Grok Bot / Cursor Grok transcripts into Crew. Never reads AppData blobs."""

from __future__ import annotations

import json
from pathlib import Path

from CortexOS.crew.import_chats import ingest, parse_export
from CortexOS.crew.store import CrewStore

TRANSCRIPTS = Path(r"C:\Users\OoiJianHong\.cursor\projects")

# Prior Grok-bot chats we reuse for specialist tone (SearchConversations 2026-08-22).
GROK_IDS = (
    "5d07d8b2-7531-43b0-ac24-85255ddebd14",  # Grok bot agent updates
    "acce924e-8440-49d8-81cb-c4c444a5be39",  # Grok Bot connectivity
    "fb051151-3659-446d-98f7-9f2b08514880",  # Grok Bot vs Perplexity
    "18d7109a-cd12-4845-b09b-fd06729f33c8",  # Cortex automation build
    "62c869fe-73a6-4d91-8779-e477f5bd622a",  # Cortex automation / Pointer
    "d0416a9b-85f7-4d57-bf9f-c59254e4e074",  # Grok bot monthly limit
    "a88f874b-0e88-44b6-a86a-4a3f00dd1efd",  # leftover Grok fleet
    "9536be94-4e8d-4b09-9937-770915f90f10",  # GROK_SYNC / no fourth orchestrator
    "6a72678b-7580-4ad6-ab2d-31b0c56f26fc",  # Grok bot orchestration insights
    "adcd2964-de59-4460-9620-3cd4de0421b8",  # Grok bot landing page
    "cee06f28-4de0-4bcb-a239-b4e6e8c65551",  # Agent orchestration workflow
    "be41b451-8492-4658-a0fd-80d5f076c3ae",  # Cortex build for hackathon
    "0265bdf2-ac8c-4bcd-9b1c-c851fca26732",  # Agent system design
)


def _jsonl_path(conv_id: str) -> Path | None:
    matches = sorted(TRANSCRIPTS.glob(f"**/agent-transcripts/**/{conv_id}.jsonl"))
    return matches[0] if matches else None


def _title_from(path: Path, fallback: str) -> str:
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        row = json.loads(first[0]) if first else {}
    except (OSError, ValueError):
        row = {}
    msg = row.get("message") if isinstance(row, dict) else {}
    content = (msg or {}).get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                line = str(block.get("text") or "").splitlines()[0].strip()
                if line:
                    return ("Grok: " + line)[:80]
    return fallback


def main() -> None:
    import os

    data_dir = Path(os.environ.get("CREW_DATA_DIR") or Path(r"D:\Cortex-crew\data\crew"))
    data_dir.mkdir(parents=True, exist_ok=True)
    store = CrewStore(data_dir / "crew.db")
    imported = 0
    skipped = 0
    for conv_id in GROK_IDS:
        path = _jsonl_path(conv_id)
        if path is None:
            skipped += 1
            print(f"skip missing {conv_id}")
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        turns = parse_export(raw)
        if not turns:
            skipped += 1
            print(f"skip empty {conv_id}")
            continue
        if len(turns) > 60:
            kept = turns[:40] + [{"role": "system", "content": f"...truncated {len(turns) - 50} turns..."}] + turns[-10:]
            raw = "\n\n".join(f"# {t['role']}\n{t['content'][:1500]}" for t in kept)
        title = _title_from(path, f"Grok {conv_id[:8]}")
        result = ingest(store, title, raw)
        imported += 1
        print(f"ok {title} count={result['count']} space={result['space']['id']}")
    store.close()
    print(f"imported={imported} skipped={skipped}")


if __name__ == "__main__":
    main()
