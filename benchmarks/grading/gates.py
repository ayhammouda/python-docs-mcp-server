"""G1–G4, the outcome tier, the G3 sample, and the C2/C3 claim verdicts (design §§2, 9.3).

Every gate fails closed: missing evidence, a shortfall or an undefined alpha make it
not evaluable (``passed is None``) or failed, never passed. C1/C3 evidence comes
from PR 4's ``egress/summary.json`` and ``airgap/checks.json`` (ruling 9), read
only through the two loaders at the end of this module.
"""

from __future__ import annotations

import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard

from benchmarks.agentic.inputs import json_mapping, read_json
from benchmarks.grading.aggregate import AnswerVotes, reference_needed
from benchmarks.grading.inputs import GradingQuestion, safe_id
from benchmarks.grading.panel import Panel, composition_key
from benchmarks.grading.probes import (
    HELD_OUT_QUESTIONS,
    PROBE_TYPES,
    REFERENCE_PER_TYPE,
    Probe,
    validate_slots,
)
from benchmarks.grading.stats import (
    AlphaResult,
    Interval,
    bootstrap_alpha,
    paired_bootstrap,
    percentile_interval,
    tokens_per_correct,
    wilson,
)
from benchmarks.report_files import artifact_bytes, artifact_paths, optional_json
from benchmarks.runner import BenchmarkValidationError

CellKey = tuple[str, str, str, int]  # (system_id, model_family_id, corpus_id, repeat)
Tier = Literal["Validated", "Tentative", "Judge not validated"]
BINDING_INPUTS = ("judge_prompt", "judge_schema", "criteria", "mapping_table", "mask_rules",
                  "probes")  # fmt: skip
OLLAMA_HOST = "host.docker.internal:11434"
G1_OVERALL, G1_PER_TYPE, G1_PER_JUDGE, REFERENCE_PROBES = 0.95, 0.90, 0.90, 0.95
ALPHA_PASS, ALPHA_MINIMUM = 0.80, 0.667


@dataclass(frozen=True)
class GateResult:
    id: str
    passed: bool | None  # None = not evaluable
    details: dict[str, Any]


@dataclass(frozen=True)
class ClaimResult:
    id: str
    model: str
    passed: bool | None
    interval: Interval | None
    reason: str | None


@dataclass(frozen=True)
class G1Binding:
    composition: dict[str, dict[str, str | None]]  # judge id -> {"model_id", "effort"}
    inputs: dict[str, str | None]  # BINDING_INPUTS -> sha256 (absent in some legacy runs)

    def missing_field(self) -> str | None:
        for judge, spec in self.composition.items():
            for field in ("model_id", "effort"):
                value = spec.get(field)
                if not isinstance(value, str) or not value.strip():
                    return f"composition.{judge}.{field}"
        for field in BINDING_INPUTS:
            if not self.inputs.get(field):
                return f"inputs.{field}"
        return None


@dataclass(frozen=True)
class OfflineEvidence:
    all_passed: bool
    checks: int
    internet_hosts: list[str]


def _accuracy(hits: int, n: int) -> dict[str, Any]:
    return {
        "accuracy": hits / n if n else 0.0,
        "n": n,
        "hits": hits,
        "wilson": list(wilson(hits, n)),
    }


def _hit(probe: Probe, score: float | None) -> bool:
    return score is not None and score == probe.expected_score  # ruling 2: scores, not labels


def _inj_credit(probes: Sequence[Probe], scores: Mapping[str, float | None]) -> int:
    return sum(1 for p in probes if p.type == "INJ" and (scores.get(p.id) or 0.0) > 0)


def held_out_shortfall(probes: Sequence[Probe], dropped: Sequence[Mapping[str, Any]]) -> str | None:
    """Account for every slot in the design, including individually permitted drops."""
    try:
        validate_slots(probes, dropped)
    except BenchmarkValidationError as exc:
        return str(exc)
    if any(p.set != "held_out" for p in probes) or any(d.get("set") != "held_out" for d in dropped):
        return "G1 requires held-out probes only"
    questions = {p.question_id for p in probes} | {d["question_id"] for d in dropped}
    planned = HELD_OUT_QUESTIONS * len(PROBE_TYPES)
    if len(questions) != HELD_OUT_QUESTIONS or len(probes) + len(dropped) != planned:
        return (
            f"G1 accounts for {len(probes) + len(dropped)} of {planned} held-out slots "
            f"on {len(questions)} questions"
        )
    if missing := set(PROBE_TYPES) - {p.type for p in probes}:
        return f"G1 has no retained held-out probes for {sorted(missing)}"
    return None


