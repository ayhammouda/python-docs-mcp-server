"""Read-only reconciliation of exported gates before report publication.

Use the scorer's gate functions and the report's verified grade capture. A summary is a cache,
not evidence that can override the frozen probes, independent reference cohort or
single-judge claims. Reads use the report's confined, bounded artifact loaders.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import asdict
from decimal import DecimalException
from typing import TYPE_CHECKING, Any

from benchmarks.agentic.inputs import CapturedInput, json_mapping
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.aggregate import (
    LLM_PANEL,
    AnswerVotes,
    is_final,
    judge_grade,
    own_grade,
    record_order,
    recorded_result,
    vote_from_record,
)
from benchmarks.grading.calibration import replay_g1
from benchmarks.grading.diagnostics import diagnostic_tables, missing_records
from benchmarks.grading.gates import (
    G1Binding,
    GateResult,
    c2,
    c3,
    g2,
    g4,
    registered_g3,
    registered_g3_sample,
)
from benchmarks.grading.grounding import is_grounded
from benchmarks.grading.mapping import grade
from benchmarks.grading.probes import probe_file_from_inputs
from benchmarks.grading.serialization import jsonable
from benchmarks.grading.stats import GATE_RESAMPLES
from benchmarks.report_files import artifact_bytes, artifact_paths, optional_json
from benchmarks.report_measurements import evidence_cell, grading_receipt_matches
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.report_contracts import PublicationRun


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


def _second_pass(
    run: PublicationRun, efforts: dict[str, str | None]
) -> dict[tuple[str, str], AnswerVotes]:
    """Capture each pass-2 vote once, selecting before checking its receipt."""
    captured = run.grade_capture
    composition = {
        j: {"model_id": run.judges[j]["model_id"], "effort": e} for j, e in efforts.items()
    }
    second = {}
    for key, av in captured.answers.items():
        request = captured.requests[key]
        parsed, records = {}, {}
        for judge, effort in efforts.items():
            base = run.root / "grading/votes/answers" / judge / (effort or "default") / "pass2"
            candidates = []
            for path in artifact_paths(run.root, base / av.item_id, "*.json"):
                body = artifact_bytes(run.root, path)
                row = json_mapping(body, "pass-2 grading vote")
                run.vote_versions[path] = row.get("reported_model")
                candidates.append((record_order(path, row), row, hashlib.sha256(body).hexdigest()))
            if not candidates:
                parsed[judge] = None
                continue
            _, row, digest = max(candidates, key=lambda c: c[0])
            expected = {
                "target": "answers",
                "pass": 2,
                "judge_id": judge,
                "model_id": run.judges[judge]["model_id"],
                "effort": effort,
                "item_id": av.item_id,
                "input_sha256": request.sha256(),
            }
            if any(type(row.get(k)) is not type(v) or row[k] != v for k, v in expected.items()):
                raise ValueError("pass-2 vote differs from current request")
            invocation = captured.runs.get(row.get("grading_run_id"), {})
            if run.judges[judge].get("api") == "scripted":
                result = recorded_result(
                    run.root / "grading", row, {**run.judges[judge], "id": judge},
                    vote_sha256=digest, invocation=invocation,
                )
                parsed[judge] = vote_from_record(row, request=request, result=result)
                records[judge] = {**row, "final": is_final(row)}
                continue
            results = {}
            if (
                invocation.get("target") != "answers"
                or invocation.get("composition") != composition
                or not grading_receipt_matches(
                    run.root,
                    row,
                    digest,
                    run.judges[judge],
                    invocation,
                    run.grading_events,
                    results,
                )
            ):
                raise ValueError("pass-2 execution evidence is unavailable")
            parsed[judge] = vote_from_record(row, request=request, result=results[digest])
            records[judge] = {**row, "final": is_final(row)}
        second[key] = AnswerVotes(av.item_id, *key, parsed, records, av.suspect, None, None)
    return second


def reconcile_disclosures(run: PublicationRun) -> None:
    """Replace diagnostic caches using the same capture as the headline gate replay."""
    source = optional_json(run.root, run.root / "grading/disclosure/g1-entries.json") or {}
    run.g1_entries = {}
    for key, entry in source.items():
        try:
            selected, reference = replay_g1(
                source,
                G1Binding(
                    {j: entry["binding"]["composition"][j] for j in key.split("+")},
                    run.inputs,
                ),
                panel=CapturedInput.from_bytes(run.frozen["panel"], "panel", "panel"),
                probes=CapturedInput.from_bytes(run.frozen["probes"], "probes", "probes"),
                criteria=CapturedInput.from_bytes(run.frozen["criteria"], "criteria", "criteria"),
            )
        except KeyLeakError:
            raise
        except (BenchmarkValidationError, KeyError, TypeError, ValueError):
            selected = GateResult("G1", None, {"reason": "unverified calibration evidence"})
            reference = GateResult("reference_probe_check", None, {})
        run.g1_replays[key] = selected, reference
        run.g1_entries[key] = (
            {**entry, "gate": {"passed": selected.passed, "details": selected.details}}
            if isinstance(entry, dict)
            else {"gate": {"passed": None, "details": {}}}
        )
    try:
        suite = probe_file_from_inputs(
            CapturedInput.from_bytes(run.frozen["probes"], "probes", "probes"),
            CapturedInput.from_bytes(run.frozen["criteria"], "criteria", "criteria"),
            None,
        )
        run.probe_dropped = dict(Counter(str(d.get("type")) for d in suite.dropped))
    except KeyLeakError:
        raise
    except (BenchmarkValidationError, KeyError, TypeError, ValueError):
        run.probe_dropped = None
    run.sample_allocation = {}
    run.retest_unknown = False
    diagnostics, breakdowns = {}, {}
    captured = run.grade_capture
    efforts = run.summary.get("efforts")
    if run.summary:
        run.summary = {**run.summary, "diagnostics": {}, "breakdowns": {}}
    if captured.reconciled and isinstance(efforts, dict) and efforts:
        providers = {}
        for judge, effort in efforts.items():
            provider = run.judges.get(judge, {}).get("provider")
            if not isinstance(provider, str) or (
                effort is not None and not isinstance(effort, str)
            ):
                return
            providers[judge] = provider
        sample_ids = []
        try:
            sample_ids, info = registered_g3_sample(
                captured.answers, run.transcripts, captured.questions, list(efforts), seed=run.seed
            )
            run.sample_allocation = {**info, "ids": sample_ids}
        except (BenchmarkValidationError, KeyError, ValueError, TypeError):
            pass  # Incomplete panels cannot establish the registered sample.
        try:
            pass2 = _second_pass(run, efforts)
        except KeyLeakError:
            raise
        except (
            BenchmarkValidationError,
            FileNotFoundError,
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
            IndexError,
            DecimalException,
        ):
            pass2 = {}
            run.retest_unknown = True
        # Grounding and labels are recomputed too: scoring records are projections.
        scoring = {}
        for k, transcript in run.transcripts.items():
            if k not in captured.scores:
                continue
            answer = transcript.get("answer") or ""
            calls = transcript.get("tool_calls") or []
            if (not isinstance(answer, str) or not isinstance(calls, list)
                    or not all(isinstance(call, dict) for call in calls)):
                return
            scoring[k] = {
                "score": captured.scores[k],
                "label": captured.finals.get(k, {}).get("label"),
                "grounded": is_grounded(answer, calls),
            }
        diagnostics, breakdowns = diagnostic_tables(
            captured.answers,
            pass2,
            captured.finals,
            run.transcripts,
            scoring,
            captured.questions,
            efforts,
            providers,
            run.families,
            sample_ids,
            missing_records(
                list(captured.runs.values()),
                efforts,
                {av.item_id: av for av in captured.answers.values()},
            ),
            seed=run.seed,
        )
    run.summary = (
        {**run.summary, "diagnostics": jsonable(diagnostics), "breakdowns": jsonable(breakdowns)}
        if run.summary
        else {}
    )


def reconcile_gates(run: PublicationRun) -> dict[str, GateResult]:
    """Derive gate eligibility from this export; never write or rescore its artifacts."""
    summary = run.summary
    saved_gates = summary.get("gates") or {}
    if not isinstance(saved_gates, dict):
        saved_gates = {}
    saved = {
        name: _gate(saved_gates.get(name), name)
        for name in ("G1", "G2", "G3", "G4")
    }
    gates = {
        name: GateResult(name, None, {"reason": "missing current gate evidence"}) for name in saved
    }
    run.summary = {**summary, "claims_by_judge": {}, "judge_sensitive": []} if summary else {}
    try:
        if summary.get("resamples") != GATE_RESAMPLES:
            raise ValueError("gate scoring requires the registered bootstrap method")
        if run.inputs.keys() != run.frozen.keys():
            raise ValueError("missing or unbound frozen inputs")
        efforts = summary["efforts"]
        composition_name = summary["composition"]
        if not isinstance(efforts, dict) or not isinstance(composition_name, str):
            raise ValueError("incomplete judging panel")
        ordered = composition_name.split("+")
        if len(efforts) != 3 or len(ordered) != 3 or set(ordered) != set(efforts):
            raise ValueError("incomplete judging panel")
        composition: dict[str, dict[str, str | None]] = {}
        for judge in ordered:
            model_id, effort = run.judges[judge]["model_id"], efforts[judge]
            if (not isinstance(model_id, str)
                    or (effort is not None and not isinstance(effort, str))):
                raise ValueError("invalid judging panel identity")
            composition[judge] = {"model_id": model_id, "effort": effort}
        binding = G1Binding(composition, run.inputs)
        if hasattr(run, "g1_replays"):
            selected, check = run.g1_replays.get(
                str(summary.get("composition")),
                (
                    GateResult("G1", None, {"reason": "missing calibration evidence"}),
                    GateResult("reference_probe_check", None, {}),
                ),
            )
        else:
            # Standalone callers still get a single bounded capture per invocation.
            root = run.bundle.run_dir
            entries = optional_json(root, root / "grading/disclosure/g1-entries.json") or {}
            selected, check = replay_g1(
                entries,
                binding,
                panel=CapturedInput.from_bytes(run.frozen["panel"], "panel", "panel"),
                probes=CapturedInput.from_bytes(run.frozen["probes"], "probes", "probes"),
                criteria=CapturedInput.from_bytes(run.frozen["criteria"], "criteria", "criteria"),
            )
        if hasattr(run, "g1_entries"):
            entry = run.g1_entries.get(str(summary.get("composition")), {})
            proof = entry.get("binding")
            if not isinstance(proof, dict) or proof.get("composition") != composition:
                raise ValueError("G1 binding differs from current composition")
        if selected.details.get("composition", summary.get("composition")) != summary.get(
            "composition"
        ):
            raise ValueError("G1 composition differs from run")
        gates["G1"] = _consistent(saved["G1"], selected)
        reference = _consistent(
            _gate(summary.get("reference_check"), "reference_probe_check"), check
        )

        captured = run.grade_capture
        own, question_of, pairs = {}, {}, {}
        panel_scores = {}
        judge_scores = {j: {} for j in efforts}
        tokens = {}
        for key, transcript in run.transcripts.items():
            coord = evidence_cell(transcript, key[1])
            score = captured.scores.get(key)
            panel_scores[coord] = score
            for scores in judge_scores.values():
                scores[coord] = score
            count = run.tokens.get(key, {}).get("client_wrapped_tokens")
            if count is not None:
                if type(count) is not int:
                    raise ValueError("invalid normalized token count")
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
                coord = evidence_cell(transcript, key[1])
                for j in efforts:
                    judge_scores[j][coord] = judge_grade(av, j, question, final)
                if final["method"] == LLM_PANEL and not av.suspect and av.reference is not None:
                    pairs[item] = final["score"], grade(av.reference.fields(), question.weights)[0]

        gates["G2"] = _consistent(saved["G2"], g2(own, question_of, seed=run.seed))
        sample = run.reference_sample
        gates["G3"] = _consistent(
            saved["G3"],
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
        cells = run.plan["cells"]
        if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
            raise BenchmarkValidationError("invalid comparison plan cells")
        planned = {evidence_cell(c) for c in cells}
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
        matched_claims = jsonable(panel_claims) == summary.get("claims") and jsonable(
            by_judge
        ) == summary.get("claims_by_judge")
        run.summary["claims_by_judge"] = jsonable(by_judge)
        verdicts = {
            j: {f"{cid}/{m}": c["passed"] for cid, by in cs.items() for m, c in by.items()}
            for j, cs in {"panel": panel_claims, **by_judge}.items()
        }
        actual_g4 = g4(verdicts)
        gates["G4"] = _consistent(saved["G4"], actual_g4)
        run.summary["judge_sensitive"] = actual_g4.details.get("judge_sensitive", [])
        if not matched_claims:
            # Keep independently replayed quantities; only eligibility is withheld.
            gates["G4"] = GateResult(
                "G4",
                None,
                {
                    **actual_g4.details,
                    "reason": "saved claims differ from captured panel or single-judge evidence",
                },
            )
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
