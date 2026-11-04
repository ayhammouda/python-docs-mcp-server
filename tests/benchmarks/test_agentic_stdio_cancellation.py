"""SDK reader cancellation must stop a job even while its host owes a reply."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic import clients, mcp_stdio, worker
from benchmarks.agentic.bridge import system_spec_from_raw
from benchmarks.agentic.host import LocalLauncher, run_jobs
from benchmarks.agentic.protocol import FrameTooLarge, KeyLeakError
from tests.benchmarks.test_agentic_host import ANSWER, REPO_ROOT, Host, _job
from tests.benchmarks.test_agentic_loop import TOOL

SERVER = Path(__file__).parent / "fixtures/agentic/grant_fault_server.py"


@pytest.fixture
def host(tmp_path, monkeypatch):
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    value = Host(tmp_path)
    yield value
    value.ledger.close()


@pytest.mark.parametrize(
    "kind,code,error", [("frame", 4, FrameTooLarge), ("credential", 3, KeyLeakError)]
)
@pytest.mark.parametrize("boundary", ["first-grant", "next-grant", "settlement"])
async def test_sdk_fault_while_host_owes_reply_stops_run(
    tmp_path, monkeypatch, host, kind, code, error, boundary
):
    marker, calls, outcome = (tmp_path / name for name in ("notify", "calls", "outcome"))
    program = tmp_path / "worker.py"
    program.write_text(f"""
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import json
from pathlib import Path
from benchmarks.agentic import clients, mcp_stdio, worker
from benchmarks.agentic.protocol import KeyLeakError
mcp_stdio.MAX_FRAME_BYTES = 512
check = worker.ProtocolChannel.check_content
def check_content(self, content):
    check(self, content)
    if b'orchard' in content:
        raise KeyLeakError('benign content-check sentinel')
worker.ProtocolChannel.check_content = check_content
call = clients.ScriptedClient.call
async def record_call(self, conv):
    with Path({str(calls)!r}).open('a') as file:
        file.write('provider\\n')
    return await call(self, conv)
clients.ScriptedClient.call = record_call
processes = []
create = mcp_stdio._create_platform_compatible_process
async def capture(**kwargs):
    process = await create(**kwargs)
    processes.append(process)
    return process
mcp_stdio._create_platform_compatible_process = capture
code = worker.main_worker()
Path({str(outcome)!r}).write_text(json.dumps({{
    'code': code, 'children': [p.returncode for p in processes]}}))
