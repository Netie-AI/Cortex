"""Netie-AI GitHub estate: adaptive repo catalog for crew ship-gate.

Live `gh` is optional. The static catalog is the 2026-08-25 probe of
github.com/Netie-AI so a ship decision still works when CREW_LIVE_PROBES=0.
Missing evidence is never a pass. File presence is not a SOC 2 / HIPAA /
GDPR certificate.

Crew does not clone every repo. Surfaces decide which production domains
apply. RTL does not get WCAG. Empty repos cannot ship.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any

ORG_DEFAULT = "Netie-AI"

# Capability template names in roles.py. Gate is the sweep owner.
PRODUCTION_CAPS: tuple[str, ...] = (
    "Security",
    "Reliability",
    "Infra",
    "Architecture",
    "Observability",
    "Surface",
)

# Surface -> production domains that must be scored (skip the rest).
SURFACE_DOMAINS: dict[str, tuple[str, ...]] = {
    "empty": (),
    "docs": ("Reliability",),
    "web": ("Security", "Reliability", "Infra", "Surface", "Observability"),
    "api": ("Security", "Reliability", "Infra", "Architecture", "Observability"),
    "engine": ("Security", "Reliability", "Infra", "Architecture", "Observability"),
    "keys": ("Security", "Reliability", "Infra", "Observability"),
    "rtl": ("Reliability", "Infra", "Architecture"),
    "analog": ("Reliability", "Infra"),
    "news": ("Security", "Reliability", "Infra", "Surface", "Observability"),
    "ci_tool": ("Reliability", "Infra"),
    "manufacturing": ("Security", "Reliability", "Observability"),
    "crew": ("Security", "Reliability"),
    "chat": ("Security", "Reliability", "Infra", "Surface", "Observability"),
    "vertical": ("Security", "Reliability", "Infra", "Architecture", "Observability"),
    "missing": (),
    "unseen": (),
    "accidental": ("Security",),
    "fork": ("Architecture",),
}


@dataclass(frozen=True)
class Fingerprint:
    slug: str
    full_name: str
    private: bool
    language: str
    surfaces: tuple[str, ...]
    workflows: tuple[str, ...] = ()
    has_tests: bool = False
    has_dockerfile: bool = False
    has_compose: bool = False
    has_sops: bool = False
    has_secrets_scan: bool = False
    has_auth: bool = False
    has_rbac: bool = False
    has_rate_limit: bool = False
    has_csrf: bool = False
    has_privacy_page: bool = False
    has_license: bool = False
    has_readme: bool = False
    has_ci: bool = False
    has_dependabot: bool = False
    has_sentry: bool = False
    has_a11y: bool = False
    empty: bool = False
    placement: str = "canonical"
    notes: tuple[str, ...] = ()

    @property
    def owner(self) -> str:
        if "/" in self.full_name:
            return self.full_name.split("/", 1)[0]
        return ORG_DEFAULT

    @property
    def kind(self) -> str:
        if self.placement in {"missing", "unseen", "accidental", "fork"}:
            return self.placement
        if self.empty:
            return "empty"
        for name in (
            "engine",
            "keys",
            "rtl",
            "analog",
            "news",
            "ci_tool",
            "manufacturing",
            "chat",
            "vertical",
            "docs",
            "web",
            "api",
        ):
            if name in self.surfaces:
                return name
        return "docs"


def _fp(**kwargs: Any) -> Fingerprint:
    return Fingerprint(**kwargs)


# Measured 2026-08-25 via gh api against github.com/Netie-AI. Refresh with
# estate.snapshot(live=True) when CREW_LIVE_PROBES=1.
CATALOG: tuple[Fingerprint, ...] = (
    _fp(
        slug="Cortex",
        full_name="Netie-AI/Cortex",
        private=True,
        language="Python",
        surfaces=("engine", "api", "web", "crew"),
        workflows=("ci.yml", "release.yml", "rls.yml", "secrets.yml", "test.yml"),
        has_tests=True,
        has_dockerfile=True,
        has_compose=True,
        has_sops=True,
        has_secrets_scan=True,
        has_auth=True,
        has_rbac=True,
        has_rate_limit=True,
        has_license=True,
        has_readme=True,
        has_ci=True,
        notes=(
            "Local-first engine. Demo UI is not a public marketing site.",
            "SOC2/HIPAA/GDPR: not certified by file presence.",
            "Private Netie-AI product repos exist (operator screenshot 2026-08-25).",
            "This token gets GitHub 404 on private repos it cannot read. That is not absence.",
            "Grant the GitHub App 'All repositories' (or select dms, Netie-KB, Pointer, ...).",
        ),
    ),
    _fp(
        slug="constructor",
        full_name="Netie-AI/constructor",
        private=False,
        language="JavaScript",
        surfaces=("web",),
        workflows=("pages.yml",),
        has_ci=True,
        has_readme=True,
        notes=("Public GitHub Pages app. No tests directory on 2026-08-25.",),
    ),
    _fp(
        slug="OpenVault",
        full_name="Netie-AI/OpenVault",
        private=False,
        language="Python",
        surfaces=("keys", "api", "web"),
        workflows=("ci.yml",),
        has_tests=True,
        has_dockerfile=True,
        has_compose=True,
        has_secrets_scan=True,
        has_auth=True,
        has_rate_limit=True,
        has_csrf=True,
        has_license=True,
        has_readme=True,
        has_ci=True,
        notes=("Key custody. Secret reveal is a gate, not a pass-through.",),
    ),
    _fp(
        slug="Cassandra",
        full_name="Netie-AI/Cassandra",
        private=False,
        language="Python",
        surfaces=("news", "api", "web"),
        workflows=("ci.yml", "deploy.yml"),
        has_tests=True,
        has_dockerfile=True,
        has_compose=True,
        has_auth=True,
        has_rate_limit=True,
        has_privacy_page=True,
        has_readme=True,
        has_ci=True,
        notes=("News sentiment. Privacy page present. Not a trading-prod claim.",),
    ),
    _fp(
        slug="VKing",
        full_name="Netie-AI/VKing",
        private=False,
        language="Python",
        surfaces=("docs",),
        has_readme=False,
        notes=(
            "Plan/docs tree. No .github/workflows on 2026-08-25.",
            "Public personal copy also at jian-hong/Vking (older branch run-0003). Canonical is this repo.",
        ),
    ),
    _fp(
        slug="OpenForge",
        full_name="Netie-AI/OpenForge",
        private=False,
        language="Python",
        surfaces=("analog",),
        workflows=("ci.yml",),
        has_tests=True,
        has_license=True,
        has_readme=True,
        has_ci=True,
        notes=("Analog CAD/research. Not a user-facing web app.",),
    ),
    _fp(
        slug="AnalogCrawler",
        full_name="Netie-AI/AnalogCrawler",
        private=False,
        language="Python",
        surfaces=("analog",),
        has_compose=True,
        has_readme=True,
        notes=(
            "Lab crawler. No GitHub workflows on 2026-08-25.",
            "A surveillance/ path exists; ship-gate does not review or enable it.",
        ),
    ),
    _fp(
        slug="OpenHBM",
        full_name="Netie-AI/OpenHBM",
        private=False,
        language="SystemVerilog",
        surfaces=("rtl",),
        workflows=(
            "agent-eval.yml",
            "asic.yml",
            "chiplet.yml",
            "formal.yml",
            "fpga.yml",
            "lint.yml",
            "sim.yml",
        ),
        has_tests=True,
        has_license=True,
        has_readme=True,
        has_ci=True,
        notes=("RTL/IP. WCAG/SEO do not apply. Sim/lint/formal are the gate.",),
    ),
    _fp(
        slug="CI-Doctor",
        full_name="Netie-AI/CI-Doctor",
        private=False,
        language="Python",
        surfaces=("ci_tool",),
        has_license=True,
        has_readme=False,
        notes=("CI fixer package. No org workflows of its own on 2026-08-25.",),
    ),
    _fp(
        slug="AIM",
        full_name="Netie-AI/AIM",
        private=False,
        language="",
        surfaces=("empty",),
        empty=True,
        notes=(
            "Empty repo (LinkedIn Alternative). Cannot ship.",
            "jian-hong/aim is a 2025 TypeScript app, not this tree. Name collision only.",
        ),
    ),
    _fp(
        slug="Vertex",
        full_name="Netie-AI/Vertex",
        private=False,
        language="Python",
        surfaces=("manufacturing",),
        workflows=("python-lint.yml",),
        has_dockerfile=True,
        has_compose=True,
        has_readme=True,
        has_ci=True,
        notes=("Precision manufacturing samples. Observability compose exists.",),
    ),
    # Private product repos. Operator screenshot 2026-08-25. This token still 404s them.
    _fp(
        slug="dms",
        full_name="Netie-AI/dms",
        private=True,
        language="Python",
        surfaces=("unseen", "vertical"),
        placement="unseen",
        notes=(
            "Operator screenshot: Netie DMS -- ChatGPT for Excel/databases (Cortex consumer).",
            "This token 404s private repos. Grant GitHub App access. Do not treat 404 as missing.",
        ),
    ),
    _fp(
        slug="Netie-KB",
        full_name="Netie-AI/Netie-KB",
        private=True,
        language="Python",
        surfaces=("unseen",),
        placement="unseen",
        notes=("Operator screenshot: Netie-KB exists (Python). Token cannot read it.",),
    ),
    _fp(
        slug="Netie",
        full_name="Netie-AI/Netie",
        private=True,
        language="Python",
        surfaces=("unseen",),
        placement="unseen",
        notes=("Operator screenshot: Netie exists (Python). Orchestration KB home.",),
    ),
    _fp(
        slug="Pointer",
        full_name="Netie-AI/Pointer",
        private=True,
        language="JavaScript",
        surfaces=("unseen",),
        placement="unseen",
        notes=("Operator screenshot: Pointer exists (JavaScript). Act/computer-control client.",),
    ),
    _fp(
        slug="landing",
        full_name="Netie-AI/landing",
        private=True,
        language="HTML",
        surfaces=("unseen", "web"),
        placement="unseen",
        notes=("Operator screenshot: landing exists (HTML). Constructor ticket landing#9.",),
    ),
    _fp(
        slug="Space",
        full_name="Netie-AI/Space",
        private=True,
        language="JavaScript",
        surfaces=("unseen", "web"),
        placement="unseen",
        notes=("Operator screenshot: Space exists (JavaScript). DMS Spaces product surface.",),
    ),
    _fp(
        slug="netie-control",
        full_name="Netie-AI/netie-control",
        private=True,
        language="Python",
        surfaces=("unseen", "crew"),
        placement="unseen",
        notes=(
            "Operator screenshot: operator shell Plane 4. Displays and launches; holds no keys, decides no route.",
            "Not a key vault. OpenVault holds keys.",
        ),
    ),
    _fp(
        slug="RUMA-Houser",
        full_name="Netie-AI/RUMA-Houser",
        private=True,
        language="",
        surfaces=("unseen",),
        placement="unseen",
        notes=("Operator screenshot: RUMA-Houser exists. Do not modify packs/ruma unless asked.",),
    ),
    _fp(
        slug="ViKing",
        full_name="Netie-AI/ViKing",
        private=True,
        language="Python",
        surfaces=("unseen",),
        placement="unseen",
        notes=(
            "Operator screenshot: ViKing exists private. Distinct from public Netie-AI/VKing.",
            "jian-hong/Vking is still an accidental public copy of the public VKing tree.",
        ),
    ),
    _fp(
        slug="AirGPT",
        full_name="Netie-AI/AirGPT",
        private=True,
        language="Python",
        surfaces=("unseen", "chat"),
        placement="unseen",
        notes=(
            "Not in the 2026-08-25 screenshot. Local D:\\AirGPT may still be the SoT.",
            "If it is a private org repo, this token cannot see it. Grant GitHub App access.",
        ),
    ),
    _fp(
        slug="chatbot",
        full_name="Netie-AI/chatbot",
        private=True,
        language="",
        surfaces=("unseen", "chat"),
        placement="unseen",
        notes=("CORTEX_WS_CHATBOT. Not in the screenshot. Token cannot confirm presence.",),
    ),
    _fp(
        slug="OMI",
        full_name="Netie-AI/OMI",
        private=True,
        language="",
        surfaces=("unseen",),
        placement="unseen",
        notes=("CORTEX_WS_OMI. Not in the screenshot. Token cannot confirm presence.",),
    ),
    # Accidental stores on github.com/jian-hong (founder personal). Token cannot see private there.
    _fp(
        slug="Vking-personal",
        full_name="jian-hong/Vking",
        private=False,
        language="Python",
        surfaces=("accidental", "docs"),
        placement="accidental",
        notes=(
            "Public personal duplicate of Netie-AI/VKing. Same cortex SKILL card. Branch run-0003 vs org run-0004.",
            "Archive or make private. Canonical is Netie-AI/VKing. Do not ship from jian-hong.",
        ),
    ),
    _fp(
        slug="Python_Automation_JH",
        full_name="jian-hong/Python_Automation_JH",
        private=False,
        language="Python",
        surfaces=("accidental",),
        placement="accidental",
        notes=(
            "Windows venv/ committed (~8809 files). .gitignore does not list venv.",
            "Not a Cortex copy. Accidental store. Do not treat as a Netie product origin.",
        ),
    ),
    _fp(
        slug="optio",
        full_name="jian-hong/optio",
        private=False,
        language="",
        surfaces=("fork",),
        placement="fork",
        notes=(
            "Fork of a third-party agent-swarm orchestrator. Do not merge into Cortex. Cortex is the loop.",
            "A token-shaped file is committed. Operator must rotate if it was real. Crew does not print it.",
        ),
    ),
)


def by_slug(name: str) -> Fingerprint | None:
    key = name.strip()
    if not key:
        return None
    low = key.lower()
    for row in CATALOG:
        if row.full_name.lower() == low:
            return row
    tail = low.split("/", 1)[-1]
    hits = [row for row in CATALOG if row.slug.lower() == tail]
    if not hits:
        # Allow AirGPT via "airgpt" even when slug has no hyphen variants.
        hits = [row for row in CATALOG if row.slug.lower().replace("-", "") == tail.replace("-", "")]
    if not hits:
        return None
    org = [h for h in hits if h.full_name.lower().startswith("netie-ai/")]
    return (org or hits)[0]


def required_domains(fp: Fingerprint) -> tuple[str, ...]:
    """Union of domains implied by surfaces. Empty repo: none (overall fail)."""
    found: list[str] = []
    seen: set[str] = set()
    for surface in fp.surfaces:
        for domain in SURFACE_DOMAINS.get(surface, ()):
            if domain in seen:
                continue
            seen.add(domain)
            found.append(domain)
    return tuple(found)


def public_row(fp: Fingerprint) -> dict[str, Any]:
    return {
        "slug": fp.slug,
        "full_name": fp.full_name,
        "owner": fp.owner,
        "private": fp.private,
        "language": fp.language,
        "kind": fp.kind,
        "placement": fp.placement,
        "surfaces": list(fp.surfaces),
        "domains": list(required_domains(fp)),
        "empty": fp.empty,
        "notes": list(fp.notes),
        "workflows": list(fp.workflows),
    }


def overlay_live_workflows(fp: Fingerprint, workflows: tuple[str, ...]) -> Fingerprint:
    if not workflows:
        return fp
    return replace(fp, workflows=workflows, has_ci=True)


def snapshot(*, live: bool | None = None, org: str | None = None) -> dict[str, Any]:
    """Estate view. Live gh names are extra; catalog remains the scored set."""
    use_live = live if live is not None else os.environ.get("CREW_LIVE_PROBES", "1") != "0"
    org_name = org or os.environ.get("CREW_GH_ORG", ORG_DEFAULT)
    live_names: list[str] = []
    live_detail = "CREW_LIVE_PROBES=0; static catalog only"
    if use_live:
        from CortexOS.crew import github

        listed = github.list_org_repos(org_name)
        live_detail = str(listed.get("detail") or "")
        live_names = [str(r.get("name") or "") for r in (listed.get("repos") or []) if r.get("name")]
    rows = [public_row(fp) for fp in CATALOG]
    catalog_slugs = {fp.slug for fp in CATALOG if fp.owner == org_name}
    unknown = [n for n in live_names if n not in catalog_slugs]
    missing = [
        fp.slug
        for fp in CATALOG
        if fp.placement == "canonical" and fp.owner == org_name and live_names and fp.slug not in live_names
    ]
    accidental = [public_row(fp) for fp in CATALOG if fp.placement == "accidental"]
    expected_missing = [public_row(fp) for fp in CATALOG if fp.placement == "missing"]
    expected_unseen = [public_row(fp) for fp in CATALOG if fp.placement == "unseen"]
    return {
        "ok": True,
        "org": org_name,
        "n": len(rows),
        "repos": rows,
        "live_names": live_names,
        "unknown_live": unknown,
        "missing_from_live": missing,
        "accidental": accidental,
        "expected_missing": expected_missing,
        "expected_unseen": expected_unseen,
        "probed": "2026-08-25",
        "detail": live_detail,
        "private_jian_hong": "invisible to this token; only public jian-hong repos were scanned",
        "law": (
            "Adaptive ship-gate. Detect the job. Spawn Gate for a sweep, not one "
            "specialist per heading. File presence is not a compliance certificate. "
            "A GitHub 404 from this token is not proof a private repo is missing. "
            "Grant the GitHub App All repositories. Do not build a remote-login box. "
            "OpenVault holds keys. Human is money/decision. Do not auto-merge."
        ),
    }


def render(snap: dict[str, Any] | None = None) -> str:
    data = snap or snapshot()
    lines = [
        str(data.get("law") or ""),
        f"Org: {data.get('org')}  catalog={data.get('n')}  probed={data.get('probed')}",
    ]
    detail = str(data.get("detail") or "")
    if detail:
        lines.append(detail)
    unknown = data.get("unknown_live") or []
    if unknown:
        lines.append("Live repos not in catalog: " + ", ".join(unknown))
    accidental = data.get("accidental") or []
    if accidental:
        lines.append("Accidental personal copies:")
        for row in accidental:
            lines.append(f"- {row.get('full_name')} [{row.get('kind')}]")
    expected = data.get("expected_unseen") or []
    if expected:
        lines.append("Private repos this token cannot read (operator confirmed they exist):")
        for row in expected:
            lines.append(f"- {row.get('full_name')} (grant GitHub App access)")
    missing_rows = data.get("expected_missing") or []
    if missing_rows:
        lines.append("Expected products with no GitHub origin:")
        for row in missing_rows:
            lines.append(f"- {row.get('full_name')} (create under Netie-AI)")
    jh = data.get("private_jian_hong")
    if jh:
        lines.append("jian-hong private: " + str(jh))
    for row in data.get("repos") or []:
        domains = ",".join(row.get("domains") or []) or "(none)"
        empty = " EMPTY" if row.get("empty") else ""
        lines.append(
            f"- {row.get('full_name')} [{row.get('kind')}]{empty} "
            f"lang={row.get('language') or '-'} domains={domains}"
        )
    return "\n".join(lines)
