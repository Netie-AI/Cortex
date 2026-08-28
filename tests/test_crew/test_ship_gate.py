"""Production ship-gate + Netie-AI estate. Assert the operator-visible report."""

from __future__ import annotations

from CortexOS.crew.detect import attached_skills, plan
from CortexOS.crew.estate import CATALOG, by_slug, render, required_domains, snapshot
from CortexOS.crew.roles import by_name, catalog
from CortexOS.crew.ship_gate import FAIL, PASS, SKIP, evaluate, evaluate_all, render_slug


def test_catalog_includes_production_templates() -> None:
    names = {r["name"] for r in catalog()}
    assert {"Security", "Reliability", "Infra", "Architecture", "Observability", "Surface"} <= names
    gate = by_name("Gate")
    assert gate is not None
    assert "ship" in gate.skills
    assert "estate_status" in gate.role
    sec = by_name("Security")
    assert sec is not None
    assert "SOC2" in sec.role
    assert attached_skills(("Surface",)) == ("ship", "product-surface", "seo")


def test_estate_catalog_covers_netie_ai() -> None:
    slugs = {fp.slug for fp in CATALOG if fp.placement == "canonical"}
    assert slugs == {
        "Cortex",
        "constructor",
        "OpenVault",
        "Cassandra",
        "VKing",
        "OpenForge",
        "AnalogCrawler",
        "OpenHBM",
        "CI-Doctor",
        "AIM",
        "Vertex",
    }
    aim = by_slug("Netie-AI/AIM")
    assert aim is not None and aim.empty is True
    hbm = by_slug("OpenHBM")
    assert hbm is not None
    assert "Surface" not in required_domains(hbm)
    ctor = by_slug("constructor")
    assert ctor is not None
    assert "Surface" in required_domains(ctor)
    assert by_slug("VKing") is not None
    assert by_slug("VKing").full_name == "Netie-AI/VKing"
    personal = by_slug("jian-hong/Vking")
    assert personal is not None
    assert personal.placement == "accidental"
    assert by_slug("AirGPT").placement == "unseen"
    assert by_slug("dms").placement == "unseen"
    assert by_slug("DMS").full_name == "Netie-AI/dms"
    assert by_slug("Netie-KB").placement == "unseen"
    assert by_slug("netie-control").placement == "unseen"
    assert by_slug("ViKing").full_name == "Netie-AI/ViKing"
    assert by_slug("VKing").full_name == "Netie-AI/VKing"


def test_estate_snapshot_offline_names_the_law(monkeypatch) -> None:
    monkeypatch.setenv("CREW_LIVE_PROBES", "0")
    snap = snapshot()
    assert snap["org"] == "Netie-AI"
    assert snap["n"] == len(CATALOG)
    assert snap["expected_unseen"]
    assert any(r["full_name"] == "Netie-AI/dms" for r in snap["expected_unseen"])
    assert any(r["full_name"] == "Netie-AI/Netie-KB" for r in snap["expected_unseen"])
    assert any(r["full_name"] == "jian-hong/Vking" for r in snap["accidental"])
    text_law = snap["law"]
    assert "compliance certificate" in text_law.lower() or "certificate" in text_law
    assert "auto-merge" in text_law.lower()
    note = str(snap.get("access_note") or "")
    assert "permissionless" in note
    assert "Helio.AI" in note
    assert "Revoke" in note
    rendered = render(snap)
    assert "Netie-AI/Cortex" in rendered
    assert "do not commit tokens" in rendered.lower()


def test_ship_gate_adaptive_verdicts_are_in_the_report() -> None:
    cortex = evaluate(by_slug("Cortex"))  # type: ignore[arg-type]
    text = render_slug("Cortex")
    assert cortex.verdict == PASS
    assert "SHIP PASS" in text
    assert "SOC2" in text
    assert "Do not auto-merge" in text
    assert "WCAG" not in text or "not a public marketing" in text.lower() or "n/a" in text

    aim = render_slug("AIM")
    assert "SHIP FAIL" in aim
    assert "Empty repo cannot ship" in aim

    ctor = render_slug("constructor")
    assert "SHIP FAIL" in ctor
    assert "Surface/a11y" in ctor
    assert "Surface/privacy" in ctor

    hbm = render_slug("OpenHBM")
    assert "SHIP PASS" in hbm
    assert "rtl_dv" in hbm
    assert "Surface/a11y" not in hbm

    analog = render_slug("AnalogCrawler")
    assert "SHIP FAIL" in analog
    assert "Infra/ci" in analog
    assert "does not review or enable" in analog

    air = render_slug("AirGPT")
    assert "SHIP FAIL" in air
    assert "private_unseen" in air
    assert "remote-login" in air
    dms = render_slug("dms")
    assert "private_unseen" in dms
    assert "Do not claim the repo is missing" in dms
    kb = render_slug("Netie-KB")
    assert "private_unseen" in kb
    control = render_slug("netie-control")
    assert "private_unseen" in control
    vking_copy = render_slug("jian-hong/Vking")
    assert "accidental_personal_copy" in vking_copy
    assert "Do not ship from here" in vking_copy
    optio = render_slug("jian-hong/optio")
    assert "second_orchestrator" in optio
    assert "Do not merge into Cortex" in optio


def test_unknown_repo_fails_closed() -> None:
    text = render_slug("not-a-netie-repo")
    assert "SHIP FAIL" in text
    assert "estate_status" in text


def test_estate_sweep_counts_fails() -> None:
    reports = evaluate_all()
    text = render_slug("all")
    failed = [r for r in reports if r.verdict == FAIL]
    assert len(reports) == len(CATALOG)
    assert len(failed) >= 1
    assert f"FAIL={len(failed)}" in text
    assert "AIM" in text
    # skip is recorded, never turned into pass
    cortex = next(r for r in reports if r.repo.endswith("/Cortex"))
    skips = [c for c in cortex.checks if c.status == SKIP]
    assert any(c.check_id == "compliance_cert" for c in skips)


def test_detect_collapses_production_sweep_to_gate() -> None:
    sweep = plan(
        "before shipping govern sql injection xss unit tests ci/cd "
        "connection pooling sentry wcag for every repo"
    )
    assert sweep.capabilities == ("Gate",)
    assert sweep.spawn is True
    assert "ship" in attached_skills(sweep.capabilities)

    deep = plan("sql injection review of the ledger API")
    assert deep.capabilities == ("Security",)
    assert "security" in attached_skills(deep.capabilities)

    a11y = plan("WCAG on constructor")
    assert "Surface" in a11y.capabilities
    assert "seo" in attached_skills(a11y.capabilities)

    ship_one = plan("before shipping Cortex")
    assert "Gate" in ship_one.capabilities
    assert "Security" not in ship_one.capabilities
