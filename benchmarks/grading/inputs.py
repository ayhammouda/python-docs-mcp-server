"""Grading inputs: criteria, tuning questions, prompts, schema, masking (design §9.1).

Everything a judge sees is built here, from files under ``docs/benchmarks/``.
``inputs_manifest`` hashes those files, plus the score mapping and the masking
rules as content, so a grading directory can refuse any invocation whose
frozen inputs differ from its own (PR 5 plan, "Evidence integrity").
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from benchmarks.agentic.inputs import CapturedInput, CapturedJSON, read_input_bytes
from benchmarks.agentic.protocol import scanned_json_bytes
from benchmarks.corpus import validate_corpus
from benchmarks.runner import BenchmarkValidationError, _load_yaml_mapping
from benchmarks.urls import _URL_RE, _trim

Weight = Literal["core", "nuance"]

REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS = REPO_ROOT / "docs" / "benchmarks"
GRADING_DOCS = _DOCS / "grading"
CRITERIA_PATH = _DOCS / "grading-criteria.yml"
JUDGE_PROMPT_PATH = GRADING_DOCS / "judge-prompt.md"
JUDGE_SCHEMA_PATH = GRADING_DOCS / "judge-schema.json"
KEY_AUDIT_PROMPT_PATH = GRADING_DOCS / "key-audit-prompt.md"
PROBE_SPEC_PATH = GRADING_DOCS / "probe-spec.md"
TUNING_PATH = GRADING_DOCS / "tuning-questions.yml"
PROBES_PATH = GRADING_DOCS / "probes.yml"

EXPLANATION_FIELD = "explanation"  # ruling 1: not "reasoning"
VOTE_FIELDS = ("attempted", "p1_met", "p2_met", "contradicts_key", "wrong_version")
CATEGORIES = ("exact_symbol", "concept", "cross_version", "pep_adjacent", "applied")
MAX_CRITERION_CHARS = 300
MAX_ITEM_ID = 64  # Anthropic custom_id: [A-Za-z0-9_-]{1,64}

_DOCS_PATH_RE = re.compile(
    r"(?<![\w/])(?:\d\.\d+/)?(?:library|reference|tutorial|howto|whatsnew|using|c-api|faq"
    r"|glossary|extending|installing|deprecations)/[\w./-]+(?:#[\w.:-]+)?"
)
MAPPING_TABLE = (
    "Score mapping (design §9.1), checked in order:\n"
    "1. not attempted -> 0.0 NOT_ATTEMPTED\n"
    "2. contradicts_key or wrong_version -> 0.0 INCORRECT\n"
    "3. a core criterion not met -> 0.0 INCORRECT\n"
    "4. both criteria met -> 1.0 CORRECT\n"
    "5. otherwise -> 0.5 PARTIAL\n"
)

MASK_RULES = (
    "Masking rules (design §9.1): each match below becomes [link], URLs first.\n"
    f"url: {_URL_RE.pattern} (trailing characters trimmed as "
    "benchmarks.urls.extract_urls trims them)\n"
    f"docs_path: {_DOCS_PATH_RE.pattern}\n"
)
_DELIMITER_RE = re.compile(r"<(/?)answer>", re.IGNORECASE)
_VERDICT_KEY_RE = re.compile(
    r'"(?:explanation|attempted|p1_met|p2_met|contradicts_key|wrong_version)"\s*:', re.IGNORECASE
)

#: Snapshot file name of each manifest entry that is a file.
SNAPSHOT_NAMES = {
    "criteria": "grading-criteria.yml",
    "judge_prompt": "judge-prompt.md",
    "judge_schema": "judge-schema.json",
    "key_audit_prompt": "key-audit-prompt.md",
    "probe_spec": "probe-spec.md",
    "panel": "panel.yml",
    "probes": "probes.yml",
}

SNAPSHOT_FILES = {**SNAPSHOT_NAMES, "mapping_table": "mapping.txt", "mask_rules": "masking.txt"}


@dataclass(frozen=True)
class Criterion:
    property: str  # the corpus expected_properties text, verbatim
    text: str  # one sentence an answer is checked against
    weight: Weight


@dataclass(frozen=True)
class GradingQuestion:
    id: str
    category: str
    version: str  # "3.13", or "3.12 and 3.13" for a version pair
    prompt: str
    answer_key: str
    ambiguity_note: str | None
    citations: tuple[str, ...]
    criteria: tuple[Criterion, Criterion]

    @property
    def weights(self) -> tuple[Weight, Weight]:
        return (self.criteria[0].weight, self.criteria[1].weight)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(value: Any) -> str:
    return " and ".join(value) if isinstance(value, list) else str(value)


def _criteria(entries: Any, properties: list[str], qid: str) -> tuple[Criterion, Criterion]:
    if len(properties) != 2:
        raise BenchmarkValidationError(f"criteria for {qid} require two expected properties")
    if not isinstance(entries, list) or len(entries) != 2:
        raise BenchmarkValidationError(f"criteria for {qid} must be a list of two entries")
    criteria: list[Criterion] = []
    for entry, prop in zip(entries, properties, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"property", "criterion", "weight"}:
            raise BenchmarkValidationError(
                f"criteria for {qid}: each entry needs exactly property, criterion and weight"
            )
        if entry["property"] != prop:
            raise BenchmarkValidationError(
                f"criteria for {qid}: property {entry['property']!r} is not the corpus "
                f"property {prop!r}"
            )
        text = entry["criterion"]
        if (
            not isinstance(text, str)
            or not text.endswith(".")
            or "\n" in text
            or len(text) > MAX_CRITERION_CHARS
        ):
            raise BenchmarkValidationError(
                f"criteria for {qid}: a criterion is one line ending in '.', at most "
                f"{MAX_CRITERION_CHARS} characters"
            )
        if entry["weight"] not in ("core", "nuance"):
            raise BenchmarkValidationError(f"criteria for {qid}: weight must be core or nuance")
        criteria.append(Criterion(prop, text, entry["weight"]))
    if not any(c.weight == "core" for c in criteria):
        raise BenchmarkValidationError(f"criteria for {qid} need at least one core criterion")
    return criteria[0], criteria[1]


def _question(raw: dict[str, Any], criteria: tuple[Criterion, Criterion]) -> GradingQuestion:
    return GradingQuestion(
        id=raw["id"],
        category=raw["category"],
        version=_version(raw["python_version"]),
        prompt=raw["prompt"],
        answer_key=raw["answer_key"],
        ambiguity_note=raw.get("ambiguity_notes"),
        citations=tuple(raw["citations"]),
        criteria=criteria,
    )


def load_criteria(
    path: Path, *, corpus_path: Path, schema_path: Path
) -> dict[str, GradingQuestion]:
    """Join the criteria file to the corpus it was derived from, in corpus order."""
    return criteria_from_mapping(
        _load_yaml_mapping(path, "grading criteria"),
        path,
        corpus_path=corpus_path,
        schema_path=schema_path,
    )


def criteria_from_mapping(
    data: dict[str, Any], path: Path, *, corpus_path: Path, schema_path: Path
) -> dict[str, GradingQuestion]:
    if data.get("schema_version") != 1:
        raise BenchmarkValidationError(f"grading criteria {path} must set schema_version: 1")
    if data.get("corpus_sha256") != _sha256(corpus_path.read_bytes()):
        raise BenchmarkValidationError(
            f"grading criteria {path}: corpus_sha256 does not match {corpus_path}"
        )
    corpus = validate_corpus(corpus_path, schema_path).questions
    entries = data.get("questions")
    if not isinstance(entries, dict):
        raise BenchmarkValidationError(f"grading criteria {path} must map question ids")
    ids = [q.id for q in corpus]
    if unknown := sorted(set(entries) - set(ids)):
        raise BenchmarkValidationError(f"grading criteria name unknown questions: {unknown}")
    if missing := [qid for qid in ids if qid not in entries]:
        raise BenchmarkValidationError(f"grading criteria miss questions: {missing}")
    return {
        q.id: _question(q.raw, _criteria(entries[q.id], q.expected_properties, q.id))
        for q in corpus
    }


def load_tuning_questions(path: Path) -> dict[str, GradingQuestion]:
    data = _load_yaml_mapping(path, "tuning questions")
    items = data.get("questions")
    if data.get("schema_version") != 1 or not isinstance(items, list) or not items:
        raise BenchmarkValidationError(
            f"tuning questions {path} need schema_version: 1 and a 'questions' list"
        )
    required = ("id", "category", "python_version", "prompt", "answer_key", "citations")
    questions: dict[str, GradingQuestion] = {}
    for raw in items:
        if not isinstance(raw, dict) or any(not raw.get(field) for field in required):
            raise BenchmarkValidationError(f"tuning question needs {list(required)}: {raw!r}")
        qid = raw["id"]
        if qid in questions:
            raise BenchmarkValidationError(f"duplicate tuning question id {qid!r}")
        if raw["category"] not in CATEGORIES:
            raise BenchmarkValidationError(f"tuning question {qid}: unknown category")
        properties = raw.get("expected_properties")
        if not isinstance(properties, list) or len(properties) != 2:
            raise BenchmarkValidationError(f"tuning question {qid} needs two expected_properties")
        questions[qid] = _question(raw, _criteria(raw.get("criteria"), properties, qid))
    return questions


def judge_prompt() -> str:
    return JUDGE_PROMPT_PATH.read_text(encoding="utf-8")


def judge_schema() -> dict[str, Any]:
    return json.loads(JUDGE_SCHEMA_PATH.read_text(encoding="utf-8"))


def key_audit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"explanation": {"type": "string"}, "key_supported": {"type": "boolean"}},
        "required": ["explanation", "key_supported"],
        "additionalProperties": False,
    }


def safe_id(*parts: str) -> str:
    """A batch item id: parts joined by ``--``, any char outside ``[A-Za-z0-9_-]`` -> ``_``."""
    value = re.sub(r"[^A-Za-z0-9_-]", "_", "--".join(parts))
    if not value or len(value) > MAX_ITEM_ID:
        raise ValueError(f"item id {value!r} must be 1 to {MAX_ITEM_ID} characters")
    return value


def mask_links(text: str) -> str:
    def url(match: re.Match[str]) -> str:
        found = match.group(0)
        return "[link]" + found[len(_trim(found)) :]

    return _DOCS_PATH_RE.sub("[link]", _URL_RE.sub(url, text))


def escape_delimiters(text: str) -> str:
    return _DELIMITER_RE.sub(lambda m: f"&lt;{m.group(1)}answer&gt;", text)


def injection_suspect(text: str) -> bool:
    """An answer holding a delimiter or a verdict-shaped JSON key (design §9.1)."""
    return bool(_DELIMITER_RE.search(text) or _VERDICT_KEY_RE.search(text))


@dataclass(frozen=True)
class JudgeInput:
    system: str
    user: str
    schema: dict[str, Any]
    suspect: bool

    def sha256(self) -> str:
        """Over system, user and the canonical schema JSON: the exact request content."""
        schema = json.dumps(self.schema, sort_keys=True, separators=(",", ":"))
        return _sha256(json.dumps([self.system, self.user, schema], separators=(",", ":")).encode())


def render_judge_input(question: GradingQuestion, answer: str) -> JudgeInput:
    lines = [
        f'<question version="{question.version}">{question.prompt}</question>',
        f"<answer_key>{question.answer_key}</answer_key>",
        "<criteria>",
        f"P1: {question.criteria[0].text}",
        f"P2: {question.criteria[1].text}",
        "</criteria>",
    ]
    if question.ambiguity_note:
        lines.append(f"<ambiguity_note>{question.ambiguity_note}</ambiguity_note>")
    lines += ["<answer>", escape_delimiters(mask_links(answer)), "</answer>"]
    return JudgeInput(
        system=judge_prompt(),
        user="\n".join(lines),
        schema=judge_schema(),
        suspect=injection_suspect(answer),
    )


def _manifest_files(
    criteria_path: Path, panel_path: Path, probes_path: Path | None
) -> dict[str, Path]:
    files = {
        "criteria": criteria_path,
        "judge_prompt": JUDGE_PROMPT_PATH,
        "judge_schema": JUDGE_SCHEMA_PATH,
        "key_audit_prompt": KEY_AUDIT_PROMPT_PATH,
        "probe_spec": PROBE_SPEC_PATH,
        "panel": panel_path,
    }
    if probes_path is not None:
        files["probes"] = probes_path
    return files


def scanned_input(path: Path, *, suffix: str) -> bytes:
    """Read and inspect the same bytes that will be hashed or exported."""
    if suffix in (".yml", ".yaml"):
        return CapturedInput.read(path, "grading input", "grading input").content
    if suffix == ".json":
        return CapturedJSON.read(path, "grading input").content
    return read_input_bytes(path, "grading input", "grading input")


def _input_bytes(
    criteria_path: Path, panel_path: Path, probes_path: Path | None
) -> dict[str, bytes]:
    files = {
        name: scanned_input(path, suffix=Path(SNAPSHOT_NAMES[name]).suffix)
        for name, path in _manifest_files(criteria_path, panel_path, probes_path).items()
    }
    files.update(mapping_table=MAPPING_TABLE.encode(), mask_rules=MASK_RULES.encode())
    return files


def inputs_manifest(
    *, criteria_path: Path, panel_path: Path, probes_path: Path | None
) -> dict[str, str]:
    """sha256 of every frozen input: file bytes, and the mapping and masking as content."""
    files = _input_bytes(criteria_path, panel_path, probes_path)
    return {name: _sha256(body) for name, body in files.items()}


def snapshot_inputs(
    dest: Path, *, criteria_path: Path, panel_path: Path, probes_path: Path | None
) -> dict[str, str]:
    """Write the exact checked bytes once, then publish their manifest."""
    files = _input_bytes(criteria_path, panel_path, probes_path)
    manifest = {name: _sha256(body) for name, body in files.items()}
    manifest_body = scanned_json_bytes(manifest, where="input manifest", indent=2, sort_keys=True)
    dest.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        with (dest / SNAPSHOT_FILES[name]).open("xb") as file:
            file.write(body)
    with (dest / "inputs.json").open("xb") as file:
        file.write(manifest_body + b"\n")
    return manifest
