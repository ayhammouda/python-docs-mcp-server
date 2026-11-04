"""Wait for owned work without letting a later interrupt replace the first one."""

import asyncio
import traceback
from collections.abc import Awaitable
from typing import Any

from benchmarks.agentic.protocol import KeyLeakError, redact_key_shaped_text
from benchmarks.runner import BenchmarkValidationError


async def settle(task: asyncio.Future[Any]) -> asyncio.CancelledError | None:
    """Leave the task's outcome for its owner, even across repeated cancellations."""
    cancelled = None
    while not task.done():
        try:
            await asyncio.wait((task,))  # cancelling the wait does not cancel the task
        except asyncio.CancelledError as exc:
            if cancelled is None:
                cancelled = exc
    return cancelled


def add_cleanup_cause(primary: BaseException, failure: BaseException) -> None:
    """Retain each rollback failure on the exception already being propagated."""
    prior = primary.__cause__
    if primary is not failure and prior is not failure:
        primary.__cause__ = (
            failure if prior is None else BaseExceptionGroup("cleanup failures", [prior, failure])
        )


async def finish[T](operation: Awaitable[T], pending: BaseException | None = None) -> T:
    """Finish the operation before propagating cancellation.

    If the caller is already unwinding a cancellation, leave it to propagate on
    success. On failure, raise that same cancellation with the failure as its cause.
    A recognized credential failure also survives ordinary cleanup errors, with
    a redacted diagnostic copy as its cause; interrupts retain their precedence.
    """
    task = asyncio.ensure_future(operation)
    cancelled = await settle(task)
    if isinstance(pending, asyncio.CancelledError):
        cancelled = pending
    try:
        result = task.result()
    except BaseException as error:
        if cancelled is not None and cancelled is not error:
            add_cleanup_cause(cancelled, error)
            raise cancelled
        if not isinstance(pending, KeyLeakError) or not isinstance(error, Exception):
            raise
        # Copy notes and chains too, without retaining raw secret-bearing exceptions.
        try:
            detail = redact_key_shaped_text("".join(traceback.format_exception(error, limit=0)))
        except Exception:  # even broken exception formatting must not replace the leak
            detail = f"could not safely report {type(error).__name__}"
        add_cleanup_cause(pending, BenchmarkValidationError(f"cleanup failed: {detail.strip()}"))
    else:
        if cancelled is not None and cancelled is not pending:
            raise cancelled
        return result
    # Outside the handler: raising here must not set the raw error as __context__.
    raise pending
