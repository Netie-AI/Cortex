"""Calibrated decision port (noul / choice / score), shaped like kev's systemone API.

Opt-in only: ``JudgmentModel(decision_backend=...)`` or ``CORTEX_KEV_URL``.
"""

from .backends import DecisionBackend, KevHttpBackend, RulesBackend, is_loopback_url, tier_question
from .calibration import (
    automatable_share,
    brier,
    choice_confidence,
    ece,
    fit_temperature,
    softmax,
)
from .decide import decide
from .models import (
    ChoiceQuestion,
    DecisionAnswer,
    NoulQuestion,
    Question,
    RawDecision,
    ScoreQuestion,
)

__all__ = [
    "ChoiceQuestion",
    "DecisionAnswer",
    "DecisionBackend",
    "KevHttpBackend",
    "NoulQuestion",
    "Question",
    "RawDecision",
    "RulesBackend",
    "ScoreQuestion",
    "automatable_share",
    "brier",
    "choice_confidence",
    "decide",
    "ece",
    "fit_temperature",
    "is_loopback_url",
    "softmax",
    "tier_question",
]
