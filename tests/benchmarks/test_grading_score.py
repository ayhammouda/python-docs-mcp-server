"""agentic-score: grades, gates, claims and disclosure from a graded run (PR 5 Task 7).

The run directories here are synthetic: transcripts, scoring placeholders,
planned cells, frozen grading inputs and vote records written with
write_vote_record. No worker runs and nothing reaches the network.
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.__main__ import main
from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.inputs import TUNING_PATH, safe_id, snapshot_inputs
from benchmarks.grading.panel import load_panel
from benchmarks.grading.probes import load_probe_file, reference_subset
from benchmarks.grading.score import (
    assemble_workspace_probes,
    score_run_dir,
    write_g1,
    write_tuning,
)
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

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
        input_sha256="d" * 64,
        schema=schema or judge_schema(),
    )


def _cell(system: str, family: str = FAMILY) -> str:
    return f"{system}--{family}--r1"


def _build_run(
    tmp_path: Path, *, families: dict[str, dict[str, tuple[str, ...]]] | None = None
) -> Path:
    """A graded run: family -> system -> its questions, repeat 1."""
    families = families or {FAMILY: dict.fromkeys(SYSTEMS, QUESTIONS)}
    run = tmp_path / "run"
    (run / "snapshots").mkdir(parents=True)
    shutil.copy(FIX / "corpus.sample.yml", run / "snapshots" / "corpus.yml")
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
        grading / "inputs", criteria_path=CRITERIA, panel_path=PANEL_PATH, probes_path=PROBES
    )
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
                _json(
                    grading / "items" / "answers" / f"{item}.json",
                    {"meta": {"competitor_id": cell, "corpus_id": qid, "suspect": False}},
                )
                text = _vote_text(attempted=False) if system == "no-docs" else _vote_text()
                for judge in JUDGES:
                    _vote(grading, judge, item, text)
    _json(run / "planned-cells.json", {"cells": planned})
    _json(
        grading / "runs" / "run-1.json",
        {
            "grading_run_id": "run-1",
            "target": "answers",
            "pass": 1,
            "composition": COMPOSITION,
            "not_run": [],
        },
    )
    events = [
        {"event": "settle", "provider": "anthropic", "booked": "0.02", "unit_id": "u1"},
        {"event": "settle", "provider": "openai", "booked": "0.01", "unit_id": "u2"},
        {"event": "reserve", "provider": "openai", "bound": "9", "unit_id": "u2"},
    ]
    (grading / "ledger-events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return run


def _g1_file(run: Path, path: Path, *, passed: bool = True) -> Path:
    inputs = json.loads((run / "grading" / "inputs" / "inputs.json").read_text())
    entry = {
        "binding": {"composition": COMPOSITION, "inputs": inputs},
        "gate": {"passed": passed, "details": {"overall": {"accuracy": 0.97}}},
        "reference_check": {"passed": True, "details": {}},
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
        resamples=200,
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
    assert summary["gates"]["G1"]["passed"] is True
    assert summary["gates"]["G3"]["passed"] is not None
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
    one = ("SYN-EX-001",)
    run = _build_run(
        tmp_path,
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


def _fields_for(expected_score: float, expected_label: str, weights: tuple[str, str]) -> dict:
    if expected_label == "NOT_ATTEMPTED":
        return {"attempted": False}
    if expected_label == "INCORRECT":
        return {"contradicts_key": True}
    if expected_score == 0.5:
        return {"p1_met": False} if weights[0] == "nuance" else {"p2_met": False}
    return {}


def _workspace(path: Path, kind: str, handoff: dict[str, Path]) -> Path:
    path.mkdir(parents=True)
    snapshot_inputs(
        path / "inputs", criteria_path=CRITERIA, panel_path=PANEL_PATH, probes_path=PROBES
    )
    (path / "handoff").mkdir()
    shas = {}
    for name, source in handoff.items():
        shutil.copy(source, path / "handoff" / name)
        shas[name] = __import__("hashlib").sha256(source.read_bytes()).hexdigest()
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


def test_assemble_workspace_probes_and_write_g1_are_separate_steps(tmp_path: Path) -> None:
    index = tmp_path / "index.db"
    conn = get_readwrite_connection(index)
    bootstrap_schema(conn)
    conn.close()
    build = _workspace(tmp_path / "build", "probe-build", {})
    gen = safe_id("gen", "PARA", "SYN-EX-001", "r1")
    chk = safe_id("chk", "PARA", "SYN-EX-001", "r1")
    from benchmarks.grading.probes import CHECK_SCHEMA, PROBE_SCHEMA, assignments

    corpus_ids = sorted(yaml.safe_load(CRITERIA.read_text())["questions"])
    generator, checker = assignments(corpus_ids, PANEL.generators)[("SYN-EX-001", "PARA")]
    _vote(
        build,
        generator,
        gen,
        json.dumps({"probe_text": "Reworded key."}),
        target="probe-gen",
        effort=None,
        schema=PROBE_SCHEMA,
    )
    _vote(
        build,
        checker,
        chk,
        json.dumps({"explanation": "ok", "meets_spec": True}),
        target="probe-check",
        effort=None,
        schema=CHECK_SCHEMA,
    )
    probes_path = assemble_workspace_probes(
        build,
        criteria_path=CRITERIA,
        tuning_path=TUNING_PATH,
        panel_path=PANEL_PATH,
        index_path=index,
        corpus_path=FIX / "corpus.sample.yml",
    )
    assert probes_path == build / "probes.yml" and not (build / "g1.json").exists()
    built = load_probe_file(probes_path, criteria_path=CRITERIA, tuning_path=TUNING_PATH)
    para = [p for p in built.probes if p.type == "PARA"]
    assert [(p.id, p.text, p.built_by["round"]) for p in para] == [
        ("PARA--SYN-EX-001", "Reworded key.", 1)
    ]
    assert {p.set for p in built.probes} == {"held_out", "tuning"}
    assert any(d["type"] == "PAD" and d["reason"] == "no_pad_text" for d in built.dropped)

    tuning_file = tmp_path / "tuning.json"
    _json(tuning_file, {"judges": {"haiku": {"chosen": "low"}}})
    held = _workspace(
        tmp_path / "held", "held-out", {"probes.yml": PROBES, "tuning-1.json": tuning_file}
    )
    probes = load_probe_file(PROBES, criteria_path=CRITERIA, tuning_path=TUNING_PATH)
    held_out = [p for p in probes.probes if p.set == "held_out"]
    for probe in held_out:
        text = _vote_text(
            **_fields_for(probe.expected_score, probe.expected_label, ("core", "nuance"))
        )
        for judge in JUDGES:
            _vote(held, judge, probe.id, text, target="probes")
    seed = probes.header["corpus_sha256"]
    for probe in reference_subset(held_out, seed=seed):
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
            "composition": COMPOSITION,
            "not_run": [],
        },
    )
    g1_path = write_g1(held, panel_path=PANEL_PATH)
    assert g1_path == held / "g1.json" and not (held / "probes.yml").exists()
    entry = json.loads(g1_path.read_text())["entries"]["haiku+luna+flash-lite"]
    assert entry["gate"]["passed"] is True and entry["reference_check"]["passed"] is True
    assert entry["binding"]["composition"] == COMPOSITION
    inputs = json.loads((held / "inputs" / "inputs.json").read_text())
    assert entry["binding"]["inputs"]["probes"] == inputs["probes"]
    assert entry["tuning"] == [{"judges": {"haiku": {"chosen": "low"}}}]


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
        "100",
    ]
    assert main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["gates"]["G1"]["passed"] is True
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--run-dir", str(tmp_path / "missing")])
    assert exited.value.code == 2
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--workspace", str(tmp_path / "missing"), "--step", "g1"])
    assert exited.value.code == 2
    with pytest.raises(SystemExit) as exited:
        main(["agentic-score", "--workspace", str(tmp_path), "--run-dir", str(tmp_path)])
    assert exited.value.code == 2
