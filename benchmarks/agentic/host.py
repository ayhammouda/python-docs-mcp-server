"""The host orchestrator: launchers, worker supervision and ledger arbitration (design §7.3).

The host never holds a provider key. It spawns one worker per (model, system),
answers its ledger requests through the one LedgerService, scans every frame
and every log for key-shaped strings, and books a lost worker's unit at twice
its bound.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import traceback
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Literal, Protocol

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import SystemSpec, system_spec_from_raw
from benchmarks.agentic.cleanup import add_cleanup_cause, add_safe_cleanup_cause, finish
from benchmarks.agentic.ledger import BatchItemSettle, LedgerService, PlannedItem
from benchmarks.agentic.loop import Caps
from benchmarks.agentic.protocol import (
    KEY_SHAPED,
    MAX_FRAME_BYTES,
    MAX_RERUNS,
    WORKER_FRAME_LIMIT_EXIT,
    WORKER_LEAK_REASON,
    FrameTooLarge,
    KeyLeakError,
    encode_frame,
    read_frame,
    redact_key_shaped,
    redact_key_shaped_text,
)
from benchmarks.agentic.protocol import (
    RESULT_CHUNK_BYTES as RESULT_CHUNK_BYTES,
)
from benchmarks.agentic.types import Buckets
from benchmarks.agentic.usage import anthropic_buckets, gemini_buckets, openai_buckets
from benchmarks.grading.batch import (
    _OPENAI_TERMINAL,
    BatchItemResult,
    ScriptedBatchClient,
    _snake,
    anthropic_result,
    compact_unbilled_evidence,
    drop_batch_raw,
    gemini_result,
    openai_result,
)
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError

#: What a local worker may see of the host's environment. Never a key, never the live flag.
_LOCAL_ENV = ("PATH", "HOME", "LANG", "TMPDIR", "XDG_CACHE_HOME", "VIRTUAL_ENV")
#: How long teardown waits for a killed worker to exit before it scrubs and settles anyway.
_EXIT_WAIT_S = 30.0
#: Scan a whole snapshot so a token cannot straddle scan boundaries. Fail closed above this size.
_MAX_LOG_BYTES = 8 * 1024 * 1024


GRADING_SYSTEM = system_spec_from_raw({"id": "grading", "adapter": "none"})
GRADING_CAPS = Caps(trajectory_s=600.0, model_call_timeout_s=300.0)
WHOLE_BATCH_FAILURE_LIMIT = 2
# A private result file lives for at most one unit; refuse excess evidence before settlement.
_MAX_STAGED_BYTES = 512 * 1024 * 1024
_BATCH_STATES = {
    "anthropic": {"ended"},
    "openai": _OPENAI_TERMINAL,
    "google": {
        "JOB_STATE_SUCCEEDED",
        "JOB_STATE_PARTIALLY_SUCCEEDED",
        "JOB_STATE_FAILED",
        "JOB_STATE_CANCELLED",
        "JOB_STATE_EXPIRED",
    },
}


class ItemSource:
    """One invocation's ordered queue. Only the host assigns items and attempt numbers.

    A batch wire item is the fields of its BatchRequest. A pull wire item holds
    the free task's input. The host adds item_id and attempt to either at grant time.
    """

    def __init__(
        self, items: Sequence[tuple[PlannedItem, dict[str, Any]]], *, max_attempts: int = 3
    ):
        self._items = {p.item_id: (p, wire) for p, wire in items}
        if len(items) != len(self._items) or max_attempts < 1:
            raise BenchmarkValidationError("invalid batch item queue")
        self.max_attempts = max_attempts
        self._pending = list(self._items)
        self._taken: dict[str | None, list[str]] = {}
        self._attempts = dict.fromkeys(self._items, 0)
        self._paid: set[str] = set()
        self._not_run: list[dict[str, Any]] = []

    def pending(self) -> list[PlannedItem]:
        return [self._items[i][0] for i in self._pending]

    def take(self, unit_id: str | None, n: int) -> list[dict[str, Any]]:
        if self._taken.get(unit_id):
            raise BenchmarkValidationError("worker still holds items")
        ids = self._pending[:n]
        del self._pending[: len(ids)]
        self._taken[unit_id] = ids
        for i in ids:
            self._attempts[i] += 1
        return [{**self._items[i][1], "item_id": i, "attempt": self.attempts(i)} for i in ids]

    def held(self, unit_id: str | None) -> list[str]:
        return self._taken.get(unit_id, [])

    def attempts(self, item_id: str) -> int:
        return self._attempts[item_id]

    def exhausted(self, unit_id: str) -> list[str]:
        return [i for i in self.held(unit_id) if self.attempts(i) >= self.max_attempts]

    def note_paid_no_result(self, unit_id: str, item_ids: Sequence[str] | None = None) -> None:
        self._paid.update(self.held(unit_id) if item_ids is None else item_ids)

    def paid_no_result(self, item_id: str) -> bool:
        return item_id in self._paid

    def _record(self, item_id: str, reason: str) -> None:
        self._not_run.append(
            {"item_id": item_id, "reason": reason, "attempt": self.attempts(item_id)}
        )

    def give_up(self, unit_id: str, item_ids: Sequence[str], reason: str) -> None:
        for i in item_ids:
            self._record(i, reason)
        excluded = set(item_ids)
        self._taken[unit_id] = [i for i in self.held(unit_id) if i not in excluded]

    def release(self, unit_id: str, item_ids: Sequence[str] | None = None) -> None:
        held = self.held(unit_id)
        released = set(held if item_ids is None else item_ids)
        retry = []
        for i in held:
            if i in released:
                if self.attempts(i) < self.max_attempts:
                    retry.append(i)
                else:
                    self._record(i, "retries_exhausted")
        self._pending[:0] = retry
        self._taken[unit_id] = [i for i in held if i not in released]

    def complete(self, unit_id: str | None) -> None:
        self._taken.pop(unit_id, None)

    def lose(self, unit_id: str | None, reason: str) -> None:
        for i in [*self.held(unit_id), *self._pending]:
            self._record(i, reason)
        self.complete(unit_id)
        self._pending.clear()

    def not_run(self) -> list[dict[str, Any]]:
        return list(self._not_run)


@dataclass(frozen=True)
class WorkerJob:
    job_id: str  # f"{family.id}--{system.id}"
    family: ModelFamily  # family.raw["script"] is absolute for scripted families
    system: SystemSpec
    trajectories: tuple[dict[str, Any], ...]  # {"corpus_id", "prompt", "repeat"}, in run order
    caps: Caps

    task: Literal["trajectories", "batches", "items"] = "trajectories"
    payload: dict[str, Any] = field(default_factory=dict)
    source: ItemSource | None = field(default=None, compare=False, repr=False)

    def report_coordinates(self, message: dict[str, Any]) -> tuple[str, int]:
        """Validate a cell report and return its host-planned output coordinates."""
        if message["type"] != "not_run" or "attempt" in message:
            attempt = message.get("attempt")
            if type(attempt) is not int or not 1 <= attempt <= 1 + MAX_RERUNS:
                raise BenchmarkValidationError(f"worker {self.job_id} reported an invalid attempt")

        corpus_id, repeat = message.get("corpus_id"), message.get("repeat")
        # JSON booleans and floats can compare equal to an integer repeat.
        if isinstance(corpus_id, str) and type(repeat) is int:
            for item in self.trajectories:
                if (item["corpus_id"], item["repeat"]) == (corpus_id, repeat):
                    return item["corpus_id"], item["repeat"]
        raise BenchmarkValidationError(f"worker {self.job_id} reported an unassigned trajectory")


class Launcher(Protocol):
    def supports(self, family: ModelFamily) -> bool:
        """Whether this launcher can run the family's worker."""
        raise NotImplementedError

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        """The worker's argv and its whole environment."""
        raise NotImplementedError

    def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
        """The private first frame for the worker's stdin, or None. Called once, after prepare."""
        raise NotImplementedError

    async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
        """Before any worker starts; raise to stop the run with nothing spent."""
        raise NotImplementedError

    async def stop_worker(self, job: WorkerJob) -> None:
        """After the group kill and before the job's in-flight unit is settled."""
        raise NotImplementedError

    async def close(self) -> list[str]:
        """After every worker is gone: release all; return problems, raise only KeyLeakError."""
        raise NotImplementedError


