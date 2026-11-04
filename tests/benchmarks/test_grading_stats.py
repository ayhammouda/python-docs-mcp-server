"""Krippendorff's alpha, cluster bootstraps and Wilson intervals (PR 5 Task 7; grading note §G)."""

from __future__ import annotations

import random

from benchmarks.grading.gates import g2
from benchmarks.grading.stats import (
    alpha_from_coincidence,
    bootstrap_alpha,
    coincidence,
    krippendorff_alpha,
    paired_bootstrap,
    percentile_interval,
    tokens_per_correct,
    wilson,
)

KRIPPENDORFF_2011 = [
    [1, 1, None, 1], [2, 2, 3, 2], [3, 3, 3, 3], [3, 3, 3, 3], [2, 2, 2, 2], [1, 2, 3, 4],
    [4, 4, 4, 4], [1, 1, 2, 1], [2, 2, 2, 2], [None, 5, 5, 5], [None, None, 1, 1],
    [None, 3, None, None],
]  # fmt: skip


def _as_grades(clusters: dict[str, list[list[float]]]) -> dict[str, dict[str, float | None]]:
    return {
        f"{qid}#{n}": {f"j{k}": value for k, value in enumerate(unit)}
        for qid, units in clusters.items()
        for n, unit in enumerate(units)
    }


def _question_of(clusters: dict[str, list[list[float]]]) -> dict[str, str]:
    return {f"{qid}#{n}": qid for qid, units in clusters.items() for n in range(len(units))}


def test_alpha_matches_the_krippendorff_2011_example() -> None:
    interval = krippendorff_alpha(KRIPPENDORFF_2011, metric="interval")
    nominal = krippendorff_alpha(KRIPPENDORFF_2011, metric="nominal")
    assert interval is not None and nominal is not None
    assert round(interval, 3) == 0.849 and round(nominal, 3) == 0.743
    assert alpha_from_coincidence(coincidence(KRIPPENDORFF_2011)) == interval


def test_alpha_is_none_without_variation_or_pairable_units() -> None:
    assert krippendorff_alpha([[1.0, 1.0], [1.0, 1.0, None]]) is None
    assert krippendorff_alpha([[1.0, None], [None, 0.5]]) is None
    assert krippendorff_alpha([]) is None
    assert krippendorff_alpha([[1.0, 0.0], [0.0, 1.0]]) == -0.5


def test_bootstrap_alpha_is_deterministic_and_reports_q() -> None:
    rng = random.Random(1)
    clusters = {
        f"Q{i}": [[s, s if rng.random() < 0.8 else 1.0 - s, s] for s in (rng.choice((0.0, 1.0)),)]
        for i in range(40)
    }
    first = bootstrap_alpha(clusters, seed="s", resamples=2000)
    assert first == bootstrap_alpha(clusters, seed="s", resamples=2000)
    assert first.clusters == 40 and first.units == 40 and first.undefined_draws == 0
    assert first.alpha is not None and first.low <= first.alpha <= first.high
    assert 0.0 <= first.q_below <= 1.0
    assert bootstrap_alpha(clusters, seed="t", resamples=2000) != first


def test_undefined_alpha_draws_fail_closed() -> None:
    clusters = {f"Q{i}": [[1.0, 1.0, 1.0]] for i in range(49)} | {"Q49": [[0.0, 0.0, 0.0]]}
    r = bootstrap_alpha(clusters, seed="7")
    assert r.alpha == 1.0 and 0.30 < r.undefined_draws / 10_000 < 0.43  # resamples missing Q49
    assert r.low == float("-inf") and r.q_below >= 0.30
    assert g2(_as_grades(clusters), _question_of(clusters), seed="7").passed is False


def test_percentile_interval_uses_indices_249_and_9750() -> None:
    values = [float(v) for v in range(10_000)]
    random.Random(3).shuffle(values)
    assert percentile_interval(values) == (249.0, 9750.0)
    assert percentile_interval([float(v) for v in range(1000)]) == (24.0, 975.0)


def test_wilson_matches_known_bounds() -> None:
    assert [round(x, 4) for x in wilson(47, 50)] == [0.8378, 0.9794]
    assert round(wilson(49, 50)[0], 4) == 0.895
    assert wilson(0, 0) == (0.0, 1.0)


def test_paired_bootstrap_resamples_questions_with_their_repeats() -> None:
    per_question = {"Q1": [1.0, 1.0], "Q2": [0.0, 0.0], "Q3": [1.0, 0.0]}

    def mean(questions: list[str] | tuple[str, ...]) -> float:
        values = [v for q in questions for v in per_question[q]]
        return sum(values) / len(values)

    seen: list[int] = []

    def counting(questions: list[str] | tuple[str, ...]) -> float:
        seen.append(len(questions))
        return mean(questions)

    interval = paired_bootstrap(sorted(per_question), counting, seed="x", resamples=500)
    assert interval.point == 0.5 and interval.low <= 0.5 <= interval.high
    assert set(seen) == {3} and len(seen) == 501  # the point, then one call per resample
    assert interval == paired_bootstrap(sorted(per_question), mean, seed="x", resamples=500)


def test_tokens_per_correct_counts_half_credit_and_failures() -> None:
    assert tokens_per_correct([100, 300, 200], [1.0, 0.5, 0.0]) == 400.0
    assert tokens_per_correct([100], [0.0]) == float("inf")
