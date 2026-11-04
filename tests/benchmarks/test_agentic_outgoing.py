"""R14 owner repros at the normal trajectory and local CLI boundaries; no network."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import MCPError
from mcp.types import CONNECTION_CLOSED, CallToolResult, TextContent

from benchmarks.agentic.bridge import Bridge
from benchmarks.agentic.clients import GeminiClient, ScriptedClient, gemini_sdk
from benchmarks.agentic.loop import Caps, run_trajectory
from benchmarks.agentic.protocol import KEY_SHAPED, KeyLeakError
from benchmarks.agentic.types import ModelClient, ProviderCallError
from tests.benchmarks.test_agentic_clients import (
    ANTHROPIC_ANSWER,
    ANTHROPIC_TOOL_TURN,
    OPENAI_CALL,
    OPENAI_MESSAGE,
    GeminiRecorder,
    Recorder,
    _anthropic,
    _chat_completion,
    _gemini,
    _gemini_ok,
    _gemini_response,
    _ok,
    _ollama,
    _openai,
    _openai_response,
    _tool_call,
)
from tests.benchmarks.test_agentic_loop import ANSWER, FAMILY, TOOL, FakeBridge

MARKER = "AIza" + "g" * 35
PROVIDERS = ["anthropic", "openai", "google", "ollama", "scripted"]
CASES = [
    "text",
    "structured",
    "structured-name",
    "hidden-text",
    "truncated",
    "beyond-cap",
    "json-boundary",
    "error-result",
    "mcp-error",
    "mcp-error-data",
    "closed",
    "exception",
]


def _result(case: str) -> CallToolResult | Exception:
    text = "diagnostic: " + MARKER
    structured = None
    if case == "structured":
        text, structured = "safe", {"nested": [{"token": MARKER}]}
    elif case == "structured-name":
        text, structured = "safe", {"nested": [{MARKER: "value"}]}
    elif case == "hidden-text":
        structured = {"output": "safe"}
    elif case == "truncated":
        text += "X"  # The raw token is too long; clipping gives it a new boundary.
        assert KEY_SHAPED.search(text) is None
    elif case == "beyond-cap":
        text = "x" * 100 + " " + MARKER
    elif case == "json-boundary":
        text = "\x00xxx" + MARKER
        assert KEY_SHAPED.search(text) is None
        assert KEY_SHAPED.search(json.dumps(text)) is not None
    elif case == "mcp-error":
        return MCPError(-32603, text)
    elif case == "mcp-error-data":
        return MCPError(-32603, "safe error", {"token": MARKER})
    elif case == "closed":
        return MCPError(CONNECTION_CLOSED, text)
    elif case == "exception":
        return RuntimeError(text)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=structured,
        isError=case == "error-result",
    )


def _bridge(result: CallToolResult | Exception) -> tuple[Bridge, AsyncMock]:
    call = AsyncMock(
        **({"side_effect": result} if isinstance(result, Exception) else {"return_value": result})
    )
    bridge = Bridge(
        type("Session", (), {"call_tool": call})(),
        {},
        [{"name": "search_docs", "description": "d", "inputSchema": {"type": "object"}}],
    )
    return bridge, call


def _client(provider: str) -> tuple[Any, Any]:
    if provider == "anthropic":
        rec = Recorder(_ok(ANTHROPIC_TOOL_TURN), _ok(ANTHROPIC_ANSWER))
        return _anthropic(rec), rec
    if provider == "openai":
        rec = Recorder(
            _ok(_openai_response([OPENAI_CALL])), _ok(_openai_response([OPENAI_MESSAGE]))
        )
        return _openai(rec), rec
    if provider == "google":
        rec = GeminiRecorder(
            _gemini_ok(_gemini_response([{"functionCall": {"name": "search_docs", "args": {}}}])),
            _gemini_ok(_gemini_response([{"text": "done"}])),
        )
        return _gemini(rec), rec
    if provider == "ollama":
        rec = Recorder(
            _ok(
                _chat_completion(
                    {"role": "assistant", "tool_calls": [_tool_call("search_docs", "{}", "c1")]}
                )
            ),
            _ok(_chat_completion({"role": "assistant", "content": "done"})),
        )
        return _ollama(rec), rec

    class RecordingScript(ScriptedClient):
        seen: list[Any]

        async def call(self, conv):
            self.seen.append(deepcopy(conv))
            return await super().call(conv)

    client = RecordingScript("anthropic", "scripted-1", [TOOL, ANSWER])
    client.seen = []
    return client, client


class RecordingLedger:
    """No pricing or model-limit dependencies; the actual spend gate is recorded."""

    def __init__(self):
        self.reserves: list[dict[str, Any]] = []
        self.settles: list[dict[str, Any]] = []

    async def reserve(self, **kwargs):
        self.reserves.append(kwargs)
        return str(len(self.reserves))

    async def settle(self, unit_id, **kwargs):
        self.settles.append({"unit_id": unit_id, **kwargs})


async def _run(client: ModelClient, bridge, ledger, **kwargs):
    return await run_trajectory(
        client=client,
        bridge=bridge,
        ledger=ledger,
        family=replace(FAMILY, model_id=client.model_id, provider=client.provider),
        prompt=kwargs.pop("prompt", "question"),
        caps=kwargs.pop("caps", Caps()),
        seen_tool_args=set(),
        **kwargs,
    )


async def _close(client):
    if client.provider == "google":
        await client._sdk.aio.aclose()
        client._sdk.close()
    elif hasattr(client, "_sdk"):
        await client._sdk.close()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("case", CASES)
async def test_tool_evidence_stops_before_the_next_request_and_reservation(provider, case):
    client, recorder = _client(provider)
    ledger = RecordingLedger()
    bridge, call = _bridge(_result(case))
    try:
        with pytest.raises(KeyLeakError) as error:
            await _run(client, bridge, ledger, caps=Caps(result_bytes=len("diagnostic: " + MARKER)))
        assert MARKER not in str(error.value)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        call.assert_awaited_once()
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("source", ["prompt", "schema", "replay", "replay-json"])
async def test_first_request_and_provider_replay_are_checked(provider, source):
    client, recorder = _client(provider)
    ledger = RecordingLedger()
    bridge, call = _bridge(CallToolResult(content=[TextContent(type="text", text="safe")]))
    kwargs = {}
    if source == "prompt":
        kwargs["prompt"] = MARKER
    elif source == "schema":
        bridge.tools[0].input_schema["description"] = MARKER
    else:
        original = client.call
        value = "\x00xxx" + MARKER if source == "replay-json" else MARKER

        async def poisoned(conv):
            turn = await original(conv)
            # Visible content stays safe: the provider-native replay alone is contaminated.
            if provider == "anthropic":
                turn.replay[0]["signature"] = value
            elif provider == "openai":
                turn.replay.append({"type": "reasoning", "encrypted_content": value})
            elif provider == "google":
                turn.replay.parts[0].text = value
            elif provider == "ollama":
                turn.replay["content"] = value
            else:
                turn = replace(turn, text=value)
            return turn

        client.call = poisoned
    try:
        with pytest.raises(KeyLeakError) as error:
            await _run(client, bridge, ledger, **kwargs)
        assert MARKER not in str(error.value)
        assert (
            len(recorder.seen) == len(ledger.reserves) == (1 if source.startswith("replay") else 0)
        )
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("case", ["text", "truncated", "json-boundary"])
async def test_loop_checks_tool_text_even_without_the_mcp_bridge(provider, case):
    client, recorder = _client(provider)
    ledger = RecordingLedger()
    bridge = FakeBridge(text=_result(case).content[0].text)
    try:
        with pytest.raises(KeyLeakError) as error:
            await _run(client, bridge, ledger, caps=Caps(result_bytes=len("diagnostic: " + MARKER)))
        assert MARKER not in str(error.value)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert len(bridge.calls) == 1
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("is_error", [False, True])
async def test_valid_tool_transcripts_still_have_two_calls(provider, is_error):
    client, recorder = _client(provider)
    ledger = RecordingLedger()
    bridge, call = _bridge(
        CallToolResult(
            content=[TextContent(type="text", text="ordinary diagnostic")], isError=is_error
        )
    )
    try:
        record = await _run(client, bridge, ledger)
        assert record["status"] == "succeeded"
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 2
        call.assert_awaited_once()
        assert record["tool_calls"][0]["is_error"] is is_error
        assert record["canonical_messages"][-2]["content"][0]["content"] == "ordinary diagnostic"
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("status", [400, 500])
async def test_provider_errors_are_clean_and_cannot_authorize_a_retry(provider, status):
    client, recorder = _client(provider)
    ledger = RecordingLedger()
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    if provider == "scripted":

        async def fail(conv):
            recorder.seen.append(deepcopy(conv))
            raise ProviderCallError(
                "server" if status == 500 else "rejected", MARKER, status=status
            )

        client.call = fail
    else:
        body = {"error": {"code": status, "type": "api_error", "message": MARKER}}
        recorder.responses[0] = (_gemini_ok if provider == "google" else _ok)(body, status)
    try:
        with pytest.raises(KeyLeakError) as error:
            await _run(client, None, ledger, sleep=sleep)
        assert MARKER not in str(error.value)
        assert len(recorder.seen) == len(ledger.reserves) == len(ledger.settles) == 1
        assert waits == []
    finally:
        await _close(client)


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
async def test_synthetic_sdk_auth_headers_are_allowed(provider):
    client, recorder = _client(provider)
    # Only the SDK auth credential is key-shaped; no content contains it.
    if provider == "google":
        await _close(client)
        client = GeminiClient(
            gemini_sdk(MARKER, 30, transport=recorder.transport()), client.model_id, 32_768
        )
    else:
        client._sdk.api_key = MARKER
    ledger = RecordingLedger()
    try:
        record = await _run(client, None, ledger)
        assert record["status"] == "succeeded"
        assert len(recorder.seen) == len(ledger.reserves) == 2
        header = {"anthropic": "x-api-key", "openai": "authorization", "google": "x-goog-api-key"}[
            provider
        ]
        assert MARKER in recorder.seen[0].headers[header]
        assert all(MARKER.encode() not in request.content for request in recorder.seen)
    finally:
        await _close(client)


@pytest.mark.parametrize(
    "case",
    [
        "text",
        "structured",
        "truncated",
        "json-boundary",
        "mcp-error",
        "mcp-error-data",
        "exception",
    ],
)
def test_cli_worker_withholds_transcript_and_never_reserves_again(
    tmp_path: Path, monkeypatch, capsys, case
):
    from benchmarks.__main__ import main
    from benchmarks.agentic.host import LocalLauncher
    from tests.benchmarks.test_agentic_run import _cli_args

    count = tmp_path / "calls.txt"
    program = tmp_path / "recording_worker.py"
    program.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "from pathlib import Path\n"
        "from benchmarks.agentic import clients, worker\n"
        "from tests.benchmarks.test_agentic_outgoing import _client, _bridge, _result\n"
        "from benchmarks.agentic.clients import ScriptedClient\n"
        "from tests.benchmarks.test_agentic_loop import TOOL, ANSWER\n"
        "class RecordingClient(ScriptedClient):\n"
        "    async def call(self, conv):\n"
        f"        with Path({str(count)!r}).open('a') as f: f.write('call\\n')\n"
        "        return await super().call(conv)\n"
        "clients.make_client = lambda family, **kw: RecordingClient(\n"
        "    family.provider, family.model_id, [TOOL, ANSWER])\n"
        "async def open_bridge(stack, system):\n"
        f"    return _bridge(_result({case!r}))[0]\n"
        "worker._open = open_bridge\n"
        "raise SystemExit(worker.main_worker())\n"
    )
    monkeypatch.setattr(
        LocalLauncher, "command", lambda self, job: ([sys.executable, "-I", str(program)], {})
    )
    monkeypatch.setattr(
        "benchmarks.agentic.run.HOSTED_CAPS", Caps(result_bytes=len("diagnostic: " + MARKER))
    )
    with pytest.raises(SystemExit) as error:
        main(
            [
                *_cli_args(tmp_path),
                "--models",
                "scripted-anthropic",
                "--systems",
                "no-docs",
                "--questions",
                "SYN-EX-001",
                "--repeats",
                "1",
            ]
        )
    assert error.value.code == 3
    assert count.read_text().splitlines() == ["call"]
    captured = capsys.readouterr()
    assert "key leak" in captured.err and MARKER not in captured.err + captured.out
    run = tmp_path / "run"
    assert not list((run / "transcripts").rglob("*.json"))
    assert all(MARKER.encode() not in p.read_bytes() for p in run.rglob("*") if p.is_file())
    events = [
        json.loads(line)
        for line in (tmp_path / "ledger" / "anthropic.jsonl").read_text().splitlines()
    ]
    assert sum(e["event"] == "reserve" for e in events) == 1
    assert sum(e["event"] == "settle" for e in events) == 1
