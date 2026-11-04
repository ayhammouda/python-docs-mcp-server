"""Exercise the normal stdio/ClientSession boundary with unvalidated MCP replies."""

from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import ClientSession, MCPError, StdioServerParameters, stdio_client

from benchmarks.agentic import worker
from benchmarks.agentic.bridge import Bridge, BridgeCrash, open_bridge, system_spec_from_raw
from benchmarks.agentic.host import LocalLauncher, run_jobs
from benchmarks.agentic.protocol import WORKER_LEAK_REASON, KeyLeakError
from tests.benchmarks.test_agentic_host import REPO_ROOT, Host, _job
from tests.benchmarks.test_agentic_sandbox import FAMILIES

SERVER = Path(__file__).parent / "fixtures/agentic/raw_result_server.py"
CASES = [
    "missing-structured",
    "invalid-structured",
    "malformed-content",
    "dropped-field",
    "error-result",
    "rpc-error",
    "dropped-error",
    "malformed-envelope",
    "listing",
    "handshake",
]


@pytest.fixture
def host(tmp_path, monkeypatch):
    from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV

    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    value = Host(tmp_path, caps={"google": Decimal(180)})
    yield value
    value.ledger.close()


def _system(case, kind):
    return system_spec_from_raw(
        {
            "id": "synthetic",
            "adapter": "mcp-stdio",
            "command": ["{python}", str(SERVER), case, kind],
        }
    )


