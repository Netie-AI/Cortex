"""CREW-INSIGHTS: intent -> ontology (where + importance) -> constrained DMS ask.

Crew never writes SQL. Ranked ontology locations and metrics pick governed
trial questions; ``EngineBridge`` runs them on ``POST /dms/query``. Envelopes
are CERTIFIED, ABSTAIN, or REFUSE. Every emitted number carries include /
exclude / unsure provenance. Missing provenance is fail-closed REFUSE.
Numbers are never invented or padded.

Excel/PPT/Copilot stay downstream (#197-#199). Heavy export should use the
existing Cloudflare Computer isolate hook (CREW-RUNTIME), not laptop Act.
1GB -> 10TB is a design target only -- not COMPLETE.

Keys stay on the OpenVault-armed Crew engine bridge. No second vault.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from CortexOS.crew.config import BACKEND_CF_COMPUTER
from CortexOS.crew.engine_bridge import EngineBridge
from CortexOS.crew.shell import CF_COMPUTER_SOURCE
from CortexOS.ontology.registry import load_link_types, load_object_types, pack_dir_for

LAW = (
    "Intent then ontology (where to get data + which data is more important) "
    "then constrained DMS ask. CERTIFIED | ABSTAIN | REFUSE. Never invent "
    "numbers. Excel/PPT deferred to #197-#199. 1GB to 10TB is a design target "
    "only, not COMPLETE. Keys via the existing OpenVault-armed Crew engine "
    "bridge. No second vault. Prefer Cloudflare Computer isolate for later "
    "heavy export; this slice is ask+ontology only."
)

STATUSES = ("CERTIFIED", "ABSTAIN", "REFUSE")
MAX_TRIALS = 3

# Engine badges this spine will certify. L2/generated is free-form: refuse.
_CERTIFY_BADGES = frozenset(
    {
        "certified",
        "governed",
        "governed_metric",
        "query_skill",
        "l0",
        "l1",
    }
)
_ABSTAIN_BADGES = frozenset(
    {"abstain", "needs_clarification", "l3", "abstained"}
)

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "and",
        "or",
        "to",
        "is",
        "are",
        "how",
        "many",
        "what",
        "which",
        "show",
        "list",
        "me",
        "please",
        "our",
        "do",
        "does",
        "can",
        "i",
        "we",
        "you",
        "with",
        "from",
        "by",
        "at",
        "be",
        "this",
        "that",
        "have",
        "has",
        "was",
        "were",
        "it",
        "as",
        "if",
        "not",
        "all",
        "any",
        "per",
    }
)
_FROM_RE = re.compile(r"\b(?:FROM|JOIN)\s+(?:[\w]+\.)?([A-Za-z_][\w]*)", re.I)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[a-z0-9]+")


def public_law(*, shell_public: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET map. No ask. No numbers. Control may display."""
    return {
        "ok": True,
        "law": LAW,
        "execute": "POST /crew/insights",
        "ontology": "GET /crew/insights/ontology?q=",
        "statuses": list(STATUSES),
        "excel_ppt": "deferred #197 #198 #199",
        "scale": "1GB to 10TB is a design target only; not COMPLETE",
        "vault": "OpenVault-armed Crew engine bridge only. No second vault.",
        "export_runtime": export_runtime_hint(shell_public),
        "agents": (
            "Retrieve ontology first: locations are where to read; ranked "
            "metrics are which data is more important. Then ask. Do not skip "
            "to SQL or export."
        ),
    }


