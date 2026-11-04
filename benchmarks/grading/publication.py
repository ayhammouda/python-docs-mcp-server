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

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.aggregate import (
    LLM_PANEL,
    AnswerVotes,
    is_final,
    judge_grade,
    own_grade,
    recorded_result,
    select_vote_file,
    vote_from_record,
)
from benchmarks.grading.calibration import _same, g1_entries, replay_g1
from benchmarks.grading.calibration_storage import calibration_bytes as artifact_bytes
from benchmarks.grading.diagnostics import diagnostic_tables, missing_records
from benchmarks.grading.gates import (
    ClaimResult,
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
from benchmarks.grading.vote_capture import MissingIssuedVote
from benchmarks.report_measurements import comparison_plan, evidence_cell, grading_receipt_matches
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.report_contracts import PublicationRun


def _gate(raw: Any, name: str) -> GateResult:
    if not isinstance(raw, dict) or not isinstance(raw.get("details"), dict):
        return GateResult(name, None, {"reason": "missing gate evidence"})
    passed = raw.get("passed")
    return GateResult(name, passed if type(passed) is bool else None, raw["details"])


def _consistent(saved: GateResult, actual: GateResult) -> GateResult:
    def core(gate: GateResult) -> dict[str, Any]:
        value = asdict(gate)
        if gate.id == "G1":
            # Auxiliary disclosure availability does not change the held-out gate.
            for name in ("held_out_test_retest", "tuning", "calibration_unknown"):
                value["details"].pop(name, None)
        return jsonable(value)

    if core(saved) == core(actual):
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
    """Capture each pass-2 vote once, selecting before replaying its reply receipt."""
    captured = run.grade_capture
    inventory = captured.inventory
    if inventory is None:
        raise ValueError("missing grading inventory capture")
    try:
        inventory.check("answers", 2)
    finally:
        run.vote_versions.update(inventory.versions)
    composition = {
        j: {"model_id": run.judges[j]["model_id"], "effort": e} for j, e in efforts.items()
    }
    second = {}
    for key, av in captured.answers.items():
        request = captured.requests[key]
        parsed, records = {}, {}
        for judge, effort in efforts.items():
            candidates = inventory.records(
                target="answers", judge_id=judge, effort=effort,
                pass_number=2, item_id=av.item_id,
            )
            for path, row, _ in candidates:
                run.vote_versions[path] = row.get("reported_model")
            selected = select_vote_file(candidates)
            if selected is None:
                parsed[judge] = None
                continue
            _, row, digest = selected
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
            invocation = captured.runs.get(row["grading_run_id"], {})
            if run.judges[judge].get("api") == "scripted":
                result = recorded_result(
                    run.root / "grading", row, {**run.judges[judge], "id": judge},
                    vote_sha256=digest, invocation=invocation,
                    history=inventory.history(row),
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
                    inventory.history(row),
                )
            ):
                raise ValueError("pass-2 execution evidence is unavailable")
            parsed[judge] = vote_from_record(row, request=request, result=results[digest])
            records[judge] = {**row, "final": is_final(row)}
        second[key] = AnswerVotes(av.item_id, *key, parsed, records, av.suspect, None, None)
    return second


def _calibration_archives(run: PublicationRun) -> tuple[dict[str, list[Any]] | None, str | None]:
    """Capture each named immutable export once; paths are derived from digests."""
    archives: dict[str, list[Any]] = {}
    try:
        disclosure = run.summary.get("disclosure")
        if "disclosure" not in run.summary or (
            isinstance(disclosure, dict) and "g1_files" not in disclosure
        ):
            return None, None  # Historical exports did not declare original copies.
        copies = disclosure.get("g1_files") if isinstance(disclosure, dict) else None
        if not isinstance(copies, list):
            raise ValueError("invalid calibration archive declaration")
        identities = {}
        for copy in copies:
            if not isinstance(copy, dict):
                raise ValueError("invalid calibration archive declaration")
            digest = copy["sha256"]
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in "0123456789abcdef" for c in digest)
                    or copy["path"] != f"disclosure/g1/{digest}.json"):
                raise ValueError("invalid calibration archive identity")
            identities[digest] = copy["path"]
        for digest, path in sorted(identities.items()):
            root = run.bundle.run_dir
            body = artifact_bytes(root, root / "grading" / path)
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError("calibration archive digest differs")
            for key, entry in g1_entries(body).items():
                archives.setdefault(key, []).append(entry)
        return archives, None
    except KeyLeakError:
        raise
    except (BenchmarkValidationError, FileNotFoundError, KeyError, TypeError, ValueError):
        return {}, "original calibration archive missing or invalid"


