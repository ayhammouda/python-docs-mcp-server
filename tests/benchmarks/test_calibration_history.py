"""Calibration disclosure needs selected attempts, not two agreeing summaries."""

import copy
import json

import pytest

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.grading.calibration import replay_g1
from benchmarks.grading.gates import G1Binding
from benchmarks.grading.score import write_g1, write_tuning
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs


def replay(held, entry):
    def captured(name, filename):
        return CapturedInput.from_bytes((held / "inputs" / filename).read_bytes(), name, name)

    return replay_g1(
        {"haiku+luna+flash-lite": entry},
        G1Binding(
            {j: entry["binding"]["composition"][j] for j in runs.JUDGES}, entry["binding"]["inputs"]
        ),
        panel=captured("panel", "panel.yml"),
        probes=captured("probes", "probes.yml"),
        criteria=captured("criteria", "grading-criteria.yml"),
    )[0]


@pytest.fixture
def history(tmp_path):
    tune = runs._workspace(tmp_path / "tune", "tuning", {"probes.yml": runs.PROBES})
    suite = runs.load_probe_file(
        runs.PROBES, criteria_path=runs.CRITERIA, tuning_path=runs.TUNING_PATH
    )
    for p in suite.probes:
        if p.set == "tuning":
            weights = runs.load_tuning_questions(runs.TUNING_PATH)[p.question_id].weights
            text = runs._vote_text(**runs._fields_for(p.expected_score, p.expected_label, weights))
            for j in runs.JUDGES:
                runs._vote(tune, j, p.id, text, target="tuning")
    tuning = write_tuning(tune)
    held = runs._workspace(
        tmp_path / "held",
        "held-out",
        {
            "probes.yml": runs.PROBES,
            "tuning-1.json": tuning,
        },
    )
    runs._json(
        held / "runs/run-1.json",
        {
            "target": "probes",
            "pass": 1,
            "composition": runs.COMPOSITION,
        },
    )
    for p in suite.probes:
        if p.set == "held_out":
            for j in runs.JUDGES:
                runs._vote(held, j, p.id, runs._vote_text(), target="probes")
    item = "KEY--SYN-EX-001"
    runs._vote(held, "haiku", item, runs._vote_text(), target="probes", pass_number=2)
    (entry,) = json.loads(write_g1(held).read_bytes())["entries"].values()
    return held, entry


def test_new_exports_replay_selected_attempts_and_disclose_tuning(history):
    held, entry = history
    gate = replay(held, entry)
    assert gate.details["tuning"][0]["judges"]["haiku"]["tried"] == ["low"]
    assert gate.details["tuning"][0]["judges"]["haiku"]["chosen"] == "low"
    repeat = gate.details["held_out_test_retest"]["haiku"]
    assert repeat["items"] == 1 and repeat["pass2_records"] == 1


def test_legacy_summaries_cannot_attest_repeat_or_tuning(history):
    held, entry = history
    entry.pop("source", None)
    for record in entry["tuning"]:
        record.pop("source", None)
    gate = replay(held, entry)
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == "unknown"
    assert gate.details["calibration_unknown"]["tuning"]
    assert "tuning" not in gate.details
    assert gate.details["overall"]["n"] == 28


@pytest.mark.parametrize("field,value", [("chosen", "high"), ("tried", ["high"])])
def test_self_consistent_tuning_overrides_are_refused(history, field, value):
    held, entry = history
    entry = copy.deepcopy(entry)
    entry["tuning"][0]["judges"]["haiku"][field] = value
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        replay(held, entry)


def test_self_consistent_repeat_override_is_refused(history):
    held, entry = history
    row = entry["gate"]["details"]["held_out_test_retest"]["haiku"]["observations"]
    row["KEY--SYN-EX-001"]["pass2"] = 0.25
    with pytest.raises(BenchmarkValidationError, match="repeat"):
        replay(held, entry)


@pytest.mark.parametrize(
    "field,value",
    [
        ("effort", "high"),
        ("judge_id", "luna"),
        ("input_sha256", "d" * 64),
        ("text", runs._vote_text(attempted=False)),
    ],
)
def test_repeat_proof_replays_actual_selected_reply(history, field, value):
    held, entry = history
    files = entry["source"]["files"]
    name = next(n for n in files if "votes/probes/haiku/low/pass2/" in n)
    row = json.loads(files[name])
    row[field] = value
    files[name] = json.dumps(row)
    with pytest.raises(BenchmarkValidationError):
        replay(held, entry)


