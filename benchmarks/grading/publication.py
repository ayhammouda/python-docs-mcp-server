"""Read-only reconciliation of exported gates before report publication.

Use the scorer's gate functions and the report's verified grade capture. A summary is a cache,
not evidence that can override the frozen probes, independent reference cohort or
single-judge claims. Reads use the report's confined, bounded artifact loaders.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.aggregate import LLM_PANEL, judge_grade, own_grade
from benchmarks.grading.calibration import replay_g1
from benchmarks.grading.gates import (
    G1Binding,
    GateResult,
    c2,
    c3,
    g2,
    g4,
    registered_g3,
)
from benchmarks.grading.mapping import grade
from benchmarks.grading.serialization import jsonable
from benchmarks.grading.stats import GATE_RESAMPLES
from benchmarks.report_files import optional_json
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks import agentic_report


def _gate(raw: Any, name: str) -> GateResult:
    if not isinstance(raw, dict) or not isinstance(raw.get("details"), dict):
        return GateResult(name, None, {"reason": "missing gate evidence"})
    passed = raw.get("passed")
    return GateResult(name, passed if type(passed) is bool else None, raw["details"])


def _consistent(saved: GateResult, actual: GateResult) -> GateResult:
    if jsonable(asdict(saved)) == jsonable(asdict(actual)):
        return actual
    return GateResult(
        saved.id,
        None,
        {
            **actual.details,
            "recorded_details": saved.details,
            "reason": "saved gate contradicts current gate evidence",
        },
    )


def reconcile_gates(run: agentic_report._RunMetadata) -> dict[str, GateResult]:
    """Derive gate eligibility from this export; never write or rescore its artifacts."""
    summary = run.summary
    gates = {
        name: _gate((summary.get("gates") or {}).get(name), name)
        for name in ("G1", "G2", "G3", "G4")
    }
    # No calibration can become eligible by changing the tier scalar.
    if gates["G1"].passed is not True:
        return gates
    try:
        if summary.get("resamples") != GATE_RESAMPLES:
            raise ValueError("gate scoring requires the registered bootstrap method")
        if run.inputs.keys() != run.frozen.keys():
            raise ValueError("missing or unbound frozen inputs")
        efforts = summary["efforts"]
        ordered = summary["composition"].split("+")
        if len(efforts) != 3 or len(ordered) != 3 or set(ordered) != set(efforts):
            raise ValueError("incomplete judging panel")
        composition = {
            j: {"model_id": run.judges[j]["model_id"], "effort": efforts[j]} for j in ordered
        }
        binding = G1Binding(composition, run.inputs)
        root = run.bundle.run_dir
        entries = optional_json(root, root / "grading/disclosure/g1-entries.json") or {}
        selected, check = replay_g1(
            entries,
            binding,
            panel=CapturedInput.from_bytes(run.frozen["panel"], "panel", "panel"),
            probes=CapturedInput.from_bytes(run.frozen["probes"], "probes", "probes"),
            criteria=CapturedInput.from_bytes(run.frozen["criteria"], "criteria", "criteria"),
        )
        if selected.details.get("composition") != summary.get("composition"):
            raise ValueError("G1 composition differs from run")
        gates["G1"] = _consistent(gates["G1"], selected)
        if gates["G1"].passed is not True:
            return gates
        reference = _consistent(
            _gate(summary.get("reference_check"), "reference_probe_check"), check
        )

        captured = run.grade_capture
        own, question_of, pairs = {}, {}, {}
        panel_scores = {}
        judge_scores = {j: {} for j in efforts}
        tokens = {}
        for key, transcript in run.transcripts.items():
            coord = (
                transcript["system_id"],
                transcript["model_family_id"],
                key[1],
                transcript["repeat"],
            )
            score = captured.scores.get(key)
            panel_scores[coord] = score
            for scores in judge_scores.values():
                scores[coord] = score
            count = run.tokens.get(key, {}).get("client_wrapped_tokens")
            if count is not None:
                tokens[coord] = count

        for key, av in captured.answers.items():
            item = av.item_id
            question_of[item] = key[1]
            question = captured.questions[key[1]]
            scores = {j: own_grade(av, j, question.weights) for j in efforts}
            if not av.suspect and sum(s is not None for s in scores.values()) >= 2:
                own[item] = scores
            final = captured.finals.get(key)
            if final is not None:
                transcript = run.transcripts[key]
                coord = (
                    transcript["system_id"],
                    transcript["model_family_id"],
                    key[1],
                    transcript["repeat"],
                )
                for j in efforts:
                    judge_scores[j][coord] = judge_grade(av, j, question, final)
                if final["method"] == LLM_PANEL and not av.suspect and av.reference is not None:
                    pairs[item] = final["score"], grade(av.reference.fields(), question.weights)[0]

        gates["G2"] = _consistent(gates["G2"], g2(own, question_of, seed=run.seed))
        sample = run.reference_sample
        gates["G3"] = _consistent(
            gates["G3"],
            registered_g3(
                sample,
                pairs,
                question_of,
                answers=captured.answers,
                transcripts=run.transcripts,
                questions=captured.questions,
                judges=list(efforts),
                seed=run.seed,
                reference_check=reference,
            ),
        )
        planned = {
            (c["system_id"], c["model_family_id"], c["corpus_id"], c["repeat"])
            for c in run.plan["cells"]
        }
        systems_of: dict[str, set[str]] = defaultdict(set)
        for system, model, _question, _repeat in planned:
            systems_of[model].add(system)
        c2_models = {
            m for m, systems in systems_of.items() if {"python-docs", "docs-fetch"} <= systems
        }
        c3_models = {
            m
            for m, systems in systems_of.items()
            if {"python-docs", "no-docs"} <= systems
            and run.families.get(m, {}).get("arm") == "air-gapped"
        }
        offline = run.offline_ok()

        def claims(scores: dict[Any, Any]) -> dict[str, dict[str, Any]]:
            return {
                "C2": {
                    m: asdict(c2(tokens, scores, planned, model=m, seed=run.seed))
                    for m in c2_models
                },
                "C3": {
                    m: asdict(c3(scores, planned, model=m, seed=run.seed, offline_ok=offline))
                    for m in c3_models
                },
            }

        panel_claims = claims(panel_scores)
        by_judge = {
            j: panel_claims if scores == panel_scores else claims(scores)
            for j, scores in judge_scores.items()
        }
        if jsonable(panel_claims) != summary.get("claims") or jsonable(by_judge) != summary.get(
            "claims_by_judge"
        ):
            # Pending or unverified cells cannot inherit a cached claim. Keep
            # independently reconciled G2/G3 evidence, including eligible G2 pairs.
            gates["G4"] = GateResult(
                "G4", None, {
                    **gates["G4"].details,
                    "reason": "saved claims differ from captured panel or single-judge evidence",
                },
            )
            return gates
        verdicts = {
            j: {f"{cid}/{m}": c["passed"] for cid, by in cs.items() for m, c in by.items()}
            for j, cs in {"panel": panel_claims, **by_judge}.items()
        }
        actual_g4 = g4(verdicts)
        gates["G4"] = _consistent(gates["G4"], actual_g4)
        if summary.get("judge_sensitive") != actual_g4.details.get("judge_sensitive"):
            raise ValueError("judge-sensitive claims differ from G4")
    except KeyLeakError:
        raise
    except (
        KeyError,
        TypeError,
        ValueError,
        StopIteration,
        ZeroDivisionError,
        BenchmarkValidationError,
    ):
        # Preserve measured statistics for disclosure while withholding eligibility.
        gates = {
            name: GateResult(
                name, None, {**g.details, "reason": "missing or inconsistent gate evidence"}
            )
            for name, g in gates.items()
        }
    return gates
