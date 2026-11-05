"""Issued calibration history is distinct from ordinary unallocated work."""

import copy
import hashlib
import json

import pytest

from benchmarks.grading.aggregate import write_vote_record
from benchmarks.grading.batch import scripted_result
from benchmarks.grading.inputs import judge_schema
from benchmarks.grading.score import write_g1, write_tuning
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_calibration_history import history as history
from tests.benchmarks.test_calibration_history import replay
from tests.benchmarks.test_grading_probes import index_db as index_db
from tests.benchmarks.test_grading_refusal_integrity import _receipt


def issued(root, *, run="issued-repeat", target="probes", pass_number=2,
           item="KEY--SYN-EX-001", judge="haiku", effort="low", text=None):
    spec = runs.PANEL.judges[judge]
    result = scripted_result(
        item, {"match": ".*", "text": text or runs._vote_text()},
        provider=spec.provider, model_id=spec.model_id,
    )
    path = write_vote_record(
        root, target=target, judge=spec, effort=effort, pass_number=pass_number,
        grading_run_id=run, attempt=1, result=result, terminal=False,
        unit_id="owned-unit", provider_batch_id="owned-scripted-batch",
        input_sha256=runs._probe_digest(root, target, item), schema=judge_schema(),
    )
    _receipt(root, path, result, panel=runs.PANEL)
    invocation = root / "runs" / f"{run}.json"
    row = json.loads(invocation.read_bytes())
    row.update(target=target, records_written=len(row["execution_results"]))
    row["pass"] = pass_number
    if target == "probes":
        row["composition"] = runs.COMPOSITION
    runs._json(invocation, row)
    return path


def entry(root):
    (result,) = json.loads(write_g1(root).read_bytes())["entries"].values()
    return result


@pytest.mark.parametrize("missing", ["vote", "receipt"])
def test_issued_repeat_loss_is_unknown_without_losing_pass1(history, missing):
    held, _ = history
    path = issued(held)
    intact = entry(held)
    assert replay(held, intact).details["held_out_test_retest"]["haiku"]["items"] == 1
    if missing == "vote":
        # Remove every vote at this coordinate, keeping the actual issuance proof.
        for candidate in path.parent.glob("*.json"):
            candidate.unlink()
    else:
        (held / "executions/issued-repeat/haiku/KEY--SYN-EX-001--a1.json").unlink()
    damaged = entry(held)
    result = replay(held, damaged)
    assert damaged["scores"] == intact["scores"]
    assert result.passed is intact["gate"]["passed"]
    assert result.details["held_out_test_retest"]["haiku"]["items"] == "unknown"
    assert result.details["held_out_test_retest"]["haiku"]["pass2_records"] == "unknown"
    assert result.details["calibration_unknown"]["held_out_test_retest"]
    for judge in ("luna", "flash-lite"):
        assert result.details["held_out_test_retest"][judge]["pass2_records"] == 0
    # Portable replay of an older intact export with the same loss is also honest.
    files = intact["source"]["files"]
    for name in list(files):
        if (missing == "vote" and name.startswith("votes/probes/haiku/low/pass2/")) or (
            missing == "receipt" and name.startswith("executions/issued-repeat/")
        ):
            del files[name]
    assert replay(held, intact).details["held_out_test_retest"]["haiku"]["items"] == "unknown"


def test_never_issued_repeat_and_unallocated_efforts_still_have_zero(history):
    held, _ = history
    for candidate in (held / "votes/probes/haiku/low/pass2").glob("*/*.json"):
        candidate.unlink()
    result = replay(held, entry(held))
    assert "held_out_test_retest" not in result.details["calibration_unknown"]
    for repeat in result.details["held_out_test_retest"].values():
        assert repeat["items"] == repeat["pass2_records"] == 0
        assert repeat["alpha"] is None


def test_positive_legacy_issuance_count_cannot_prove_zero_repeats(history):
    held, _ = history
    for path in (held / "votes/probes/haiku/low/pass2").glob("*/*.json"):
        path.unlink()
    runs._json(held / "runs/legacy-repeat.json", {
        "grading_run_id": "legacy-repeat", "target": "probes", "pass": 2,
        "composition": runs.COMPOSITION, "records_written": 1,
    })
    result = replay(held, entry(held))
    assert result.details["held_out_test_retest"]["haiku"]["items"] == "unknown"
    assert result.details["calibration_unknown"]["held_out_test_retest"]


@pytest.mark.parametrize("item,effort", [("KEY--TUNE-001", "low"), ("KEY--SYN-EX-001", "medium")])
def test_unrelated_issued_coordinates_do_not_claim_missing_held_out_repeats(history, item, effort):
    held, _ = history
    path = issued(held, item=item, effort=effort)
    # These coordinates were never allocated to the frozen held-out comparison.
    path.unlink()
    result = replay(held, entry(held))
    assert "held_out_test_retest" not in result.details["calibration_unknown"]
    assert result.details["held_out_test_retest"]["haiku"]["items"] == 1


