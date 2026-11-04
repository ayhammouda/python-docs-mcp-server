"""Replay the public scoring command against portable, synthetic grading bundles."""

import json
from pathlib import Path

import pytest

from benchmarks.__main__ import main
from tests.benchmarks import test_grading_score as runs

# Reuse the scripted voting bundle at the same command boundary as the scoring tests.
scripted_graded_run = runs.scripted_graded_run


def _files(run: Path) -> dict[Path, bytes]:
    return {path.relative_to(run): path.read_bytes() for path in run.rglob("*") if path.is_file()}


def _score(run: Path, g1: Path, *args: str) -> int:
    return main(["agentic-score", "--run-dir", str(run), "--g1", str(g1), *args])


def _change_weight(path: Path) -> None:
    original = path.read_bytes()
    anchor = b"States the fixture source that SYN-EX-002 cites.\n      weight: nuance"
    assert original.count(anchor) == 1
    path.write_bytes(original.replace(anchor, anchor.replace(b"nuance", b"core")))


def test_cli_replays_the_frozen_bundle(scripted_graded_run: Path, tmp_path: Path) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    assert _score(run, g1) == 0
    split = runs._record(run, "python-docs", "SYN-EX-002")
    summary = json.loads((run / "grading/summary.json").read_bytes())
    assert split["score"] == 0.5
    assert summary["claims"]["C2"][runs.FAMILY]["interval"]["point"] == pytest.approx(2 / 3)
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["gates"]["G1"]["details"]["reason"]
    assert summary["gates"]["G1"]["details"]["overall"]["accuracy"] == 1.0
    assert summary["resamples"] == 10_000


@pytest.mark.parametrize("source", ["override", "snapshot", "same-path"])
def test_cli_rejects_a_single_changed_weight_before_writing(
    scripted_graded_run: Path, tmp_path: Path, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = scripted_graded_run
    frozen = run / "grading/inputs/grading-criteria.yml"
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    criteria = tmp_path / "original.yml"
    criteria.write_bytes(frozen.read_bytes())
    if source == "same-path":
        criteria = frozen
    args = ["--criteria", str(criteria), "--panel", str(runs.PANEL_PATH)]
    assert _score(run, g1, *args) == 0
    assert runs._record(run, "python-docs", "SYN-EX-002")["score"] == 0.5
    _change_weight(criteria if source == "override" else frozen)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1, *args)
    assert error.value.code == 2 and "criteria" in capsys.readouterr().err
    assert _files(run) == before


