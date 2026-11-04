"""Protocol declarations retain their signature-only inheritance behavior."""

import inspect

import pytest

from benchmarks.agentic.count import CountClient, Envelope
from benchmarks.grading.batch import BatchClient


class InheritedBatch(BatchClient):
    pass


class InheritedCount(CountClient):
    pass


@pytest.mark.asyncio
async def test_unimplemented_protocol_members_keep_implicit_none() -> None:
    batch = InheritedBatch()
    count = InheritedCount()
    assert batch.request_params() is None
    assert await batch.submit([]) is None
    assert await batch.poll("batch") is None
    assert await batch.results("batch", []) is None
    assert await count.count(Envelope("", None, [])) is None
    assert inspect.iscoroutinefunction(CountClient.count)
    assert not getattr(CountClient.count, "__isabstractmethod__", False)
    assert BatchClient._is_protocol and CountClient._is_protocol
