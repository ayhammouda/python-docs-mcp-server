"""Benchmark corpus schema validation for the v0.5.0 public benchmark.

Validates a corpus file (``docs/benchmarks/corpus.yml`` once frozen, or any
other corpus-shaped YAML/JSON file such as a test fixture) against
``docs/benchmarks/corpus.schema.json``. See
``docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md`` ("Corpus Design") for the
methodology this schema encodes.

This module intentionally does not depend on a third-party JSON Schema
library: the schema file's ``$defs.question`` block (required fields,
``category`` enum, field types) and its ``x-category-distribution`` extension
are read directly and enforced with hand-written checks, matching the
existing house style in ``benchmarks.runner`` and ``benchmarks.model_matrix``.
This keeps the 15/10/15/5/5 methodology distribution declared exactly once
(in the schema file) while avoiding a new runtime dependency.

Corpus question AUTHORSHIP is out of scope here and for issue #94. This
module only validates shape, uniqueness, citation presence, category
membership, and category counts -- it never generates or edits corpus
questions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.runner import BenchmarkValidationError

_SAFE_ID_FIELD = "id"


@dataclass(frozen=True)
class CorpusQuestion:
    """One validated corpus question."""

    id: str
    category: str
    python_version: str | list[str]
    prompt: str
    answer_key: str
    citations: list[str]
    expected_properties: list[str]
    ambiguity_notes: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class CorpusValidationResult:
    """Outcome of validating a corpus file against the corpus schema."""

    corpus_path: Path
    schema_path: Path
    questions: list[CorpusQuestion]
    category_counts: dict[str, int]

    @property
    def question_count(self) -> int:
        return len(self.questions)


def validate_corpus(corpus_path: Path, schema_path: Path) -> CorpusValidationResult:
    """Validate ``corpus_path`` against ``schema_path``.

    Raises ``BenchmarkValidationError`` with a clean, non-traceback message on
    any of: a missing or malformed file, a corpus question that does not
    match the schema's ``question`` shape, a duplicate question ID, missing
    citations, an unsupported category, or a category whose count does not
    exactly match the schema's declared distribution.
    """
    schema = _load_schema(schema_path)
    question_schema = _question_schema(schema)
    required_fields = _required_fields(question_schema)
    allowed_categories = _allowed_categories(question_schema)
    expected_distribution = _category_distribution(schema, allowed_categories)

    corpus_data = _load_corpus(corpus_path)
    items = corpus_data.get("questions")
    if not isinstance(items, list) or not items:
        raise BenchmarkValidationError(
            f"corpus file must contain a non-empty 'questions' list: {corpus_path}"
        )

    questions: list[CorpusQuestion] = []
    seen_ids: set[str] = set()
    category_counts: dict[str, int] = dict.fromkeys(allowed_categories, 0)

    for index, item in enumerate(items):
        question = _validate_question(
            item,
            index=index,
            required_fields=required_fields,
            allowed_categories=allowed_categories,
            seen_ids=seen_ids,
        )
        questions.append(question)
        category_counts[question.category] += 1

    _validate_category_counts(category_counts, expected_distribution)

    return CorpusValidationResult(
        corpus_path=corpus_path,
        schema_path=schema_path,
        questions=questions,
        category_counts=category_counts,
    )


def _load_schema(schema_path: Path) -> dict[str, Any]:
    if not schema_path.exists():
        raise BenchmarkValidationError(f"corpus schema file does not exist: {schema_path}")
    try:
        text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkValidationError(
            f"corpus schema file could not be read: {schema_path}: {exc}"
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BenchmarkValidationError(
            f"corpus schema file is not valid JSON: {schema_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BenchmarkValidationError(
            f"corpus schema file must contain a JSON object: {schema_path}"
        )
    return data


def _question_schema(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs")
    question_schema = defs.get("question") if isinstance(defs, dict) else None
    if not isinstance(question_schema, dict):
        raise BenchmarkValidationError(
            "corpus schema file must define '$defs.question' describing one corpus question"
        )
    return question_schema


def _required_fields(question_schema: dict[str, Any]) -> list[str]:
    required = question_schema.get("required")
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(f, str) for f in required)
    ):
        raise BenchmarkValidationError(
            "corpus schema '$defs.question' must declare a non-empty 'required' field list"
        )
    return [str(field) for field in required]


def _allowed_categories(question_schema: dict[str, Any]) -> list[str]:
    properties = question_schema.get("properties")
    category_schema = properties.get("category") if isinstance(properties, dict) else None
    categories = category_schema.get("enum") if isinstance(category_schema, dict) else None
    if (
        not isinstance(categories, list)
        or not categories
        or not all(isinstance(c, str) for c in categories)
    ):
        raise BenchmarkValidationError(
            "corpus schema '$defs.question.properties.category' must declare a non-empty 'enum'"
        )
    return [str(category) for category in categories]


def _category_distribution(schema: dict[str, Any], allowed_categories: list[str]) -> dict[str, int]:
    distribution = schema.get("x-category-distribution")
    if not isinstance(distribution, dict) or not distribution:
        raise BenchmarkValidationError(
            "corpus schema must declare a non-empty 'x-category-distribution' mapping"
        )
    parsed: dict[str, int] = {}
    for category, count in distribution.items():
        is_valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        if not isinstance(category, str) or not is_valid_count:
            raise BenchmarkValidationError(
                "corpus schema 'x-category-distribution' entries must map a category name to a "
                f"non-negative integer count; got {category!r}: {count!r}"
            )
        if category not in allowed_categories:
            raise BenchmarkValidationError(
                f"corpus schema 'x-category-distribution' references unsupported category "
                f"{category!r}; allowed categories are {allowed_categories}"
            )
        parsed[category] = count
    return parsed


def _load_corpus(corpus_path: Path) -> dict[str, Any]:
    if not corpus_path.exists():
        raise BenchmarkValidationError(f"corpus file does not exist: {corpus_path}")
    try:
        text = corpus_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BenchmarkValidationError(
            f"corpus file could not be read: {corpus_path}: {exc}"
        ) from exc

    if corpus_path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BenchmarkValidationError(
                f"corpus file is not valid JSON: {corpus_path}: {exc}"
            ) from exc
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise BenchmarkValidationError(
                f"corpus file is not valid YAML: {corpus_path}: {exc}"
            ) from exc

    if not isinstance(data, dict):
        raise BenchmarkValidationError(f"corpus file must contain a mapping: {corpus_path}")
    return data


def _validate_question(
    item: Any,
    *,
    index: int,
    required_fields: list[str],
    allowed_categories: list[str],
    seen_ids: set[str],
) -> CorpusQuestion:
    label = f"corpus question at index {index}"
    if not isinstance(item, dict):
        raise BenchmarkValidationError(f"{label} must be a mapping")

    missing = [field for field in required_fields if field not in item]
    if missing:
        raise BenchmarkValidationError(f"{label} is missing required field(s): {missing}")

    question_id = item.get(_SAFE_ID_FIELD)
    if not isinstance(question_id, str) or not question_id.strip():
        raise BenchmarkValidationError(f"{label} must have a non-empty string 'id'")
    if question_id in seen_ids:
        raise BenchmarkValidationError(f"duplicate corpus question id: {question_id!r}")
    seen_ids.add(question_id)

    category = item.get("category")
    if not isinstance(category, str) or not category.strip():
        raise BenchmarkValidationError(
            f"corpus question {question_id!r} must have a non-empty 'category'"
        )
    if category not in allowed_categories:
        raise BenchmarkValidationError(
            f"corpus question {question_id!r} has unsupported category {category!r}; "
            f"allowed categories are {allowed_categories}"
        )

    python_version = _validate_python_version(item.get("python_version"), question_id)
    prompt = _require_nonempty_string(item, "prompt", question_id)
    answer_key = _require_nonempty_string(item, "answer_key", question_id)
    citations = _require_nonempty_string_list(item, "citations", question_id)
    expected_properties = _require_nonempty_string_list(item, "expected_properties", question_id)

    ambiguity_notes = item.get("ambiguity_notes")
    if ambiguity_notes is not None and not isinstance(ambiguity_notes, str):
        raise BenchmarkValidationError(
            f"corpus question {question_id!r} 'ambiguity_notes' must be a string or null"
        )

    return CorpusQuestion(
        id=question_id,
        category=category,
        python_version=python_version,
        prompt=prompt,
        answer_key=answer_key,
        citations=citations,
        expected_properties=expected_properties,
        ambiguity_notes=ambiguity_notes,
        raw=item,
    )


def _validate_python_version(value: Any, question_id: str) -> str | list[str]:
    if isinstance(value, str) and value.strip():
        return value
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, str) and v.strip() for v in value)
    ):
        return [str(v) for v in value]
    raise BenchmarkValidationError(
        f"corpus question {question_id!r} 'python_version' must be a non-empty string or a "
        "two-item list of version strings"
    )


def _require_nonempty_string(item: dict[str, Any], field: str, question_id: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkValidationError(
            f"corpus question {question_id!r} must have a non-empty string {field!r}"
        )
    return value


def _require_nonempty_string_list(item: dict[str, Any], field: str, question_id: str) -> list[str]:
    value = item.get(field)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v.strip() for v in value)
    ):
        raise BenchmarkValidationError(
            f"corpus question {question_id!r} must have a non-empty list of non-empty strings "
            f"for {field!r}"
        )
    return [str(v) for v in value]


def _validate_category_counts(
    category_counts: dict[str, int], expected_distribution: dict[str, int]
) -> None:
    mismatches = {
        category: (category_counts.get(category, 0), expected_count)
        for category, expected_count in expected_distribution.items()
        if category_counts.get(category, 0) != expected_count
    }
    if mismatches:
        details = ", ".join(
            f"{category}: got {actual}, expected {expected}"
            for category, (actual, expected) in sorted(mismatches.items())
        )
        raise BenchmarkValidationError(f"corpus has wrong category counts: {details}")