def _calibration_replays(
    run: PublicationRun,
) -> tuple[dict[str, Any], dict[str, tuple[GateResult, GateResult]]]:
    """Capture originals once; a missing optional projection cannot hide them."""
    root = run.bundle.run_dir
    try:
        body = artifact_bytes(root, root / "grading/disclosure/g1-entries.json")
    except FileNotFoundError:
        source = {}
    else:
        try:
            source = g1_entries(body, projection=True)
        except KeyLeakError:
            raise
        except BenchmarkValidationError:
            source = {}
    archives, archive_problem = _calibration_archives(run)
    entries, replays = {}, {}
    for key in source.keys() | (archives.keys() if archives is not None else set()):
        originals = archives.get(key, []) if archives is not None else []
        entry = source.get(key, originals[0] if originals else None)
        try:
            if not isinstance(key, str) or not isinstance(entry, dict):
                raise ValueError("invalid calibration projection entry")
            binding = G1Binding(
                {j: entry["binding"]["composition"][j] for j in key.split("+")}, run.inputs,
            )
            panel, probes, criteria = (
                CapturedInput.from_bytes(run.frozen[name], name, name)
                for name in ("panel", "probes", "criteria")
            )
            if archive_problem or archives is not None and key not in archives:
                raise ValueError("declared calibration original unavailable")
            problems = {}
            originals = archives[key] if archives is not None else []
            for original in originals:
                if not isinstance(original, dict) or (
                    any(not _same(entry.get(f), original.get(f)) for f in ("binding", "scores"))
                    or not _same(
                        _gate(entry.get("reference_check"), "reference_probe_check").details.get(
                            "scores"
                        ),
                        _gate(original.get("reference_check"), "reference_probe_check").details.get(
                            "scores"
                        ),
                    )
                ):
                    raise ValueError("calibration projection differs from original score evidence")
                if entry.get("tuning") != original.get("tuning"):
                    problems["tuning"] = "tuning disclosure differs from original archive"
                if entry.get("source") != original.get("source") or (
                    _gate(entry.get("gate"), "G1").details.get("held_out_test_retest")
                    != _gate(original.get("gate"), "G1").details.get("held_out_test_retest")
                ):
                    problems["held_out_test_retest"] = (
                        "held-out disclosure differs from original archive"
                    )
            selected, reference = replay_g1(
                {key: entry}, binding, panel=panel, probes=probes, criteria=criteria,
                history_problems=problems,
            )
            for original in originals:
                if not _same(original, entry):
                    # Projection mismatches can suppress auxiliary history, but
                    # never primary-source verification. Equal copies were just
                    # replayed above. All source proof is portable captured bytes.
                    replay_g1(
                        {key: original}, binding, panel=panel, probes=probes, criteria=criteria,
                    )
        except KeyLeakError:
            raise
        except (BenchmarkValidationError, KeyError, TypeError, ValueError):
            selected = GateResult("G1", None, {
                "reason": "unverified calibration evidence",
                "calibration_unknown": {
                    "tuning": "inconsistent calibration source evidence",
                    "held_out_test_retest": "inconsistent calibration source evidence",
                },
            })
            reference = GateResult("reference_probe_check", None, {})
        replays[key] = selected, reference
        entries[key] = (
            {**entry, "gate": {"passed": selected.passed, "details": selected.details}}
            if isinstance(entry, dict)
            else {"gate": {"passed": None, "details": {}}}
        )
    return entries, replays


