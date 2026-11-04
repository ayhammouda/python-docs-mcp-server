"""``agentic-score``: grades, gates, claims and disclosure for a graded run (design §9).

``score_run_dir`` reads a run directory only: its transcripts, scoring and token
records, ``planned-cells.json``, the frozen ``grading/inputs/`` and the vote
records. The three workspace steps build the probe file, the tuning evidence and
the G1 evidence. Nothing here makes a network call.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from benchmarks.agentic.egress import open_log_dir, read_log_at
from benchmarks.agentic.inputs import CapturedInput, read_input_bytes, read_json
from benchmarks.agentic.protocol import (
    assert_no_key_shaped_json,
    scanned_json_bytes,
)
from benchmarks.grading.aggregate import (
    LLM_PANEL,
    AnswerVotes,
    chosen_record,
    final_grade,
    has_final_record,
    judge_grade,
    load_answer_votes,
)
from benchmarks.grading.gates import (
    BINDING_INPUTS,
    CellKey,
    ClaimResult,
    GateResult,
    alpha_by_group,
    c2,
    c3,
    g1,
    g2,
    g3,
    g4,
    length_grade_correlation,
    load_egress_evidence,
    load_offline_evidence,
    offline_verdict,
    outcome_tier,
    pass1_composition,
    reference_probe_check,
    run_binding,
    select_g1,
    self_preference,
    test_retest,
)
from benchmarks.grading.grounding import is_grounded
from benchmarks.grading.inputs import (
    PROBE_SPEC_PATH,
    REPO_ROOT,
    SNAPSHOT_FILES,
    TUNING_PATH,
    GradingQuestion,
    Weight,
    criteria_from_mapping,
    load_criteria,
    load_tuning_questions,
    safe_id,
    scanned_input,
)
from benchmarks.grading.mapping import Vote, grade, panel_fields
from benchmarks.grading.panel import Panel, composition_key, load_panel, panel_from_mapping
from benchmarks.grading.probes import (
    MAX_ROUNDS,
    Probe,
    ProbeFile,
    assemble,
    assignments,
    load_probe_file,
    reference_subset,
    write_probe_file,
)
from benchmarks.grading.stats import GATE_RESAMPLES
from benchmarks.runner import BenchmarkValidationError
from benchmarks.scoring import apply_llm_grades, score_run

CORPUS_PATH = REPO_ROOT / "docs" / "benchmarks" / "corpus.yml"
CORPUS_SCHEMA_PATH = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
G1_PER_JUDGE, NO_RECORD = 0.90, "no_record"


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BenchmarkValidationError(f"could not read JSON {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    """JSON without NaN or infinities: a non-finite float becomes the string 'inf' etc."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _write(path: Path, data: Any) -> Any:
    body = scanned_json_bytes(_jsonable(data), where="grading evidence", indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body + b"\n")
    return json.loads(body)


def _read_g1(path: Path) -> tuple[bytes, dict[str, Any]]:
    body = read_input_bytes(path, "G1 evidence", "G1 evidence")

    def checked_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        assert_no_key_shaped_json(pairs, where="G1 evidence")
        data = dict(pairs)
        if len(data) != len(pairs):
            raise BenchmarkValidationError("G1 evidence contains a duplicate JSON member")
        return data

    try:
        data = json.loads(body.decode("utf-8"), object_pairs_hook=checked_pairs)
    except BenchmarkValidationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise BenchmarkValidationError("could not read G1 evidence as UTF-8 JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise BenchmarkValidationError("G1 evidence needs an entries object")
    return body, data["entries"]


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
        bodies[sha] = body
        copies.append({"source": str(path), "path": f"disclosure/g1/{sha}.json", "sha256": sha})
    return entries, bodies, copies


def _cells(base: Path) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(base.glob("*/*.json")):
        data = _read(path)
        records[(data["competitor_id"], data["corpus_id"])] = data
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


def _own(av: AnswerVotes, judge_id: str, weights: tuple[Weight, Weight]) -> float | None:
    vote = av.votes.get(judge_id)
    return None if vote is None else grade(vote.fields(), weights)[0]


def _criteria_weights(path: Path) -> dict[str, tuple[Weight, Weight]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        qid: (entries[0]["weight"], entries[1]["weight"])
        for qid, entries in (data.get("questions") or {}).items()
    }


def _tuning_weights(path: Path) -> dict[str, tuple[Weight, Weight]]:
    return {qid: q.weights for qid, q in load_tuning_questions(path).items()}


def _ledger_spend(grading: Path, graded: int) -> dict[str, Any]:
    booked: dict[str, Decimal] = defaultdict(Decimal)
    path = grading / "ledger-events.jsonl"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line) if line.strip() else {}
            if event.get("event") == "settle":
                booked[event["provider"]] += Decimal(str(event["booked"]))
    return {
        "by_provider": {p: str(v) for p, v in sorted(booked.items())},
        "graded_answers": graded,
        "per_graded_answer": {
            p: str((v / graded).quantize(Decimal("0.000001"))) for p, v in sorted(booked.items())
        }
        if graded
        else {},
    }


