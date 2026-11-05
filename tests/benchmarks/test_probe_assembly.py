"""Probe assembly replays captured requests and replies before publishing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.grading.probes import CHECK_SCHEMA, PROBE_SCHEMA, assignments
from benchmarks.grading.score import assemble_workspace_probes
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks import test_grading_score as runs
from tests.benchmarks.test_grading_probes import index_db as index_db


def _build(tmp_path: Path, *, criteria=runs.CRITERIA):
    ws = runs._workspace(tmp_path / "build", "probe-build", {}, criteria=criteria)
    sets = (runs.yaml.safe_load(criteria.read_bytes())["questions"],
            runs.load_tuning_questions(runs.TUNING_PATH))
    # Use the real writer, exact request digest and producer item document.
    for questions in sets:
        for (qid, kind), (gen, checker) in assignments(
            sorted(questions), runs.PANEL.generators,
        ).items():
            for target, judge, prefix, text, schema in (
                ("probe-gen", gen, "gen", {"probe_text": "Scripted candidate."}, PROBE_SCHEMA),
                ("probe-check", checker, "chk", {"explanation": "checked", "meets_spec": True},
                 CHECK_SCHEMA),
            ):
                runs._vote(ws, judge, runs.safe_id(prefix, kind, qid, "r1"), json.dumps(text),
                           target=target, effort=None, schema=schema)
    return ws


def _assemble(ws: Path, index: Path, *, criteria=runs.CRITERIA,
              corpus=runs.FIX / "corpus.sample.yml"):
    return assemble_workspace_probes(
        ws, criteria_path=criteria, tuning_path=runs.TUNING_PATH,
        panel_path=runs.PANEL_PATH, index_path=index,
        corpus_path=corpus, schema_path=runs.SCHEMA,
    )


def test_actual_assembler_rejects_stale_checker_and_generator(tmp_path, index_db):
    ws = _build(tmp_path)
    export = _assemble(ws, index_db).read_bytes()
    command = ["agentic-score", "--workspace", str(ws), "--step", "assemble-probes",
               "--index", str(index_db)]
    assert runs.main(command) == 0
    assert (ws / "probes.yml").read_bytes() == export
    for target, item, text in (
        ("probe-check", "chk--PARA--SYN-EX-001--r1",
         {"explanation": "rejected", "meets_spec": False}),
        ("probe-gen", "gen--PARA--SYN-EX-001--r1", {"probe_text": "Changed candidate."}),
    ):
        path = next((ws / "votes" / target).glob(f"*/*/pass1/{item}/*.json"))
        body = path.read_bytes()
        record = json.loads(body)
        runs._json(path, {**record, "text": json.dumps(text)})
        try:
            with pytest.raises(BenchmarkValidationError):
                _assemble(ws, index_db)
            assert (ws / "probes.yml").read_bytes() == export
            with pytest.raises(SystemExit) as error:
                runs.main(command)
            assert error.value.code == 2
            assert (ws / "probes.yml").read_bytes() == export
        finally:
            path.write_bytes(body)


def _round(ws: Path, kind: str, number: int, verdict: bool):
    qid = "SYN-EX-001"
    questions = runs.yaml.safe_load(runs.CRITERIA.read_bytes())["questions"]
    gen, checker = assignments(sorted(questions), runs.PANEL.generators)[qid, kind]
    for target, judge, prefix, text, schema in (
        ("probe-gen", gen, "gen", {"probe_text": "Scripted candidate."}, PROBE_SCHEMA),
        ("probe-check", checker, "chk", {"explanation": "checked", "meets_spec": verdict},
         CHECK_SCHEMA),
    ):
        runs._vote(ws, judge, runs.safe_id(prefix, kind, qid, f"r{number}"), json.dumps(text),
                   target=target, effort=None, schema=schema)


def _path(ws: Path, target: str, kind="PARA", number=1, qid="SYN-EX-001"):
    prefix = "gen" if target == "probe-gen" else "chk"
    item = runs.safe_id(prefix, kind, qid, f"r{number}")
    return next((ws / "votes" / target).glob(f"*/*/pass1/{item}/*.json"))


def _reject(ws, index, path, change):
    body = path.read_bytes()
    export = (ws / "probes.yml").read_bytes()
    record = json.loads(body)
    change(record)
    runs._json(path, record)
    try:
        with pytest.raises(BenchmarkValidationError):
            _assemble(ws, index)
        assert (ws / "probes.yml").read_bytes() == export
    finally:
        path.write_bytes(body)


def test_rounds_drops_selection_and_binding(tmp_path, index_db):
    ws = _build(tmp_path)
    _round(ws, "PARA", 2, False)
    _round(ws, "PARA", 3, True)
    first = _path(ws, "probe-check")
    record = json.loads(first.read_bytes())
    record["text"] = json.dumps({"explanation": "checked", "meets_spec": False})
    record["parsed"] = json.loads(record["text"])
    runs._json(first, record)
    suite = runs.yaml.safe_load(_assemble(ws, index_db).read_bytes())
    probe = next(p for p in suite["probes"] if p["id"] == "PARA--SYN-EX-001")
    assert probe["built_by"]["round"] == 3
    assert len(probe["built_by"]["failed_checks"]) == 2
    # Every preceding rejection and the final accepted round need intact evidence.
    for target in ("probe-gen", "probe-check"):
        for number in (1, 2, 3):
            path = _path(ws, target, number=number)
            for field, value in (
                ("input_sha256", "d" * 64), ("judge_id", "foreign"),
                ("model_id", "foreign"), ("effort", "high"), ("target", "answers"),
                ("pass", True), ("item_id", "foreign"), ("attempt", 2),
                ("grading_run_id", "other-run"),
            ):
                _reject(ws, index_db, path, lambda r, k=field, v=value: r.update({k: v}))
            doc = ws / "items" / target / f"{json.loads(path.read_bytes())['item_id']}.json"
            for field, value in (("question_id", "other"), ("round", True),
                                 ("probe_type", "FACT")):
                _reject(ws, index_db, doc,
                        lambda r, k=field, v=value: r["meta"].update({k: v}))
            _reject(ws, index_db, doc, lambda r: r.update(user=r["user"] + "changed"))
    # Tuning has its own assignment and question identity.
    _reject(ws, index_db, _path(ws, "probe-check", qid="TUNE-001"),
            lambda r: r.update(parsed={"explanation": "checked", "meets_spec": False}))
    # Matching changed generator output is still bound to the ORIGINAL checker request.
    _reject(ws, index_db, _path(ws, "probe-gen"), lambda r: r.update(
        text=json.dumps({"probe_text": "Replacement"}), parsed={"probe_text": "Replacement"}))
    # The last final wins over an unfinished retry, but all selected attempts are bound.
    path = _path(ws, "probe-gen", number=3)
    original = json.loads(path.read_bytes())
    newer = path.with_name("run-2--a2.json")
    runs._json(newer, {**original, "grading_run_id": "run-2", "attempt": 2,
                      "graded_at": "9999", "status": "errored", "withheld": "errored",
                      "terminal": False, "text": None, "parsed": None})
    assert _assemble(ws, index_db).is_file()
    _reject(ws, index_db, newer, lambda r: r.update(terminal=True, parsed={"probe_text": "fake"}))
    newer.unlink()
    # A real three-check rejection drops FACT and dependent PADX, without an approval.
    first = _path(ws, "probe-check", kind="FACT")
    r = json.loads(first.read_bytes())
    r.update(text=json.dumps({"explanation": "checked", "meets_spec": False}))
    r["parsed"] = json.loads(r["text"])
    runs._json(first, r)
    _round(ws, "FACT", 2, False)
    _round(ws, "FACT", 3, False)
    suite = runs.yaml.safe_load(_assemble(ws, index_db).read_bytes())
    assert any(d["type"] == "FACT" and d["reason"] == "check_failed" for d in suite["dropped"])
    assert any(d["type"] == "PADX" and d["reason"] == "fact_dropped" for d in suite["dropped"])
    _reject(ws, index_db, _path(ws, "probe-check", kind="FACT", number=3),
            lambda r: r.update(parsed={"explanation": "checked", "meets_spec": True}))


def test_withheld_omitted_unused_and_legacy_request_proof(tmp_path, index_db, monkeypatch):
    from benchmarks.grading import probes

    ws = _build(tmp_path)
    _assemble(ws, index_db)
    # Rendering uses frozen spec bytes, without reopening today's spec.
    monkeypatch.setattr(probes, "PROBE_SPEC_PATH", tmp_path / "unavailable")
    assert _assemble(ws, index_db).is_file()
    path = _path(ws, "probe-gen")
    for fields in (
        {"withheld": "refusal", "parsed": None},
        {"refusal": True, "withheld": "refusal", "parsed": None},
        {"status": "errored", "withheld": "errored"},
        {"stop_reason": "max_tokens", "withheld": "output_cap"},
        {"parsed": {"probe_text": "forged"}, "text": "not JSON"},
    ):
        _reject(ws, index_db, path, lambda r, f=fields: r.update(f))
    # Honest withholding never gives a checker permission to approve the absent candidate.
    _reject(ws, index_db, path, lambda r: r.update(
        text="not JSON", parsed=None, withheld="parse_failure"))
    doc = ws / "items/probe-gen/gen--PARA--SYN-EX-001--r1.json"
    body = doc.read_bytes()
    export = (ws / "probes.yml").read_bytes()
    doc.unlink()
    try:
        with pytest.raises(BenchmarkValidationError, match="request proof"):
            _assemble(ws, index_db)
        assert (ws / "probes.yml").read_bytes() == export
    finally:
        doc.write_bytes(body)
    # Later unneeded rounds can legitimately be absent. If present, they must replay.
    monkeypatch.undo()
    _round(ws, "PARA", 2, True)
    _assemble(ws, index_db)
    _reject(ws, index_db, _path(ws, "probe-gen", number=2),
            lambda r: r.update(text=json.dumps({"probe_text": "altered unused round"})))
    # Honest omitted later checker data and a real parse failure are harmless
    # after an earlier acceptance; the failed object is never used as a candidate.
    _round(ws, "PARA", 3, True)
    _path(ws, "probe-check", number=3).unlink()
    third = _path(ws, "probe-gen", number=3)
    r = json.loads(third.read_bytes())
    r.update(text="not JSON", parsed=None, withheld="parse_failure")
    runs._json(third, r)
    assert _assemble(ws, index_db).is_file()