class LocalLauncher:
    """A local process with no provider key: scripted models only (PR 4 adds containers)."""

    def supports(self, family: ModelFamily) -> bool:
        return family.api == "scripted"

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        env = {name: os.environ[name] for name in _LOCAL_ENV if name in os.environ}
        return [sys.executable, "-m", "benchmarks", "agentic-worker"], env

    def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
        return None

    async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
        return None

    async def stop_worker(self, job: WorkerJob) -> None:
        return None

    async def close(self) -> list[str]:
        return []


def refuse_host_keys(environ: Mapping[str, str]) -> None:
    # Every key name, competitors included. Only agentic-run calls this: the fixed-flow
    # `run` command still counts tokens with ANTHROPIC_API_KEY on the host.
    held = sorted(name for name in set(PROVIDER_API_KEY_ENV.values()) if environ.get(name))
    if held:
        raise BenchmarkValidationError(
            f"the host must never hold a provider key, but {', '.join(held)} is set: "
            "keys go only to the worker of their own provider"
        )


def _start_anchor() -> subprocess.Popen[bytes]:
    """The leader of a worker's process group, in a group of its own in this session.

    Plain Popen, not asyncio: no child watcher ever reaps it, and nothing here waits on it
    before the final group kill. So even an anchor killed from outside stays a zombie, and
    its pid, which is the group id, cannot be reused while the host may still signal it. It
    reads its stdin, a pipe only the host holds, so it also ends with a host killed outright.
    """
    # The host's `cat`; run.sh checks for it, and the worker image has it too
    # (benchmarks/sandbox/Dockerfile).
    return subprocess.Popen(
        ["cat"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        process_group=0,  # a worker cannot join a group of another session
    )


def _kill_group(anchor: subprocess.Popen[bytes]) -> None:
    """Kill the anchor's whole group: the worker and every descendant still in it.

    The anchor is not reaped before this, so the group id is still this job's.
    """
    # Reaches only this host's process tree. A container launcher's `stop_worker` removes its
    # own container, which ends everything in it, before the unit is settled.
    # ESRCH, or EPERM (macOS, when the group holds only zombies, the anchor included).
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(anchor.pid, signal.SIGKILL)


async def _reap(anchor: subprocess.Popen[bytes]) -> None:
    """After the group kill only: close the anchor's stdin and reap it, within the bound."""
    if anchor.stdin is not None:
        anchor.stdin.close()
    with suppress(subprocess.TimeoutExpired):  # an anchor that never exits cannot hang the run
        await asyncio.to_thread(anchor.wait, _EXIT_WAIT_S)


def _diagnostic(message: str) -> None:
    # During terminal hangup even stderr can be closed.
    if sys.stderr is not None:
        with suppress(OSError, ValueError):
            print(redact_key_shaped_text(message), file=sys.stderr, flush=True)


def _stop_grants(ledger: LedgerService | None) -> None:
    if ledger is not None:  # free token counting has no spend ledger
        ledger.stop_grants()


async def _teardown(
    proc: asyncio.subprocess.Process | None,
    anchor: subprocess.Popen[bytes],
    log_path: Path,
    ledger: LedgerService | None,
    in_flight: str | None,
    worker_log: BinaryIO,
    stop_worker: Callable[[], Awaitable[None]] | None = None,
    on_leak: Callable[[KeyLeakError], None] | None = None,
) -> None:
    """Kill the worker, scrub its log and book any open unit before teardown returns."""

    errors: list[BaseException] = []
    location = f"worker teardown ({log_path.name})"

    @contextmanager
    def attempt() -> Iterator[None]:
        # Report each job's failures before gather drains it, and attempt every cleanup step.
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 - propagate after all cleanup steps
            if isinstance(exc, KeyLeakError):
                _stop_grants(ledger)
                if on_leak is not None:
                    on_leak(exc)
            errors.append(exc)

    with attempt():
        _kill_group(anchor)
        if proc is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), _EXIT_WAIT_S)
    with attempt():
        await _reap(anchor)
    if stop_worker is not None:
        with attempt():
            await stop_worker()
    with attempt():
        redacted, pattern = _scrubbed_snapshot(worker_log)
        if pattern:
            _stop_grants(ledger)
        detected = f"a key-shaped string ({pattern} pattern) was found in {log_path}"
        leak = KeyLeakError(detected) if pattern else None
        if leak is not None and on_leak is not None:
            on_leak(leak)
        try:
            _publish_worker_log(redacted, log_path)
        except Exception:
            if leak is not None:
                # Keep the leak primary and report the storage error after settlement.
                leak.args = (f"{detected}; scrubbed log publication failed",)
                errors.append(leak)
            raise
        if leak is not None:
            leak.args = (f"{detected}; redacted there",)
            raise leak
    with attempt():
        worker_log.close()
    if in_flight is not None:
        with attempt():
            assert ledger is not None
            await ledger.settle(in_flight, outcome="worker_lost", buckets=None)

    # Formatting failures must never interrupt mandatory scrubbing or accounting.
    for i, exc in enumerate(errors):
        try:
            detail = f"{location}: {type(exc).__name__}: {redact_key_shaped_text(str(exc))}"
            if KEY_SHAPED.search("".join(traceback.format_exception(exc, limit=0))):
                # An outer exception may be clean while its notes or chained daemon error leak.
                leak = KeyLeakError(detail + " (key shape redacted)")
                errors[i] = leak
                _stop_grants(ledger)
                if on_leak is not None:
                    on_leak(leak)
            else:
                exc.add_note(location)
            _diagnostic(detail)
        except Exception:  # noqa: BLE001 - an unscannable failure must not enter the traceback
            leak = KeyLeakError(f"{location}: could not safely report {type(exc).__name__}")
            errors[i] = leak
            _stop_grants(ledger)
            if on_leak is not None:
                on_leak(leak)
    if errors:
        for primary, secondary in zip(errors, errors[1:]):
            add_cleanup_cause(primary, secondary)
        raise errors[0]