def reconcile_disclosures(run: PublicationRun) -> None:
    """Replace diagnostic caches using the same capture as the headline gate replay."""
    run.g1_entries, run.g1_replays = _calibration_replays(run)
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
        composition: dict[str, dict[str, str | None]] = {}
        for judge, effort in efforts.items():
            provider = run.judges.get(judge, {}).get("provider")
            model_id = run.judges.get(judge, {}).get("model_id")
            if not isinstance(provider, str) or not isinstance(model_id, str) or (
                effort is not None and not isinstance(effort, str)
            ):
                return
            providers[judge] = provider
            composition[judge] = {"model_id": model_id, "effort": effort}
        sample_ids = []
        try:
            sample_ids, info = registered_g3_sample(
                captured.answers, run.transcripts, captured.questions, list(efforts), seed=run.seed
            )
            run.sample_allocation = {**info, "ids": sample_ids}
        except (BenchmarkValidationError, KeyError, ValueError, TypeError):
            pass  # Incomplete panels cannot establish the registered sample.
        retest_unavailable = None
        try:
            pass2 = _second_pass(run, efforts)
        except KeyLeakError:
            raise
        except MissingIssuedVote as error:
            pass2 = {}
            run.retest_unknown = True
            retest_unavailable = str(error)
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
            retest_unavailable = "unverified pass-2 evidence"
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
                composition,
                {av.item_id: av for av in captured.answers.values()},
            ),
            seed=run.seed,
            retest_unavailable=retest_unavailable,
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
    except (KeyError, TypeError, ValueError):
        # These identities and frozen inputs are shared by every gate.
        return gates

    reference = GateResult("reference_probe_check", None, {})
    try:
        if hasattr(run, "g1_replays"):
            entries = run.g1_entries
            selected, check = run.g1_replays.get(
                str(summary.get("composition")),
                (
                    GateResult("G1", None, {"reason": "missing calibration evidence"}),
                    GateResult("reference_probe_check", None, {}),
                ),
            )
        else:
            # Standalone callers use the same archive rules, captured once here.
            entries, replays = _calibration_replays(run)
            selected, check = replays.get(
                composition_name,
                (GateResult("G1", None, {}), GateResult("reference_probe_check", None, {})),
            )
        entry = entries.get(composition_name, {})
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
    except KeyLeakError:
        raise
    except (KeyError, TypeError, ValueError, BenchmarkValidationError):
        gates["G1"] = GateResult("G1", None, {"reason": "missing or inconsistent G1 evidence"})

    captured = run.grade_capture
    question_of = {av.item_id: key[1] for key, av in captured.answers.items()}
    try:
        own = {}
        for key, av in captured.answers.items():
            question = captured.questions[key[1]]
            scores = {j: own_grade(av, j, question.weights) for j in efforts}
            if not av.suspect and sum(s is not None for s in scores.values()) >= 2:
                own[av.item_id] = scores
        gates["G2"] = _consistent(saved["G2"], g2(own, question_of, seed=run.seed))
    except KeyLeakError:
        raise
    except (KeyError, TypeError, ValueError, ZeroDivisionError, BenchmarkValidationError):
        gates["G2"] = GateResult("G2", None, {"reason": "missing or inconsistent G2 evidence"})

    try:
        pairs = {}
        for key, av in captured.answers.items():
            final = captured.finals.get(key)
            if (final is not None and final["method"] == LLM_PANEL
                    and not av.suspect and av.reference is not None):
                pairs[av.item_id] = final["score"], grade(
                    av.reference.fields(), captured.questions[key[1]].weights
                )[0]
        gates["G3"] = _consistent(
            saved["G3"],
            registered_g3(
                run.reference_sample,
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
    except KeyLeakError:
        raise
    except (KeyError, TypeError, ValueError, ZeroDivisionError, BenchmarkValidationError):
        gates["G3"] = GateResult("G3", None, {"reason": "missing or inconsistent G3 evidence"})

    try:
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
            question = captured.questions[key[1]]
            final = captured.finals.get(key)
            if final is not None:
                transcript = run.transcripts[key]
                coord = evidence_cell(transcript, key[1])
                for j in efforts:
                    judge_scores[j][coord] = judge_grade(av, j, question, final)
        cells = run.plan.get("cells")
        systems_of: dict[str, set[str]] = defaultdict(set)
        for row in [*(cells if isinstance(cells, list) else []), *run.transcripts.values()]:
            if isinstance(row, dict):
                model, system = row.get("model_family_id"), row.get("system_id")
                if isinstance(model, str) and isinstance(system, str):
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
        planned = {
            cid: {m: comparison_plan(cells, run.transcripts, model=m, systems=("python-docs", peer))
                  for m in models}
            for cid, peer, models in (("C2", "docs-fetch", c2_models), ("C3", "no-docs", c3_models))
        }

        def claims(scores: dict[Any, Any]) -> dict[str, dict[str, Any]]:
            def claim(cid: str, model: str) -> dict[str, Any]:
                cohort = planned[cid][model]
                if cohort is None:
                    return asdict(ClaimResult(
                        cid, model, None, None,
                        "planned comparison differs from captured trajectories",
                    ))
                return asdict(
                    c2(tokens, scores, cohort, model=model, seed=run.seed) if cid == "C2" else
                    c3(scores, cohort, model=model, seed=run.seed, offline_ok=offline)
                )

            return {
                "C2": {m: claim("C2", m) for m in c2_models},
                "C3": {m: claim("C3", m) for m in c3_models},
            }

        panel_claims = claims(panel_scores)
        by_judge = {
            j: panel_claims if scores == panel_scores else claims(scores)
            for j, scores in judge_scores.items()
        }
        def admitted(raw: Any) -> Any:
            # An identity-ineligible comparison supplies no G4 verdict. Its old
            # cached result cannot veto an independently matched comparison.
            # Keep all other keys and values, including unexpected claims.
            if not isinstance(raw, dict):
                return raw
            return {
                cid: {m: c for m, c in by.items()
                      if m not in planned.get(cid, {}) or planned[cid][m] is not None}
                if isinstance(by, dict) else by
                for cid, by in raw.items()
            }

        saved_single = summary.get("claims_by_judge")
        matched_claims = (
            jsonable(admitted(panel_claims)) == admitted(summary.get("claims"))
            and isinstance(saved_single, dict)
            and jsonable({j: admitted(cs) for j, cs in by_judge.items()})
            == {j: admitted(cs) for j, cs in saved_single.items()}
        )
        run.summary["claims"] = jsonable(panel_claims)
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
        gates["G4"] = GateResult(
            "G4", None,
            {**gates["G4"].details, "reason": "missing or inconsistent G4 evidence"},
        )
    return gates
