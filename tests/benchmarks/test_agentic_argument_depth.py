"""Bound argument normalization before recursive transcript/MCP serialization; no network."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, TextContent

from benchmarks.agentic import clients, worker
from benchmarks.agentic.bridge import open_bridge
from benchmarks.agentic.protocol import KEY_SHAPED, KeyLeakError, encode_frame
from benchmarks.agentic.types import Buckets
from tests.benchmarks.test_agentic_argument_content import _argument_client
from tests.benchmarks.test_agentic_host import NO_DOCS, _family
from tests.benchmarks.test_agentic_mcp_boundary import _system
from tests.benchmarks.test_agentic_outgoing import RecordingLedger, _bridge, _close, _run


def _arguments(depth, shape, leaf='"ordinary"'):
    """Build JSON text with exactly depth containers, without recursive fixture code."""
    opens, closes = [], []
    for index in range(depth - 1):
        array = shape == "array" or (shape == "mixed" and index % 2 == 0)
        opens.append("[" if array else '{"value":')
        closes.append("]" if array else "}")
    return '{ "query": ' + "".join(opens) + leaf + "".join(reversed(closes)) + " }"


def _native_arguments(provider, payload, *, response=False):
    if provider == "openai":
        items = payload["output" if response else "input"]
        return next(item["arguments"] for item in items if item.get("type") == "function_call")
    message = payload["choices"][0]["message"] if response else next(
        item for item in payload["messages"] if item.get("tool_calls")
    )
    return message["tool_calls"][0]["function"]["arguments"]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("shape", ["array", "object", "mixed"])
@pytest.mark.parametrize("depth", [65, 1100, 9500])
@pytest.mark.parametrize("shadowed", [False, True])
async def test_deep_sdk_arguments_keep_usage_evidence_and_invalid_argument_continuation(
    provider, shape, depth, shadowed
):
    arguments = _arguments(depth, shape)
    if shadowed:
        arguments = arguments[:-2] + ', "query":"safe" }'
    client, recorder = _argument_client(provider, arguments)
    ledger = RecordingLedger()
    bridge, dispatch = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    channel = worker.ProtocolChannel(-1, (), -1, None, ())
    try:
        record = await _run(client, bridge, ledger, check_content=channel.check_content)
        assert record["status"] == "succeeded"
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 2
        expected = (
            Buckets(500, 300, 200, 50, 40) if provider == "openai" else Buckets(120, 0, 0, 30)
        )
        assert ledger.settles[0] == {"unit_id": "1", "outcome": "ok", "buckets": expected}
        call = record["tool_calls"][0]
        assert call["is_error"] is True
        assert call["arguments"] == {}
        assert call["raw_arguments"] == arguments
        raw_response = record["model_calls"][0]["raw_response"]
        assert _native_arguments(provider, raw_response, response=True) == arguments
        assert _native_arguments(provider, recorder.body(1)) == arguments
        # Exercise publication, which previously failed before a score could be produced.
        encode_frame({"type": "trajectory", "record": record}, where="test trajectory")
        dispatch.assert_not_awaited()
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("shape", ["array", "object", "mixed"])
@pytest.mark.parametrize("depth", [1, 16, 64])
async def test_reasonable_argument_depth_completes_real_mcp_validation_and_publication(
    provider, shape, depth
):
    # Brackets, escaped quotes and backslashes inside strings must not count as containers.
    arguments = _arguments(depth, shape, json.dumps('ordinary [ { \\ " ] }' * 80))
    client, recorder = _argument_client(provider, arguments)
    ledger = RecordingLedger()
    try:
        async with open_bridge(_system("valid", "clean")) as bridge:
            record = await _run(client, bridge, ledger)
        assert record["status"] == "succeeded"
        call = record["tool_calls"][0]
        assert call["arguments"] == json.loads(arguments)
        assert call["raw_arguments"] is None
        assert call["is_error"] is False
        assert call["result"] == '{"answer":"clean answer"}'
        assert _native_arguments(provider, recorder.body(1)) == arguments
        encode_frame({"type": "trajectory", "record": record}, where="test trajectory")
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("depth", [64, 65, 1100])
@pytest.mark.parametrize("kind", ["opaque", "shaped"])
@pytest.mark.parametrize("shadowed", [False, True])
async def test_deep_and_near_limit_arguments_scan_encoded_content_even_when_shadowed(
    provider, depth, kind, shadowed
):
    marker = "orchard" if kind == "opaque" else "AIza" + "g" * 35
    encoded = json.dumps(marker).replace(marker[0], f"\\u{ord(marker[0]):04x}", 1)
    arguments = _arguments(depth, "mixed", encoded)
    if shadowed:
        arguments = arguments[:-2] + ', "query":"safe" }'
    assert KEY_SHAPED.search(arguments) is None
    client, recorder = _argument_client(provider, arguments, safe_first=True)
    ledger = RecordingLedger()
    bridge, dispatch = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    literals = (marker.encode(),) if kind == "opaque" else ()
    channel = worker.ProtocolChannel(-1, literals, -1, None, ())
    try:
        with pytest.raises(KeyLeakError):
            await _run(client, bridge, ledger, check_content=channel.check_content)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert ledger.settles[0]["outcome"] == "ok"
        dispatch.assert_not_awaited()
    finally:
        await _close(client)


async def _run_sdk_worker(client, monkeypatch):
    reader = asyncio.StreamReader()
    messages = []

    class Channel:
        check_content = worker.ProtocolChannel(-1, (), -1, None, ()).check_content

        def send(self, message):
            encode_frame(message, where="test worker report")
            messages.append(message)
            if message["type"] == "reserve":
                reader.feed_data(encode_frame({"type": "grant", "unit_id": str(len(messages))}))
            elif message["type"] in ("settle", "trajectory", "discarded"):
                reader.feed_data(encode_frame({"type": "ack"}))

    job = {
        "type": "job",
        "family": _family(Path("unused")).raw,  # scripted limits: no models endpoint
        "system": NO_DOCS.raw,
        "caps": {},
        "trajectories": [{"corpus_id": "Q1", "repeat": 1, "prompt": "ordinary"}],
    }
    monkeypatch.setattr(clients, "make_client", lambda *args, **kwargs: client)
    return await worker.run_worker(Channel(), reader, job), messages


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_sdk_accepted_depth_keeps_worker_alive_and_publishes_a_trajectory(
    monkeypatch, provider
):
    client, recorder = _argument_client(provider, _arguments(1100, "array"))
    try:
        status, messages = await _run_sdk_worker(client, monkeypatch)
        assert status == 0, [m for m in messages if m["type"] == "error"]
        assert messages[-1] == {"type": "done"}
        assert len(recorder.seen) == 2
        assert [m["outcome"] for m in messages if m["type"] == "settle"] == ["ok", "ok"]
        trajectories = [m["record"] for m in messages if m["type"] == "trajectory"]
        assert len(trajectories) == 1
        assert trajectories[0]["status"] == "succeeded"
        assert trajectories[0]["tool_calls"][0]["raw_arguments"] == _arguments(1100, "array")
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("arguments", [r'{"query":"\ud800"}', r'{"\udfff":"value"}'])
async def test_unpaired_surrogate_stops_worker_after_one_correct_settlement(
    monkeypatch, provider, arguments
):
    client, recorder = _argument_client(provider, arguments, safe_first=True)
    bridge, dispatch = _bridge(CallToolResult(content=[]))
    monkeypatch.setattr(worker, "_open", AsyncMock(return_value=bridge))
    try:
        status, messages = await _run_sdk_worker(client, monkeypatch)
        assert status == 3
        assert messages[-1] == {"type": "error", "reason": worker.WORKER_LEAK_REASON}
        assert len(recorder.seen) == 1
        dispatch.assert_not_awaited()
        assert [m["type"] for m in messages if m["type"] != "system_info"] == [
            "reserve", "settle", "error"
        ]
        settlement = next(m for m in messages if m["type"] == "settle")
        expected = (
            Buckets(500, 300, 200, 50, 40) if provider == "openai" else Buckets(120, 0, 0, 30)
        )
        assert settlement["outcome"] == "ok"
        assert Buckets(**settlement["buckets"]) == expected
    finally:
        await _close(client)
