"""Krippendorff's alpha, cluster bootstraps and Wilson intervals (grading note §G; ruling 3).

Alpha follows Krippendorff (2011): alpha = 1 - D_o / D_e over the coincidence
matrix, units with fewer than two values omitted. Bootstraps resample cluster ids
(question ids) with replacement under ``random.Random(seed)``; a resample whose
alpha is undefined counts at its worst case, so a gate that depends on it fails
closed.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

Unit = Sequence[float | None]  # one value per coder; None = missing
Metric = Literal["interval", "nominal"]


def _delta(c: float, k: float, metric: str) -> float:
    return (c - k) ** 2 if metric == "interval" else float(c != k)


def coincidence(units: Iterable[Unit]) -> dict[tuple[float, float], float]:
    o: dict[tuple[float, float], float] = defaultdict(float)
    for unit in units:
        values = [v for v in unit if v is not None]
        m = len(values)
        if m < 2:
            continue
        for i, c in enumerate(values):
            for j, k in enumerate(values):
                if i != j:
                    o[(c, k)] += 1 / (m - 1)
    return dict(o)


def alpha_from_coincidence(
    o: Mapping[tuple[float, float], float], *, metric: str = "interval"
) -> float | None:
    n_c: dict[float, float] = defaultdict(float)
    for (c, _k), count in o.items():
        n_c[c] += count
    n = sum(n_c.values())
    if n <= 1:
        return None
    d_o = sum(count * _delta(c, k, metric) for (c, k), count in o.items()) / n
    d_e = sum(n_c[c] * n_c[k] * _delta(c, k, metric) for c in n_c for k in n_c) / (n * (n - 1))
    if d_e <= 1e-12:
        return None
    return 1 - d_o / d_e


def krippendorff_alpha(units: Iterable[Unit], *, metric: Metric = "interval") -> float | None:
    return alpha_from_coincidence(coincidence(units), metric=metric)


@dataclass(frozen=True)
class AlphaResult:
    alpha: float | None
    low: float
    high: float
    q_below: float
    undefined_draws: int
    units: int
    clusters: int


def percentile_interval(values: Sequence[float]) -> tuple[float, float]:
    """``s[ceil(0.025 B) - 1]`` and ``s[floor(0.975 B)]``: indices 249 and 9750 for B = 10,000."""
    s = sorted(values)
    b = len(s)
    return s[-(-b * 25 // 1000) - 1], s[min(b - 1, b * 975 // 1000)]


def bootstrap_alpha(
    clusters: Mapping[str, Sequence[Unit]],
    *,
    seed: str,
    resamples: int = 10_000,
    minimum: float = 0.667,
    metric: str = "interval",
) -> AlphaResult:
    """Alpha with a question-cluster bootstrap; undefined draws sit at -inf (fail closed)."""
    ids = sorted(clusters)
    per_cluster = {cid: coincidence(clusters[cid]) for cid in ids}
    values = sorted({v for o in per_cluster.values() for pair in o for v in pair})
    pairs = [(c, k) for c in values for k in values]
    vectors = {cid: [per_cluster[cid].get(pair, 0.0) for pair in pairs] for cid in ids}

    def alpha_of(counts: Mapping[str, int]) -> float | None:
        total = [0.0] * len(pairs)
        for cid, times in counts.items():
            for index, value in enumerate(vectors[cid]):
                total[index] += times * value
        return alpha_from_coincidence(dict(zip(pairs, total, strict=True)), metric=metric)

    point = alpha_of(dict.fromkeys(ids, 1))
    rng = random.Random(seed)
    draws: list[float] = []
    undefined = 0
    for _ in range(resamples):
        value = alpha_of(Counter(rng.choices(ids, k=len(ids)))) if ids else None
        if value is None:
            undefined += 1
        draws.append(-math.inf if value is None else value)
    low, high = percentile_interval(draws) if draws else (-math.inf, -math.inf)
    units = sum(1 for cid in ids for unit in clusters[cid] if sum(v is not None for v in unit) > 1)
    return AlphaResult(
        alpha=point,
        low=low,
        high=high,
        q_below=sum(v < minimum for v in draws) / resamples if resamples else 1.0,
        undefined_draws=undefined,
        units=units,
        clusters=len(ids),
    )


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float


def paired_bootstrap(
    questions: Sequence[str],
    statistic: Callable[[Sequence[str]], float],
    *,
    seed: str,
    resamples: int = 10_000,
) -> Interval:
    """Resample question ids with replacement; ``statistic`` keeps every repeat of each."""
    point = statistic(questions)
    rng = random.Random(seed)
    draws = [statistic(rng.choices(questions, k=len(questions))) for _ in range(resamples)]
    low, high = percentile_interval(draws)
    return Interval(point, low, high)


def tokens_per_correct(tokens: Iterable[int], scores: Iterable[float]) -> float:
    """Sum of tokens over the sum of scores (design §8); infinite with no credit."""
    credit = sum(scores)
    return sum(tokens) / credit if credit > 0 else math.inf