def _scrubbed_snapshot(worker_log: BinaryIO) -> tuple[bytes, str | None]:
    """Late writers keep their anonymous inode and offset."""
    fd = worker_log.fileno()
    size = os.fstat(fd).st_size
    if size > _MAX_LOG_BYTES:
        raise BenchmarkValidationError(f"worker log exceeds the {_MAX_LOG_BYTES}-byte scan limit")
    data = os.pread(fd, size, 0)
    if len(data) != size:
        raise OSError("incomplete worker log read")
    return redact_key_shaped(data)


def _publish_worker_log(redacted: bytes, log_path: Path) -> None:
    stage = tempfile.NamedTemporaryFile(dir=log_path.parent, suffix=".tmp", delete=False)
    try:
        with stage:
            if stage.write(redacted) != len(redacted):
                raise OSError("incomplete scrubbed log write")
        os.replace(stage.name, log_path)
    except BaseException:
        os.unlink(stage.name)
        raise


def _validate_token_counts(usage: dict[str, Any]) -> None:
    # The shared bucket functions accept int-coercible values. The worker wire does not.
    for key, value in usage.items():
        if key.endswith(("tokens", "token_count", "TokenCount")) and value is not None:
            if type(value) is not int or value < 0:
                raise BenchmarkValidationError("invalid provider token count")
        elif isinstance(value, dict):
            _validate_token_counts(value)


def _verified_buckets(row: dict[str, Any], provider: str) -> Buckets | None:
    usage, claimed = row["raw_usage"], row["buckets"]
    if usage is None and claimed is None:
        return None
    if (
        provider == "openai"
        and row["status"] != "succeeded"
        and isinstance(usage, dict)
        and claimed is None
        and any(usage.get(k) is None for k in ("input_tokens", "output_tokens"))
    ):
        _validate_token_counts(usage)
        return None  # Preserve the partial receipt; the raw-line match below is still mandatory.
    if (
        not isinstance(usage, dict)
        or not isinstance(claimed, dict)
        or any(type(v) is not int or v < 0 for v in claimed.values())
    ):
        raise BenchmarkValidationError("invalid batch result usage")
    _validate_token_counts(usage)
    if provider in ("anthropic", "openai") and any(
        usage.get(k) is None for k in ("input_tokens", "output_tokens")
    ):
        raise BenchmarkValidationError("missing provider token counts")
    if provider == "google":
        fields = _snake(usage)  # JSONL is camelCase; SDK/scripted usage can be snake_case.
        if len(fields) != len(usage):
            raise BenchmarkValidationError("ambiguous Gemini usage fields")
        expected = gemini_buckets(fields)
    elif provider == "anthropic":
        expected = anthropic_buckets(usage)
    else:
        if any(
            usage.get(k) is not None and not isinstance(usage[k], dict)
            for k in ("input_tokens_details", "output_tokens_details")
        ):
            raise BenchmarkValidationError("invalid OpenAI token details")
        expected = openai_buckets(usage)
        if (
            usage.get("total_tokens") is not None
            and usage["total_tokens"] != expected.total_input + expected.output
        ):
            raise BenchmarkValidationError("OpenAI total tokens disagree with reported usage")
        if expected.reasoning > expected.output:
            raise BenchmarkValidationError("reasoning exceeds reported output")
    if claimed != asdict(expected):
        raise BenchmarkValidationError("batch buckets disagree with retained provider usage")
    return expected


