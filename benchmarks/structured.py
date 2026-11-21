"""Local, deterministic multiple-choice rendering and scoring for issue #63.

Freeze before running models::

    python -m benchmarks.structured render --items docs/benchmarks/structured/items.json \
        --source docs/benchmarks/corpus.yml --out /tmp/structured-corpus.yml
    python -m benchmarks.structured score --corpus /tmp/structured-corpus.yml \
        --run /path/to/agentic-run --out /tmp/structured-scores.json

The rendered YAML is the private runner input and frozen scoring reference. Keep
its printed SHA-256 with the study plan. Rendering never replaces differing bytes.
Scoring requires a byte-identical run snapshot; do not substitute a post-run corpus.
Only the score report is publishable: question IDs, coordinates, letters and numeric
metrics, never question prompts, answer text, citations, keys or conversations.

Algorithm v1: sort IDs by (SHA256(UTF8("structured-v1/id/" + id)), id), assign
A/B/C/D cyclically, sort distractors by (SHA256(UTF8("structured-v1/option/" +
id + "/" + text)), text), then insert the correct option at its assigned position.
No seed search or model outputs are inputs. Fifty items yield counts 13/13/12/12.
The authored question is preserved verbatim. The system prompt is unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from benchmarks.agentic.prompt import SYSTEM_PROMPT

ALGORITHM = "structured-v1"
LETTERS = "ABCD"
FORMAT_INSTRUCTION = (
    'Return only a valid JSON object such as {"answer":"A","citations":[]}. '
    'The required "answer" must be one letter: A, B, C, or D. '
    'The optional "citations" must be a list of URL strings; citations are ignored '
    'for factual grading. Do not include other members, Markdown fences, or explanation.'
)
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class StructuredError(ValueError):
    """Invalid local input; messages deliberately exclude source contents."""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredError("duplicate object member")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise StructuredError("non-JSON numeric constant")


def _json(content: str | bytes) -> Any:
    try:
        return json.loads(content, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise StructuredError("invalid JSON or duplicate member") from None


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StructuredError("expected an object")
    return value


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise StructuredError(f"cannot read required file: {path.name}") from None


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        return _pairs(self.construct_pairs(node, deep=deep))


def _yaml(content: bytes) -> dict[str, Any]:
    try:
        return _mapping(yaml.load(content, Loader=_UniqueLoader))
    except (yaml.YAMLError, UnicodeError, TypeError, RecursionError):
        raise StructuredError("invalid corpus YAML") from None


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise StructuredError("invalid coordinate ID")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StructuredError("expected nonempty text")
    return value


def _indexed(items: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list) or len(items) != 50:
        raise StructuredError("corpus must contain exactly 50 unique IDs")
    indexed = {}
    for item in items:
        item = _mapping(item)
        ident = _id(item.get("id"))
        if ident in indexed:
            raise StructuredError("duplicate corpus ID")
        indexed[ident] = item
    return indexed


def _order(ident: str) -> tuple[str, str]:
    return _digest(f"{ALGORITHM}/id/{ident}".encode()), ident


def _prompt(question: str, options: list[str]) -> str:
    return question + "\n\n" + "\n".join(
        f"{letter}. {option}" for letter, option in zip(LETTERS, options, strict=True)
    ) + "\n\n" + FORMAT_INSTRUCTION


def render_corpus(source: bytes, items: bytes) -> bytes:
    """Validate independent authored items and produce deterministic runner YAML."""
    metadata = _yaml(source)
    original = _indexed(metadata.get("questions"))
    authored = _indexed(_json(items))
    if original.keys() != authored.keys():
        raise StructuredError("authored IDs must exactly match the source corpus")
    questions = []
    for index, ident in enumerate(sorted(authored, key=_order)):
        item = authored[ident]
        if set(item) != {"id", "question", "correct", "distractors"}:
            raise StructuredError("authored item requires id, question, correct and distractors")
        question, correct = _text(item["question"]), _text(item["correct"])
        distractors = item["distractors"]
        if not isinstance(distractors, list) or len(distractors) != 3:
            raise StructuredError("each item requires three distractors")
        choices = [correct, *map(_text, distractors)]
        if len({choice.strip() for choice in choices}) != 4:
            raise StructuredError("each item requires four distinct options")
        options = sorted(distractors, key=lambda text: (
            _digest(f"{ALGORITHM}/option/{ident}/{text}".encode()), text,
        ))
        options.insert(index % 4, correct)
        questions.append({
            **original[ident], "question": question, "options": options,
            "prompt": _prompt(question, options), "answer_key": LETTERS[index % 4],
        })
    metadata["questions"] = questions
    metadata["structured"] = {
        "algorithm": ALGORITHM, "source_sha256": _digest(source),
        "items_sha256": _digest(items),
        "system_prompt_sha256": _digest(SYSTEM_PROMPT.encode()),
    }
    return yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).encode()


def _freeze(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if _read(path) != content:
            raise StructuredError("refusing to change an existing frozen output") from None


def parse_answer(text: Any) -> str | None:
    """Strict unfenced JSON only; citations are validated but never graded."""
    if not isinstance(text, str):
        return None
    try:
        obj = _mapping(_json(text))
        if set(obj) - {"answer", "citations"} or obj.get("answer") not in tuple(LETTERS):
            return None
        citations = obj.get("citations", [])
        if not isinstance(citations, list):
            return None
        for url in citations:
            if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
                return None
            parts = urlsplit(url)
            if parts.scheme not in {"https", "http"} or not parts.hostname:
                return None
            _ = parts.port  # Invalid ports are malformed URLs too.
        return obj["answer"]
    except (ValueError, TypeError, RecursionError):
        return None


def _coordinate(row: dict[str, Any]) -> tuple[str, str]:
    system, model = _id(row.get("system_id")), _id(row.get("model_family_id"))
    repeat = row.get("repeat")
    if type(repeat) is not int or repeat < 1:
        raise StructuredError("repeat must be a positive integer")
    cell = f"{system}--{model}--r{repeat}"
    if row.get("competitor_id") != cell or row.get("cell", cell) != cell:
        raise StructuredError("inconsistent cell coordinates")
    return cell, _id(row.get("corpus_id"))


def _number(value: Any) -> int | float | None:
    if type(value) is int and value >= 0:
        return value
    if type(value) is float and value >= 0 and math.isfinite(value):
        return value
    return None


def score_run(run: Path, corpus: Path) -> dict[str, Any]:
    """Score retained agentic artifacts against an explicit, frozen corpus reference.

    The plan defines the denominator, including interrupted/unstarted trajectories.
    Discarded attempts, intermediate messages and scoring/judge files are not read.
    Only top-level ``answer`` supplies factual evidence. Numeric metrics are allowlisted.
    """
    frozen = _read(corpus)
    if _read(run / "snapshots/corpus.yml") != frozen:
        raise StructuredError("run corpus snapshot is not bound to the frozen corpus")
    data = _yaml(frozen)
    spec = _mapping(data.get("structured"))
    system_hash = _digest(SYSTEM_PROMPT.encode())
    if spec.get("algorithm") != ALGORITHM or spec.get("system_prompt_sha256") != system_hash:
        raise StructuredError("unsupported corpus algorithm or changed system prompt")
    if any(not isinstance(spec.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", spec[name])
           for name in ("source_sha256", "items_sha256")):
        raise StructuredError("missing corpus source bindings")
    if _read(run / "snapshots/system-prompt.txt") != SYSTEM_PROMPT.encode():
        raise StructuredError("run system prompt differs from the preserved prompt")
    questions = _indexed(data.get("questions"))
    for index, ident in enumerate(sorted(questions, key=_order)):
        q = questions[ident]
        options = q.get("options")
        if (not isinstance(options, list) or len(options) != 4
                or len({_text(option).strip() for option in options}) != 4
                or q.get("answer_key") != LETTERS[index % 4]):
            raise StructuredError("invalid frozen options or answer key")
        if q.get("prompt") != _prompt(_text(q.get("question")), options):
            raise StructuredError("frozen question prompt differs from its options")
    planned = _mapping(_json(_read(run / "planned-cells.json"))).get("cells")
    if not isinstance(planned, list) or not planned:
        raise StructuredError("missing nonempty planned cells")
    plan: dict[tuple[str, str], dict[str, Any]] = {}
    cell_identities: dict[str, tuple[str, str, int]] = {}
    for entry in planned:
        entry = _mapping(entry)
        coordinate = _coordinate(entry)
        if coordinate in plan or coordinate[1] not in questions:
            raise StructuredError("duplicate or unknown planned coordinate")
        identity = entry["system_id"], entry["model_family_id"], entry["repeat"]
        if cell_identities.setdefault(coordinate[0], identity) != identity:
            raise StructuredError("ambiguous planned cell ID")
        plan[coordinate] = entry
    expected = {Path(cell) / f"{qid}.json" for cell, qid in plan}
    for folder in ("transcripts", "tokens", "latency"):
        found = {path.relative_to(run / folder) for path in (run / folder).rglob("*.json")}
        if found - expected:
            raise StructuredError("unplanned or duplicate artifact coordinates")
    rows = []
    model_identities: dict[str, tuple[str, str]] = {}
    for (cell, qid), entry in sorted(plan.items()):
        path = run / "transcripts" / cell / f"{qid}.json"
        record = _mapping(_json(_read(path))) if path.exists() else None
        if record is not None:
            if (_coordinate(record) != (cell, qid)
                    or any(record.get(k) != entry[k] for k in (
                        "system_id", "model_family_id", "repeat"))):
                raise StructuredError("transcript coordinates differ from plan")
            if record.get("system_prompt_sha256") != system_hash:
                raise StructuredError("transcript system prompt is unbound")
            if "provider" in record or "model_id" in record:
                model_identity = _text(record.get("provider")), _text(record.get("model_id"))
                if model_identities.setdefault(entry["model_family_id"], model_identity) != (
                    model_identity
                ):
                    raise StructuredError("inconsistent model identity within a family")
        answer = parse_answer(record.get("answer")) if record else None
        success = record is not None and record.get("status") == "succeeded"
        incomplete = record is None or not isinstance(record.get("answer"), str) or not record[
            "answer"
        ].strip()
        row = {
            "question_id": qid, "model": entry["model_family_id"],
            "system": entry["system_id"], "repeat": entry["repeat"],
            "success": success, "validity": answer is not None,
            "correctness": int(success and answer == questions[qid]["answer_key"]),
            "answer_letter": answer, "keyed_letter": questions[qid]["answer_key"],
            "incomplete": incomplete,
        }
        if record:
            timing = record.get("timing")
            if isinstance(timing, dict):
                for name in ("wall_ms", "used_ms", "overhead_ms", "grant_wait_ms",
                             "failed_attempt_ms", "retry_wait_ms"):
                    row[name] = _number(timing.get(name))
            calls = record.get("model_calls")
            row["rounds"] = len(calls) if isinstance(calls, list) else None
        for folder, names in (
            ("tokens", ("client_wrapped_tokens", "raw_payload_tokens")),
            ("latency", ("latency_ms",)),
        ):
            sidecar = run / folder / cell / f"{qid}.json"
            if sidecar.exists():
                metrics = _mapping(_json(_read(sidecar)))
                if metrics.get("competitor_id") != cell or metrics.get("corpus_id") != qid:
                    raise StructuredError("metric coordinates differ from plan")
                for name in names:
                    row[name] = _number(metrics.get(name))
        rows.append(row)
    correct = sum(row["correctness"] for row in rows)
    return {
        "algorithm": ALGORITHM, "corpus_sha256": _digest(frozen),
        "planned": len(rows), "incomplete": sum(row["incomplete"] for row in rows),
        "successful": sum(row["success"] for row in rows),
        "valid": sum(row["validity"] for row in rows),
        "correct": correct, "accuracy": correct / len(rows), "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="validate and freeze runner corpus YAML")
    render.add_argument("--items", type=Path, required=True)
    render.add_argument("--source", type=Path, default=Path("docs/benchmarks/corpus.yml"))
    render.add_argument("--out", type=Path, required=True)
    score = commands.add_parser("score", help="write publishable local scores")
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--corpus", type=Path, required=True, help="frozen pre-run corpus")
    score.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            content = render_corpus(_read(args.source), _read(args.items))
            _freeze(args.out, content)
            print(f"corpus_sha256: {_digest(content)}")
        else:
            report = score_run(args.run, args.corpus)
            _freeze(args.out, (json.dumps(report, indent=2, allow_nan=False) + "\n").encode())
            print(f"correct: {report['correct']}/{report['planned']}; "
                  f"incomplete: {report['incomplete']}")
    except (StructuredError, OSError) as error:
        # StructuredError is safe to print; OS errors can include arbitrary paths.
        parser.error(str(error) if isinstance(error, StructuredError) else "cannot write output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