def _no_final_record(
    grading: Path, efforts: Mapping[str, str | None], items: Sequence[str]
) -> dict[str, dict[str, list[str]]]:
    """Amendment 2: per judge, the items with no final pass-1 record, by last not_run reason."""
    last_reason: dict[tuple[str, str], str] = {}
    for path in sorted((grading / "runs").glob("*.json")):
        for entry in _read(path).get("not_run") or []:
            if entry.get("target") == "answers" and entry.get("pass") == 1:
                last_reason[(entry["judge_id"], entry["item_id"])] = entry["reason"]
    result: dict[str, dict[str, list[str]]] = {}
    for judge_id, effort in efforts.items():
        for item in items:
            if not has_final_record(
                grading,
                target="answers",
                judge_id=judge_id,
                effort=effort,
                pass_number=1,
                item_id=item,
            ):
                reason = last_reason.get((judge_id, item), NO_RECORD)
                result.setdefault(judge_id, {}).setdefault(reason, []).append(item)
    return result


def _scoring_inputs(
    run_dir: Path, criteria_path: Path | None, panel_path: Path | None, schema_path: Path
) -> tuple[dict[str, str], dict[str, GradingQuestion], Panel]:
    root = run_dir / "grading" / "inputs"
    manifest = read_json(root / "inputs.json", "grading input manifest")
    if manifest.keys() - SNAPSHOT_FILES.keys():
        raise BenchmarkValidationError("unrecognized entries in grading input manifest")
    overrides = {"criteria": criteria_path, "panel": panel_path}
    settings = {}
    for name, filename in SNAPSHOT_FILES.items():
        path = root / filename
        if name == "probes" and name not in manifest and not path.exists():
            continue  # An older run without probes can still be scored without G1.
        if name in overrides:
            captured = CapturedInput.read(path, name, name)
            settings[name] = captured.mapping
            body = captured.content
        else:
            body = scanned_input(path, suffix=path.suffix)
        if hashlib.sha256(body).hexdigest() != manifest.get(name):
            raise BenchmarkValidationError(f"frozen {name} differs from the grading input manifest")
        override = overrides.get(name)
        if (
            override is not None
            and override != path
            and scanned_input(override, suffix=path.suffix) != body
        ):
            raise BenchmarkValidationError(f"--{name} differs from the run's frozen {name}")

    questions = criteria_from_mapping(
        settings["criteria"],
        root / SNAPSHOT_FILES["criteria"],
        corpus_path=run_dir / "snapshots" / "corpus.yml",
        schema_path=schema_path,
    )
    panel = panel_from_mapping(settings["panel"], root / SNAPSHOT_FILES["panel"])
    return manifest, questions, panel


