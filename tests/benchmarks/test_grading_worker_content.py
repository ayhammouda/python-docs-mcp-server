"""Worker-only content checks, synthetic bootstrap keys, and real SDK wire bodies."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from dataclasses import asdict, replace
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import httpx
import httpx2
import pytest

from benchmarks.agentic import count, worker
from benchmarks.agentic.clients import anthropic_sdk, gemini_sdk, openai_sdk
from benchmarks.agentic.host import GRADING_SYSTEM
from benchmarks.agentic.loop import Caps
from benchmarks.agentic.protocol import WORKER_LEAK_REASON, encode_frame
from benchmarks.agentic.types import ProviderCallError
from benchmarks.grading import worker_task
from benchmarks.grading.batch import (
    AnthropicBatchClient,
    BatchRequest,
    GeminiBatchClient,
    OpenAIBatchClient,
    missing,
)
from tests.benchmarks.test_grading_batch import (
    ANTHROPIC_BATCH,
    Router,
    _gemini_job,
    _json,
    _openai_batch,
)

LITERALS = ["opaque_bootstrap_only", 'opaque_é"\\bootstrap']
LEXICAL = "AIza" + "q" * 35


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    """Snapshot only a synthetic private bootstrap, just as protect_output does."""

    def build(secret=LITERALS[0]):
        monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
        worker.apply_bootstrap({"env": {"ANTHROPIC_API_KEY": secret}})
        sink = (tmp_path / "frames").open("w+b", buffering=0)
        channel = worker.ProtocolChannel(
            sink.fileno(), (secret.encode(),), -1, threading.Thread(), ()
        )
        return channel, sink

    return build


async def run(channel, sink, job, replies):
    reader = asyncio.StreamReader()
    for reply in replies:
        # Private host-to-worker input: deliberately no host scan of literal keys.
        reader.feed_data(encode_frame(reply))
    reader.feed_eof()
    code = await worker.run_worker(channel, reader, job, bootstrapped=True)
    sink.seek(0)
    frames = []
    while header := sink.read(8):
        frames.append(json.loads(sink.read(int.from_bytes(header, "big"))))
    sink.close()
    return code, frames


def job(task):
    return {
        "type": "job",
        "task": task,
        "family": count.count_family().raw,
        "system": GRADING_SYSTEM.raw,
        "caps": asdict(Caps()),
        "payload": {"effort": "low"},
    }


def grant(request=None):
    request = request or BatchRequest("i1", "Grade.", "<answer>safe</answer>", {}, "grade")
    return {"type": "grant", "unit_id": "held-unit", "items": [{**asdict(request), "attempt": 1}]}


class RecordingBatch:
    def __init__(self, poison=None, stage=None):
        self.poison, self.stage = poison, stage
        self.calls = []

    def request_params(self):
        return {"model": self.poison if self.stage == "settings" else "test-model"}

    async def submit(self, requests):
        self.calls.append("submit")
        if self.stage == "submit-error":
            raise ProviderCallError("server", self.poison, status=500)
        return self.poison if self.stage == "batch-id" else "batch-1"

    async def poll(self, batch_id):
        self.calls.append("poll")
        if self.stage == "poll-error":
            raise ProviderCallError("connection", self.poison)
        return self.poison if self.stage == "state" else "ended"

    async def results(self, batch_id, item_ids):
        self.calls.append("results")
        if self.stage == "results-error":
            raise ProviderCallError("server", self.poison, status=500)
        result = missing("i1")
        if self.stage == "raw-result":
            result = replace(result, raw={"nested": ["z" * 2000 + ": " + self.poison]})
        return [result]


@pytest.mark.parametrize("token", [*LITERALS, LEXICAL])
@pytest.mark.parametrize("field", ["user", "schema", "system", "schema_name", "item_id"])
async def test_batch_grant_checked_before_upload_or_submission(boundary, monkeypatch, token, field):
    channel, sink = boundary(token if token != LEXICAL else LITERALS[0])
    client = RecordingBatch()
    monkeypatch.setattr(worker_task, "make_batch_client", lambda *a, **k: client)
    wire = grant()
    wire["items"][0][field] = (
        {"properties": {token: {"type": "string"}}} if field == "schema" else f"answer: {token}."
    )
    code, frames = await run(channel, sink, job("batches"), [wire, {"type": "no_more_items"}])
    assert client.calls == []
    assert code == 3
    # Content arrives WITH a held reservation; the worker must terminate for host teardown.
    assert [f["type"] for f in frames] == ["reserve", "error"]
    assert frames[-1]["reason"] == WORKER_LEAK_REASON


@pytest.mark.parametrize("token", [*LITERALS, LEXICAL])
@pytest.mark.parametrize(
    "stage,calls",
    [
        ("settings", []),
        ("submit-error", ["submit"]),
        ("batch-id", ["submit"]),
        ("poll-error", ["submit", "poll"]),
        ("state", ["submit", "poll"]),
        ("results-error", ["submit", "poll", "results"]),
        ("raw-result", ["submit", "poll", "results"]),
    ],
)
async def test_batch_detection_is_terminal_before_retry_or_more_work(
    boundary, monkeypatch, capsys, token, stage, calls
):
    channel, sink = boundary(token if token != LEXICAL else LITERALS[0])
    client = RecordingBatch(token, stage)
    monkeypatch.setattr(worker_task, "make_batch_client", lambda *a, **k: client)
    # A raw leak must be detected BEFORE oversized-raw compaction can erase it.
    monkeypatch.setattr(worker_task, "RESULT_CHUNK_BYTES", 600)

    async def no_wait(*a):
        pytest.fail("detection must precede backoff")

    original = worker_task.run_batch_task
    monkeypatch.setattr(worker_task, "run_batch_task", lambda *a: original(*a, sleep=no_wait))
    code, frames = await run(
        channel,
        sink,
        job("batches"),
        [grant(), {"type": "ack"}, {"type": "ack"}, {"type": "no_more_items"}],
    )
    assert code == 3
    assert client.calls == calls
    assert sum(f["type"] == "reserve" for f in frames) == (0 if stage == "settings" else 1)
    assert not any(f["type"] in ("batch_results", "settle", "settle_batch", "done") for f in frames)
    assert frames[-1] == {"type": "error", "reason": WORKER_LEAK_REASON}
    assert token not in json.dumps(frames, ensure_ascii=False)
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


def envelope_wire():
    return {
        "kind": "envelope",
        "item_id": "i1",
        "name": "premise",
        "envelope": asdict(count.Envelope("", None, [{"role": "user", "content": "safe"}])),
    }


@pytest.mark.parametrize("token", [*LITERALS, LEXICAL])
@pytest.mark.parametrize("source", ["premise", "normalized-answer", "late-tool-result", "settings"])
async def test_count_scans_whole_item_before_first_endpoint(boundary, monkeypatch, token, source):
    channel, sink = boundary(token if token != LEXICAL else LITERALS[0])
    client = count.ScriptedCountClient()
    monkeypatch.setattr(count, "make_count_client", lambda *a, **k: client)
    j = job("items")
    wire = envelope_wire()
    if source == "settings":
        j["family"]["model_id"] = token
    elif source == "premise":
        wire["envelope"]["messages"][0]["content"] = {"nested": [{token: "value"}]}
    else:
        messages = [
            {"role": "user", "content": "safe"},
            {"role": "assistant", "content": [{"type": "text", "text": "safe"}]},
        ]
        if source == "normalized-answer":
            messages[1]["content"][0]["text"] = token
        else:
            messages.append(
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": token}],
                }
            )
        wire = {
            "kind": "trajectory",
            "item_id": "i1",
            "item": asdict(count.CountItem("cell", "Q1", "model", "", None, messages, [1], [], 0)),
        }
    code, frames = await run(
        channel, sink, j, [{"type": "item", "item": wire}, {"type": "no_more_items"}]
    )
    assert code == 3
    assert client.seen == []
    assert [f["type"] for f in frames] == (
        ["error"] if source == "settings" else ["next_item", "error"]
    )
    assert frames[-1]["reason"] == WORKER_LEAK_REASON


@pytest.mark.parametrize("token", [*LITERALS, LEXICAL])
@pytest.mark.parametrize("result", [False, True])
@pytest.mark.parametrize("trajectory", [False, True])
async def test_count_error_or_result_stops_before_retry_and_next_item(
    boundary, monkeypatch, capsys, token, result, trajectory
):
    channel, sink = boundary(token if token != LEXICAL else LITERALS[0])
    calls = []

    async def endpoint(envelope):
        calls.append(envelope)
        if result:
            return token
        raise ProviderCallError("server", token, status=500)

    monkeypatch.setattr(count, "make_count_client", lambda *a, **k: SimpleNamespace(count=endpoint))
    wire = envelope_wire()
    if trajectory:
        wire = {
            "kind": "trajectory",
            "item_id": "i1",
            "item": asdict(
                count.CountItem(
                    "cell",
                    "Q1",
                    "model",
                    "",
                    None,
                    [
                        {"role": "user", "content": "safe"},
                        {"role": "assistant", "content": [{"type": "text", "text": "safe"}]},
                    ],
                    [1],
                    [],
                    0,
                )
            ),
        }
    code, frames = await run(
        channel,
        sink,
        job("items"),
        [{"type": "item", "item": wire}, {"type": "no_more_items"}],
    )
    assert code == 3
    assert len(calls) == 1
    assert [f["type"] for f in frames] == ["next_item", "error"]
    assert frames[-1]["reason"] == WORKER_LEAK_REASON
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
@pytest.mark.parametrize("token", [None, *LITERALS, LEXICAL])
@pytest.mark.parametrize("source", ["user", "schema"])
async def test_worker_boundary_guards_real_sdk_structured_batch_serialization(
    boundary, monkeypatch, provider, token, source
):
    channel, sink = boundary(token if token in LITERALS else LITERALS[0])
    # Auth is intentionally outside every inspected body. A safe control must reach
    # the transport while using that same synthetic bootstrap as its SDK credential.
    file_body = {
        "id": "file-in",
        "object": "file",
        "bytes": 1,
        "created_at": 0,
        "filename": "batch.jsonl",
        "purpose": "batch",
        "status": "processed",
    }
    if provider == "anthropic":
        router = Router([("POST", r"/v1/messages/batches$", _json(httpx2, ANTHROPIC_BATCH))])
        sdk = anthropic_sdk(LITERALS[0], 5, transport=httpx2.MockTransport(router))
        client = AnthropicBatchClient(sdk, "claude-haiku-5-5", 16384, "low")
    elif provider == "openai":
        router = Router(
            [
                ("POST", r"/v1/files$", _json(httpx2, file_body)),
                ("POST", r"/v1/batches$", _json(httpx2, _openai_batch("validating"))),
            ]
        )
        sdk = openai_sdk(LITERALS[0], 5, transport=httpx2.MockTransport(router))
        client = OpenAIBatchClient(sdk, "gpt-6-luna", 16384, "none")
    else:
        router = Router(
            [
                (
                    "POST",
                    r"/upload/v1beta/files$",
                    lambda _: httpx.Response(
                        200,
                        json={"file": {"name": "files/in"}},
                        headers={
                            "x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=1"
                        },
                    ),
                ),
                (
                    "POST",
                    r"upload_id=1",
                    _json(
                        httpx,
                        {"file": {"name": "files/in"}},
                        headers={"x-goog-upload-status": "final"},
                    ),
                ),
                ("POST", r":batchGenerateContent$", _json(httpx, _gemini_job("JOB_STATE_PENDING"))),
            ]
        )
        sdk = gemini_sdk(LITERALS[0], 5, transport=httpx.MockTransport(router))
        client = GeminiBatchClient(sdk, "gemini-3.1-flash-lite", 16384, "minimal")

    async def poll(_):
        return "ended"

    async def results(*_):
        return [missing("i1")]

    monkeypatch.setattr(client, "poll", poll)
    monkeypatch.setattr(client, "results", results)
    monkeypatch.setattr(worker_task, "make_batch_client", lambda *a, **k: client)
    value = token or 'structured é "quote" \\slash'
    property_name = value if source == "schema" else "safe"
    request = BatchRequest(
        "i1",
        "Grade.",
        "<answer>" + (value if source == "user" else "safe") + "</answer>",
        {"type": "object", "properties": {property_name: {"type": "string"}}},
        "grade",
    )
    code, frames = await run(
        channel,
        sink,
        job("batches"),
        [grant(request), {"type": "ack"}, {"type": "ack"}, {"type": "no_more_items"}],
    )
    assert code == (3 if token else 0)
    if token:
        assert router.seen == []  # no JSON body, multipart upload, or billed creation
    else:
        assert len(router.seen) == {"anthropic": 1, "openai": 2, "google": 3}[provider]
        if provider == "anthropic":
            schema = json.loads(router.seen[0].content)["requests"][0]["params"]["output_config"][
                "format"
            ]["schema"]
        elif provider == "openai":
            line = next(
                line
                for line in router.seen[0].content.splitlines()
                if line.startswith(b'{"custom_id"')
            )
            schema = json.loads(line)["body"]["text"]["format"]["schema"]
        else:
            schema = json.loads(router.seen[1].content)["request"]["generation_config"][
                "response_json_schema"
            ]
        assert schema == {"type": "object", "properties": {property_name: {"type": "string"}}}
        assert frames[-1] == {"type": "done"}


@pytest.mark.parametrize("token", [None, *LITERALS, LEXICAL])
@pytest.mark.parametrize("source", ["schema", "tool-result"])
async def test_count_worker_guards_real_sdk_structured_body(boundary, monkeypatch, token, source):
    channel, sink = boundary(token if token in LITERALS else LITERALS[0])
    bodies = []

    def transport(request):
        bodies.append(json.loads(request.content))  # never read auth headers
        return httpx2.Response(200, json={"input_tokens": 12})

    sdk = anthropic_sdk(LITERALS[0], 5, transport=httpx2.MockTransport(transport))
    client = count.AnthropicCountClient(sdk, "test-model")
    monkeypatch.setattr(count, "make_count_client", lambda *a, **k: client)
    wire = envelope_wire()
    property_name = (token or "safe") if source == "schema" else "safe"
    wire["envelope"]["tools"] = [
        {
            "name": "lookup",
            "input_schema": {"type": "object", "properties": {property_name: {"type": "string"}}},
        }
    ]
    if source == "tool-result":
        wire["envelope"]["messages"] = [
            {"role": "user", "content": "Look up docs."},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "c1", "name": "lookup", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": token or "safe"}
                ],
            },
        ]
    code, frames = await run(
        channel, sink, job("items"), [{"type": "item", "item": wire}, {"type": "no_more_items"}]
    )
    await sdk.close()
    assert code == (3 if token else 0)
    assert len(bodies) == (0 if token else 1)
    if not token:
        assert bodies[0]["tools"][0]["input_schema"]["properties"] == {"safe": {"type": "string"}}
        if source == "tool-result":
            assert bodies[0]["messages"][-1]["content"][0]["content"] == "safe"
        assert next(f for f in frames if f["type"] == "counted")["tokens"] == 12


@pytest.mark.parametrize("stage", ["settings", "grant", "raw-result", "results-error"])
async def test_bootstrapped_batch_process_stops_and_host_settles_held_unit(tmp_path, stage):
    from benchmarks.agentic.host import LocalLauncher
    from benchmarks.agentic.protocol import KeyLeakError
    from tests.benchmarks.test_grading_jobs import Judges

    # The host never receives the worker's synthetic bootstrap value. Poison is
    # introduced only after a private bootstrap and, for grant, AFTER reservation.
    program = tmp_path / "bootstrap_worker.py"
    calls = tmp_path / "calls"
    program.write_text(
        dedent(f"""\
        import sys
        sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})
        from pathlib import Path
        from benchmarks.agentic import worker
        token = 'worker_private_' + 'synthetic'
        worker.read_frame_fd = lambda fd: {{'type': 'bootstrap',
            'env': {{'ANTHROPIC_API_KEY': token}}}}
        protect = worker.protect_output
        clients = []
        def setup():
            channel = protect()
            from benchmarks.grading import worker_task
            from tests.benchmarks.test_grading_worker_content import RecordingBatch
            client = RecordingBatch(token, {stage!r})
            clients.append(client)
            worker_task.make_batch_client = lambda *a, **k: client
            read = worker.read_frame
            async def receive(reader):
                frame = await read(reader)
                if {stage!r} == 'grant' and frame and frame.get('type') == 'grant':
                    frame['items'][0]['user'] = 'answer: ' + token
                return frame
            worker.read_frame = receive
            return channel
        worker.protect_output = setup
        code = worker.main_worker()
        Path({str(calls)!r}).write_text('\\n'.join(clients[0].calls))
        raise SystemExit(code)
    """)
    )

    class SyntheticLauncher(LocalLauncher):
        def command(self, job):
            return [sys.executable, "-I", str(program)], {}

    judges = Judges(tmp_path)
    try:
        with pytest.raises(KeyLeakError):
            await judges.run(judges.job(), launcher=SyntheticLauncher())
        expected = [] if stage in ("settings", "grant") else ["submit", "poll", "results"]
        assert calls.read_text().splitlines() == expected
        reserves, settlements = judges.events("reserve"), judges.events("settle")
        held = 0 if stage == "settings" else 1
        assert len(reserves) == len(settlements) == held
        if held:
            assert settlements[0]["unit_id"] == reserves[0]["unit_id"]
            assert settlements[0]["outcome"] == "worker_lost"
            assert judges.ledger.spent("anthropic") > 0  # conservative existing teardown
        assert not judges.frames("batch_results")
        for path in tmp_path.rglob("*"):
            if path.is_file() and path != program:
                assert b"worker_private_synthetic" not in path.read_bytes()
    finally:
        judges.ledger.close()


@pytest.mark.parametrize("token", [*LITERALS, LEXICAL])
def test_premise_command_uses_checked_worker_before_count_transport(
    tmp_path, boundary, monkeypatch, capsys, token
):
    from benchmarks import __main__ as cli
    from benchmarks.agentic import live
    from benchmarks.agentic.protocol import KeyLeakError
    from tests.benchmarks.test_agentic_count_run import premise_module

    channel, sink = boundary(token if token in LITERALS else LITERALS[0])
    # The bootstrap snapshot belongs to the worker; the command's host remains keyless.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    module = premise_module()
    bodies, frames = [], []

    def transport(request):
        bodies.append(request.content)
        return httpx2.Response(200, json={"input_tokens": 7})

    sdk = anthropic_sdk(LITERALS[0], 5, transport=httpx2.MockTransport(transport))
    monkeypatch.setattr(
        count, "make_count_client", lambda *a, **k: count.AnthropicCountClient(sdk, "test-model")
    )
    monkeypatch.setattr(live, "authorize_live", lambda _: None)
    monkeypatch.setattr(cli, "_container_launcher", lambda *a: object())

    async def local_worker(jobs, **kwargs):
        # Replace the external launcher only; run the actual items branch and SDK.
        assigned = jobs[0].source.take(None, 1)[0]
        assigned["envelope"]["messages"][0]["content"] = "answer: " + token
        j = job("items")
        j["family"] = jobs[0].family.raw
        code, actual = await run(
            channel, sink, j, [{"type": "item", "item": assigned}, {"type": "no_more_items"}]
        )
        frames.extend(actual)
        await sdk.close()
        if code == 3:
            raise KeyLeakError(WORKER_LEAK_REASON)
        pytest.fail("the premise worker accepted poisoned content")

    monkeypatch.setattr(module, "run_jobs", local_worker)
    assert (
        module.main(
            [
                "--scratch",
                str(tmp_path / "scratch"),
                "--image",
                "synthetic",
                "--keys-file",
                str(tmp_path / "never-opened"),
            ]
        )
        == 3
    )
    assert bodies == []
    assert [f["type"] for f in frames] == ["next_item", "error"]
    assert not list((tmp_path / "scratch").rglob("results/*.json"))
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert WORKER_LEAK_REASON in captured.err


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google", "count"])
@pytest.mark.parametrize("token", [*LITERALS, LEXICAL])
async def test_sdk_error_body_checked_before_formatting_can_hide_literal(
    boundary, monkeypatch, provider, token
):
    channel, sink = boundary(token if token in LITERALS else LITERALS[0])
    bodies = []

    def transport(request):
        bodies.append(request.content)  # content only, never SDK authentication
        body = {"error": {"message": token, "type": "api_error", "code": 500, "status": "INTERNAL"}}
        kind = httpx if provider == "google" else httpx2
        return kind.Response(500, json=body)

    if provider == "google":
        sdk = gemini_sdk(LITERALS[0], 5, transport=httpx.MockTransport(transport))
        client = GeminiBatchClient(sdk, "gemini-3.1-flash-lite", 100, "minimal")
    elif provider == "openai":
        sdk = openai_sdk(LITERALS[0], 5, transport=httpx2.MockTransport(transport))
        client = OpenAIBatchClient(sdk, "gpt-6-luna", 100, "none")
    else:
        sdk = anthropic_sdk(LITERALS[0], 5, transport=httpx2.MockTransport(transport))
        client = (
            count.AnthropicCountClient(sdk, "test-model")
            if provider == "count"
            else AnthropicBatchClient(sdk, "claude-haiku-5-5", 100, "low")
        )
    if provider == "count":
        monkeypatch.setattr(count, "make_count_client", lambda *a, **k: client)
        replies = [{"type": "item", "item": envelope_wire()}, {"type": "no_more_items"}]
        j = job("items")
    else:
        monkeypatch.setattr(worker_task, "make_batch_client", lambda *a, **k: client)

        async def no_wait(*a):
            pytest.fail("a leaked SDK error must not retry")

        original = worker_task.run_batch_task
        monkeypatch.setattr(worker_task, "run_batch_task", lambda *a: original(*a, sleep=no_wait))
        replies = [grant(), {"type": "ack"}, {"type": "no_more_items"}]
        j = job("batches")
    code, frames = await run(channel, sink, j, replies)
    assert code == 3
    assert len(bodies) == 1
    assert [f["type"] for f in frames] == [
        "next_item" if provider == "count" else "reserve",
        "error",
    ]
    assert frames[-1]["reason"] == WORKER_LEAK_REASON
