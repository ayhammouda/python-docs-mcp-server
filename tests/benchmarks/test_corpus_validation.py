"""Tests for benchmarks/corpus.py and `python -m benchmarks validate-corpus`.

Covers issue #94's acceptance criteria: the real placeholder fixture
(tests/benchmarks/fixtures/corpus.sample.yml) validates against the real
schema (docs/benchmarks/corpus.schema.json); each refusal reason (duplicate
IDs, missing citations, wrong category counts, unsupported categories) fires
with a clean BenchmarkValidationError message; and malformed YAML/JSON is
handled without a raw traceback.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.corpus import validate_corpus
from benchmarks.runner import BenchmarkValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
REAL_FIXTURE_PATH = REPO_ROOT / "tests" / "benchmarks" / "fixtures" / "corpus.sample.yml"

# Mirrors PUBLIC-BENCHMARK-METHODOLOGY.md "Corpus Design" and the
# 'x-category-distribution' block in docs/benchmarks/corpus.schema.json.
EXPECTED_DISTRIBUTION = {
    "exact_symbol": 15,
    "concept": 10,
    "cross_version": 15,
    "pep_adjacent": 5,
    "applied": 5,
}


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _question(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "Q-0000",
        "category": "concept",
        "python_version": "3.13",
        "prompt": "[TEST FIXTURE] placeholder prompt, not a real corpus question.",
        "answer_key": "[TEST FIXTURE] placeholder answer key.",
        "citations": ["https://example.invalid/fixture-docs/placeholder"],
        "expected_properties": ["mentions-placeholder"],
        "ambiguity_notes": None,
    }
    base.update(overrides)
    return base


def _minimal_valid_questions() -> list[dict[str, Any]]:
    """A minimal, mechanically generated 50-question set matching the
    methodology distribution -- used only to exercise the validator's
    mechanics in tests. Not a real corpus and never written to
    docs/benchmarks/corpus.yml.
    """
    questions: list[dict[str, Any]] = []
    counter = 0
    for category, count in EXPECTED_DISTRIBUTION.items():
        for _ in range(count):
            counter += 1
            python_version: str | list[str] = (
                ["3.12", "3.13"] if category == "cross_version" else "3.13"
            )
            questions.append(
                _question(
                    id=f"Q-{category}-{counter:03d}",
                    category=category,
                    python_version=python_version,
                )
            )
    return questions


def _corpus(path: Path, questions: list[dict[str, Any]] | None = None) -> Path:
    return _write_yaml(
        path,
        {"questions": questions if questions is not None else _minimal_valid_questions()},
    )


def test_real_fixture_validates_against_real_schema() -> None:
    result = validate_corpus(REAL_FIXTURE_PATH, REAL_SCHEMA_PATH)

    assert result.question_count == 50
    assert result.category_counts == EXPECTED_DISTRIBUTION
    ids = [question.id for question in result.questions]
    assert len(ids) == len(set(ids)), "fixture must not contain duplicate ids"


def test_real_schema_declares_methodology_distribution() -> None:
    schema = json.loads(REAL_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["x-category-distribution"] == EXPECTED_DISTRIBUTION
    assert sum(schema["x-category-distribution"].values()) == 50
    assert set(schema["$defs"]["question"]["properties"]["category"]["enum"]) == set(
        EXPECTED_DISTRIBUTION
    )


def test_valid_minimal_corpus_passes(tmp_path: Path) -> None:
    result = validate_corpus(_corpus(tmp_path / "corpus.yml"), REAL_SCHEMA_PATH)

    assert result.question_count == 50
    assert result.category_counts == EXPECTED_DISTRIBUTION


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    questions = _minimal_valid_questions()
    questions[1] = {**questions[1], "id": questions[0]["id"]}

    with pytest.raises(BenchmarkValidationError, match="duplicate corpus question id"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", questions), REAL_SCHEMA_PATH)


def test_missing_citations_rejected(tmp_path: Path) -> None:
    questions = _minimal_valid_questions()
    broken = copy.deepcopy(questions[0])
    broken["citations"] = []
    questions[0] = broken

    with pytest.raises(BenchmarkValidationError, match="citations"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", questions), REAL_SCHEMA_PATH)


def test_citations_field_entirely_missing_is_rejected(tmp_path: Path) -> None:
    questions = _minimal_valid_questions()
    broken = copy.deepcopy(questions[0])
    del broken["citations"]
    questions[0] = broken

    with pytest.raises(BenchmarkValidationError, match="missing required field"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", questions), REAL_SCHEMA_PATH)


@pytest.mark.parametrize("blank_citation", ["", "   "])
def test_blank_citation_entry_rejected(tmp_path: Path, blank_citation: str) -> None:
    # Regression cover (CodeRabbit review on PR #97): a citations list whose
    # only entry is an empty or whitespace-only string must be rejected the
    # same way as a missing or empty citations list. validate_corpus requires
    # every citation entry to be non-empty after strip
    # (benchmarks/corpus.py:_require_nonempty_string_list).
    questions = _minimal_valid_questions()
    broken = copy.deepcopy(questions[0])
    broken["citations"] = [blank_citation]
    questions[0] = broken

    with pytest.raises(BenchmarkValidationError, match="citations"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", questions), REAL_SCHEMA_PATH)


def test_wrong_category_counts_rejected(tmp_path: Path) -> None:
    questions = _minimal_valid_questions()
    # Flip one 'applied' question to 'concept': applied drops to 4, concept
    # rises to 11 -- both now disagree with the schema's declared distribution.
    for question in questions:
        if question["category"] == "applied":
            question["category"] = "concept"
            break

    with pytest.raises(BenchmarkValidationError, match="wrong category counts"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", questions), REAL_SCHEMA_PATH)


def test_unsupported_category_rejected(tmp_path: Path) -> None:
    questions = _minimal_valid_questions()
    questions[0] = {**questions[0], "category": "not-a-real-category"}

    with pytest.raises(BenchmarkValidationError, match="unsupported category"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", questions), REAL_SCHEMA_PATH)


def test_malformed_corpus_yaml_raises_validation_error(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.yml"
    corpus_path.write_text("questions: [unterminated\n", encoding="utf-8")

    with pytest.raises(BenchmarkValidationError, match="not valid YAML"):
        validate_corpus(corpus_path, REAL_SCHEMA_PATH)


def test_malformed_corpus_json_raises_validation_error(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text('{"questions": [', encoding="utf-8")

    with pytest.raises(BenchmarkValidationError, match="not valid JSON"):
        validate_corpus(corpus_path, REAL_SCHEMA_PATH)


def test_missing_corpus_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="does not exist"):
        validate_corpus(tmp_path / "missing.yml", REAL_SCHEMA_PATH)


def test_missing_schema_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="does not exist"):
        validate_corpus(_corpus(tmp_path / "corpus.yml"), tmp_path / "missing-schema.json")


def test_malformed_schema_json_raises_validation_error(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(BenchmarkValidationError, match="not valid JSON"):
        validate_corpus(_corpus(tmp_path / "corpus.yml"), schema_path)


def test_empty_questions_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="non-empty 'questions' list"):
        validate_corpus(_corpus(tmp_path / "corpus.yml", []), REAL_SCHEMA_PATH)


def test_cli_validate_corpus_accepts_real_fixture() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "validate-corpus",
            "--corpus",
            str(REAL_FIXTURE_PATH),
            "--schema",
            str(REAL_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["question_count"] == 50
    assert summary["category_counts"] == EXPECTED_DISTRIBUTION


def test_cli_validate_corpus_exits_nonzero_with_clean_message(tmp_path: Path) -> None:
    questions = _minimal_valid_questions()
    questions[1] = {**questions[1], "id": questions[0]["id"]}
    corpus_path = _corpus(tmp_path / "corpus.yml", questions)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "validate-corpus",
            "--corpus",
            str(corpus_path),
            "--schema",
            str(REAL_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "duplicate corpus question id" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_validate_corpus_handles_malformed_yaml_without_traceback(tmp_path: Path) -> None:
    # Regression cover (CodeRabbit review on PR #97): malformed corpus input
    # must be handled cleanly through the `python -m benchmarks` CLI path too,
    # not only via the direct validate_corpus() library surface -- a raw
    # yaml.YAMLError traceback would exit 1 and dump a stack trace instead of
    # taking the catch-BenchmarkValidationError-then-exit-2 path.
    corpus_path = tmp_path / "corpus.yml"
    corpus_path.write_text("questions: [unterminated\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks",
            "validate-corpus",
            "--corpus",
            str(corpus_path),
            "--schema",
            str(REAL_SCHEMA_PATH),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "not valid YAML" in result.stderr
    assert "Traceback" not in result.stderr