def g1(
    probes: Sequence[Probe],
    panel_scores: Mapping[str, float | None],
    judge_scores: Mapping[str, Mapping[str, float | None]],
    *,
    dropped: Sequence[Mapping[str, Any]] = (),
) -> GateResult:
    """Design §9.3 G1 on held-out probes; a probe with no panel score is a miss."""
    hits = [p for p in probes if _hit(p, panel_scores.get(p.id))]
    by_type = {
        t: _accuracy(sum(1 for p in hits if p.type == t), sum(1 for p in probes if p.type == t))
        for t in PROBE_TYPES
    }
    by_judge = {
        judge: {
            **_accuracy(sum(1 for p in probes if _hit(p, scores.get(p.id))), len(probes)),
            "inj_credit": _inj_credit(probes, scores),
        }
        for judge, scores in judge_scores.items()
    }
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for p in probes:
        score = panel_scores.get(p.id)
        confusion[p.expected_label][str(score) if score is not None else "unresolved"] += 1
    misses = Counter(str(p.built_by.get("generator", "code")) for p in probes if p not in hits)
    overall = _accuracy(len(hits), len(probes))
    inj = _inj_credit(probes, panel_scores)
    reason = held_out_shortfall(probes, dropped)
    passed = (
        bool(judge_scores)
        and overall["accuracy"] >= G1_OVERALL
        and all(t["accuracy"] >= G1_PER_TYPE for t in by_type.values())
        and all(j["accuracy"] >= G1_PER_JUDGE and j["inj_credit"] == 0 for j in by_judge.values())
        and inj == 0
    )
    return GateResult(
        "G1",
        None if reason else passed,
        {
            **({"reason": reason} if reason else {}),
            "overall": overall,
            "by_type": by_type,
            "by_judge": by_judge,
            "inj_credit": inj,
            "confusion": {label: dict(c) for label, c in sorted(confusion.items())},
            "misses_by_generator": dict(misses),
        },
    )


def reference_probe_check(
    probes: Sequence[Probe], scores: Mapping[str, float | None]
) -> GateResult:
    """The reference grader's precondition for G3: >= 0.95 on its subset, zero INJ credit."""
    overall = _accuracy(sum(1 for p in probes if _hit(p, scores.get(p.id))), len(probes))
    inj = _inj_credit(probes, scores)
    details = {"overall": overall, "inj_credit": inj, "scores": dict(scores)}
    try:
        validate_slots(probes)
    except BenchmarkValidationError as exc:
        return GateResult("reference_probe_check", None, {**details, "reason": str(exc)})
    required = dict.fromkeys(PROBE_TYPES, REFERENCE_PER_TYPE)
    if (
        any(p.set != "held_out" for p in probes)
        or Counter(p.type for p in probes) != required
        or set(scores) != {p.id for p in probes}
        or any(s is None for s in scores.values())
    ):
        return GateResult(
            "reference_probe_check",
            None,
            {
                **details,
                "reason": f"reference needs {REFERENCE_PER_TYPE} distinct held-out scores per type",
            },
        )
    return GateResult(
        "reference_probe_check", overall["accuracy"] >= REFERENCE_PROBES and inj == 0, details
    )


def pass1_composition(
    root: Path, target: str, panel: Panel | None = None,
    *, runs: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, str | None]]:
    """The composition (judge -> model id and effort) of every pass-1 run of ``target`` under
    ``root/runs``; runs that disagree are refused."""
    compositions = []
    if runs is None:
        runs = {
            path.stem: json_mapping(artifact_bytes(root, path), "grading invocation")
            for path in artifact_paths(root, root / "runs", "*.json")
        }
    for run in runs.values():
        if run.get("target") == target and run.get("pass") == 1:
            if not isinstance(run.get("composition"), dict) or not run["composition"]:
                raise BenchmarkValidationError(f"{root}: pass-1 {target} run has no composition")
            compositions.append(run["composition"])
    return checked_composition(compositions, panel, where=f"{root}: pass-1 {target}")


def checked_composition(
    compositions: Sequence[dict[str, Any]], panel: Panel | None, *, where: str
) -> dict[str, dict[str, str | None]]:
    """Validate captured invocations with the producer's registered seat rules."""
    if not compositions:
        raise BenchmarkValidationError(f"{where} has no grading run")
    if any(c != compositions[0] for c in compositions):
        raise BenchmarkValidationError(
            f"{where} runs disagree on the panel composition or its efforts"
        )
    composition = compositions[0]
    if panel is None:
        return composition
    # The records pin the actual model and effort. The panel supplies provider order
    # and the registered alternative in each seat.
    judges = [j if j in composition else panel.fallback.get(j) for j in panel.panel]
    if set(composition) != set(judges):
        raise BenchmarkValidationError(f"{where}: unregistered pass-1 composition")
    for judge, spec in composition.items():
        if not isinstance(spec, dict):
            raise BenchmarkValidationError(f"invalid composition.{judge}")
        if spec.get("model_id") != panel.judges[judge].model_id:
            raise BenchmarkValidationError(f"unregistered composition.{judge}.model_id")
        if not spec.get("effort") or spec["effort"] != panel.judges[judge].effort:
            raise BenchmarkValidationError(f"unregistered composition.{judge}.effort")
    return {j: composition[j] for j in judges if j is not None}


def run_binding(
    run_dir: Path, panel: Panel | None = None, *, inputs: Mapping[str, str] | None = None,
    runs: Mapping[str, Mapping[str, Any]] | None = None,
) -> G1Binding:
    """The run's frozen inputs and the composition (with efforts) that graded pass 1."""
    grading = run_dir / "grading"
    if inputs is None:
        inputs = read_json(grading / "inputs" / "inputs.json", "grading input manifest")
    return G1Binding(
        composition=pass1_composition(grading, "answers", panel, runs=runs),
        inputs={name: inputs.get(name) for name in BINDING_INPUTS},
    )


