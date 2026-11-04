"""Public diagnostic quantities follow captured observations, not summary caches."""

import copy
import json
import re

from tests.benchmarks.accepted_report_fixture import read, write
from tests.benchmarks.test_agentic_report import _render
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template


def test_public_report_rebuilds_entire_diagnostic_cache(validated_bundle):
    run = validated_bundle
    before = _render(run)
    path = run / "grading/summary.json"
    summary = read(path)
    for gate in ("G2", "G3"):
        for grouping in ("by_family", "by_system"):
            for row in summary["breakdowns"][gate][grouping].values():
                row.update(alpha=-0.75, low=-0.95, high=-0.5, q_below=1)
            summary["breakdowns"][gate][grouping]["invented"] = copy.deepcopy(row)
    summary["breakdowns"]["G3"]["confusion"] = {"1.0": {"0.0": 901}}
    summary["diagnostics"] = {
        "test_retest": {"invented": {"alpha": -0.75}},
        "self_preference": {"invented": {"point": 901, "low": 900, "high": 902}},
        "length_grade_correlation": 901,
        "label_shares": {"invented": {"wrong": 1}},
        "grounded_rate": {"invented": 1},
        "suspects": {"invented": {"suspect": 901, "differs": 901}},
        "per_judge": {"invented": {"records": 901, "withheld": {"refusal": 901}}},
        "no_final_record": {"invented": {"invented": ["imaginary"]}},
    }
    write(path, summary)
    # Also corrupt the duplicate projection: comparing two caches is insufficient.
    write(run / "grading/disclosure/breakdowns.json", summary["breakdowns"])
    after = _render(run)
    assert after == before


def test_held_out_statistics_are_rebuilt_from_observations(validated_bundle):
    run = validated_bundle
    import yaml

    from benchmarks.grading.calibration import probe_retest

    path = run / "grading/disclosure/g1-entries.json"
    summary_path = run / "grading/summary.json"
    summary = read(summary_path)
    entries = read(path)
    entry = entries[summary["composition"]]
    suite = yaml.safe_load((run / "grading/inputs/probes.yml").read_text())
    probes = suite["probes"]
    scores = entry["scores"]["judges"]["haiku"]
    first = next(p for p in probes if p["id"] in scores)
    other = next(p for p in probes if p["id"] in scores and scores[p["id"]] != scores[first["id"]])
    observations = {
        p["id"]: {
            "question_id": p["question_id"],
            "pass1": scores[p["id"]],
            "pass2": scores[p["id"]],
        }
        for p in (first, other)
    }
    result = probe_retest(
        "haiku",
        observations,
        expected=700,
        seed=suite["header"]["corpus_sha256"],
    )
    entry["gate"]["details"]["held_out_test_retest"] = {"haiku": result}
    entry["gate"]["details"]["composition"] = summary["composition"]
    summary["gates"]["G1"] = copy.deepcopy(entry["gate"])
    write(path, entries)
    write(summary_path, summary)
    before = _render(run)
    expected = "Held-out probe test–retest, haiku: alpha 1.000, 2/700 paired, 2 pass-2 records"
    assert expected in before and "Grading outcome: Validated" in before
    entry["gate"]["details"]["held_out_test_retest"]["haiku"].update(
        alpha=-0.75, low=-0.95, high=-0.5, items=901, expected=901, pass2_records=901
    )
    entry["dropped"] = {"invented": 901}
    # Editing both copies of the aggregate cannot certify the changed numbers.
    summary["gates"]["G1"] = copy.deepcopy(entry["gate"])
    write(path, entries)
    write(summary_path, summary)
    after = _render(run)
    assert expected in after
    assert re.search(r"\b901\b", after) is None
    assert "-0.75" not in after and "invented" not in after


