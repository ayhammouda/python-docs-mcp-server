"""Literal bootstrap credentials stay inside synthetic workers, before any side effect."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from benchmarks.agentic.loop import Caps


@pytest.mark.parametrize("shaped", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        "prompt",
        "schema",
        "settings",
        "limits",
        "args",
        "later-args",
        "name",
        "id",
        "raw-args",
        "text",
        "structured",
        "hidden-text",
        "error-result",
        "mcp-error",
        "mcp-error-data",
        "closed",
        "exception",
        "beyond-cap",
        "truncation-marker",
        "provider-error",
        "answer",
    ],
)
def test_bootstrap_literal_stops_worker_before_outgoing_side_effects(
    tmp_path: Path, monkeypatch, capsys, shaped, source
):
    from benchmarks.__main__ import main
    from benchmarks.agentic.host import LocalLauncher
    from tests.benchmarks.test_agentic_run import _cli_args

    # Only the worker constructs and holds the synthetic bootstrap value. It is never
    # passed as argv, a host bootstrap, an environment file, or a public record.
    program = tmp_path / "literal_worker.py"
    calls = tmp_path / "calls.txt"
    program.write_text(
        dedent(f"""\
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
        from pathlib import Path
        from dataclasses import replace
        from mcp import MCPError
        from mcp.types import CallToolResult, TextContent, CONNECTION_CLOSED
        from benchmarks.agentic import clients, worker
        from benchmarks.agentic.types import ProviderCallError
        from tests.benchmarks.test_agentic_outgoing import _bridge
        from tests.benchmarks.test_agentic_loop import TOOL, ANSWER

        token = ('AIza' + 'q' * 35) if {shaped!r} else ('dummy_bootstrap_' + 'opaque')
        if {source!r} == 'truncation-marker':
            token = 'UTF-8 ' + 'bytes'
        value = 'prefix' + token + '-suffix'
        source = {source!r}
        def note(kind):
            with Path({str(calls)!r}).open('a') as file:
                file.write(kind + '\\n')
        # A private synthetic first frame, followed by the host's ordinary job on stdin.
        worker.read_frame_fd = lambda fd: {{
            'type': 'bootstrap', 'env': {{'GOOGLE_API_KEY': token}}}}
        read = worker.read_frame
        async def read_job(reader):
            frame = await read(reader)
            if source == 'prompt' and frame and frame.get('type') == 'job':
                frame['trajectories'][0]['prompt'] = value
            if source == 'limits' and frame and frame.get('type') == 'job':
                frame['family']['api'] = 'anthropic-messages'
            return frame
        worker.read_frame = read_job

        class RecordingClient(clients.ScriptedClient):
            def request_params(self):
                return {{'model': value}} if source == 'settings' else super().request_params()
            async def call(self, conv):
                note('provider')
                if source == 'provider-error':
                    raise ProviderCallError('server', value, status=500)
                turn = await super().call(conv)
                call = turn.tool_calls[0] if turn.tool_calls else None
                if source in ('args', 'later-args'):
                    bad = replace(call, arguments={{'query': value}})
                    turn = replace(turn, tool_calls=(bad,) if source == 'args' else (call, bad))
                elif source == 'name':
                    turn = replace(turn, tool_calls=(replace(call, name=value),))
                elif source == 'id':
                    turn = replace(turn, tool_calls=(replace(call, id=value),))
                elif source == 'raw-args':
                    turn = replace(turn, tool_calls=(replace(call, raw_arguments=value),))
                elif source == 'answer':
                    turn = replace(turn, text=value, tool_calls=())
                return turn
        clients.make_client = lambda family, **kw: RecordingClient(
            family.provider, value if source == 'limits' else family.model_id, [TOOL, ANSWER])
        if source == 'limits':
            RecordingClient.api = 'anthropic-messages'
        async def fetch_limits(client):
            note('limits')
            return None
        clients.fetch_model_limits = fetch_limits
        async def open_bridge(stack, system):
            text, structured = 'safe', None
            if source in ('text', 'error-result', 'hidden-text'):
                text = value
            if source in ('structured', 'mcp-error-data'):
                structured = {{'nested': [{{value: 'safe'}}]}}
            elif source == 'hidden-text':
                structured = {{'output': 'safe'}}
            elif source == 'beyond-cap':
                text = 'z' * 200 + value
            elif source == 'truncation-marker':
                text = 'z' * 200
            result = CallToolResult(content=[TextContent(type='text', text=text)],
                structuredContent=structured, isError=source == 'error-result')
            if source in ('mcp-error', 'closed', 'mcp-error-data'):
                result = MCPError(CONNECTION_CLOSED if source == 'closed' else -32603,
                    'safe' if source == 'mcp-error-data' else value, structured)
            elif source == 'exception':
                result = RuntimeError(value)
            bridge, call = _bridge(result)
            original = bridge._session.call_tool
            async def dispatch(*args, **kwargs):
                note('bridge')
                return await original(*args, **kwargs)
            bridge._session.call_tool = dispatch
            if source == 'schema':
                bridge.tools[0].input_schema['description'] = value
            return bridge
        worker._open = open_bridge
        raise SystemExit(worker.main_worker())
        """)
    )
    monkeypatch.setattr(
        LocalLauncher, "command", lambda self, job: ([sys.executable, "-I", str(program)], {})
    )
    monkeypatch.setattr("benchmarks.agentic.run.HOSTED_CAPS", Caps(result_bytes=100))
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
    seen = calls.read_text().splitlines() if calls.exists() else []
    initial = source in ("prompt", "schema", "settings", "limits")
    no_bridge = initial or source in (
        "args",
        "later-args",
        "name",
        "id",
        "raw-args",
        "provider-error",
        "answer",
    )
    assert seen.count("provider") == (0 if initial else 1)
    assert seen.count("bridge") == (0 if no_bridge else 1)
    assert seen.count("limits") == 0
    ledger = tmp_path / "ledger" / "anthropic.jsonl"
    events = (
        [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    )
    assert sum(e["event"] == "reserve" for e in events) == (0 if initial else 1)
    assert sum(e["event"] == "settle" for e in events) == (0 if initial else 1)
    captured = capsys.readouterr()
    assert "key leak" in captured.err
    # Reconstruct only for assertions after the worker exits; never give it to the host.
    token = ("AIza" + "q" * 35) if shaped else ("dummy_bootstrap_" + "opaque")
    if source == "truncation-marker":
        token = "UTF-8 bytes"
    assert token not in captured.err + captured.out
    assert not list((tmp_path / "run" / "transcripts").rglob("*.json"))
    for root in (tmp_path / "run", tmp_path / "ledger"):
        assert all(token.encode() not in p.read_bytes() for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("location", ["env", "command"])
async def test_bootstrapped_job_cannot_pass_worker_secret_to_mcp_child(monkeypatch, location):
    import asyncio
    from tempfile import TemporaryFile
    from unittest.mock import AsyncMock, Mock

    from benchmarks.agentic import bridge, clients, worker
    from benchmarks.agentic.protocol import WORKER_LEAK_REASON, encode_frame
    from tests.benchmarks.test_agentic_sandbox import FAMILIES, SYSTEMS

    token = "AI" + "za" + "g" * 35
    embedded = "prefix" + token + "-suffix"
    system = dict(SYSTEMS["python-docs"].raw)
    if location == "env":
        system["env"] = {"MCP_AUTH": embedded}
    else:
        system["command"] = [*system["command"], embedded]
    job = {
        "type": "job",
        "family": FAMILIES["gemini-3.8-flash"].raw,
        "system": system,
        "caps": {},
        "trajectories": [],
    }
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(job, where="job frame"))
    reader.feed_eof()
    client = Mock()
    client.request_params.return_value = {"model": job["family"]["model_id"]}
    create = Mock(return_value=client)
    limits = AsyncMock(return_value=None)
    launch = Mock(side_effect=RuntimeError("child launch intercepted"))
    monkeypatch.setattr(clients, "make_client", create)
    monkeypatch.setattr(clients, "fetch_model_limits", limits)
    monkeypatch.setattr(bridge, "stdio_client", launch)

    with TemporaryFile() as output:
        channel = worker.ProtocolChannel(output.fileno(), (token.encode(),), -1, None, ())
        code = await worker.run_worker(channel, reader, bootstrapped=True)
        output.seek(0)
        frame = json.loads(output.read()[8:])
    assert (code, frame) == (3, {"type": "error", "reason": WORKER_LEAK_REASON})
    create.assert_not_called()
    limits.assert_not_awaited()
    launch.assert_not_called()
