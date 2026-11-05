"""Worker task I/O contracts, independent of task dispatch and host supervision.

The protected worker channel and host-ledger proxy implement these ports.
Counting and grading consume only these operations, as do recorded transports.
"""

from typing import Any, Protocol

from benchmarks.agentic.ledger import Outcome
from benchmarks.agentic.types import Buckets


class TaskChannel(Protocol):
    def send(self, message: dict[str, Any]) -> None: ...

    def check_content(self, content: bytes) -> None: ...


class TaskHost(Protocol):
    async def request(self, message: dict[str, Any]) -> dict[str, Any]: ...

    async def settle(self, unit_id: str, *, outcome: Outcome, buckets: Buckets | None) -> None: ...
