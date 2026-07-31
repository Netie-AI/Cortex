"""Human gold verification for Phase 1b paraphrases.

The N behind "0 confidently wrong" is only allowed to count questions a person
has read. This is the tool that makes that tractable — it prints one paraphrase
next to its parent seed's intent and gold SQL, and on approval rewrites the YAML
entry from a bare string into ``{q: ..., gold_verified: true, verified_by: ...}``.

Deliberately not automated. A script that marked its own output verified would
turn the gate into decoration.

    python -m bench.verify_gold --status
    python -m bench.verify_gold --review --by aminah
    python -m bench.verify_gold --seed gf_unique_sku_count --index 0 --by aminah
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from bench.corpus import PARAPHRASES_PATH, SEEDS_PATH, load_seeds


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _entry_question(entry: Any) -> str:
    return entry if isinstance(entry, str) else str(entry.get("q", ""))


def _entry_verified(entry: Any, default: bool) -> bool:
    if isinstance(entry, str):
        return default
    return bool(entry.get("gold_verified", default))


def _dump(path: Path, data: dict[str, Any]) -> None:
    """Rewrite the file, preserving the header comment block.

    The header carries the authoring rules; losing it to a round-trip would strip
    the only place those rules are written down next to the data.
    """
    original = path.read_text(encoding="utf-8")
    header_lines: list[str] = []
    for line in original.splitlines():
        if line.startswith("#") or not line.strip():
            header_lines.append(line)
            continue
        break
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    path.write_text("\n".join(header_lines).rstrip() + "\n\n" + body, encoding="utf-8")


def mark_verified(seed_id: str, index: int, by: str, path: Path = PARAPHRASES_PATH) -> str:
    data = _load(path)
    default = bool(data.get("default_gold_verified", False))
    groups = data.get("paraphrases") or {}
    if seed_id not in groups:
        raise SystemExit(f"unknown seed id: {seed_id}")
    variants = groups[seed_id]
    if not 0 <= index < len(variants):
        raise SystemExit(f"{seed_id}: index {index} out of range (0..{len(variants) - 1})")
    question = _entry_question(variants[index])
    if _entry_verified(variants[index], default):
        return question
    variants[index] = {"q": question, "gold_verified": True, "verified_by": by}
    _dump(path, data)
    return question


def status(path: Path = PARAPHRASES_PATH) -> dict[str, Any]:
    data = _load(path)
    default = bool(data.get("default_gold_verified", False))
    groups = data.get("paraphrases") or {}
    total = verified = 0
    per_seed: dict[str, dict[str, int]] = {}
    for seed_id, variants in groups.items():
        v = sum(1 for e in variants or [] if _entry_verified(e, default))
        per_seed[seed_id] = {"total": len(variants or []), "verified": v}
        total += len(variants or [])
        verified += v
    seeds = load_seeds(SEEDS_PATH)
    return {
        "seed_n": len(seeds),
        "paraphrase_n": total,
        "paraphrase_verified": verified,
        "expanded_n": len(seeds) + total,
        "claim_n": len(seeds) + verified,
        "per_seed": per_seed,
    }


def _review(by: str, limit: int, path: Path) -> None:
    data = _load(path)
    default = bool(data.get("default_gold_verified", False))
    groups = data.get("paraphrases") or {}
    by_id = {s.id: s for s in load_seeds(SEEDS_PATH)}
    reviewed = 0

    for seed_id, variants in groups.items():
        parent = by_id.get(seed_id)
        if parent is None:
            continue
        for i, entry in enumerate(list(variants or [])):
            if _entry_verified(entry, default) or reviewed >= limit:
                continue
            print("\n" + "=" * 78)
            print(f"parent   {seed_id}  [{parent.category} · {parent.match}]")
            print(f"intent   {parent.engineer_intent}")
            print(f"gold     {(parent.canonical_sql or '(abstain / blocked)').strip()[:200]}")
            print(f"seed q   {parent.question}")
            print(f"variant  {_entry_question(entry)}")
            answer = input("same question, same gold? [y/N/q] ").strip().lower()
            if answer == "q":
                return
            if answer == "y":
                mark_verified(seed_id, i, by, path)
                print("  → verified")
            reviewed += 1


def main() -> None:
    p = argparse.ArgumentParser(description="Human gold verification for Phase 1b")
    p.add_argument("--path", type=Path, default=PARAPHRASES_PATH)
    p.add_argument("--status", action="store_true", help="Print verification progress")
    p.add_argument("--review", action="store_true", help="Interactive review loop")
    p.add_argument("--by", default="", help="Reviewer name recorded on each approval")
    p.add_argument("--limit", type=int, default=25, help="Max items per review session")
    p.add_argument("--seed", default=None, help="Mark one item: seed id")
    p.add_argument("--index", type=int, default=None, help="Mark one item: 0-based index")
    args = p.parse_args()

    if args.seed is not None:
        if args.index is None or not args.by:
            raise SystemExit("--seed requires --index and --by")
        q = mark_verified(args.seed, args.index, args.by, args.path)
        print(f"verified {args.seed}#p{args.index + 1}: {q}")
        return

    if args.review:
        if not args.by:
            raise SystemExit("--review requires --by <name>")
        if not sys.stdin.isatty():
            raise SystemExit("--review needs an interactive terminal")
        _review(args.by, args.limit, args.path)

    s = status(args.path)
    print(
        f"seeds={s['seed_n']}  paraphrases={s['paraphrase_n']} "
        f"(verified {s['paraphrase_verified']})\n"
        f"expanded_n={s['expanded_n']}  claim_n={s['claim_n']}"
    )


if __name__ == "__main__":
    main()