def _validate_evidence(row: dict[str, Any], family: ModelFamily) -> Buckets | None:
    """Check independently retained evidence against the worker's normalized result."""
    fields = BatchItemResult.__dataclass_fields__
    if not fields.keys() <= row.keys() or row.keys() - fields.keys() - {
        "attempt",
        "raw_dropped",
        "billing_evidence",
    }:
        raise BenchmarkValidationError("invalid batch result fields")
    status, raw = row["status"], row["raw"]
    if (
        status not in ("succeeded", "missing", "errored", "expired", "canceled")
        or any(type(row[k]) is not bool for k in ("retryable", "unbilled", "refusal"))
        or type(row.get("raw_dropped", False)) is not bool
        or (raw is not None and (not isinstance(raw, dict) or row.get("raw_dropped")))
        or any(
            row[k] is not None and not isinstance(row[k], str)
            for k in ("text", "reported_model", "stop_reason")
        )
    ):
        raise BenchmarkValidationError("invalid batch result fields")
    provider = family.provider
    if status != "succeeded":
        if row["refusal"] or any(
            row[k] is not None for k in ("text", "stop_reason", "reported_model")
        ):
            raise BenchmarkValidationError("vote fields on unsuccessful result")
        # A worker cannot price a failure from its own token counts. Only a retained
        # OpenAI item line can establish paid usage; it is normalized and matched below.
        if row["raw_usage"] is not None or row["buckets"] is not None:
            if (
                provider != "openai"
                or status not in ("errored", "canceled", "expired")
                or raw is None
                or "scripted_rule" in raw
                or row["unbilled"]
                or not row["retryable"]
            ):
                raise BenchmarkValidationError(
                    "unsuccessful result usage needs raw OpenAI evidence"
                )
            response = raw.get("response")
            if not isinstance(response, dict) or not isinstance(response.get("body"), dict):
                raise BenchmarkValidationError("missing OpenAI item receipt")
            code = response.get("status_code")
            if type(code) is not int or not (
                400 <= code < 600
                or (
                    code == 200
                    and response["body"].get("status")
                    in ("failed", "canceled", "cancelled", "incomplete", "queued", "in_progress")
                )
            ):
                raise BenchmarkValidationError("invalid OpenAI item receipt status")

    free = row["unbilled"]
    retry = row["retryable"]
    if "billing_evidence" in row:
        evidence = row["billing_evidence"]
        if (
            raw is not None
            or not row.get("raw_dropped")
            or not free
            or not retry
            or not isinstance(evidence, dict)
            or compact_unbilled_evidence(evidence) != evidence
        ):
            raise BenchmarkValidationError("invalid compact billing evidence")
        raw = evidence
    if not retry:
        # A synthetic result for failed input validation. The enclosing state is checked later.
        if provider != "openai" or status != "errored" or not free or raw is not None:
            raise BenchmarkValidationError("invalid failed OpenAI batch result")
    elif free and (status in ("missing", "succeeded") or provider == "google" or raw is None):
        raise BenchmarkValidationError("undocumented unbilled batch result")
    if status == "missing" and raw is not None:
        raise BenchmarkValidationError("unexpected evidence on missing result")

    try:
        buckets = _verified_buckets(row, provider)
        if raw is None:
            return buckets
        if family.api == "scripted" and "scripted_rule" in raw:
            rule = raw["scripted_rule"]
            if not isinstance(rule, dict) or (free and provider != "anthropic"):
                raise BenchmarkValidationError("invalid scripted batch evidence")
            # Reuse the scripted worker's transformation without compiling a worker-supplied
            # regex. The assignment has already selected this single rule.
            client = ScriptedBatchClient(
                provider, family.model_id, {"rules": [{**rule, "match": ".*"}]}
            )
            expected = client._result(row["item_id"])
        else:
            # Use the batch client's own transformations, including raw item identity,
            # model, stop/refusal and usage. Anthropic's unknown-kind fallback cannot
            # establish that the result is one of its documented free statuses.
            if provider == "anthropic" and raw["result"]["type"] != status:
                raise BenchmarkValidationError("invalid Anthropic result type")
            if (
                provider == "openai"
                and (free or status == "expired")
                and raw.get("response") is not None
            ):
                code = raw["response"]["status_code"]
                if type(code) is not int or not 400 <= code < 500:
                    raise BenchmarkValidationError("unbilled OpenAI row with conflicting response")
            normalize = {
                "anthropic": anthropic_result,
                "openai": openai_result,
                "google": gemini_result,
            }[provider]
            expected = normalize(raw)
        if expected.raw_usage is not None:
            _validate_token_counts(expected.raw_usage)
        if any(row[k] != getattr(expected, k) for k in fields if k not in ("buckets", "raw")):
            raise BenchmarkValidationError("batch row disagrees with provider result")
        return buckets
    except BenchmarkValidationError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise BenchmarkValidationError("malformed batch result evidence") from exc