@pytest.mark.parametrize("kind", ["literal", "lexical"])
@pytest.mark.parametrize("case", CASES)
async def test_real_worker_stops_before_second_paid_dispatch(
    tmp_path, monkeypatch, host, case, kind
):
    """Removing the pre-validation scan must restore a second grant or a normal crash."""
    calls = tmp_path / "calls.txt"
    program = tmp_path / "worker.py"
    program.write_text(f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from pathlib import Path
from benchmarks.agentic import clients, worker
from tests.benchmarks.test_agentic_clients import (
    GeminiRecorder, _gemini_ok, _gemini_response)
from tests.benchmarks.fixtures.agentic.raw_result_server import token

class RecordingTransport(GeminiRecorder):
    def transport(self):
        import httpx
        original = super().transport()
        async def dispatch(request):
            assert token({kind!r}).encode() not in request.content
            assert request.headers['x-goog-api-key'] == token({kind!r})
            with Path({str(calls)!r}).open('a') as stream:
                stream.write('provider\\n')
            return await original.handle_async_request(request)
        return httpx.MockTransport(dispatch)

rec = RecordingTransport(
    _gemini_ok(_gemini_response([{{'functionCall': {{'name': 'search_docs', 'args': {{}}}}}}])),
    *[_gemini_ok(_gemini_response([{{'text': 'done'}}])) for _ in range(3)])
clients.make_client = lambda family, **kw: clients.GeminiClient(
    clients.gemini_sdk(token({kind!r}), 5, transport=rec.transport()), family.model_id, 32768)
async def limits(client):
    return None
clients.fetch_model_limits = limits
# Synthetic bootstrap stays in this worker; the host holds no credential.
worker.read_frame_fd = lambda fd: {{
    'type': 'bootstrap', 'env': {{'GOOGLE_API_KEY': token({kind!r})}}}}
raise SystemExit(worker.main_worker())
""")
    job = replace(
        _job(tmp_path, [], job_id="synthetic"),
        family=FAMILIES["gemini-3.8-flash"],
        system=_system(case, kind),
        trajectories=tuple(
            {"corpus_id": q, "repeat": 1, "prompt": "question"} for q in ("Q1", "Q2")
        ),
    )
    monkeypatch.setattr(
        LocalLauncher, "command", lambda self, job: ([sys.executable, "-I", str(program)], {})
    )
    monkeypatch.setattr(LocalLauncher, "supports", lambda self, family: True)
    service = host.service(job.family)
    with pytest.raises(KeyLeakError, match=WORKER_LEAK_REASON):
        await run_jobs(
            [job],
            launcher=LocalLauncher(),
            ledger=service,
            on_message=host.on_message,
            log_dir=tmp_path / "logs",
            cwd=REPO_ROOT,
        )
    initial = case in ("handshake", "listing")
    if initial:
        assert not calls.exists()
    else:
        assert calls.read_text().splitlines() == ["provider"]
    events = host.ledger.invocation_events()
    assert [e["event"] for e in events] == ([] if initial else ["reserve", "settle"])
    assert not [m for m in host.messages() if m["type"] in ("trajectory", "discarded", "done")]
    assert (
        await service.reserve(
            provider=job.family.provider,
            model_id=job.family.model_id,
            input_bound=1,
            output_bound=1,
        )
        is None
    )
    logs = b"".join(p.read_bytes() for p in (tmp_path / "logs").rglob("*.log"))
    assert b"synthetic server diagnostic retained" in logs
    from tests.benchmarks.fixtures.agentic.raw_result_server import token

    assert token(kind).encode() not in logs


@pytest.mark.parametrize("case", ["missing-clean", "valid", "rpc-clean"])
async def test_clean_sdk_outcomes_are_preserved(case):
    async with open_bridge(_system(case, "literal")) as bridge:
        if case == "missing-clean":
            with pytest.raises(BridgeCrash, match="did not return structured content"):
                await bridge.call("search_docs", {})
        elif case == "rpc-clean":
            result = await bridge.call("search_docs", {})
            assert result.is_error and result.text == "clean error"
        else:
            result = await bridge.call("search_docs", {})
            assert result.text == '{"answer":"clean answer"}'


@pytest.mark.parametrize("case", [
    "missing-structured", "invalid-structured", "malformed-content",
    "dropped-field", "error-result", "rpc-error", "dropped-error", "valid",
])
async def test_clean_raw_data_keeps_exact_sdk_validation_outcome(case):
    async def outcome(session):
        try:
            result = await session.call_tool("search_docs", {})
        except Exception as error:
            return type(error), str(error)
        return type(result), result.model_dump(mode="json", by_alias=True)

    params = StdioServerParameters(command=sys.executable, args=[str(SERVER), case, "clean"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.list_tools()
            expected = await outcome(session)
    async with open_bridge(_system(case, "clean")) as bridge:
        assert await outcome(bridge._session) == expected


@pytest.mark.parametrize("wrapped", ["group", "cancel-group", "mcp", "context"])
async def test_bridge_preserves_nested_key_failure(wrapped):
    leak = KeyLeakError("synthetic credential failure")
    if wrapped == "group":
        error = ExceptionGroup("MCP tasks", [RuntimeError("clean"), leak])
    elif wrapped == "cancel-group":
        error = BaseExceptionGroup("MCP tasks", [asyncio.CancelledError(), leak])
    else:
        error = MCPError(-32603, "clean") if wrapped == "mcp" else RuntimeError("clean")
        if wrapped == "context":
            error.__context__ = leak
            leak.__context__ = error  # implicit chains may have cycles
        else:
            error.__cause__ = leak
    bridge = Bridge(type("Session", (), {"call_tool": AsyncMock(side_effect=error)})(), {}, [])
    with pytest.raises(KeyLeakError) as caught:
        await bridge.call("search_docs", {})
    assert caught.value is leak


@pytest.mark.parametrize("cancelled", [False, True])
async def test_close_does_not_swallow_grouped_key_failure(cancelled):
    stack = AsyncExitStack()
    leak = KeyLeakError("synthetic credential failure")

    async def close():
        other = asyncio.CancelledError() if cancelled else RuntimeError("clean")
        raise BaseExceptionGroup("MCP teardown", [other, leak])

    stack.push_async_callback(close)
    with pytest.raises(KeyLeakError) as caught:
        await worker._close(stack)
    assert caught.value is leak


def test_mcp_reply_checks_escaped_values_before_duplicate_fields_are_discarded():
    from benchmarks.agentic.mcp_stdio import ReplyGuard
    from benchmarks.agentic.worker import ProtocolChannel

    channel = ProtocolChannel(-1, (b"opaque_marker",), -1, None, ())
    guard = ReplyGuard(channel.check_content)
    line = (
        rb'{"jsonrpc":"2.0","id":2,"result":{"content":[],'
        rb'"note":"prefix\u006fpaque_marker-suffix","note":"safe"}}'
    )
    with pytest.raises(KeyLeakError):
        guard.parse(line, StdioServerParameters(command="unused"))
    with pytest.raises(KeyLeakError):
        guard.raise_if_failed()
