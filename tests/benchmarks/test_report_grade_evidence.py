"""Public grades must agree with their owning votes, requests and frozen rubric.

All exports are benign recordings; no provider, external data or credentials.
"""

import hashlib
import json
import shutil

import pytest
import yaml

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading import inputs as grading_inputs
from benchmarks.grading.aggregate import final_grade, load_answer_votes
from benchmarks.grading.inputs import load_criteria
from benchmarks.grading.panel import load_panel
from benchmarks.report import _load_run_bundle
from tests.benchmarks.accepted_report_fixture import rebuild_grading
from tests.benchmarks.test_agentic_report import (
    GFIX,
    JUDGES,
    SCHEMA,
    _json,
    _live_like_run,
    _readme,
    _render,
    _score,
    _validated,
)

CELL = "docs-fetch--scripted-anthropic--r1"
QID = "SYN-EX-001"


def _read(path):
    return json.loads(path.read_bytes())


def _record(run):
    return run / "scoring" / CELL / f"{QID}.json"


def _vote(run, judge="haiku"):
    item = f"{CELL}--{QID}"
    return next((run / "grading/votes/answers" / judge / "low/pass1" / item).glob("*.json"))


def _regrade(run):
    """Persist scorer output after deliberately changing a recording's evidence."""
    root = run / "grading"
    panel = load_panel(root / "inputs/panel.yml")
    questions = load_criteria(
        GFIX / "criteria.sample.yml", corpus_path=run / "snapshots/corpus.yml", schema_path=SCHEMA
    )
    inputs = _read(root / "inputs/inputs.json")
    for key, av in load_answer_votes(
        root, panel=panel, judges=dict.fromkeys(JUDGES, "low")
    ).items():
        grade = final_grade(av, questions[key[1]], inputs=inputs)
        if grade is not None:
            path = run / "scoring" / key[0] / f"{key[1]}.json"
            record = _read(path)
            record.update({k: grade[k] for k in ("score", "label", "adjudicator", "grading")})
            record["scoring_method"] = grade["method"]
            _json(path, record)


@pytest.fixture(scope="module")
def recorded_template(tmp_path_factory):
    return _live_like_run(tmp_path_factory.mktemp("grade-recording"))


@pytest.fixture
def recording(tmp_path, recorded_template):
    return shutil.copytree(recorded_template, tmp_path / "recorded")