class _GradingJob:
    """Validate each grading frame before handing evidence to the collector.

    Keep settlement metadata in memory and stream provisional rows to a private file.
    Only a durably settled unit can publish votes.
    """

    def __init__(
        self,
        job: WorkerJob,
        ledger: LedgerService | None,
        on_message: Callable[[WorkerJob, dict[str, Any]], Awaitable[None]],
    ) -> None:
        if job.source is None:
            raise BenchmarkValidationError("grading jobs need a host item source")
        self.job = job
        self.source = job.source
        self._ledger = ledger
        self.on_message = on_message
        self.unit: str | None = None
        self.batch_id: str | None = None
        self.failures = 0
        self.assigned: set[str] = set()
        self.results: dict[str, BatchItemSettle] = {}
        self.status: dict[str, tuple[str, bool]] = {}
        self.stage: BinaryIO | None = None
        self.published_offset = 0
        self.terminals: deque[dict[str, Any]] = deque()

    @property
    def ledger(self) -> LedgerService:
        if self._ledger is None:
            raise BenchmarkValidationError("free counting cannot use the inference ledger")
        return self._ledger

    async def _reserve(self) -> dict[str, Any]:
        if self.unit is not None:
            raise BenchmarkValidationError("worker reserved twice")
        if self.failures >= WHOLE_BATCH_FAILURE_LIMIT:
            self.source.lose(None, "whole_batch_failures")
            return {"type": "deny"}
        items = self.source.pending()
        if not items:
            return {"type": "no_more_items"}
        reservation = await self.ledger.reserve_batch(
            provider=self.job.family.provider,
            model_id=self.job.family.model_id,
            items=items,
            job_id=self.job.job_id,
        )
        if reservation is None:
            self.source.lose(None, "budget_stop")
            return {"type": "deny"}
        self.unit, n = reservation
        wire = self.source.take(self.unit, n)
        self.assigned = {r["item_id"] for r in wire}
        self.results.clear()
        self.status.clear()
        self.batch_id = None
        return {"type": "grant", "unit_id": self.unit, "items": wire}

    def _stage_result(self, unit: str, row: dict[str, Any]) -> None:
        # One row per callback gives the collector a single immutable vote to persist.
        message = {
            "type": "batch_results",
            "unit_id": unit,
            "provider_batch_id": self.batch_id,
            "results": [row],
        }
        try:
            frame = encode_frame(message, limit=RESULT_CHUNK_BYTES - 8)
        except FrameTooLarge:
            # The host adds the attempt, terminal flag and provider ID after validating raw.
            if row["raw"] is None:
                raise
            drop_batch_raw(row)
            frame = encode_frame(message, limit=RESULT_CHUNK_BYTES - 8)
        if self.stage is None:
            self.stage = tempfile.TemporaryFile()
            self.published_offset = 0
        if self.stage.tell() + len(frame) > _MAX_STAGED_BYTES:
            raise BenchmarkValidationError(f"batch result staging limit exceeded for unit {unit}")
        self.stage.write(frame)

    def _publications(self) -> Iterator[dict[str, Any]]:
        # Advance only after the callback returns. On interruption the next iterator starts
        # at the first unacknowledged notification, even if it was the last row in a file.
        while self.terminals:
            yield self.terminals[0]
            self.terminals.popleft()
        if self.stage is not None and self.unit is None:
            self.stage.seek(self.published_offset)
            while header := self.stage.read(8):
                size = int.from_bytes(header, "big")
                yield json.loads(self.stage.read(size))
                self.published_offset = self.stage.tell()
            stage, self.stage = self.stage, None
            stage.close()

    async def _publish(self, *, teardown: bool = False) -> None:
        failure = None
        for message in self._publications():
            try:
                await self.on_message(self.job, message)
            except Exception as exc:
                if not teardown:
                    raise
                # Persistent collector errors must surface, but still try the other items.
                item_id = (
                    message["results"][0]["item_id"] if "results" in message else message["item_id"]
                )
                exc.add_note(f"publishing item {item_id} of unit {message['unit_id']}")
                if failure is None:
                    failure = exc
                else:
                    _diagnostic(
                        f"collector also failed for item {item_id} of unit {message['unit_id']}"
                    )
        if failure is not None:
            raise failure

    async def _submit_failure(self, unit: str, message: dict[str, Any]) -> None:
        outcome = message.get("outcome")
        paid = outcome in ("connection", "timeout", "server")
        # Only the terminal-result read path can stop these items after a paid settle.
        # This flag does not change pricing or the normal submit/poll retry policy.
        unavailable = message.get("results_unavailable", False)
        if (
            not paid
            and outcome not in ("rejected", "rate_limited")
            or message.get("buckets") is not None
            or self.results
            or (self.batch_id is not None and not paid)
            or type(unavailable) is not bool
            or (unavailable and (self.batch_id is None or not paid))
        ):
            raise BenchmarkValidationError("invalid batch failure outcome")
        await self.ledger.settle(unit, outcome=outcome, buckets=None)
        self.unit = None
        if paid:
            self.source.note_paid_no_result(unit)
        self.failures = self.failures + 1 if paid else 0
        if unavailable:
            self.source.give_up(unit, self.source.held(unit), "batch_failed")
            self.source.complete(unit)
            return
        terminal, rejected = [], []
        for i in self.source.exhausted(unit):
            if self.source.paid_no_result(i):
                terminal.append(i)
            else:
                rejected.append(i)
        self.source.give_up(unit, rejected, "submit_rejected")
        self.source.give_up(unit, terminal, "retries_exhausted")
        self.source.release(unit)
        self.source.complete(unit)
        self.terminals.extend(
            {
                "type": "item_terminal",
                "item_id": i,
                "attempt": self.source.attempts(i),
                "reason": "retries_exhausted",
                "unit_id": unit,
            }
            for i in terminal
        )
        await self._publish()

    def _result(self, row: dict[str, Any]) -> BatchItemSettle:
        key = row.get("item_id")
        if not isinstance(key, str) or key not in self.assigned or key in self.results:
            raise BenchmarkValidationError("unassigned or duplicate batch result")
        attempt = self.source.attempts(key)
        if "attempt" in row and (type(row["attempt"]) is not int or row["attempt"] != attempt):
            raise BenchmarkValidationError("invalid batch attempt")
        buckets = _validate_evidence(row, self.job.family)
        status, retryable, free = row["status"], row["retryable"], row["unbilled"]
        row["attempt"] = attempt
        row["terminal"] = (
            status != "succeeded" and retryable and attempt >= self.source.max_attempts
        )
        self.status[key] = (status, retryable)
        return BatchItemSettle(
            key,
            buckets,
            free,
            reported_zero=(status != "succeeded" and buckets is not None and buckets.is_empty),
        )

    async def _settle(self, unit: str, message: dict[str, Any]) -> None:
        n = message.get("result_count")
        if (
            type(n) is not int
            or n != len(self.results)
            or self.results.keys() != self.assigned
            or not isinstance(message.get("batch_state"), str)
        ):
            raise BenchmarkValidationError("incomplete batch settlement")
        state = message["batch_state"]
        if state not in _BATCH_STATES[self.job.family.provider] or any(
            (state == "failed") != (not retry) for _, retry in self.status.values()
        ):
            raise BenchmarkValidationError("batch state and item outcomes disagree")
        assert self.stage is not None  # one staged row per assigned item
        self.stage.flush()
        await self.ledger.settle_batch(unit, results=list(self.results.values()))
        self.unit = None
        self.source.note_paid_no_result(
            unit,
            [
                i
                for i, (status, _) in self.status.items()
                if status != "succeeded"
                and not self.results[i].unbilled
                and not self.results[i].reported_zero
            ],
        )
        whole = all(status == "missing" for status, _ in self.status.values())
        self.failures = self.failures + 1 if whole else 0
        failed = [i for i, (_, retryable) in self.status.items() if not retryable]
        self.source.give_up(unit, failed, "batch_failed")
        self.source.release(
            unit, [i for i, (s, retry) in self.status.items() if s != "succeeded" and retry]
        )
        self.source.complete(unit)
        await self._publish()

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = message.get("type")
        if kind == "done":
            if self.unit or self.source.held(None) or self.source.pending():
                raise BenchmarkValidationError("worker finished with outstanding items")
            await self.on_message(self.job, {"type": "done"})
            return None
        if self.job.task == "items":
            if kind == "next_item":
                items = self.source.take(None, 1)
                return {"type": "item", "item": items[0]} if items else {"type": "no_more_items"}
            if kind == "counted":
                if self.source.held(None) != [message.get("item_id")]:
                    raise BenchmarkValidationError("unassigned counted item")
                await self.on_message(self.job, message)
                self.source.complete(None)
                return None
            if kind == "heartbeat":
                await self.on_message(self.job, message)
                return None
            raise BenchmarkValidationError("invalid pull worker frame")
        if kind == "reserve" and message.get("kind") == "batch":
            return await self._reserve()
        unit = self.unit
        if unit is None or message.get("unit_id") != unit:
            raise BenchmarkValidationError("batch worker does not hold that unit")
        if kind == "settle":
            await self._submit_failure(unit, message)
        elif kind == "submitted":
            batch_id = message.get("provider_batch_id")
            if self.batch_id is not None or not isinstance(batch_id, str) or not batch_id:
                raise BenchmarkValidationError("invalid submitted batch id")
            self.batch_id = batch_id
            await self.on_message(self.job, message)
            return None
        elif kind == "heartbeat":
            await self.on_message(self.job, message)
            return None
        elif kind == "batch_results" and self.batch_id:
            encode_frame(message, limit=RESULT_CHUNK_BYTES - 8)
            rows = message.get("results")
            if not isinstance(rows, list) or not rows:
                raise BenchmarkValidationError("empty batch results")
            for row in rows:
                if not isinstance(row, dict):
                    raise BenchmarkValidationError("invalid batch result")
                result = self._result(row)
                self.results[result.item_id] = result
                self._stage_result(unit, row)
        elif kind == "settle_batch":
            await self._settle(unit, message)
        else:
            raise BenchmarkValidationError("invalid batch worker frame")
        return {"type": "ack"}

    async def finish(self) -> None:
        self.source.lose(self.unit, "worker_lost")
        try:
            await self._publish(teardown=True)
        finally:
            try:
                if self.stage is not None:
                    self.stage.close()
            finally:
                for entry in self.source.not_run():
                    await self.on_message(self.job, {"type": "item_not_run", **entry})


