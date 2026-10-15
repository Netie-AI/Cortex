"""CERT-ORACLE-SPLIT (#343): the L0 serve set is never the climb/score oracle.

``packs/dms/semantic/certified_queries.yaml`` is what ``answer()`` serves from.
It was seeded from ``bench/golden/dms_golden_v1.yaml``. Before this split
``cot_climb.gold_sql_for`` graded against that same file, so any score through
L0 was memorised by construction.

Must-fail tests (each fails on the parent and passes on the head):
  (a) ``test_must_fail_serving_never_opens_the_oracle_or_bench`` (the answer
      path, contract /ask, the #329 plan+SQL ladder and /v1/insights) and
      ``test_must_fail_import_contract_serve_never_reads_oracle_is_declared``
  (b) ``test_must_fail_gold_sql_for_never_reads_the_serve_set``
  (c) ``test_must_fail_scored_climb_refuses_serve_set_id``,
      ``test_must_fail_scored_climb_refuses_serve_set_question`` and
      ``test_must_fail_heldout_score_refuses_serve_set_overlap``
  (d) ``test_must_fail_climb_oracle_is_disjoint_from_heldout_and_serve_set`` and
      ``test_must_fail_climb_without_gold_says_so``: the climb never tunes on
      the frozen held-out split, and with no disjoint gold it says NO_GOLD
      instead of printing an empty exact score.

"The oracle" in (a) is whatever file ``gold_sql_for`` actually opens, observed
through an ``open`` audit hook. File reads are invisible to import-linter, so
the IO guard is the check; the named contract covers the import route.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from CortexOS.crew import cot_climb

ROOT = Path(__file__).resolve().parents[2]
SERVE_SET = (ROOT / "packs" / "dms" / "semantic" / "certified_queries.yaml").resolve()
BENCH = (ROOT / "bench").resolve()
HELDOUT_DIR = ROOT / "bench" / "heldout"
HELDOUT_ID = "spider_country_mean_lead_having"  # a frozen held-out sql item
CONTRACT = "importlinter:contract:serve-never-reads-oracle"
SKU_GOLD = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
RANK_Q = "Rank suppliers by combined risk and lead time score"
HELDOUT_Q = (
    "Which supplier countries have a mean lead time strictly above 20 days "
    "and at least three suppliers in that country?"
)

_TRACES: list[list[Path]] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event != "open" or not _TRACES or not args:
        return
    target = args[0]
    if isinstance(target, (str, bytes, os.PathLike)):
        try:
            _TRACES[-1].append(Path(os.fsdecode(target)).resolve())
        except (OSError, ValueError):
            pass


# Audit hooks cannot be removed; this one is inert unless a trace is open.
sys.addaudithook(_audit)


@contextmanager
def _opened() -> Iterator[list[Path]]:
    seen: list[Path] = []
    _TRACES.append(seen)
    try:
        yield seen
    finally:
        _TRACES.remove(seen)


def _gold_files(monkeypatch: pytest.MonkeyPatch) -> set[Path]:
    monkeypatch.setattr(cot_climb, "_GOLD", None)
    with _opened() as seen:
        cot_climb.gold_sql_for(HELDOUT_ID)
    files = {p for p in seen if p.suffix in {".yaml", ".yml", ".json"}}
    assert files, "gold_sql_for opened no gold file"
    return files


def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )


def _script(sql: str) -> Callable[..., Any]:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, prompt, kwargs
        text = sql if purpose == "generative_ask" else "Use inventory sku. No numbers."
        return {"ok": True, "text": text, "identity": f"cortex:crew:{purpose}"}

    return fake


# --- (a) serving never opens the oracle or anything under bench/ -------------


@pytest.fixture
def serving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The answer path, contract /ask and /v1/insights, set up before any trace."""
    import base64
    from datetime import datetime, timedelta, timezone

    import netie.config
    from cortex_contract.execution import Manifest
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi.testclient import TestClient

    from bench.accuracy import _ensure_db_loaded
    from CortexOS.api.app import create_app
    from CortexOS.execution.manifest import JwksCache, ManifestVerifier, canonical_manifest_bytes
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
    from CortexOS.execution.session_manifests import (
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from CortexOS.execution.submit import set_verifier_for_tests
    from packs.dms.security.rate_limit import reset_limiter

    def b64u(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    netie.config._cached_config = None
    _ensure_db_loaded()
    _armed(monkeypatch)

    issuer = Ed25519PrivateKey.generate()
    raw = issuer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install(
        {"keys": [{"kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
                   "kid": "int-1", "x": b64u(raw)}]}
    )
    verifier = ManifestVerifier(cache)
    set_verifier_for_tests(verifier)
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    reset_limiter(10_000)
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="cert-oracle-343",
        org_id="acme",
        space_id="alpha",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates={"inventory": "TRUE", "suppliers": "TRUE", "transactions": "TRUE"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="",
    )
    manifest.signature = b64u(issuer.sign(canonical_manifest_bytes(manifest)))
    get_session_registry().bind(verifier.verify(manifest))
    # A real key header, not auth off: #366 refuses DMS_AUTH_DISABLED outside dev mode.
    yield TestClient(
        create_app(), client=("127.0.0.1", 5555), headers={"X-API-Key": "dms-demo-admin-key"}
    )
    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


def _plan_sql_reply(fake: Any, content: str) -> None:
    """Scripted OpenVault reply carrying the served_* stamps the ladder requires."""
    fake.replies.append(
        (
            200,
            {
                "model": "openai/gpt-oss-120b",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "served_provider": "groq",
                "served_model": "openai/gpt-oss-120b",
                "served_local": False,
            },
        )
    )


def _payload(table: str, *columns: tuple[str, str]) -> dict[str, Any]:
    return {
        "tables": [
            {"name": table, "columns": [{"name": n, "type": t} for n, t in columns]}
        ]
    }


# (question, dms_payload, scripted SQL, column the rows must carry)
PLAN_SQL_ASKS = [
    (
        "How many SKUs do we have in inventory?",  # an L0 certified question
        _payload("inventory", ("sku", "VARCHAR"), ("category", "VARCHAR")),
        SKU_GOLD,
        "sku_count",
    ),
    (
        HELDOUT_Q,  # a held-out question, answered by the ladder, not graded
        _payload("suppliers", ("country", "VARCHAR"), ("lead_time_days", "INTEGER")),
        "SELECT country, COUNT(*) AS n FROM suppliers GROUP BY country ORDER BY country",
        "country",
    ),
]


def test_must_fail_serving_never_opens_the_oracle_or_bench(
    armed_openvault: Any, serving: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr
    from CortexOS.dms import plan_sql_ask
    from CortexOS.dms.answer_engine import answer
    from packs.dms.semantic.loader import reload

    gold = _gold_files(monkeypatch)
    monkeypatch.setattr(fr, "complete", _script(SKU_GOLD))
    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    for _, _, sql, _ in PLAN_SQL_ASKS:
        _plan_sql_reply(armed_openvault, "1. Read the selected table.\n2. Aggregate.")
        _plan_sql_reply(armed_openvault, f"```sql\n{sql};\n```")
    asks = [
        "How many SKUs do we have in inventory?",  # an L0 certified question
        RANK_Q,
        HELDOUT_Q,  # a held-out / oracle question
    ]
    with _opened() as seen:
        reload()  # the serve set is loaded inside the trace, not before it
        envelopes = [answer(q) for q in asks]
        for q in asks:
            res = serving.post(
                "/v1/contract/ask",
                json={"question": q, "session_id": "cert-oracle-343", "space_id": "alpha"},
            )
            assert res.status_code == 200, res.text
        laddered = []
        for q, payload, _, _ in PLAN_SQL_ASKS:
            res = serving.post(
                "/v1/contract/ask",
                json={
                    "question": q,
                    "session_id": "cert-oracle-343",
                    "space_id": "alpha",
                    "dms_payload": payload,
                },
            )
            assert res.status_code == 200, res.text
            laddered.append(res.json())
        res = serving.post("/v1/insights", json={"intent": RANK_Q, "ask": False, "generate": True})
        assert res.status_code == 200, res.text

    assert SERVE_SET in seen, "trace saw no serve-set load; the guard would prove nothing"
    assert envelopes[0]["rows"], envelopes[0]
    # The ladder really served inside the trace: plan rung, rows, rendered answer.
    for body, (_, _, sql, column) in zip(laddered, PLAN_SQL_ASKS, strict=True):
        assert body["plan_sql_rung"] == plan_sql_ask.RUNG_PLAN, body
        assert body["provenance"]["layer"] == "plan_sql", body
        assert body["sql_used"].startswith(sql), body["sql_used"]
        rows = body["rows"]
        assert rows and body["row_count"] == len(rows), body
        assert all(row.get(column) is not None for row in rows), rows
        assert str(rows[0][column]) in body["answer"], body["answer"]
    assert len(armed_openvault.chat_calls) == 2 * len(PLAN_SQL_ASKS)
    bad = _oracle_reads(seen, gold)
    assert bad == [], f"serving opened the climb/score oracle or bench/: {bad}"


def _oracle_reads(seen: list[Path], gold: set[Path]) -> list[str]:
    return sorted({str(p) for p in seen if p in gold or p.is_relative_to(BENCH)})


def test_audit_sees_an_oracle_read_on_the_plan_sql_path(
    armed_openvault: Any, serving: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trace covers the ladder: a gold read inside plan_sql_ask is reported."""
    from CortexOS.dms import plan_sql_ask

    gold = _gold_files(monkeypatch)
    leak = sorted(gold)[0]
    real = plan_sql_ask._sample_values

    def leaky(*args: Any, **kwargs: Any) -> Any:
        leak.read_bytes()
        return real(*args, **kwargs)

    monkeypatch.setattr(plan_sql_ask, "_sample_values", leaky)
    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    question, payload, sql, _ = PLAN_SQL_ASKS[0]
    _plan_sql_reply(armed_openvault, "1. Count distinct sku.")
    _plan_sql_reply(armed_openvault, sql)
    with _opened() as seen:
        res = serving.post(
            "/v1/contract/ask",
            json={
                "question": question,
                "session_id": "cert-oracle-343",
                "space_id": "alpha",
                "dms_payload": payload,
            },
        )
    assert res.status_code == 200, res.text
    assert res.json()["plan_sql_rung"] == plan_sql_ask.RUNG_PLAN
    assert _oracle_reads(seen, gold) == [str(leak)]


def _contract() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(ROOT / ".importlinter", encoding="utf-8")
    assert parser.has_section(CONTRACT), f"{CONTRACT} missing from .importlinter"
    return parser[CONTRACT]


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_must_fail_import_contract_serve_never_reads_oracle_is_declared() -> None:
    section = _contract()
    assert section["type"] == "forbidden"
    assert "#343" in section["name"]
    assert set(_lines(section["source_modules"])) == {
        "CortexOS.dms.answer_engine",
        "CortexOS.api.contract_routes",
        "CortexOS.dms.plan_sql_ask",  # the #329 plan+SQL ladder
    }
    assert set(_lines(section["forbidden_modules"])) >= {
        "bench",
        "CortexOS.crew.score_oracle",
        "CortexOS.crew.cot_climb",
    }
    assert "ignore_imports" not in section


def _scratch(tmp: Path, offending: str | None, source: str = "dms/answer_engine") -> Path:
    section = _contract()
    for pkg in ("CortexOS", "CortexOS/dms", "CortexOS/api", "CortexOS/crew", "packs", "bench"):
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    for mod in (
        "crew/score_oracle", "crew/cot_climb", "api/contract_routes",
        "dms/answer_engine", "dms/plan_sql_ask",
    ):
        (tmp / f"CortexOS/{mod}.py").write_text("", encoding="utf-8")
    body = "import json\n" + (f"import {offending}\n" if offending else "")
    (tmp / f"CortexOS/{source}.py").write_text(body, encoding="utf-8")
    lines = [f"[{CONTRACT}]"] + [
        f"{key} =" + ("\n    " + "\n    ".join(_lines(value)) if "\n" in value else f" {value}")
        for key, value in section.items()
    ]
    config = tmp / ".importlinter"
    config.write_text(
        "[importlinter]\nroot_packages =\n    CortexOS\n    packs\n"
        "include_external_packages = True\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return config


def _lint(tmp: Path, config: Path) -> subprocess.CompletedProcess[str]:
    found = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    assert Path(found).exists(), "lint-imports is not installed; pip install -e '.[dev]'"
    return subprocess.run(
        [found, "--config", str(config), "--no-cache"],
        cwd=tmp,
        env={**os.environ, "PYTHONPATH": str(tmp)},
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


@pytest.mark.parametrize(
    "offending", ["CortexOS.crew.score_oracle", "CortexOS.crew.cot_climb", "bench"]
)
@pytest.mark.parametrize("source", ["dms/answer_engine", "dms/plan_sql_ask"])
def test_import_contract_breaks_when_serving_imports_the_oracle(
    tmp_path: Path, offending: str, source: str
) -> None:
    proc = _lint(tmp_path, _scratch(tmp_path, offending, source))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "BROKEN" in out and "#343" in out.replace("\n", " "), out


def test_import_contract_keeps_on_clean_tree(tmp_path: Path) -> None:
    proc = _lint(tmp_path, _scratch(tmp_path, None))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "1 kept, 0 broken" in out, out


# --- (b) gold_sql_for never reads the serve set ------------------------------


def test_must_fail_gold_sql_for_never_reads_the_serve_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cot_climb, "_GOLD", None)
    with _opened() as seen:
        served = cot_climb.gold_sql_for("cq_sku_count")
        heldout = cot_climb.gold_sql_for(HELDOUT_ID)
    assert SERVE_SET not in seen, "gold_sql_for opened certified_queries.yaml"
    assert served == ""  # a serve-set id has no gold
    assert heldout == ""  # a frozen held-out id has no climb gold either
    assert cot_climb.gold_sql_for("cq_sku_count", SKU_GOLD) == SKU_GOLD


# --- (d) the climb never tunes on held-out, and says so when it has no gold ----


def _heldout_keys() -> tuple[set[str], set[str], set[str]]:
    """(ids, normalised questions, normalised SQL) of every frozen held-out file."""
    from CortexOS.crew import score_oracle

    files = sorted(p for p in HELDOUT_DIR.rglob("*") if p.suffix in {".yaml", ".yml"})
    assert files, "no held-out file found; the disjointness check would prove nothing"
    ids: set[str] = set()
    questions: set[str] = set()
    sqls: set[str] = set()
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = doc.get("items") or []
        assert rows, f"{path} has no items"
        for row in rows:
            ids.add(str(row["id"]).strip())
            questions.add(score_oracle.norm_question(row.get("question")))
            if row.get("canonical_sql"):
                sqls.add(cot_climb.normalize_sql(str(row["canonical_sql"])))
    return ids, questions, sqls


def test_must_fail_climb_oracle_is_disjoint_from_heldout_and_serve_set() -> None:
    from CortexOS.crew import score_oracle

    doc = yaml.safe_load(score_oracle.ORACLE_PATH.read_text(encoding="utf-8"))
    assert doc["split"] == "oracle"
    oracle = doc["gold"]
    assert isinstance(oracle, list)
    ids, questions, sqls = _heldout_keys()
    serve = score_oracle.serve_set()
    leaks = []
    for row in oracle:
        cid = str(row["id"]).strip()
        if cid in ids:
            leaks.append(f"heldout_id:{cid}")
        if score_oracle.norm_question(row["question"]) in questions:
            leaks.append(f"heldout_question:{cid}")
        if cot_climb.normalize_sql(str(row["sql"])) in sqls:
            leaks.append(f"heldout_sql:{cid}")
        if "heldout" in str(row.get("source") or ""):
            leaks.append(f"heldout_source:{cid}")
        hit = score_oracle.serve_hit(cid, str(row["question"]), serve)
        if hit:
            leaks.append(hit)
    assert leaks == [], f"climb oracle overlaps held-out or the serve set: {leaks}"
    assert set(score_oracle.oracle_gold_sql()).isdisjoint(ids)
    assert not score_oracle.ORACLE_PATH.resolve().is_relative_to((ROOT / "packs").resolve())
    assert not score_oracle.ORACLE_PATH.resolve().is_relative_to(HELDOUT_DIR.resolve())


@pytest.mark.asyncio
async def test_must_fail_climb_without_gold_says_so(
    crew_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import insights, score_oracle

    _armed(monkeypatch)
    monkeypatch.setattr(cot_climb, "_GOLD", None)
    intent = "Which supplier countries have a mean lead time above 20 days?"
    report = await cot_climb.measure_climb(
        [
            {
                "id": HELDOUT_ID,  # held-out gold must not be reachable from the climb
                "intent": intent,
                "ranking": insights.retrieve_ontology(intent),
                "complete": _script(SKU_GOLD),
            }
        ]
    )
    assert report["this_run"]["n"] == 1
    assert report["this_run"]["gold_n"] == 0
    assert report["this_run"]["gold"] == score_oracle.NO_GOLD
    gold = report["gold"]
    assert gold["status"] == score_oracle.NO_GOLD
    assert gold["gold_n"] == 0 and gold["n"] == 1
    assert gold["oracle"] == "bench/oracle/climb_oracle_v1.yaml"
    assert gold["oracle_entries"] == len(score_oracle.oracle_gold_sql())
    assert "exact is not a score" in gold["reason"]
    assert score_oracle.NO_GOLD in report["vs_baseline"]["note"]
    assert report["vs_baseline"]["improved"] is False
    assert report["status"] == "INCOMPLETE"


@pytest.mark.asyncio
async def test_climb_with_pinned_gold_is_not_stamped_no_gold(
    crew_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import insights, score_oracle

    _armed(monkeypatch)
    report = await cot_climb.measure_climb(
        [
            {
                "id": "fresh_case_pinned",
                "intent": "how many skus",
                "ranking": insights.retrieve_ontology("how many skus"),
                "expected_sql": SKU_GOLD,
                "complete": _script(SKU_GOLD),
            }
        ]
    )
    assert report["this_run"]["gold_n"] == 1
    assert report["this_run"]["gold"] == score_oracle.HAS_GOLD
    assert report["gold"]["status"] == score_oracle.HAS_GOLD
    assert report["this_run"]["exact"] == "100.00%"
    assert score_oracle.NO_GOLD not in report["vs_baseline"]["note"]


# --- (c) a scored run refuses serve-set cases by name -------------------------


@pytest.mark.asyncio
async def test_must_fail_scored_climb_refuses_serve_set_id(
    crew_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import insights

    _armed(monkeypatch)
    report = await cot_climb.measure_climb(
        [
            {
                "id": "cq_sku_count",
                "intent": "how many skus",  # question text does not match; the id does
                "ranking": insights.retrieve_ontology("how many skus"),
                "expected_sql": SKU_GOLD,
                "complete": _script(SKU_GOLD),
            }
        ]
    )
    assert report["excluded"] == [{"id": "cq_sku_count", "reason": "serve_set_id:cq_sku_count"}]
    assert report["excluded_ids"] == ["cq_sku_count"]
    assert report["this_run"]["n"] == 0
    assert report["this_run"]["exact_matched"] == 0
    assert report["this_run"]["exact"] == "0.00%"
    assert report["outcomes"] == []
    assert report["ok"] is False and report["status"] == "REFUSE"
    assert report["refuse_reason"] == "all_cases_excluded:serve_set_id:cq_sku_count"


@pytest.mark.asyncio
async def test_must_fail_scored_climb_refuses_serve_set_question(
    crew_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import insights

    _armed(monkeypatch)
    ranking = insights.retrieve_ontology("how many skus")
    report = await cot_climb.measure_climb(
        [
            {
                "id": "fresh_case_categoty",
                "intent": "Show top 3 categoty sales",  # a certified synonym, new id
                "ranking": ranking,
                "expected_sql": SKU_GOLD,
                "complete": _script(SKU_GOLD),
            },
            {
                "id": "fresh_case_clean",
                "intent": "how many skus",
                "ranking": ranking,
                "expected_sql": SKU_GOLD,
                "complete": _script(SKU_GOLD),
            },
        ]
    )
    assert report["excluded"] == [
        {"id": "fresh_case_categoty", "reason": "serve_set_question:cq_top3_category_sales"}
    ]
    assert [row["id"] for row in report["outcomes"]] == ["fresh_case_clean"]
    assert report["this_run"]["n"] == 1
    assert report["this_run"]["exact_matched"] == 1
    assert report["status"] == "INCOMPLETE"
    assert report["counts_toward_score"] is True


def test_must_fail_heldout_score_refuses_serve_set_overlap() -> None:
    from bench.heldout import HeldoutItem, score_engine

    def item(iid: str, question: str) -> HeldoutItem:
        return HeldoutItem(
            id=iid, split="sql", provenance="bird_style", question=question,
            expect="correct_rows", match="resultset", key_columns=["supplier_id"],
            expected_rows=[{"supplier_id": "S1"}],
        )

    asked: list[str] = []

    def ask(question: str) -> dict[str, Any]:
        asked.append(question)
        return {"answer": "S1", "rows": [{"supplier_id": "S1"}], "badge": "certified"}

    items = [
        item("memorised_rank", RANK_Q.upper() + "?"),
        item("cq_supplier_ranking", "Which supplier is S1?"),
        item("clean", "Which supplier is S1?"),
    ]
    report = score_engine(items, ask=ask, count_shadow=False)
    assert report["excluded"] == [
        {"id": "memorised_rank", "reason": "serve_set_question:cq_supplier_ranking"},
        {"id": "cq_supplier_ranking", "reason": "serve_set_id:cq_supplier_ranking"},
    ]
    assert report["excluded_n"] == 2
    assert report["totals"] == {"total": 1, "correct": 1, "abstained": 0, "incorrect": 0}
    assert [r["id"] for r in report["results"]] == ["clean"]
    assert asked == ["Which supplier is S1?"]


def test_heldout_score_with_only_serve_set_cases_names_the_refusal() -> None:
    from bench.heldout import HeldoutItem, score_engine

    only = HeldoutItem(
        id="x", split="must_abstain", provenance="different_model_abstain",
        question="List active alerts across the warehouse network", expect="abstain",
    )
    report = score_engine([only], ask=lambda q: {}, count_shadow=False)
    assert report["totals"]["total"] == 0
    assert report["refuse_reason"] == "all_cases_excluded:serve_set_question:cq_active_alerts"
    assert report["gates"]["g_abs"] is False  # no must-abstain left is not a pass


# --- dms_golden_v1 counts toward no score until re-split ----------------------


def test_golden_v1_counts_toward_no_score_and_says_so() -> None:
    from bench.accuracy import load_golden, render_markdown, stamp_golden
    from CortexOS.crew import score_oracle

    items = load_golden()
    report = stamp_golden({"generated_at": "t", "tiers": {}, "results": []}, items)
    assert report["counts_toward_score"] is False
    assert "counts toward no score until it is re-split" in report["score_note"]
    assert report["excluded_ids"] == [i.id for i in items]
    assert {row["reason"] for row in report["excluded"]} == {score_oracle.GOLDEN_NOT_RESPLIT}
    serve = {row["id"]: row["serve"] for row in report["excluded"] if "serve" in row}
    assert serve["sku_count"] == "serve_set_question:cq_sku_count"
    assert len(serve) == 18
    text = render_markdown(report)
    assert "NOT A SCORE" in text and "dms_golden_v1" in text
    assert "`sku_count`: dms_golden_v1_not_resplit (serve_set_question:cq_sku_count)" in text


@pytest.mark.asyncio
async def test_golden_v1_corpus_climb_counts_toward_no_score(
    crew_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _armed(monkeypatch)
    report = await cot_climb.measure_climb(
        [{"id": "anything", "intent": "how many skus", "complete": _script(SKU_GOLD)}],
        corpus="dms_golden_v1",
    )
    assert report["excluded"] == [{"id": "anything", "reason": "dms_golden_v1_not_resplit"}]
    assert report["counts_toward_score"] is False
    assert report["status"] == "REFUSE"
    assert report["refuse_reason"] == "all_cases_excluded:dms_golden_v1_not_resplit"
