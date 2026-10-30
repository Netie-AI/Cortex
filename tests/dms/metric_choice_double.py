"""Test double for the metric-choice port.

Production calls OpenVault. This double agrees only when the question is one
declared wording, so controls that named that wording still get governed SQL.
It is not the serve path.
"""

from __future__ import annotations

from CortexOS.dms.metric_choice import UNARMED_CLOSEST, MetricSelection, MetricVerdict


class ExactDeclaredChoice:
    def choose(self, question: str) -> MetricSelection:
        from CortexOS.dms.answer_engine import (
            _exact_declared_plan,
            cascade_match_is_non_exact,
        )

        plan = _exact_declared_plan(question)
        if plan is not None:
            return MetricSelection(
                metric_id=plan.metric_id,
                slots=dict(plan.slots),
                closest_question=UNARMED_CLOSEST,
                why=plan.reason,
            )
        # A cascade neighbour is not a document question. The customer path
        # does not consult the cascade; this double only keeps the suite from
        # letting keyword documents stand in for that neighbour.
        if cascade_match_is_non_exact(question):
            return MetricSelection(
                closest_question=UNARMED_CLOSEST,
                why="the question is not that metric's declared question",
                allow_documents=False,
            )
        return MetricSelection(
            closest_question=UNARMED_CLOSEST,
            why="no declared metric was chosen, so it cannot be served directly",
        )

    def check(self, question: str, selection: MetricSelection) -> MetricVerdict:
        del question, selection
        return MetricVerdict(agrees=True, unsure=False, why="")
