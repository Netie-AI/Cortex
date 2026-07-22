from dataclasses import dataclass


@dataclass(slots=True)
class EvalSummary:
    sentiment_f1_macro: float
    intent_f1_macro: float
    language_fidelity: float
    avg_cost_myr: float


def gate(summary: EvalSummary) -> bool:
    return (
        summary.sentiment_f1_macro >= 0.78
        and summary.intent_f1_macro >= 0.72
        and summary.avg_cost_myr <= 0.50
        and summary.language_fidelity >= 0.70
    )
