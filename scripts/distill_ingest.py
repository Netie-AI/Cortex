"""Ingest skill_distill captures → learned index + PARKING_LOT P19 + DISTILL stamp.

CLI:
  python scripts/distill_ingest.py
  python scripts/distill_ingest.py --capture skill_distill/captures/foo.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISTILL_ROOT = ROOT / "skill_distill"
CAPTURES = DISTILL_ROOT / "captures"
LEARNED = DISTILL_ROOT / "learned"
DISTILL_MD = DISTILL_ROOT / "DISTILL.md"
PARKING = ROOT / "PARKING_LOT.md"
INDEX = LEARNED / "INDEX.md"

PROMOTE_RE = re.compile(r"promote:\s*(rule|skill|subagent|parking|none)", re.I)
FACT_ROW_RE = re.compile(
    r"^\|\s*(.+?)\s*\|\s*(observed|docs|inferred)\s*\|\s*(high|med|low)\s*\|\s*(rule|skill|subagent|parking|none)\s*\|",
    re.I | re.M,
)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _list_captures(explicit: Path | None) -> list[Path]:
    if explicit:
        return [explicit]
    return sorted(
        p for p in CAPTURES.glob("*.md") if p.name != "_TEMPLATE.md" and p.is_file()
    )


def _parse_capture(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    facts = [
        {
            "fact": m.group(1).strip(),
            "evidence": m.group(2).lower(),
            "confidence": m.group(3).lower(),
            "promote": m.group(4).lower(),
        }
        for m in FACT_ROW_RE.finditer(text)
    ]
    promotes = [m.group(1).lower() for m in PROMOTE_RE.finditer(text)]
    parking_needed = any(p == "parking" for p in promotes) or any(
        f["promote"] == "parking" for f in facts
    )
    return {
        "path": path,
        "rel": path.relative_to(ROOT).as_posix(),
        "facts": facts,
        "parking_needed": parking_needed,
        "bytes": len(text),
    }


def _update_index(parsed: list[dict]) -> None:
    LEARNED.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Learned index",
        "",
        "Normalized facts from `captures/`. Updated by `scripts/distill_ingest.py`.",
        "",
        f"_Last ingest: {_now()}_",
        "",
        "| Date | Capture | Facts | Promotions |",
        "|------|---------|-------|------------|",
    ]
    for item in parsed:
        promos = ", ".join(sorted({f["promote"] for f in item["facts"]})) or "—"
        date = item["path"].name[:10] if item["path"].name[:1].isdigit() else "—"
        lines.append(
            f"| {date} | `{item['rel']}` | {len(item['facts'])} | {promos} |"
        )
    lines.extend(
        [
            "",
            "## Auto-extracted facts",
            "",
        ]
    )
    for item in parsed:
        if not item["facts"]:
            continue
        lines.append(f"### {item['rel']}")
        lines.append("")
        for f in item["facts"]:
            lines.append(
                f"- ({f['confidence']}/{f['evidence']} → {f['promote']}) {f['fact']}"
            )
        lines.append("")
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stamp_distill(n: int) -> None:
    if not DISTILL_MD.exists():
        return
    text = DISTILL_MD.read_text(encoding="utf-8")
    stamp = f"| Last process | {_now()} ({n} capture(s) ingested) |"
    if "| Last process |" in text:
        text = re.sub(r"\| Last process \|.*?\|", stamp, text, count=1)
    else:
        text = text.replace(
            "| Owners | Netie engine (Cortex) builders |",
            f"| Owners | Netie engine (Cortex) builders |\n{stamp}",
        )
    DISTILL_MD.write_text(text, encoding="utf-8")


def _ensure_parking_p19(parsed: list[dict]) -> None:
    if not PARKING.exists():
        return
    text = PARKING.read_text(encoding="utf-8")
    if "## P19 —" not in text:
        block = """
## P19 — Skill distill continuous learning (Claude Code + Cursor → Netie)
Keep asking Claude Code / Cursor / Claude.app how they implement memory, RAG,
tools/MCP, skills, subagents, one-shot plan, multitask, cloud deploy/scale.
Store under `skill_distill/captures/`; always trace `skill_distill/DISTILL.md`.
Ingest: `python scripts/distill_ingest.py`. Promote survivors to `.cursor/` rules/skills
and Cortex discovery; defer the rest here with `distill:` cites.
**Condition:** ongoing — process after every major orchestration learning.
**Seed:** Claude Capabilities tool lazy-load + Skills/Connectors/Plugins split
(`skill_distill/sources/claude_capabilities_2026-07-24.md`).

### P19 open distill debts
"""
        # Insert before "## Move out of parking lot"
        marker = "## Move out of parking lot"
        if marker in text:
            text = text.replace(marker, block + "\n" + marker)
        else:
            text += "\n" + block
        PARKING.write_text(text, encoding="utf-8")

    debts: list[str] = []
    for item in parsed:
        if not item["parking_needed"] and not any(
            f["promote"] == "parking" for f in item["facts"]
        ):
            # Still record captures that explicitly mention park in body
            body = item["path"].read_text(encoding="utf-8", errors="replace").lower()
            if "park" not in body and "defer" not in body:
                continue
        for f in item["facts"]:
            if f["promote"] == "parking":
                debts.append(
                    f"- [ ] {f['fact'][:160]} — `distill: {item['rel']}`"
                )
        if item["parking_needed"] and not any(f["promote"] == "parking" for f in item["facts"]):
            debts.append(f"- [ ] Review capture for deferred items — `distill: {item['rel']}`")

    if not debts:
        return

    # Append unique debt lines under P19 open distill debts
    existing = PARKING.read_text(encoding="utf-8")
    for line in debts:
        if line.split("`distill:")[-1] in existing and line in existing:
            continue
        if line not in existing:
            existing = existing.replace(
                "### P19 open distill debts\n",
                "### P19 open distill debts\n" + line + "\n",
                1,
            )
    PARKING.write_text(existing, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=None)
    args = parser.parse_args()
    paths = _list_captures(args.capture.resolve() if args.capture else None)
    if not paths:
        print("No captures found (only _TEMPLATE.md). Write a capture first.")
        return
    parsed = [_parse_capture(p) for p in paths]
    _update_index(parsed)
    _stamp_distill(len(parsed))
    _ensure_parking_p19(parsed)
    print(f"Ingested {len(parsed)} capture(s).")
    print(f"  index -> {INDEX.relative_to(ROOT)}")
    print(f"  trace -> {DISTILL_MD.relative_to(ROOT)}")
    print(f"  parking -> {PARKING.relative_to(ROOT)} (P19)")


if __name__ == "__main__":
    main()
