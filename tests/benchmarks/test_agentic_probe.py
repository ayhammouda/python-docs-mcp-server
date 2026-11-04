"""The isolation probe: in-container checks, the host verdict and the probe containers.

Listeners and fake proxies bind 127.0.0.1 only; every docker command goes to FakeDocker.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import threading
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import benchmarks.agentic.probe as probe
from benchmarks.__main__ import main
from benchmarks.agentic.egress import ProxyEvent, write_egress_summary
from benchmarks.agentic.host import run_jobs
from benchmarks.agentic.ledger import Ledger, LedgerService
from benchmarks.agentic.probe import (
    IsolationProbeFailed,
    control_probe,
    host_addresses,
    isolation_probe,
    probe_verdict,
    run_probe_checks,
)
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic.sandbox import ContainerLauncher, Sandbox
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_egress import PROBE_LINES, PYPI_LINE
from tests.benchmarks.test_agentic_run import _cli
from tests.benchmarks.test_agentic_sandbox import (
    SYNTHETIC,
    FakeDocker,
    LateCreate,
    _config,
    _done,
    _job,
    _verified,
)

TOKEN = "probe-token-0123456789abcdef"


@pytest.fixture(autouse=True)
def _loopback_canary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "_CANARY_BIND", "127.0.0.1")  # never every interface in a test


DENIED = ("example.com", 443)
CANARY = ("host.docker.internal", 50000)


@contextmanager
def _listener(reply: Callable[[bytes], bytes] | None = None) -> Iterator[tuple[int, list[bytes]]]:
    """A TCP server on 127.0.0.1 that records what each connection sends, and may answer."""
    server = socket.create_server(("127.0.0.1", 0))
    received: list[bytes] = []
    stop = threading.Event()

    def serve() -> None:
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            with conn:
                conn.settimeout(2)
                data = b""
                try:
                    while b"\r\n\r\n" not in data and (chunk := conn.recv(4096)):
                        data += chunk
                except TimeoutError:
                    pass  # a client that sends no blank line: keep what arrived
                received.append(data)
                if reply is not None:
                    conn.sendall(reply(data))

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1], received
    finally:
        stop.set()
        thread.join(5)
        server.close()


def _check(results: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [r for r in results if r["check"] == name]


def test_direct_check_fails_and_sends_the_token_when_something_listens() -> None:
    with _listener() as (port, received):
        results = run_probe_checks({"direct": [["127.0.0.1", port]], "token": TOKEN})
    (direct,) = _check(results, "direct")
    assert direct["passed"] is False and direct["target"] == f"127.0.0.1:{port}"
    assert any(f"Bearer {TOKEN}".encode() in data for data in received)


def test_direct_check_passes_when_nothing_listens() -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        (direct,) = _check(
            run_probe_checks({"direct": [["127.0.0.1", port]], "token": TOKEN}), "direct"
        )
    assert direct["passed"] is True


def test_dns_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def unresolvable(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(socket.EAI_NONAME, "no name")

    monkeypatch.setattr(probe.socket, "getaddrinfo", unresolvable)
    (dns,) = _check(run_probe_checks({"dns_name": "pypi.org"}), "dns")
    assert dns["passed"] is True and dns["target"] == "pypi.org"
    monkeypatch.setattr(probe.socket, "getaddrinfo", lambda *a, **k: [("x",)])
    (dns,) = _check(run_probe_checks({"dns_name": "pypi.org"}), "dns")
    assert dns["passed"] is False


def test_host_addresses_work_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_name(name: str) -> Any:
        raise socket.gaierror(socket.EAI_NONAME, "offline")

    class NoRoute:
        def __init__(self, *args: Any) -> None:
            pass

        def __enter__(self) -> NoRoute:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def connect(self, address: Any) -> None:
            raise OSError(51, "Network is unreachable")

        def getsockname(self) -> Any:
            raise AssertionError("unreachable")

    monkeypatch.setattr(probe.socket, "gethostbyname_ex", no_name)
    monkeypatch.setattr(probe.socket, "socket", NoRoute)
    assert host_addresses() == []

    class Routed(NoRoute):
        def connect(self, address: Any) -> None:
            return None

        def getsockname(self) -> Any:
            return ("192.168.1.20", 50123)

    monkeypatch.setattr(probe.socket, "socket", Routed)
    assert host_addresses() == ["192.168.1.20"]


def _proxy_reply(statuses: dict[str, int]) -> Callable[[bytes], bytes]:
    def reply(request: bytes) -> bytes:
        method = request.split(b" ", 1)[0].decode()
        is_version = b"/api/version" in request.split(b"\r\n", 1)[0]
        status = statuses["version"] if is_version else statuses[method]
        return f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\n\r\n".encode()

    return reply


def test_proxy_checks_read_the_status_line() -> None:
    spec = {
        "token": TOKEN,
        "denied": list(DENIED),
        "canary_via_proxy": list(CANARY),
        "allowed_http": ["http://host.docker.internal:11434/api/version"],
    }
    deny = _proxy_reply({"CONNECT": 403, "GET": 403, "version": 200})
    with _listener(deny) as (port, received):
        results = run_probe_checks(spec, proxy=("127.0.0.1", port))
    for name in ("proxy_denied", "proxy_canary", "allowed_http"):
        assert [r["passed"] for r in _check(results, name)] == [True], name
    assert any(f"Bearer {TOKEN}".encode() in data for data in received)  # the canary request
    allow = _proxy_reply({"CONNECT": 200, "GET": 200, "version": 403})
    with _listener(allow) as (port, _):
        results = run_probe_checks(spec, proxy=("127.0.0.1", port))
    for name in ("proxy_denied", "proxy_canary", "allowed_http"):
        assert [r["passed"] for r in _check(results, name)] == [False], name


def test_env_check_names_forbidden_variables_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@x:1")
    (env,) = _check(run_probe_checks({}), "env")
    assert env["passed"] is False and "HTTPS_PROXY" in env["detail"]
    assert "secret" not in json.dumps(env)
    # The failure names the variables, so the operator sees which one the docker CLI injected.
    failed = probe_verdict([env], **_verdict_args())
    assert failed == ["env HTTPS_PROXY"] and "secret" not in json.dumps(failed)


def _passing() -> list[dict[str, Any]]:
    return [{"check": "dns", "target": "pypi.org", "passed": True, "detail": "gaierror"}]


def _denials(*, canary: bool = True) -> list[ProxyEvent]:
    events = [ProxyEvent("t", "172.30.0.2", "CONNECT", *DENIED, 403, "TCP_DENIED")]
    if canary:
        events.append(ProxyEvent("t", "172.30.0.2", "GET", *CANARY, 403, "TCP_DENIED"))
    return events


def _verdict(**overrides: Any) -> list[str]:
    values: dict[str, Any] = {
        "canary_received": [],
        "token": TOKEN,
        "proxy_events": _denials(),
        "denied": DENIED,
        "canary": CANARY,
    }
    return probe_verdict(_passing(), **(values | overrides))


def test_verdict_fails_when_the_canary_saw_the_token() -> None:
    assert _verdict(canary_received=[f"Bearer {TOKEN}".encode()]) == ["canary_saw_token"]


def test_verdict_needs_both_denials_logged() -> None:
    assert _verdict(proxy_events=_denials(canary=False)) == ["denial_not_logged"]
    allowed = [ProxyEvent("t", "c", "CONNECT", *DENIED, 200, "TCP_TUNNEL"), _denials()[1]]
    assert _verdict(proxy_events=allowed) == ["denial_not_logged"]


def test_verdict_passes_with_every_check_and_both_denials() -> None:
    assert _verdict() == []
    failed = [{"check": "direct", "target": "h:1", "passed": False, "detail": "connected"}]
    assert probe_verdict(failed, **_verdict_args()) == ["direct h:1"]


def _verdict_args() -> dict[str, Any]:
    return {
        "canary_received": [],
        "token": TOKEN,
        "proxy_events": _denials(),
        "denied": DENIED,
        "canary": CANARY,
    }


class ProbeDocker(FakeDocker):
    """A FakeDocker whose probe container 'runs': it logs both denials and reports checks."""

    def __init__(self, *, passed: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.passed = passed
        self.specs: list[dict[str, Any]] = []

    async def __call__(self, argv: Sequence[str]) -> Any:
        argv = list(argv)
        if argv[1:3] != ["start", "--attach"]:
            return await super().__call__(argv)
        self.calls.append(argv)
        (create,) = [c for c in self.ran("create") if f"id-{c[c.index('--name') + 1]}" == argv[-1]]
        spec = json.loads(create[create.index("--spec") + 1])
        self.specs.append(spec)
        if "control" in spec:
            checks = [{"check": "control", "target": "x", "passed": self.passed, "detail": ""}]
            return _done(argv, out=json.dumps({"checks": checks}))
        lines = [
            f"2026-11-04T10:00:00.001Z 172.30.0.2 CONNECT {spec['denied'][0]} "
            f"{spec['denied'][1]} 403 TCP_DENIED",
            f"2026-11-04T10:00:00.002Z 172.30.0.2 GET {spec['canary_via_proxy'][0]} "
            f"{spec['canary_via_proxy'][1]} 403 TCP_DENIED",
        ]
        if spec["allowed_http"]:
            lines.append(
                "2026-11-04T10:00:00.003Z 172.30.0.2 GET host.docker.internal 11434 200 TCP_MISS"
            )
        spec_job = (
            create[create.index("--name") + 1].removeprefix("pdmcp-r1-").removesuffix("-probe")
        )
        (self.squid_spool(spec_job) / "access.log").write_text(
            "".join(f"{line}\n" for line in lines)
        )
        checks = [{"check": "env", "target": "", "passed": self.passed, "detail": ""}]
        return _done(argv, out=json.dumps({"checks": checks}))


async def _egress(
    tmp_path: Path,
    docker: FakeDocker,
    gateways: Sequence[str],
    allow: tuple[tuple[str, int], ...] = (("api.anthropic.com", 443),),
) -> Any:
    (tmp_path / "run").mkdir(exist_ok=True)
    docker.gateways = gateways
    sandbox = Sandbox(_config(tmp_path), docker=docker, verify=_verified)
    return sandbox, await sandbox.open_egress("j", allow)


async def test_read_only_artifacts_cannot_disclose_squid_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        probe, "Canary", lambda: nullcontext(SimpleNamespace(port=50000, received=[]))
    )
    monkeypatch.setattr(probe, "host_addresses", lambda: [])
    job = _job("scripted-anthropic")
    docker = ProbeDocker()
    sandbox = Sandbox(_config(tmp_path), docker=docker, verify=_verified)
    out = sandbox.config.out_dir
    out.mkdir()
    launcher = ContainerLauncher(sandbox, control=AsyncMock())
    await launcher.prepare([job])
    spool = docker.squid_spool(job.job_id)
    (spool / "cache.log").write_text(f"header {SYNTHETIC}")
    (spool / "unknown").write_text(SYNTHETIC)

    def read_only(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("simulated artifact volume read-only")

    monkeypatch.setattr("benchmarks.agentic.sandbox.write_new", read_only)
    assert {p.name for p in (out / "egress" / job.job_id).iterdir()} == {"squid.conf"}
    failure = None
    try:
        await launcher.close()
    except KeyLeakError as exc:
        failure = exc
    assert all(SYNTHETIC.encode() not in p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert failure is not None and "cache.log" in str(failure)
    assert (out / "probe" / f"{job.job_id}.collection-pending").is_file()
    assert not spool.parent.exists()
    assert not docker.resources


@pytest.mark.parametrize("source", ["access.log", "checks", "checks-newline"])
async def test_leaking_probe_never_publishes_a_prefix_or_allows_clean_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setattr(
        probe, "Canary", lambda: nullcontext(SimpleNamespace(port=50000, received=[]))
    )
    monkeypatch.setattr(probe, "host_addresses", lambda: [])
    job = _job("scripted-anthropic")
    docker = FakeDocker()
    sandbox = Sandbox(_config(tmp_path), docker=docker, verify=_verified)
    out = sandbox.config.out_dir
    out.mkdir()
    launcher = ContainerLauncher(sandbox, control=AsyncMock())

    key = "AIza" + "g" * 35 if source == "checks-newline" else SYNTHETIC

    async def one_shot(*args: Any) -> list[dict[str, Any]]:
        spool = docker.squid_spool(job.job_id)
        lines = list(PROBE_LINES)
        if source == "access.log":
            lines.append(PYPI_LINE.replace("pypi.org", SYNTHETIC))
        (spool / "access.log").write_text("\n".join(lines) + "\n")
        (spool / "cache.log").write_text("squid started\n")
        detail = {"checks": key, "checks-newline": "\n" + key}.get(source, "")
        return [{"check": "env", "passed": True, "detail": detail}]

    monkeypatch.setattr(probe, "_one_shot", one_shot)
    with pytest.raises(KeyLeakError) as detected:
        await launcher.prepare([job])
    assert key not in str(detected.value)
    assert not (out / "probe" / f"{job.job_id}.json").exists()
    # Even if Squid replaces a leaking prefix before collection, the verdict stays failed.
    spool = docker.squid_spool(job.job_id)
    (spool / "access.log").write_text("\n".join(PROBE_LINES) + "\n")
    with pytest.raises(KeyLeakError):
        await launcher.close()
    assert not spool.parent.exists()
    assert all(key.encode() not in p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert (out / "probe" / f"{job.job_id}.collection-pending").is_file()
    assert json.loads((out / "egress" / job.job_id / "collected.json").read_text())["ok"] is False


async def test_the_probe_targets_every_ipam_gateway_and_records_the_inspect(
    tmp_path: Path,
) -> None:
    docker = ProbeDocker()
    sandbox, egress = await _egress(tmp_path, docker, ("172.30.0.1", "fd00::1"))
    job = _job("claude-sonnet-5-5")
    record = await isolation_probe(
        sandbox, egress, job_id="j", family=job.family, system_id="no-docs"
    )
    (spec,) = docker.specs
    hosts = {host for host, _ in spec["direct"]}
    assert {"172.30.0.1", "fd00::1", "host.docker.internal", "gateway.docker.internal"} <= hosts
    assert {port for _, port in spec["direct"]} >= {11434}
    assert spec["dns_name"] == "pypi.org" and spec["denied"] == list(DENIED)
    assert spec["allowed_http"] == []
    assert record["passed"] is True and record["failed"] == []
    assert record["network_inspect"] == egress.inspect
    log = (egress.spool_dir / "access.log").read_text().splitlines()
    assert record["proxy_prefix"] == log and len(log) == 2
    written = json.loads((tmp_path / "run" / "probe" / "j.json").read_text())
    assert written == record
    (run,) = docker.ran("create")[-1:]
    assert "-i" not in run and "--rm" in run and run[run.index("--name") + 1].endswith("-j-probe")
    assert not [arg for arg in run if "API_KEY" in arg]
    assert docker.ran("rm", "-f", "id-pdmcp-r1-j-probe")


async def test_scripted_offline_probe_has_no_ollama_request(tmp_path: Path) -> None:
    docker = ProbeDocker()
    sandbox, egress = await _egress(tmp_path, docker, ("172.30.0.1",), allow=())
    scripted = replace(_job("gemma4-12b").family, api="scripted")
    await isolation_probe(sandbox, egress, job_id="j", family=scripted, system_id="no-docs")
    assert docker.specs[0]["allowed_http"] == []


async def test_an_ollama_probe_checks_its_version_url(tmp_path: Path) -> None:
    docker = ProbeDocker()
    sandbox, egress = await _egress(
        tmp_path, docker, ("172.30.0.1",), allow=(("host.docker.internal", 11434),)
    )
    family = _job("gemma4-12b").family
    await isolation_probe(sandbox, egress, job_id="j", family=family, system_id="no-docs")
    assert docker.specs[0]["allowed_http"] == ["http://host.docker.internal:11434/api/version"]


@pytest.mark.parametrize("initial", ["missing", "denials", "never_logged", "no_peer_reply"])
async def test_probe_waits_for_its_log_before_freezing_query_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str
) -> None:
    # No sockets or containers. The proxy publishes the completed log on the next loop turn.
    monkeypatch.setattr(
        probe, "Canary", lambda: nullcontext(SimpleNamespace(port=50000, received=[]))
    )
    monkeypatch.setattr(probe, "host_addresses", lambda: [])
    job = _job("gemma4-12b")
    sandbox, egress = await _egress(
        tmp_path, FakeDocker(), (), allow=(("host.docker.internal", 11434),)
    )
    log = egress.spool_dir / "access.log"
    ollama = "2026-11-04T10:00:03.000Z 172.30.0.3 GET host.docker.internal 11434 200 TCP_MISS"
    prefix = [*PROBE_LINES, ollama]
    if initial == "no_peer_reply":
        prefix = [*(line + " HIER_NONE -" for line in PROBE_LINES), ollama + " HIER_DIRECT -"]

    async def one_shot(*args: Any) -> list[dict[str, Any]]:
        if initial != "missing":
            log.write_text("\n".join(PROBE_LINES) + "\n")
        if initial == "no_peer_reply":
            log.write_text("\n".join(prefix) + "\n")
        elif initial != "never_logged":
            asyncio.get_running_loop().call_soon(log.write_text, "\n".join(prefix) + "\n")
        return [
            {
                "check": "allowed_http",
                "target": probe._OLLAMA_VERSION_URL,
                "passed": True,
                "detail": "200",
            }
        ]

    monkeypatch.setattr(probe, "_one_shot", one_shot)
    if initial in ("never_logged", "no_peer_reply"):
        monkeypatch.setattr(probe, "_SETTLE_S", 0)
        with pytest.raises(IsolationProbeFailed):
            await isolation_probe(
                sandbox, egress, job_id="j", family=job.family, system_id="no-docs"
            )
        assert json.loads((tmp_path / "run/probe/j.json").read_text())["passed"] is False
        return
    record = await isolation_probe(
        sandbox, egress, job_id="j", family=job.family, system_id="no-docs"
    )
    assert record["proxy_prefix"] == prefix
    out = tmp_path / "run"
    with log.open("a") as file:
        file.write(PYPI_LINE + "\n")
    (egress.spool_dir / "cache.log").write_text("squid started\n")
    problems = await sandbox.close_egress(egress)
    assert problems == []
    (out / "probe/control.json").write_text('{"passed":true}')
    summary = write_egress_summary(out, [replace(job, job_id="j")], [])
    assert summary["isolation_probe"] == "pass"
    assert summary["jobs"]["j"]["hosts_allowed"] == []
    assert summary["jobs"]["j"]["hosts_denied"] == ["pypi.org:443"]


async def test_a_failed_check_raises_and_still_writes_the_record(tmp_path: Path) -> None:
    docker = ProbeDocker(passed=False)
    sandbox, egress = await _egress(tmp_path, docker, ("172.30.0.1",))
    family = _job("claude-sonnet-5-5").family
    with pytest.raises(IsolationProbeFailed, match="env"):
        await isolation_probe(sandbox, egress, job_id="j", family=family, system_id="no-docs")
    assert json.loads((tmp_path / "run" / "probe" / "j.json").read_text())["passed"] is False


async def test_a_completed_probe_accepts_docker_auto_removal(tmp_path: Path) -> None:
    name = "pdmcp-r1-control"
    checks = [{"check": "control", "passed": True}]

    class AutoRemove(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["start", "--attach"]:
                assert argv[-1] == f"id-{name}"
                del self.resources[argv[-1]]
                return _done(argv, out=json.dumps({"checks": checks}))
            if argv[1:3] == ["rm", "-f"]:
                assert argv[-1] == f"id-{name}"
                return _done(argv, 1, err="Error response from daemon: No such container")
            return await super().__call__(argv)

    docker = AutoRemove()
    sandbox = Sandbox(_config(tmp_path), docker=docker)
    argv = probe._probe_argv(sandbox, name, "test-net", {}, {})
    assert await probe._one_shot(sandbox, argv, name) == checks
    assert not docker.resources


@pytest.mark.parametrize("source", ["stderr-tail", "decoder"])
async def test_probe_failure_never_echoes_key_in_traceback(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "AIza" + "g" * 35
    proc = _done([], 1, err="X" + key + " " + "." * 260)
    if source == "decoder":
        proc = _done([], out="invalid")
        error = json.JSONDecodeError("invalid token " + key, "invalid", 0)
        monkeypatch.setattr(probe.json, "loads", Mock(side_effect=error))
    sandbox = Mock(
        create_resource=AsyncMock(return_value="fake-id"),
        docker=AsyncMock(return_value=proc),
        remove_container=AsyncMock(),
    )
    with pytest.raises((KeyLeakError, IsolationProbeFailed)) as caught:
        await probe._one_shot(sandbox, ["create"], "probe")
    assert key not in str(caught.value)
    assert key not in "".join(traceback.format_exception(caught.value))
    sandbox.remove_container.assert_awaited_once()


@pytest.mark.parametrize("delimiter", ["\n", r"\x20"])
def test_cli_withholds_structured_probe_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delimiter: str,
) -> None:
    key = "sk-proj-" + "k" * 40
    stderr = json.dumps({"error": "." * 350 + delimiter + key})
    sandbox = Mock(
        create_resource=AsyncMock(return_value="fake-id"),
        docker=AsyncMock(return_value=_done([], 1, err=stderr)),
        remove_container=AsyncMock(),
    )

    async def prepare(*args: Any) -> None:
        await probe._one_shot(sandbox, ["create"], "probe")

    monkeypatch.setattr("benchmarks.agentic.host.LocalLauncher.prepare", prepare)
    with pytest.raises(SystemExit) as caught:
        main(_cli(tmp_path, "--systems", "no-docs"))
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err
    assert caught.value.code == 3 and "probe stderr" in captured.err
    summary = json.loads((tmp_path / "run/run-summary.json").read_bytes())
    assert "KeyLeakError" in summary["abort_reason"] and key not in summary["abort_reason"]


async def test_probe_create_conflict_does_not_remove_the_existing_container(tmp_path: Path) -> None:
    name = "pdmcp-r1-j-probe"
    docker = FakeDocker(fail=lambda args: name in args and args[0] in {"run", "create"})
    docker.resources[f"id-{name}"] = {"a different invocation"}
    sandbox = Sandbox(_config(tmp_path), docker=docker)
    argv = probe._probe_argv(sandbox, name, "test-net", {}, {})
    with pytest.raises(BenchmarkValidationError):
        await probe._one_shot(sandbox, argv, name)
    assert not docker.ran("rm")
    assert f"id-{name}" in docker.resources


async def test_probe_creation_finishes_before_cancellation_removes_it(tmp_path: Path) -> None:
    name = "pdmcp-r1-j-probe"
    docker = LateCreate(name)
    sandbox = Sandbox(_config(tmp_path), docker=docker)
    argv = probe._probe_argv(sandbox, name, "test-net", {}, {})
    await docker.cancel_create(asyncio.create_task(probe._one_shot(sandbox, argv, name)))
    assert not docker.resources
    assert not docker.ran("start")


@pytest.mark.parametrize(("timeout", "rm_fails"), [(False, False), (True, False), (False, True)])
async def test_a_timed_out_or_cancelled_probe_container_is_removed(
    tmp_path: Path, timeout: bool, rm_fails: bool
) -> None:
    name = "pdmcp-r1-j-probe"
    started = asyncio.Event()

    interrupted: list[asyncio.CancelledError] = []

    class Hangs(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["start", "--attach"]:
                assert argv[-1] == f"id-{name}"
                started.set()
                if timeout:
                    raise subprocess.TimeoutExpired(list(argv), 120)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError as exc:
                    interrupted.append(exc)
                    raise
            return await super().__call__(argv)

    docker = Hangs(fail=lambda args: rm_fails and args[:2] == ["rm", "-f"])
    sandbox = Sandbox(_config(tmp_path), docker=docker)
    argv = probe._probe_argv(sandbox, name, "test-net", {}, {})
    task = asyncio.create_task(probe._one_shot(sandbox, argv, name))
    await asyncio.wait_for(started.wait(), 2)
    if not timeout:
        task.cancel("signal SIGTERM")
    with pytest.raises(subprocess.TimeoutExpired if timeout else asyncio.CancelledError) as error:
        await task
    assert docker.calls[-1] == ["docker", "rm", "-f", f"id-{name}"]
    if not timeout:
        assert error.value is interrupted[0]
        assert str(error.value) == "signal SIGTERM"
    if rm_fails:
        assert isinstance(error.value.__cause__, BenchmarkValidationError)
        assert "boom" in str(error.value.__cause__)
    else:
        assert not docker.resources


async def test_a_failed_control_makes_the_probe_inconclusive(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    docker = ProbeDocker(passed=False)
    sandbox = Sandbox(_config(tmp_path), docker=docker, verify=_verified)
    with pytest.raises(IsolationProbeFailed, match="inconclusive"):
        await control_probe(sandbox)
    (spec,) = docker.specs
    assert spec["control"][0] == "host.docker.internal"
    run = docker.ran("create")[-1]
    assert run[run.index("--network") + 1] == "pdmcp-r1-out"
    assert docker.calls[-1] == ["docker", "rm", "-f", "id-pdmcp-r1-control"]
    assert json.loads((tmp_path / "run" / "probe" / "control.json").read_text())["passed"] is False


async def test_no_worker_starts_when_a_probe_fails(tmp_path: Path) -> None:
    started: list[str] = []

    class Recording(ContainerLauncher):
        def command(self, job: Any) -> tuple[list[str], dict[str, str]]:
            started.append(job.job_id)
            raise AssertionError("no worker may start")

        async def close(self) -> list[str]:
            started.append("close")
            return await super().close()

    async def failing(sandbox: Sandbox, egress: Any, **kwargs: Any) -> dict[str, Any]:
        raise IsolationProbeFailed(f"isolation probe failed for {kwargs['job_id']}: direct")

    async def control(sandbox: Sandbox) -> dict[str, Any]:
        return {"passed": True}

    (tmp_path / "run").mkdir()
    docker = FakeDocker()
    sandbox = Sandbox(_config(tmp_path), docker=docker, verify=_verified)
    launcher = Recording(sandbox, probe=failing, control=control)
    ledger = Ledger.open(tmp_path / "ledger", run_id="r1", caps={"anthropic": Decimal(1)})

    async def ignore(job: Any, message: dict[str, Any]) -> None:
        return None

    try:
        with pytest.raises(IsolationProbeFailed):
            await run_jobs(
                [_job("scripted-anthropic")],
                launcher=launcher,
                ledger=LedgerService(ledger, {}, {}),
                on_message=ignore,
                log_dir=tmp_path / "logs",
                cwd=tmp_path,
            )
    finally:
        ledger.close()
    assert started == ["close"]
    assert not [c for c in docker.calls if "agentic-worker" in c]


@pytest.mark.parametrize("fail_immediately", [False, True])
async def test_probe_listener_close_preserves_attach_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    fail_immediately: bool,
) -> None:
    closing, release = asyncio.Event(), asyncio.Event()
    failure = OSError("listener close failed")

    async def wait_closed() -> None:
        closing.set()
        await release.wait()
        raise failure

    server = Mock(
        wait_closed=wait_closed, close=Mock(side_effect=failure if fail_immediately else None)
    )
    server.sockets = [Mock(getsockname=lambda: ("localhost", 1234))]
    monkeypatch.setattr(asyncio, "start_server", AsyncMock(return_value=server))
    original = asyncio.CancelledError("signal SIGTERM")

    async def probe_with_listener() -> None:
        async with probe.Canary():
            raise original

    task = asyncio.create_task(probe_with_listener())
    if not fail_immediately:
        await asyncio.wait_for(closing.wait(), 2)
        task.cancel("signal SIGHUP")
        release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    assert error.value is original and error.value.__cause__ is failure