def score_run_dir(
    run_dir: Path,
    *,
    criteria_path: Path | None = None,
    panel_path: Path | None = None,
    schema_path: Path,
    g1_paths: Sequence[Path] = (),
    resamples: int = GATE_RESAMPLES,
) -> dict[str, Any]:
    """Score, grade, gate and claim one run; write ``grading/summary.json`` and disclosure."""
    if resamples != GATE_RESAMPLES:
        raise BenchmarkValidationError(
            f"gate scoring requires {GATE_RESAMPLES} bootstrap resamples"
        )
    grading = run_dir / "grading"
    seed = str(_read(run_dir / "environment.json").get("seed"))
    manifest, questions, panel = _scoring_inputs(run_dir, criteria_path, panel_path, schema_path)
    binding = run_binding(run_dir, panel, inputs=manifest)
    entries, bodies, copies = _g1_inputs(g1_paths)
    if entries and binding.missing_field():
        raise BenchmarkValidationError(f"G1 input cannot bind missing {binding.missing_field()}")
    g1_result = (
        select_g1(entries, binding)
        if entries
        else GateResult("G1", None, {"reason": "no G1 evidence was given (--g1)"})
    )
    key = composition_key(list(binding.composition))
    entry = entries.get(key, {})
    check = entry.get("reference_check") or {}
    if not isinstance(check, dict) or not isinstance(check.get("details", {}), dict):
        raise BenchmarkValidationError("G1 reference check must contain gate details")
    reference_check = GateResult(
        "reference_probe_check", check.get("passed"), dict(check.get("details") or {})
    )

    disclosure = grading / "disclosure"
    raw_g1 = disclosure / "g1"
    if any(p.is_symlink() for p in (grading, disclosure, raw_g1)):
        raise BenchmarkValidationError("G1 disclosure directory must be within the run")
    efforts = {judge: spec.get("effort") for judge, spec in binding.composition.items()}
    answer_votes = load_answer_votes(grading, panel=panel, judges=efforts)
    pass2 = load_answer_votes(grading, panel=panel, judges=efforts, pass_number=2)
    transcripts = _cells(run_dir / "transcripts")

    # 2. LLM grades: an item waits until every panel judge has a final pass-1 record.
    finals: dict[tuple[str, str], dict[str, Any]] = {}
    for key, av in answer_votes.items():
        complete = all(
            has_final_record(
                grading, target="answers", judge_id=j, effort=e, pass_number=1, item_id=av.item_id
            )
            for j, e in efforts.items()
        )
        final = final_grade(av, questions[av.corpus_id], inputs=manifest) if complete else None
        if final is not None:
            assert_no_key_shaped_json(final, where="cell grade")
            finals[key] = final
    grounded = {
        key: is_grounded(t.get("answer") or "", t.get("tool_calls") or [])
        for key, t in transcripts.items()
    }
    score_run(run_dir, schema_path=schema_path)
    apply_llm_grades(run_dir, finals, grounded=grounded, schema_path=schema_path)
    scoring = _cells(run_dir / "scoring")
    pending = sorted(f"{c}/{q}" for (c, q), r in scoring.items() if r.get("score") is None)

    by_item = {av.item_id: av for av in answer_votes.values()}
    question_of = {av.item_id: av.corpus_id for av in answer_votes.values()}
    family_of = {
        av.item_id: transcripts[k]["model_family_id"]
        for k, av in answer_votes.items()
        if k in transcripts
    }
    system_of = {
        av.item_id: transcripts[k]["system_id"]
        for k, av in answer_votes.items()
        if k in transcripts
    }
    weights = {qid: q.weights for qid, q in questions.items()}

    # 3. G2 on the judges' own pass-1 grades of every panel-graded (non-suspect) answer.
    own = {
        av.item_id: {j: _own(av, j, weights[av.corpus_id]) for j in efforts}
        for k, av in answer_votes.items()
        if k in finals and not av.suspect
    }
    g2_result = g2(own, question_of, seed=seed, resamples=resamples)

    # 4. G3 on the stored reference sample: final grade vs an independent reference vote.
    pairs: dict[str, tuple[float, float]] = {}
    for k, final in finals.items():
        av = answer_votes[k]
        if final["method"] == LLM_PANEL and not av.suspect and av.reference is not None:
            pairs[av.item_id] = (
                final["score"],
                grade(av.reference.fields(), weights[av.corpus_id])[0],
            )
    sample_path = grading / "reference-sample.json"
    if sample_path.is_file():
        sample = _read(sample_path)
        sample_ids = sample.get("ids")
        if not isinstance(sample_ids, list) or not all(isinstance(i, str) for i in sample_ids):
            raise BenchmarkValidationError("reference-sample.json must list answer IDs")
        g3_result = g3(
            sample_ids,
            pairs,
            question_of,
            seed=seed,
            reference_check=reference_check,
            sample_info=sample,
            resamples=resamples,
        )
    else:
        sample_ids = []
        g3_result = GateResult(
            "G3", None, {"reason": "G3 was not evaluated: the run has no reference sample."}
        )
    in_sample = {i: pairs[i] for i in sample_ids if i in pairs}
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for final_score, reference_score in in_sample.values():
        confusion[str(final_score)][str(reference_score)] += 1

    # 6. Claims under the panel and under each single judge, then G4.
    planned: set[CellKey] = {
        (c["system_id"], c["model_family_id"], c["corpus_id"], int(c["repeat"]))
        for c in _read(run_dir / "planned-cells.json")["cells"]
    }
    cell_family = {
        c["cell"]: c["model_family_id"] for c in _read(run_dir / "planned-cells.json")["cells"]
    }
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
    for system, model, _q, _r in planned:
        systems_of[model].add(system)
    c2_models = sorted(m for m, s in systems_of.items() if {"python-docs", "docs-fetch"} <= s)
    c3_models = sorted(
        m for m, s in systems_of.items() if {"python-docs", "no-docs"} <= s and m in airgap
    )
    offline: dict[str, Any] | None = None
    offline_ok: bool | None = None
    if c3_models:
        airgap_jobs = sorted({f"{m}--{s}" for s, m, _q, _r in planned if m in airgap})
        reported = sum(1 for t in transcripts.values() if t["model_family_id"] in airgap)
        reported += sum(
            1
            for path in (run_dir / "discarded").glob("*/*.json")
            if cell_family.get(path.parent.name) in airgap
        )
        verdict = offline_verdict(
            load_offline_evidence(run_dir),
            load_egress_evidence(run_dir),
            expected_checks=reported + 2,
            airgap_jobs=airgap_jobs,
        )
        offline_ok = verdict.ok
        offline = {
            "ok": verdict.ok,
            "expected_checks": reported + 2,
            "airgap_jobs": airgap_jobs,
            "denied_attempts": verdict.denied_attempts,
        }

    def claims_for(
        scores: Mapping[CellKey, float | None], cells: set[CellKey]
    ) -> dict[str, dict[str, ClaimResult]]:
        return {
            "C2": {
                m: c2(tokens, scores, cells, model=m, seed=seed, resamples=resamples)
                for m in c2_models
            },
            "C3": {
                m: c3(scores, cells, model=m, seed=seed, offline_ok=offline_ok, resamples=resamples)
                for m in c3_models
            },
        }

    panel_claims = claims_for(panel_scores, planned)
    judge_claims = {j: claims_for(s, planned) for j, s in judge_scores.items()}

    def verdicts_of(claims: Mapping[str, Mapping[str, ClaimResult]]) -> dict[str, bool | None]:
        return {f"{cid}/{m}": r.passed for cid, by in claims.items() for m, r in by.items()}

    g4_result = g4(
        {"panel": verdicts_of(panel_claims)}
        | {j: verdicts_of(claims) for j, claims in judge_claims.items()}
    )

    # 7. The key audit: the reference's verdict on each answer key.
    reference = panel.judges[panel.reference]
    audited: dict[str, bool] = {}
    for qid in questions:
        record = chosen_record(
            grading,
            target="key-audit",
            judge_id=reference.id,
            effort=reference.effort,
            pass_number=1,
            item_id=safe_id(qid),
        )
        if record is not None and isinstance(record.get("parsed"), dict):
            audited[qid] = bool(record["parsed"].get("key_supported"))
    flagged = sorted(q for q, supported in audited.items() if not supported)
    without_flagged = None
    if flagged:
        kept = {cell for cell in planned if cell[2] not in flagged}
        kept_scores = {k: v for k, v in panel_scores.items() if k[2] not in flagged}
        without_flagged = claims_for(kept_scores, kept)

    # 8. The tier and the diagnostics.
    tier = outcome_tier(g1_result, g2_result, g3_result, g4_result)
    group_key = {i: f"{family_of.get(i)}/{system_of.get(i)}" for i in by_item}
    own2 = {
        av.item_id: {j: _own(av, j, weights[av.corpus_id]) for j in efforts}
        for av in pass2.values()
        if any(v is not None for v in av.votes.values())
    }
    providers = {fid: str(f.get("provider")) for fid, f in families.items()}
    spi = self_preference(
        own,
        {i: providers.get(family_of.get(i, ""), "") for i in own},
        question_of,
        {j: panel.judges[j].provider for j in efforts},
        seed=seed,
        resamples=resamples,
    )
    graded = [(k, f) for k, f in finals.items() if k in transcripts]
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    grounded_cells: dict[str, list[bool]] = defaultdict(list)
    for k, record in scoring.items():
        if k not in transcripts:
            continue
        group = f"{transcripts[k]['model_family_id']}/{transcripts[k]['system_id']}"
        if record.get("label"):
            labels[group][record["label"]] += 1
        if record.get("score") is not None:
            grounded_cells[group].append(bool(record.get("grounded")))
    suspects: dict[str, dict[str, int]] = defaultdict(lambda: {"suspect": 0, "differs": 0})
    for k, final in finals.items():
        av = answer_votes[k]
        if av.suspect:
            group = group_key[av.item_id]
            suspects[group]["suspect"] += 1
            pf = panel_fields(av.votes)
            if (
                pf.needs_reference
                or grade({f: bool(v) for f, v in pf.fields.items()}, weights[av.corpus_id])[0]
                != final["score"]
            ):
                suspects[group]["differs"] += 1
    per_judge = {
        j: {
            "records": sum(1 for av in answer_votes.values() if j in av.records),
            "withheld": dict(
                Counter(
                    av.records[j]["withheld"]
                    for av in answer_votes.values()
                    if j in av.records and av.records[j]["withheld"] is not None
                )
            ),
        }
        for j in efforts
    }
    diagnostics = {
        "test_retest": test_retest(
            {i: own[i] for i in own if i in own2},
            own2,
            question_of,
            seed=seed,
            resamples=resamples,
        )
        if own2
        else None,
        "self_preference": {f"{j}/{f}": asdict(v) for (j, f), v in spi.items()},
        "length_grade_correlation": length_grade_correlation(
            [len(transcripts[k].get("answer") or "") for k, _ in graded],
            [f["score"] for _, f in graded],
        ),
        "label_shares": {
            g: {label: n / sum(c.values()) for label, n in sorted(c.items())}
            for g, c in sorted(labels.items())
        },
        "grounded_rate": {g: sum(v) / len(v) for g, v in sorted(grounded_cells.items()) if v},
        "suspects": dict(sorted(suspects.items())),
        "per_judge": per_judge,
        # shortcut: an item listed here blocks the reference target until it is fixed and
        # graded again (Task 8 refuses `reference` while any panel item lacks a final record).
        "no_final_record": _no_final_record(grading, efforts, sorted(by_item)),
    }

    def breakdown(grades: Mapping[str, Any], of: Mapping[str, str]) -> dict[str, Any]:
        groups = alpha_by_group(
            grades, {i: of.get(i, "?") for i in grades}, question_of, seed=seed, resamples=resamples
        )
        return {g: asdict(r) for g, r in groups.items()}

    g3_grades = {i: {"final": p[0], "reference": p[1]} for i, p in in_sample.items()}
    breakdowns = {
        "G2": {"by_family": breakdown(own, family_of), "by_system": breakdown(own, system_of)},
        "G3": {
            "by_family": breakdown(g3_grades, family_of),
            "by_system": breakdown(g3_grades, system_of),
            "confusion": {k: dict(v) for k, v in sorted(confusion.items())},
        },
    }
    _write(grading / "disclosure" / "breakdowns.json", breakdowns)
    for path, value in (
        (disclosure / "g1-entries.json", entries),
        (disclosure / "tuning.json", entry.get("tuning")),
    ):
        path.unlink(missing_ok=True)
        if value:
            _write(path, value)
    if bodies:
        raw_g1.mkdir(parents=True, exist_ok=True)
    for sha, body in bodies.items():
        path = raw_g1 / f"{sha}.json"
        try:
            with path.open("xb") as f:
                f.write(body)
        except FileExistsError:
            with open_log_dir(raw_g1) as directory:
                if read_log_at(directory, path.name, max_bytes=len(body)) != body:
                    raise BenchmarkValidationError("archived G1 bytes differ from their digest")

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
        "key_audit": {"audited": len(audited), "flagged": flagged},
        "claims_without_flagged_keys": None
        if without_flagged is None
        else {cid: {m: _claim(r) for m, r in by.items()} for cid, by in without_flagged.items()},
        "offline": offline,
        "graded_cells": len(finals),
        "pending_cells": pending,
        "diagnostics": diagnostics,
        "breakdowns": breakdowns,
        "spend": _ledger_spend(grading, len(finals)),
        "disclosure": {"g1_files": copies},
    }
    return _write(grading / "summary.json", summary)