def test_intact_realistic_export_retains_live_panel_and_suspect_grades(recording):
    run = _RunMetadata(_load_run_bundle(recording))
    assert run.grading == "live"
    record = _read(_record(recording))
    assert (record["score"], record["label"], record["scoring_method"]) == (
        1,
        "CORRECT",
        "llm_panel",
    )
    suspect = _read(recording / "scoring/python-docs--scripted-openai--r1/SYN-EX-002.json")
    assert (suspect["score"], suspect["label"], suspect["scoring_method"]) == (
        0.5,
        "PARTIAL",
        "llm_reference",
    )
    assert run.tier != "Validated"
    assert "fewer normalized tokens" not in _readme(recording)


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", 0.5),
        ("score", True),
        ("label", "PARTIAL"),
        ("scoring_method", "llm_reference"),
        ("adjudicator", ["unrelated-model"]),
        ("grading.valid_votes", 1),
        ("grading.panel_fields", {}),
        ("grading.split_fields", ["p1_met"]),
        ("grading.settled_fields", ["p1_met"]),
        ("grading.injection_suspect", True),
        ("grading.input_sha256", "a" * 64),
        ("grading.pass", 2),
        ("grading.pass", True),
        ("grading.votes", {}),
    ],
)
def test_altered_persisted_grade_is_unknown_and_not_public(recording, field, value):
    path = _record(recording)
    record = _read(path)
    if field.startswith("grading."):
        record["grading"][field.split(".")[1]] = value
    else:
        record[field] = value
    _json(path, record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"
    assert "fewer normalized tokens" not in _readme(_validated(recording))
    assert "grading provenance unknown" in _render(recording)
    assert "## Correctness" in _render(recording)  # retain raw diagnostics


@pytest.mark.parametrize(
    "mutation",
    [
        "digest",
        "text",
        "fields",
        "missing-panel",
        "newer-final",
        "nonfinal",
        "source-hash",
        "source-meta",
    ],
)
def test_chosen_evidence_must_match_recorded_content_and_selection(recording, mutation):
    path = _vote(recording)
    vote = _read(path)
    if mutation == "digest":
        vote["input_sha256"] = "d" * 64
    elif mutation == "text":
        vote["text"] = json.dumps({**vote["vote"], "p1_met": False})
    elif mutation == "fields":
        vote["vote"]["p1_met"] = "false"
    elif mutation == "missing-panel":
        path.unlink()
    elif mutation == "newer-final":
        vote["attempt"] = 2
        vote["graded_at"] = "2099-01-01T00:00:00Z"
        _json(path.with_name("run-1--a2.json"), vote)
    elif mutation == "nonfinal":
        vote.update(status="errored", terminal=False, vote=None, text=None)
    elif mutation == "source-hash":
        record = _read(_record(recording))
        record["grading"]["votes"]["haiku"]["sha256"] = "f" * 64
        _json(_record(recording), record)
    elif mutation == "source-meta":
        record = _read(_record(recording))
        record["grading"]["votes"]["haiku"]["input_sha256"] = "f" * 64
        _json(_record(recording), record)
    if mutation not in {"missing-panel", "newer-final", "source-hash", "source-meta"}:
        _json(path, vote)
        if mutation == "fields":
            record = _read(_record(recording))
            record["grading"]["votes"]["haiku"]["sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            _json(_record(recording), record)
        else:
            _regrade(recording)  # refreshed hashes cannot bind inconsistent vote content
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


@pytest.mark.parametrize("mutation", ["rubric", "mapping", "transcript", "item", "legacy"])
def test_unavailable_or_mismatched_request_evidence_is_unknown(recording, mutation):
    root = recording / "grading"
    item = root / "items/answers" / f"{CELL}--{QID}.json"
    if mutation == "legacy":
        item.unlink()
    elif mutation == "item":
        record = _read(item)
        record["user"] += "different answer"
        _json(item, record)
    elif mutation == "transcript":
        path = recording / "transcripts" / CELL / f"{QID}.json"
        record = _read(path)
        record["answer"] += " changed after grading"
        _json(path, record)
    else:
        path = root / "inputs" / ("grading-criteria.yml" if mutation == "rubric" else "mapping.txt")
        if mutation == "rubric":
            data = yaml.safe_load(path.read_bytes())
            # The suspect answer has p2=False: nuance -> 0.5, core -> 0.0.
            data["questions"]["SYN-EX-002"][1]["weight"] = "core"
            path.write_text(yaml.safe_dump(data))
        else:
            path.write_text("All votes earn 1.0 CORRECT\n")
        manifest = root / "inputs/inputs.json"
        data = _read(manifest)
        data["criteria" if mutation == "rubric" else "mapping_table"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        _json(manifest, data)
        for path in (recording / "scoring").glob("*/*.json"):
            record = _read(path)
            record["grading"]["inputs"] = data
            _json(path, record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


def test_one_panel_vote_cannot_impersonate_the_registered_panel(recording):
    record = _read(_record(recording))
    record["grading"]["votes"] = {"haiku": record["grading"]["votes"]["haiku"]}
    _json(_record(recording), record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


@pytest.mark.parametrize(
    "payload", [[], {"votes": []}, {"votes": {"haiku": []}}, {"reference": []}]
)
def test_malformed_grade_evidence_is_unknown_without_crashing(recording, payload):
    path = _record(recording)
    record = _read(path)
    record["grading"] = payload
    _json(path, record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


@pytest.mark.parametrize(
    "fields,score,label",
    [
        ({"p2_met": False}, 0.5, "PARTIAL"),
        ({"p1_met": False}, 0, "INCORRECT"),
        ({"attempted": False}, 0, "NOT_ATTEMPTED"),
        ({"wrong_version": True}, 0, "INCORRECT"),
        ({"contradicts_key": True}, 0, "INCORRECT"),
    ],
)
def test_actual_panel_majority_maps_content_with_frozen_weights(recording, fields, score, label):
    for judge in ("haiku", "luna"):
        path = _vote(recording, judge)
        vote = _read(path)
        vote["vote"].update(fields)
        vote["text"] = json.dumps(vote["vote"])
        _json(path, vote)
    rebuild_grading(recording)
    _regrade(recording)
    record = _read(_record(recording))
    assert (record["score"], record["label"]) == (score, label)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


def _reference(recording):
    root = recording / "grading"
    item = f"{CELL}--{QID}"
    template = next((root / "votes/reference").glob("*/*/*/*/*.json"))
    vote = _read(template)
    vote.update(item_id=item, input_sha256=_read(_vote(recording))["input_sha256"])
    _json(template.parent.parent / item / template.name, vote)
    _json(root / "items/reference" / f"{item}.json", _read(root / "items/answers" / f"{item}.json"))


@pytest.mark.parametrize("withheld", [1, 2])
def test_real_split_or_insufficient_votes_use_the_reference(recording, withheld):
    # haiku p2=True, luna p2=False, flash withheld: a real 1–1 split.
    for judge in JUDGES:
        path = _vote(recording, judge)
        vote = _read(path)
        if judge == "flash-lite" or (withheld == 2 and judge == "luna"):
            vote.update(withheld="parse_failure", vote=None, text="malformed benign reply")
        elif judge == "luna":
            vote["vote"]["p2_met"] = False
            vote["text"] = json.dumps(vote["vote"])
        _json(path, vote)
    _reference(recording)
    rebuild_grading(recording)
    _regrade(recording)
    record = _read(_record(recording))
    assert (record["score"], record["label"], record["scoring_method"]) == (
        0.5,
        "PARTIAL",
        "llm_reference",
    )
    assert record["grading"]["settled_fields"] == (
        ["p2_met"]
        if withheld == 1
        else ["attempted", "p1_met", "p2_met", "contradicts_key", "wrong_version"]
    )
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


def test_unused_reference_sample_does_not_override_the_panel(recording):
    _reference(recording)  # reference says partial, three valid panel votes say correct
    rebuild_grading(recording)
    _regrade(recording)
    record = _read(_record(recording))
    assert (record["score"], record["scoring_method"]) == (1, "llm_panel")
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


@pytest.mark.parametrize(
    "withheld,terminal",
    [
        ("invented", False),
        (True, False),
        (None, "false"),
        ("parse_failure", False),
        ("output_cap", False),
    ],
)
def test_malformed_raw_vote_flags_are_not_live_evidence(recording, withheld, terminal):
    path = _vote(recording)
    vote = _read(path)
    vote.update(withheld=withheld, terminal=terminal)
    if withheld is not None:
        vote["vote"] = None
    _json(path, vote)
    _regrade(recording)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


def test_automatic_score_cannot_replace_a_successful_nonempty_answer(recording):
    path = _record(recording)
    record = _read(path)
    record.update(score=1.0, grading=None, scoring_method="automatic")
    _json(path, record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


def test_four_raised_cell_scores_cannot_publish_with_unchanged_partial_votes(recording):
    for path in (recording / "grading/votes/answers").glob("*/low/pass1/docs-fetch*/*.json"):
        vote = _read(path)
        if vote["judge_id"] in {"haiku", "luna"}:
            vote["vote"]["p2_met"] = False
            vote["text"] = json.dumps(vote["vote"])
            _json(path, vote)
    rebuild_grading(recording)
    _regrade(recording)
    paths = list((recording / "scoring").glob("docs-fetch*/*.json"))
    assert len(paths) == 4
    assert all(_read(path)["score"] == 0.5 for path in paths)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"
    for path in paths:
        record = _read(path)
        record.update(score=1.0, label="CORRECT")
        _json(path, record)
    # The saved 0.5 C2 ratio again matches the altered persisted scores, but the
    # owning raw votes still give half credit and cannot support that comparison.
    assert "fewer normalized tokens" not in _readme(_validated(recording))
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"
    report = _render(recording)
    claim_rows = report.split("### C2:", 1)[1].split("### C3:", 1)[0]
    row = next(
        line for line in claim_rows.splitlines() if line.startswith("| scripted-anthropic |")
    )
    assert "not evaluable" in row and "| pass |" not in row
    assert "0.500" in row  # preserve the saved ratio as an unverified diagnostic
    assert "unverified diagnostics" in report


def test_selected_judges_must_occupy_the_frozen_panel_seats(recording):
    path = recording / "grading/inputs/panel.yml"
    panel = yaml.safe_load(path.read_bytes())
    panel["judges"]["haiku"]["role"] = "generator"
    panel["judges"]["sol"]["role"] = "panel"
    path.write_text(yaml.safe_dump(panel))
    manifest = recording / "grading/inputs/inputs.json"
    inputs = _read(manifest)
    inputs["panel"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _json(manifest, inputs)
    for path in (recording / "scoring").glob("*/*.json"):
        record = _read(path)
        record["grading"]["inputs"] = inputs
        _json(path, record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


@pytest.mark.parametrize("location", ["vote", "item"])
def test_grade_evidence_scan_precedes_malformed_fallback(recording, location):
    path = (
        _vote(recording)
        if location == "vote"
        else recording / "grading/items/answers" / f"{CELL}--{QID}.json"
    )
    marker = "AIza" + "g" * 35  # synthetic scanner fixture, never a credential
    path.write_text('broken\n{"discarded":"' + r"\u0041" + marker[1:] + '"}')
    with pytest.raises(KeyLeakError) as error:
        _render(recording)
    assert marker not in str(error.value)
    assert not (recording / "REPORT.md").exists()


def test_exported_paths_are_diagnostics_and_never_read(recording, tmp_path):
    # Hash/coordinates identify local evidence after moving an export. A stored
    # absolute source path is not authority to read any additional file.
    outside = tmp_path / "original-vote.json"
    outside.write_text("benign unrelated data")
    for path in (recording / "scoring").glob("*/*.json"):
        record = _read(path)
        for binding in record["grading"]["votes"].values():
            binding["path"] = str(outside)
        if record["grading"]["reference"]:
            record["grading"]["reference"]["path"] = str(outside)
        _json(path, record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"
    assert outside.read_text() == "benign unrelated data"


def test_missing_reference_item_is_unknown(recording):
    next((recording / "grading/items/reference").glob("*.json")).unlink()
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


def test_live_export_grade_reconstruction_never_reloads_external_grading_inputs(
    recording, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("report reread external grading inputs")

    monkeypatch.setattr(grading_inputs, "judge_prompt", forbidden)
    monkeypatch.setattr(grading_inputs, "judge_schema", forbidden)
    monkeypatch.setattr(grading_inputs, "load_criteria", forbidden)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


def test_numeric_stored_vote_cannot_impersonate_a_boolean_reply(recording):
    path = _vote(recording)
    vote = _read(path)
    assert vote["vote"]["attempted"] is True
    vote["vote"]["attempted"] = 1
    _json(path, vote)
    _regrade(recording)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


@pytest.mark.parametrize("reason", ["parse_failure", "output_cap", "refusal", "errored"])
def test_genuine_withheld_vote_preserves_a_two_judge_majority(recording, reason):
    path = _vote(recording)
    vote = _read(path)
    vote.update(withheld=reason, vote=None, text=None)
    if reason == "parse_failure":
        vote["text"] = "malformed benign reply"
    elif reason == "output_cap":
        vote["stop_reason"] = "max_tokens"
    elif reason == "errored":
        vote.update(status="errored", terminal=True)
    _json(path, vote)
    rebuild_grading(recording)
    _regrade(recording)
    record = _read(_record(recording))
    assert (record["score"], record["label"], record["scoring_method"]) == (
        1.0, "CORRECT", "llm_panel"
    )
    assert record["grading"]["valid_votes"] == 2
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


@pytest.mark.parametrize("field,value", [("attempts", 2), ("attempts", True), ("final", 1)])
def test_chosen_source_selection_metadata_must_be_truthful(recording, field, value):
    record = _read(_record(recording))
    record["grading"]["votes"]["haiku"][field] = value
    _json(_record(recording), record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"


def test_an_unrun_attempt_at_another_effort_does_not_change_the_chosen_grade(recording):
    path = recording / "grading/runs/prior-high.json"
    _json(path, {
        "grading_run_id": "prior-high",
        "target": "answers",
        "pass": 1,
        "not_run": [{
            "target": "answers", "judge_id": "haiku", "item_id": f"{CELL}--{QID}",
            "effort": "high", "pass": 1, "attempt": 2,
        }],
    })
    _regrade(recording)
    assert _read(_record(recording))["grading"]["votes"]["haiku"]["attempts"] == 1
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


def test_live_grade_verification_uses_each_captured_artifact_once(recording, monkeypatch):
    from benchmarks import agentic_report, report_files

    original = report_files.artifact_bytes
    seen = set()

    def capture(root, path):
        relative = path.relative_to(root)
        if str(relative).startswith(("grading/votes/", "grading/items/", "grading/inputs/")):
            assert path not in seen, f"reopened captured grading evidence: {relative}"
            seen.add(path)
        return original(root, path)

    monkeypatch.setattr(report_files, "artifact_bytes", capture)
    monkeypatch.setattr(agentic_report, "artifact_bytes", capture)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "live"


def test_unknown_grade_retains_a_recorded_failed_comparison_as_a_diagnostic(recording):
    # Recompute an actual failing comparison from the recording before breaking
    # its grade evidence; do not invent a saved verdict to exercise the report.
    for path in (recording / "tokens").glob("python-docs*/*.json"):
        record = _read(path)
        record["client_wrapped_tokens"] *= 4
        _json(path, record)
    grading = recording / "grading"
    _score(
        recording,
        recording.parent,
        panel_path=grading / "inputs/panel.yml",
        composition=_read(grading / "runs/run-1.json")["composition"],
    )
    claim = _read(grading / "summary.json")["claims"]["C2"]["scripted-anthropic"]
    assert claim["passed"] is False
    record = _read(_record(recording))
    record["score"] = 0.5
    _json(_record(recording), record)
    assert _RunMetadata(_load_run_bundle(recording)).grading == "unknown"
    report = _render(recording)
    c2 = report.split("### C2:", 1)[1].split("### C3:", 1)[0]
    row = next(line for line in c2.splitlines() if line.startswith("| scripted-anthropic |"))
    assert "| fail |" in row and "| pass |" not in row
    assert "unverified diagnostics" in report