def test_missing_original_request_does_not_leave_numeric_repeat(history):
    held, entry = history
    del entry["source"]["files"]["items/probes/KEY--SYN-EX-001.json"]
    with pytest.raises(BenchmarkValidationError, match="missing calibration input"):
        replay(held, entry)


def test_later_unfinished_take_keeps_original_selection_and_exports_candidates(history):
    held, entry = history
    files = entry["source"]["files"]
    name = next(n for n in files if "votes/probes/haiku/low/pass2/" in n)
    pending = json.loads(files[name])
    pending.update(
        grading_run_id="later",
        attempt=1,
        graded_at="2099-01-01T00:00:00Z",
        status="missing",
        terminal=False,
        vote=None,
        text=None,
        withheld="missing",
    )
    files[name.rsplit("/", 1)[0] + "/later--a1.json"] = json.dumps(pending)
    repeat = replay(held, entry).details["held_out_test_retest"]["haiku"]
    assert repeat["items"] == 1 and repeat["pass2_records"] == 1
    assert repeat["observations"]["KEY--SYN-EX-001"]["pass2_record"] == {
        "grading_run_id": "run-1",
        "attempt": 1,
    }
    pending.update(
        status="succeeded",
        withheld=None,
        text=runs._vote_text(attempted=False),
        vote=json.loads(runs._vote_text(attempted=False)),
    )
    files[name.rsplit("/", 1)[0] + "/later--a1.json"] = json.dumps(pending)
    with pytest.raises(BenchmarkValidationError, match="repeat"):
        replay(held, entry)


def test_absent_repeat_has_proven_zero_coverage(history):
    held, _ = history
    for path in (held / "votes/probes/haiku/low/pass2").glob("*/*.json"):
        path.unlink()
    (entry,) = json.loads(write_g1(held).read_bytes())["entries"].values()
    result = replay(held, entry).details["held_out_test_retest"]["haiku"]
    assert result["items"] == 0 and result["pass2_records"] == 0
    assert result["expected"] == 28 and result["alpha"] is None


def test_capture_does_not_reopen_mutable_vote_or_invocation(history, monkeypatch):
    from benchmarks.grading import calibration_history

    held, _ = history
    reads = {}
    real = calibration_history.artifact_bytes

    def once(root, path):
        reads[path] = reads.get(path, 0) + 1
        assert reads[path] == 1, f"reopened mutable calibration bytes: {path}"
        return real(root, path)

    monkeypatch.setattr(calibration_history, "artifact_bytes", once)
    (entry,) = json.loads(write_g1(held).read_bytes())["entries"].values()
    # Portable replay cannot reopen workspace files, even when they remain nearby.
    monkeypatch.setattr(calibration_history, "artifact_bytes", lambda *_: pytest.fail("local read"))
    assert replay(held, entry).details["held_out_test_retest"]["haiku"]["items"] == 1


def test_capture_parses_each_invocation_once_instead_of_once_per_result(history, monkeypatch):
    from benchmarks.grading import aggregate, calibration_history

    held, _ = history
    parsed = []

    def counting(original):
        def read(body, label):
            if label in ("calibration invocation", "grading invocation"):
                parsed.append(label)
            return original(body, label)
        return read

    monkeypatch.setattr(aggregate, "json_mapping", counting(aggregate.json_mapping))
    monkeypatch.setattr(
        calibration_history, "json_mapping", counting(calibration_history.json_mapping)
    )
    write_g1(held)
    assert parsed == ["calibration invocation"]


def test_normal_producer_scorer_and_renderer_retain_genuine_repeat_coverage(
    history, tmp_path, monkeypatch
):
    from benchmarks.grading.score import score_run_dir
    from benchmarks.report import generate_report
    from tests.benchmarks.test_agentic_report import MATRIX, METHODOLOGY, _build

    held, _ = history
    for path in (held / "votes/probes/haiku/low/pass2").glob("*/*.json"):
        path.unlink()
    suite = runs.load_probe_file(
        runs.PROBES, criteria_path=runs.CRITERIA, tuning_path=runs.TUNING_PATH
    )
    held_probes = [p for p in suite.probes if p.set == "held_out"]
    for i, p in enumerate(held_probes):
        reply = runs._vote_text(attempted=p.expected_score != 0)
        runs._vote(held, "haiku", p.id, reply, target="probes", attempt=2)
        if i < 26:
            runs._vote(
                held,
                "haiku",
                p.id,
                reply if i < 24 else "not a vote",
                target="probes",
                pass_number=2,
            )
    evidence = write_g1(held)
    run = _build(tmp_path / "answering", families_run=("scripted-anthropic",), suspect_answer=False)
    read = CapturedInput.read

    def frozen_only(path, name, label):
        assert name != "tuning", "a portable run must use original tuning inputs"
        return read(path, name, label)

    monkeypatch.setattr(CapturedInput, "read", frozen_only)
    summary = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[evidence])
    assert summary["gates"]["G1"]["details"]["held_out_test_retest"]["haiku"]["items"] == 24
    assert (
        json.loads((run / "grading/disclosure/g1-entries.json").read_bytes())
        == (json.loads(evidence.read_bytes())["entries"])
    )
    report = generate_report(run, model_matrix_path=MATRIX, methodology_path=METHODOLOGY)
    text = report.read_text()
    assert "Held-out probe test–retest, haiku: alpha 1.000, 24/28 paired, 26 pass-2 records" in text
    assert "Tuning, haiku: efforts tried low; chosen low" in text