def _record_second_pass(run):
    """Record a second take of the same SDK fixture replies with its own receipts."""
    from tests.benchmarks.accepted_report_fixture import digest

    root = run / "grading"
    events = [json.loads(line) for line in (root / "ledger-events.jsonl").read_text().splitlines()]
    added = []
    for path in list((root / "runs").glob("*.json")):
        original = read(path)
        if original["target"] != "answers" or original["pass"] != 1:
            continue
        old_id = original["grading_run_id"]
        new_id = f"retest-{old_id}"
        units = {u: f"retest-{u}" for u in original["units"]}
        invocation = {
            **original,
            "grading_run_id": new_id,
            "pass": 2,
            "units": list(units.values()),
            "execution_results": {},
        }
        for vote_path in (root / "votes/answers").glob("*/*/pass1/*/*.json"):
            vote = read(vote_path)
            if vote["grading_run_id"] != old_id:
                continue
            relative = f"{vote['judge_id']}/{vote['item_id']}--a{vote['attempt']}.json"
            history = read(root / "executions" / old_id / relative)
            vote.update(grading_run_id=new_id, unit_id=units[vote["unit_id"]], **{"pass": 2})
            dest = (
                root
                / "votes/answers"
                / vote["judge_id"]
                / (vote["effort"] or "default")
                / "pass2"
                / vote["item_id"]
                / f"{new_id}--a{vote['attempt']}.json"
            )
            body = write(dest, vote)
            history.update(
                grading_run_id=new_id,
                unit_id=vote["unit_id"],
                vote_sha256=digest(body),
                **{"pass": 2},
            )
            invocation["execution_results"][relative] = digest(
                write(root / "executions" / new_id / relative, history)
            )
        write(root / "runs" / f"{new_id}.json", invocation)
        added.extend(
            {**e, "run_id": new_id, "unit_id": units[e["unit_id"]]}
            for e in events
            if e.get("run_id") == old_id
        )
    with (root / "ledger-events.jsonl").open("a") as file:
        for event in added:
            file.write(json.dumps(event) + "\n")


def test_real_second_pass_survives_cache_edits_and_requires_receipts(validated_bundle, monkeypatch):
    from collections import Counter

    from benchmarks import agentic_report, report_files, report_measurements
    from benchmarks.grading import publication

    run = validated_bundle
    _record_second_pass(run)
    reads = Counter()
    reader = agentic_report.artifact_bytes

    def captured(root, path):
        body = reader(root, path)
        if "/votes/" in str(path) or path.name == "g1-entries.json":
            reads[path] += 1
        return body

    for module in (agentic_report, publication, report_files, report_measurements):
        monkeypatch.setattr(module, "artifact_bytes", captured)
    before = _render(run)
    assert "Real-answer test–retest alpha, haiku: 1.000" in before
    assert reads and set(reads.values()) == {1}
    path = run / "grading/summary.json"
    summary = read(path)
    summary["diagnostics"]["test_retest"] = {"haiku": {"alpha": -901}}
    write(path, summary)
    assert _render(run) == before
    receipt = next((run / "grading/executions").glob("retest-*/*/*.json"))
    receipt.unlink()
    after = _render(run)
    assert "Real-answer test–retest: unknown (unverified pass-2 evidence)" in after
    assert "-901" not in after
    assert "Grading outcome: Validated" in after


def test_pending_grades_retain_g2_pairs_without_claiming_final_diagnostics(validated_bundle):
    from benchmarks.agentic_report import _RunMetadata
    from benchmarks.report import _load_run_bundle

    run = validated_bundle
    path = next((run / "scoring").glob("*/*.json"))
    row = read(path)
    row.update(score=None, grading=None, scoring_method="pending", label=None)
    write(path, row)
    current = _RunMetadata(_load_run_bundle(run))
    assert current.grade_capture.reconciled
    assert len(current.grade_capture.finals) == 299
    assert current.summary["breakdowns"]["G2"]["by_family"]
    # Its membership is registered, but its unavailable final contributes no
    # reference comparison and cannot inherit the saved 300-answer gate.
    confusion = current.summary["breakdowns"]["G3"]["confusion"]
    assert sum(n for row in confusion.values() for n in row.values()) == 299
    assert current.summary["gates"]["G3"]["passed"] is not True
    assert current.tier != "Validated"


def test_grounding_quantities_follow_captured_answers(tmp_path):
    from tests.benchmarks import test_agentic_report as reports

    run = reports._build(tmp_path)
    reports._score(run, tmp_path)
    before = _render(run)
    for path in (run / "scoring").glob("*/*.json"):
        row = read(path)
        row["grounded"] = not row.get("grounded", False)
        row["correct_but_ungrounded"] = not row.get("correct_but_ungrounded", False)
        write(path, row)
    assert _render(run) == before
