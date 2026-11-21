"""Synthetic evidence only: no private run artifacts or provider calls."""

import hashlib
import json
from collections import Counter

import pytest
import yaml

from benchmarks.agentic.prompt import SYSTEM_PROMPT
from benchmarks.runner import _load_questions
from benchmarks.structured import (
    FORMAT_INSTRUCTION,
    StructuredError,
    main,
    parse_answer,
    render_corpus,
    score_run,
)


@pytest.fixture
def inputs():
    source = {"schema_version": 1, "source_tags": {"3.13": "reference"}, "questions": []}
    items = []
    for n in range(50):
        ident = f"Q-{n:03}"
        source["questions"].append({
            "id": ident, "prompt": f"Old question {n}", "answer_key": "old key",
            "category": "concept", "python_version": "3.13",
            "citations": ["https://docs.python.org/3.13/library/json.html"],
            "expected_properties": ["property"], "ambiguity_notes": None,
        })
        items.append({
            "id": ident, "question": f"Authored question {n}?",
            "correct": f"Correct {n}", "distractors": [f"Wrong {n}/{i}" for i in range(3)],
        })
    return source, items


def rendered(inputs):
    source, items = inputs
    return render_corpus(yaml.safe_dump(source).encode(), json.dumps(items).encode())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def evidence(tmp_path, inputs):
    corpus = tmp_path / "frozen.yml"
    body = rendered(inputs)
    corpus.write_bytes(body)
    run = tmp_path / "run"
    (run / "snapshots").mkdir(parents=True)
    (run / "snapshots/corpus.yml").write_bytes(body)
    (run / "snapshots/system-prompt.txt").write_text(SYSTEM_PROMPT)
    cells = [
        {
            "cell": f"docs--model--r{repeat}", "competitor_id": f"docs--model--r{repeat}",
            "system_id": "docs", "model_family_id": "model", "repeat": repeat,
            "corpus_id": f"Q-{n:03}",
        }
        for repeat in (1, 2) for n in range(50)
    ]
    write_json(run / "planned-cells.json", {"cells": cells})
    return run, corpus, cells


def transcript(run, cell, answer, status="succeeded"):
    record = {
        **cell, "answer": answer, "status": status,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "canonical_messages": ["PRIVATE TRANSCRIPT: never publish"],
        "model_calls": [{"raw_response": "PRIVATE COMPLETION"}],
        "timing": {"wall_ms": 12, "used_ms": 8, "overhead_ms": 1, "secret": "PRIVATE"},
    }
    # The retained runner artifact has competitor_id, not the plan's cell alias.
    record.pop("cell")
    path = run / "transcripts" / cell["cell"] / f"{cell['corpus_id']}.json"
    write_json(path, record)
    return path


def test_render_is_reproducible_balanced_and_runner_compatible(inputs):
    content = rendered(inputs)
    assert content == rendered(inputs)
    corpus = yaml.safe_load(content)
    assert len(_load_questions(corpus)) == 50
    assert Counter(q["answer_key"] for q in corpus["questions"]) == {
        "A": 13, "B": 13, "C": 12, "D": 12,
    }
    source, items = inputs
    authored = {item["id"]: item for item in items}
    originals = {item["id"]: item for item in source["questions"]}
    for q in corpus["questions"]:
        item = authored[q["id"]]
        assert q["options"]["ABCD".index(q["answer_key"])] == item["correct"]
        assert set(q["options"]) == {item["correct"], *item["distractors"]}
        assert q["question"] == item["question"]
        assert q["prompt"].startswith(item["question"] + "\n\n")
        assert q["prompt"].endswith(FORMAT_INSTRUCTION)
        for label, option in zip("ABCD", q["options"], strict=True):
            assert f"{label}. {option}" in q["prompt"]
        for name in ("citations", "expected_properties", "category", "python_version"):
            assert q[name] == originals[q["id"]][name]
    # Input row/distractor ordering does not select different correct positions.
    items.reverse()
    for item in items:
        item["distractors"].reverse()
    assert yaml.safe_load(rendered(inputs))["questions"] == corpus["questions"]