def select_g1(g1_entries: Mapping[str, Mapping[str, Any]], binding: G1Binding) -> GateResult:
    """The run's composition entry; any binding mismatch is refused, naming the field."""
    key = composition_key(list(binding.composition))
    entry = g1_entries.get(key)
    if entry is None:
        return GateResult(
            "G1", None, {"composition": key, "reason": f"no G1 entry for composition {key}"}
        )
    bound = entry.get("binding")
    if not isinstance(bound, dict):
        raise BenchmarkValidationError(f"G1 entry {key} needs a binding")
    composition = bound.get("composition")
    if not isinstance(composition, dict) or composition.keys() != binding.composition.keys():
        raise BenchmarkValidationError(f"G1 entry {key} differs from the run at composition")
    for judge_id, spec in binding.composition.items():
        theirs = composition[judge_id]
        if not isinstance(theirs, dict):
            raise BenchmarkValidationError(f"G1 entry {key} differs at composition.{judge_id}")
        for field in ("model_id", "effort"):
            if spec.get(field) and theirs.get(field) != spec[field]:
                raise BenchmarkValidationError(
                    f"G1 entry {key} differs at composition.{judge_id}.{field}"
                )
    inputs = bound.get("inputs")
    if not isinstance(inputs, dict):
        raise BenchmarkValidationError(f"G1 entry {key} needs binding inputs")
    for field in BINDING_INPUTS:
        expected = binding.inputs.get(field)
        if not expected:
            continue  # A legacy run cannot acquire a pin from the supplied evidence.
        if inputs.get(field) != expected:
            raise BenchmarkValidationError(f"G1 entry {key} differs from the run at inputs.{field}")
        if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
            raise BenchmarkValidationError(
                f"G1 entry {key} has an invalid SHA-256 at inputs.{field}"
            )
    missing = binding.missing_field()
    if missing:
        return GateResult(
            "G1", None, {"composition": key, "reason": f"run binding lacks {missing}"}
        )
    gate = entry.get("gate")
    if (
        not isinstance(gate, dict)
        or "passed" not in gate
        or not isinstance(gate.get("details"), dict)
    ):
        raise BenchmarkValidationError(f"G1 entry {key} needs a gate verdict and details")
    passed = gate["passed"]
    if passed is not None and not isinstance(passed, bool):
        raise BenchmarkValidationError(f"G1 entry {key} has an invalid gate verdict")
    return GateResult("G1", passed, {**gate["details"], "composition": key})


def _clusters(
    grades: Mapping[str, Mapping[str, float | None]], question_of: Mapping[str, str]
) -> dict[str, list[list[float | None]]]:
    clusters: dict[str, list[list[float | None]]] = defaultdict(list)
    for item in sorted(grades):
        values = grades[item]
        clusters[question_of[item]].append([values[k] for k in sorted(values)])
    return dict(clusters)


def _alpha_details(result: AlphaResult) -> dict[str, Any]:
    return asdict(result)


def g2(
    grades: Mapping[str, Mapping[str, float | None]],
    question_of: Mapping[str, str],
    *,
    seed: str,
    resamples: int = 10_000,
) -> GateResult:
    """Panel reliability: alpha >= 0.80 and P(alpha < 0.667) <= 0.05."""
    result = bootstrap_alpha(_clusters(grades, question_of), seed=f"{seed}/G2", resamples=resamples)
    passed = result.alpha is not None and result.alpha >= ALPHA_PASS and result.q_below <= 0.05
    return GateResult("G2", passed, _alpha_details(result))


def g3_shortfall_sentence(n: int, size: int, info: Mapping[str, Any]) -> str:
    groups = ", ".join(
        f"{group} ({counts['taken']} of {counts['allocated']})"
        for group, counts in sorted((info.get("groups") or {}).items())
        if group in (info.get("shortfall") or {})
    )
    reason = f"G3 was not evaluated: the reference sample holds {n} of {size} answers"
    if groups:
        reason += f", because {groups} have too few eligible answers"
    return reason + "."


def g3(
    sample_ids: Sequence[str],
    pairs: Mapping[str, tuple[float, float]],
    question_of: Mapping[str, str],
    *,
    seed: str,
    reference_check: GateResult,
    size: int = 300,
    sample_info: Mapping[str, Any] | None = None,
    resamples: int = 10_000,
) -> GateResult:
    """Reference agreement on the full sample: alpha >= 0.80 and its lower bound >= 0.667."""
    distinct = len(set(sample_ids))
    if distinct != len(sample_ids):
        reason = f"G3 was not evaluated: the sample contains only {distinct} distinct answers."
        return GateResult("G3", None, {"reason": reason, "sample_size": distinct})
    if len(sample_ids) != size:
        reason = (
            g3_shortfall_sentence(len(sample_ids), size, sample_info)
            if sample_info is not None
            else f"G3 was not evaluated: the reference sample holds {len(sample_ids)} of {size}"
            " answers."
        )
        return GateResult("G3", None, {"reason": reason, "sample_size": len(sample_ids)})
    if reference_check.passed is not True:
        return GateResult(
            "G3",
            None,
            {"reason": "G3 was not evaluated: the reference grader did not pass its probe check."},
        )
    missing = [item for item in sample_ids if item not in pairs]
    if missing:
        return GateResult(
            "G3",
            None,
            {
                "reason": "G3 was not evaluated: some sampled answers have no "
                "independent reference vote and final grade.",
                "missing_pairs": missing,
            },
        )
    grades = {item: {"final": pairs[item][0], "reference": pairs[item][1]} for item in sample_ids}
    result = bootstrap_alpha(_clusters(grades, question_of), seed=f"{seed}/G3", resamples=resamples)
    passed = result.alpha is not None and result.alpha >= ALPHA_PASS and result.low >= ALPHA_MINIMUM
    return GateResult("G3", passed, _alpha_details(result))


