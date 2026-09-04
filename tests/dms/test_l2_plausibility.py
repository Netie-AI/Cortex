"""C7-03 — plausibility stage. Envelope assertions, not SQL-only."""

from __future__ import annotations

from CortexOS.dms.l2_plausibility import assess_plausibility, sql_table_names


def test_sql_table_names():
    assert sql_table_names("SELECT sku FROM inventory LIMIT 5") == {"inventory"}
    assert sql_table_names("SELEC bad") == set()


def test_empty_success_abstains():
    trip = assess_plausibility(
        "which suppliers have a risk score above 0.7?",
        "SELECT * FROM suppliers WHERE risk_score > 0.7",
        [],
        retrieved_tables=["suppliers"],
    )
    assert trip.ok is False
    assert trip.code == "implausible_empty"


def test_empty_without_assertion_passes():
    trip = assess_plausibility(
        "anything else in the notes",
        "SELECT note FROM alerts LIMIT 5",
        [],
        retrieved_tables=["alerts"],
    )
    assert trip.ok is True


def test_scalar_listing_shape():
    trip = assess_plausibility(
        "how many shipments are delayed?",
        "SELECT shipment_id FROM shipments",
        [{"shipment_id": "1"}, {"shipment_id": "2"}],
        retrieved_tables=["shipments"],
    )
    assert trip.ok is False
    assert trip.code == "implausible_shape"


def test_retrieval_miss():
    sql = "SELECT sku FROM inventory LIMIT 5"
    trip = assess_plausibility(
        "which suppliers have a risk score above 0.7?",
        sql,
        [{"sku": "SKU-ALPHA"}],
        retrieved_tables=["suppliers"],
    )
    assert trip.ok is False
    assert trip.code == "implausible_tables"
    assert sql_table_names(sql) == {"inventory"}


def test_literal_leftover():
    sql = "SELECT sku FROM inventory WHERE sku = 'BETA'"
    trip = assess_plausibility(
        "stock for BETA",
        sql,
        [{"sku": "SKU-ALPHA"}],
        retrieved_tables=["inventory"],
        leftover_literals=["sku:BETA->SKU-BETA"],
    )
    assert trip.ok is False
    assert trip.code == "implausible_literal"
    assert sql == "SELECT sku FROM inventory WHERE sku = 'BETA'"


def test_low_confidence_does_not_override_empty():
    trip = assess_plausibility(
        "which suppliers have a risk score above 0.7?",
        "SELECT * FROM suppliers",
        [],
        retrieved_tables=["suppliers"],
        score=0.99,
    )
    assert trip.code == "implausible_empty"


def test_low_confidence_when_rows_ok():
    trip = assess_plausibility(
        "stock on hand",
        "SELECT sku, quantity_kg FROM inventory LIMIT 5",
        [{"sku": "SKU-ALPHA", "quantity_kg": 1}],
        retrieved_tables=["inventory"],
        score=0.4,
    )
    assert trip.ok is False
    assert trip.code == "low_confidence"


def test_pass_does_not_rewrite_sql():
    sql = "SELECT sku FROM inventory LIMIT 5"
    trip = assess_plausibility(
        "list some skus",
        sql,
        [{"sku": "SKU-ALPHA"}],
        retrieved_tables=["inventory"],
        score=0.9,
    )
    assert trip.ok is True
    assert sql == "SELECT sku FROM inventory LIMIT 5"


def test_l2_empty_success_customer_envelope(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from cortex_contract.execution import Manifest

    from CortexOS.dms import l2_generation
    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.warehouse_db import KNOWN_TABLES
    from CortexOS.execution.manifest import VerifiedManifest

    class _Port:
        def is_configured(self) -> bool:
            return True

        def retrieve_schema(self, question: str) -> dict:
            return {"tables": {"suppliers": {}}}

        def generate_candidates(self, question, schema, *, prior_violations=None):
            del question, schema, prior_violations
            return ["SELECT supplier_id FROM suppliers WHERE risk_score > 999999"]

        def record_validated(self, question, sql):
            del question, sql
            return None

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _Port())
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr(
        "packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False
    )
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="c7-03",
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/**"],
        row_predicates={name: "TRUE" for name in KNOWN_TABLES},
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    verified = VerifiedManifest(
        manifest=manifest, issuer_kid="int-1", verified_at=when
    )
    r = answer(
        "which suppliers have a risk score above 9.5?",
        verified=verified,
    )
    assert r["badge"] == "abstain"
    assert r["route"] != "sql"
    assert r["rows"] == []
    assert "empty-success" in (r.get("assumptions") or "")
    assert r.get("sql_used") is None


def test_l2_retrieval_miss_customer_envelope(monkeypatch):
    from datetime import datetime, timedelta, timezone

    from cortex_contract.execution import Manifest

    from CortexOS.dms import l2_generation
    from CortexOS.dms.answer_engine import answer
    from CortexOS.dms.warehouse_db import KNOWN_TABLES
    from CortexOS.execution.manifest import VerifiedManifest

    class _Port:
        def is_configured(self) -> bool:
            return True

        def retrieve_schema(self, question: str) -> dict:
            return {"tables": {"suppliers": {}}}

        def generate_candidates(self, question, schema, *, prior_violations=None):
            del question, schema, prior_violations
            return ["SELECT sku FROM inventory LIMIT 5"]

        def record_validated(self, question, sql):
            del question, sql
            return None

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _Port())
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr(
        "packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False
    )
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="c7-03-miss",
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/**"],
        row_predicates={name: "TRUE" for name in KNOWN_TABLES},
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    verified = VerifiedManifest(
        manifest=manifest, issuer_kid="int-1", verified_at=when
    )
    r = answer(
        "which suppliers have a risk score above 0.7?",
        verified=verified,
    )
    assert r["badge"] == "abstain"
    assert r["rows"] == []
    assert "retrieval miss" in (r.get("assumptions") or "")
    assert r.get("sql_used") is None
