"""Read-only joins of exported measurement records; no local runner state needed."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmarks.adapters.claude_tokens import DEFAULT_COUNT_MODEL
from benchmarks.agentic.count import (
    CountItem,
    _anthropic_method,
    _raw_arguments,
    count_item_from_transcript,
)
from benchmarks.agentic.count_evidence import is_count_coordinate, validate_count_fields
from benchmarks.agentic.inputs import json_mapping
from benchmarks.agentic.protocol import KeyLeakError, scanned_json_bytes
from benchmarks.grading.aggregate import AnswerVotes
from benchmarks.grading.batch import BatchItemResult
from benchmarks.grading.execution_evidence import grading_execution_matches
from benchmarks.grading.inputs import GradingQuestion, JudgeInput
from benchmarks.report_files import artifact_bytes
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.grading.vote_capture import VoteCapture


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,240}", value):
        raise ValueError("invalid measurement coordinate")
    return value


def _coordinate(value: Any) -> str:
    if not is_count_coordinate(value):
        raise ValueError("invalid measurement coordinate")
    return value


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return scanned_json_bytes(value, where="measurement evidence", indent=2, sort_keys=True) + b"\n"


def grading_receipt_matches(
    root: Path,
    vote: dict[str, Any],
    digest: str,
    judge: dict[str, Any],
    invocation: dict[str, Any],
    events: list[dict[str, Any]],
    results: dict[str, BatchItemResult] | None = None,
    history: dict[str, Any] | None = None,
) -> bool:
    """Join a receipt; a supplied history has already passed its manifest hash check.

    Retain its normalized result only on admission. Standalone callers capture
    and check their own receipt bytes here.
    """
    run = _identifier(vote["grading_run_id"])
    judge_id, item = _identifier(vote["judge_id"]), _identifier(vote["item_id"])
    attempt = vote["attempt"]
    if type(attempt) is not int or attempt < 1:
        return False
    relative = f"{judge_id}/{item}--a{attempt}.json"
    path = root / "grading/executions" / run / relative
    receipts = invocation["execution_results"]
    if history is None:
        body = artifact_bytes(root, path)
        if receipts.get(relative) != _digest(body):
            return False
        try:
            history = json_mapping(body, "grading execution history")
        except KeyLeakError:
            raise
        except BenchmarkValidationError:
            return False
    if (
        relative not in receipts
        or type(invocation["records_written"]) is not int
        or len(receipts) != invocation["records_written"]
    ):
        return False
    try:
        return grading_execution_matches(vote, digest, judge, invocation, events, history, results)
    except BenchmarkValidationError:
        return False


class CountCapture:
    """Capture count evidence once per scoring/publication operation.

    Every first read uses the confined, bounded, sensitive-data-scanning reader.
    Validating a shared invocation is linear in its items even when every item
    is subsequently joined by a claim. Mutable files are recaptured next render.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._bodies: dict[str, bytes] = {}
        self._mappings: dict[str, dict[str, Any]] = {}
        self._items: dict[
            tuple[str, str], tuple[dict[str, Any], CountItem, dict[str, Any]]
        ] = {}
        self._invocations: dict[str, dict[str, bytes] | None] = {}

    def _bytes(self, relative: str) -> bytes:
        if relative not in self._bodies:
            self._bodies[relative] = artifact_bytes(self.root, self.root / relative)
        return self._bodies[relative]

    def _mapping(self, relative: str) -> dict[str, Any]:
        if relative not in self._mappings:
            body = self._bytes(relative)
            try:
                self._mappings[relative] = json_mapping(body, "count evidence")
            except KeyLeakError:
                raise
            except BenchmarkValidationError as exc:
                raise ValueError("malformed count evidence") from exc
        return self._mappings[relative]

    def _item(self, cell: str, qid: str) -> tuple[dict[str, Any], CountItem, dict[str, Any]]:
        key = cell, qid
        if key in self._items:
            return self._items[key]
        relative = f"transcripts/{cell}/{qid}.json"
        transcript = self._mapping(relative)
        if (transcript["competitor_id"], transcript["corpus_id"]) != key:
            raise ValueError("count transcript identity differs")
        family, system = (
            _coordinate(transcript["model_family_id"]),
            _coordinate(transcript["system_id"]),
        )
        prompt = self._bytes("snapshots/system-prompt.txt").decode("utf-8")
        system_path = f"systems/{family}--{system}.json"
        info = self._mapping(system_path)
        try:
            item = count_item_from_transcript(
                transcript, system_prompt=prompt, tools_raw=info["tools_raw"]
            )
        except BenchmarkValidationError as exc:
            raise ValueError("count inputs differ") from exc
        for position in item.calls:
            if (
                type(position) is not int
                or not 0 < position < len(item.messages)
                or item.messages[position]["role"] != "assistant"
                or item.messages[position - 1]["role"] != "user"
            ):
                raise ValueError("invalid counted position")
        if item.calls != sorted(set(item.calls)):
            raise ValueError("invalid counted positions")
        for call, position in zip(
            (c for c in transcript["model_calls"] if c.get("tool_calls_requested") is not None),
            item.calls,
            strict=True,
        ):
            uses = sum(b.get("type") == "tool_use" for b in item.messages[position]["content"])
            raw = _raw_arguments(transcript["provider"], call.get("raw_response"))
            if (
                type(call["tool_calls_requested"]) is not int
                or uses != call["tool_calls_requested"]
                or len(raw) != uses
            ):
                raise ValueError("count tool calls differ")
        frozen = {
            "item": asdict(item),
            "transcript_sha256": _digest(self._bytes(relative)),
            "system_sha256": _digest(self._bytes(system_path)),
        }
        inputs = self._mapping("grading/count-inputs.json")
        if inputs.get(_digest(f"{cell}/{qid}".encode())) != frozen:
            raise ValueError("frozen count inputs differ")
        self._items[key] = transcript, item, frozen
        return self._items[key]

    def _read_invocation(self, run: str) -> dict[str, bytes]:
        receipt = self._mapping(f"grading/count-runs/{run}.json")
        results = receipt.get("results")
        if not (
            isinstance(results, dict)
            and receipt.get("count_run_id") == run
            and receipt.get("dry_run") is False
            and receipt.get("status") == "completed"
            and receipt.get("not_run") == []
            and receipt.get("worker_errors") == []
            and receipt.get("units") == []
            and type(receipt.get("records_written")) is int
            and receipt["records_written"] == len(results)
            and type(receipt.get("items")) is int
            and receipt["items"] == receipt["records_written"]
            and type(receipt.get("count_calls")) is int
        ):
            raise ValueError("incomplete count invocation")
        actual = 0
        archived = {}
        for relative_result, digest in results.items():
            parts = relative_result.split("/")
            if len(parts) != 2 or not parts[1].endswith(".json"):
                raise ValueError("invalid count result coordinate")
            rcell, rqid = _coordinate(parts[0]), _coordinate(parts[1][:-5])
            path = f"grading/counts/{run}/{relative_result}"
            result_body = self._bytes(path)
            row = self._mapping(path)
            _, counted_item, assigned = self._item(rcell, rqid)
            try:
                validate_count_fields(
                    row, counted_item, _anthropic_method(DEFAULT_COUNT_MODEL).record_fields()
                )
            except BenchmarkValidationError as exc:
                raise ValueError("invalid archived count result") from exc
            if (
                row.get("status") != "counted"
                or row.get("count_run_id") != run
                or row.get("input_sha256") != _digest(_json_bytes(assigned))
                or (counted_item.cell, counted_item.corpus_id) != (rcell, rqid)
                or digest != _digest(result_body)
            ):
                raise ValueError("archived count binding differs")
            actual += row["count_calls"]
            archived[relative_result] = result_body
        if receipt["count_calls"] != actual:
            raise ValueError("invocation count calls differ from actual item totals")
        return archived

    def matches(self, transcript: dict[str, Any], token: dict[str, Any]) -> bool:
        """Bind a projection to a complete invocation and all its current inputs."""
        try:
            cell = _coordinate(transcript["competitor_id"])
            qid = _coordinate(transcript["corpus_id"])
            _coordinate(transcript["model_family_id"])
            _coordinate(transcript["system_id"])
            if self._item(cell, qid)[0] != transcript:
                return False
            run = _identifier(token["count_run_id"])
            if run not in self._invocations:
                try:
                    self._invocations[run] = self._read_invocation(run)
                except BenchmarkValidationError:
                    raise  # Includes sensitive-data and unsafe-file failures.
                except (
                    FileNotFoundError, ValueError, KeyError, TypeError, AttributeError, IndexError,
                ):
                    self._invocations[run] = None
            archived = self._invocations[run]
            relative = f"{cell}/{qid}.json"
            return bool(
                archived is not None
                and relative in archived
                and archived[relative] == self._bytes(f"tokens/{relative}")
                and self._mapping(f"tokens/{relative}") == token
            )
        except KeyLeakError:
            raise
        except BenchmarkValidationError:
            raise  # Unsafe artifact reads are errors, not missing measurements.
        except (FileNotFoundError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            return False

    def admitted_counts(
        self,
        transcripts: Mapping[tuple[str, str], dict[str, Any]],
        tokens: Mapping[tuple[str, str], dict[str, Any]],
        *,
        scripted_models: Sequence[str] = (),
    ) -> dict[tuple[str, str], int | None]:
        """Capture C2 counts before scoring or publication can write anything.

        Hosted totals require the actual invocation and current input/byte joins.
        Explicitly scripted models may retain historical technical estimates;
        a claimed hosted counter still needs its receipt. C3 needs no counts.
        """
        models = {
            t["model_family_id"] for t in transcripts.values()
            if t.get("system_id") == "docs-fetch" and isinstance(t.get("model_family_id"), str)
        }
        admitted = {}
        for key, transcript in transcripts.items():
            model = transcript.get("model_family_id")
            if (not isinstance(model, str) or model not in models
                    or transcript.get("system_id") not in ("python-docs", "docs-fetch")):
                continue
            token = tokens.get(key, {})
            total = token.get("client_wrapped_tokens")
            execution = token.get("execution")
            answering = transcript.get("execution")
            technical = model in scripted_models and (
                "execution" not in transcript
                or isinstance(answering, dict) and answering.get("api") == "scripted"
            ) and (
                "execution" not in token
                or isinstance(execution, dict) and execution.get("api") == "scripted"
            )
            valid = token.get("status") == "counted" and type(total) is int and total >= 0
            if technical:
                valid = valid and all(
                    token.get(field) == transcript.get(field)
                    for field in ("competitor_id", "corpus_id", "tool_model_key")
                )
            else:
                # Even an invalid projection cannot hide unsafe retained source bytes.
                valid = self.matches(transcript, token) and valid
            admitted[key] = total if valid else None
        return admitted


def live_count_matches(root: Path, transcript: dict[str, Any], token: dict[str, Any]) -> bool:
    """A standalone join captures fresh files on every call."""
    return CountCapture(root).matches(transcript, token)


@dataclass
class GradeCapture:
    """Evidence admitted by the grade join, reused by publication without I/O.

    These unsigned exports establish internal continuity, not provider authenticity.
    A pending answer may contribute verified independent votes to G2, but only
    a reconciled final (or automatic zero) contributes a cell score.
    """

    requests: dict[tuple[str, str], JudgeInput] = field(default_factory=dict)
    questions: dict[str, GradingQuestion] = field(default_factory=dict)
    answers: dict[tuple[str, str], AnswerVotes] = field(default_factory=dict)
    finals: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    scores: dict[tuple[str, str], float] = field(default_factory=dict)
    graded_answers: int | None = None
    runs: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    inventory: VoteCapture | None = None
    reconciled: bool = False  # the whole join succeeded, including any genuinely pending cells


def evidence_cell(
    record: Mapping[str, object], question: str | None = None
) -> tuple[str, str, str, int]:
    """Read an answering coordinate without coercing malformed exported identities."""
    system, model = record["system_id"], record["model_family_id"]
    corpus, repeat = record["corpus_id"] if question is None else question, record["repeat"]
    if (not isinstance(system, str) or not isinstance(model, str)
            or not isinstance(corpus, str) or type(repeat) is not int):
        raise BenchmarkValidationError("invalid answering cell coordinate")
    return system, model, corpus, repeat


def comparison_identities_match(
    plan: Sequence[Mapping[str, Any]],
    transcripts: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    model: str,
    systems: Sequence[str],
    excluded: Sequence[str] = (),
) -> bool:
    """Match only a comparison's retained arms, before projecting away identity.

    Transcript keys come from the captured directory and filename. A declared
    cell must use the runner's canonical system/family/repeat spelling and the
    existing safe coordinate grammar. No filesystem reads or inventory-wide
    admission: unrelated arms and excluded questions cannot veto this join.
    """
    def needed(row: Mapping[str, Any], question: str | None = None) -> bool:
        return (row.get("model_family_id") == model and row.get("system_id") in systems
                and row.get("corpus_id") not in excluded and question not in excluded)

    def identity(row: Mapping[str, Any], *, planned: bool) -> tuple[str, str]:
        system, family, question, repeat = evidence_cell(row)
        for value in (system, family, question):
            _coordinate(value)
        cell = _coordinate(row["competitor_id"])
        if (repeat < 1 or cell != f"{system}--{family}--r{repeat}"
                or (planned or "cell" in row) and row.get("cell") != cell):
            raise ValueError("comparison cell identity differs")
        return cell, question

    try:
        want = [identity(row, planned=True) for row in plan if needed(row)]
        have = []
        for key, row in transcripts.items():
            if needed(row, key[1]):
                if identity(row, planned=False) != key:
                    return False
                have.append(key)
        return (len(want) == len(set(want)) and len(have) == len(set(have))
                and set(want) == set(have))
    except (BenchmarkValidationError, KeyError, TypeError, ValueError):
        return False


def comparison_plan(
    plan: Any,
    transcripts: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    model: str,
    systems: Sequence[str],
    excluded: Sequence[str] = (),
) -> set[tuple[str, str, str, int]] | None:
    """Admit identities before reducing a comparison to statistical coordinates."""
    if (not isinstance(plan, list) or not all(isinstance(row, dict) for row in plan)
            or not comparison_identities_match(
                plan, transcripts, model=model, systems=systems, excluded=excluded,
            )):
        return None
    return {
        evidence_cell(row) for row in plan
        if row.get("model_family_id") == model and row.get("system_id") in systems
        and row.get("corpus_id") not in excluded
    }