@pytest.mark.parametrize("problem", ["count", "duplicate", "mismatch", "same", "blank", "three"])
def test_renderer_rejects_invalid_items(inputs, problem):
    _, items = inputs
    if problem == "count":
        items.pop()
    elif problem == "duplicate":
        items[1]["id"] = items[0]["id"]
    elif problem == "mismatch":
        items[0]["id"] = "OTHER"
    elif problem == "same":
        items[0]["distractors"][0] = " " + items[0]["correct"]
    elif problem == "blank":
        items[0]["question"] = " "
    else:
        items[0]["distractors"].pop()
    with pytest.raises(StructuredError):
        rendered(inputs)


def test_duplicate_source_ids_and_duplicate_members_are_rejected(inputs):
    source, items = inputs
    content = yaml.safe_dump(source).encode()
    with pytest.raises(StructuredError, match="duplicate"):
        render_corpus(content + b"schema_version: 1\n", json.dumps(items).encode())
    source["questions"][1]["id"] = source["questions"][0]["id"]
    with pytest.raises(StructuredError, match="duplicate"):
        rendered(inputs)


@pytest.mark.parametrize("text", [
    '{"answer":"A","answer":"B"}', '{"answer":"A","answer":"A"}',
    '{"answer":"A","citations":[],"citations":[]}',
    '```json\n{"answer":"A"}\n```', 'Here: {"answer":"A"}',
    '{"answer":"A"} trailing', '{"answer":"A"} {"answer":"A"}',
    '[]', 'null', '"A"', '{}', '{"answer":"a"}', '{"answer":"AB"}',
    '{"answer":"E"}', '{"answer":1}', '{"answer":["A"]}',
    '{"answer":"A","extra":true}', '{"answer":"A","citations":null}',
    '{"answer":"A","citations":"https://example.org"}',
    '{"answer":"A","citations":[1]}', '{"answer":"A","citations":["not a URL"]}',
    '{"answer":"A","citations":["https://exa mple.org"]}',
    '{"answer":"A","citations":["https://example.org:bad"]}',
    '{"answer":"A","citations":[NaN]}', '{"answer":"A",}', '', None,
])
def test_strict_invalid_answers(text):
    assert parse_answer(text) is None


@pytest.mark.parametrize("letter", list("ABCD"))
def test_valid_answers_and_optional_citations(letter):
    assert parse_answer(json.dumps({"answer": letter})) == letter
    assert parse_answer(' \n' + json.dumps({
        "answer": letter, "citations": ["https://docs.python.org/3/library/json.html#json.loads"],
    }) + '\n') == letter


def test_full_denominator_final_answer_only_and_clean_rows(evidence):
    run, corpus, cells = evidence
    keys = {q["id"]: q["answer_key"] for q in yaml.safe_load(corpus.read_bytes())["questions"]}
    for i, cell in enumerate(cells[:5]):
        key = keys[cell["corpus_id"]]
        answer = json.dumps({"answer": key})
        if i == 1:
            answer = json.dumps({"answer": "ABCD"[("ABCD".index(key) + 1) % 4]})
        if i == 2:
            answer = f'{{"answer":"{key}","answer":"{key}"}}'
        if i == 4:
            answer = ""
        path = transcript(run, cell, answer, "failed" if i == 3 else "succeeded")
        record = json.loads(path.read_text())
        # Intermediate answers cannot rescue a wrong/invalid/missing final answer.
        record["canonical_messages"] = [{"answer": key}]
        write_json(path, record)
    write_json(run / "tokens" / cells[0]["cell"] / "Q-000.json", {
        **cells[0], "client_wrapped_tokens": 42, "raw_payload_tokens": 21,
        "notes": "PRIVATE TOKEN NOTES",
    })
    report = score_run(run, corpus)
    assert (report["planned"], report["correct"], report["accuracy"]) == (100, 1, 0.01)
    assert report["incomplete"] == 96
    assert report["successful"] == 4
    assert report["valid"] == 3
    rows = {(r["question_id"], r["repeat"]): r for r in report["rows"]}
    assert rows["Q-000", 1]["client_wrapped_tokens"] == 42
    assert rows["Q-000", 1]["rounds"] == 1
    assert rows["Q-000", 1]["wall_ms"] == 12
    assert rows["Q-003", 1]["validity"] is True
    assert rows["Q-003", 1]["correctness"] == 0
    assert all(rows[f"Q-{n:03}", 2]["correctness"] == 0 for n in range(50))
    public = json.dumps(report)
    for excluded in ("PRIVATE", "canonical_messages", "raw_response", "prompt", "Authored",
                     "Correct 0", "notes", "citations"):
        assert excluded not in public


