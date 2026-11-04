"""Optional historical markers must not turn score arrays into malformed proof."""

import copy
import hashlib

import pytest

from benchmarks.__main__ import main
from tests.benchmarks.accepted_report_fixture import read, write
from tests.benchmarks.test_agentic_report import MATRIX, METHODOLOGY
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_calibration_history import history as history
from tests.benchmarks.test_calibration_history import replay


@pytest.mark.parametrize("marker", [None, {}, False, "historical source not retained"])
def test_historical_tuning_sources_only_withhold_tuning(history, marker):
    held, entry = history
    for record in entry["tuning"]:
        record.pop("source", None)
    absent = replay(held, entry)
    for record in entry["tuning"]:
        record["source"] = marker
    actual = replay(held, entry)
    assert actual == absent
    assert "tuning" in actual.details["calibration_unknown"]
    assert "tuning" not in actual.details
    assert actual.details["held_out_test_retest"]["haiku"]["items"] == 1


def test_producer_accepts_historical_tuning_handoff_without_attesting_trials(history):
    held, original = history
    handoff = held / "handoff/tuning-1.json"
    tuning = read(handoff)
    tuning.pop("source")
    baseline = None
    for present, marker in (
        (False, None), (True, None), (True, {}), (True, False),
        (True, "historical source not retained"),
    ):
        record = {**tuning, **({"source": marker} if present else {})}
        body = write(handoff, record)
        meta = read(held / "workspace.json")
        meta["handoff"][handoff.name] = hashlib.sha256(body).hexdigest()
        write(held / "workspace.json", meta)
        assert main(["agentic-score", "--workspace", str(held), "--step", "g1"]) == 0
        (entry,) = read(held / "g1.json")["entries"].values()
        assert entry["scores"] == original["scores"]
        assert entry["reference_check"] == original["reference_check"]
        gate = replay(held, entry)
        if baseline is None:
            baseline = gate
        assert gate == baseline
        assert gate.details["held_out_test_retest"]["haiku"]["items"] == 1
        assert "tuning" in gate.details["calibration_unknown"]
        assert "tuning" not in gate.details


def test_bad_tuning_handoff_cannot_replace_producer_export(history):
    held, _ = history
    exported = (held / "g1.json").read_bytes()
    handoff = held / "handoff/tuning-1.json"
    tuning = read(handoff)
    for proof in ({"version": 0}, '{"opaque": "serialized history"}'):
        body = write(handoff, {**tuning, "source": proof})
        meta = read(held / "workspace.json")
        meta["handoff"][handoff.name] = hashlib.sha256(body).hexdigest()
        write(held / "workspace.json", meta)
        with pytest.raises(SystemExit) as error:
            main(["agentic-score", "--workspace", str(held), "--step", "g1"])
        assert error.value.code == 2
        assert (held / "g1.json").read_bytes() == exported


@pytest.mark.parametrize("slot", ["source", "tuning"])
@pytest.mark.parametrize("marker", [None, {}, False, "historical source not retained"])
def test_historical_sources_score_and_publish_like_absence(
    validated_bundle, tmp_path, marker, slot,
):
    run = validated_bundle
    summary = read(run / "grading/summary.json")
    original = run / "grading" / summary["disclosure"]["g1_files"][0]["path"]
    archive = read(original)
    key = summary["composition"]
    archive["entries"][key].pop("source", None)
    archive["entries"][key]["tuning"] = [{"judges": {}}]
    supplied = tmp_path / "historical.json"
    write(supplied, archive)
    args = ["agentic-score", "--run-dir", str(run), "--g1", str(supplied)]
    assert main(args) == 0
    absent = read(run / "grading/summary.json")
    if slot == "source":
        archive["entries"][key]["source"] = marker
    else:
        archive["entries"][key]["tuning"][0]["source"] = marker
    body = write(supplied, archive)
    # A historical bundle already exported with both digest-bound original and
    # matching projection must also remain publishable before fresh scoring.
    digest = hashlib.sha256(body).hexdigest()
    relative = f"disclosure/g1/{digest}.json"
    (run / "grading" / relative).write_bytes(body)
    write(run / "grading/disclosure/g1-entries.json", archive["entries"])
    absent["disclosure"]["g1_files"] = [{"path": relative, "sha256": digest}]
    write(run / "grading/summary.json", absent)
    report_args = [
        "report", "--run-dir", str(run), "--model-matrix", str(MATRIX),
        "--methodology", str(METHODOLOGY),
    ]
    assert main(report_args) == 0
    assert "G1 for this run's composition: pass." in (run / "REPORT.md").read_text()
    assert "fewer normalized tokens" in (run / "README-SUMMARY.md").read_text()
    assert main(args) == 0
    actual = read(run / "grading/summary.json")
    for field in ("gates", "reference_check", "claims", "spend", "tier"):
        assert actual[field] == absent[field]
    for gate, n in ((actual["gates"]["G1"], 700), (actual["reference_check"], 350)):
        assert gate["passed"] is True
        assert gate["details"]["overall"]["hits"] == gate["details"]["overall"]["n"] == n
    repeats = actual["gates"]["G1"]["details"]["held_out_test_retest"]
    assert all(row["items"] == "unknown" for row in repeats.values())
    assert (run / "grading/disclosure/g1" / f"{digest}.json").read_bytes() == body
    assert read(run / "grading/disclosure/g1-entries.json") == archive["entries"]
    assert main(report_args) == 0
    assert "Grading outcome: Validated" in (run / "REPORT.md").read_text()
    assert "ratio 0.500" in (run / "README-SUMMARY.md").read_text()
    assert "fewer normalized tokens" in (run / "README-SUMMARY.md").read_text()


@pytest.mark.parametrize("slot", ["source", "tuning"])
@pytest.mark.parametrize("proof", [
    {"version": 0}, {"version": None}, {"version": False},
    {"version": 1}, {"version": 2}, {"inputs": {}}, {"files": {}}, {"blobs": {}},
    {"notes": "no original files"}, '{"opaque": "serialized history"}',
])
def test_explicit_or_opaque_sources_refuse_before_writes(
    validated_bundle, tmp_path, slot, proof,
):
    run = validated_bundle
    summary = read(run / "grading/summary.json")
    archive = read(run / "grading" / summary["disclosure"]["g1_files"][0]["path"])
    entry = archive["entries"][summary["composition"]]
    if slot == "source":
        entry["source"] = copy.deepcopy(proof)
    else:
        entry["tuning"] = [{"source": copy.deepcopy(proof)}]
    supplied = tmp_path / "invalid.json"
    write(supplied, archive)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit) as error:
        main(["agentic-score", "--run-dir", str(run), "--g1", str(supplied)])
    assert error.value.code == 2
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.rglob("*") if p.is_file()}
    assert after == before
