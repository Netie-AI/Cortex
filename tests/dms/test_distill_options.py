"""Constructor distill-options registry schema (RSF-01)."""

from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.execution.distill_options import (
    ALLOWED_ENGINE_ROLES,
    EGRESS_CONFIG_KEY,
    FREEROUTE_PATH,
    OMNIROUTE_VENDOR_PORT,
    PRODUCT_ENGINE_ROLE,
    REQUIRED_OPTION_IDS,
    catalog,
    get_option,
    register_option,
    reset_registry,
)

DISTILL_OPTIONS_PY = (
    Path(__file__).resolve().parents[2] / "CortexOS" / "execution" / "distill_options.py"
)


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    reset_registry()


def test_required_ids_are_present_and_extensible():
    ids = {row["id"] for row in catalog()}
    assert set(REQUIRED_OPTION_IDS) <= ids
    register_option(
        {
            "id": "rsf_hook",
            "name": "RSF hook",
            "engine_role": "compete",
            "adapter": "meta_router",
            "route": {"kind": "meta_router_route", "module": None, "via": None},
            "egress": {"config_key": EGRESS_CONFIG_KEY},
            "blurb": "Tiny RSF-03/04/06 hook. Not a product engine.",
        }
    )
    assert "rsf_hook" in {row["id"] for row in catalog()}


def test_required_roles_never_product_engine():
    for row in catalog():
        if row["id"] not in REQUIRED_OPTION_IDS:
            continue
        assert row["engine_role"] in ALLOWED_ENGINE_ROLES
        assert row["engine_role"] != PRODUCT_ENGINE_ROLE
        assert row["adapter"] == "meta_router"


def test_analogs_are_distill_only():
    for option_id in ("myn8n", "langchain", "langflow"):
        assert get_option(option_id)["engine_role"] == "distill_only"
        assert get_option(option_id)["route"]["module"] is None


def test_gencfsm_dag_points_at_existing_gen_cfsm_dag_runner():
    row = get_option("gencfsm_dag")
    assert row["engine_role"] == "learn"
    assert row["route"]["module"] == "CortexOS.execution.gen_cfsm"
    assert row["route"]["via"] == "CortexOS.execution.dag_runner"
    gen_cfsm_py = DISTILL_OPTIONS_PY.parent / "gen_cfsm.py"
    assert gen_cfsm_py.is_file()
    source = gen_cfsm_py.read_text(encoding="utf-8")
    assert "from CortexOS.execution.dag_runner import" in source
    assert "No third orchestrator" in source


def test_register_option_rejects_product_engine():
    with pytest.raises(ValueError, match="product_engine"):
        register_option(
            {
                "id": "evil",
                "engine_role": "product_engine",
                "adapter": "meta_router",
            }
        )


def test_freeroute_egress_is_config_key_only():
    source = DISTILL_OPTIONS_PY.read_text(encoding="utf-8")
    for row in catalog():
        assert row["egress"]["config_key"] == EGRESS_CONFIG_KEY
        assert row["egress"]["freeroute_path"] == FREEROUTE_PATH
        assert "20128" in row["egress"]["note"]
    assert OMNIROUTE_VENDOR_PORT == 20128
    assert "127.0.0.1:20128" not in source
    assert "do not vendor" in source.lower()