@pytest.mark.parametrize("changed", ["panel", "mapping_table", "g1", "model_id", "effort"])
def test_cli_rejects_mismatched_scoring_evidence_without_partial_writes(
    scripted_graded_run: Path, tmp_path: Path, changed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    if changed in {"model_id", "effort"}:
        path = run / "grading/runs/run-1.json"
        record = json.loads(path.read_bytes())
        record["composition"]["haiku"][changed] = "unexpected"
        runs._json(path, record)
        # Even a matching G1 cannot attest a judge setting outside the frozen panel.
        evidence = json.loads(g1.read_bytes())
        entry = next(iter(evidence["entries"].values()))
        entry["binding"]["composition"] = record["composition"]
        runs._json(g1, evidence)
    elif changed == "g1":
        evidence = json.loads(g1.read_bytes())
        entry = next(iter(evidence["entries"].values()))
        entry["binding"]["inputs"]["criteria"] = "0" * 64
        runs._json(g1, evidence)
    else:
        name = "mapping.txt" if changed == "mapping_table" else "panel.yml"
        path = run / "grading/inputs" / name
        path.write_bytes(path.read_bytes() + b"\n")
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1, "--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH))
    assert error.value.code == 2
    assert ("criteria" if changed == "g1" else changed) in capsys.readouterr().err
    assert _files(run) == before


def test_cli_cannot_publish_a_one_draw_interval(scripted_graded_run: Path, tmp_path: Path) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(
            run,
            g1,
            "--criteria",
            str(runs.CRITERIA),
            "--panel",
            str(runs.PANEL_PATH),
            "--resamples",
            "1",
        )
    assert error.value.code == 2
    assert _files(run) == before


def test_cli_rejects_conflicting_g1_files_and_clears_previous_selection(
    scripted_graded_run: Path, tmp_path: Path
) -> None:
    run = scripted_graded_run
    passed = runs._g1_file(run, tmp_path / "pass.json")
    failed = runs._g1_file(run, tmp_path / "fail.json", passed=False)
    args = ["--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH)]
    assert _score(run, passed, *args) == 0
    before = _files(run)
    for first, last in ((passed, failed), (failed, passed)):
        with pytest.raises(SystemExit) as error:
            _score(run, first, "--g1", str(last), *args)
        assert error.value.code == 2
        assert _files(run) == before
    assert main(["agentic-score", "--run-dir", str(run), *args]) == 0
    selected = run / "grading/disclosure"
    assert not (selected / "g1-entries.json").exists()
    assert not (selected / "tuning.json").exists()
    assert list((selected / "g1").glob("*.json"))


@pytest.mark.parametrize(
    "name", ["grading-criteria.yml", "panel.yml", "judge-prompt.md", "judge-schema.json"]
)
def test_cli_uses_the_verified_bytes_even_if_the_path_is_replaced(
    scripted_graded_run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    frozen = run / "grading/inputs" / name
    replacement = tmp_path / name
    replacement.write_bytes(b"{}")
    from tests.benchmarks.test_workspace_binding import _replace_after_open

    _replace_after_open(monkeypatch, frozen, replacement)
    assert _score(run, g1, "--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH)) == 0
    assert not replacement.exists()
    assert runs._record(run, "python-docs", "SYN-EX-002")["score"] == 0.5


@pytest.mark.parametrize(
    "field",
    ["model_id", "effort", "judge_id", "pass", "target", "item_id", "grading_run_id", "attempt"],
)
def test_cli_binds_chosen_vote_to_its_frozen_judge(
    scripted_graded_run: Path, tmp_path: Path, field: str
) -> None:
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    path = next((run / "grading/votes/answers/haiku").rglob("*.json"))
    vote = json.loads(path.read_bytes())
    vote[field] = 7 if field == "attempt" else "different"
    runs._json(path, vote)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1, "--criteria", str(runs.CRITERIA), "--panel", str(runs.PANEL_PATH))
    assert error.value.code == 2
    assert _files(run) == before


def test_report_discloses_gate_draws_separately_from_descriptive_intervals(tmp_path: Path) -> None:
    from tests.benchmarks import test_agentic_report as reports

    run = reports._score(reports._build(tmp_path), tmp_path)
    report = reports._render(run)
    assert "Gate and claim bootstrap resamples: 10000" in report
    assert "2000 resamples" in report
    path = run / "grading/summary.json"
    old_summary = json.loads(path.read_bytes())
    del old_summary["resamples"]
    runs._json(path, old_summary)
    assert "Gate and claim bootstrap resamples: not recorded" in reports._render(run)


@pytest.mark.parametrize("fault", ["extra judge", "missing probes", "missing binding"])
def test_cli_requires_a_complete_matching_g1_binding(
    scripted_graded_run: Path, tmp_path: Path, fault: str
) -> None:
    run = scripted_graded_run
    root = run / "grading/inputs"
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    if fault == "missing probes":
        manifest = json.loads((root / "inputs.json").read_bytes())
        del manifest["probes"]
        runs._json(root / "inputs.json", manifest)
        (root / "probes.yml").unlink()
    evidence = json.loads(g1.read_bytes())
    entry = next(iter(evidence["entries"].values()))
    if fault == "extra judge":
        entry["binding"]["composition"]["unexpected"] = entry["binding"]["composition"]["haiku"]
    elif fault == "missing binding":
        del entry["binding"]
    runs._json(g1, evidence)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1)
    assert error.value.code == 2
    assert _files(run) == before
    if fault == "missing probes":
        assert main(["agentic-score", "--run-dir", str(run)]) == 0
        summary = json.loads((run / "grading/summary.json").read_bytes())
        assert summary["gates"]["G1"]["passed"] is None
        assert runs._record(run, "python-docs", "SYN-EX-002")["score"] == 0.5


@pytest.mark.parametrize(
    "fault",
    [
        "changed answer",
        "panel digest",
        "cross-answer",
        "missing digest",
        "reference",
        "pass2",
        "item",
        "suspect",
        "relabel item",
    ],
)
def test_cli_checks_the_actual_judge_request(scripted_graded_run, tmp_path, fault):
    run = scripted_graded_run
    grading = run / "grading"
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    cell, qid = runs._cell("docs-fetch"), runs.QUESTIONS[0]
    item = runs.safe_id(cell, qid)
    if fault == "pass2":
        runs._vote(grading, "haiku", item, runs._vote_text(), pass_number=2)
    assert _score(run, g1) == 0
    good = runs._record(run, "docs-fetch", qid)
    summary = json.loads((grading / "summary.json").read_bytes())
    assert good["score"] == 1.0 and summary["pending_cells"] == []
    assert summary["claims"]["C2"][runs.FAMILY]["interval"]["point"] == pytest.approx(2 / 3)
    assert summary["spend"]["by_provider"] == {"anthropic": "0.02", "openai": "0.01"}
    expected = "ad71ed1df648b08e26cd34747400baa9f4d14f4d28c74780748e89c33cb15fb3"
    assert good["grading"]["input_sha256"] == expected
    if fault == "changed answer":
        path = run / "transcripts" / cell / f"{qid}.json"
        runs._json(
            path, {**json.loads(path.read_bytes()), "answer": "Different substantive answer."}
        )
    elif fault in {"item", "suspect", "relabel item"}:
        path = grading / "items/answers" / f"{item}.json"
        evidence = json.loads(path.read_bytes())
        if fault == "item":
            del evidence["meta"]["input_sha256"]
        elif fault == "suspect":
            evidence["meta"]["suspect"] = True
        else:
            evidence["meta"]["competitor_id"] = runs._cell("no-docs")
        runs._json(path, evidence)
    else:
        coordinates = {
            "pass2": "answers/haiku/low/pass2",
            "reference": "reference/reference/high/pass1",
        }.get(fault, "answers/luna/low/pass1")
        path = next((grading / "votes" / coordinates / item).glob("*.json"))
        vote = json.loads(path.read_bytes())
        assert vote["input_sha256"] == expected
        if fault == "missing digest":
            del vote["input_sha256"]
        elif fault == "cross-answer":
            vote["input_sha256"] = runs._record(run, "python-docs", qid)["grading"]["input_sha256"]
        else:
            vote["input_sha256"] = "d" * 64
        runs._json(path, vote)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1)
    assert error.value.code == 2 and _files(run) == before


def test_public_claim_rechecks_answer_after_scoring(tmp_path):
    from benchmarks.agentic_report import _RunMetadata
    from tests.benchmarks import test_agentic_report as reports

    run = reports._live_like_run(tmp_path)
    cell = runs._record(run, "docs-fetch", runs.QUESTIONS[0])
    assert cell["score"] == 1.0
    assert cell["grading"]["input_sha256"] == (
        "3553b8ad031d0517ff04d4d5baadaa093742864fbfa74870bd66a03bde20e8a8"
    )
    verified = _RunMetadata(reports._load_run_bundle(run))
    assert (verified.answering, verified.grading) == ("live", "live")
    assert "No public claim" in reports._readme(run)
    reports._validated(run)  # An adversarial tier must not bypass the answer check.
    path = run / "transcripts" / runs._cell("docs-fetch") / f"{runs.QUESTIONS[0]}.json"
    runs._json(path, {**json.loads(path.read_bytes()), "answer": "An entirely different answer."})
    raw = reports._section(reports._render(run), "## Claims")
    assert r"not evaluable\: missing or inconsistent grading evidence" in raw
    readme = reports._readme(run)
    assert "Execution provenance is unknown for answering and grading." in readme
    assert reports.ROUND_1_SCOPE not in readme


def test_scorer_uses_frozen_renderer_after_worktree_edits(
    scripted_graded_run, tmp_path, monkeypatch
):
    from benchmarks.grading import inputs

    g1 = runs._g1_file(scripted_graded_run, tmp_path / "g1.json")
    # Simulate replacement of both worktree assets after the votes were recorded.
    for attr in ("JUDGE_PROMPT_PATH", "JUDGE_SCHEMA_PATH"):
        path = tmp_path / attr
        path.write_bytes(b"unusable edited worktree asset")
        monkeypatch.setattr(inputs, attr, path)
    assert _score(scripted_graded_run, g1) == 0
    assert runs._record(scripted_graded_run, "docs-fetch", runs.QUESTIONS[0])["score"] == 1.0


@pytest.mark.parametrize("which", ["vote", "sample", "reference item"])
def test_cli_refuses_redirected_grade_inputs_before_writing(scripted_graded_run, tmp_path, which):
    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    grading = run / "grading"
    path = {
        "vote": next((grading / "votes/answers").rglob("*.json")),
        "sample": grading / "reference-sample.json",
        "reference item": next((grading / "items/reference").glob("*.json")),
    }[which]
    outside = tmp_path / "outside.json"
    path.rename(outside)
    path.symlink_to(outside)
    before = _files(run)
    with pytest.raises(SystemExit) as error:
        _score(run, g1)
    assert error.value.code == 2 and before == _files(run)


@pytest.mark.parametrize("tamper", ["scores", "omit vote", "superseded vote", "bound effort"])
def test_public_claim_requires_current_scores_from_all_final_votes(tmp_path, tamper):
    from dataclasses import asdict

    from benchmarks.grading.gates import c2
    from tests.benchmarks import test_agentic_report as reports

    run = reports._validated(reports._live_like_run(tmp_path))
    family = reports.FAMILIES[0]
    grading = run / "grading"
    score_path = run / "scoring" / runs._cell("docs-fetch") / f"{runs.QUESTIONS[0]}.json"
    current = json.loads(score_path.read_bytes())
    if tamper == "scores":
        for path in score_path.parent.glob("*.json"):
            runs._json(path, {**json.loads(path.read_bytes()), "score": 0.5})
        scores, counts = {}, {}
        for system in ("docs-fetch", "python-docs"):
            for qid in runs.QUESTIONS:
                cell = runs._cell(system)
                key = (system, family, qid, 1)
                scores[key] = runs._record(run, system, qid)["score"]
                counts[key] = json.loads((run / "tokens" / cell / f"{qid}.json").read_bytes())[
                    "client_wrapped_tokens"
                ]
        summary_path = grading / "summary.json"
        summary = json.loads(summary_path.read_bytes())
        comparison = c2(counts, scores, set(scores), model=family, seed=summary["seed"])
        assert comparison.passed and comparison.interval.point == 0.25
        summary["claims"]["C2"][family] = asdict(comparison)
        runs._json(summary_path, summary)
    elif tamper == "bound effort":
        current["grading"]["votes"]["luna"]["effort"] = "different"
        runs._json(score_path, current)
    elif tamper == "omit vote":
        del current["grading"]["votes"]["luna"]
        runs._json(score_path, current)
    else:
        path = next(
            (grading / "votes/answers/luna/low/pass1" / current["grading"]["item_id"]).glob(
                "*.json"
            )
        )
        vote = json.loads(path.read_bytes())
        runs._json(
            path.with_name("run-1--a2.json"), {**vote, "attempt": 2, "graded_at": "2099-01-01"}
        )
    readme = reports._readme(run)
    assert "Execution provenance is unknown for grading." in readme
    assert reports.ROUND_1_SCOPE not in readme


def test_cli_verifies_the_masked_answer_seen_by_the_panel(tmp_path):
    from benchmarks.grading.inputs import load_criteria

    run = runs._build_run(tmp_path)
    grading = run / "grading"
    cell, qid = runs._cell("docs-fetch"), runs.QUESTIONS[0]
    item = runs.safe_id(cell, qid)
    path = run / "transcripts" / cell / f"{qid}.json"
    transcript = json.loads(path.read_bytes())
    transcript["answer"] = "See https://docs.python.org/3/library/json.html."
    runs._json(path, transcript)
    question = load_criteria(
        runs.CRITERIA, corpus_path=run / "snapshots/corpus.yml", schema_path=runs.SCHEMA
    )[qid]
    runs._answer_item(grading, question, cell, transcript["answer"])
    for judge in runs.JUDGES:
        runs._vote(grading, judge, item, runs._vote_text(), attempt=2)
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    assert _score(run, g1) == 0
    binding = runs._record(run, "docs-fetch", qid)["grading"]
    runs._json(path, {**transcript, "answer": "See https://docs.python.org/3/library/abc.html."})
    assert _score(run, g1) == 0
    assert runs._record(run, "docs-fetch", qid)["grading"] == binding


def test_chosen_vote_is_scored_and_hashed_from_one_capture(
    scripted_graded_run, tmp_path, monkeypatch
):
    import hashlib

    from benchmarks.grading import aggregate

    run = scripted_graded_run
    g1 = runs._g1_file(run, tmp_path / "g1.json")
    cell, qid = runs._cell("docs-fetch"), runs.QUESTIONS[0]
    path = next(
        (run / "grading/votes/answers/luna/low/pass1" / runs.safe_id(cell, qid)).glob("*.json")
    )
    original = path.read_bytes()
    capture = aggregate.artifact_bytes
    captures = 0

    def swap(root, name):
        nonlocal captures
        body = capture(root, name)
        if name == path:
            captures += 1
            runs._json(path, {**json.loads(body), "input_sha256": "d" * 64})
        return body

    monkeypatch.setattr(aggregate, "artifact_bytes", swap)
    assert _score(run, g1) == 0
    record = runs._record(run, "docs-fetch", qid)
    assert captures == 1 and record["score"] == 1.0
    assert record["grading"]["votes"]["luna"]["sha256"] == hashlib.sha256(original).hexdigest()
    with pytest.raises(SystemExit) as error:
        _score(run, g1)
    assert error.value.code == 2
