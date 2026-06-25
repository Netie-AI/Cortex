from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(slots=True)
class RuleViolation:
    rule_id: str
    severity: str
    message: str


def _rules_dir() -> Path:
    from netie.config import get_config
    from netie.packs.loader import load_pack, resolve_pack_dir

    cfg = get_config()
    pack = load_pack(cfg.pack, resolve_pack_dir(cfg.pack_dir))
    if pack.rules_dir.is_dir():
        return pack.rules_dir
    return Path(__file__).resolve().parent / "rules"


def _subset_matches(extracted: dict, matchers: dict) -> bool:
    for field, expected in matchers.items():
        if extracted.get(field) != expected:
            return False
    return True


class ComplianceEngine:
    def __init__(self, rules_path: Path | str) -> None:
        path = Path(rules_path)
        raw = path.read_text(encoding="utf-8")
        self.rules = yaml.safe_load(raw).get("rules", [])

    @classmethod
    def from_ruleset(cls, ruleset: str) -> "ComplianceEngine":
        p = Path(ruleset)
        if p.is_file():
            return cls(p)
        candidate = Path.cwd() / ruleset
        if candidate.is_file():
            return cls(candidate)
        packaged = _rules_dir() / ruleset
        if packaged.is_file():
            return cls(packaged)
        rooted = Path(__file__).resolve().parents[2] / ruleset.replace("\\", "/")
        if rooted.is_file():
            return cls(rooted)
        raise FileNotFoundError(f"Ruleset not found: {ruleset!r}")

    def evaluate(self, doc_type: str, extracted: dict, context: dict | None = None) -> list[RuleViolation]:
        violations: list[RuleViolation] = []
        context = context or {}
        for rule in self.rules:
            if rule.get("when_doc_type") and rule["when_doc_type"] != doc_type:
                continue
            when_m = rule.get("when_matches") or {}
            if when_m and not _subset_matches(extracted, when_m):
                continue
            unless_m = rule.get("unless_matches") or {}
            if unless_m and _subset_matches(extracted, unless_m):
                continue
            required_key = rule.get("require_key")
            if required_key and not extracted.get(required_key):
                violations.append(
                    RuleViolation(
                        rule_id=rule["id"],
                        severity=rule.get("severity", "error"),
                        message=rule["description"],
                    )
                )
            ratio = rule.get("compare_numeric_ratio")
            if ratio:
                num_k = ratio.get("numerator_key")
                den_k = ratio.get("denominator_key")
                max_pct = float(ratio.get("max_relative_deviation_pct", 0.0))
                if num_k and den_k:
                    num = extracted.get(num_k)
                    den = extracted.get(den_k)
                    if num is None or den is None:
                        continue
                    if isinstance(num, str):
                        num = float(num)
                    if isinstance(den, str):
                        den = float(den)
                    if den == 0:
                        continue
                    rel_pct = abs((float(num) - float(den)) / float(den)) * 100.0
                    if rel_pct > max_pct + 1e-12:
                        violations.append(
                            RuleViolation(
                                rule_id=rule["id"],
                                severity=rule.get("severity", "error"),
                                message=rule.get("description", rule["id"]),
                            )
                        )
        return violations

    def check(self, doc: dict | None) -> list[str]:
        """
        Normalize a document envelope and return violated rule ids.
        Accepts ``{doc_type, extracted}`` or a flat mapping (``doc_type`` optional; defaults to ``spa``).
        """
        if not doc:
            doc = {}
        doc_type = doc.get("doc_type") or "spa"
        if isinstance(doc.get("extracted"), dict):
            extracted = dict(doc["extracted"])
        else:
            extracted = {k: v for k, v in doc.items() if k != "doc_type"}
        viols = self.evaluate(doc_type, extracted, {})
        return [v.rule_id for v in viols]