def test_portable_lookups_do_not_rescan_other_items():
    from benchmarks.grading.calibration_history import CalibrationSource

    class NoRescan(dict):
        def __iter__(self):
            raise AssertionError("an item lookup scanned the entire calibration archive")

    source, _ = CalibrationSource.restore({
        "version": 1, "inputs": {}, "files": dict.fromkeys((
            "votes/first/b.json", "votes/first/a.json", "votes/second/c.json",
            "votes/first/nested/d.json", "votes/first/readme.txt",
        ), "{}"),
    })
    source.files = NoRescan(source.files)
    first = source.root / "votes/first"
    assert source.json_paths(first) == [first / "a.json", first / "b.json"]
    assert source.json_paths(source.root / "votes/absent") == []


def test_source_metadata_cannot_egress_a_decoded_key(history):
    from benchmarks.agentic.protocol import KeyLeakError

    held, entry = history
    token = "AIza" + "q" * 35
    files = entry["source"]["files"]
    name = next(n for n in files if "votes/probes/haiku/low/pass2/" in n)
    row = json.loads(files[name])
    row["reported_model"] = "metadata\n" + token
    files[name] = json.dumps(row)
    with pytest.raises(KeyLeakError) as error:
        replay(held, entry)
    assert token not in str(error.value)


def test_oversize_capture_leaves_existing_export_untouched(history, monkeypatch):
    from benchmarks.grading import score

    held, _ = history
    before = (held / "g1.json").read_bytes()
    monkeypatch.setattr(score, "MAX_CALIBRATION_BYTES", 100)
    with pytest.raises(BenchmarkValidationError, match="size limit"):
        write_g1(held)
    assert (held / "g1.json").read_bytes() == before


@pytest.mark.parametrize("name", ["runs/../foreign.json", "/foreign.json"])
def test_portable_source_never_follows_recorded_external_paths(history, name):
    held, entry = history
    entry["source"]["files"][name] = "{}"
    with pytest.raises(BenchmarkValidationError, match="invalid.*(layout|file)"):
        replay(held, entry)


def test_missing_tuning_proof_retains_independent_held_out_coverage(history):
    held, entry = history
    entry["tuning"][0].pop("source")
    gate = replay(held, entry)
    assert gate.details["calibration_unknown"]["tuning"]
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == 1
    assert gate.details["overall"]["n"] == 28


def test_unselected_judge_effort_does_not_override_selected_frozen_settings(history):
    import yaml

    held, entry = history
    panel = yaml.safe_load(entry["source"]["inputs"]["panel"])
    panel["judges"]["flash"]["effort"] = "medium"
    entry["source"]["inputs"]["panel"] = yaml.safe_dump(panel)
    gate = replay(held, entry)
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == 1
    assert gate.details["tuning"][0]["judges"]["flash-lite"]["chosen"] == "low"


def test_writer_refuses_nested_marker_in_unselected_take(history):
    from benchmarks.agentic.protocol import KeyLeakError

    held, entry = history
    before = (held / "g1.json").read_bytes()
    name, body = next((n, b) for n, b in entry["source"]["files"].items()
                      if "votes/probes/haiku/low/pass2/" in n)
    marker = "AIza" + "q" * 35
    reply = json.dumps({"note": marker}).replace(marker, "".join(
        f"\\u{ord(character):04x}" for character in marker
    ))
    candidate = json.loads(body)
    candidate.update(grading_run_id="older", attempt=2, graded_at="2001-01-01T00:00:00Z",
                     status="missing", terminal=False, vote=None, text=reply, withheld="missing")
    (held / name).with_name("older--a2.json").write_text(json.dumps(candidate))

    with pytest.raises(KeyLeakError) as error:
        write_g1(held)
    assert marker not in str(error.value)
    assert (held / "g1.json").read_bytes() == before
