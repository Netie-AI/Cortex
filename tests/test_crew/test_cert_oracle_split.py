"""CERT-ORACLE-SPLIT (#343): the L0 serve set is never the climb/score oracle.

``packs/dms/semantic/certified_queries.yaml`` is what ``answer()`` serves from.
It was seeded from ``bench/golden/dms_golden_v1.yaml``. Before this split
``cot_climb.gold_sql_for`` graded against that same file, so any score through
L0 was memorised by construction.

Must-fail tests (each fails on the parent and passes on the head):
  (a) ``test_must_fail_serving_never_opens_the_oracle_or_bench`` and
      ``test_must_fail_import_contract_serve_never_reads_oracle_is_declared``
  (b) ``test_must_fail_gold_sql_for_never_reads_the_serve_set``
  (c) ``test_must_fail_scored_climb_refuses_serve_set_id``,
      ``test_must_fail_scored_climb_refuses_serve_set_question`` and
      ``test_must_fail_heldout_score_refuses_serve_set_overlap``

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
HELDOUT = ROOT / "bench" / "heldout" / "c7_heldout_v1.yaml"
CONTRACT = "importlinter:contract:serve-never-reads-oracle"
SKU_GOLD = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
RANK_Q = "Rank suppliers by combined risk and lead time score"

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
        cot_climb.gold_sql_for("spider_country_mean_lead_having")
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
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
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
    yield TestClient(create_app(), client=("127.0.0.1", 5555))
    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()
    netie.config._cached_config = None


def test_must_fail_serving_never_opens_the_oracle_or_bench(
    serving: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.crew import freeroute as fr
    from CortexOS.dms.answer_engine import answer
    from packs.dms.semantic.loader import reload

    gold = _gold_files(monkeypatch)
    monkeypatch.setattr(fr, "complete", _script(SKU_GOLD))
    asks = [
        "How many SKUs do we have in inventory?",  # an L0 certified question
        RANK_Q,
        "Which supplier countries have a mean lead time strictly above 20 days "
        "and at least three suppliers in that country?",  # a held-out / oracle question
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
        res = serving.post("/v1/insights", json={"intent": RANK_Q, "ask": False, "generate": True})
        assert res.status_code == 200, res.text

    assert SERVE_SET in seen, "trace saw no serve-set load; the guard would prove nothing"
    assert envelopes[0]["rows"], envelopes[0]
    bad = sorted({str(p) for p in seen if p in gold or p.is_relative_to(BENCH)})
    assert bad == [], f"serving opened the climb/score oracle or bench/: {bad}"


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
    }
    assert set(_lines(section["forbidden_modules"])) >= {
        "bench",
        "CortexOS.crew.score_oracle",
        "CortexOS.crew.cot_climb",
    }
    assert "ignore_imports" not in section


def _scratch(tmp: Path, offending: str | None) -> Path:
    section = _contract()
    for pkg in ("CortexOS", "CortexOS/dms", "CortexOS/api", "CortexOS/crew", "packs", "bench"):
        (tmp / pkg).mkdir(parents=True, exist_ok=True)
        (tmp / pkg / "__init__.py").write_text("", encoding="utf-8")
    for mod in ("crew/score_oracle", "crew/cot_climb", "api/contract_routes"):
        (tmp / f"CortexOS/{mod}.py").write_text("", encoding="utf-8")
    body = "import json\n" + (f"import {offending}\n" if offending else "")
    (tmp / "CortexOS/dms/answer_engine.py").write_text(body, encoding="utf-8")
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
def test_import_contract_breaks_when_serving_imports_the_oracle(
    tmp_path: Path, offending: str
) -> None:
    proc = _lint(tmp_path, _scratch(tmp_path, offending))
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
        oracle = cot_climb.gold_sql_for("spider_country_mean_lead_having")
    assert SERVE_SET not in seen, "gold_sql_for opened certified_queries.yaml"
    assert served == ""  # a serve-set id has no gold
    assert oracle.startswith("SELECT country FROM suppliers")
    assert cot_climb.gold_sql_for("cq_sku_count", SKU_GOLD) == SKU_GOLD


def test_oracle_is_pinned_to_heldout_and_disjoint_from_the_serve_set() -> None:
    from CortexOS.crew import score_oracle

    oracle = yaml.safe_load(score_oracle.ORACLE_PATH.read_text(encoding="utf-8"))["gold"]
    heldout = {
        row["id"]: row
        for row in yaml.safe_load(HELDOUT.read_text(encoding="utf-8"))["items"]
        if row["split"] == "sql"
    }
    assert [row["id"] for row in oracle] == list(heldout)
    for row in oracle:
        src = heldout[row["id"]]
        assert row["sql"].strip() == src["canonical_sql"].strip(), row["id"]
        assert " ".join(row["question"].split()) == " ".join(src["question"].split())
    serve = score_oracle.serve_set()
    hits = [row["id"] for row in oracle if score_oracle.serve_hit(row["id"], row["question"], serve)]
    assert hits == []
    assert not score_oracle.ORACLE_PATH.resolve().is_relative_to((ROOT / "packs").resolve())


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