def export_runtime_hint(shell_public: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reuse CREW-RUNTIME isolate. Do not run Excel/PPT here."""
    snap = dict(shell_public or {})
    backend = str(snap.get("backend") or "laptop")
    return {
        "prefer": BACKEND_CF_COMPUTER,
        "backend": backend,
        "source": CF_COMPUTER_SOURCE,
        "this_slice": "ask+ontology spine",
        "heavy_export": False,
        "excel_ppt": "deferred #197 #198 #199",
        "production": False,
        "preview": True,
    }


def _fold(token: str) -> str:
    t = token.lower()
    if len(t) > 3 and t.endswith("s"):
        return t[:-1]
    return t


def tokens(text: str) -> frozenset[str]:
    raw = _WORD_RE.findall((text or "").lower())
    out: set[str] = set()
    for word in raw:
        if word in _STOP or len(word) < 2:
            continue
        out.add(word)
        out.add(_fold(word))
    return frozenset(out)


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _tables_in_sql(sql: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _FROM_RE.finditer(sql or ""):
        name = match.group(1).lower()
        if name not in found:
            found.append(name)
    return tuple(found)


def _numbers_from_text(text: str) -> set[str]:
    found: set[str] = set()
    for raw in _NUM_RE.findall(text or ""):
        found.add(_num_key(raw))
    return found


def _num_key(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:.12g}"
        return text
    text = str(value).strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _numbers_from_rows(rows: Any) -> set[str]:
    found: set[str] = set()
    if not isinstance(rows, list):
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        for val in row.values():
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                found.add(_num_key(val))
            elif isinstance(val, str):
                found.update(_numbers_from_text(val))
    return found


def _score_overlap(intent: frozenset[str], hay: frozenset[str]) -> int:
    return len(intent & hay)


def _synonym_hit(intent_norm: str, phrases: list[str]) -> tuple[int, str]:
    """Longest phrase contained in the intent wins."""
    best_score = 0
    best = ""
    for raw in phrases:
        phrase = _norm(raw)
        if not phrase:
            continue
        words = [w for w in phrase.split() if w not in _STOP]
        if phrase == intent_norm:
            score = 50 + 10 * max(1, len(words))
        elif phrase and phrase in intent_norm:
            score = 20 + 8 * max(1, len(words))
        else:
            overlap = _score_overlap(tokens(phrase), tokens(intent_norm))
            score = overlap * 2 if overlap else 0
        if score > best_score:
            best_score = score
            best = raw.strip()
    return best_score, best


def _pack_dir(pack_dir: Path | str | None = None) -> Path:
    """DMS ask spine. Do not follow PACK=ruma; that pack has no ontology YAML."""
    if pack_dir is not None:
        return Path(pack_dir)
    return pack_dir_for("dms")


def retrieve_ontology(intent: str, *, pack_dir: Path | str | None = None) -> dict[str, Any]:
    """Where + importance ranking. No engine ask. No numbers."""
    text = (intent or "").strip()
    intent_norm = _norm(text)
    intent_tok = tokens(text)
    base = _pack_dir(pack_dir)
    try:
        objects = load_object_types(base)
        links = load_link_types(base)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "phase": "ontology",
            "intent": text,
            "locations": [],
            "metrics": [],
            "certified": [],
            "joins": [],
            "law": "Ontology first.",
            "refuse_reason": f"ontology yaml unreadable: {exc}",
        }
    layer = _read_yaml(base / "semantic_layer.yaml")
    metrics_doc = _read_yaml(base / "semantic" / "metrics.yaml")
    certified_doc = _read_yaml(base / "semantic" / "certified_queries.yaml")
    glossary = list(layer.get("glossary") or [])
    sensitive = {str(c).lower() for c in (layer.get("sensitive_columns") or [])}

    why_glossary: dict[str, list[str]] = {}
    for row in glossary:
        if not isinstance(row, dict):
            continue
        term = str(row.get("term") or "")
        table = str(row.get("table") or "")
        if not term:
            continue
        term_norm = _norm(term)
        if term_norm and term_norm in intent_norm:
            if table:
                why_glossary.setdefault(table, []).append(term)
            else:
                # definition-only terms still boost matching object tokens
                for obj in objects:
                    if _fold(obj.id) in tokens(term) or obj.id in tokens(term):
                        why_glossary.setdefault(obj.id, []).append(term)

    locations: list[dict[str, Any]] = []
    for obj in objects:
        hay = tokens(obj.id + " " + obj.description)
        visible: list[str] = []
        hidden: list[str] = []
        for prop in obj.properties:
            name = prop.name.lower()
            hay = hay | tokens(prop.name)
            if (not prop.agent_visible) or name in sensitive:
                hidden.append(prop.name)
            else:
                visible.append(prop.name)
                hay = hay | tokens(prop.name)
        score = _score_overlap(intent_tok, hay)
        reasons: list[str] = []
        if obj.id in intent_tok or _fold(obj.id) in intent_tok:
            score += 5
            reasons.append(f"object id {obj.id}")
        for term in why_glossary.get(obj.id, []):
            score += 8
            reasons.append(f"glossary '{term}'")
        if score <= 0:
            continue
        if not reasons:
            overlap = sorted(intent_tok & hay)
            reasons.append("token overlap: " + ", ".join(overlap[:8]))
        locations.append(
            {
                "id": obj.id,
                "kind": "object",
                "where": {
                    "table": obj.id,
                    "primary_key": obj.primary_key,
                    "columns": visible,
                },
                "importance": {
                    "score": score,
                    "why": "; ".join(reasons),
                },
                "exclude_columns": hidden,
            }
        )

    metrics: list[dict[str, Any]] = []
    for row in metrics_doc.get("metrics") or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("id") or "")
        synonyms = [str(s) for s in (row.get("synonyms") or []) if str(s).strip()]
        sql = str(row.get("sql") or "")
        tables = _tables_in_sql(sql)
        score, matched = _synonym_hit(intent_norm, synonyms + [mid.replace("_", " ")])
        if mid and _fold(mid.replace("_", " ")) in intent_tok:
            score = max(score, 6)
        if score <= 0:
            continue
        trial = matched or (synonyms[0] if synonyms else mid.replace("_", " "))
        if matched and _norm(matched) in intent_norm and len(intent_norm) > len(_norm(matched)):
            # Two-way: keep operator slots (warehouse, SKU) on the constrained synonym.
            trial = text
        why = f"synonym '{matched}'" if matched else f"metric id {mid}"
        metrics.append(
            {
                "id": mid,
                "kind": "metric",
                "where": {"tables": list(tables), "synonym": matched or trial},
                "importance": {"score": score, "why": why},
                "trial_question": trial,
            }
        )

    certified: list[dict[str, Any]] = []
    for row in certified_doc.get("certified") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        question = str(row.get("question") or "")
        synonyms = [str(s) for s in (row.get("synonyms") or []) if str(s).strip()]
        phrases = [p for p in [question, *synonyms] if p]
        score, matched = _synonym_hit(intent_norm, phrases)
        if score <= 0:
            continue
        sql = str(row.get("sql") or "")
        certified.append(
            {
                "id": cid,
                "kind": "certified",
                "where": {"tables": list(_tables_in_sql(sql)), "question": question},
                "importance": {
                    "score": score + 5,  # L0 wording outranks a loose metric paraphrase
                    "why": f"certified '{matched or question}'",
                },
                "trial_question": question or matched,
            }
        )

    locations.sort(key=lambda r: (-int(r["importance"]["score"]), r["id"]))
    metrics.sort(key=lambda r: (-int(r["importance"]["score"]), r["id"]))
    certified.sort(key=lambda r: (-int(r["importance"]["score"]), r["id"]))
    for idx, row in enumerate(locations, start=1):
        row["importance"]["rank"] = idx
    for idx, row in enumerate(metrics, start=1):
        row["importance"]["rank"] = idx
    for idx, row in enumerate(certified, start=1):
        row["importance"]["rank"] = idx

    ranked_ids = {row["id"] for row in locations}
    joins: list[dict[str, str]] = []
    for link in links:
        if link.from_object in ranked_ids or link.to_object in ranked_ids:
            joins.append(
                {
                    "id": link.id,
                    "from": link.from_object,
                    "to": link.to_object,
                    "from_property": link.from_property,
                    "to_property": link.to_property,
                }
            )

    has_path = bool(locations or metrics or certified)
    return {
        "ok": has_path,
        "phase": "ontology",
        "intent": text,
        "locations": locations,
        "metrics": metrics,
        "certified": certified,
        "joins": joins,
        "law": (
            "Ontology first: locations = where to get data; rank = which data "
            "is more important. Insights ask only after this ranking."
        ),
        "refuse_reason": "" if has_path else "no ontology path or metric for intent",
    }


def constrain_trials(ranking: dict[str, Any]) -> list[dict[str, Any]]:
    """Governed trial questions from ranked certified + metrics. Not SQL."""
    ranked = list(ranking.get("certified") or []) + list(ranking.get("metrics") or [])
    ranked.sort(key=lambda r: (-int((r.get("importance") or {}).get("score") or 0), r["id"]))
    seen: set[str] = set()
    trials: list[dict[str, Any]] = []
    for row in ranked:
        question = str(row.get("trial_question") or "").strip()
        if not question:
            continue
        key = _norm(question)
        if key in seen:
            continue
        seen.add(key)
        trials.append(
            {
                "id": row["id"],
                "kind": row.get("kind") or "metric",
                "question": question,
                "where": row.get("where") or {},
                "importance": row.get("importance") or {},
            }
        )
        if len(trials) >= MAX_TRIALS:
            break
    return trials


def _ranked_tables(ranking: dict[str, Any]) -> set[str]:
    tables: set[str] = set()
    for row in ranking.get("locations") or []:
        table = ((row.get("where") or {}).get("table") or row.get("id") or "")
        if table:
            tables.add(str(table).lower())
    for row in list(ranking.get("metrics") or []) + list(ranking.get("certified") or []):
        for table in (row.get("where") or {}).get("tables") or []:
            tables.add(str(table).lower())
    return tables


def _ranked_metric_ids(ranking: dict[str, Any]) -> set[str]:
    ids = {str(row.get("id") or "") for row in ranking.get("metrics") or []}
    ids |= {str(row.get("id") or "") for row in ranking.get("certified") or []}
    ids.discard("")
    return ids


def _engine_tables(envelope: dict[str, Any]) -> set[str]:
    tables: set[str] = set()
    sources = envelope.get("sources") or []
    if isinstance(sources, list):
        for item in sources:
            if isinstance(item, str) and item.strip():
                tables.add(item.strip().lower())
    sql = str(envelope.get("sql_used") or "")
    tables.update(_tables_in_sql(sql))
    source_table = str(envelope.get("source_table") or "").strip().lower()
    if source_table:
        tables.add(source_table)
    return tables


def _badge_status(envelope: dict[str, Any]) -> str:
    if not envelope.get("ok"):
        return "REFUSE"
    badge = str(envelope.get("badge") or "").strip().lower()
    layer = str(envelope.get("layer") or "").strip().lower()
    if badge in {"engine_offline", "engine_error", "refused"}:
        return "REFUSE"
    if badge in _ABSTAIN_BADGES or layer in _ABSTAIN_BADGES:
        return "ABSTAIN"
    if badge in {"l2_validated", "generated"} or layer in {"generated", "l2"}:
        return "REFUSE"
    if badge in _CERTIFY_BADGES or layer in {"certified", "governed_metric", "query_skill"}:
        return "CERTIFIED"
    if badge in {"session"}:
        return "REFUSE"
    return "REFUSE"


def _validation(
    *,
    status: str,
    ranking: dict[str, Any],
    trial: dict[str, Any] | None,
    envelope: dict[str, Any],
    unused_trials: list[dict[str, Any]],
    extra_unsure: list[dict[str, str]],
) -> dict[str, Any]:
    include: list[dict[str, str]] = []
    exclude: list[dict[str, str]] = []
    unsure: list[dict[str, str]] = list(extra_unsure)

    if trial is not None:
        include.append(
            {
                "id": str(trial.get("id") or ""),
                "kind": str(trial.get("kind") or "metric"),
                "why": (
                    f"ontology rank used trial '{trial.get('question')}' "
                    f"({(trial.get('importance') or {}).get('why') or 'ranked'})"
                ),
            }
        )
        tables = (trial.get("where") or {}).get("tables") or []
        for table in tables:
            include.append(
                {
                    "id": str(table),
                    "kind": "table",
                    "why": f"metric/certified trial reads {table}",
                }
            )

    used_ids = {str(trial.get("id") or "")} if trial else set()
    used_tables = set()
    if trial:
        used_tables = {str(t).lower() for t in ((trial.get("where") or {}).get("tables") or [])}
    used_tables |= _engine_tables(envelope)

    for row in ranking.get("locations") or []:
        table = str((row.get("where") or {}).get("table") or row.get("id") or "")
        if table.lower() in used_tables:
            include.append(
                {
                    "id": table,
                    "kind": "object",
                    "why": str((row.get("importance") or {}).get("why") or "ranked location"),
                }
            )
        else:
            exclude.append(
                {
                    "id": table,
                    "kind": "object",
                    "why": (
                        "ranked location not selected for this trial "
                        f"(importance {(row.get('importance') or {}).get('rank')})"
                    ),
                }
            )
        for col in row.get("exclude_columns") or []:
            exclude.append(
                {
                    "id": f"{table}.{col}",
                    "kind": "column",
                    "why": "sensitive or not agent-visible",
                }
            )

    for row in list(ranking.get("metrics") or []) + list(ranking.get("certified") or []):
        rid = str(row.get("id") or "")
        if rid in used_ids:
            continue
        exclude.append(
            {
                "id": rid,
                "kind": str(row.get("kind") or "metric"),
                "why": (
                    "lower importance than the selected trial "
                    f"({(row.get('importance') or {}).get('why') or 'ranked'})"
                ),
            }
        )

    for skipped in unused_trials:
        exclude.append(
            {
                "id": str(skipped.get("id") or ""),
                "kind": "trial",
                "why": f"not attempted after {status} on an earlier trial",
            }
        )

    if envelope.get("truncated"):
        unsure.append(
            {
                "id": "truncated",
                "kind": "rows",
                "why": "engine marked truncated; extra rows were not shown",
            }
        )
    if status == "CERTIFIED" and envelope.get("rows") in (None, []):
        unsure.append(
            {
                "id": "rows",
                "kind": "rows",
                "why": "engine omitted row payload; numbers taken from answer + audit_id",
            }
        )
    suggestions = envelope.get("suggestions") or []
    if suggestions:
        unsure.append(
            {
                "id": "suggestions",
                "kind": "engine",
                "why": "engine returned suggestions; not used as numbers",
            }
        )
    assumptions = str(envelope.get("assumptions") or "").strip()
    if assumptions:
        unsure.append(
            {
                "id": "assumptions",
                "kind": "engine",
                "why": assumptions[:300],
            }
        )
    if not envelope.get("audit_id") and status == "CERTIFIED":
        unsure.append(
            {
                "id": "audit_id",
                "kind": "audit",
                "why": "missing audit_id",
            }
        )

    # Dedup while keeping order.
    def _dedup(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, str]] = []
        for row in rows:
            key = (row.get("kind", ""), row.get("id", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    return {
        "include": _dedup(include),
        "exclude": _dedup(exclude),
        "unsure": _dedup(unsure),
    }


def _invented_adds(envelope: dict[str, Any], answer: str) -> list[str]:
    """Numbers in the crew answer that the engine did not return."""
    engine_nums = _numbers_from_text(str(envelope.get("answer") or ""))
    engine_nums |= _numbers_from_rows(envelope.get("rows"))
    for key in ("row_count", "total_count"):
        val = envelope.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            engine_nums.add(_num_key(val))
    crew_nums = _numbers_from_text(answer)
    extra = sorted(crew_nums - engine_nums)
    rows = envelope.get("rows")
    if isinstance(rows, list) and rows:
        answer_only = _numbers_from_text(str(envelope.get("answer") or "")) - _numbers_from_rows(
            rows
        )
        # Row payload present: a number in the answer that is not in rows is an add.
        extra = sorted(set(extra) | answer_only)
    return extra


def _certify_trial(
    *,
    ranking: dict[str, Any],
    trial: dict[str, Any],
    envelope: dict[str, Any],
    unused_trials: list[dict[str, Any]],
) -> dict[str, Any]:
    status = _badge_status(envelope)
    extra_unsure: list[dict[str, str]] = []
    answer = str(envelope.get("answer") or "")
    badge = str(envelope.get("badge") or "")

    if status == "CERTIFIED":
        metric_id = str(envelope.get("metric_id") or "").strip()
        ranked_metrics = _ranked_metric_ids(ranking)
        engine_tables = _engine_tables(envelope)
        ranked_tables = _ranked_tables(ranking)
        if metric_id and ranked_metrics and metric_id not in ranked_metrics:
            if engine_tables and not engine_tables <= ranked_tables:
                status = "REFUSE"
                extra_unsure.append(
                    {
                        "id": metric_id,
                        "kind": "metric",
                        "why": "engine metric was not in the ontology ranking",
                    }
                )
            elif not engine_tables:
                extra_unsure.append(
                    {
                        "id": metric_id,
                        "kind": "metric",
                        "why": "engine metric_id not pre-ranked; tables not returned",
                    }
                )
                status = "REFUSE"
        if engine_tables and ranked_tables and not engine_tables <= ranked_tables:
            status = "REFUSE"
            extra_unsure.append(
                {
                    "id": ",".join(sorted(engine_tables - ranked_tables)),
                    "kind": "table",
                    "why": "engine read a table the ontology ranking did not select",
                }
            )
        if not envelope.get("audit_id"):
            status = "REFUSE"
            extra_unsure.append(
                {
                    "id": "audit_id",
                    "kind": "audit",
                    "why": "certified numbers require audit_id; refused instead of padding",
                }
            )
        row_count = envelope.get("row_count")
        rows = envelope.get("rows")
        has_number = bool(
            _numbers_from_text(answer) or _numbers_from_rows(rows) or row_count not in (None, 0)
        )
        if row_count == 0 and not _numbers_from_text(answer) and not _numbers_from_rows(rows):
            status = "ABSTAIN"
            extra_unsure.append(
                {
                    "id": "empty",
                    "kind": "rows",
                    "why": "empty success; refused to certify a padded zero",
                }
            )
        invented = _invented_adds(envelope, answer)
        if invented:
            status = "REFUSE"
            extra_unsure.append(
                {
                    "id": ",".join(invented),
                    "kind": "number",
                    "why": "answer contains numbers not in engine rows; never invent adds",
                }
            )
        if status == "CERTIFIED" and not has_number:
            extra_unsure.append(
                {
                    "id": "values",
                    "kind": "number",
                    "why": "no numeric values on a success badge",
                }
            )

    if status == "ABSTAIN":
        answer = answer or "Engine abstained. No number invented."
    if status == "REFUSE":
        why = extra_unsure[-1]["why"] if extra_unsure else "Refused. No ontology-certified number."
        answer = why

    validation = _validation(
        status=status,
        ranking=ranking,
        trial=trial,
        envelope=envelope,
        unused_trials=unused_trials,
        extra_unsure=extra_unsure,
    )
    if status == "CERTIFIED" and not validation["unsure"]:
        validation["unsure"].append(
            {
                "id": "none",
                "kind": "unsure",
                "why": "no unmarked remainder on this trial",
            }
        )
    if status == "CERTIFIED" and not validation["exclude"]:
        validation["exclude"].append(
            {
                "id": "none",
                "kind": "exclude",
                "why": "no lower-ranked ontology hit left unused",
            }
        )
    if status == "CERTIFIED" and not (validation["include"] and validation["exclude"] is not None):
        status = "REFUSE"
        answer = "Refused. Validation metadata missing; no padded number."

    values: list[Any] = []
    if status == "CERTIFIED":
        rows = envelope.get("rows")
        if isinstance(rows, list):
            values = rows
        elif envelope.get("row_count") not in (None,):
            values = [{"row_count": envelope.get("row_count")}]

    return {
        "ok": status != "REFUSE",
        "status": status,
        "phase": "ask",
        "answer": answer if status != "REFUSE" or envelope.get("answer") else answer,
        "badge": badge,
        "layer": envelope.get("layer"),
        "metric_id": envelope.get("metric_id") or trial.get("id"),
        "audit_id": envelope.get("audit_id"),
        "row_count": envelope.get("row_count"),
        "values": values if status == "CERTIFIED" else [],
        "sources": envelope.get("sources"),
        "sql_used": envelope.get("sql_used") if status != "REFUSE" else None,
        "trial": {
            "id": trial.get("id"),
            "kind": trial.get("kind"),
            "question": trial.get("question"),
        },
        "validation": validation,
        "engine_ok": bool(envelope.get("ok")),
    }


def _refuse(
    *,
    intent: str,
    ranking: dict[str, Any],
    reason: str,
    envelope: dict[str, Any] | None = None,
    shell_public: dict[str, Any] | None = None,
    trials: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    env = envelope or {}
    validation = _validation(
        status="REFUSE",
        ranking=ranking,
        trial=None,
        envelope=env,
        unused_trials=list(trials or []),
        extra_unsure=[{"id": "refuse", "kind": "gate", "why": reason}],
    )
    return {
        "ok": False,
        "status": "REFUSE",
        "phase": "ask" if trials is not None else ranking.get("phase") or "ontology",
        "intent": intent,
        "answer": reason,
        "badge": env.get("badge") or "refused",
        "audit_id": env.get("audit_id"),
        "values": [],
        "ontology": ranking,
        "trials": trials or [],
        "validation": validation,
        "law": LAW,
        "export_runtime": export_runtime_hint(shell_public),
        "scale": "1GB to 10TB is a design target only; not COMPLETE",
    }


def _abstain(
    *,
    intent: str,
    ranking: dict[str, Any],
    trial: dict[str, Any],
    envelope: dict[str, Any],
    unused_trials: list[dict[str, Any]],
    shell_public: dict[str, Any] | None,
    attempted: list[dict[str, Any]],
) -> dict[str, Any]:
    body = _certify_trial(
        ranking=ranking,
        trial=trial,
        envelope=envelope,
        unused_trials=unused_trials,
    )
    body.update(
        {
            "ok": True,
            "status": "ABSTAIN",
            "intent": intent,
            "ontology": ranking,
            "trials": attempted,
            "values": [],
            "law": LAW,
            "export_runtime": export_runtime_hint(shell_public),
            "scale": "1GB to 10TB is a design target only; not COMPLETE",
        }
    )
    if not body.get("answer"):
        body["answer"] = "Engine abstained. No number invented."
    return body


async def run_insights(
    intent: str,
    *,
    bridge: EngineBridge,
    ask: bool = True,
    shell_public: dict[str, Any] | None = None,
    pack_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Ontology first. Optional constrained DMS ask. Never invents numbers."""
    text = (intent or "").strip()
    if not text:
        empty = {
            "ok": False,
            "phase": "ontology",
            "locations": [],
            "metrics": [],
            "certified": [],
            "joins": [],
            "refuse_reason": "empty intent",
            "intent": "",
            "law": "Ontology first.",
        }
        return _refuse(
            intent="",
            ranking=empty,
            reason="empty intent",
            shell_public=shell_public,
        )

    ranking = retrieve_ontology(text, pack_dir=pack_dir)
    if not ask:
        return {
            "ok": bool(ranking.get("ok")),
            "status": None,
            "phase": "ontology",
            "intent": text,
            "ontology": ranking,
            "answer": ranking.get("law"),
            "values": [],
            "validation": None,
            "law": LAW,
            "export_runtime": export_runtime_hint(shell_public),
            "scale": "1GB to 10TB is a design target only; not COMPLETE",
            "refuse_reason": ranking.get("refuse_reason") or "",
        }

    if not ranking.get("ok"):
        return _refuse(
            intent=text,
            ranking=ranking,
            reason="no ontology path or metric for intent",
            shell_public=shell_public,
        )

    trials = constrain_trials(ranking)
    if not trials:
        return _refuse(
            intent=text,
            ranking=ranking,
            reason="ontology ranked locations but no constrained metric/certified trial",
            shell_public=shell_public,
        )

    last_abstain: dict[str, Any] | None = None
    attempted: list[dict[str, Any]] = []
    for idx, trial in enumerate(trials):
        envelope = await bridge.ask(str(trial["question"]))
        attempted.append(
            {
                "id": trial["id"],
                "question": trial["question"],
                "badge": envelope.get("badge"),
                "ok": envelope.get("ok"),
            }
        )
        unused = trials[idx + 1 :]
        status = _badge_status(envelope)
        if status == "CERTIFIED":
            body = _certify_trial(
                ranking=ranking,
                trial=trial,
                envelope=envelope,
                unused_trials=unused,
            )
            if body["status"] == "CERTIFIED":
                body.update(
                    {
                        "intent": text,
                        "ontology": ranking,
                        "trials": attempted,
                        "law": LAW,
                        "export_runtime": export_runtime_hint(shell_public),
                        "scale": "1GB to 10TB is a design target only; not COMPLETE",
                    }
                )
                return body
            if body["status"] == "ABSTAIN":
                last_abstain = _abstain(
                    intent=text,
                    ranking=ranking,
                    trial=trial,
                    envelope=envelope,
                    unused_trials=unused,
                    shell_public=shell_public,
                    attempted=attempted,
                )
                continue
            body.update(
                {
                    "ok": False,
                    "intent": text,
                    "ontology": ranking,
                    "trials": attempted,
                    "values": [],
                    "law": LAW,
                    "export_runtime": export_runtime_hint(shell_public),
                    "scale": "1GB to 10TB is a design target only; not COMPLETE",
                }
            )
            return body
        if status == "ABSTAIN":
            last_abstain = _abstain(
                intent=text,
                ranking=ranking,
                trial=trial,
                envelope=envelope,
                unused_trials=unused,
                shell_public=shell_public,
                attempted=attempted,
            )
            continue
        # REFUSE this trial; try the next constrained question unless the engine is gone.
        badge = str(envelope.get("badge") or "")
        if badge in {"engine_offline", "engine_error"}:
            return _refuse(
                intent=text,
                ranking=ranking,
                reason=str(envelope.get("answer") or "engine unreachable"),
                envelope=envelope,
                shell_public=shell_public,
                trials=attempted,
            )

    if last_abstain is not None:
        return last_abstain
    return _refuse(
        intent=text,
        ranking=ranking,
        reason="constrained trials did not certify; refused instead of padding",
        shell_public=shell_public,
        trials=attempted,
    )


def render_tool_text(envelope: dict[str, Any]) -> str:
    """Operator-visible tool payload. Assert tests on this text, not SQL only."""
    ontology = envelope.get("ontology") or {}
    locations = ontology.get("locations") or []
    metrics = ontology.get("metrics") or []
    certified = ontology.get("certified") or []
    top_where = ", ".join(
        f"{row.get('id')}#{(row.get('importance') or {}).get('rank')}"
        for row in locations[:5]
    ) or "none"
    top_imp = ", ".join(
        f"{row.get('id')}#{(row.get('importance') or {}).get('rank')}"
        for row in (certified + metrics)[:5]
    ) or "none"
    validation = envelope.get("validation") or {}
    include = "; ".join(
        f"{r.get('id')}: {r.get('why')}" for r in (validation.get("include") or [])[:6]
    ) or "none"
    exclude = "; ".join(
        f"{r.get('id')}: {r.get('why')}" for r in (validation.get("exclude") or [])[:6]
    ) or "none"
    unsure = "; ".join(
        f"{r.get('id')}: {r.get('why')}" for r in (validation.get("unsure") or [])[:6]
    ) or "none"
    status = envelope.get("status") or "ONTOLOGY"
    return (
        f"status: {status}\n"
        f"phase: {envelope.get('phase')}\n"
        f"where: {top_where}\n"
        f"importance: {top_imp}\n"
        f"answer: {envelope.get('answer') or ''}\n"
        f"audit_id: {envelope.get('audit_id')}\n"
        f"include: {include}\n"
        f"exclude: {exclude}\n"
        f"unsure: {unsure}\n"
        f"export: prefer {BACKEND_CF_COMPUTER} (Excel/PPT deferred)\n"
        f"scale: 1GB to 10TB is a design target only; not COMPLETE"
    )