def _model_families(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "snapshots" / "model-matrix.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {str(f["id"]): f for f in (data or {}).get("model_families") or []}


def _workspace(path: Path, kind: str) -> dict[str, Any]:
    meta_path = path / "workspace.json"
    if not meta_path.is_file():
        raise BenchmarkValidationError(f"{path} is not a grading workspace (no workspace.json)")
    meta = _read(meta_path)
    if meta.get("kind") != kind:
        raise BenchmarkValidationError(
            f"{path} is a {meta.get('kind')} workspace; this step needs a {kind} workspace"
        )
    return meta


def _record_vote(record: Mapping[str, Any] | None) -> Vote | None:
    if record is None or record.get("withheld") is not None or record.get("vote") is None:
        return None
    return Vote(**record["vote"])


def _judge_score(
    root: Path,
    target: str,
    judge_id: str,
    effort: str | None,
    probe: Probe,
    weights: Mapping[str, tuple[Weight, Weight]],
    pass_number: int = 1,
) -> tuple[bool, float | None]:
    """(a record exists, the judge's score of the probe or None)."""
    record = chosen_record(
        root,
        target=target,
        judge_id=judge_id,
        effort=effort,
        pass_number=pass_number,
        item_id=probe.id,
    )
    vote = _record_vote(record)
    score = None if vote is None else grade(vote.fields(), weights[probe.question_id])[0]
    return record is not None, score


def assemble_workspace_probes(
    workspace: Path,
    *,
    criteria_path: Path,
    tuning_path: Path,
    panel_path: Path,
    index_path: Path,
    corpus_path: Path = CORPUS_PATH,
    schema_path: Path = CORPUS_SCHEMA_PATH,
) -> Path:
    """probe-build workspace: the generation and check votes plus code-built probes."""
    _workspace(workspace, "probe-build")
    panel = load_panel(panel_path)
    sets = {
        "held_out": load_criteria(criteria_path, corpus_path=corpus_path, schema_path=schema_path),
        "tuning": load_tuning_questions(tuning_path),
    }
    probes: list[Probe] = []
    dropped: list[dict[str, Any]] = []
    for set_name, questions in sets.items():
        assignment = assignments(sorted(questions), panel.generators)
        generated: dict[tuple[str, str, int], str] = {}
        checks: dict[tuple[str, str, int], bool] = {}
        for (qid, probe_type), (generator, checker) in assignment.items():
            for r in range(1, MAX_ROUNDS + 1):
                gen = chosen_record(
                    workspace,
                    target="probe-gen",
                    judge_id=generator,
                    effort=panel.judges[generator].effort,
                    pass_number=1,
                    item_id=safe_id("gen", probe_type, qid, f"r{r}"),
                )
                if gen is not None and isinstance(gen.get("parsed"), dict):
                    generated[(qid, probe_type, r)] = str(gen["parsed"]["probe_text"])
                chk = chosen_record(
                    workspace,
                    target="probe-check",
                    judge_id=checker,
                    effort=panel.judges[checker].effort,
                    pass_number=1,
                    item_id=safe_id("chk", probe_type, qid, f"r{r}"),
                )
                if chk is not None and isinstance(chk.get("parsed"), dict):
                    checks[(qid, probe_type, r)] = bool(chk["parsed"]["meets_spec"])
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
        "criteria_sha256": _sha256(criteria_path),
        "corpus_sha256": _sha256(corpus_path),
        "tuning_sha256": _sha256(tuning_path),
        "spec_sha256": _sha256(PROBE_SPEC_PATH),
        "index_sha256": _sha256(index_path),
    }
    path = workspace / "probes.yml"
    write_probe_file(path, ProbeFile(header, probes, dropped))
    return path


