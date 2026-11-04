"""``agentic-score``: grades, gates, claims and disclosure for a graded run (design §9).

``score_run_dir`` reads a run directory only: its transcripts, scoring and token
records, ``planned-cells.json``, the frozen ``grading/inputs/`` and the vote
records. The three workspace steps build the probe file, the tuning evidence and
the G1 evidence. Nothing here makes a network call.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.agentic.egress import read_log_at, write_new
from benchmarks.agentic.inputs import (
    CapturedInput,
    CapturedJSON,
    json_mapping,
    read_input_bytes,
    read_json,
)
from benchmarks.agentic.protocol import (
    MAX_FRAME_BYTES,
    KeyLeakError,
    assert_no_key_shaped_json,
    scanned_json_bytes,
)
from benchmarks.grading.aggregate import (
    LLM_PANEL,
    chosen_vote_file,
    final_grade,
    judge_grade,
    load_answer_votes,
    parsed_from_record,
    recorded_result,
    vote_from_record,
)
from benchmarks.grading.aggregate import own_grade as _own
from benchmarks.grading.batch import BatchRequest
from benchmarks.grading.calibration import g1_entries, replay_g1, verified_tuning
from benchmarks.grading.calibration import judge_settings as _judge_settings
from benchmarks.grading.calibration_history import (
    CalibrationSource,
    held_out_scores,
    repeat_problems,
    tuning_summary,
)
from benchmarks.grading.calibration_scan import scan_calibration_sources
from benchmarks.grading.calibration_storage import (
    MAX_CALIBRATION_BYTES,
    calibration_bytes,
    check_calibration_metadata,
)
from benchmarks.grading.diagnostics import diagnostic_tables, missing_records
from benchmarks.grading.disclosure import ledger_spend, verified_key_audit
from benchmarks.grading.gates import (
    BINDING_INPUTS,
    CellKey,
    ClaimResult,
    GateResult,
    c2,
    c3,
    g1,
    g2,
    g4,
    outcome_tier,
    reference_probe_check,
    registered_g3,
    registered_g3_sample,
    run_binding,
    run_offline_evidence,
)
from benchmarks.grading.grounding import is_grounded
from benchmarks.grading.inputs import (
    REPO_ROOT,
    SNAPSHOT_FILES,
    TUNING_PATH,
    GradingQuestion,
    JudgeInput,
    answer_requests,
    criteria_from_mapping,
    safe_id,
    scanned_input,
    tuning_questions_from_mapping,
)
from benchmarks.grading.mapping import Vote, grade
from benchmarks.grading.panel import (
    JudgeSpec,
    Panel,
    composition_key,
    panel_from_mapping,
)
from benchmarks.grading.probes import (
    MAX_ROUNDS,
    Probe,
    ProbeFile,
    assemble,
    assignments,
    check_request,
    generation_request,
    probe_file_from_inputs,
    reference_subset,
    write_probe_file,
)
from benchmarks.grading.serialization import jsonable as _jsonable
from benchmarks.grading.stats import GATE_RESAMPLES
from benchmarks.grading.vote_capture import MissingIssuedVote, VoteCapture
from benchmarks.report_files import (
    artifact_bytes,
    artifact_output_dirs,
    artifact_paths,
    check_output_paths,
)
from benchmarks.report_measurements import comparison_plan
from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import apply_llm_grades, score_run

CORPUS_PATH = REPO_ROOT / "docs" / "benchmarks" / "corpus.yml"
CORPUS_SCHEMA_PATH = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"


def _run_json(run_dir: Path, filename: str) -> dict[str, Any] | None:
    try:
        content = artifact_bytes(run_dir, run_dir / filename)
    except FileNotFoundError:
        return None
    return json_mapping(content, filename)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_bytes(path: Path, data: Any, *, calibration: bool = False) -> bytes:
    if calibration:
        if isinstance(data, list):
            entries = {"tuning": {"tuning": data}}
        elif path.name == "g1-entries.json":
            entries = data
        else:
            entries = data.get("entries", {"tuning": data})
        check_calibration_metadata(entries)
        scan_calibration_sources({"entries": entries})
    body = scanned_json_bytes(_jsonable(data), where="grading evidence", indent=2, sort_keys=True)
    if len(body) + 1 > (MAX_CALIBRATION_BYTES if calibration else MAX_FRAME_BYTES):
        raise BenchmarkValidationError("grading evidence exceeds the artifact size limit")
    return body


def _write(root: Path, path: Path, data: Any, *, calibration: bool = False) -> Any:
    body = _evidence_bytes(path, data, calibration=calibration)
    with artifact_output_dirs(root, [path]) as directories:
        write_new(directories[path.parent], path.name, body + b"\n", mode=0o644)
    return json.loads(body)


def _read_g1(path: Path) -> tuple[bytes, dict[str, Any]]:
    body = calibration_bytes(path.parent, path)
    return body, g1_entries(body)


def _g1_inputs(
    paths: Sequence[Path],
) -> tuple[dict[str, Any], dict[str, bytes], list[dict[str, str]]]:
    entries: dict[str, Any] = {}
    bodies: dict[str, bytes] = {}
    copies: list[dict[str, str]] = []
    for path in paths:
        body, supplied = _read_g1(path)
        for key, entry in supplied.items():
            if not isinstance(entry, dict):
                raise BenchmarkValidationError(f"G1 entry {key} must be an object")
            if key in entries and json.dumps(entries[key], sort_keys=True) != json.dumps(
                entry, sort_keys=True
            ):
                raise BenchmarkValidationError(f"conflicting G1 evidence for composition {key}")
            entries[key] = entry
        sha = hashlib.sha256(body).hexdigest()
        if sha not in bodies and sum(map(len, bodies.values())) + len(body) > MAX_CALIBRATION_BYTES:
            raise BenchmarkValidationError("combined G1 evidence exceeds the artifact size limit")
        bodies[sha] = body
        copies.append({"source": str(path), "path": f"disclosure/g1/{sha}.json", "sha256": sha})
    return entries, bodies, copies


def _new_g1_archives(root: Path, raw_dir: Path, bodies: Mapping[str, bytes]) -> dict[str, bytes]:
    missing = {}
    for sha, body in bodies.items():
        try:
            archived = calibration_bytes(root, raw_dir / f"{sha}.json")
        except FileNotFoundError:
            missing[sha] = body
        else:
            if archived != body:
                raise BenchmarkValidationError("archived G1 bytes differ from their digest")
    return missing


def _publish_g1_archives(root: Path, raw_dir: Path, missing: Mapping[str, bytes]) -> None:
    if not missing:
        return
    paths = [raw_dir / f"{sha}.json" for sha in missing]
    with artifact_output_dirs(root, paths) as directories:
        directory = directories[raw_dir]
        for sha, body in missing.items():
            name = f"{sha}.json"
            staged = f".{name}.{secrets.token_hex(8)}.tmp"
            write_new(directory, staged, body, mode=0o644)
            try:
                # Publish complete immutable bytes without replacing a concurrent archive.
                try:
                    os.link(staged, name, src_dir_fd=directory, dst_dir_fd=directory)
                except FileExistsError:
                    if read_log_at(directory, name, max_bytes=len(body)) != body:
                        raise BenchmarkValidationError("archived G1 bytes differ from their digest")
            finally:
                os.unlink(staged, dir_fd=directory)


def _cells(base: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for path in artifact_paths(base.parent, base, "*/*.json"):
        data = json_mapping(artifact_bytes(base.parent, path), "run cell")
        key = path.parent.name, path.stem
        if key != (data.get("competitor_id"), data.get("corpus_id")):
            raise BenchmarkValidationError(f"{base.name} cell coordinates differ from its path")
        records[key] = data
    return records


def _claim(result: ClaimResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "model": result.model,
        "passed": result.passed,
        "interval": None if result.interval is None else asdict(result.interval),
        "reason": result.reason,
    }


def _gate(result: GateResult) -> dict[str, Any]:
    return {"passed": result.passed, "details": result.details}


def _ledger_events(grading: Path) -> list[dict[str, Any]] | None:
    path = grading / "ledger-events.jsonl"
    try:
        body = artifact_bytes(grading, path)
    except FileNotFoundError:
        return None
    return [
        json_mapping(line, "grading ledger") for line in body.splitlines() if line.strip()
    ]


def _ledger_spend(grading: Path, graded: int | None) -> dict[str, Any]:
    return ledger_spend(_ledger_events(grading), graded)


def _frozen_inputs(
    root: Path, *, criteria_path: Path | None, panel_path: Path | None
) -> tuple[dict[str, str], dict[str, CapturedInput], dict[str, bytes]]:
    """Capture each snapshot once; path overrides are byte-for-byte assertions."""

    def capture(path: Path) -> bytes:
        try:
            return artifact_bytes(root.parent, path)
        except FileNotFoundError:
            raise BenchmarkValidationError(f"missing grading input {path.name}") from None

    manifest = json_mapping(capture(root / "inputs.json"), "grading input manifest")
    if manifest.keys() - SNAPSHOT_FILES.keys():
        raise BenchmarkValidationError("unrecognized entries in grading input manifest")
    overrides = {"criteria": criteria_path, "panel": panel_path}
    settings = {}
    bodies = {}
    for name, filename in SNAPSHOT_FILES.items():
        path = root / filename
        if name == "probes" and name not in manifest and not path.exists():
            continue  # An older run without probes can still be scored without G1.
        body = bodies[name] = capture(path)
        if name in {"criteria", "panel", "probes"}:
            settings[name] = CapturedInput.from_bytes(body, name, name)
        elif path.suffix == ".json":
            json_mapping(body, name)
        if hashlib.sha256(body).hexdigest() != manifest.get(name):
            raise BenchmarkValidationError(f"frozen {name} differs from the grading input manifest")
        override = overrides.get(name)
        if (
            override is not None
            and override != path
            and scanned_input(override, suffix=path.suffix) != body
        ):
            raise BenchmarkValidationError(f"--{name} differs from the frozen {name}")

    return manifest, settings, bodies


def score_run_dir(
    run_dir: Path,
    *,
    criteria_path: Path | None = None,
    panel_path: Path | None = None,
    schema_path: Path,
    g1_paths: Sequence[Path] = (),
    tuning_path: Path = TUNING_PATH,
    resamples: int = GATE_RESAMPLES,
) -> dict[str, Any]:
    """Score, grade, gate and claim one run; write ``grading/summary.json`` and disclosure."""
    if resamples != GATE_RESAMPLES:
        raise BenchmarkValidationError(
            f"gate scoring requires {GATE_RESAMPLES} bootstrap resamples"
        )
    grading = run_dir / "grading"
    if grading.is_symlink():
        raise BenchmarkValidationError("grading directory must be within the run")
    environment = _run_json(run_dir, "environment.json")
    if environment is None or environment.get("mode") != "agentic":
        raise BenchmarkValidationError("agentic scoring requires mode agentic in environment.json")
    run_summary = _run_json(run_dir, "run-summary.json")
    if run_summary is not None and run_summary.get("mode") != "agentic":
        raise BenchmarkValidationError("run-summary.json disagrees with the agentic run mode")
    seed = str(environment.get("seed"))
    root = grading / "inputs"
    manifest, settings, frozen = _frozen_inputs(
        root, criteria_path=criteria_path, panel_path=panel_path
    )
    questions = criteria_from_mapping(
        settings["criteria"].mapping,
        root / SNAPSHOT_FILES["criteria"],
        corpus_path=run_dir / "snapshots/corpus.yml",
        corpus_body=artifact_bytes(run_dir, run_dir / "snapshots/corpus.yml"),
        schema_path=schema_path,
    )
    panel = panel_from_mapping(settings["panel"].mapping, root / SNAPSHOT_FILES["panel"])
    vote_capture = VoteCapture(grading)
    binding = run_binding(run_dir, panel, inputs=manifest, runs=vote_capture.runs)
    entries, bodies, copies = _g1_inputs(g1_paths)
    if entries and binding.missing_field():
        raise BenchmarkValidationError(f"G1 input cannot bind missing {binding.missing_field()}")
    key = composition_key(list(binding.composition))
    entry = entries.get(key, {})
    g1_result = GateResult("G1", None, {"reason": "no G1 evidence was given (--g1)"})
    reference_check = GateResult("reference_probe_check", None, {})
    if entries:
        g1_result, reference_check = replay_g1(
            entries,
            binding,
            panel=settings["panel"],
            probes=settings["probes"],
            criteria=settings["criteria"],
            tuning=(
                CapturedInput.read(tuning_path, "tuning", "tuning questions")
                if entry and tuning_path != TUNING_PATH else None
            ),
        )

    # Keep the entry projection identical to the captured source. Replayed verdicts
    # and auxiliary availability belong to the summary, never a second history.

    disclosure = grading / "disclosure"
    raw_g1 = disclosure / "g1"
    if any(p.is_symlink() for p in (grading, disclosure, raw_g1)):
        raise BenchmarkValidationError("G1 disclosure directory must be within the run")
    # Include removals and immutable archives: none may fail after scoring has changed.
    check_output_paths(
        run_dir,
        [
            grading / "summary.json",
            disclosure / "breakdowns.json",
            disclosure / "g1-entries.json",
            disclosure / "tuning.json",
            *(raw_g1 / f"{sha}.json" for sha in bodies),
        ],
    )
    missing_archives = _new_g1_archives(run_dir, raw_g1, bodies)
    # Validate and serialize the combined projection before any scoring mutation.
    # Individually bounded imports can still exceed the combined export budget.
    calibration_outputs = {
        path: _evidence_bytes(path, value, calibration=True) if value else None
        for path, value in (
            (disclosure / "g1-entries.json", entries),
            (disclosure / "tuning.json", entry.get("tuning")),
        )
    }
    efforts = {judge: spec.get("effort") for judge, spec in binding.composition.items()}
    transcripts = _cells(run_dir / "transcripts")
    requests = answer_requests(
        questions, transcripts, prompt=frozen["judge_prompt"], schema=frozen["judge_schema"]
    )
    for target, number in (("answers", 1), ("reference", 1)):
        vote_capture.check(target, number)
    answer_votes = load_answer_votes(
        grading, panel=panel, judges=efforts, requests=requests, capture=vote_capture,
    )
    retest_unavailable = None
    try:
        vote_capture.check("answers", 2)
    except MissingIssuedVote as error:
        retest_unavailable = str(error)
    pass2 = load_answer_votes(
        grading,
        panel=panel,
        judges=efforts,
        pass_number=2,
        requests=requests,
        include_reference=False,
        capture=vote_capture,
        allow_missing_repeats=True,
    )
    if retest_unavailable is not None:
        # Validate the remaining selected replies from the same capture, but do
        # not present incomplete issued repeats as a smaller observed sample.
        pass2 = {}

    sample_path = grading / "reference-sample.json"
    try:
        sample = json_mapping(artifact_bytes(run_dir, sample_path), "reference sample")
    except FileNotFoundError:
        sample = None
    sample_ids = [] if sample is None else sample.get("ids")
    if not isinstance(sample_ids, list) or not all(isinstance(i, str) for i in sample_ids):
        raise BenchmarkValidationError("reference-sample.json must list answer IDs")

    # 2. LLM grades: an item waits until every panel judge has a final pass-1 record.
    finals: dict[tuple[str, str], dict[str, Any]] = {}
    for key, av in answer_votes.items():
        complete = all(av.records.get(j, {}).get("final") for j in efforts)
        final = final_grade(av, questions[av.corpus_id], inputs=manifest) if complete else None
        if final is not None:
            assert_no_key_shaped_json(final, where="cell grade")
            finals[key] = final
    grounded = {
        key: is_grounded(t.get("answer") or "", t.get("tool_calls") or [])
        for key, t in transcripts.items()
    }
    score_run(run_dir, schema_path=schema_path, current_llm_cells=finals.keys())
    apply_llm_grades(run_dir, finals, grounded=grounded, schema_path=schema_path)
    scoring = _cells(run_dir / "scoring")
    pending = sorted(f"{c}/{q}" for (c, q), r in scoring.items() if r.get("score") is None)

    by_item = {av.item_id: av for av in answer_votes.values()}
    question_of = {av.item_id: av.corpus_id for av in answer_votes.values()}
    weights = {qid: q.weights for qid, q in questions.items()}

    # 3. G2 uses independent pass-1 votes even while the final grade is pending.
    panel_votes = {
        av.item_id: {j: _own(av, j, weights[av.corpus_id]) for j in efforts}
        for k, av in answer_votes.items()
        if k in transcripts and not av.suspect
    }
    agreement = {
        item: votes
        for item, votes in panel_votes.items()
        if sum(v is not None for v in votes.values()) >= 2
    }
    g2_result = g2(agreement, question_of, seed=seed, resamples=resamples)

    # 4. Reconstruct the registered cohort independently of saved reference outcomes.
    pairs: dict[str, tuple[float, float]] = {}
    for k, final in finals.items():
        av = answer_votes[k]
        if final["method"] == LLM_PANEL and not av.suspect and av.reference is not None:
            pairs[av.item_id] = (
                final["score"],
                grade(av.reference.fields(), weights[av.corpus_id])[0],
            )
    if sample is not None:
        g3_result = registered_g3(
            sample,
            pairs,
            question_of,
            answers=answer_votes,
            transcripts=transcripts,
            questions=questions,
            judges=list(efforts),
            seed=seed,
            reference_check=reference_check,
            resamples=resamples,
        )
    else:
        g3_result = GateResult(
            "G3", None, {"reason": "G3 was not evaluated: the run has no reference sample."}
        )
    # 6. Claims under the panel and under each single judge, then G4.
    plan = _run_json(run_dir, "planned-cells.json") or {}
    plan_cells = plan.get("cells")
    key_of: dict[tuple[str, str], CellKey] = {
        k: (t["system_id"], t["model_family_id"], t["corpus_id"], int(t["repeat"]))
        for k, t in transcripts.items()
    }
    tokens: dict[CellKey, int | None] = {}
    for k, record in _cells(run_dir / "tokens").items():
        if k in key_of:
            counted = record.get("status") == "counted"
            tokens[key_of[k]] = record.get("client_wrapped_tokens") if counted else None
    panel_scores: dict[CellKey, float | None] = {
        key_of[k]: r.get("score") for k, r in scoring.items() if k in key_of
    }
    judge_scores: dict[str, dict[CellKey, float | None]] = {}
    for judge in efforts:
        scores = dict(panel_scores)
        for k, final in finals.items():
            av = answer_votes[k]
            if k in key_of:
                scores[key_of[k]] = judge_grade(av, judge, questions[av.corpus_id], final)
        judge_scores[judge] = scores

    families = _model_families(run_dir)
    airgap = {fid for fid, f in families.items() if f.get("arm") == "air-gapped"}
    systems_of: dict[str, set[str]] = defaultdict(set)
    for row in [*(plan_cells if isinstance(plan_cells, list) else []), *transcripts.values()]:
        if isinstance(row, dict):
            model, system = row.get("model_family_id"), row.get("system_id")
            if isinstance(model, str) and isinstance(system, str):
                systems_of[model].add(system)
    c2_models = sorted(m for m, s in systems_of.items() if {"python-docs", "docs-fetch"} <= s)
    c3_models = sorted(
        m for m, s in systems_of.items() if {"python-docs", "no-docs"} <= s and m in airgap
    )
    offline: dict[str, Any] | None = None
    offline_ok: bool | None = None
    if c3_models:
        current_offline = run_offline_evidence(run_dir, families)
        offline_ok = current_offline.verdict.ok
        offline = current_offline.summary()

    def admitted_plans(excluded: Sequence[str] = ()) -> dict[str, dict[str, set[CellKey] | None]]:
        return {
            cid: {m: comparison_plan(
                plan_cells, transcripts, model=m, systems=("python-docs", peer), excluded=excluded,
            ) for m in models}
            for cid, peer, models in (("C2", "docs-fetch", c2_models), ("C3", "no-docs", c3_models))
        }

    def claims_for(
        scores: Mapping[CellKey, float | None],
        cells: Mapping[str, Mapping[str, set[CellKey] | None]],
    ) -> dict[str, dict[str, ClaimResult]]:
        def claim(cid: str, model: str) -> ClaimResult:
            planned = cells[cid][model]
            if planned is None:
                return ClaimResult(cid, model, None, None,
                                   "planned comparison differs from captured trajectories")
            return (
                c2(tokens, scores, planned, model=model, seed=seed, resamples=resamples)
                if cid == "C2" else
                c3(scores, planned, model=model, seed=seed,
                   offline_ok=offline_ok, resamples=resamples)
            )

        return {
            "C2": {m: claim("C2", m) for m in c2_models},
            "C3": {m: claim("C3", m) for m in c3_models},
        }

    planned = admitted_plans()
    panel_claims = claims_for(panel_scores, planned)
    judge_claims = {
        j: panel_claims if s == panel_scores else claims_for(s, planned)
        for j, s in judge_scores.items()
    }

    def verdicts_of(claims: Mapping[str, Mapping[str, ClaimResult]]) -> dict[str, bool | None]:
        return {f"{cid}/{m}": r.passed for cid, by in claims.items() for m, r in by.items()}

    g4_result = g4(
        {"panel": verdicts_of(panel_claims)}
        | {j: verdicts_of(claims) for j, claims in judge_claims.items()}
    )

    # 7. The key audit: the reference's verdict on each answer key.
    reference = panel.judges[panel.reference]
    ledger_events = _ledger_events(grading)
    try:
        audit = verified_key_audit(
            run_dir,
            questions=questions,
            reference=reference.id,
            judge=reference.raw,
            inputs=manifest,
            prompt=frozen["key_audit_prompt"],
            events=ledger_events or [],
            runs=vote_capture.runs,
        )
    except KeyLeakError:
        raise
    except (FileNotFoundError, KeyError, TypeError, ValueError, BenchmarkValidationError):
        audit = {"verified": False, "audited": None, "flagged": None}
    flagged = audit["flagged"] or []
    without_flagged = None
    if flagged:
        kept_scores = {k: v for k, v in panel_scores.items() if k[2] not in flagged}
        without_flagged = claims_for(kept_scores, admitted_plans(flagged))

    # 8. The tier and the diagnostics.
    tier = outcome_tier(g1_result, g2_result, g3_result, g4_result)
    # Descriptive G3 tables use the registered membership too, even when a
    # contradictory saved selection withholds the gate's eligibility.
    try:
        diagnostic_ids, _ = registered_g3_sample(
            answer_votes, transcripts, questions, list(efforts), seed=seed
        )
    except (BenchmarkValidationError, KeyError, TypeError, ValueError):
        diagnostic_ids = []
    diagnostics, breakdowns = diagnostic_tables(
        answer_votes,
        pass2,
        finals,
        transcripts,
        scoring,
        questions,
        efforts,
        {j: panel.judges[j].provider for j in efforts},
        families,
        diagnostic_ids,
        missing_records(list(vote_capture.runs.values()), binding.composition, by_item),
        seed=seed,
        resamples=resamples,
        retest_unavailable=retest_unavailable,
    )
    _write(run_dir, disclosure / "breakdowns.json", breakdowns)
    for path, body in calibration_outputs.items():
        if body is not None:
            with artifact_output_dirs(run_dir, [path]) as directories:
                write_new(directories[path.parent], path.name, body + b"\n", mode=0o644)
        elif path.exists():
            with artifact_output_dirs(run_dir, [path]) as directories:
                os.unlink(path.name, dir_fd=directories[path.parent])
    _publish_g1_archives(run_dir, raw_g1, missing_archives)

    summary = {
        "run_dir": str(run_dir),
        "seed": seed,
        "resamples": resamples,
        "tier": tier,
        "composition": composition_key(list(binding.composition)),
        "efforts": efforts,
        "gates": {
            "G1": _gate(g1_result),
            "G2": _gate(g2_result),
            "G3": _gate(g3_result),
            "G4": _gate(g4_result),
        },
        "reference_check": _gate(reference_check),
        "claims": {cid: {m: _claim(r) for m, r in by.items()} for cid, by in panel_claims.items()},
        "claims_by_judge": {
            j: {cid: {m: _claim(r) for m, r in by.items()} for cid, by in claims.items()}
            for j, claims in judge_claims.items()
        },
        "judge_sensitive": g4_result.details.get("judge_sensitive", []),
        "key_audit": audit,
        "claims_without_flagged_keys": None
        if without_flagged is None
        else {cid: {m: _claim(r) for m, r in by.items()} for cid, by in without_flagged.items()},
        "offline": offline,
        "graded_cells": len(finals),
        "pending_cells": pending,
        "diagnostics": diagnostics,
        "breakdowns": breakdowns,
        "spend": ledger_spend(ledger_events, len(finals)),
        "disclosure": {"g1_files": copies},
    }
    return _write(run_dir, grading / "summary.json", summary)


def _model_families(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "snapshots" / "model-matrix.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {str(f["id"]): f for f in (data or {}).get("model_families") or []}


def _workspace(path: Path, kind: str) -> dict[str, Any]:
    meta_path = path / "workspace.json"
    if not meta_path.is_file():
        raise BenchmarkValidationError(f"{path} is not a grading workspace (no workspace.json)")
    meta = read_json(meta_path, "grading workspace")
    actual = meta.get("kind")
    if actual not in ("probe-build", "tuning", "held-out"):
        raise BenchmarkValidationError("unrecognized grading workspace kind")
    if actual != kind:
        raise BenchmarkValidationError(f"{actual} workspace cannot be used for {kind}")
    return meta


def _record_vote(record: Mapping[str, Any] | None) -> Vote | None:
    if record is None or record.get("withheld") is not None or record.get("vote") is None:
        return None
    return Vote(**record["vote"])


def _probe_record(
    root: Path,
    *,
    target: str,
    judge: JudgeSpec,
    effort: str | None,
    pass_number: int,
    item_id: str,
    request: JudgeInput | None = None,
    question_id: str | None = None,
    item_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Select the original producer's record, verifying any supplied judge request."""
    selected = chosen_vote_file(
        root,
        target=target,
        judge_id=judge.id,
        effort=effort,
        pass_number=pass_number,
        item_id=item_id,
    )
    if selected is None:
        return None
    record = selected[1]
    identity = {
        "target": target,
        "judge_id": judge.id,
        "model_id": judge.model_id,
        "effort": effort,
        "pass": pass_number,
        "item_id": item_id,
    }
    if request is not None:
        identity["input_sha256"] = request.sha256()
    if any(type(record.get(k)) is not type(v) or record.get(k) != v for k, v in identity.items()):
        raise BenchmarkValidationError(f"foreign vote record for {target}/{judge.id}/{item_id}")
    if request is not None:
        path = root / "items" / target / f"{item_id}.json"
        try:
            document = json_mapping(artifact_bytes(root, path), "calibration item")
        except FileNotFoundError:
            raise BenchmarkValidationError(
                f"missing calibration input document for {target}/{item_id}; "
                "legacy records without request proof cannot establish a verdict"
            ) from None
        expected = request.item_evidence(
            {"question_id": question_id, "suspect": request.suspect}
            if item_meta is None else item_meta
        )
        if json.dumps(document, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise BenchmarkValidationError(f"calibration input document differs for {item_id}")
        result = recorded_result(root, record, judge, vote_sha256=selected[2])
        if item_meta is not None:
            parsed = parsed_from_record(record, request=request, result=result)
            record = {**record, "parsed": parsed}
        else:
            if record.get("parsed") is not None:
                raise BenchmarkValidationError(f"unexpected parsed calibration reply for {item_id}")
            vote_from_record(record, request=request, result=result)
    return record


def probe_build_reply(
    root: Path, *, target: str, judge: JudgeSpec, request: BatchRequest,
    question_id: str, probe_type: str, round_number: int,
) -> dict[str, Any]:
    """Select and replay one probe item; absent or honestly withheld data has no object."""
    record = _probe_record(
        root, target=target, judge=judge, effort=judge.effort, pass_number=1,
        item_id=request.item_id,
        request=JudgeInput(request.system, request.user, request.schema, False),
        item_meta={"question_id": question_id, "probe_type": probe_type, "round": round_number},
    )
    return {} if record is None or record["parsed"] is None else record["parsed"]


def probe_build_round(
    root: Path, *, question: GradingQuestion, probe_type: str, round_number: int,
    generator: JudgeSpec, checker: JudgeSpec, spec_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The assembler and producer replay the same selected generation/check pair."""
    if generator.provider == checker.provider:
        raise BenchmarkValidationError("probe checker must belong to another provider")
    gen = probe_build_reply(
        root, target="probe-gen", judge=generator,
        request=generation_request(probe_type, question, round_number, spec_text=spec_text),
        question_id=question.id, probe_type=probe_type, round_number=round_number,
    )
    candidate = gen.get("probe_text")
    if not isinstance(candidate, str) or not candidate.strip():
        # A checker cannot approve a missing/refused/invalid candidate.
        selected = chosen_vote_file(
            root, target="probe-check", judge_id=checker.id, effort=checker.effort,
            pass_number=1, item_id=safe_id("chk", probe_type, question.id, f"r{round_number}"),
        )
        if selected is not None:
            raise BenchmarkValidationError("probe checker has no admissible generation")
        return gen, {}
    chk = probe_build_reply(
        root, target="probe-check", judge=checker,
        request=check_request(probe_type, question, candidate, round_number, spec_text=spec_text),
        question_id=question.id, probe_type=probe_type, round_number=round_number,
    )
    return gen, chk


@dataclass(frozen=True)
class _WorkspaceInputs:
    meta: dict[str, Any]
    settings: dict[str, CapturedInput]
    panel: Panel
    tuning: CapturedInput
    bodies: dict[str, bytes]


def _workspace_inputs(
    workspace: Path,
    kind: str,
    panel_path: Path | None,
    criteria_path: Path | None,
    tuning_path: Path,
) -> _WorkspaceInputs:
    meta = _workspace(workspace, kind)
    root = workspace / "inputs"
    manifest, settings, bodies = _frozen_inputs(
        root, criteria_path=criteria_path, panel_path=panel_path
    )
    if meta.get("inputs") != manifest:
        raise BenchmarkValidationError("workspace metadata differs from its grading input manifest")
    tuning = CapturedInput.read(tuning_path, "tuning", "tuning questions")
    # Stage workspaces pin both fields. Earlier pure exports have neither.
    if kind == "held-out" and ("judges" in meta) != ("tuning_sha256" in meta):
        raise BenchmarkValidationError("incomplete held-out workspace metadata")
    if (
        "tuning_sha256" in meta
        and meta["tuning_sha256"] != hashlib.sha256(tuning.content).hexdigest()
    ):
        raise BenchmarkValidationError("workspace tuning questions differ")
    panel = panel_from_mapping(settings["panel"].mapping, root / SNAPSHOT_FILES["panel"])
    return _WorkspaceInputs(meta, settings, panel, tuning, bodies)


def assemble_workspace_probes(
    workspace: Path,
    *,
    criteria_path: Path | None,
    tuning_path: Path,
    panel_path: Path | None,
    index_path: Path,
    corpus_path: Path = CORPUS_PATH,
    schema_path: Path = CORPUS_SCHEMA_PATH,
) -> Path:
    """probe-build workspace: the generation and check votes plus code-built probes."""
    check_output_paths(workspace, [workspace / "probes.yml"])
    inputs = _workspace_inputs(workspace, "probe-build", panel_path, criteria_path, tuning_path)
    panel = inputs.panel
    criteria = inputs.settings["criteria"]
    captured_corpus = workspace / "inputs/corpus.yml"
    corpus_body = (
        artifact_bytes(workspace, captured_corpus)
        if captured_corpus.exists() or captured_corpus.is_symlink() else None
    )
    sets = {
        "held_out": criteria_from_mapping(
            criteria.mapping,
            workspace / "inputs" / SNAPSHOT_FILES["criteria"],
            corpus_path=captured_corpus if corpus_body is not None else corpus_path,
            corpus_body=corpus_body,
            schema_path=schema_path,
        ),
        "tuning": tuning_questions_from_mapping(inputs.tuning.mapping),
    }
    if sets["held_out"].keys() & sets["tuning"].keys():
        raise BenchmarkValidationError("held-out and tuning question ids overlap")
    probes: list[Probe] = []
    dropped: list[dict[str, Any]] = []
    for set_name, questions in sets.items():
        assignment = assignments(sorted(questions), panel.generators)
        generated: dict[tuple[str, str, int], str] = {}
        checks: dict[tuple[str, str, int], bool] = {}
        for (qid, probe_type), (generator, checker) in assignment.items():
            for r in range(1, MAX_ROUNDS + 1):
                gen, chk = probe_build_round(
                    workspace, question=questions[qid], probe_type=probe_type, round_number=r,
                    generator=panel.judges[generator], checker=panel.judges[checker],
                    spec_text=inputs.bodies["probe_spec"].decode("utf-8"),
                )
                if isinstance(value := gen.get("probe_text"), str) and value.strip():
                    generated[(qid, probe_type, r)] = value
                if isinstance(verdict := chk.get("meets_spec"), bool):
                    checks[(qid, probe_type, r)] = verdict
        kept, lost = assemble(
            questions,
            set_name,  # type: ignore[arg-type]
            index_path=index_path,
            generated=generated,
            checks=checks,
            assignment=assignment,
        )
        probes += kept
        dropped += [{**d, "set": set_name} for d in lost]
    header = {
        "criteria_sha256": inputs.meta["inputs"]["criteria"],
        "corpus_sha256": criteria.mapping["corpus_sha256"],
        "tuning_sha256": hashlib.sha256(inputs.tuning.content).hexdigest(),
        "spec_sha256": inputs.meta["inputs"]["probe_spec"],
        "index_sha256": _sha256(index_path),
    }
    path = workspace / "probes.yml"
    write_probe_file(path, ProbeFile(header, probes, dropped))
    return path


def _handoff_inputs(
    workspace: Path, inputs: _WorkspaceInputs
) -> tuple[ProbeFile, list[dict[str, Any]]]:
    meta, settings = inputs.meta, inputs.settings
    manifest = meta["inputs"]
    handoff = meta.get("handoff")
    if (
        not isinstance(handoff, dict)
        or "probes" not in settings
        or (handoff.get("probes.yml") != manifest["probes"])
    ):
        raise BenchmarkValidationError("workspace probes handoff differs from the frozen inputs")
    directory = workspace / "handoff"
    held = read_input_bytes(directory / "probes.yml", "probes", "workspace probe handoff")
    if held != settings["probes"].content:
        raise BenchmarkValidationError("workspace probe handoff differs from the frozen probes")
    evidence = []
    for name in sorted(handoff.keys() - {"probes.yml"}):
        if meta["kind"] != "held-out" or not re.fullmatch(r"tuning-[1-9][0-9]*\.json", name):
            raise BenchmarkValidationError("invalid workspace handoff entry")
        body = calibration_bytes(workspace, directory / name)
        record = CapturedJSON(body, json_mapping(body, "tuning handoff"))
        if hashlib.sha256(record.content).hexdigest() != handoff[name]:
            raise BenchmarkValidationError("workspace tuning handoff differs")
        evidence.append(record.mapping)
    if any(p.name not in handoff for p in directory.glob("tuning-*.json")) or (
        meta["kind"] == "held-out" and "judges" in meta and not evidence
    ):
        raise BenchmarkValidationError("incomplete workspace tuning handoff")
    return probe_file_from_inputs(settings["probes"], settings["criteria"], inputs.tuning), evidence


def write_tuning(
    workspace: Path,
    *,
    panel_path: Path | None = None,
    criteria_path: Path | None = None,
    tuning_path: Path = TUNING_PATH,
) -> Path:
    """tuning workspace: accuracy per judge per effort, and the lowest effort meeting G1's
    per-judge criteria (overall >= 0.90, zero INJ credit)."""
    check_output_paths(workspace, [workspace / "tuning.json"])
    inputs = _workspace_inputs(workspace, "tuning", panel_path, criteria_path, tuning_path)
    suite, _ = _handoff_inputs(workspace, inputs)
    panel = inputs.panel
    probes = [p for p in suite.probes if p.set == "tuning"]
    source = CalibrationSource(workspace)
    bodies = {**inputs.bodies, "tuning": inputs.tuning.content}
    judges = tuning_summary(source, panel, probes, bodies)
    path = workspace / "tuning.json"
    _write(
        workspace,
        path,
        {
            "workspace": str(workspace),
            "inputs": inputs.meta["inputs"],
            "prompt_sha256": inputs.meta["inputs"].get("judge_prompt"),
            "judge_settings": _judge_settings(
                inputs.settings["panel"], list(judges), before_effort_freeze=True
            ),
            "judges": judges,
            "source": source.export(bodies),
        },
        calibration=True,
    )
    return path


def _check_tuning_evidence(
    inputs: _WorkspaceInputs, evidence: Sequence[dict[str, Any]], judges: Sequence[str]
) -> None:
    if "judges" not in inputs.meta and all("inputs" not in record for record in evidence):
        return  # Early pure exports carried only a tuning disclosure.
    manifest = inputs.meta["inputs"]
    settings = _judge_settings(inputs.settings["panel"], judges, before_effort_freeze=True)
    chosen = set()
    for record in evidence:
        recorded = record.get("inputs")
        attempts = record.get("judges")
        if (
            not isinstance(recorded, dict)
            or not isinstance(attempts, dict)
            or record.get("prompt_sha256") != manifest["judge_prompt"]
            or any(recorded.get(k) != manifest[k] for k in BINDING_INPUTS)
        ):
            raise BenchmarkValidationError("tuning evidence differs from the frozen grading inputs")
        for judge in judges:
            entry = attempts.get(judge, {})
            if not isinstance(entry, dict):
                raise BenchmarkValidationError("invalid tuning judge evidence")
            if entry.get("chosen") is None:
                continue
            spec = inputs.panel.judges[judge]
            efforts = entry.get("efforts")
            if not isinstance(efforts, dict) or not all(
                isinstance(v, dict) for v in efforts.values()
            ):
                raise BenchmarkValidationError("invalid tuning effort evidence")
            passing = next(
                (e for e in spec.effort_levels if efforts.get(e, {}).get("meets") is True), None
            )
            if not passing or passing != entry["chosen"] or passing != spec.effort:
                raise BenchmarkValidationError("frozen judge effort differs from tuning evidence")
            pins = record.get("judge_settings")
            if "judge_settings" in record and (
                not isinstance(pins, dict) or pins.get(judge) != settings[judge]
            ):
                raise BenchmarkValidationError("frozen judge settings differ from tuning evidence")
            chosen.add(judge)
    if chosen != set(judges):
        raise BenchmarkValidationError("incomplete tuning evidence for the frozen composition")


def write_g1(
    workspace: Path,
    *,
    panel_path: Path | None = None,
    criteria_path: Path | None = None,
    tuning_path: Path = TUNING_PATH,
) -> Path:
    """held-out workspace: G1 for its composition, the reference probe check, the dropped
    probes and the handed-off tuning evidence, as one entry of ``g1.json``."""
    check_output_paths(workspace, [workspace / "g1.json"])
    inputs = _workspace_inputs(workspace, "held-out", panel_path, criteria_path, tuning_path)
    probe_file, evidence = _handoff_inputs(workspace, inputs)
    panel = inputs.panel
    held_out = [p for p in probe_file.probes if p.set == "held_out"]
    source = CalibrationSource(workspace)
    corpus = workspace / "inputs/corpus.yml"
    try:
        corpus_body = source.read(workspace, corpus)
    except FileNotFoundError:
        corpus_body = read_input_bytes(CORPUS_PATH, "corpus", "calibration corpus")
        if hashlib.sha256(corpus_body).hexdigest() != inputs.settings["criteria"].mapping.get(
            "corpus_sha256"
        ):
            raise BenchmarkValidationError(
                "legacy calibration lacks a matching frozen corpus; cannot establish a verdict"
            ) from None
    bodies = {**inputs.bodies, "tuning": inputs.tuning.content, "corpus": corpus_body}
    composition = source.composition(panel)
    if "judges" in inputs.meta and inputs.meta["judges"] != list(composition):
        raise BenchmarkValidationError("held-out workspace composition differs")
    _check_tuning_evidence(inputs, evidence, list(composition))
    _verified, tuning_unknown = verified_tuning(
        evidence, panel=inputs.settings["panel"], probes=inputs.settings["probes"],
        criteria=inputs.settings["criteria"], judges=list(composition),
        inputs=inputs.meta["inputs"],
    )
    scores, repeats, reference_scores = held_out_scores(
        source, panel, held_out, bodies, seed=probe_file.header["corpus_sha256"]
    )
    panel_scores, judge_scores = scores["panel"], scores["judges"]
    gate = g1(
        held_out,
        panel_scores,
        judge_scores,
        dropped=[d for d in probe_file.dropped if d["set"] == "held_out"],
    )
    reference = panel.judges[panel.reference]
    subset = reference_subset(held_out, seed=probe_file.header["corpus_sha256"])
    check = None
    if reference_scores:
        check = _gate(reference_probe_check(subset, reference_scores))
    if not evidence and (gate.passed is not None or (check and check["passed"] is not None)):
        raise BenchmarkValidationError("evaluable calibration requires a tuning handoff")
    gate.details["held_out_test_retest"] = repeats
    if problems := {**tuning_unknown, **repeat_problems(repeats)}:
        gate.details["calibration_unknown"] = problems
    entry = {
        "binding": {
            "composition": composition,
            "judge_settings": _judge_settings(
                inputs.settings["panel"],
                list(composition),
                reference=reference.id if check else None,
            ),
            "inputs": {name: inputs.meta["inputs"].get(name) for name in BINDING_INPUTS},
        },
        "gate": _gate(gate),
        "scores": {"panel": panel_scores, "judges": judge_scores},
        "reference_check": check,
        "dropped": dict(Counter(str(d.get("type")) for d in probe_file.dropped)),
        "tuning": evidence,
        "source": source.export(bodies),
    }
    path = workspace / "g1.json"
    _write(
        workspace, path, {"entries": {composition_key(list(composition)): entry}}, calibration=True
    )
    return path