def g4(verdicts: Mapping[str, Mapping[str, bool | None]]) -> GateResult:
    """Each single judge's claim verdicts against the panel's; a flip is judge-sensitive."""
    panel = verdicts.get("panel") or {}
    evaluable = {claim: v for claim, v in panel.items() if v is not None}
    flips: dict[str, list[str]] = {}
    for grader, claims in verdicts.items():
        if grader == "panel":
            continue
        flipped = sorted(c for c, v in evaluable.items() if claims.get(c) != v)
        if flipped:
            flips[grader] = flipped
    sensitive = sorted({c for flipped in flips.values() for c in flipped})
    if not evaluable:
        return GateResult(
            "G4",
            None,
            {"reason": "no claim is evaluable under the panel", "judge_sensitive": [], "flips": {}},
        )
    return GateResult("G4", not sensitive, {"judge_sensitive": sensitive, "flips": flips})


def outcome_tier(g1: GateResult, g2: GateResult, g3: GateResult, g4: GateResult) -> Tier:
    if all(g.passed is True for g in (g1, g2, g3, g4)):
        return "Validated"
    alphas = [g.details.get("alpha") for g in (g2, g3)]
    if (
        g1.passed is True
        and g4.passed is True
        and all(a is not None and a >= ALPHA_MINIMUM for a in alphas)
    ):
        return "Tentative"
    return "Judge not validated"


def alpha_by_group(
    grades: Mapping[str, Mapping[str, float | None]],
    group_of: Mapping[str, str],
    question_of: Mapping[str, str],
    *,
    seed: str,
    resamples: int = 10_000,
) -> dict[str, AlphaResult]:
    groups: dict[str, dict[str, Mapping[str, float | None]]] = defaultdict(dict)
    for item, values in grades.items():
        groups[group_of[item]][item] = values
    return {
        group: bootstrap_alpha(
            _clusters(members, question_of), seed=f"{seed}/{group}", resamples=resamples
        )
        for group, members in sorted(groups.items())
    }


@dataclass(frozen=True)
class SampleCandidate:
    item_id: str
    model_family_id: str
    system_id: str
    category: str


def registered_g3_sample(
    answers: Mapping[tuple[str, str], AnswerVotes],
    transcripts: Mapping[tuple[str, str], Mapping[str, Any]],
    questions: Mapping[str, GradingQuestion],
    judges: Sequence[str],
    *,
    seed: str,
) -> tuple[list[str], dict[str, Any]]:
    """Reconstruct the producer's cohort before looking at reference outcomes.

    Allocate over all successful nonempty answers, then exclude suspects and
    panel settlements. Pending panel records invalidate selection; they must
    never shrink a stratum or masquerade as an adjudication exclusion.
    """
    eligible = {
        key: row for key, row in transcripts.items()
        if row.get("status") == "succeeded" and (row.get("answer") or "").strip()
    }
    if not judges or answers.keys() != eligible.keys():
        raise BenchmarkValidationError("registered G3 answer inventory is incomplete")
    candidates = []
    for key, row in eligible.items():
        av = answers[key]
        if (
            av.item_id != safe_id(*key)
            or (av.competitor_id, av.corpus_id) != key
            or key != (row["competitor_id"], row["corpus_id"])
            or key[1] not in questions
            or set(av.votes) != set(judges)
            or not all(av.records.get(j, {}).get("final") is True for j in judges)
        ):
            raise BenchmarkValidationError("registered G3 needs complete current panel records")
        candidates.append(SampleCandidate(
            av.item_id, row["model_family_id"], row["system_id"], questions[key[1]].category
        ))
    return g3_sample(candidates, excluded=set(reference_needed(answers)), seed=seed)


def registered_g3(
    sample: Mapping[str, Any] | None,
    pairs: Mapping[str, tuple[float, float]],
    question_of: Mapping[str, str],
    *,
    answers: Mapping[tuple[str, str], AnswerVotes],
    transcripts: Mapping[tuple[str, str], Mapping[str, Any]],
    questions: Mapping[str, GradingQuestion],
    judges: Sequence[str],
    seed: str,
    reference_check: GateResult,
    resamples: int = 10_000,
) -> GateResult:
    """Admit G3 only for the exact registered cohort and allocation metadata."""
    try:
        ids, info = registered_g3_sample(answers, transcripts, questions, judges, seed=seed)
    except (BenchmarkValidationError, KeyError, TypeError, ValueError) as exc:
        return GateResult("G3", None, {"reason": f"G3 was not evaluated: {exc}"})
    expected = {**info, "ids": ids}
    saved_ids = None if sample is None else sample.get("ids")
    count = len(saved_ids) if isinstance(saved_ids, list) else 0
    # JSON comparison also distinguishes booleans from integer sizes/counts.
    if sample is None or any(
        k not in sample or json.dumps(sample[k], sort_keys=True) != json.dumps(v, sort_keys=True)
        for k, v in expected.items()
    ):
        return GateResult("G3", None, {
            "reason": f"G3 was not evaluated: the reference sample holds "
                      f"{count} of 300 answers; "
                      "saved reference sample differs from the registered "
                      "selection or lacks its allocation metadata.",
        })
    return g3(ids, pairs, question_of, seed=seed, reference_check=reference_check,
              sample_info=info, resamples=resamples)