def _handoff_probes(workspace: Path, tuning_path: Path) -> ProbeFile:
    return load_probe_file(
        workspace / "handoff" / "probes.yml",
        criteria_path=workspace / "inputs" / "grading-criteria.yml",
        tuning_path=tuning_path,
    )


def write_tuning(workspace: Path, *, panel_path: Path, tuning_path: Path = TUNING_PATH) -> Path:
    """tuning workspace: accuracy per judge per effort, and the lowest effort meeting G1's
    per-judge criteria (overall >= 0.90, zero INJ credit)."""
    meta = _workspace(workspace, "tuning")
    panel = load_panel(panel_path)
    probes = [p for p in _handoff_probes(workspace, tuning_path).probes if p.set == "tuning"]
    weights = _tuning_weights(tuning_path)
    judges: dict[str, Any] = {}
    for judge_id in [*panel.panel, *panel.fallback.values()]:
        efforts: dict[str, Any] = {}
        for effort in panel.judges[judge_id].effort_levels:
            scored = [
                _judge_score(workspace, "tuning", judge_id, effort, p, weights) for p in probes
            ]
            if not any(exists for exists, _ in scored):
                continue
            scores = {p.id: s for p, (_, s) in zip(probes, scored, strict=True)}
            hits = sum(1 for p in probes if scores[p.id] == p.expected_score)
            inj = sum(1 for p in probes if p.type == "INJ" and (scores[p.id] or 0.0) > 0)
            accuracy = hits / len(probes) if probes else 0.0
            efforts[effort] = {
                "accuracy": accuracy,
                "n": len(probes),
                "inj_credit": inj,
                "meets": bool(probes) and accuracy >= G1_PER_JUDGE and inj == 0,
            }
        chosen = next((e for e in efforts if efforts[e]["meets"]), None)
        judges[judge_id] = {"efforts": efforts, "tried": list(efforts), "chosen": chosen}
    path = workspace / "tuning.json"
    _write(
        path,
        {
            "workspace": str(workspace),
            "inputs": meta["inputs"],
            "prompt_sha256": meta["inputs"].get("judge_prompt"),
            "judges": judges,
        },
    )
    return path


