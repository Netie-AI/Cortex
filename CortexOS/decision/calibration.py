"""Calibration and evaluation metrics for decision backends (stdlib only).

Definitions
- ``softmax(logits, temperature)``: temperature-scaled softmax.
- ``fit_temperature``: 1-D golden-section search on log T minimising NLL of the
  scaled softmax against integer labels (dev-set temperature scaling).
- ``brier``: mean over samples of sum_k (p_k - y_k)^2 (multiclass Brier).
- ``ece``: expected calibration error on max-probability bins.
- ``automatable_share``: largest fraction of decisions, taken in descending
  confidence, whose error rate stays within ``error_budget``.
- ``choice_confidence``: kev's (p_max - 1/K) / (1 - 1/K), 1.0 when K == 1.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Probs = Sequence[float]


def softmax(logits: Probs, temperature: float = 1.0) -> list[float]:
    if temperature <= 0:
        raise ValueError("temperature must be > 0")
    scaled = [x / temperature for x in logits]
    m = max(scaled)
    exps = [math.exp(x - m) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def nll(logits_rows: Sequence[Probs], labels: Sequence[int], temperature: float) -> float:
    total = 0.0
    for row, y in zip(logits_rows, labels, strict=True):
        p = softmax(row, temperature)[y]
        total -= math.log(max(p, 1e-12))
    return total / max(len(labels), 1)


def fit_temperature(
    logits_rows: Sequence[Probs],
    labels: Sequence[int],
    *,
    lo: float = 0.05,
    hi: float = 20.0,
    iters: int = 60,
) -> float:
    """Return the temperature minimising NLL on ``(logits_rows, labels)``."""
    if not logits_rows:
        raise ValueError("fit_temperature needs at least one sample")
    if len(logits_rows) != len(labels):
        raise ValueError("logits_rows and labels must have the same length")
    a, b = math.log(lo), math.log(hi)
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - phi * (b - a)
    d = a + phi * (b - a)
    fc = nll(logits_rows, labels, math.exp(c))
    fd = nll(logits_rows, labels, math.exp(d))
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = nll(logits_rows, labels, math.exp(c))
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = nll(logits_rows, labels, math.exp(d))
    return math.exp((a + b) / 2.0)


def brier(prob_rows: Sequence[Probs], labels: Sequence[int]) -> float:
    if not prob_rows:
        raise ValueError("brier needs at least one sample")
    total = 0.0
    for row, y in zip(prob_rows, labels, strict=True):
        total += sum((p - (1.0 if k == y else 0.0)) ** 2 for k, p in enumerate(row))
    return total / len(prob_rows)


def ece(prob_rows: Sequence[Probs], labels: Sequence[int], n_bins: int = 10) -> float:
    if not prob_rows:
        raise ValueError("ece needs at least one sample")
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    conf_sum = [0.0] * n_bins
    acc_sum = [0.0] * n_bins
    count = [0] * n_bins
    for row, y in zip(prob_rows, labels, strict=True):
        pred = max(range(len(row)), key=lambda k: row[k])
        conf = row[pred]
        idx = min(int(conf * n_bins), n_bins - 1)
        conf_sum[idx] += conf
        acc_sum[idx] += 1.0 if pred == y else 0.0
        count[idx] += 1
    n = len(prob_rows)
    total = 0.0
    for i in range(n_bins):
        if count[i]:
            total += (count[i] / n) * abs(acc_sum[i] / count[i] - conf_sum[i] / count[i])
    return total


def choice_confidence(probs: Probs) -> float:
    k = len(probs)
    if k == 0:
        raise ValueError("choice_confidence needs at least one probability")
    if k == 1:
        return 1.0
    p_max = max(probs)
    return max(0.0, min(1.0, (p_max - 1.0 / k) / (1.0 - 1.0 / k)))


def automatable_share(
    prob_rows: Sequence[Probs], labels: Sequence[int], error_budget: float = 0.05
) -> float:
    """Share of decisions that can be automated within ``error_budget``.

    Decisions are ordered by descending confidence; the returned value is the
    size of the largest prefix whose empirical error rate is <= the budget,
    divided by the total count.
    """
    if not prob_rows:
        return 0.0
    if not 0.0 <= error_budget <= 1.0:
        raise ValueError("error_budget must be in [0, 1]")
    scored: list[tuple[float, bool]] = []
    for row, y in zip(prob_rows, labels, strict=True):
        pred = max(range(len(row)), key=lambda k: row[k])
        scored.append((choice_confidence(row), pred == y))
    scored.sort(key=lambda t: t[0], reverse=True)
    best = 0
    errors = 0
    for i, (_, correct) in enumerate(scored, start=1):
        if not correct:
            errors += 1
        if errors / i <= error_budget:
            best = i
    return best / len(scored)
