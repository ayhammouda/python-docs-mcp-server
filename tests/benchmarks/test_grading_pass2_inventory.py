"""Optional issued repeats cannot invalidate independently supported pass-1 grades."""

import json
from collections import Counter

import pytest

from benchmarks.__main__ import main
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.score import score_run_dir
from benchmarks.report_files import artifact_bytes
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.accepted_report_fixture import read, rebuild_grading, write
from tests.benchmarks.test_agentic_report import _readme, _render
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_vote_inventory import _additional_execution, _issued_vote


@pytest.mark.parametrize("damage", ["secret", "symlink", "json", "grading", "digest"])
@pytest.mark.parametrize("missing_repeat", [False, True])
def test_optional_repeat_does_not_bypass_trust_boundaries(tmp_path, damage, missing_repeat):
    from benchmarks.runner import BenchmarkValidationError

    run = runs._build_run(tmp_path)
    rebuild_grading(run)
    original = _issued_vote(run)
    repeat = _additional_execution(run, original, run_id="repeat", pass_number=2)
    later = _additional_execution(run, original, run_id="z-later-repeat", pass_number=2)
    score_run_dir(run, schema_path=runs.SCHEMA)
    if missing_repeat:
        repeat.unlink()
    row = read(later)
    if damage == "secret":
        # Benign synthetic marker made here; no credentials are read.
        row["reported_model"] = "AIza" + "g" * 35
        write(later, row)
    elif damage == "symlink":
        later.unlink()
        later.symlink_to(original)
    elif damage == "json":
        later.write_bytes(b"{malformed")
    elif damage == "grading":
        # Rebind the vote digest; the receipt/raw result still refutes the edit.
        import hashlib

        row.update(refusal=True, withheld="refusal", vote=None)
        body = write(later, row)
        relative = f"{row['judge_id']}/{row['item_id']}--a1.json"
        receipt_path = run / "grading/executions/z-later-repeat" / relative
        receipt = read(receipt_path)
        receipt["vote_sha256"] = hashlib.sha256(body).hexdigest()
        receipt_body = write(receipt_path, receipt)
        invocation_path = run / "grading/runs/z-later-repeat.json"
        invocation = read(invocation_path)
        invocation["execution_results"][relative] = hashlib.sha256(receipt_body).hexdigest()
        write(invocation_path, invocation)
    else:
        row["unit_id"] = "wrong-unit"
        write(later, row)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(KeyLeakError if damage == "secret" else BenchmarkValidationError):
        score_run_dir(run, schema_path=runs.SCHEMA)
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_missing_issued_answer_repeat_keeps_primary_cli_outputs(tmp_path, capsys, monkeypatch):
    run = runs._build_run(tmp_path)
    rebuild_grading(run)
    repeat = _additional_execution(run, _issued_vote(run), run_id="repeat", pass_number=2)
    intact = score_run_dir(run, schema_path=runs.SCHEMA)
    assert intact["diagnostics"]["test_retest"] is not None
    assert "test_retest_unavailable" not in intact["diagnostics"]
    repeat.unlink()
    seen = Counter()

    def read_once(root, path):
        if any(part in path.parts for part in ("runs", "votes", "executions", "items")):
            seen[path] += 1
            assert seen[path] == 1, path
        return artifact_bytes(root, path)

    from benchmarks.grading import vote_capture

    monkeypatch.setattr(
        "benchmarks.grading.score.VoteCapture",
        lambda root: vote_capture.VoteCapture(root, reader=read_once),
    )
    monkeypatch.setattr("benchmarks.grading.aggregate.artifact_bytes", read_once)
    monkeypatch.setattr("benchmarks.grading.gates.artifact_bytes", read_once)
    assert main(["agentic-score", "--run-dir", str(run)]) == 0
    current = json.loads(capsys.readouterr().out)
    assert current == json.loads((run / "grading/summary.json").read_bytes())
    for name in ("gates", "graded_cells", "pending_cells", "claims", "claims_by_judge", "spend"):
        assert current[name] == json.loads(json.dumps(intact[name]))
    assert current["graded_cells"] == 6
    assert current["diagnostics"]["test_retest"] is None
    assert current["diagnostics"]["test_retest_unavailable"] == (
        "missing execution-issued vote in inventory"
    )
    assert max(seen.values()) == 1
    assert not repeat.exists()


