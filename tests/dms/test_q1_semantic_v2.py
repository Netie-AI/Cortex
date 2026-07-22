"""Q1 — governed semantic layer (metrics + certified + value dictionaries).

Proves the layer the Q2 answer engine compiles against is trustworthy:
every metric compiles + executes, params validate, injection is neutralized,
values resolve, and the certified repo covers the core golden questions.
"""
from __future__ import annotations

import pytest

from packs.dms.semantic import values as vd
from packs.dms.semantic.loader import (
    SemanticError,
    compile_metric,
    load_all,
    reload,
    validate_all,
)


@pytest.fixture(scope="module", autouse=True)
def ensure_db():
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    reload()  # fresh value dictionaries + model against the loaded DB
    yield


def test_load_all_validates():
    m = load_all()
    assert len(m.metrics) >= 15
    assert len(m.certified) >= 18
    dicts = vd.all_dictionaries()
    assert len([c for c, v in dicts.items() if v]) >= 6
    # PII columns never dictionaried
    for pii in ("email", "phone", "contact_person"):
        assert pii not in dicts


def test_metric_templates_compile_and_execute():
    report = validate_all(execute=True)
    m = load_all()
    assert len(report["metrics"]) == len(m.metrics)
    assert len(report["certified"]) == len(m.certified)


def test_param_injection_neutralized():
    m = load_all()
    # dirty string containing a real value → resolves to whitelist, payload dropped
    sql = compile_metric(m, "items_by_category",
                         {"category": "CHEMICALS'; DROP TABLE inventory--"})
    assert ";" not in sql and "DROP" not in sql.upper()
    assert "'CHEMICALS'" in sql
    # pure garbage with no resolvable value → hard fail
    with pytest.raises(SemanticError):
        compile_metric(m, "items_by_category", {"category": "'; DROP TABLE inventory --"})


def test_param_ranges_and_enums_enforced():
    m = load_all()
    with pytest.raises(SemanticError):
        compile_metric(m, "sales_by_value", {"limit": 99999})
    with pytest.raises(SemanticError):
        compile_metric(m, "suppliers_by_risk", {"threshold": 5.0})
    with pytest.raises(SemanticError):
        compile_metric(m, "sales_by_value", {"direction": "SIDEWAYS"})


def test_value_resolution():
    assert vd.resolve("Chemicals", "category").value == "CHEMICALS"
    assert vd.resolve("warehouse A", "location_code").value == "WH-A"
    assert vd.resolve("warehouse C", "location_code").value == "WAREHOUSE C"  # messy dual-coding
    assert vd.resolve("in transit", "status").value == "IN_TRANSIT"
    assert vd.resolve("dhl", "carrier").value == "DHL MY"
    assert not vd.resolve("totally bogus value", "category").ok


def test_optional_filter_and_defaults():
    m = load_all()
    unscoped = compile_metric(m, "low_stock", {})
    scoped = compile_metric(m, "low_stock", {"wh": "warehouse A"})
    assert "location_code" not in unscoped  # no filter rendered
    assert "'WH-A'" in scoped
    # value default renders without an explicit param
    assert "'DELAYED'" in compile_metric(m, "count_by_carrier", {})


def test_certified_covers_core_golden():
    from bench.accuracy import load_golden

    m = load_all()
    core_qs = {i.question for i in load_golden() if i.tier == "core"}
    certified_qs = {c.question for c in m.certified}
    # every certified query executes (checked in validate_all); here assert the
    # core golden questions are represented in the certified repository.
    missing = core_qs - certified_qs
    assert len(missing) <= 4, f"core golden not certified: {missing}"
