"""Deterministic production ship-gate for the Netie-AI estate.

Claude can scaffold. This module refuses a ship when required evidence is
missing. It never certifies SOC 2 / HIPAA / GDPR from files on disk.

Adaptive: surfaces from estate.Fingerprint pick the domains. A public static
web app is scored for privacy/a11y; an RTL tree is not. Empty repos fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from CortexOS.crew.estate import CATALOG, Fingerprint, by_slug, required_domains

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True)
class Check:
    domain: str
    check_id: str
    status: str
    evidence: str
    why: str

    def as_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "check_id": self.check_id,
            "status": self.status,
            "evidence": self.evidence,
            "why": self.why,
        }


@dataclass(frozen=True)
class Report:
    repo: str
    kind: str
    verdict: str
    domains: tuple[str, ...]
    checks: tuple[Check, ...]
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "kind": self.kind,
            "verdict": self.verdict,
            "domains": list(self.domains),
            "checks": [c.as_dict() for c in self.checks],
            "notes": list(self.notes),
            "law": (
                "Fail closed on missing evidence. Skip is not pass. "
                "Do not claim SOC2/HIPAA/GDPR from this report. Do not auto-merge."
            ),
        }


def _check(
    domain: str, check_id: str, status: str, evidence: str, why: str
) -> Check:
    return Check(domain, check_id, status, evidence, why)


def _score_repo(fp: Fingerprint) -> list[Check]:
    out: list[Check] = []
    domains = set(required_domains(fp))
    # Public marketing / Pages apps. Local consoles (engine, keys) are not scored as WCAG sites.
    public_web = (not fp.private) and "web" in fp.surfaces and not (
        {"engine", "keys"} & set(fp.surfaces)
    )
    apiish = bool({"api", "engine", "keys", "news"} & set(fp.surfaces))

    if fp.empty:
        out.append(
            _check(
                "Reliability",
                "repo_empty",
                FAIL,
                "git tree empty",
                "Empty repo cannot ship to users.",
            )
        )
        return out

    if "Security" in domains:
        secret_ok = fp.has_sops or fp.has_secrets_scan
        out.append(
            _check(
                "Security",
                "secrets_hygiene",
                PASS if secret_ok else (SKIP if not apiish else FAIL),
                "sops" if fp.has_sops else ("secrets_scan" if fp.has_secrets_scan else "none"),
                "Need SOPS or a secrets scanner before an API/engine/keys ship."
                if apiish
                else "No API surface; scanner optional.",
            )
        )
        if apiish:
            out.append(
                _check(
                    "Security",
                    "auth",
                    PASS if (fp.has_auth or fp.has_csrf) else FAIL,
                    "auth" if fp.has_auth else ("csrf" if fp.has_csrf else "none"),
                    "API/keys/engine need auth or CSRF evidence.",
                )
            )
            out.append(
                _check(
                    "Security",
                    "rate_limit",
                    PASS if fp.has_rate_limit else FAIL,
                    "rate_limit" if fp.has_rate_limit else "none",
                    "API surfaces need a rate limiter.",
                )
            )
        if "keys" in fp.surfaces or "engine" in fp.surfaces:
            out.append(
                _check(
                    "Security",
                    "rbac",
                    PASS if fp.has_rbac else (PASS if "keys" in fp.surfaces and fp.has_auth else FAIL),
                    "rbac" if fp.has_rbac else ("auth-as-gate" if fp.has_auth else "none"),
                    "Engine needs RBAC. Vault may use its own auth gate.",
                )
            )
        out.append(
            _check(
                "Security",
                "compliance_cert",
                SKIP,
                "none",
                "File presence is not SOC 2, HIPAA, or GDPR certification. Do not claim it.",
            )
        )

    if "Reliability" in domains:
        out.append(
            _check(
                "Reliability",
                "tests",
                PASS if fp.has_tests else FAIL,
                "tests/" if fp.has_tests else "none",
                "Code that ships to users needs tests.",
            )
        )

    if "Infra" in domains:
        ci_ok = fp.has_ci or bool(fp.workflows)
        out.append(
            _check(
                "Infra",
                "ci",
                PASS if ci_ok else FAIL,
                ",".join(fp.workflows) if fp.workflows else "none",
                "Need a CI workflow (or Pages workflow) before shipping.",
            )
        )
        if apiish:
            boxed = fp.has_dockerfile or fp.has_compose
            out.append(
                _check(
                    "Infra",
                    "container",
                    PASS if boxed else SKIP,
                    "docker" if boxed else "none",
                    "Container evidence helps deploy. Missing is skip, not a fake pass.",
                )
            )

    if "Architecture" in domains:
        if "engine" in fp.surfaces:
            # Cortex pins cortex-contract; catalog notes that as the versioning story.
            out.append(
                _check(
                    "Architecture",
                    "contract",
                    PASS,
                    "contract/ (catalog)",
                    "Engine ships a versioned OpenAPI contract. Do not hand-edit it.",
                )
            )
        elif "rtl" in fp.surfaces:
            out.append(
                _check(
                    "Architecture",
                    "rtl_dv",
                    PASS if fp.has_tests and fp.has_ci else FAIL,
                    "sim/lint/formal" if fp.has_ci else "none",
                    "RTL ships through sim/lint/formal, not WCAG.",
                )
            )
        else:
            out.append(
                _check(
                    "Architecture",
                    "api_shape",
                    SKIP,
                    "unproven",
                    "No contract artifact in catalog. Do not invent versioning.",
                )
            )

    if "Observability" in domains:
        out.append(
            _check(
                "Observability",
                "vuln_scan",
                PASS if (fp.has_secrets_scan or fp.has_dependabot or "secrets.yml" in fp.workflows) else SKIP,
                "secrets.yml" if "secrets.yml" in fp.workflows else (
                    "secrets_scan" if fp.has_secrets_scan else "none"
                ),
                "Vuln/secrets scan is evidence. Sentry/RUM is not inferred.",
            )
        )
        out.append(
            _check(
                "Observability",
                "sentry_rum",
                SKIP,
                "sentry" if fp.has_sentry else "none",
                "No Sentry/RUM claim from file presence.",
            )
        )

    if "Surface" in domains:
        out.append(
            _check(
                "Surface",
                "readme",
                PASS if fp.has_readme else FAIL,
                "README.md" if fp.has_readme else "none",
                "Public or shippable code needs a README.",
            )
        )
        if not fp.private:
            out.append(
                _check(
                    "Surface",
                    "license",
                    PASS if fp.has_license else FAIL,
                    "LICENSE" if fp.has_license else "none",
                    "Public repos need a LICENSE.",
                )
            )
        if public_web:
            out.append(
                _check(
                    "Surface",
                    "privacy",
                    PASS if fp.has_privacy_page else FAIL,
                    "privacy page" if fp.has_privacy_page else "none",
                    "Public web app needs a privacy page before user traffic.",
                )
            )
            out.append(
                _check(
                    "Surface",
                    "a11y",
                    PASS if fp.has_a11y else FAIL,
                    "wcag" if fp.has_a11y else "none",
                    "Public web app needs WCAG evidence. Missing is fail, not skip.",
                )
            )
        else:
            out.append(
                _check(
                    "Surface",
                    "a11y",
                    SKIP,
                    "n/a",
                    "Not a public marketing/web-only surface. Demo UI is not scored as WCAG.",
                )
            )

    return out


def evaluate(fp: Fingerprint) -> Report:
    checks = tuple(_score_repo(fp))
    fails = [c for c in checks if c.status == FAIL]
    verdict = FAIL if fails else PASS
    return Report(
        repo=fp.full_name,
        kind=fp.kind,
        verdict=verdict,
        domains=required_domains(fp),
        checks=checks,
        notes=fp.notes,
    )


def evaluate_slug(name: str) -> Report | None:
    fp = by_slug(name)
    if fp is None:
        return None
    return evaluate(fp)


def evaluate_all() -> list[Report]:
    return [evaluate(fp) for fp in CATALOG]


def render_report(report: Report) -> str:
    lines = [
        f"SHIP {report.verdict.upper()}  {report.repo}  kind={report.kind}",
        "Fail closed on missing evidence. Skip is not pass. Do not auto-merge.",
        "Do not claim SOC2/HIPAA/GDPR from this report.",
        "domains: " + (",".join(report.domains) or "(none)"),
    ]
    for note in report.notes:
        lines.append(f"note: {note}")
    for check in report.checks:
        lines.append(
            f"- {check.status.upper()} {check.domain}/{check.check_id} "
            f"evidence={check.evidence} -- {check.why}"
        )
    fails = [c for c in report.checks if c.status == FAIL]
    if fails:
        lines.append("Next: spawn a job-named teammate only for FAIL domains.")
    else:
        lines.append("Next: human merge. Spawn 0 domain specialists.")
    return "\n".join(lines)


def render_slug(name: str) -> str:
    if name.strip() in {"*", "all", "estate"}:
        reports = evaluate_all()
        lines = [f"Estate ship-gate: {len(reports)} repos"]
        failed = [r for r in reports if r.verdict == FAIL]
        lines.append(f"FAIL={len(failed)} PASS={len(reports) - len(failed)}")
        for report in reports:
            lines.append("")
            lines.append(render_report(report))
        return "\n".join(lines)
    report = evaluate_slug(name)
    if report is None:
        return (
            f"SHIP FAIL  unknown repo '{name}'. "
            "Call estate_status. Catalog is github.com/Netie-AI as of 2026-08-25."
        )
    return render_report(report)
