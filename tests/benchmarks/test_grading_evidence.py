"""Credential boundaries at the grading evidence writers, using synthetic provider shapes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading.probes import Probe, ProbeFile, write_probe_file
from benchmarks.grading.score import score_run_dir
from tests.benchmarks import test_grading_aggregate as votes
from tests.benchmarks import test_grading_score as runs

GOOGLE = "AIza" + "g" * 35
OPENAI = "sk-proj-" + "s" * 40


def _score(run: Path, *g1_paths: Path) -> dict:
    return score_run_dir(
        run,
        criteria_path=runs.CRITERIA,
        panel_path=runs.PANEL_PATH,
        schema_path=runs.SCHEMA,
        g1_paths=g1_paths,
    )


@pytest.mark.parametrize(
    "boundary,token", [("\n", GOOGLE), ("\u2014", OPENAI)], ids=["lf", "unicode"]
)
def test_vote_checks_decoded_explanation_before_exclusive_create(
    tmp_path: Path, boundary: str, token: str
) -> None:
    text = json.dumps({"explanation": "judge" + boundary + token, **votes._ALL_MET})
    with pytest.raises(KeyLeakError) as error:
        votes._write(tmp_path, "luna", votes._result(text))
    assert token not in str(error.value)
    assert not list(tmp_path.rglob("*.json"))


def test_score_checks_summary_before_writing(tmp_path: Path) -> None:
    run = runs._build_run(tmp_path)
    runs._json(run / "environment.json", {"mode": "agentic", "seed": "seed\n" + GOOGLE})
    with pytest.raises(KeyLeakError) as error:
        _score(run)
    assert GOOGLE not in str(error.value)
    assert not (run / "grading" / "summary.json").exists()


def test_score_checks_group_names_before_publishing_breakdowns(tmp_path: Path) -> None:
    run = runs._build_run(tmp_path)
    path = next((run / "transcripts").glob("*/*.json"))
    transcript = json.loads(path.read_bytes())
    transcript["model_family_id"] = "family\u2014" + OPENAI
    runs._json(path, transcript)
    with pytest.raises(KeyLeakError) as error:
        _score(run)
    assert OPENAI not in str(error.value)
    assert not (run / "grading" / "disclosure" / "breakdowns.json").exists()


def test_vote_rejects_unsafe_item_id_without_echoing_the_path(tmp_path: Path) -> None:
    result = votes._result(votes._text(), item_id="item\n" + GOOGLE)
    with pytest.raises(KeyLeakError) as error:
        votes._write(tmp_path, "luna", result)
    assert GOOGLE not in str(error.value)
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"unused": {"note\u2014" + OPENAI: "ignored"}}),
        # The extra decoded letter makes this too long for a Google key. The
        # raw bytes still contain the exact key followed by a backslash.
        '{"note":"' + GOOGLE + r'\u0041"}',
        '{"note":"\\n' + GOOGLE + '","note":null}',
    ],
    ids=["escaped-name", "raw-bytes", "shadowed-value"],
)
def test_score_refuses_leak_in_raw_g1_even_outside_entries(tmp_path: Path, body: str) -> None:
    run = runs._build_run(tmp_path)
    path = tmp_path / "g1.json"
    path.write_bytes(body.encode())
    with pytest.raises(KeyLeakError) as error:
        _score(run, path)
    assert GOOGLE not in str(error.value) and OPENAI not in str(error.value)
    assert not list((run / "grading" / "disclosure" / "g1").glob("*.json"))


def test_raw_g1_digest_and_copy_use_the_original_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = runs._build_run(tmp_path)
    path = runs._g1_file(run, tmp_path / "g1.json")
    original = b"  " + path.read_bytes().replace(b"\n", b"\r\n") + b"\r\n"
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b'{"entries":{}}')
    path_open = Path.open
    reads = 0

    def replace_after_open(source: Path, *args, **kwargs):
        nonlocal reads
        handle = path_open(source, *args, **kwargs)
        if source == path:
            reads += 1
            if reads == 1:
                replacement.replace(path)
        return handle

    monkeypatch.setattr(Path, "open", replace_after_open)
    summary = _score(run, path)
    assert reads == 1
    gate = summary["gates"]["G1"]
    assert gate["passed"] is None  # The original entry describes a partial fixture.
    assert gate["details"]["overall"]["accuracy"] == 0.97
    copy = f"disclosure/g1/{digest}.json"
    assert summary["disclosure"]["g1_files"] == [
        {"source": str(path), "path": copy, "sha256": digest}
    ]
    assert (run / "grading" / copy).read_bytes() == original


def test_probe_checks_strings_hidden_by_yaml_escaping(tmp_path: Path) -> None:
    text = "probe\x01\n" + GOOGLE
    probe = Probe("id", "KEY", "Q1", "held_out", text, 1.0, "CORRECT", {"code": True})
    path = tmp_path / "probes.yml"
    with pytest.raises(KeyLeakError) as error:
        write_probe_file(path, ProbeFile({}, [probe], []))
    assert GOOGLE not in str(error.value)
    assert not path.exists()


def test_probe_checks_serialized_yaml_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(yaml, "safe_dump", lambda *args, **kwargs: "note: " + OPENAI + "\n")
    path = tmp_path / "probes.yml"
    with pytest.raises(KeyLeakError) as error:
        write_probe_file(path, ProbeFile({}, [], []))
    assert OPENAI not in str(error.value)
    assert not path.exists()


def test_withheld_vote_checks_json_text_even_without_a_parsed_vote(tmp_path: Path) -> None:
    text = json.dumps({"unexpected": "comment\n" + GOOGLE})
    with pytest.raises(KeyLeakError) as error:
        votes._write(tmp_path, "luna", votes._result(text))
    assert GOOGLE not in str(error.value)
    assert not list(tmp_path.rglob("*.json"))


def test_score_checks_legacy_vote_provenance_before_grading_a_cell(tmp_path: Path) -> None:
    run = runs._build_run(tmp_path)
    vote = next((run / "grading" / "votes" / "answers").rglob("*.json"))
    record = json.loads(vote.read_bytes())
    record["reported_model"] = "model\n" + GOOGLE
    runs._json(vote, record)
    cell = run / "grading" / "items" / "answers" / (record["item_id"] + ".json")
    meta = json.loads(cell.read_bytes())["meta"]
    scored_cell = run / "scoring" / meta["competitor_id"] / (meta["corpus_id"] + ".json")

    with pytest.raises(KeyLeakError) as error:
        _score(run)
    assert GOOGLE not in str(error.value)
    assert GOOGLE.encode() not in scored_cell.read_bytes()
    assert json.loads(scored_cell.read_bytes()).get("grading") is None


def test_pending_vote_queue_does_not_copy_a_key_from_an_answer(tmp_path: Path) -> None:
    from benchmarks.scoring import ScoringPaths

    run = runs._build_run(tmp_path)
    vote = next((run / "grading" / "votes" / "answers").rglob("*.json"))
    item_id = json.loads(vote.read_bytes())["item_id"]
    vote.unlink()
    item = json.loads((run / "grading" / "items" / "answers" / f"{item_id}.json").read_bytes())
    meta = item["meta"]
    path = run / "transcripts" / meta["competitor_id"] / f"{meta['corpus_id']}.json"
    transcript = json.loads(path.read_bytes())
    transcript["answer"] = "pending reply\n" + GOOGLE
    runs._json(path, transcript)
    with pytest.raises(KeyLeakError) as error:
        _score(run)
    assert GOOGLE not in str(error.value)
    assert not ScoringPaths(run).queue_path.exists()
