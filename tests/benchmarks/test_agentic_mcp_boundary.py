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

from benchmarks.agentic import mcp_stdio, worker
from benchmarks.agentic.bridge import Bridge, BridgeCrash, open_bridge, system_spec_from_raw
from benchmarks.agentic.host import LocalLauncher, run_jobs
from benchmarks.agentic.protocol import WORKER_LEAK_REASON, FrameTooLarge, KeyLeakError
from tests.benchmarks.test_agentic_host import REPO_ROOT, Host, _job
from tests.benchmarks.test_agentic_sandbox import FAMILIES

SERVER = Path(__file__).parent / "fixtures/agentic/raw_result_server.py"
CASES = [
    "missing-structured",
    "invalid-structured",
    "malformed-content",
    "dropped-field",
    "escaped-field",
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


@pytest.mark.parametrize("kind", ["literal", "bound-shaped", "lexical"])
@pytest.mark.parametrize("case", CASES)
async def test_real_worker_stops_before_second_paid_dispatch(
    tmp_path, monkeypatch, host, case, kind
):
    """Removing the pre-validation scan must restore a second grant or a normal crash."""
    calls = tmp_path / "calls.txt"
    exit_code = tmp_path / "exit-code.txt"
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
code = worker.main_worker()
Path({str(exit_code)!r}).write_text(str(code))
raise SystemExit(code)
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
    assert exit_code.read_text() == "3"


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


@pytest.mark.parametrize(
    "case",
    [
        "missing-structured",
        "invalid-structured",
        "malformed-content",
        "dropped-field",
        "error-result",
        "rpc-error",
        "dropped-error",
        "valid",
    ],
)
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


async def test_caller_cancellation_reaps_only_the_owned_stdio_process(monkeypatch):
    processes = []
    create = mcp_stdio._create_platform_compatible_process

    async def capture(**kwargs):
        process = await create(**kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mcp_stdio, "_create_platform_compatible_process", capture)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.5):
            async with open_bridge(_system("wait", "clean")) as bridge:
                await bridge.call("search_docs", {})
    assert len(processes) == 1 and processes[0].returncode is not None
    # A cancelled caller can immediately create and use a fresh, independent session.
    async with open_bridge(_system("valid", "clean")) as bridge:
        assert (await bridge.call("search_docs", {})).text == '{"answer":"clean answer"}'
    assert len(processes) == 2 and processes[1].returncode == 0


async def test_stdio_line_limit_bounds_buffer_and_releases_process(monkeypatch):
    monkeypatch.setattr(mcp_stdio, "MAX_FRAME_BYTES", 512)
    # The request cannot wait indefinitely when the raw peer sends no valid reply.
    with pytest.raises(BaseExceptionGroup) as caught:
        async with asyncio.timeout(5):
            async with open_bridge(_system("oversize", "clean")) as bridge:
                with pytest.raises(BridgeCrash):
                    await bridge.call("search_docs", {})
    assert caught.value.subgroup(FrameTooLarge) is not None


async def test_broken_stdio_scans_the_pending_partial_line(monkeypatch):
    from contextlib import suppress
    from types import SimpleNamespace

    import anyio

    from benchmarks.agentic.protocol import find_key_leak

    async def broken_stdout():
        yield b'{"result":"prefixopaque_marker-suffix'
        raise anyio.BrokenResourceError

    process = SimpleNamespace(stdout=broken_stdout(), stdin=object())
    monkeypatch.setattr(
        mcp_stdio, "_create_platform_compatible_process", AsyncMock(return_value=process)
    )
    monkeypatch.setattr(mcp_stdio, "_stop_server_process", AsyncMock())
    channel = worker.ProtocolChannel(-1, (b"opaque_marker",), -1, None, ())
    guard = mcp_stdio.ReplyGuard(channel.check_content)
    try:
        async with mcp_stdio.checked_stdio_client(
            StdioServerParameters(command="unused"), errlog=sys.stderr, guard=guard
        ) as (read, _):
            with suppress(anyio.EndOfStream):
                await read.receive()
    except BaseExceptionGroup as group:
        assert find_key_leak(group) is guard.failure
    with pytest.raises(KeyLeakError):
        guard.raise_if_failed()
