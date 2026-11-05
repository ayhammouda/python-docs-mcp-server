"""agentic-score: grades, gates, claims and disclosure from a graded run (PR 5 Task 7).

The run directories here are synthetic: transcripts, scoring placeholders,
planned cells, frozen grading inputs and vote records written with
write_vote_record. No worker runs and nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.__main__ import main
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.gates import g1, reference_probe_check
from benchmarks.grading.inputs import TUNING_PATH, load_tuning_questions, safe_id, snapshot_inputs
from benchmarks.grading.panel import load_panel
from benchmarks.grading.probes import (
    CHECK_SCHEMA,
    PROBE_SCHEMA,
    assignments,
    load_probe_file,
    reference_subset,
)
from benchmarks.grading.score import (
    assemble_workspace_probes,
    score_run_dir,
    write_g1,
    write_tuning,
)
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_grading_probes import index_db as index_db

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
GFIX = FIX / "grading"
CRITERIA = GFIX / "criteria.sample.yml"
PANEL_PATH = GFIX / "panel.yml"
PROBES = GFIX / "probes.sample.yml"
SCHEMA = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
PANEL = load_panel(PANEL_PATH)
JUDGES = ("haiku", "luna", "flash-lite")
COMPOSITION = {j: {"model_id": PANEL.judges[j].model_id, "effort": "low"} for j in JUDGES}
QUESTIONS = ("SYN-EX-001", "SYN-EX-002")
SYSTEMS = ("python-docs", "docs-fetch", "no-docs")
FAMILY = "scripted-anthropic"
TOKENS = {"python-docs": 500, "docs-fetch": 1000, "no-docs": 50}
_ALL_MET = {
    "attempted": True,
    "p1_met": True,
    "p2_met": True,
    "contradicts_key": False,
    "wrong_version": False,
}


def _json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _vote_text(**fields: bool) -> str:
    return json.dumps({"explanation": "x", **_ALL_MET, **fields})


def _result(item_id: str, text: str | None, status: str = "succeeded") -> BatchItemResult:
    return BatchItemResult(
        item_id,
        status,
        text,
        False,
        "end_turn",
        None,  # type: ignore[arg-type]
        {"input_tokens": 10},
        None,
        "scripted",
        False,
    )


def _answer_item(root: Path, question, cell: str, answer: str) -> None:
    from benchmarks.grading.inputs import render_judge_input

    value = render_judge_input(question, answer)
    schema = json.dumps(value.schema, sort_keys=True, separators=(",", ":")).encode()
    _json(
        root / "items/answers" / f"{safe_id(cell, question.id)}.json",
        {
            "system_sha256": hashlib.sha256(value.system.encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(schema).hexdigest(),
            "user": value.user,
            "meta": {
                "competitor_id": cell,
                "corpus_id": question.id,
                "suspect": value.suspect,
                "input_sha256": value.sha256(),
            },
        },
    )


def _answer_digest(root: Path, target: str, item: str) -> str:
    path = root / "items/answers" / f"{item}.json"
    evidence = json.loads(path.read_bytes())
    if target == "reference":
        _json(root / "items/reference" / path.name, evidence)
    return evidence["meta"]["input_sha256"]


@lru_cache(maxsize=128)
def _probe_fixture_requests(root: Path):
    from benchmarks.grading.inputs import criteria_from_mapping, render_judge_input

    suite = load_probe_file(
        root / "inputs/probes.yml",
        criteria_path=root / "inputs/grading-criteria.yml",
        tuning_path=TUNING_PATH,
    )
    questions = criteria_from_mapping(
        yaml.safe_load((root / "inputs/grading-criteria.yml").read_bytes()),
        root / "inputs/grading-criteria.yml",
        corpus_path=root / "inputs/corpus.yml",
        schema_path=SCHEMA,
    )
    questions.update(load_tuning_questions(TUNING_PATH))
    system = (root / "inputs/judge-prompt.md").read_text()
    schema = json.loads((root / "inputs/judge-schema.json").read_bytes())
    return {
        p.id: (
            p, render_judge_input(questions[p.question_id], p.text, system=system, schema=schema)
        )
        for p in suite.probes
    }


@lru_cache(maxsize=128)
def _probe_build_questions(root: Path):
    from benchmarks.grading.inputs import criteria_from_mapping

    questions = criteria_from_mapping(
        yaml.safe_load((root / "inputs/grading-criteria.yml").read_bytes()),
        root / "inputs/grading-criteria.yml",
        corpus_path=root / "inputs/corpus.yml", schema_path=SCHEMA,
    )
    questions.update(load_tuning_questions(TUNING_PATH))
    return questions


def _probe_digest(root: Path, target: str, item_id: str) -> str:
    if target in {"probe-gen", "probe-check"}:
        from benchmarks.grading.inputs import JudgeInput
        from benchmarks.grading.probes import check_request, generation_request

        _, kind, qid, round_id = item_id.split("--")
        questions = _probe_build_questions(root)
        request = (
            generation_request(kind, questions[qid], int(round_id[1:]))
            if target == "probe-gen" else
            check_request(kind, questions[qid], "Scripted candidate.", int(round_id[1:]))
        )
        value = JudgeInput(request.system, request.user, request.schema, False)
        _json(root / "items" / target / f"{item_id}.json", value.item_evidence({
            "question_id": qid, "probe_type": kind, "round": int(round_id[1:]),
        }))
        return value.sha256()
    if target not in {"tuning", "probes", "reference-probes"}:
        return "d" * 64
    pair = _probe_fixture_requests(root).get(item_id)
    if pair is None:
        return "d" * 64  # Deliberately unrelated item in retest selection controls.
    probe, request = pair
    _json(
        root / "items" / target / f"{item_id}.json",
        request.item_evidence({"question_id": probe.question_id, "suspect": request.suspect}),
    )
    return request.sha256()


def _vote(
    root: Path,
    judge: str,
    item_id: str,
    text: str | None,
    *,
    target: str = "answers",
    status: str = "succeeded",
    effort: str | None = "low",
    pass_number: int = 1,
    attempt: int = 1,
    schema: dict[str, Any] | None = None,
) -> None:
    from benchmarks.grading.inputs import judge_schema

    write_vote_record(
        root,
        target=target,
        judge=PANEL.judges[judge],
        effort=effort,
        pass_number=pass_number,
        grading_run_id="run-1",
        attempt=attempt,
        result=_result(item_id, text, status),
        terminal=False,
        unit_id="u1",
        provider_batch_id="b1",
        input_sha256=_answer_digest(root, target, item_id)
        if target in {"answers", "reference"}
        else _probe_digest(root, target, item_id),
        schema=schema or judge_schema(),
    )


def _cell(system: str, family: str = FAMILY) -> str:
    return f"{system}--{family}--r1"


def _build_run(
    tmp_path: Path,
    *,
    families: dict[str, dict[str, tuple[str, ...]]] | None = None,
    corpus: Path = FIX / "corpus.sample.yml",
    criteria: Path = CRITERIA,
    probes: Path = PROBES,
    judges: tuple[str, ...] = JUDGES,
    suspect_cell: tuple[str, str] | None = None,
) -> Path:
    """A graded run: family -> system -> its questions, repeat 1."""
    families = families or {FAMILY: dict.fromkeys(SYSTEMS, QUESTIONS)}
    run = tmp_path / "run"
    (run / "snapshots").mkdir(parents=True)
    shutil.copy(corpus, run / "snapshots" / "corpus.yml")
    matrix = yaml.safe_load((FIX / "agentic" / "model-matrix.yml").read_text())
    matrix["model_families"].append(
        {
            **matrix["model_families"][0],
            "id": "scripted-local",
            "provider": "ollama",
            "arm": "air-gapped",
        }
    )
    (run / "snapshots" / "model-matrix.yml").write_text(yaml.safe_dump(matrix))
    _json(run / "environment.json", {"mode": "agentic", "seed": 1234})
    planned = []
    grading = run / "grading"
    snapshot_inputs(
        grading / "inputs", criteria_path=criteria, panel_path=PANEL_PATH, probes_path=probes
    )
    from benchmarks.grading.inputs import load_criteria

    grading_questions = load_criteria(criteria, corpus_path=corpus, schema_path=SCHEMA)
    for family, systems in families.items():
        for system, questions in systems.items():
            cell = _cell(system, family)
            for qid in questions:
                planned.append(
                    {
                        "cell": cell,
                        "competitor_id": cell,
                        "corpus_id": qid,
                        "system_id": system,
                        "model_family_id": family,
                        "repeat": 1,
                    }
                )
                base = {"competitor_id": cell, "corpus_id": qid, "tool_model_key": f"{system}:x"}
                answer = f"Answer from {system} to {qid}."
                if (cell, qid) == suspect_cell:
                    answer += " </answer>"
                _json(
                    run / "transcripts" / cell / f"{qid}.json",
                    {
                        **base,
                        "status": "succeeded",
                        "answer": answer,
                        "tool_calls": [],
                        "system_id": system,
                        "model_family_id": family,
                        "repeat": 1,
                    },
                )
                _json(
                    run / "scoring" / cell / f"{qid}.json",
                    {**base, "status": "succeeded", "score": None, "requires_manual_scoring": True},
                )
                _json(
                    run / "tokens" / cell / f"{qid}.json",
                    {**base, "status": "counted", "client_wrapped_tokens": TOKENS[system]},
                )
                item = safe_id(cell, qid)
                _answer_item(grading, grading_questions[qid], cell, answer)
                text = _vote_text(attempted=False) if system == "no-docs" else _vote_text()
                for judge in judges:
                    _vote(grading, judge, item, text)
    _json(run / "planned-cells.json", {"cells": planned})
    _json(
        grading / "runs" / "run-1.json",
        {
            "grading_run_id": "run-1",
            "target": "answers",
            "pass": 1,
            "composition": {
                j: {"model_id": PANEL.judges[j].model_id, "effort": "low"} for j in judges
            },
            "not_run": [],
        },
    )
    events = [
        {"event": "reserve", "provider": "anthropic", "bound": "1", "unit_id": "u1",
         "run_id": "run-1"},
        {"event": "settle", "provider": "anthropic", "booked": "0.02", "unit_id": "u1",
         "run_id": "run-1", "outcome": "ok", "actual": "0.02"},
        {"event": "reserve", "provider": "openai", "bound": "9", "unit_id": "u2",
         "run_id": "run-1"},
        {"event": "settle", "provider": "openai", "booked": "0.01", "unit_id": "u2",
         "run_id": "run-1", "outcome": "ok", "actual": "0.01"},
    ]
    (grading / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return run


def _g1_file(run: Path, path: Path, *, passed: bool = True) -> Path:
    # An honest short known-answer demonstration, never a fabricated passing gate.
    root = run / "grading/inputs"
    inputs = json.loads((root / "inputs.json").read_text())
    suite = load_probe_file(
        root / "probes.yml", criteria_path=root / "grading-criteria.yml", tuning_path=TUNING_PATH
    )
    probes = [p for p in suite.probes if p.set == "held_out"]
    scores = {p.id: p.expected_score if passed else None for p in probes}
    judges = {j: dict(scores) for j in JUDGES}
    gate = g1(probes, scores, judges, dropped=[d for d in suite.dropped if d["set"] == "held_out"])
    subset = reference_subset(probes, seed=suite.header["corpus_sha256"])
    reference = reference_probe_check(subset, {p.id: p.expected_score for p in subset})
    panel = yaml.safe_load((root / "panel.yml").read_bytes())
    pins = {
        j: hashlib.sha256(json.dumps(panel["judges"][j], sort_keys=True).encode()).hexdigest()
        for j in (*JUDGES, "reference")
    }
    entry = {
        "binding": {"composition": COMPOSITION, "inputs": inputs, "judge_settings": pins},
        "gate": {"passed": gate.passed, "details": gate.details},
        "scores": {"panel": scores, "judges": judges},
        "reference_check": {"passed": reference.passed, "details": reference.details},
        "dropped": {},
        "tuning": [{"judges": {"haiku": {"chosen": "low"}}}],
    }
    _json(path, {"entries": {"haiku+luna+flash-lite": entry}})
    return path


@pytest.fixture
def scripted_graded_run(tmp_path: Path) -> Path:
    run = _build_run(tmp_path)
    grading = run / "grading"
    split = safe_id(_cell("python-docs"), "SYN-EX-002")
    shutil.rmtree(grading / "votes" / "answers" / "luna" / "low" / "pass1" / split)
    shutil.rmtree(grading / "votes" / "answers" / "flash-lite" / "low" / "pass1" / split)
    _vote(grading, "luna", split, _vote_text(p2_met=False))
    _vote(grading, "flash-lite", split, "not a vote")
    _vote(grading, "reference", split, _vote_text(p2_met=False), target="reference", effort="high")
    sample = [
        safe_id(_cell(s), q)
        for s in ("python-docs", "docs-fetch")
        for q in QUESTIONS
        if (s, q) != ("python-docs", "SYN-EX-002")
    ]
    sample.append(safe_id(_cell("no-docs"), "SYN-EX-001"))
    for item in sample:
        text = _vote_text(attempted=False) if "no-docs" in item else _vote_text()
        _vote(grading, "reference", item, text, target="reference", effort="high")
    _json(
        grading / "reference-sample.json",
        {"seed": "1234", "size": len(sample), "ids": sample, "borrowed": {}, "shortfall": {}},
    )
    return run


def _record(run: Path, system: str, qid: str) -> dict[str, Any]:
    return json.loads((run / "scoring" / _cell(system) / f"{qid}.json").read_text())


def _score(run: Path, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return score_run_dir(
        run,
        criteria_path=CRITERIA,
        panel_path=PANEL_PATH,
        schema_path=SCHEMA,
        g1_paths=[_g1_file(run, tmp_path / "g1.json")],
        **kwargs,
    )


def test_score_run_dir_writes_grades_gates_claims_and_disclosure(
    scripted_graded_run: Path, tmp_path: Path
) -> None:
    run = scripted_graded_run
    summary = _score(run, tmp_path)
    assert _record(run, "python-docs", "SYN-EX-001")["scoring_method"] == "llm_panel"
    split = _record(run, "python-docs", "SYN-EX-002")
    assert (split["score"], split["scoring_method"]) == (0.5, "llm_reference")
    assert split["grading"]["settled_fields"] == ["p2_met"]
    assert _record(run, "no-docs", "SYN-EX-001")["score"] == 0.0
    assert json.loads((run / "grading" / "summary.json").read_text()) == summary
    assert set(summary["gates"]) == {"G1", "G2", "G3", "G4"}
    assert summary["gates"]["G1"]["passed"] is None
    # A short artifact cannot redefine the preregistered 300-answer gate.
    assert summary["gates"]["G3"]["passed"] is None
    assert "of 300 answers" in summary["gates"]["G3"]["details"]["reason"]
    assert summary["tier"] in ("Validated", "Tentative", "Judge not validated")
    c2 = summary["claims"]["C2"][FAMILY]
    assert c2["interval"]["point"] == pytest.approx((500 * 2 / 1.5) / (1000 * 2 / 2))
    assert summary["claims"]["C3"] == {}
    assert set(summary["claims_by_judge"]) == set(JUDGES)
    assert summary["pending_cells"] == [] and summary["seed"] == "1234"
    copies = summary["disclosure"]["g1_files"]
    assert len(copies) == 1 and (run / "grading" / copies[0]["path"]).is_file()
    assert summary["spend"]["by_provider"] == {"anthropic": "0.02", "openai": "0.01"}
    assert summary["spend"]["graded_answers"] == 6
    diag = summary["diagnostics"]
    assert diag["per_judge"]["flash-lite"]["withheld"] == {"parse_failure": 1}
    assert diag["label_shares"][f"{FAMILY}/no-docs"] == {"NOT_ATTEMPTED": 1.0}
    assert diag["grounded_rate"][f"{FAMILY}/python-docs"] == 0.0
    assert diag["no_final_record"] == {}
    assert (run / "grading" / "disclosure" / "breakdowns.json").is_file()


@pytest.mark.parametrize("state", ["missing", "empty", "duplicate"])
def test_score_discloses_current_ledger_availability_and_uniqueness(
    scripted_graded_run: Path, tmp_path: Path, state: str,
) -> None:
    path = scripted_graded_run / "grading/ledger-events.jsonl"
    if state == "missing":
        path.unlink()
    elif state == "empty":
        path.write_text("")
    else:
        lines = path.read_text().splitlines()
        path.write_text("\n".join([*lines, lines[1]]) + "\n")
    spend = _score(scripted_graded_run, tmp_path)["spend"]
    assert spend["verified"] is (state == "empty")
    assert spend["reason"] == {"missing": "missing ledger", "empty": None,
                               "duplicate": "inconsistent ledger"}[state]
    assert spend["by_provider"] == {}
    assert spend["per_graded_answer"] == {}
    assert spend["graded_answers"] == 6


def test_score_run_dir_leaves_cells_pending_without_their_reference_vote(
    scripted_graded_run: Path, tmp_path: Path
) -> None:
    run = scripted_graded_run
    split = safe_id(_cell("python-docs"), "SYN-EX-002")
    shutil.rmtree(run / "grading" / "votes" / "reference" / "reference" / "high" / "pass1" / split)
    summary = _score(run, tmp_path)
    assert _record(run, "python-docs", "SYN-EX-002")["score"] is None
    assert summary["pending_cells"] == [f"{_cell('python-docs')}/SYN-EX-002"]
    assert summary["claims"]["C2"][FAMILY]["passed"] is None


def test_an_item_missing_a_judges_final_record_is_not_graded(
    scripted_graded_run: Path, tmp_path: Path
) -> None:
    run = scripted_graded_run
    grading = run / "grading"
    item = safe_id(_cell("docs-fetch"), "SYN-EX-001")
    shutil.rmtree(grading / "votes" / "answers" / "luna" / "low" / "pass1" / item)
    _vote(grading, "luna", item, None, status="errored", attempt=1)
    not_run = {
        "item_id": item,
        "reason": "submit_rejected",
        "attempt": 3,
        "target": "answers",
        "judge_id": "luna",
        "effort": "low",
        "pass": 1,
    }
    _json(
        grading / "runs" / "run-2.json",
        {
            "grading_run_id": "run-2",
            "target": "answers",
            "pass": 1,
            "composition": COMPOSITION,
            "not_run": [not_run],
        },
    )
    summary = _score(run, tmp_path)
    assert _record(run, "docs-fetch", "SYN-EX-001")["score"] is None
    assert f"{_cell('docs-fetch')}/SYN-EX-001" in summary["pending_cells"]
    assert summary["diagnostics"]["no_final_record"] == {"luna": {"submit_rejected": [item]}}


@pytest.fixture
def scripted_airgap_run(tmp_path: Path) -> Path:
    return _airgap_run(tmp_path)


def _airgap_run(tmp_path: Path, suspect_cell: tuple[str, str] | None = None) -> Path:
    one = ("SYN-EX-001",)
    run = _build_run(
        tmp_path,
        suspect_cell=suspect_cell,
        families={
            FAMILY: {"python-docs": QUESTIONS, "docs-fetch": QUESTIONS},
            "scripted-local": {"python-docs": one, "no-docs": one},
        },
    )
    # three reported attempts of the air-gapped jobs: two transcripts and one discard
    _json(
        run / "discarded" / _cell("python-docs", "scripted-local") / "SYN-EX-001--attempt1.json",
        {"reason": "budget_stop"},
    )
    jobs = {
        f"scripted-local--{s}": {
            "hosts_allowed": ["host.docker.internal:11434"],
            "hosts_denied": ["pypi.org:443"],
            "lookup_package_docs_attempts": 1,
        }
        for s in ("python-docs", "no-docs")
    }
    _json(run / "egress" / "summary.json", {"jobs": jobs, "isolation_probe": "pass"})
    return run


@pytest.mark.parametrize("checks,c3_evaluable", [(4, False), (5, True)])
def test_score_run_dir_needs_one_check_round_per_reported_attempt_plus_two(
    scripted_airgap_run: Path, tmp_path: Path, checks: int, c3_evaluable: bool
) -> None:
    run = scripted_airgap_run
    _json(
        run / "airgap" / "checks.json", {"all_passed": True, "checks": checks, "internet_hosts": []}
    )
    summary = _score(run, tmp_path)
    c3 = summary["claims"]["C3"]["scripted-local"]
    assert (c3["passed"] is not None) is c3_evaluable
    assert summary["offline"]["expected_checks"] == 5
    assert summary["offline"]["denied_attempts"] == [
        {"job_id": "scripted-local--no-docs", "host": "pypi.org:443"},
        {"job_id": "scripted-local--python-docs", "host": "pypi.org:443"},
    ]
    if c3_evaluable:
        assert c3["passed"] is True


def test_replay_without_required_reference_blocks_only_the_dependent_claim(tmp_path: Path) -> None:
    local = "scripted-local"
    cell, qid = _cell("python-docs", local), QUESTIONS[0]
    item = safe_id(cell, qid)
    run = _airgap_run(tmp_path, suspect_cell=(cell, qid))
    _json(run / "airgap/checks.json", {"all_passed": True, "checks": 5, "internet_hosts": []})
    _vote(run / "grading", "reference", item, _vote_text(), target="reference", effort="high")
    complete = _score(run, tmp_path)
    assert complete["claims"]["C3"][local]["passed"] is True
    assert complete["claims"]["C2"][FAMILY]["passed"] is True
    reference = run / "grading" / "votes" / "reference" / "reference" / "high" / "pass1" / item
    next(reference.glob("*.json")).unlink()
    replay = _score(run, tmp_path)
    assert replay["pending_cells"] == [f"{cell}/{qid}"]
    assert replay["diagnostics"]["no_final_record"] == {}  # All panel votes are still present.
    assert replay["claims"]["C3"][local]["passed"] is None
    assert replay["claims"]["C2"] == complete["claims"]["C2"]


def _fields_for(expected_score: float, expected_label: str, weights: tuple[str, str]) -> dict:
    if expected_label == "NOT_ATTEMPTED":
        return {"attempted": False}
    if expected_label == "INCORRECT":
        return {"contradicts_key": True}
    if expected_score == 0.5:
        return {"p1_met": False} if weights[0] == "nuance" else {"p2_met": False}
    return {}


def _workspace(
    path: Path,
    kind: str,
    handoff: dict[str, Path],
    *,
    criteria: Path = CRITERIA,
    panel: Path = PANEL_PATH,
) -> Path:
    path.mkdir(parents=True)
    snapshot_inputs(
        path / "inputs",
        criteria_path=criteria,
        panel_path=panel,
        probes_path=handoff.get("probes.yml", PROBES),
    )
    corpus = criteria.parent / "corpus.yml"
    if not corpus.is_file():
        corpus = FIX / "corpus.sample.yml"
    shutil.copyfile(corpus, path / "inputs/corpus.yml")
    (path / "handoff").mkdir()
    shas = {}
    for name, source in handoff.items():
        shutil.copy(source, path / "handoff" / name)
        shas[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    inputs = json.loads((path / "inputs" / "inputs.json").read_text())
    _json(
        path / "workspace.json",
        {"kind": kind, "created_at": "2026-11-04T00:00:00Z", "inputs": inputs, "handoff": shas},
    )
    return path


def test_write_tuning_picks_the_lowest_passing_effort(tmp_path: Path) -> None:
    ws = _workspace(tmp_path / "tuning", "tuning", {"probes.yml": PROBES})
    probes = load_probe_file(PROBES, criteria_path=CRITERIA, tuning_path=TUNING_PATH)
    tuning = [p for p in probes.probes if p.set == "tuning"]
    weights = ("nuance", "core")  # TUNE-001
    for probe in tuning:
        right = _vote_text(**_fields_for(probe.expected_score, probe.expected_label, weights))
        wrong = _vote_text()  # CORRECT for everything: INJ gets credit
        _vote(ws, "haiku", probe.id, wrong, target="tuning", effort="low")
        _vote(ws, "haiku", probe.id, right, target="tuning", effort="medium")
        _vote(ws, "luna", probe.id, right, target="tuning", effort="low")
    path = write_tuning(ws, panel_path=PANEL_PATH)
    assert path == ws / "tuning.json"
    tuning_json = json.loads(path.read_text())
    haiku = tuning_json["judges"]["haiku"]
    assert haiku["tried"] == ["low", "medium"] and haiku["chosen"] == "medium"
    assert haiku["efforts"]["low"]["inj_credit"] > 0 and haiku["efforts"]["medium"]["meets"]
    assert tuning_json["judges"]["luna"]["chosen"] == "low"
    assert tuning_json["judges"]["flash-lite"] == {"efforts": {}, "tried": [], "chosen": None}
    assert tuning_json["prompt_sha256"] == tuning_json["inputs"]["judge_prompt"]
    with pytest.raises(Exception, match="tuning"):
        write_g1(ws, panel_path=PANEL_PATH)


def _full_calibration(
    tmp_path: Path, index_db: Path, drop_fact: bool
) -> tuple[Path, Path, dict[str, Any], Path]:
    corpus = yaml.safe_load((FIX / "corpus.sample.yml").read_text())
    for question in corpus["questions"]:
        question["citations"] = ["https://docs.python.org/3.13/library/json.html#json.dumps"]
    corpus_path = tmp_path / "corpus.yml"
    corpus_path.write_text(yaml.safe_dump(corpus))
    criteria = yaml.safe_load(CRITERIA.read_text())
    criteria["corpus_sha256"] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    criteria_path = tmp_path / "criteria.yml"
    criteria_path.write_text(yaml.safe_dump(criteria))
    build = _workspace(tmp_path / "build", "probe-build", {}, criteria=criteria_path)

    def assemble_workspace():
        return assemble_workspace_probes(
            build,
            criteria_path=criteria_path,
            tuning_path=TUNING_PATH,
            panel_path=PANEL_PATH,
            index_path=index_db,
            corpus_path=corpus_path,
        )

    with pytest.raises(BenchmarkValidationError, match="PARA"):
        assemble_workspace()
    assert not (build / "probes.yml").exists()
    for questions in (criteria["questions"], load_tuning_questions(TUNING_PATH)):
        for (qid, kind), (generator, checker) in assignments(
            sorted(questions), PANEL.generators
        ).items():
            rounds = 3 if drop_fact and (qid, kind) == ("SYN-EX-001", "FACT") else 1
            for r in range(1, rounds + 1):
                _vote(
                    build,
                    generator,
                    safe_id("gen", kind, qid, f"r{r}"),
                    json.dumps({"probe_text": "Scripted candidate."}),
                    target="probe-gen",
                    effort=None,
                    schema=PROBE_SCHEMA,
                )
                _vote(
                    build,
                    checker,
                    safe_id("chk", kind, qid, f"r{r}"),
                    json.dumps({"explanation": "Scripted check.", "meets_spec": rounds == 1}),
                    target="probe-check",
                    effort=None,
                    schema=CHECK_SCHEMA,
                )
    probes_path = assemble_workspace()
    assert probes_path == build / "probes.yml" and not (build / "g1.json").exists()
    original = probes_path.read_bytes()
    criteria_bytes = criteria_path.read_bytes()
    criteria_path.write_bytes(criteria_bytes + b"\n# replacement\n")
    with pytest.raises(BenchmarkValidationError, match="criteria"):
        assemble_workspace()
    assert probes_path.read_bytes() == original
    criteria_path.write_bytes(criteria_bytes)
    probes = load_probe_file(probes_path, criteria_path=criteria_path, tuning_path=TUNING_PATH)
    held_out = [p for p in probes.probes if p.set == "held_out"]
    assert len({p.id for p in held_out}) == (698 if drop_fact else 700)
    assert len({p.type for p in held_out}) == 14 and len({p.question_id for p in held_out}) == 50
    assert len({p.question_id for p in probes.probes if p.set == "tuning"}) == 10
    assert any(d["reason"] == "fact_dropped" for d in probes.dropped) == drop_fact
    assert any(d["reason"] == "no_pad_text" for d in probes.dropped)
    # Select efforts using actual synthetic tuning votes; held-out consumes the exported record.
    tuning_questions = load_tuning_questions(TUNING_PATH)
    judges = JUDGES if drop_fact else ("haiku", "luna", "flash")
    tuning_panel = PANEL_PATH
    if not drop_fact:
        # Tuning precedes the effort freeze. Its panel hash legitimately differs.
        unfrozen = yaml.safe_load(PANEL_PATH.read_bytes())
        for judge in judges:
            unfrozen["judges"][judge]["effort"] = None
        tuning_panel = tmp_path / "tuning-panel.yml"
        tuning_panel.write_text(yaml.safe_dump(unfrozen))
    tuning = _workspace(
        tmp_path / "tune",
        "tuning",
        {"probes.yml": probes_path},
        criteria=criteria_path,
        panel=tuning_panel,
    )
    for probe in probes.probes:
        if probe.set == "tuning":
            weight = tuning_questions[probe.question_id].weights
            text = _vote_text(**_fields_for(probe.expected_score, probe.expected_label, weight))
            for judge in judges:
                _vote(tuning, judge, probe.id, text, target="tuning")
    assert main(["agentic-score", "--workspace", str(tuning), "--step", "tuning"]) == 0
    tuning_file = tuning / "tuning.json"
    held = _workspace(
        tmp_path / "held",
        "held-out",
        {"probes.yml": probes_path, "tuning-1.json": tuning_file},
        criteria=criteria_path,
    )
    meta = json.loads((held / "workspace.json").read_bytes())
    meta.update(judges=list(judges), tuning_sha256=probes.header["tuning_sha256"])
    _json(held / "workspace.json", meta)
    for probe in held_out:
        text = _vote_text(
            **_fields_for(probe.expected_score, probe.expected_label, ("core", "nuance"))
        )
        for judge in judges:
            _vote(held, judge, probe.id, text, target="probes")
    seed = probes.header["corpus_sha256"]
    selected = reference_subset(held_out, seed=seed)
    assert len({p.id for p in selected}) == 350
    assert Counter(p.type for p in selected) == dict.fromkeys({p.type for p in held_out}, 25)
    assert {p.id for p in reference_subset(list(reversed(held_out)), seed=seed)} == {
        p.id for p in selected
    }
    for probe in selected:
        text = _vote_text(
            **_fields_for(probe.expected_score, probe.expected_label, ("core", "nuance"))
        )
        _vote(held, "reference", probe.id, text, target="reference-probes", effort="high")
    _json(
        held / "runs" / "run-1.json",
        {
            "grading_run_id": "run-1",
            "target": "probes",
            "pass": 1,
            "composition": {
                j: {"model_id": PANEL.judges[j].model_id, "effort": "low"} for j in judges
            },
            "not_run": [],
        },
    )
    assert main(["agentic-score", "--workspace", str(held), "--step", "g1"]) == 0
    g1_path = held / "g1.json"
    assert g1_path == held / "g1.json" and not (held / "probes.yml").exists()
    entry = json.loads(g1_path.read_text())["entries"]["+".join(judges)]
    assert entry["gate"]["passed"] is True and entry["reference_check"]["passed"] is True
    assert entry["reference_check"]["details"]["overall"]["n"] == 350
    assert set(entry["binding"]["composition"]) == set(judges)
    inputs = json.loads((held / "inputs" / "inputs.json").read_text())
    assert entry["binding"]["inputs"]["probes"] == inputs["probes"]
    assert entry["tuning"] == [json.loads(tuning_file.read_bytes())]
    run = _build_run(
        tmp_path, corpus=corpus_path, criteria=criteria_path, probes=probes_path, judges=judges
    )
    assert main(["agentic-score", "--run-dir", str(run), "--g1", str(g1_path)]) == 0
    summary = json.loads((run / "grading/summary.json").read_bytes())
    assert summary["reference_check"]["passed"] is True
    assert summary["gates"]["G1"]["passed"] is True
    return run, held, entry, criteria_path


@pytest.mark.parametrize("drop_fact", [False, True])
def test_assemble_workspace_probes_and_write_g1_are_separate_steps(
    tmp_path: Path, index_db: Path, drop_fact: bool
) -> None:
    run, held, entry, criteria_path = _full_calibration(tmp_path, index_db, drop_fact)
    g1_path = held / "g1.json"
    judges = JUDGES if drop_fact else ("haiku", "luna", "flash")
    entry["gate"]["details"]["by_type"]["foreign"] = {"n": 1}
    _json(g1_path, {"entries": {"+".join(judges): entry}})
    before = g1_path.read_bytes()
    with pytest.raises(BenchmarkValidationError, match="aggregate evidence"):
        score_run_dir(
            run,
            criteria_path=criteria_path,
            panel_path=PANEL_PATH,
            schema_path=SCHEMA,
            g1_paths=[g1_path],
        )
    assert g1_path.read_bytes() == before
    # Even 349 perfect comparisons cannot stand in for the registered 350.
    missing = next((held / "votes/reference-probes").rglob("run-1--a1.json"))
    missing.unlink()
    (entry,) = json.loads(write_g1(held, panel_path=PANEL_PATH).read_text())["entries"].values()
    assert entry["gate"]["passed"] is True and entry["reference_check"]["passed"] is None

    # A relocated pure workspace may report its small calibration sample, but a
    # complete suite needs the tuning handoff before it can publish a G1 verdict.
    meta = json.loads((held / "workspace.json").read_bytes())
    del meta["judges"], meta["tuning_sha256"], meta["handoff"]["tuning-1.json"]
    (held / "handoff/tuning-1.json").unlink()
    _json(held / "workspace.json", meta)
    if drop_fact:
        shutil.rmtree(held / "votes/probes/haiku")  # Evaluable failures also need tuning.
    before = g1_path.read_bytes()
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        write_g1(held, panel_path=PANEL_PATH)
    assert g1_path.read_bytes() == before


def test_g1_exports_only_observed_held_out_retest_pairs(tmp_path: Path) -> None:
    held = _workspace(tmp_path / "held", "held-out", {"probes.yml": PROBES})
    _json(held / "runs/run-1.json", {"target": "probes", "pass": 1, "composition": COMPOSITION})
    keys = ["KEY--SYN-EX-001", "IDK--SYN-EX-002", "FACT--SYN-EX-001"]
    for key in keys:
        _vote(held, "haiku", key, _vote_text(attempted=key == keys[0]), target="probes")

    def entry():
        (result,) = json.loads(write_g1(held, panel_path=PANEL_PATH).read_text())[
            "entries"
        ].values()
        return result

    first = entry()
    for key in keys:
        text = "invalid" if key == keys[2] else _vote_text(attempted=key != keys[0])
        _vote(held, "haiku", key, text, target="probes", pass_number=2)
    # Valid votes for another effort or the tuning set do not belong to this comparison.
    _vote(held, "luna", keys[0], _vote_text(), target="probes", pass_number=2, effort="high")
    _vote(held, "haiku", "KEY--TUNE-001", _vote_text(), target="probes", pass_number=2)
    second = entry()
    metric = "held_out_test_retest"
    before = first["gate"]["details"].pop(metric)
    assert before["haiku"]["items"] == 0 and before["haiku"]["alpha"] is None
    observed = second["gate"]["details"].pop(metric)
    first_source, second_source = first.pop("source"), second.pop("source")
    assert first == second
    assert first_source["inputs"] == second_source["inputs"]
    assert all(second_source["files"][k] == v for k, v in first_source["files"].items())
    added = set(second_source["files"]) - set(first_source["files"])
    assert added == {
        f"votes/probes/haiku/low/pass2/{key}/run-1--a1.json" for key in keys
    } | {
        # Inventory reconciliation retains other allocated coordinates too;
        # they still do not enter this frozen held-out comparison.
        "votes/probes/luna/high/pass2/KEY--SYN-EX-001/run-1--a1.json",
        "votes/probes/haiku/low/pass2/KEY--TUNE-001/run-1--a1.json",
    }
    haiku = observed["haiku"]
    assert (haiku["alpha"], haiku["items"], haiku["expected"], haiku["pass2_records"]) == (
        -0.5,
        2,
        28,
        3,
    )
    bad = haiku["observations"][keys[2]]
    assert bad["withheld"] == "parse_failure" and bad["pass2"] is None
    assert bad["pass1_record"] == bad["pass2_record"] == {"grading_run_id": "run-1", "attempt": 1}
    assert observed["luna"]["pass2_records"] == 0
    run = _build_run(tmp_path)
    summary = score_run_dir(
        run,
        criteria_path=CRITERIA,
        panel_path=PANEL_PATH,
        schema_path=SCHEMA,
        g1_paths=[held / "g1.json"],
    )
    shutil.rmtree(held)
    assert summary["gates"]["G1"]["details"][metric] == observed
    assert summary["diagnostics"]["test_retest"] is None
    (copy,) = summary["disclosure"]["g1_files"]
    (exported,) = json.loads((run / "grading" / copy["path"]).read_text())["entries"].values()
    assert exported["binding"] == first["binding"]
    assert exported["gate"]["details"][metric] == observed


@pytest.mark.parametrize(
    "field,value", [("input_sha256", "b" * 64), ("model_id", "unrelated"), ("pass", 1)]
)
def test_g1_rejects_a_foreign_retest(field, value, tmp_path):
    from benchmarks.runner import BenchmarkValidationError

    held = _workspace(tmp_path / "held", "held-out", {"probes.yml": PROBES})
    _json(held / "runs/run-1.json", {"target": "probes", "pass": 1, "composition": COMPOSITION})
    key = "KEY--SYN-EX-001"
    for number in (1, 2):
        _vote(held, "haiku", key, _vote_text(), target="probes", pass_number=number)
    (path,) = (held / "votes/probes/haiku/low/pass2" / key).glob("*.json")
    _json(path, {**json.loads(path.read_text()), field: value})
    with pytest.raises(BenchmarkValidationError, match=key):
        write_g1(held, panel_path=PANEL_PATH)
    assert not (held / "g1.json").exists()


def test_cli_agentic_score_needs_no_network_and_exits_2_on_bad_input(
    scripted_graded_run: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("agentic-score opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    g1 = _g1_file(scripted_graded_run, tmp_path / "g1.json")
    argv = [
        "agentic-score",
        "--run-dir",
        str(scripted_graded_run),
        "--g1",
        str(g1),
        "--criteria",
        str(CRITERIA),
        "--panel",
        str(PANEL_PATH),
        "--resamples",
        "10000",
    ]
    assert main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["gates"]["G1"]["passed"] is None
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--run-dir", str(tmp_path / "missing")])
    assert exited.value.code == 2
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--workspace", str(tmp_path / "missing"), "--step", "g1"])
    assert exited.value.code == 2
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--workspace", str(tmp_path), "--run-dir", str(tmp_path)])
    assert exited.value.code == 2


@pytest.mark.parametrize("paired", [True, False])
def test_spi_keeps_observed_point_when_question_draws_drop_a_family(
    tmp_path: Path, paired: bool
) -> None:
    families = {
        FAMILY: {"python-docs": QUESTIONS if paired else QUESTIONS[:1]},
        "scripted-openai": {"python-docs": QUESTIONS if paired else QUESTIONS[1:]},
    }
    run = _build_run(tmp_path, families=families)
    for q in families[FAMILY]["python-docs"]:
        item = safe_id(_cell("python-docs", FAMILY), q)
        shutil.rmtree(run / "grading/votes/answers/haiku/low/pass1" / item)
        _vote(run / "grading", "haiku", item, _vote_text(attempted=False))
    spi = _score(run, tmp_path)["diagnostics"]["self_preference"]
    assert spi["haiku/anthropic"] == {
        "point": -1.0,
        "low": -1.0 if paired else None,
        "high": -1.0 if paired else None,
    }
    assert spi["haiku/openai"]["point"] == 1.0


def test_spi_discloses_family_when_all_its_comparisons_are_withheld(tmp_path: Path) -> None:
    one = {"python-docs": QUESTIONS[:1]}
    run = _build_run(tmp_path, families={FAMILY: one, "scripted-openai": one})
    item = safe_id(_cell("python-docs", "scripted-openai"), QUESTIONS[0])
    shutil.rmtree(run / "grading/votes/answers/luna/low/pass1" / item)
    _vote(run / "grading", "luna", item, "withheld")
    spi = _score(run, tmp_path)["diagnostics"]["self_preference"]
    assert spi == {
        f"{judge}/{family}": None for judge in JUDGES for family in ("anthropic", "openai")
    }


def test_short_fixture_can_be_scored_without_certifying_calibration(tmp_path):
    held = _workspace(tmp_path / "held", "held-out", {"probes.yml": PROBES})
    suite = load_probe_file(PROBES, criteria_path=CRITERIA, tuning_path=TUNING_PATH)
    probes = [p for p in suite.probes if p.set == "held_out"]
    assert len(probes) == 28
    for p in probes:
        text = _vote_text(**_fields_for(p.expected_score, p.expected_label, ("core", "nuance")))
        for j in JUDGES:
            _vote(held, j, p.id, text, target="probes")
        _vote(held, "reference", p.id, text, target="reference-probes", effort="high")
    _json(held / "runs" / "run-1.json", {"target": "probes", "pass": 1, "composition": COMPOSITION})
    (entry,) = json.loads(write_g1(held, panel_path=PANEL_PATH).read_text())["entries"].values()
    assert entry["gate"]["details"]["overall"]["accuracy"] == 1.0
    assert entry["gate"]["passed"] is None
    assert entry["reference_check"]["passed"] is None


def test_foreign_reference_vote_file_cannot_score_the_requested_probe(tmp_path):
    held = _workspace(tmp_path / "held", "held-out", {"probes.yml": PROBES})
    _json(held / "runs/run-1.json", {"target": "probes", "pass": 1, "composition": COMPOSITION})
    item = "KEY--SYN-EX-001"
    _vote(held, "reference", item, _vote_text(), target="reference-probes", effort="high")
    (path,) = (held / "votes/reference-probes/reference/high/pass1" / item).glob("*.json")
    _json(path, {**json.loads(path.read_text()), "item_id": "KEY--TUNE-001"})
    with pytest.raises(BenchmarkValidationError, match=item):
        write_g1(held, panel_path=PANEL_PATH)
    assert not (held / "g1.json").exists()
