"""Eval-set questions are not metric synonyms.

A synonym copied from the golden set, the paraphrase set, the adversarial
set, or the corpus seeds teaches the cascade the questions it is graded on.
This fails when any of those questions appears verbatim in metrics.yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
METRICS = ROOT / "packs" / "dms" / "semantic" / "metrics.yaml"
GOLDEN = ROOT / "bench" / "golden" / "dms_golden_v1.yaml"
PARAPHRASE = ROOT / "bench" / "golden" / "dms_paraphrase_v1.yaml"
ADVERSARIAL = ROOT / "bench" / "golden" / "dms_adversarial_v1.yaml"
SEEDS = ROOT / "bench" / "corpus" / "seeds_v1.yaml"


def _fold(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _eval_questions() -> set[str]:
    questions: set[str] = set()
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    for item in golden.get("items") or []:
        questions.add(_fold(item["question"]))
    paraphrases = yaml.safe_load(PARAPHRASE.read_text(encoding="utf-8"))
    for group in (paraphrases.get("paraphrases") or {}).values():
        for question in group or []:
            questions.add(_fold(question))
    adversarial = yaml.safe_load(ADVERSARIAL.read_text(encoding="utf-8"))
    for group in (adversarial.get("categories") or {}).values():
        for item in group or []:
            questions.add(_fold(item["question"]))
    seeds = yaml.safe_load(SEEDS.read_text(encoding="utf-8"))
    for group in (seeds.get("categories") or {}).values():
        for item in group or []:
            questions.add(_fold(item["question"]))
    return questions


def _verbatim_hits(metrics_path: Path | None = None) -> list[str]:
    data = yaml.safe_load((metrics_path or METRICS).read_text(encoding="utf-8"))
    banned = _eval_questions()
    hits: list[str] = []
    for metric in data.get("metrics") or []:
        for phrase in metric.get("synonyms") or []:
            if _fold(phrase) in banned:
                hits.append(str(phrase))
    return sorted(hits)


def test_no_eval_question_is_a_verbatim_synonym():
    assert _eval_questions(), "eval questions did not load"
    assert _verbatim_hits() == []


def test_eval_question_planted_as_a_synonym_is_a_hit(tmp_path: Path):
    """The check can fail: a planted eval question is a hit."""
    planted = tmp_path / "metrics.yaml"
    planted.write_text(
        "metrics:\n"
        "  - id: planted\n"
        '    synonyms: ["How many shipments are delayed?"]\n',
        encoding="utf-8",
    )
    assert _verbatim_hits(planted) == ["How many shipments are delayed?"]
