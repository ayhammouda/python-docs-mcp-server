"""Wait for owned work without letting a later interrupt replace the first one."""

import asyncio
import signal
import sys
import traceback
from collections.abc import Awaitable, Coroutine
from typing import Any

from benchmarks.agentic.protocol import (
    FrameTooLarge,
    KeyLeakError,
    find_key_leak,
    redact_key_shaped_text,
)
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


def add_safe_cleanup_cause(primary: BaseException, failure: BaseException) -> None:
    """Attach diagnostics without retaining raw exceptions, their notes or chains."""
    try:
        detail = redact_key_shaped_text("".join(traceback.format_exception(failure, limit=0)))
    except Exception:  # even broken exception formatting must not replace recognition
        detail = f"could not safely report {type(failure).__name__}"
    add_cleanup_cause(primary, BenchmarkValidationError(f"cleanup failed: {detail.strip()}"))


async def finish[T](operation: Awaitable[T], pending: BaseException | None = None) -> T:
    """Finish the operation before propagating cancellation.

    If the caller is already unwinding a cancellation, leave it to propagate on
    success. On failure, raise that same cancellation with the failure as its cause.
    A recognized credential or frame failure also survives ordinary cleanup errors, with
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
        if not isinstance(error, Exception):
            raise
        if isinstance(pending, FrameTooLarge) and (leak := find_key_leak(error)):
            raise leak
        if not isinstance(pending, (KeyLeakError, FrameTooLarge)):
            raise
        add_safe_cleanup_cause(pending, error)
    else:
        if cancelled is not None and cancelled is not pending:
            raise cancelled
        return result
    # Outside the handler: raising here must not set the raw error as __context__.
    raise pending


async def until_signalled(run: Coroutine[Any, Any, None]) -> None:
    """Await ``run``; SIGTERM or SIGHUP cancels it, so every worker is torn down as on Ctrl-C.

    Workers run in process groups of their own, so no signal sent to the host's group reaches
    them: only the host's teardown can end them. An ignored signal stays ignored, a thread
    that cannot take signals runs without the handlers, and each disposition the handlers
    replaced is restored afterwards.
    """
    task = asyncio.create_task(run)
    loop = asyncio.get_running_loop()
    signal_seen = False

    def interrupt(signum: signal.Signals) -> None:
        nonlocal signal_seen
        if not signal_seen:
            signal_seen = True
            if not task.cancelling():
                task.cancel(f"signal {signum.name}")

    wanted = [  # an ignored signal stays ignored, as under nohup
        (signum, previous)
        for signum in (signal.SIGTERM, signal.SIGHUP)
        if (previous := signal.getsignal(signum)) is not signal.SIG_IGN
    ]
    installed: list[tuple[signal.Signals, Any]] = []  # each with the disposition it replaced
    try:
        for signum, previous in wanted:
            loop.add_signal_handler(signum, interrupt, signum)
            installed.append((signum, previous))
    except (RuntimeError, NotImplementedError, ValueError) as exc:
        # Not the main thread, or a loop without signal support: run without those handlers.
        skipped = ", ".join(signum.name for signum, _ in wanted[len(installed) :])
        print(
            f"agentic run: signal handlers not installed for {skipped} "
            f"({type(exc).__name__}: {exc}); no teardown on those signals",
            file=sys.stderr,
        )
    try:
        await task
    finally:
        for signum, previous in installed:
            loop.remove_signal_handler(signum)  # leaves SIG_DFL behind
            signal.signal(signum, signal.SIG_DFL if previous is None else previous)