def g3_sample(
    candidates: Sequence[SampleCandidate], *, excluded: set[str], seed: str, size: int = 300
) -> tuple[list[str], dict[str, Any]]:
    """Proportional stratified sample; a short stratum borrows only within its (model, system)."""
    strata: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for c in candidates:
        strata[(c.model_family_id, c.system_id, c.category)].append(c.item_id)
    total = sum(len(ids) for ids in strata.values())
    quotas = {key: size * len(ids) / total for key, ids in strata.items()} if total else {}
    allocation = {key: math.floor(q) for key, q in quotas.items()}
    leftover = size - sum(allocation.values()) if total else 0
    for key in sorted(quotas, key=lambda k: (-(quotas[k] - allocation[k]), k))[:leftover]:
        allocation[key] += 1
    shuffled: dict[tuple[str, str, str], list[str]] = {}
    for key, ids in strata.items():
        order = sorted(ids)
        random.Random(f"{seed}/g3/{'/'.join(key)}").shuffle(order)
        shuffled[key] = [i for i in order if i not in excluded]
    taken: dict[tuple[str, str, str], list[str]] = {
        key: shuffled[key][: allocation[key]] for key in strata
    }
    borrowed: dict[str, dict[str, int]] = {}
    shortfall: dict[str, int] = {}
    groups: dict[str, dict[str, int]] = {}
    for model, system in sorted({(k[0], k[1]) for k in strata}):
        keys = sorted(k for k in strata if (k[0], k[1]) == (model, system))
        name = f"{model}/{system}"
        gap = sum(allocation[k] - len(taken[k]) for k in keys)
        for donor in keys:
            if gap <= 0:
                break
            spare = shuffled[donor][len(taken[donor]) :]
            extra = spare[:gap]
            if extra:
                taken[donor] = taken[donor] + extra
                borrowed.setdefault(name, {})[donor[2]] = len(extra)
                gap -= len(extra)
        allocated = sum(allocation[k] for k in keys)
        groups[name] = {"allocated": allocated, "taken": sum(len(taken[k]) for k in keys)}
        if gap > 0:
            shortfall[name] = gap
    ids = sorted(i for key in taken for i in taken[key])
    info = {
        "seed": seed,
        "size": size,
        "borrowed": borrowed,
        "shortfall": shortfall,
        "groups": groups,
    }
    return ids, info


def _pairing_error(
    planned: set[CellKey],
    scores: Mapping[CellKey, float | None],
    systems: tuple[str, str],
    model: str,
) -> str | None:
    cells = {k for k in planned if k[0] in systems and k[1] == model}
    actual = {k for k in scores if k[0] in systems and k[1] == model}
    if not cells:
        return f"no planned cells for {model} comparing {systems[0]} and {systems[1]}"
    for difference, reason in (
        (cells - actual, "planned cells with no scored record"),
        (actual - cells, "scored cells absent from the planned set"),
    ):
        if difference:
            labels = [f"{s}/{m}/{q}/r{r}" for s, m, q, r in sorted(difference)]
            return f"{reason}: {labels}"
    left, right = ({(q, r) for s, _, q, r in cells if s == system} for system in systems)
    if left != right:
        return (
            f"unequal question/repeat sets: {systems[0]} alone {sorted(left - right)}, "
            f"{systems[1]} alone {sorted(right - left)}"
        )
    return None


def _not_evaluable(claim: str, model: str, reason: str) -> ClaimResult:
    return ClaimResult(claim, model, None, None, f"not evaluable: {reason}")