async def _supervise(
    job: WorkerJob,
    *,
    launcher: Launcher,
    ledger: LedgerService | None,
    on_message: Callable[[WorkerJob, dict[str, Any]], Awaitable[None]],
    log_dir: Path,
    cwd: Path,
    max_frame_bytes: int,
    aborted: asyncio.Event,
    on_failure: Callable[[KeyLeakError | FrameTooLarge], None],
) -> None:
    grading = _GradingJob(job, ledger, on_message) if job.task != "trajectories" else None
    argv, env = launcher.command(job)
    log_path = log_dir / f"{job.job_id}.stderr.log"
    # On POSIX the TemporaryFile fallback unlinks before returning the descriptor.
    with tempfile.TemporaryDirectory() as private_dir:
        log = tempfile.TemporaryFile(dir=private_dir, buffering=0)
    try:
        anchor = _start_anchor()
    except BaseException:
        log.close()
        raise
    proc = None
    in_flight: str | None = None
    reported: set[tuple[str, int]] = set()
    done = False
    lost_reason = "worker_lost"
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
            process_group=anchor.pid,  # the anchor's group, which teardown kills whole
        )
        assert proc.stdin is not None and proc.stdout is not None
        silence_s = job.caps.trajectory_s + job.caps.model_call_timeout_s

        async def check_terminal_status() -> None:
            # EOF can precede process exit (including the redactor's final drain). Only
            # inspect this owned child, before teardown can replace its status with SIGKILL.
            with suppress(TimeoutError):
                status = await asyncio.wait_for(proc.wait(), min(silence_s, 5))
                if status == 3:
                    raise KeyLeakError(f"worker {job.job_id}: {WORKER_LEAK_REASON} (exit 3)")
                if status == WORKER_FRAME_LIMIT_EXIT:
                    raise FrameTooLarge(
                        f"worker {job.job_id}: protocol frame limit exceeded (exit {status})"
                    )

        try:
            # The private key frame first, then the job. The frame is never logged or kept.
            if (bootstrap := launcher.bootstrap_frame(job)) is not None:
                proc.stdin.write(encode_frame(bootstrap))
                del bootstrap
                await proc.stdin.drain()
            job_frame = {
                "type": "job",
                "job_id": job.job_id,
                "family": job.family.raw,
                "system": job.system.raw,
                "trajectories": list(job.trajectories),
                "caps": asdict(job.caps),
                "task": job.task,
                "payload": job.payload,
            }
            proc.stdin.write(encode_frame(job_frame, where="job frame"))
            await proc.stdin.drain()
            # Between two frames a worker runs one model call or a trajectory's other tool
            # calls, plus a backoff of at most 120 s: silent for longer, it is lost.
            while True:
                # Decode in this task so a credential failure latches before another task runs.
                async with asyncio.timeout(silence_s):
                    message = await read_frame(
                        proc.stdout, limit=max_frame_bytes, where="worker frame"
                    )
                if message is None:
                    await check_terminal_status()
                    break
                kind = message.get("type")
                reply: dict[str, Any] | None = None
                if grading is not None and kind != "error":
                    reply = await grading.handle(message)
                elif kind in (
                    "batch_results",
                    "submitted",
                    "next_item",
                    "settle_batch",
                    "item_terminal",
                    "item_not_run",
                    "counted",
                ) or (kind == "reserve" and message.get("kind") == "batch"):
                    raise BenchmarkValidationError("grading frame on trajectory worker")
                elif kind == "reserve":
                    assert ledger is not None
                    if in_flight is not None:
                        raise BenchmarkValidationError(f"worker {job.job_id} reserved twice")
                    # The job's own provider and model, never the worker's claim.
                    in_flight = await ledger.reserve(
                        provider=job.family.provider,
                        model_id=job.family.model_id,
                        input_bound=int(message["input_bound"]),
                        # Never below the cap the family sends: a worker cannot shrink its bound.
                        output_bound=max(
                            int(message["output_bound"]), job.family.max_output_tokens or 0
                        ),
                        kind=str(message.get("kind", "model_call")),
                        job_id=job.job_id,
                    )
                    reply = (
                        {"type": "grant", "unit_id": in_flight} if in_flight else {"type": "deny"}
                    )
                elif kind == "settle":
                    assert ledger is not None
                    if in_flight is None or message.get("unit_id") != in_flight:
                        raise BenchmarkValidationError(
                            f"worker {job.job_id} settled a unit it does not hold"
                        )
                    buckets = message.get("buckets")
                    await ledger.settle(
                        in_flight,
                        outcome=message["outcome"],
                        buckets=None if buckets is None else Buckets(**buckets),
                    )
                    in_flight = None
                    reply = {"type": "ack"}
                elif kind == "error":
                    reason = str(message.get("reason"))
                    recognized: KeyLeakError | FrameTooLarge | None = None
                    if reason in (WORKER_LEAK_REASON, "own key found in output"):
                        recognized = KeyLeakError(f"worker {job.job_id}: {reason}")
                    elif reason.startswith("FrameTooLarge:"):
                        recognized = FrameTooLarge(f"worker {job.job_id}: {reason}")
                    if recognized is not None:
                        on_failure(recognized)  # before waiting for exit or sibling cleanup
                    with suppress(TimeoutError):  # its drain writes the last lines to the log
                        await asyncio.wait_for(proc.wait(), 5)
                    if recognized is not None:
                        raise recognized
                    # Any other failure costs only this job's cells, like a lost worker.
                    lost_reason = "worker_error"
                    await on_message(job, {"type": "worker_error", "reason": reason})
                    break
                else:
                    if kind in ("trajectory", "discarded", "not_run"):
                        coordinates = job.report_coordinates(message)
                        # A retries_exhausted discard still owes its rerun or a final not_run.
                        if kind != "discarded" or message["reason"] == "budget_stop":
                            reported.add(coordinates)
                    if not aborted.is_set():
                        await on_message(job, message)
                    done = done or kind == "done"
                    if kind in ("trajectory", "discarded"):
                        reply = {"type": "ack"}
                if reply is not None:
                    proc.stdin.write(encode_frame(reply, where="host reply"))
                    await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            await check_terminal_status()
        except TimeoutError:
            pass  # the worker died or stalled; handled as a lost worker below
    except (KeyLeakError, FrameTooLarge) as recognized:
        on_failure(recognized)
        raise
    finally:

        async def teardown() -> None:
            try:
                await _teardown(
                    proc,
                    anchor,
                    log_path,
                    ledger,
                    grading.unit if grading else in_flight,
                    log,
                    partial(launcher.stop_worker, job),
                    on_failure,
                )
            finally:
                if grading is not None:
                    await grading.finish()

        await finish(teardown(), sys.exception())
    if not done and not aborted.is_set():
        for item in job.trajectories:
            if (item["corpus_id"], item["repeat"]) not in reported:
                await on_message(
                    job,
                    {
                        "type": "not_run",
                        "corpus_id": item["corpus_id"],
                        "repeat": item["repeat"],
                        "reason": lost_reason,
                    },
                )


