"""CERT-ORACLE-SPLIT (#343): the climb/score oracle is not the serve set.

``packs/dms/semantic/certified_queries.yaml`` is a serving input: L0 answers
from it and ``certified_serve`` serves its SQL as stored. It was seeded from
``bench/golden/dms_golden_v1.yaml``. A score graded against that file, or on a
case that file already answers, is memorised by construction.

Gold lives in ``bench/oracle/climb_oracle_v1.yaml``. No serving module opens it
(must-fail: ``tests/test_crew/test_cert_oracle_split.py``; import-linter
contract ``serve-never-reads-oracle``). This module opens the serve set only
to exclude from scoring: a case whose id, or whose normalised question text
(the certified question or a synonym), is in the serve set earns no score and
is listed by name with a named reason. ``dms_golden_v1`` counts toward no
score until it is re-split.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml

ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = ROOT / "bench" / "oracle" / "climb_oracle_v1.yaml"
SERVE_SET_REL = Path("semantic") / "certified_queries.yaml"

GOLDEN_UNSPLIT = "dms_golden_v1"
SERVE_SET_ID = "serve_set_id"
SERVE_SET_QUESTION = "serve_set_question"
GOLDEN_NOT_RESPLIT = "dms_golden_v1_not_resplit"
ALL_EXCLUDED = "all_cases_excluded"
GOLDEN_NOTE = (
    "dms_golden_v1 seeded the L0 serve set (packs/dms/semantic/certified_queries.yaml); "
    "it counts toward no score until it is re-split"
)

T = TypeVar("T")


def norm_question(text: Any) -> str:
    """The certified loader's phrase key: lowercase, non-alphanumerics to one space."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def oracle_gold_sql(path: Path | str | None = None) -> dict[str, str]:
    """Gold SQL by case id from the oracle file. A missing file is no gold, not invented gold."""
    out: dict[str, str] = {}
    for row in _read(Path(path) if path is not None else ORACLE_PATH).get("gold") or []:
        if not isinstance(row, Mapping):
            continue
        cid = str(row.get("id") or "").strip()
        sql = str(row.get("sql") or "").strip()
        if cid and sql:
            out[cid] = sql
    return out


def serve_set(pack_dir: Path | str | None = None) -> tuple[frozenset[str], dict[str, str]]:
    """(certified ids, normalised question or synonym -> certified id) of the L0 serve set."""
    if pack_dir is None:
        from CortexOS.ontology.registry import pack_dir_for

        pack_dir = pack_dir_for("dms")
    ids: set[str] = set()
    questions: dict[str, str] = {}
    for row in _read(Path(pack_dir) / SERVE_SET_REL).get("certified") or []:
        if not isinstance(row, Mapping):
            continue
        cid = str(row.get("id") or "").strip()
        if not cid:
            continue
        ids.add(cid)
        for phrase in (row.get("question"), *(row.get("synonyms") or [])):
            key = norm_question(phrase)
            if key:
                questions.setdefault(key, cid)
    return frozenset(ids), questions


def serve_hit(
    case_id: str, question: str, serve: tuple[frozenset[str], dict[str, str]]
) -> str:
    """Named reason this case is in the serve set, or "" when it is not."""
    ids, questions = serve
    cid = str(case_id or "").strip()
    if cid and cid in ids:
        return f"{SERVE_SET_ID}:{cid}"
    hit = questions.get(norm_question(question))
    return f"{SERVE_SET_QUESTION}:{hit}" if hit else ""


def split_scored(
    cases: Iterable[T],
    ident: Callable[[T], tuple[str, str]],
    *,
    corpus: str = "",
    pack_dir: Path | str | None = None,
) -> tuple[list[T], list[dict[str, str]]]:
    """(cases that may be scored, excluded rows ``{id, reason[, serve]}``)."""
    serve = serve_set(pack_dir)
    unsplit = (corpus or "").strip() == GOLDEN_UNSPLIT
    kept: list[T] = []
    excluded: list[dict[str, str]] = []
    for case in cases:
        cid, question = ident(case)
        hit = serve_hit(cid, question, serve)
        if unsplit:
            row = {"id": cid, "reason": GOLDEN_NOT_RESPLIT}
            if hit:
                row["serve"] = hit
            excluded.append(row)
        elif hit:
            excluded.append({"id": cid, "reason": hit})
        else:
            kept.append(case)
    return kept, excluded


def stamp(
    report: dict[str, Any],
    excluded: Iterable[Mapping[str, Any]],
    *,
    counts_toward_score: bool = True,
) -> dict[str, Any]:
    """Name every excluded case on the report. Never a silent drop."""
    rows = [dict(row) for row in excluded]
    report["excluded"] = rows
    report["excluded_ids"] = [str(row.get("id") or "") for row in rows]
    report["excluded_n"] = len(rows)
    report["counts_toward_score"] = counts_toward_score
    if not counts_toward_score:
        report["score_note"] = GOLDEN_NOTE
    return report


__all__ = [
    "ALL_EXCLUDED",
    "GOLDEN_NOTE",
    "GOLDEN_NOT_RESPLIT",
    "GOLDEN_UNSPLIT",
    "ORACLE_PATH",
    "SERVE_SET_ID",
    "SERVE_SET_QUESTION",
    "norm_question",
    "oracle_gold_sql",
    "serve_hit",
    "serve_set",
    "split_scored",
    "stamp",
]