raise SystemExit(code)
""")
    system = system_spec_from_raw(
        {
            "id": "synthetic",
            "adapter": "mcp-stdio",
            "command": ["{python}", str(SERVER), str(marker), kind],
        }
    )
    job = _job(tmp_path, [TOOL, ANSWER, ANSWER], system=system)
    processes = []
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    monkeypatch.setattr(
        LocalLauncher, "command", lambda self, job: ([sys.executable, "-I", str(program)], {})
    )
    service = host.service(job.family)
    operation = "settle" if boundary == "settlement" else "reserve"
    original = getattr(service, operation)
    requests = 0

    async def delayed_reply(*args, **kwargs):
        nonlocal requests
        requests += 1
        if requests == (2 if boundary == "next-grant" else 1):
            marker.touch()
            # The worker must terminate without needing the outstanding host reply.
            await asyncio.wait_for(processes[0].wait(), 5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, operation, delayed_reply)
    async with asyncio.timeout(10):
        with pytest.raises(error):
            await run_jobs(
                [job],
                launcher=LocalLauncher(),
                ledger=service,
                on_message=host.on_message,
                log_dir=tmp_path / "logs",
                cwd=REPO_ROOT,
            )
    assert processes[0].returncode == code
    assert json.loads(outcome.read_text()) == {"code": code, "children": [0]}
    assert (calls.read_text().splitlines() if calls.exists() else []) == (
        [] if boundary == "first-grant" else ["provider"]
    )
    assert [m["type"] for m in host.messages()] == ["system_info"]
    events = host.ledger.invocation_events()
    assert [e["event"] for e in events] == (
        ["reserve", "settle", "reserve", "settle"]
        if boundary == "next-grant"
        else ["reserve", "settle"]
    )
    assert [e["outcome"] for e in events if e["event"] == "settle"] == {
        "first-grant": ["worker_lost"],
        "next-grant": ["ok", "worker_lost"],
        "settlement": ["ok"],
    }[boundary]
    assert (
        await service.reserve(
            provider=job.family.provider,
            model_id=job.family.model_id,
            input_bound=1,
            output_bound=1,
        )
        is None
    )


@pytest.mark.parametrize("kind", ["frame", "credential"])
@pytest.mark.parametrize("fault", [False, True])
@pytest.mark.parametrize("cancellation", ["operator", "repeated", "timeout"])
async def test_operator_cancellation_wins_and_drains_sdk_job(
    tmp_path, monkeypatch, kind, fault, cancellation
):
    marker = tmp_path / "notify"
    system = system_spec_from_raw(
        {
            "id": "synthetic",
            "adapter": "mcp-stdio",
            "command": ["{python}", str(SERVER), str(marker), kind],
        }
    )
    job = _job(tmp_path, [TOOL, ANSWER], system=system)
    monkeypatch.setattr(mcp_stdio, "MAX_FRAME_BYTES", 512)
    reader = asyncio.StreamReader()
    messages, processes = [], []
    create = mcp_stdio._create_platform_compatible_process

    async def capture(**kwargs):
        process = await create(**kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mcp_stdio, "_create_platform_compatible_process", capture)
    client = clients.ScriptedClient("anthropic", "scripted-1", [TOOL, ANSWER])
    monkeypatch.setattr(clients, "make_client", lambda *args, **kwargs: client)

    def interrupt():
        if cancellation == "timeout":
            timer.reschedule(asyncio.get_running_loop().time())
        else:
            owner.cancel("operator interrupt")
            if cancellation == "repeated":
                asyncio.get_running_loop().call_soon(owner.cancel, "later interrupt")

    class Channel:
        def check_content(self, content):
            if b"orchard" in content:
                raise KeyLeakError("benign content-check sentinel")

        def send(self, message):
            messages.append(message)
            if message["type"] == "reserve":
                if fault:
                    marker.touch()
                else:
                    interrupt()

    close = worker._close

    async def cancel_during_unwind(stack, pending=None):
        if fault and pending is not None:
            interrupt()
        await close(stack, pending)

    monkeypatch.setattr(worker, "_close", cancel_during_unwind)
    before = asyncio.all_tasks()
    timer = asyncio.timeout(None)

    async def invoke():
        async with timer:
            return await worker.run_worker(
                Channel(),
                reader,
                {
                    "type": "job",
                    "family": job.family.raw,
                    "system": job.system.raw,
                    "caps": {},
                    "trajectories": list(job.trajectories),
                },
            )

    owner = asyncio.create_task(invoke())
    with pytest.raises(
        TimeoutError if cancellation == "timeout" else asyncio.CancelledError,
        match=None if cancellation == "timeout" else "operator interrupt",
    ):
        await asyncio.wait_for(owner, 5)
    assert timer.expired() == (cancellation == "timeout")
    assert owner.done()
    assert len(processes) == 1 and processes[0].returncode is not None
    assert [m["type"] for m in messages] == ["system_info", "reserve"]
    await asyncio.sleep(0)  # Run done callbacks before checking for abandoned SDK tasks.
    assert not (asyncio.all_tasks() - before - {asyncio.current_task()})


async def test_unrecognized_job_cancellation_is_not_a_frame_failure(monkeypatch):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError("job cancelled without a recognized reply failure")

    monkeypatch.setattr(worker, "_run_job", cancelled)
    with pytest.raises(asyncio.CancelledError, match="without a recognized reply failure"):
        await worker.run_worker(None, None)