def test_unissued_repeats_do_not_claim_lost_evidence(tmp_path):
    run = runs._build_run(tmp_path)
    rebuild_grading(run)
    summary = score_run_dir(run, schema_path=runs.SCHEMA)
    assert summary["graded_cells"] == 6
    assert summary["diagnostics"]["test_retest"] is None
    assert "test_retest_unavailable" not in summary["diagnostics"]


@pytest.mark.parametrize(
    "judge,google_total,google_rate",
    [("luna", "3.00", "0.010000"), ("flash-lite", "3.01", "0.010033")],
)
def test_rescored_missing_repeat_keeps_report_and_readme_comparisons(
    validated_bundle, judge, google_total, google_rate
):
    from benchmarks.agentic_report import _RunMetadata
    from benchmarks.report import _load_run_bundle

    run = validated_bundle
    original = next((run / "grading/votes/answers" / judge).glob("*/pass1/*/*.json"))
    repeat = _additional_execution(run, original, run_id="repeat", pass_number=2)
    archives = sorted((run / "grading/disclosure/g1").glob("*.json"))
    intact = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=archives)
    assert intact["tier"] == "Validated"
    repeated_judge = read(repeat)["judge_id"]
    expected = {judge: int(judge == repeated_judge) for judge in intact["efforts"]}
    reported = _RunMetadata(_load_run_bundle(run)).summary
    for output in (intact, reported):
        repeats = output["diagnostics"]["test_retest"]
        assert {judge: row["items"] for judge, row in repeats.items()} == expected
    text = _render(run)
    for judge, n in expected.items():
        line = next(s for s in text.splitlines() if f"test–retest alpha, {judge}:" in s)
        assert f"({n} paired)" in line
    before_readme = _readme(run)
    assert "fewer normalized tokens" in before_readme
    repeat.unlink()
    current = score_run_dir(run, schema_path=runs.SCHEMA, g1_paths=archives)
    for name in ("gates", "claims", "claims_by_judge", "spend", "graded_cells"):
        assert current[name] == intact[name]
    metadata = _RunMetadata(_load_run_bundle(run))
    assert metadata.tier == "Validated" and metadata.retest_unknown
    assert metadata.summary["diagnostics"] == json.loads(json.dumps(current["diagnostics"]))
    assert metadata.grading_spend["graded_answers"] == 300
    assert metadata.grading_spend["by_provider"]["google"] == google_total
    assert metadata.grading_spend["per_graded_answer"]["google"] == google_rate
    text = _render(run)
    assert "Real-answer test–retest: unknown (unverified pass-2 evidence)" in text
    assert "Real-answer test–retest: no paired scores reported" not in text
    assert _readme(run) == before_readme
    assert not repeat.exists()


@pytest.mark.parametrize("target", ["answers", "reference"])
def test_missing_primary_issued_vote_still_rejects_before_writes(tmp_path, target, capsys):
    from benchmarks.runner import BenchmarkValidationError

    run = runs._build_run(tmp_path)
    if target == "reference":
        item = next((run / "grading/votes/answers").glob("*/*/pass1/*/*.json")).parent.name
        runs._vote(run / "grading", "reference", item, runs._vote_text(), target="reference")
        path = next((run / "grading/votes/reference").glob("*/*/pass1/*/*.json"))
        row = read(path)
        row["grading_run_id"] = "ref-1"
        path.unlink()
        write(path.with_name("ref-1--a1.json"), row)
    rebuild_grading(run)
    score_run_dir(run, schema_path=runs.SCHEMA)
    _issued_vote(run, target).unlink()
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(BenchmarkValidationError, match="missing execution-issued vote"):
        score_run_dir(run, schema_path=runs.SCHEMA)
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    with pytest.raises(SystemExit) as caught:
        main(["agentic-score", "--run-dir", str(run)])
    assert caught.value.code == 2
    assert "missing execution-issued vote" in capsys.readouterr().err
    assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before


def test_retest_counts_only_observed_pairs_for_each_judge():
    from benchmarks.grading.gates import test_retest

    first = {"a": {"one": 0.0, "two": 1.0}, "b": {"one": None, "two": 0.5}}
    second = {"a": {"one": 0.5, "two": None}, "b": {"one": 1.0}}
    result = test_retest(first, second, {"a": "q", "b": "q"}, seed="7", resamples=10)
    assert {judge: row["items"] for judge, row in result.items()} == {"one": 1, "two": 0}
