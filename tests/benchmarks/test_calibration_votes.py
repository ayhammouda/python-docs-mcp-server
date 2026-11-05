"""Calibration verdicts must follow the actual recorded request and raw reply."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.grading.score import write_g1
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_probes import index_db as index_db


def _entry(held: Path):
    return json.loads(write_g1(held).read_bytes())["entries"]["haiku+luna+flash"]


def test_complete_calibration_checks_raw_votes_before_export(tmp_path, index_db):
    run, held, entry, _ = runs._full_calibration(tmp_path, index_db, False)
    assert entry["gate"]["details"]["by_judge"]["haiku"]["hits"] == 700
    assert entry["reference_check"]["details"]["overall"]["hits"] == 350
    # The report's portable reader replays this genuine fallback composition
    # without reading the calibration workspace or a local docs index.
    from benchmarks.agentic.inputs import CapturedInput
    from benchmarks.grading.calibration import replay_g1
    from benchmarks.grading.gates import G1Binding

    frozen = run / "grading/inputs"
    gate, reference = replay_g1(
        {"haiku+luna+flash": entry},
        G1Binding(
            {j: entry["binding"]["composition"][j] for j in ("haiku", "luna", "flash")},
            entry["binding"]["inputs"],
        ),
        panel=CapturedInput.read(frozen / "panel.yml", "panel", "panel"),
        probes=CapturedInput.read(frozen / "probes.yml", "probes", "probes"),
        criteria=CapturedInput.read(frozen / "grading-criteria.yml", "criteria", "criteria"),
    )
    assert gate.passed is True and reference.passed is True
    assert gate.details["tuning"][0]["judges"]["flash"]["chosen"] == "low"
    assert "flash-lite" not in gate.details["tuning"][0]["judges"]
    assert gate.details["held_out_test_retest"]["haiku"]["pass2_records"] == 0
    export = (held / "g1.json").read_bytes()
    paths = list((held / "votes/probes/haiku/low/pass1").glob("*/*.json"))
    assert len(paths) == 700
    original = {p: p.read_bytes() for p in paths}
    negative = runs._vote_text(attempted=False)
    for path in paths:
        record = json.loads(original[path])
        record["text"] = negative
        runs._json(path, record)
    with pytest.raises(BenchmarkValidationError, match="stored vote differs"):
        write_g1(held)
    assert (held / "g1.json").read_bytes() == export

    # The same actual replies with agreeing parsed votes must produce the real failure.
    for path in paths:
        record = json.loads(path.read_bytes())
        record["vote"] = json.loads(negative)
        runs._json(path, record)
    failed = _entry(held)
    assert failed["gate"]["passed"] is False
    assert failed["gate"]["details"]["by_judge"]["haiku"]["hits"] == 450
    assert failed["reference_check"]["passed"] is True
    for path, body in original.items():
        path.write_bytes(body)
    assert _entry(held) == entry

    references = list((held / "votes/reference-probes/reference/high/pass1").glob("*/*.json"))
    assert len(references) == 350
    original_references = {p: p.read_bytes() for p in references}
    for path in references:
        record = json.loads(original_references[path])
        runs._json(path, {**record, "text": negative})
    export = (held / "g1.json").read_bytes()
    with pytest.raises(BenchmarkValidationError, match="stored vote differs"):
        write_g1(held)
    assert (held / "g1.json").read_bytes() == export
    for path in references:
        record = json.loads(path.read_bytes())
        runs._json(path, {**record, "vote": json.loads(negative)})
    failed_reference = _entry(held)
    assert failed_reference["gate"]["passed"] is True
    assert failed_reference["reference_check"]["passed"] is False
    assert failed_reference["reference_check"]["details"]["overall"]["hits"] == 225
    for path, body in original_references.items():
        path.write_bytes(body)
    assert _entry(held) == entry


def test_calibration_request_document_selection_reference_and_retest(tmp_path):
    held = runs._workspace(tmp_path / "held", "held-out", {"probes.yml": runs.PROBES})
    runs._json(held / "runs/run-1.json", {
        "target": "probes", "pass": 1, "composition": runs.COMPOSITION,
    })
    item = "KEY--SYN-EX-001"
    for judge in runs.JUDGES:
        runs._vote(held, judge, item, runs._vote_text(), target="probes")
    runs._vote(held, "reference", item, runs._vote_text(),
               target="reference-probes", effort="high")

    def _entry(held):
        (entry,) = json.loads(write_g1(held).read_bytes())["entries"].values()
        return entry

    baseline = _entry(held)
    runs._vote(held, "haiku", item, runs._vote_text(), target="probes", pass_number=2)
    intact = _entry(held)
    assert intact["gate"]["details"]["held_out_test_retest"]["haiku"]["items"] == 1

    def refused(path, change, match=None):
        original = path.read_bytes()
        export = (held / "g1.json").read_bytes()
        try:
            record = json.loads(original)
            change(record)
            runs._json(path, record)
            with pytest.raises(BenchmarkValidationError, match=match):
                write_g1(held)
            assert (held / "g1.json").read_bytes() == export
            (held / "g1.json").unlink()
            with pytest.raises(BenchmarkValidationError, match=match):
                write_g1(held)
            assert not (held / "g1.json").exists()
        finally:
            path.write_bytes(original)
            (held / "g1.json").write_bytes(export)

    locations = [
        held / "votes/probes/haiku/low/pass1" / item / "run-1--a1.json",
        held / "votes/probes/haiku/low/pass2" / item / "run-1--a1.json",
        next((held / "votes/reference-probes/reference/high/pass1").glob("*/*.json")),
    ]
    for path in locations:
        for field, value in (
            ("input_sha256", "d" * 64), ("target", "answers"),
            ("judge_id", "foreign"), ("model_id", "foreign"),
            ("effort", "high" if "haiku" in path.parts else "low"),
            ("pass", True), ("item_id", "foreign"), ("attempt", 2),
        ):
            refused(path, lambda r, k=field, v=value: r.update({k: v}))
        refused(path, lambda r: r.update(text=runs._vote_text(attempted=False)),
                "stored vote differs")
        refused(path, lambda r: r.update(vote={**r["vote"], "attempted": 1}),
                "stored vote differs")
    for target in ("probes", "reference-probes"):
        doc = next((held / "items" / target).glob("*.json"))
        for change in (
            lambda d: d.update(user=d["user"] + "\nChanged probe."),
            lambda d: d.update(system_sha256="d" * 64),
            lambda d: d.update(schema_sha256="d" * 64),
            lambda d: d["meta"].update(question_id="foreign"),
            lambda d: d["meta"].update(suspect=False if d["meta"]["suspect"] else True),
        ):
            refused(doc, change)
        body = doc.read_bytes()
        doc.unlink()
        try:
            with pytest.raises(BenchmarkValidationError, match="missing.*(document|input)"):
                write_g1(held)
        finally:
            doc.write_bytes(body)
    assert _entry(held) == intact
    assert intact["gate"]["passed"] is baseline["gate"]["passed"] is None


def test_calibration_replays_frozen_prompt_schema_and_selection(tmp_path, monkeypatch):
    from benchmarks.grading import inputs
    from benchmarks.grading.aggregate import write_vote_record

    held = runs._workspace(tmp_path / "held", "held-out", {"probes.yml": runs.PROBES})
    runs._json(held / "runs/run-1.json", {
        "target": "probes", "pass": 1, "composition": runs.COMPOSITION,
    })
    prompt = held / "inputs/judge-prompt.md"
    prompt.write_bytes(prompt.read_bytes() + b"\nFrozen local revision.\n")
    schema_path = held / "inputs/judge-schema.json"
    schema = json.loads(schema_path.read_bytes())
    schema["description"] = "Benign frozen schema revision."
    runs._json(schema_path, schema)
    manifest = json.loads((held / "inputs/inputs.json").read_bytes())
    manifest.update(judge_prompt=hashlib.sha256(prompt.read_bytes()).hexdigest(),
                    judge_schema=hashlib.sha256(schema_path.read_bytes()).hexdigest())
    runs._json(held / "inputs/inputs.json", manifest)
    meta = json.loads((held / "workspace.json").read_bytes())
    runs._json(held / "workspace.json", {**meta, "inputs": manifest})
    key = "KEY--SYN-EX-001"
    runs._vote(held, "haiku", key, runs._vote_text(), target="probes")
    # The verifier must not reopen today's prompt/schema to reinterpret old requests.
    monkeypatch.setattr(inputs, "JUDGE_PROMPT_PATH", tmp_path / "absent-prompt")
    first = write_g1(held).read_bytes()
    request = runs._probe_fixture_requests(held)[key][1]
    path = write_vote_record(
        held, target="probes", judge=runs.PANEL.judges["haiku"], effort="low",
        pass_number=1, grading_run_id="later", attempt=1,
        result=runs._result(key, None, "missing"), terminal=False,
        unit_id="local-unit", provider_batch_id="local-batch",
        input_sha256=request.sha256(), schema=schema,
    )
    newer = json.loads(write_g1(held).read_bytes())
    (old_entry,) = json.loads(first)["entries"].values()
    (new_entry,) = newer["entries"].values()
    # The unfinished attempt is retained as selection evidence; the final stays selected.
    assert {k: v for k, v in new_entry.items() if k != "source"} == {
        k: v for k, v in old_entry.items() if k != "source"
    }
    name = str(path.relative_to(held))
    assert json.loads(new_entry["source"]["files"][name])["status"] == "missing"
    assert name not in old_entry["source"]["files"]
    path.unlink()
    path = write_vote_record(
        held, target="probes", judge=runs.PANEL.judges["haiku"], effort="low",
        pass_number=1, grading_run_id="later", attempt=1,
        result=runs._result(key, runs._vote_text(attempted=False)), terminal=False,
        unit_id="local-unit", provider_batch_id="local-batch",
        input_sha256=request.sha256(), schema=schema,
    )
    updated = json.loads(write_g1(held).read_bytes())
    (entry,) = updated["entries"].values()
    assert entry["scores"]["judges"]["haiku"][key] == 0.0
    # The selected latest final must be verified, even though a good older take remains.
    before = (held / "g1.json").read_bytes()
    record = json.loads(path.read_bytes())
    runs._json(path, {**record, "text": runs._vote_text()})
    with pytest.raises(BenchmarkValidationError, match="stored vote differs"):
        write_g1(held)
    assert (held / "g1.json").read_bytes() == before


def test_legacy_calibration_without_request_proof_cannot_export(tmp_path, monkeypatch):
    from benchmarks.grading import score

    held = runs._workspace(tmp_path / "held", "held-out", {"probes.yml": runs.PROBES})
    runs._json(held / "runs/run-1.json", {
        "target": "probes", "pass": 1, "composition": runs.COMPOSITION,
    })
    key = "KEY--SYN-EX-001"
    runs._vote(held, "haiku", key, runs._vote_text(), target="probes")
    path = write_g1(held)
    before = path.read_bytes()
    document = held / "items/probes" / f"{key}.json"
    body = document.read_bytes()
    document.unlink()
    with pytest.raises(BenchmarkValidationError, match="legacy records.*cannot establish"):
        write_g1(held)
    assert path.read_bytes() == before
    document.write_bytes(body)

    # Historical stage 2 did not capture the corpus; its criteria still pin exact bytes.
    corpus = held / "inputs/corpus.yml"
    body = corpus.read_bytes()
    corpus.unlink()
    source = tmp_path / "legacy-corpus.yml"
    source.write_bytes(body)
    monkeypatch.setattr(score, "CORPUS_PATH", source)
    assert write_g1(held).read_bytes() == before
    source.write_bytes(body + b"\n# changed source\n")
    with pytest.raises(BenchmarkValidationError, match="legacy calibration lacks.*frozen corpus"):
        write_g1(held)
    assert path.read_bytes() == before
