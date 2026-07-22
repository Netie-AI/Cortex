"""Layered PII detection: fast regex first, optional local NER, optional cloud.

Extends the regex choke-point in ``pii.py`` (which stays the audited default)
with entity types regex cannot see — names, addresses, org/person references —
via a **pluggable, degrade-gracefully** stack:

    regex (always on, offline)
      + local NER      (Microsoft Presidio analyzer / spaCy) — opt-in, offline
      + cloud fallback (AWS Comprehend)                       — opt-in, egress!

Everything returns ``pii.PiiSpan`` so ``token_vault`` and ``redact_for_prompt``
consume one shape. If a layer's dependency is absent it silently contributes
nothing — the regex floor guarantees the gate never fails open to *less* than
today. Cloud fallback is **off by default** because it egresses text.
"""

from __future__ import annotations

from dataclasses import dataclass

from packs.dms.security.pii import PiiSpan, detect as regex_detect


def _merge_nonoverlapping(spans: list[PiiSpan]) -> list[PiiSpan]:
    """Same non-overlap policy as pii.detect: earliest start, longest wins."""
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    merged: list[PiiSpan] = []
    cursor = -1
    for span in spans:
        if span.start >= cursor:
            merged.append(span)
            cursor = span.end
    return merged


class PresidioDetector:
    """Local Microsoft Presidio analyzer. No-op if presidio_analyzer absent."""

    def __init__(self, language: str = "en") -> None:
        self.language = language
        self._analyzer = None
        try:  # pragma: no cover - depends on optional heavy dep
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
        except Exception:
            self._analyzer = None

    @property
    def available(self) -> bool:
        return self._analyzer is not None

    def detect(self, text: str) -> list[PiiSpan]:
        if self._analyzer is None:
            return []
        try:  # pragma: no cover
            results = self._analyzer.analyze(text=text, language=self.language)
        except Exception:
            return []
        return [
            PiiSpan(start=r.start, end=r.end, kind=r.entity_type.lower(), text=text[r.start:r.end])
            for r in results
        ]


class ComprehendDetector:
    """AWS Comprehend PII fallback. Egresses text — opt-in only, no-op without boto3."""

    def __init__(self, language: str = "en", region: str | None = None) -> None:
        self.language = language
        self._client = None
        try:  # pragma: no cover - needs boto3 + creds
            import boto3

            self._client = boto3.client("comprehend", region_name=region) if region else boto3.client("comprehend")
        except Exception:
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def detect(self, text: str) -> list[PiiSpan]:
        if self._client is None:
            return []
        try:  # pragma: no cover
            resp = self._client.detect_pii_entities(Text=text, LanguageCode=self.language)
        except Exception:
            return []
        return [
            PiiSpan(start=e["BeginOffset"], end=e["EndOffset"], kind=e["Type"].lower(),
                    text=text[e["BeginOffset"]:e["EndOffset"]])
            for e in resp.get("Entities", [])
        ]


@dataclass(slots=True)
class LayeredDetector:
    """Regex floor + optional NER + optional cloud, merged non-overlapping."""

    use_ner: bool = False
    use_cloud: bool = False
    _presidio: PresidioDetector | None = None
    _comprehend: ComprehendDetector | None = None

    def __post_init__(self) -> None:
        if self.use_ner:
            self._presidio = PresidioDetector()
        if self.use_cloud:
            self._comprehend = ComprehendDetector()

    @property
    def layers(self) -> tuple[str, ...]:
        active = ["regex"]
        if self._presidio and self._presidio.available:
            active.append("presidio")
        if self._comprehend and self._comprehend.available:
            active.append("comprehend")
        return tuple(active)

    def detect(self, text: str) -> list[PiiSpan]:
        spans: list[PiiSpan] = list(regex_detect(text))
        if self._presidio is not None:
            spans.extend(self._presidio.detect(text))
        if self._comprehend is not None:
            spans.extend(self._comprehend.detect(text))
        return _merge_nonoverlapping(spans)


def default_detector(*, use_ner: bool = False, use_cloud: bool = False) -> LayeredDetector:
    """Regex-only by default (offline, always works). Opt into NER/cloud explicitly."""
    return LayeredDetector(use_ner=use_ner, use_cloud=use_cloud)