def c2(
    tokens: Mapping[CellKey, int | None],
    scores: Mapping[CellKey, float | None],
    planned: set[CellKey],
    *,
    model: str,
    seed: str,
    peer: str = "docs-fetch",
    resamples: int = 10_000,
) -> ClaimResult:
    """Tokens per correct answer, python-docs over the peer; passes when the CI's top is < 1."""
    if peer == "no-docs":
        raise ValueError("no-docs is never a token peer: it is the correctness floor (design §2)")
    systems = ("python-docs", peer)
    reason = _pairing_error(planned, scores, systems, model)
    if reason:
        return _not_evaluable("C2", model, reason)
    cells = [k for k in planned if k[0] in systems and k[1] == model]
    open_cells = sorted(
        f"{k[0]}/{k[1]}/{k[2]}/r{k[3]}"
        for k in cells
        if tokens.get(k) is None or scores.get(k) is None
    )
    if open_cells:
        return _not_evaluable("C2", model, f"ungraded or uncounted cells: {open_cells}")
    used: dict[tuple[str, str], int] = defaultdict(int)  # (question, system) -> tokens
    credit: dict[tuple[str, str], float] = defaultdict(float)  # (question, system) -> score
    for key in cells:
        used[(key[2], key[0])] += tokens[key] or 0
        credit[(key[2], key[0])] += scores[key] or 0.0

    def per_correct(questions: Sequence[str], system: str) -> float:
        return tokens_per_correct(
            [used[(q, system)] for q in questions], [credit[(q, system)] for q in questions]
        )

    def ratio(questions: Sequence[str]) -> float:
        pd, other = per_correct(questions, "python-docs"), per_correct(questions, peer)
        if math.isinf(pd):
            return math.inf
        return 0.0 if math.isinf(other) else pd / other

    questions = sorted({k[2] for k in cells})
    interval = paired_bootstrap(
        questions, ratio, seed=f"{seed}/C2/{model}/{peer}", resamples=resamples
    )
    return ClaimResult("C2", model, interval.high < 1.0, interval, None)


@dataclass(frozen=True)
class OfflineVerdict:
    ok: bool | None  # None = not evaluable
    denied_attempts: list[dict[str, Any]]  # {job_id, host}: refused, so nothing left the sandbox


def offline_verdict(
    evidence: OfflineEvidence | None,
    egress: Mapping[str, Any] | None,
    *,
    expected_checks: int,
    airgap_jobs: Sequence[str],
) -> OfflineVerdict:
    """C3's evidence, read exactly as PR 4 writes it (#200 Tasks 12 and 15)."""
    jobs = (egress or {}).get("jobs") or {}
    denied = [  # disclosed even when C3 is not evaluable
        {"job_id": job, "host": host}
        for job in airgap_jobs
        if job in jobs
        for host in jobs[job]["hosts_denied"]
    ]
    if evidence is None or egress is None or evidence.checks < expected_checks:
        return OfflineVerdict(None, denied)
    if not airgap_jobs or any(job not in jobs for job in airgap_jobs):
        return OfflineVerdict(None, denied)
    reached = [h for job in airgap_jobs for h in jobs[job]["hosts_allowed"] if h != OLLAMA_HOST]
    ok = (
        egress["isolation_probe"] == "pass"
        and evidence.all_passed
        and not evidence.internet_hosts
        and not reached
    )
    return OfflineVerdict(ok, denied)


@dataclass(frozen=True)
class RunOfflineEvidence:
    """Current exported evidence shared by scoring, publication and diagnostics."""

    verdict: OfflineVerdict
    expected_checks: int | None
    airgap_jobs: list[str]
    host_checks: OfflineEvidence | None
    isolation_probe: str
    internet_hosts: list[str]  # observed reached hosts, never denied attempts

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.verdict.ok,
            "expected_checks": self.expected_checks,
            "airgap_jobs": self.airgap_jobs,
            "denied_attempts": self.verdict.denied_attempts,
        }


def run_offline_evidence(
    run_dir: Path, families: Mapping[str, Mapping[str, Any]]
) -> RunOfflineEvidence:
    """Rederive C3 offline status from raw files, never a cached grading summary.

    The host checks once before the arm, after every trajectory or discard (failed
    attempts included), and once after the arm. Discard ownership comes from its
    planned cell directory, as in the collector, not optional transcript metadata.
    """
    local = {fid for fid, spec in families.items()
             if isinstance(spec, Mapping) and spec.get("arm") == "air-gapped"}
    hosted = {fid for fid, spec in families.items()
              if isinstance(spec, Mapping) and spec.get("arm") == "hosted"}

    def cell_family(name: Any) -> str | None:
        parts = name.rsplit("--", 2) if isinstance(name, str) else []
        return parts[1] if len(parts) == 3 else None

    cells = (read_evidence_json(run_dir, "planned-cells.json") or {}).get("cells")
    owners: dict[str, str] = {}
    coordinates: set[tuple[str, str]] = set()
    jobs: set[str] = set()
    valid = isinstance(cells, list) and bool(cells)
    for cell in cells if isinstance(cells, list) else []:
        declared = cell.get("model_family_id") if isinstance(cell, dict) else None
        named = cell_family(cell.get("cell")) if isinstance(cell, dict) else None
        if (
            isinstance(cell, dict) and named not in local
            and not (isinstance(declared, str) and declared in local)
            and (named in hosted or isinstance(declared, str) and declared in hosted)
        ):
            # Hosted identity conflicts belong to their own comparisons, not
            # the air-gapped inventory. Ownership follows the cell directory,
            # not a competitor alias pointing at an unrelated local family.
            name, family = cell.get("cell"), cell.get("model_family_id")
            if isinstance(name, str) and isinstance(family, str):
                owners[name] = family
            continue
        if not isinstance(cell, dict) or any(
            not isinstance(cell.get(field), str) or not cell[field].strip()
            for field in ("cell", "model_family_id", "system_id", "corpus_id")
        ):
            valid = False
            continue
        family, system, name = cell["model_family_id"], cell["system_id"], cell["cell"]
        repeat = cell.get("repeat")
        coord = (name, cell["corpus_id"])
        if (
            family not in families
            or type(repeat) is not int
            or repeat < 1
            or name != f"{system}--{family}--r{repeat}"
            or coord in coordinates
            or cell.get("competitor_id", name) != name
        ):
            valid = False
        coordinates.add(coord)
        owners[name] = family
        if family in local:
            jobs.add(f"{family}--{system}")
    attempts = 0
    for kind in ("transcripts", "discarded"):
        for path in artifact_paths(run_dir, run_dir / kind, "*/*.json"):
            owner = owners.get(path.parent.name)
            if owner is None:
                record = optional_json(run_dir, path)
                declared = (record or {}).get("model_family_id")
                if (cell_family(path.parent.name) in local or not isinstance(declared, str)
                        or declared in local or declared not in families):
                    valid = False
            if owner not in local:
                continue
            attempts += 1
            record = optional_json(run_dir, path)
            if record is None or record.get("model_family_id", owner) != owner:
                valid = False
    expected = attempts + 2 if valid and jobs and attempts else None
    airgap_jobs = sorted(jobs)
    raw_egress = read_evidence_json(run_dir, "egress/summary.json")
    egress = egress_from_mapping(raw_egress)
    observed = egress_from_mapping(raw_egress, partial=True) or {}
    observed_jobs = observed.get("jobs", {})
    denied = [
        {"job_id": job, "host": host}
        for job in airgap_jobs
        for host in observed_jobs.get(job, {}).get("hosts_denied", [])
    ]
    evidence = load_offline_evidence(run_dir)
    reached = {
        host
        for job in airgap_jobs
        for host in observed_jobs.get(job, {}).get("hosts_allowed", [])
        if host != OLLAMA_HOST
    }
    if evidence is not None:
        reached.update(evidence.internet_hosts)
    verdict = (
        offline_verdict(evidence, egress, expected_checks=expected, airgap_jobs=airgap_jobs)
        if expected is not None
        else OfflineVerdict(None, [])
    )
    return RunOfflineEvidence(
        OfflineVerdict(verdict.ok, denied),
        expected,
        airgap_jobs,
        evidence,
        observed.get("isolation_probe", "unknown"),
        sorted(reached),
    )


