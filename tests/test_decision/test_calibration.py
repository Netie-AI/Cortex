import math
import random

import pytest

from CortexOS.decision.calibration import (
    automatable_share,
    brier,
    choice_confidence,
    ece,
    fit_temperature,
    softmax,
)


def test_choice_confidence_matches_kev_formula():
    # K=3, p_max=0.47: (0.47 - 1/3) / (1 - 1/3) = 0.205
    assert choice_confidence([0.47, 0.28, 0.25]) == pytest.approx(0.205, abs=1e-9)
    # uniform is zero confidence, one-hot is full confidence
    assert choice_confidence([0.25, 0.25, 0.25, 0.25]) == pytest.approx(0.0)
    assert choice_confidence([0.0, 1.0]) == pytest.approx(1.0)
    # K=1 is trivially certain
    assert choice_confidence([1.0]) == 1.0


def test_softmax_temperature_flattens():
    sharp = softmax([2.0, 0.0], 0.5)
    flat = softmax([2.0, 0.0], 4.0)
    assert sum(sharp) == pytest.approx(1.0)
    assert sharp[0] > flat[0] > 0.5
    with pytest.raises(ValueError):
        softmax([1.0], 0.0)


def test_fit_temperature_recovers_known_temperature():
    rng = random.Random(7)
    true_t = 2.5
    rows, labels = [], []
    for _ in range(3000):
        logits = [rng.gauss(0, 3) for _ in range(4)]
        probs = softmax(logits, true_t)
        r, acc, y = rng.random(), 0.0, 0
        for k, p in enumerate(probs):
            acc += p
            if r <= acc:
                y = k
                break
        rows.append(logits)
        labels.append(y)
    fitted = fit_temperature(rows, labels)
    assert abs(math.log(fitted) - math.log(true_t)) < 0.15


def test_brier_hand_computed():
    # sample 1: [0.8, 0.2] label 0 -> 0.04 + 0.04 = 0.08
    # sample 2: [0.3, 0.7] label 0 -> 0.49 + 0.49 = 0.98
    assert brier([[0.8, 0.2], [0.3, 0.7]], [0, 0]) == pytest.approx(0.53)


def test_ece_hand_computed():
    # two bins used: (0.9,0.9 correct, correct) -> |1.0 - 0.9| = 0.1 with weight 0.5
    # (0.6 wrong, 0.6 correct) -> |0.5 - 0.6| = 0.1 with weight 0.5 => ECE 0.1
    rows = [[0.9, 0.1], [0.9, 0.1], [0.6, 0.4], [0.6, 0.4]]
    labels = [0, 0, 1, 0]
    assert ece(rows, labels, n_bins=10) == pytest.approx(0.1)
    assert ece([[1.0, 0.0]], [0]) == pytest.approx(0.0)


def test_automatable_share_respects_error_budget():
    # confidences: 1.0 (ok), 0.8 (ok), 0.6 (wrong), 0.4 (ok)
    rows = [[1.0, 0.0], [0.9, 0.1], [0.2, 0.8], [0.7, 0.3]]
    labels = [0, 0, 0, 0]
    # 5% budget: only the first two (0 errors) qualify
    assert automatable_share(rows, labels, error_budget=0.05) == pytest.approx(0.5)
    # 50% budget: all four (1/4 error) qualify
    assert automatable_share(rows, labels, error_budget=0.5) == pytest.approx(1.0)
    assert automatable_share([], []) == 0.0


def test_automatable_share_never_cuts_inside_a_tie_group():
    """A threshold cannot split tied confidences, so the share is the same
    whatever order the tied rows arrive in (coordinator fix on #247)."""
    rows = [[0.1, 0.9]] * 4
    good_first = automatable_share(rows, [1, 1, 1, 0], error_budget=0.0)
    bad_first = automatable_share(rows, [0, 1, 1, 1], error_budget=0.0)
    assert good_first == bad_first == 0.0
