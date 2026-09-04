"""C7-01 — DMS_L2_SHADOW records L2 without changing the served envelope."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from CortexOS.dms import l2_generation


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    yield


class _OkPort:
    def is_configured(self) -> bool:
        return True

    def retrieve_schema(self, question: str) -> dict:
        return {"tables": {"inventory": {}}}

    def generate_candidates(self, question, schema, *, prior_violations=None):
        return ["SELECT sku FROM inventory LIMIT 5"]

    def record_validated(self, question, sql):
        raise AssertionError("shadow must not promote")


class _BoomPort(_OkPort):
    def generate_candidates(self, question, schema, *, prior_violations=None):
        raise RuntimeError("l2 boom")


class _FrozenDateTime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 9, 4, 1, 2, 3, tzinfo=timezone.utc)


def _freeze(monkeypatch) -> None:
    monkeypatch.setattr(
        "CortexOS.dms.answer_engine.uuid.uuid4",
        lambda: uuid.UUID("00000000-0000-4000-8000-000000000099"),
    )
    monkeypatch.setattr("CortexOS.dms.sql_guardrail.datetime", _FrozenDateTime)


def _dump(envelope: dict) -> bytes:
    return json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")


def _ask():
    from CortexOS.dms.answer_engine import answer

    return answer("Which suppliers have a risk score above 0.7?")


def test_shadow_off_vs_on_envelope_identical(monkeypatch, tmp_path: Path):
    _freeze(monkeypatch)
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _OkPort())
    off = _ask()

    monkeypatch.setenv("DMS_L2_SHADOW", "1")
    monkeypatch.setenv("DMS_L2_SHADOW_PATH", str(tmp_path / "l2_shadow.jsonl"))
    on = _ask()

    assert _dump(on) == _dump(off)
    assert on["layer"] == "governed_metric"
    assert on["badge"] == off["badge"]
    assert on["answer"] == off["answer"]
    assert on["rows"] == off["rows"]


def test_shadow_writes_one_jsonl_record(monkeypatch, tmp_path: Path):
    _freeze(monkeypatch)
    path = tmp_path / "nested" / "l2_shadow.jsonl"
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.setenv("DMS_L2_SHADOW", "1")
    monkeypatch.setenv("DMS_L2_SHADOW_PATH", str(path))
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _OkPort())
    served = _ask()
    assert served["layer"] == "governed_metric"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["question"].startswith("Which suppliers")
    assert rec["served_layer"] == "governed_metric"
    assert rec["served_badge"] == "governed_metric"
    assert rec["served_row_count"] == 8
    assert rec["l2_sql"]
    assert rec["l2_refusal_type"] is None
    assert rec["l2_row_count"] == 5
    assert isinstance(rec["agree"], bool)
    assert rec["latency_ms"] >= 0


def test_shadow_l2_exception_does_not_change_envelope(monkeypatch, tmp_path: Path):
    _freeze(monkeypatch)
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    off = _ask()

    monkeypatch.setenv("DMS_L2_SHADOW", "1")
    monkeypatch.setenv("DMS_L2_SHADOW_PATH", str(tmp_path / "l2_shadow.jsonl"))
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: _BoomPort())
    on = _ask()
    assert _dump(on) == _dump(off)
    rec = json.loads((tmp_path / "l2_shadow.jsonl").read_text(encoding="utf-8"))
    assert rec["l2_refusal_type"] == "exception:RuntimeError"
    assert rec["agree"] is False