def test_two_tuning_runs_keep_failed_efforts_and_fallback_history(history, tmp_path):
    held, _ = history
    failed = runs._workspace(tmp_path / "failed", "tuning", {"probes.yml": runs.PROBES})
    probes = [p for p in runs._probe_fixture_requests(failed).values() if p[0].set == "tuning"]
    for probe, _request in probes:
        for judge in ("haiku", "flash"):
            for effort in ("low", "medium"):
                issued(failed, run=f"trial-{judge}-{effort}", target="tuning", pass_number=1,
                       item=probe.id, judge=judge, effort=effort,
                       text=runs._vote_text(attempted=False))
    first = json.loads(write_tuning(failed).read_bytes())
    assert first["judges"]["haiku"]["chosen"] is None
    assert first["judges"]["haiku"]["tried"] == ["low", "medium"]
    successful = (held / "handoff/tuning-1.json").read_bytes()
    runs._json(held / "handoff/tuning-1.json", first)
    (held / "handoff/tuning-2.json").write_bytes(successful)
    metadata = json.loads((held / "workspace.json").read_bytes())
    metadata["handoff"].update({
        f"tuning-{i}.json": hashlib.sha256(
            (held / f"handoff/tuning-{i}.json").read_bytes()
        ).hexdigest() for i in (1, 2)
    })
    runs._json(held / "workspace.json", metadata)
    original = entry(held)
    result = replay(held, original)
    trials = result.details["tuning"]
    assert trials[0]["judges"]["haiku"] == first["judges"]["haiku"]
    assert trials[0]["judges"]["flash"] == first["judges"]["flash"]
    assert trials[1]["judges"]["haiku"]["chosen"] == "low"
    assert not result.details["calibration_unknown"]
    from benchmarks.grading.score import score_run_dir
    from benchmarks.report import generate_report
    from tests.benchmarks.test_agentic_report import MATRIX, METHODOLOGY, _build

    run = _build(tmp_path / "answers", families_run=("scripted-anthropic",), suspect_answer=False)
    score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=[held / "g1.json"])
    report = generate_report(run, model_matrix_path=MATRIX, methodology_path=METHODOLOGY)
    text = report.read_text()
    assert "Tuning, haiku: efforts tried low, medium; chosen none" in text
    assert "Tuning, flash: efforts tried low, medium; chosen none" in text
    assert "Tuning, haiku: efforts tried low; chosen low" in text


def test_missing_issued_tuning_vote_cannot_erase_an_attempt(tmp_path):
    root = runs._workspace(tmp_path / "tune", "tuning", {"probes.yml": runs.PROBES})
    probe = next(p for p, _r in runs._probe_fixture_requests(root).values() if p.set == "tuning")
    path = issued(root, run="trial", target="tuning", pass_number=1, item=probe.id)
    path.unlink()
    with pytest.raises(BenchmarkValidationError, match="issued|inventory"):
        write_tuning(root)


@pytest.mark.parametrize("field,value", [
    ("model_id", "foreign-model"), ("effort", "medium"),
    ("input_sha256", "d" * 64), ("pass", 1),
])
def test_repeat_inventory_checks_receipt_identity(history, field, value):
    held, _ = history
    issued(held)
    record = entry(held)
    files = record["source"]["files"]
    name = "executions/issued-repeat/haiku/KEY--SYN-EX-001--a1.json"
    receipt = json.loads(files[name])
    receipt[field] = value
    files[name] = json.dumps(receipt)
    run = json.loads(files["runs/issued-repeat.json"])
    run["execution_results"][name.removeprefix("executions/issued-repeat/")] = hashlib.sha256(
        files[name].encode()
    ).hexdigest()
    files["runs/issued-repeat.json"] = json.dumps(run)
    with pytest.raises(BenchmarkValidationError):
        replay(held, copy.deepcopy(record))


def test_repeat_invocation_cannot_relabel_its_registered_composition(history):
    held, _ = history
    issued(held)
    record = entry(held)
    files = record["source"]["files"]
    invocation = json.loads(files["runs/issued-repeat.json"])
    invocation["composition"]["haiku"]["effort"] = "medium"
    files["runs/issued-repeat.json"] = json.dumps(invocation)
    with pytest.raises(BenchmarkValidationError, match="composition"):
        replay(held, record)


def test_registered_g1_and_reference_survive_only_optional_repeat_loss(tmp_path, index_db):
    _run, held, original, _criteria = runs._full_calibration(tmp_path, index_db, False)
    path = issued(held)
    invocation = held / "runs/issued-repeat.json"
    row = json.loads(invocation.read_bytes())
    row["composition"] = original["binding"]["composition"]
    runs._json(invocation, row)
    intact = entry(held)
    assert intact["reference_check"]["passed"] is True
    path.unlink()
    damaged = entry(held)
    from benchmarks.agentic.inputs import CapturedInput
    from benchmarks.grading.calibration import replay_g1
    from benchmarks.grading.gates import G1Binding

    gate, reference = replay_g1(
        {"haiku+luna+flash": damaged},
        G1Binding(
            {j: damaged["binding"]["composition"][j] for j in ("haiku", "luna", "flash")},
            damaged["binding"]["inputs"],
        ),
        **{name: CapturedInput.from_bytes((held / "inputs" / filename).read_bytes(), name, name)
           for name, filename in (("panel", "panel.yml"), ("probes", "probes.yml"),
                                  ("criteria", "grading-criteria.yml"))},
    )
    assert gate.passed is reference.passed is True
    assert gate.details["overall"]["hits"] == 700
    assert reference.details["overall"]["hits"] == 350
    assert gate.details["held_out_test_retest"]["haiku"]["items"] == "unknown"
    assert gate.details["held_out_test_retest"]["luna"]["items"] == 0
    assert damaged["scores"] == intact["scores"]
    assert damaged["reference_check"] == intact["reference_check"]
