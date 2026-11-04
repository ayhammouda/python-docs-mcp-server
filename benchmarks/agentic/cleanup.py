"""Wait for owned work without letting a later interrupt replace the first one."""

import asyncio
from collections.abc import Awaitable
from typing import Any


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


async def finish[T](operation: Awaitable[T], pending: BaseException | None = None) -> T:
    """Finish the operation before propagating cancellation.

    If the caller is already unwinding a cancellation, leave it to propagate on
    success. On failure, raise that same cancellation with the failure as its cause.
    """
    task = asyncio.ensure_future(operation)
    cancelled = await settle(task)
    if isinstance(pending, asyncio.CancelledError):
        cancelled = pending
    try:
        result = task.result()
    except BaseException as error:
        if cancelled is not None and cancelled is not error:
            raise cancelled from error
        raise
    if cancelled is not None and cancelled is not pending:
        raise cancelled
    return result
