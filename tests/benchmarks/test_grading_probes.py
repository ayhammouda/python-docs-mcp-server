"""The probe suite builder (PR 5 Task 5; design §9.3; owner ruling 4)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from benchmarks.grading.inputs import (
    PROBE_SPEC_PATH,
    TUNING_PATH,
    Criterion,
    GradingQuestion,
    injection_suspect,
    load_criteria,
    load_tuning_questions,
)
from benchmarks.grading.probes import (
    AGENT_WRITTEN,
    CHECK_SCHEMA,
    CODE_BUILT,
    PROBE_SCHEMA,
    PROBE_TYPES,
    Probe,
    ProbeFile,
    assemble,
    assignments,
    check_request,
    cited_sections,
    code_probe,
    expected_grade,
    generation_request,
    load_probe_file,
    pad_sentences,
    reference_subset,
    write_probe_file,
)
from benchmarks.runner import BenchmarkValidationError
from mcp_server_python_docs.storage.db import bootstrap_schema, get_readwrite_connection

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "grading"
SAMPLE_CORPUS = REPO_ROOT / "tests" / "benchmarks" / "fixtures" / "corpus.sample.yml"
CORPUS_SCHEMA = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"

JSON_SENTENCES = [
    "Serialize obj to a JSON formatted str using this conversion table.",
    "If skipkeys is true, then dict keys that are not of a basic type will be skipped.",
    "Short one.",
    (
        "If ensure_ascii is true, the output is guaranteed to have all incoming "
        "non-ASCII characters escaped."
    ),
    (
        "If check_circular is false, then the circular reference check for container "
        "types will be skipped."
    ),
    "If allow_nan is false, then it will be a ValueError to serialize out of range float values.",
]


def _question(**overrides: Any) -> GradingQuestion:
    values: dict[str, Any] = {
        "id": "Q1",
        "category": "exact_symbol",
        "version": "3.13",
        "prompt": "What does json.dumps() return in Python 3.13?",
        "answer_key": "It returns JSON text.",
        "ambiguity_note": None,
        "citations": ("https://docs.python.org/3.13/library/json.html#json.dumps",),
        "criteria": (
            Criterion("str result", "States that the result is a str.", "core"),
            Criterion("JSON text", "States that the text is JSON.", "nuance"),
        ),
    }
    values.update(overrides)
    return GradingQuestion(**values)


@pytest.fixture
def index_db(tmp_path: Path) -> Path:
    path = tmp_path / "index.db"
    conn = get_readwrite_connection(path)
    bootstrap_schema(conn)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.13', 'en', 'Python 3.13', 1, 'https://docs.python.org/3.13/')"
    )
    doc_set = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    page_text = "The json module encodes and decodes JSON. It is in the standard library."
    conn.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/json.html', 'library/json', 'json', ?, ?)",
        (doc_set, page_text, len(page_text)),
    )
    doc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    text = " ".join(JSON_SENTENCES)
    conn.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, content_text, "
        "char_count) VALUES (?, 'library/json.html#json.dumps', 'json.dumps', 'json.dumps', 3, "
        "1, ?, ?)",
        (doc, text, len(text)),
    )
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize(
    "probe_type,weights,expected",
    [
        ("KEY", ("core", "nuance"), (1.0, "CORRECT")),
        ("DROP2", ("core", "nuance"), (0.5, "PARTIAL")),
        ("DROP1", ("core", "nuance"), (0.0, "INCORRECT")),
        ("DROP2", ("core", "core"), (0.0, "INCORRECT")),
        ("VER", ("core", "nuance"), (0.0, "INCORRECT")),
        ("SPLIT", ("core", "nuance"), (0.0, "NOT_ATTEMPTED")),
        ("INJ", ("core", "nuance"), (0.0, "NOT_ATTEMPTED")),
    ],
)
def test_expected_grades_follow_the_mapping(
    probe_type: str, weights: tuple[Any, Any], expected: tuple[float, str]
) -> None:
    q = _question(
        criteria=(
            Criterion("a", "A.", weights[0]),
            Criterion("b", "B.", weights[1]),
        )
    )
    assert expected_grade(probe_type, q) == expected


def test_probe_types_split_into_code_built_and_agent_written() -> None:
    assert set(PROBE_TYPES) == CODE_BUILT | set(AGENT_WRITTEN) and len(PROBE_TYPES) == 14
    assert not CODE_BUILT & set(AGENT_WRITTEN)
    assert list(PROBE_SCHEMA["properties"]) == ["probe_text"]
    assert list(CHECK_SCHEMA["properties"]) == ["explanation", "meets_spec"]


def test_code_probes_rotate_and_inj_json_is_a_suspect(index_db: Path) -> None:
    q = _question(answer_key="It returns JSON text.")
    assert code_probe("KEY", q, 0, index_path=index_db) == "It returns JSON text."
    assert injection_suspect(code_probe("INJ", q, 2, index_path=index_db) or "")
    assert code_probe("JUNK", q, 0, index_path=index_db) == q.prompt
    assert code_probe("JUNK", q, 1, index_path=index_db) == "Let me think about this step by step."
    assert code_probe("IDK", q, 4, index_path=index_db) == (
        "I can't answer that without more context."
    )
    assert code_probe("HEDGE", q, 0, index_path=index_db) == (
        "I'm not fully sure, but I believe this is right: It returns JSON text."
    )
    pad = code_probe("PAD", q, 0, index_path=index_db)
    assert pad == " ".join(["It returns JSON text.", *JSON_SENTENCES[:2], JSON_SENTENCES[3]])
    padx = code_probe("PADX", q, 0, index_path=index_db, fact_text="It returns bytes.")
    assert padx is not None and padx.startswith("It returns bytes. Serialize obj")
    assert code_probe("PADX", q, 0, index_path=index_db, fact_text=None) is None
    with pytest.raises(ValueError):
        code_probe("PARA", q, 0, index_path=index_db)


def test_pad_sentences_read_the_cited_section_from_the_index(index_db: Path) -> None:
    q = _question()
    (section,) = cited_sections(q, index_db)
    assert (section.version, section.page, section.anchor) == (
        "3.13",
        "library/json.html",
        "json.dumps",
    )
    assert section.heading == "json.dumps" and section.text.startswith("Serialize obj")
    sentences = pad_sentences(q, index_db)
    assert sentences == [JSON_SENTENCES[i] for i in (0, 1, 3, 4)]
    page = _question(citations=("https://docs.python.org/3.13/library/json.html",))
    assert [s.text for s in cited_sections(page, index_db)][0].startswith("The json module")
    keyed = _question(answer_key=f"{JSON_SENTENCES[0]} More.")
    assert JSON_SENTENCES[0] not in pad_sentences(keyed, index_db)
    absent = _question(citations=("https://docs.python.org/3.12/library/json.html#json.dumps",))
    assert cited_sections(absent, index_db) == [] and pad_sentences(absent, index_db) == []
    assert code_probe("PAD", absent, 0, index_path=index_db) is None


def test_generator_rotation_uses_three_providers_and_another_checks() -> None:
    a = assignments([f"Q{i}" for i in range(50)], ("sonnet", "sol", "gemini"))
    assert len(a) == 350 and all(g != c for g, c in a.values())
    assert Counter(g for g, _ in a.values()) == Counter({"sonnet": 117, "sol": 117, "gemini": 116})
    assert a[("Q0", "PARA")] == ("sonnet", "sol") and a[("Q1", "DROP1")] == ("gemini", "sonnet")


def test_requests_carry_the_spec_sections_and_safe_ids() -> None:
    q = _question()
    gen = generation_request("FACT", q, 2)
    assert gen.item_id == "gen--FACT--Q1--r2" and gen.schema == PROBE_SCHEMA
    assert gen.schema_name == "probe"
    assert "at most 120 words" in gen.system and "core fact altered" in gen.system
    assert "Checker" not in gen.system
    assert q.answer_key in gen.user and q.criteria[1].text in gen.user
    chk = check_request("FACT", q, "It returns bytes.", 2)
    assert chk.item_id == "chk--FACT--Q1--r2" and chk.schema == CHECK_SCHEMA
    assert "meets_spec" in chk.system and "core fact altered" in chk.system
    assert "<probe>\nIt returns bytes.\n</probe>" in chk.user


def test_assemble_keeps_the_first_passing_round_and_drops_after_three(index_db: Path) -> None:
    questions = {"Q1": _question(), "Q2": _question(id="Q2")}
    assignment = assignments(sorted(questions), ("sonnet", "sol", "gemini"))
    generated: dict[tuple[str, str, int], str] = {}
    checks: dict[tuple[str, str, int], bool] = {}
    for qid in questions:
        for t in AGENT_WRITTEN:
            generated[(qid, t, 1)] = f"{t} for {qid}, round 1"
            checks[(qid, t, 1)] = True
    for r in (1, 2, 3):
        generated[("Q1", "FACT", r)] = f"bad fact {r}"
        checks[("Q1", "FACT", r)] = False
    checks[("Q1", "PARA", 1)] = False
    generated[("Q1", "PARA", 2)] = "PARA for Q1, round 2"
    checks[("Q1", "PARA", 2)] = True
    probes, dropped = assemble(
        questions,
        "held_out",
        index_path=index_db,
        generated=generated,
        checks=checks,
        assignment=assignment,
    )
    by_id = {p.id: p for p in probes}
    assert "FACT--Q1" not in by_id and "PADX--Q1" not in by_id and "PADX--Q2" in by_id
    assert {"question_id": "Q1", "type": "FACT", "reason": "check_failed", "attempts": 3} in dropped
    assert any(d["type"] == "PADX" and d["question_id"] == "Q1" for d in dropped)
    para = by_id["PARA--Q1"]
    assert para.text == "PARA for Q1, round 2" and para.built_by["round"] == 2
    assert para.built_by["generator"] == assignment[("Q1", "PARA")][0]
    assert by_id["KEY--Q1"].built_by == {"code": True} and by_id["KEY--Q1"].set == "held_out"
    assert by_id["DROP2--Q2"].expected_score == 0.5
    assert by_id["PADX--Q2"].text.startswith("FACT for Q2, round 1 Serialize obj")
    assert len(probes) == 2 * 14 - 2


def test_probe_file_round_trips_and_pins_its_inputs(tmp_path: Path) -> None:
    criteria = FIX / "criteria.sample.yml"
    header = {
        "criteria_sha256": hashlib.sha256(criteria.read_bytes()).hexdigest(),
        "corpus_sha256": "c",
        "tuning_sha256": hashlib.sha256(TUNING_PATH.read_bytes()).hexdigest(),
        "spec_sha256": hashlib.sha256(PROBE_SPEC_PATH.read_bytes()).hexdigest(),
        "index_sha256": "i",
    }
    probe = Probe("KEY--Q1", "KEY", "Q1", "held_out", "x", 1.0, "CORRECT", {"code": True})
    dropped = [{"question_id": "Q1", "type": "PAD", "reason": "no_pad_text"}]
    path = tmp_path / "probes.yml"
    write_probe_file(path, ProbeFile(header, [probe], dropped))
    loaded = load_probe_file(path, criteria_path=criteria, tuning_path=TUNING_PATH)
    assert loaded == ProbeFile(header, [probe], dropped)
    other = tmp_path / "criteria.yml"
    other.write_text(criteria.read_text() + "# edited\n")
    with pytest.raises(BenchmarkValidationError, match="criteria_sha256"):
        load_probe_file(path, criteria_path=other, tuning_path=TUNING_PATH)
    with pytest.raises(BenchmarkValidationError, match="tuning_sha256"):
        load_probe_file(path, criteria_path=criteria, tuning_path=other)


def test_probes_sample_fixture_header_matches_its_inputs() -> None:
    criteria_path = FIX / "criteria.sample.yml"
    loaded = load_probe_file(
        FIX / "probes.sample.yml", criteria_path=criteria_path, tuning_path=TUNING_PATH
    )
    assert loaded.header["corpus_sha256"] == hashlib.sha256(SAMPLE_CORPUS.read_bytes()).hexdigest()
    assert loaded.header["spec_sha256"] == hashlib.sha256(PROBE_SPEC_PATH.read_bytes()).hexdigest()
    questions = load_criteria(criteria_path, corpus_path=SAMPLE_CORPUS, schema_path=CORPUS_SCHEMA)
    questions |= load_tuning_questions(TUNING_PATH)
    for probe in loaded.probes:
        expected = expected_grade(probe.type, questions[probe.question_id])
        assert (probe.expected_score, probe.expected_label) == expected
    assert Counter(p.set for p in loaded.probes) == {"held_out": 28, "tuning": 14}


def _held_out(n_per_type: int) -> list[Probe]:
    return [
        Probe(f"{t}--Q{i:02d}", t, f"Q{i:02d}", "held_out", "x", 0.0, "INCORRECT", {"code": True})
        for t in PROBE_TYPES
        for i in range(n_per_type)
    ]


def test_reference_subset_takes_25_per_type_deterministically() -> None:
    probes = _held_out(50)
    tuning = Probe("KEY--T", "KEY", "T", "tuning", "x", 1.0, "CORRECT", {"code": True})
    subset = reference_subset([*probes, tuning], seed="abc")
    assert len(subset) == 350 and tuning not in subset
    assert Counter(p.type for p in subset) == {t: 25 for t in PROBE_TYPES}
    assert subset == reference_subset(list(reversed(probes)), seed="abc")
    assert subset != reference_subset(probes, seed="abd")
    assert len(reference_subset(_held_out(10), seed="abc")) == 140
    assert json.dumps([p.id for p in subset])  # ids are plain strings
