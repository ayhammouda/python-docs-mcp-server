"""Run a judge batch behind the worker's protected, length-prefixed channel.

Terminal result reads get at most ten attempts under the original poll deadline,
with the same provider batch id. Exhaustion settles once at the conservative rate
and lists the items as batch_failed for a later invocation; it never resubmits
them in this invocation. Input uploads before batch creation settle as free
rejections, retaining their original error kind and retry-after for backoff.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import asdict
from functools import partial
from typing import Any

from benchmarks.agentic.loop import RETRYABLE, backoff_s
from benchmarks.agentic.protocol import (
    RESULT_CHUNK_BYTES as RESULT_CHUNK_BYTES,
)
from benchmarks.agentic.protocol import (
    FrameTooLarge,
    encode_frame,
    scanned_json_bytes,
)
from benchmarks.agentic.types import ProviderCallError
from benchmarks.agentic.worker_ports import TaskChannel, TaskHost
from benchmarks.grading.batch import (
    BatchClient,
    BatchItemResult,
    BatchRequest,
    BatchUploadError,
    check_error_content,
    drop_batch_raw,
    make_batch_client,
)
from benchmarks.model_matrix import model_family_from_raw
from benchmarks.runner import BenchmarkValidationError

POLL_S = 60.0
POLL_CEILING_S = 48 * 3600.0
RESULT_READ_ATTEMPTS = 10


def chunk_results(
    results: Sequence[BatchItemResult], *, limit: int = RESULT_CHUNK_BYTES
) -> Iterator[list[dict[str, Any]]]:
    """Pack compact UTF-8 JSON rows, counting commas as well as the array delimiters."""

    def size(row: dict[str, Any]) -> int:
        return len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())

    chunk: list[dict[str, Any]] = []
    used = 2
    for result in results:
        row = asdict(result)
        length = size(row)
        if length + 2 > limit:
            drop_batch_raw(row)
            length = size(row)
            if length + 2 > limit:
                raise FrameTooLarge("a batch result exceeds the chunk limit even without raw")
        if chunk and used + length + 1 > limit:
            yield chunk
            chunk = []
            used = 2
        used += length + bool(chunk)
        chunk.append(row)
    if chunk:
        yield chunk


async def _poll(
    client: BatchClient,
    batch_id: str,
    unit: str,
    channel: TaskChannel,
    *,
    poll_s: float,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> str:
    errors = 0
    while clock() < deadline:
        try:
            state = await client.poll(batch_id)
            errors = 0
        except ProviderCallError as exc:
            check_error_content(exc, check_content=channel.check_content)
            errors += 1
            if errors == 10:
                raise ProviderCallError("connection", "batch poll failed ten times") from None
            state = None
        scanned_json_bytes(state, where="batch state", check_content=channel.check_content)
        if state is not None and clock() < deadline:
            return state
        channel.send({"type": "heartbeat", "unit_id": unit})
        await sleep(min(poll_s, max(0.0, deadline - clock())))
    raise ProviderCallError("timeout", "batch exceeded the poll ceiling")


async def run_batch_task(
    job: dict[str, Any],
    host: TaskHost,
    channel: TaskChannel,
    *,
    poll_s: float = POLL_S,
    ceiling_s: float = POLL_CEILING_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    check = partial(
        scanned_json_bytes, where="batch provider content", check_content=channel.check_content
    )
    # Settings are already available before asking the host for a paid reservation.
    check(job["family"])
    check(job["payload"])
    family = model_family_from_raw(job["family"])
    client = make_batch_client(
        family, effort=job["payload"].get("effort"), timeout_s=job["caps"]["model_call_timeout_s"]
    )
    check(client.request_params())
    while True:
        grant = await host.request({"type": "reserve", "kind": "batch"})
        if grant.get("type") in ("deny", "no_more_items"):
            break
        if grant.get("type") != "grant":
            raise BenchmarkValidationError("expected batch grant")
        unit = grant["unit_id"]
        # The host reserves BEFORE delivering these items. A leak leaves that held
        # unit for host teardown; never upload, retry, or request another reservation.
        check(grant)
        requests = [
            BatchRequest(
                item_id=i["item_id"],
                system=i["system"],
                user=i["user"],
                schema=i["schema"],
                schema_name=i["schema_name"],
            )
            for i in grant["items"]
        ]
        try:
            batch_id = await client.submit(requests)
        except ProviderCallError as exc:
            check_error_content(exc, check_content=channel.check_content)
            # Uploads precede the inference request, even if the upload response was lost.
            # Keep the original kind/retry-after for backoff, but book a free rejection.
            outcome = "rejected" if isinstance(exc, BatchUploadError) else exc.kind
            await host.settle(unit, outcome=outcome, buckets=None)
            if exc.kind in RETRYABLE:
                attempt = max(i["attempt"] for i in grant["items"])
                delay = backoff_s(attempt, exc.retry_after_s)
                if delay is None:
                    raise
                await sleep(delay)
            continue
        check(batch_id)
        channel.send({"type": "submitted", "unit_id": unit, "provider_batch_id": batch_id})
        deadline = clock() + ceiling_s
        try:
            state = await _poll(
                client,
                batch_id,
                unit,
                channel,
                poll_s=poll_s,
                deadline=deadline,
                clock=clock,
                sleep=sleep,
            )
        except ProviderCallError as exc:
            # Before a terminal state is known, retain the existing conservative policy.
            await host.settle(
                unit, outcome="timeout" if exc.kind == "timeout" else "connection", buckets=None
            )
            continue
        try:
            read_attempt = 0
            while True:
                read_attempt += 1
                try:
                    results = await client.results(batch_id, [r.item_id for r in requests])
                    break
                except ProviderCallError as exc:
                    check_error_content(exc, check_content=channel.check_content)
                    delay = backoff_s(read_attempt, exc.retry_after_s)
                    if (
                        read_attempt == RESULT_READ_ATTEMPTS
                        or exc.kind not in RETRYABLE
                        or delay is None
                        or clock() >= deadline
                    ):
                        raise
                    channel.send({"type": "heartbeat", "unit_id": unit})
                    await sleep(min(delay, max(0.0, deadline - clock())))
                    if clock() >= deadline:
                        raise ProviderCallError("timeout", "batch result read ceiling") from exc
        except ProviderCallError as exc:
            # The provider already finished. Settle conservatively once, but never turn
            # an unavailable result file into another paid submission of these items.
            await host.request(
                {
                    "type": "settle",
                    "unit_id": unit,
                    "outcome": "timeout" if exc.kind == "timeout" else "connection",
                    "buckets": None,
                    "results_unavailable": True,
                }
            )
            continue
        # Inspect every row before chunking can drop raw evidence or publish a prefix.
        for result in results:
            check(asdict(result))
        frame = {"type": "batch_results", "unit_id": unit, "results": []}
        overhead = len(encode_frame(frame)) - 2  # array brackets included by chunk_results
        for rows in chunk_results(results, limit=RESULT_CHUNK_BYTES - overhead):
            await host.request({**frame, "results": rows})
        await host.request(
            {
                "type": "settle_batch",
                "unit_id": unit,
                "result_count": len(results),
                "batch_state": state,
            }
        )
    channel.send({"type": "done"})