def write_g1(workspace: Path, *, panel_path: Path, tuning_path: Path = TUNING_PATH) -> Path:
    """held-out workspace: G1 for its composition, the reference probe check, the dropped
    probes and the handed-off tuning evidence, as one entry of ``g1.json``."""
    meta = _workspace(workspace, "held-out")
    panel = load_panel(panel_path)
    probe_file = _handoff_probes(workspace, tuning_path)
    held_out = [p for p in probe_file.probes if p.set == "held_out"]
    weights = _criteria_weights(workspace / "inputs" / "grading-criteria.yml")
    composition = pass1_composition(workspace, "probes", panel)
    judge_scores: dict[str, dict[str, float | None]] = {}
    panel_scores: dict[str, float | None] = {}
    for probe in held_out:
        votes: dict[str, Vote | None] = {}
        for judge_id, spec in composition.items():
            record = chosen_record(
                workspace,
                target="probes",
                judge_id=judge_id,
                effort=spec.get("effort"),
                pass_number=1,
                item_id=probe.id,
            )
            vote = _record_vote(record)
            votes[judge_id] = vote
            judge_scores.setdefault(judge_id, {})[probe.id] = (
                None if vote is None else grade(vote.fields(), weights[probe.question_id])[0]
            )
        fields = panel_fields(votes)
        panel_scores[probe.id] = (
            None
            if fields.needs_reference
            else grade({f: bool(v) for f, v in fields.fields.items()}, weights[probe.question_id])[
                0
            ]
        )
    gate = g1(held_out, panel_scores, judge_scores)
    reference = panel.judges[panel.reference]
    subset = reference_subset(held_out, seed=probe_file.header["corpus_sha256"])
    scored = [
        _judge_score(workspace, "reference-probes", reference.id, reference.effort, p, weights)
        for p in subset
    ]
    check = None
    if any(exists for exists, _ in scored):
        result = reference_probe_check(
            subset, {p.id: s for p, (_, s) in zip(subset, scored, strict=True)}
        )
        check = _gate(result)
    tuning = [
        _read(workspace / "handoff" / name)
        for name in sorted(meta.get("handoff") or {})
        if name.startswith("tuning")
    ]
    entry = {
        "binding": {
            "composition": composition,
            "inputs": {name: meta["inputs"].get(name) for name in BINDING_INPUTS},
        },
        "gate": _gate(gate),
        "reference_check": check,
        "dropped": dict(Counter(str(d.get("type")) for d in probe_file.dropped)),
        "tuning": tuning,
    }
    path = workspace / "g1.json"
    _write(path, {"entries": {composition_key(list(composition)): entry}})
    return path