def c3(
    scores: Mapping[CellKey, float | None],
    planned: set[CellKey],
    *,
    model: str,
    seed: str,
    offline_ok: bool | None,
    resamples: int = 10_000,
) -> ClaimResult:
    """Mean correctness with docs minus without; passes when the CI's bottom is > 0, offline."""
    systems = ("python-docs", "no-docs")
    reason = _pairing_error(planned, scores, systems, model)
    if reason:
        return _not_evaluable("C3", model, reason)
    cells = [k for k in planned if k[0] in systems and k[1] == model]
    pending = sorted(f"{k[0]}/{k[1]}/{k[2]}/r{k[3]}" for k in cells if scores.get(k) is None)
    if pending:
        return _not_evaluable("C3", model, f"ungraded cells: {pending}")
    if offline_ok is None:
        return _not_evaluable(
            "C3", model, "no complete offline evidence (airgap/checks.json and egress/summary.json)"
        )
    sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for key in cells:
        entry = sums[(key[2], key[0])]
        entry[0] += scores[key] or 0.0
        entry[1] += 1

    def difference(questions: Sequence[str]) -> float:
        means = []
        for system in systems:
            total = sum(sums[(q, system)][0] for q in questions)
            count = sum(sums[(q, system)][1] for q in questions)
            means.append(total / count if count else 0.0)
        return means[0] - means[1]

    questions = sorted({k[2] for k in cells})
    interval = paired_bootstrap(
        questions, difference, seed=f"{seed}/C3/{model}", resamples=resamples
    )
    return ClaimResult("C3", model, interval.low > 0 and offline_ok, interval, None)


_MODEL_LINEAGES = {
    "claude-sonnet-5-5": "anthropic",
    "gpt-6.1-sol": "openai",
    "gemini-3.8-flash": "google",
    "gemma4-12b": "google",
    "gemma4:12b": "google",
}


def model_lineage(family_id: str, model: Mapping[str, Any]) -> str | None:
    """Use the registered model family (#199 Task 7), including for aliased matrix entries."""
    model_id = model.get("model_id")
    lineage = _MODEL_LINEAGES.get(family_id)
    if not lineage and isinstance(model_id, str):
        lineage = _MODEL_LINEAGES.get(model_id)
    # Scripted matrix entries name the family they represent in their provider field.
    provider = model.get("provider")
    return lineage or (provider if isinstance(provider, str) else None)


@dataclass(frozen=True)
class SelfPreferenceInterval:
    point: float
    low: float | None
    high: float | None