async def run_jobs(
    jobs: Sequence[WorkerJob],
    *,
    launcher: Launcher,
    ledger: LedgerService | None,
    on_message: Callable[[WorkerJob, dict[str, Any]], Awaitable[None]],
    log_dir: Path,
    cwd: Path,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> None:
    """Run every job in its own worker, in parallel; a harness error kills them all."""
    refuse_host_keys(os.environ)
    if ledger is None and any(job.task != "items" for job in jobs):
        raise BenchmarkValidationError("only free item jobs may run without a ledger")
    unsupported = sorted({job.family.id for job in jobs if not launcher.supports(job.family)})
    if unsupported:
        raise BenchmarkValidationError(
            f"the {type(launcher).__name__} cannot run model families {unsupported}: "
            "the local launcher runs only scripted models; use --launcher container"
        )
    log_dir.mkdir(parents=True, exist_ok=True)
    primary: KeyLeakError | FrameTooLarge | None = None
    aborted = asyncio.Event()

    def recognize(failure: KeyLeakError | FrameTooLarge) -> None:
        nonlocal primary
        _stop_grants(ledger)
        aborted.set()
        if primary is None:
            primary = failure
        elif isinstance(primary, FrameTooLarge) and isinstance(failure, KeyLeakError):
            add_safe_cleanup_cause(failure, primary)
            primary = failure

    failed = True
    try:
        await launcher.prepare(jobs)  # raises before any worker starts
        tasks = [
            asyncio.create_task(
                _supervise(
                    job,
                    launcher=launcher,
                    ledger=ledger,
                    on_message=on_message,
                    log_dir=log_dir,
                    cwd=cwd,
                    max_frame_bytes=max_frame_bytes,
                    aborted=aborted,
                    on_failure=recognize,
                )
            )
            for job in jobs
        ]
        failure: BaseException | None = None
        try:
            await asyncio.gather(*tasks)
        except BaseException as exc:
            for task in tasks:
                task.cancel()  # each supervisor kills its worker on the way out

            async def outcome(task: asyncio.Task[None]) -> BaseException | None:
                try:
                    await task
                except BaseException as error:
                    # gather(return_exceptions=True) substitutes a fresh CancelledError,
                    # losing any teardown failure chained to the original cancellation.
                    return error
                return None

            drained = asyncio.gather(*(outcome(task) for task in tasks))
            try:
                await finish(drained, exc)
            except BaseException as interrupted:
                failure = interrupted
            else:
                failure = exc
            if primary is not None:
                # Preserve interrupts, then credential failures, then structural frame failures,
                # even when the recognizing supervisor was cancelled during cleanup.
                selected = failure if not isinstance(failure, Exception) else primary
                prior = selected.__cause__ if selected is not primary else None
                if prior is not None and prior is not primary:
                    selected.__cause__ = None
                seen = {id(selected)}
                if selected.__cause__ is primary:
                    seen.add(id(primary))
                for error in (failure, primary, prior, selected.__context__, *drained.result()):
                    if isinstance(error, asyncio.CancelledError):
                        error = error.__cause__ or error.__context__
                    if isinstance(error, Exception) and id(error) not in seen:
                        seen.add(id(error))
                        add_safe_cleanup_cause(selected, error)
                # A cancellation raised by finish while handling a sibling exception
                # acquires that raw exception as implicit context. Keep only its safe copy.
                selected.__context__ = None
                failure = selected
        if failure is not None:
            # Outside the handler: don't retain the losing raw exception as __context__.
            raise failure
        failed = False
    finally:
        reported: BenchmarkValidationError | None = None

        async def close() -> None:
            nonlocal reported
            if problems := await launcher.close():
                detail = redact_key_shaped_text("; ".join(problems))
                if failed:
                    _diagnostic(f"launcher cleanup problems: {detail}")
                reason = (
                    "launcher cleanup problems"
                    if failed
                    else "the run completed, but the launcher reported problems"
                )
                reported = BenchmarkValidationError(f"{reason}: {detail}")
                raise reported

        pending = sys.exception()
        try:
            await finish(close(), pending)
        except KeyLeakError as leak:
            if leak is pending:
                raise
            if isinstance(pending, Exception):
                # The CLI prints only the final message. Keep earlier resource IDs visible.
                earlier = redact_key_shaped_text(str(pending))
                raise KeyLeakError(
                    f"{redact_key_shaped_text(str(leak))}; earlier failure: {earlier}"
                ) from None
            raise
        except BenchmarkValidationError as exc:
            if not failed or exc is not reported:
                raise