@pytest.mark.parametrize("problem", ["snapshot", "system", "duplicate", "coordinate", "orphan",
                                      "record", "metric", "record_system", "prompt", "binding"])
def test_refuses_unbound_or_inconsistent_evidence(evidence, problem):
    run, corpus, cells = evidence
    path = transcript(run, cells[0], '{"answer":"A"}')
    if problem == "snapshot":
        (run / "snapshots/corpus.yml").write_bytes(corpus.read_bytes() + b"\n")
    elif problem == "system":
        (run / "snapshots/system-prompt.txt").write_text("changed")
    elif problem in {"prompt", "binding"}:
        data = yaml.safe_load(corpus.read_bytes())
        if problem == "prompt":
            data["questions"][0]["prompt"] = "changed"
        else:
            del data["structured"]["items_sha256"]
        body = yaml.safe_dump(data).encode()
        corpus.write_bytes(body)
        (run / "snapshots/corpus.yml").write_bytes(body)
    elif problem == "duplicate":
        write_json(run / "planned-cells.json", {"cells": [*cells, cells[0]]})
    elif problem == "coordinate":
        cells[0]["repeat"] = 2
        write_json(run / "planned-cells.json", {"cells": cells})
    elif problem == "orphan":
        write_json(run / "transcripts/extra/Q-000.json", {})
    elif problem in {"record", "record_system"}:
        record = json.loads(path.read_text())
        record["corpus_id" if problem == "record" else "system_prompt_sha256"] = "wrong"
        write_json(path, record)
    else:
        write_json(run / "tokens" / cells[0]["cell"] / "Q-000.json", {
            "competitor_id": "wrong", "corpus_id": "Q-000", "client_wrapped_tokens": 42,
        })
    with pytest.raises(StructuredError):
        score_run(run, corpus)


def test_missing_final_answer_and_inconsistent_model(evidence):
    run, corpus, cells = evidence
    for i in (0, 1):
        path = transcript(run, cells[i], '{"answer":"A"}')
        record = json.loads(path.read_text())
        del record["answer"]
        record.update(provider="provider", model_id="deployment")
        write_json(path, record)
    report = score_run(run, corpus)
    assert report["incomplete"] == 100
    assert report["correct"] == report["valid"] == 0
    record["model_id"] = "different-deployment"
    write_json(path, record)
    with pytest.raises(StructuredError, match="model identity"):
        score_run(run, corpus)


def test_cli_freeze_and_score(tmp_path, inputs, evidence, capsys):
    source, items = inputs
    source_path, items_path, out = (
        tmp_path / name for name in ("source.yml", "items.json", "out.yml")
    )
    source_path.write_text(yaml.safe_dump(source))
    write_json(items_path, items)
    args = ["render", "--source", str(source_path), "--items", str(items_path), "--out", str(out)]
    assert main(args) == 0
    frozen = out.read_bytes()
    assert main(args) == 0
    items[0]["question"] += " changed"
    write_json(items_path, items)
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert "refusing to change" in capsys.readouterr().err
    assert out.read_bytes() == frozen
    run, corpus, _ = evidence
    report = tmp_path / "public.json"
    assert main(["score", "--run", str(run), "--corpus", str(corpus), "--out", str(report)]) == 0
    assert json.loads(report.read_text())["incomplete"] == 100
