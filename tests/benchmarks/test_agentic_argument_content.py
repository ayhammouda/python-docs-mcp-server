"""Real adapter argument replay with synthetic credentials and recording SDK transports."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from mcp.types import CallToolResult, TextContent

from benchmarks.agentic.loop import Caps
from benchmarks.agentic.protocol import KEY_SHAPED, KeyLeakError
from benchmarks.agentic.types import Buckets
from benchmarks.agentic.worker import ProtocolChannel
from tests.benchmarks.test_agentic_clients import (
    OPENAI_CALL,
    OPENAI_MESSAGE,
    Recorder,
    _chat_completion,
    _ok,
    _ollama,
    _openai,
    _openai_response,
    _tool_call,
)
from tests.benchmarks.test_agentic_outgoing import RecordingLedger, _bridge, _close, _run


def _argument_client(provider, arguments, name="search_docs", *, safe_first=False):
    calls = [(name, arguments)]
    if safe_first:
        calls.insert(0, ("search_docs", '{"query":"json"}'))
    if provider == "openai":
        first = _openai_response(
            [
                {**OPENAI_CALL, "call_id": f"call_{i}", "name": tool, "arguments": args}
                for i, (tool, args) in enumerate(calls)
            ]
        )
        recorder = Recorder(_ok(first), _ok(_openai_response([OPENAI_MESSAGE])))
        return _openai(recorder), recorder
    first = _chat_completion(
        {
            "role": "assistant",
            "tool_calls": [
                _tool_call(tool, args, f"call_{i}") for i, (tool, args) in enumerate(calls)
            ],
        },
        "tool_calls",
    )
    recorder = Recorder(_ok(first), _ok(_chat_completion({"role": "assistant", "content": "done"})))
    return _ollama(recorder), recorder


CASES = [
    "array",
    "scalar",
    "object",
    "duplicate",
    "nested-duplicate",
    "duplicate-array",
    "duplicate-name",
    "malformed",
    "unfinished",
    "unfinished-slash",
    "unfinished-unicode",
    "broken-escape",
    "raw-invalid",
    "name",
    "later-name",
    "later-object",
]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("mode", ["opaque-literal", "shaped-literal", "lexical"])
@pytest.mark.parametrize("case", CASES)
async def test_arguments_stop_before_dispatch_or_second_grant_with_first_usage_settled(
    provider, mode, case
):
    literal = "synthetic_bootstrap_opaque" if mode == "opaque-literal" else "sk-proj-" + "g" * 60
    value = literal if mode == "lexical" else "prefix" + literal + "-suffix"
    if mode != "lexical":
        assert KEY_SHAPED.search(value) is None
    encoded = json.dumps(value).replace("s", r"\u0073")
    name = value if case in ("name", "later-name") else "search_docs"
    arguments = {
        "array": f"[{encoded}]",
        "scalar": encoded,
        "object": f'{{"query":{encoded}}}',
        "duplicate": f'{{"query":{encoded},"query":"json"}}',
        "nested-duplicate": f'{{"query":{{"value":{encoded},"value":"json"}}}}',
        "duplicate-array": f'[{{"query":{encoded},"query":"json"}}]',
        "duplicate-name": f'{{{encoded}:"first",{encoded}:"second"}}',
        "malformed": f'{{"query":{encoded},',
        "raw-invalid": "not-json " + value,
    }.get(case, '{"query":"json"}')
    if case.startswith("unfinished") or case == "broken-escape":
        # Only one encoded character, with a completed harmless marker before damage.
        prefix = ('"' if mode == "lexical" else '"prefix') + literal.replace(
            literal[0], f"\\u{ord(literal[0]):04x}", 1
        )
        tail = {
            "unfinished": "-suffix",
            "unfinished-slash": "-suffix\\",
            "unfinished-unicode": "-suffix\\u00",
            "broken-escape": '-suffix\\x20"}',
        }[case]
        if mode == "lexical":
            tail = tail.replace("-suffix", " ")
        arguments = '{"query":' + prefix + tail
    if case == "later-object":
        arguments = f'{{"query":{encoded}}}'
    client, recorder = _argument_client(
        provider, arguments, name, safe_first=case.startswith("later-")
    )
    # The bound credential also authenticates the first SDK request. Content checks must
    # never compare against auth headers or attach the callback to the HTTP transport.
    if provider == "openai":
        client._sdk.api_key = literal
    channel = ProtocolChannel(-1, (literal.encode(),), -1, Mock(), (Mock(), Mock()))
    ledger = RecordingLedger()
    bridge, dispatch = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    try:
        with pytest.raises(KeyLeakError) as caught:
            await _run(
                client,
                bridge,
                ledger,
                check_content=None if mode == "lexical" else channel.check_content,
            )
        assert literal not in str(caught.value)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        expected = (
            Buckets(500, 300, 200, 50, 40) if provider == "openai" else Buckets(120, 0, 0, 30)
        )
        assert ledger.settles == [{"unit_id": "1", "outcome": "ok", "buckets": expected}]
        dispatch.assert_not_awaited()
        assert literal.encode() not in recorder.seen[0].content
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize(
    "arguments,valid",
    [
        ('{"query":"js\\u006fn"}', True),
        ('{"query":"old","query":"json"}', True),
        ('["ordinary"]', False),
        ('"ordinary"', False),
        ("42", False),
        ("true", False),
        ("null", False),
        ('{"query":"ordinary",', False),
        ('{"query":"ord\\u0069nary', False),
        ('{"query":"quote: \\" slash: \\\\ emoji: \\ud83d\\ude00"}', True),
    ],
)
async def test_safe_arguments_keep_native_replay_dispatch_and_billing(provider, arguments, valid):
    client, recorder = _argument_client(provider, arguments)
    channel = ProtocolChannel(-1, (b"synthetic_bootstrap_opaque",), -1, Mock(), (Mock(), Mock()))
    ledger = RecordingLedger()
    bridge, dispatch = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    try:
        record = await _run(client, bridge, ledger, check_content=channel.check_content)
        assert record["status"] == "succeeded"
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 2
        if valid:
            dispatch.assert_awaited_once_with("search_docs", json.loads(arguments))
        else:
            dispatch.assert_not_awaited()
            assert record["tool_calls"][0]["is_error"] is True
        body = recorder.body(1)
        replay = (
            next(item for item in body["input"] if item.get("type") == "function_call")
            if provider == "openai"
            else body["messages"][2]["tool_calls"][0]["function"]
        )
        assert replay["arguments"] == arguments
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("usage", [True, False])
async def test_argument_rejection_settles_even_when_usage_absent_or_model_cap_reached(
    provider, usage
):
    literal = "synthetic_bootstrap_opaque"
    arguments = '["prefix\\u0073ynthetic_bootstrap_opaque-suffix"]'
    client, recorder = _argument_client(provider, arguments)
    if not usage:
        body = json.loads(recorder.responses[0].content)
        body.pop("usage")
        recorder.responses[0] = _ok(body)
    channel = ProtocolChannel(-1, (literal.encode(),), -1, Mock(), (Mock(), Mock()))
    ledger = RecordingLedger()
    try:
        with pytest.raises(KeyLeakError):
            await _run(
                client, None, ledger, caps=Caps(model_calls=1), check_content=channel.check_content
            )
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert ledger.settles[0]["outcome"] == ("ok" if usage else "no_usage")
        assert (ledger.settles[0]["buckets"] is not None) is usage
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("case", ["integer-limit", "nesting-limit"])
async def test_argument_normalization_limits_keep_known_usage_and_safe_continuation(provider, case):
    arguments = (
        '{"query":' + "7" * 4301 + "}"
        if case == "integer-limit"
        else '{"query":' + "[" * 20000 + "0" + "]" * 20000 + "}"
    )
    client, recorder = _argument_client(provider, arguments)
    ledger = RecordingLedger()
    bridge, dispatch = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    channel = ProtocolChannel(-1, (b"synthetic_bootstrap_opaque",), -1, Mock(), (Mock(), Mock()))
    try:
        record = await _run(client, bridge, ledger, check_content=channel.check_content)
        assert record["status"] == "succeeded"
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 2
        expected = (
            Buckets(500, 300, 200, 50, 40) if provider == "openai" else Buckets(120, 0, 0, 30)
        )
        assert ledger.settles[0] == {"unit_id": "1", "outcome": "ok", "buckets": expected}
        assert record["tool_calls"][0]["is_error"] is True
        assert record["tool_calls"][0]["raw_arguments"] == arguments
        dispatch.assert_not_awaited()
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("arguments", ['{"query":"ordinary\\', '{"query":"ordinary\\u00'])
async def test_uninspectable_argument_suffix_stops_after_honest_settlement(provider, arguments):
    client, recorder = _argument_client(provider, arguments)
    ledger = RecordingLedger()
    try:
        with pytest.raises(KeyLeakError, match="uninspectable JSON string"):
            await _run(client, None, ledger)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert ledger.settles[0]["outcome"] == "ok"
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
async def test_argument_inspection_budget_fails_closed_after_settlement(monkeypatch, provider):
    from benchmarks.agentic import clients, protocol

    monkeypatch.setattr(clients, "MAX_FRAME_BYTES", 64)
    monkeypatch.setattr(protocol, "MAX_FRAME_BYTES", 64)
    client, recorder = _argument_client(provider, '{"query":"' + "x" * 65 + '"}')
    ledger = RecordingLedger()
    try:
        with pytest.raises(KeyLeakError, match="content inspection limit"):
            await _run(client, None, ledger)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert ledger.settles[0]["outcome"] == "ok"
    finally:
        await _close(client)


async def test_unbalanced_prefix_cannot_hide_a_replayed_argument():
    client, recorder = _argument_client("openai", r'x"{"query":"pre\u006frchard-suffix"}')
    ledger = RecordingLedger()
    bridge, dispatch = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    channel = ProtocolChannel(-1, (b"orchard",), -1, None, ())
    try:
        with pytest.raises(KeyLeakError):
            await _run(client, bridge, ledger, check_content=channel.check_content)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert ledger.settles[0]["outcome"] == "ok"
        dispatch.assert_not_awaited()
    finally:
        await _close(client)