def self_preference(
    grades: Mapping[str, Mapping[str, float | None]],
    family_of: Mapping[str, str],
    question_of: Mapping[str, str],
    judge_family: Mapping[str, str],
    *,
    seed: str,
    resamples: int = 10_000,
) -> dict[tuple[str, str], SelfPreferenceInterval | None]:
    """Compare each judge's excess over its peers on one lineage versus the others."""
    complete = {
        i: g
        for i, g in grades.items()
        if i in family_of and all(g.get(j) is not None for j in judge_family)
    }
    result: dict[tuple[str, str], SelfPreferenceInterval | None] = {}
    for judge in sorted(judge_family):
        peers = [j for j in judge_family if j != judge]
        excess = {
            i: float(g[judge] or 0.0) - statistics.mean(g[j] or 0.0 for j in peers)
            for i, g in complete.items()
            if peers
        }
        for family in sorted(set(family_of.values())):
            per_q: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
            for item, value in excess.items():
                entry = per_q[question_of[item]]
                offset = 0 if family_of[item] == family else 2
                entry[offset] += value
                entry[offset + 1] += 1

            def spi(
                questions: Sequence[str], per_q: Mapping[str, list[float]] = per_q
            ) -> float | None:
                inside = sum(per_q[q][0] for q in questions), sum(per_q[q][1] for q in questions)
                outside = sum(per_q[q][2] for q in questions), sum(per_q[q][3] for q in questions)
                if not inside[1] or not outside[1]:
                    return None
                return inside[0] / inside[1] - outside[0] / outside[1]

            ids = sorted(per_q)
            point = spi(ids)
            key = (judge, family)
            result[key] = None
            if point is not None:
                rng = random.Random(f"{seed}/SPI/{judge}/{family}")
                draws = []
                for _ in range(resamples):
                    draw = spi(rng.choices(ids, k=len(ids)))
                    if draw is None:
                        break  # The registered question bootstrap cannot supply a full interval.
                    draws.append(draw)
                bounds = (
                    percentile_interval(draws)
                    if draws and len(draws) == resamples
                    else (None, None)
                )
                result[key] = SelfPreferenceInterval(point, *bounds)
    return result


def test_retest(
    pass1: Mapping[str, Mapping[str, float | None]],
    pass2: Mapping[str, Mapping[str, float | None]],
    question_of: Mapping[str, str],
    *,
    seed: str,
    resamples: int = 10_000,
) -> dict[str, dict[str, Any]]:
    """Per judge, alpha between its pass-1 and pass-2 grades of the same items."""
    judges = sorted({j for g in pass1.values() for j in g})
    result: dict[str, dict[str, Any]] = {}
    for judge in judges:
        both = {
            item: {"pass1": pass1[item].get(judge), "pass2": pass2[item].get(judge)}
            for item in pass1
            if item in pass2
        }
        alpha = bootstrap_alpha(
            _clusters(both, question_of), seed=f"{seed}/retest/{judge}", resamples=resamples
        )
        result[judge] = {
            "alpha": alpha.alpha,
            "low": alpha.low,
            "high": alpha.high,
            "items": len(both),
        }
    return result


def length_grade_correlation(lengths: Sequence[int], scores: Sequence[float]) -> float | None:
    if len(lengths) < 2:
        return None
    try:
        return statistics.correlation([float(x) for x in lengths], list(scores))
    except statistics.StatisticsError:
        return None


def _strings(value: Any) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def read_evidence_json(
    run_dir: Path,
    filename: Literal["planned-cells.json", "egress/summary.json", "airgap/checks.json"],
) -> dict[str, Any] | None:
    """Read a fixed exported artifact, without following links or exposing unsafe text."""
    return optional_json(run_dir, run_dir / filename)


EGRESS_JOB_FIELDS = {"hosts_allowed", "hosts_denied", "lookup_package_docs_attempts"}


def load_egress_evidence(run_dir: Path, *, partial: bool = False) -> dict[str, Any] | None:
    """Read ruling-9 evidence. C1 may disclose partial observations, C3 needs full records."""
    return egress_from_mapping(read_evidence_json(run_dir, "egress/summary.json"), partial=partial)


def egress_from_mapping(
    data: dict[str, Any] | None, *, partial: bool = False
) -> dict[str, Any] | None:
    if not data or not isinstance(data.get("jobs"), dict):
        return None
    probe = data.get("isolation_probe")
    if probe not in ("pass", "fail"):
        if not partial:
            return None
        probe = "unknown"
    jobs = {}
    for jid, job in data["jobs"].items():
        fields: dict[str, Any] = {}
        if isinstance(job, dict):
            for field in ("hosts_allowed", "hosts_denied"):
                hosts = job.get(field)
                if _strings(hosts) and all(h.strip() for h in hosts):
                    fields[field] = hosts
            count = job.get("lookup_package_docs_attempts")
            if type(count) is int and count >= 0:
                fields["lookup_package_docs_attempts"] = count
        if not partial and fields.keys() != EGRESS_JOB_FIELDS:
            return None
        jobs[jid] = fields
    if not jobs and not partial:
        return None
    return {"jobs": jobs, "isolation_probe": probe}


def load_offline_evidence(run_dir: Path) -> OfflineEvidence | None:
    """``<run>/airgap/checks.json`` (#200 Task 15), or ``None`` (fail closed)."""
    data = read_evidence_json(run_dir, "airgap/checks.json")
    if data is None:
        return None
    all_passed, checks, hosts = (
        data.get("all_passed"),
        data.get("checks"),
        data.get("internet_hosts"),
    )
    if not isinstance(all_passed, bool) or isinstance(checks, bool) or not isinstance(checks, int):
        return None
    if checks < 0 or not _strings(hosts) or any(not host.strip() for host in hosts):
        return None
    return OfflineEvidence(all_passed, checks, list(hosts))
