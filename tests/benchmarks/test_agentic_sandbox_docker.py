"""The opt-in keyless docker test: the real sandbox on Docker Desktop, with scripted models only.

Skipped unless ``BENCHMARK_DOCKER_TESTS=1`` and ``BENCHMARK_SANDBOX_IMAGE`` (the worker image
ID, built from HEAD by ``benchmarks/sandbox/run.sh``) are set. Never run in CI. It reads no
key, sets no key, makes no provider call and pulls nothing: every image is local, by ID or
digest. It settles the Docker behaviours no document covers (design note §§B, C), and the
runbook's "Docker Desktop results" records what it found.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.egress import parse_proxy_log
from benchmarks.agentic.probe import IsolationProbeFailed, isolation_probe
from benchmarks.agentic.run import run_agentic
from benchmarks.agentic.sandbox import (
    RUN_LABEL,
    ContainerLauncher,
    Sandbox,
    SandboxConfig,
)
from benchmarks.runner import _repo_commit_sha
from tests.benchmarks.test_agentic_run import _config, _matrix_with
from tests.benchmarks.test_agentic_sandbox import _job
from tests.test_mcp_get_docs_cache_smoke import _create_contentful_json_index

IMAGE = os.environ.get("BENCHMARK_SANDBOX_IMAGE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BENCHMARK_DOCKER_TESTS") != "1" or not IMAGE,
    reason="opt-in: set BENCHMARK_DOCKER_TESTS=1 and BENCHMARK_SANDBOX_IMAGE=<image id>",
)
REPO_ROOT = Path(__file__).resolve().parents[2]
BIG = 150_000  # a trajectory frame well over 100,000 bytes


@pytest.fixture(autouse=True)
def _keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)  # removed unread: this test holds no key
    monkeypatch.delenv("CI", raising=False)


@pytest.fixture
def image() -> str:
    return IMAGE


def _sandbox_config(tmp_path: Path, run_id: str, index: Path | None = None) -> SandboxConfig:
    return SandboxConfig(
        image=IMAGE,
        run_id=run_id,
        out_dir=tmp_path / "run",
        index_path=index or tmp_path / "no-index.db",
        keys_file=None,
        repo_commit=_repo_commit_sha(),
    )


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    """A sandbox for single steps; every egress it opened is closed afterwards."""
    (tmp_path / "run").mkdir()
    box = Sandbox(_sandbox_config(tmp_path, f"t{os.getpid()}-{tmp_path.name[-12:]}"))
    opened: list[Any] = []
    real_open = box.open_egress

    async def open_egress(job_id: str, allow: Any) -> Any:
        egress = await real_open(job_id, allow)
        opened.append(egress)
        return egress

    box.open_egress = open_egress  # type: ignore[method-assign]
    yield box

    async def cleanup() -> None:
        for egress in opened:
            await box.close_egress(egress)
        if box.outside_created:
            await box.docker(["docker", "network", "rm", box.outside])

    asyncio.run(cleanup())


async def _probe_container(box: Sandbox, network: str, spec: dict[str, Any]) -> dict[str, Any]:
    name = f"{box.worker_name('direct')}-probe"
    argv = box.container_argv(
        name=name,
        network=network,
        env={"HOME": "/tmp"},
        mounts=[],
        argv=["python", "-m", "benchmarks", "agentic-probe", "--spec", json.dumps(spec)],
        interactive=False,
    )
    try:
        proc = await box.docker(argv)
    finally:
        await box.docker(["docker", "rm", "-f", name])
    assert proc.returncode == 0, proc.stderr
    return {c["check"]: c for c in json.loads(proc.stdout)["checks"]}


def test_image_runs_unprivileged_with_cat_without_node_or_proxy(image: str) -> None:
    script = "id -u; command -v cat; command -v node || echo no-node; env"
    proc = _docker("run", "--rm", "--pull", "never", image, "sh", "-c", script)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[:3] == ["10001", "/usr/bin/cat", "no-node"]
    assert not [line for line in lines if "PROXY" in line.upper().split("=", 1)[0]]
    assert (
        _docker(
            "run", "--rm", "--pull", "never", image, "sh", "-c", "command -v mcp-server-fetch"
        ).stdout.strip()
        == "/usr/local/bin/mcp-server-fetch"
    )


def test_a_scripted_run_completes_inside_containers(tmp_path: Path, image: str) -> None:
    index = _create_contentful_json_index(tmp_path / "index")
    config = _config(
        tmp_path,
        models=("scripted-anthropic",),
        systems=("python-docs", "no-docs"),
        questions=("SYN-EX-001",),
        repeats=1,
        run_id="dockerscripted",
    )
    launcher = ContainerLauncher(Sandbox(_sandbox_config(tmp_path, "dockerscripted", index)))
    summary = run_agentic(config, launcher=launcher)
    run = tmp_path / "run"
    assert summary["status"] == "completed" and summary["succeeded_cells"] == 2
    control = json.loads((run / "probe" / "control.json").read_text())
    assert control["passed"] is True
    for job_id in ("scripted-anthropic--python-docs", "scripted-anthropic--no-docs"):
        probe = json.loads((run / "probe" / f"{job_id}.json").read_text())
        assert probe["passed"] is True, probe["failed"]
        assert probe["network_inspect"]["EnableIPv6"] is False
        events = parse_proxy_log((run / "egress" / job_id / "access.log").read_text())
        denied = {(e.method, e.host) for e in events if not e.allowed}
        assert ("CONNECT", "example.com") in denied
        assert any(m == "GET" and h == "host.docker.internal" for m, h in denied)
        collected = json.loads((run / "egress" / job_id / "collected.json").read_text())
        assert collected["ok"] is True, collected["problems"]
        assert collected["state_after_stop"]["ExitCode"] == 0  # Ubuntu's Squid, after SIGTERM
    egress = json.loads((run / "egress" / "summary.json").read_text())
    assert set(egress) == {"jobs", "isolation_probe"} and egress["isolation_probe"] == "pass"
    for entry in egress["jobs"].values():
        assert entry["hosts_allowed"] == [] and entry["hosts_denied"] == []
    transcript = run / "transcripts" / "python-docs--scripted-anthropic--r1" / "SYN-EX-001.json"
    calls = json.loads(transcript.read_text())["tool_calls"]
    assert calls and not calls[0]["is_error"]  # the server ran on the tmpfs cache with the index


def test_a_trajectory_frame_over_100000_bytes_round_trips_through_a_container(
    tmp_path: Path, image: str
) -> None:
    answer = "x" * BIG
    script = [{"text": answer, "usage": {"input_tokens": 1000, "output_tokens": 100}}]
    config = _config(
        tmp_path,
        matrix_path=_matrix_with(tmp_path, {"scripted-anthropic": script}),
        models=("scripted-anthropic",),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
        run_id="dockerbig",
    )
    launcher = ContainerLauncher(Sandbox(_sandbox_config(tmp_path, "dockerbig")))
    summary = run_agentic(config, launcher=launcher)
    assert summary["status"] == "completed", summary.get("abort_reason")
    transcript = tmp_path / "run" / "transcripts" / "no-docs--scripted-anthropic--r1"
    assert json.loads((transcript / "SYN-EX-001.json").read_text())["answer"] == answer


async def test_an_ip_literal_connect_whose_ptr_names_an_allowlisted_host_is_denied(
    sandbox: Sandbox,
) -> None:
    egress = await sandbox.open_egress("ptr", (("localhost", 443),))
    checks = await _probe_container(
        sandbox, egress.network, {"token": "t", "denied": ["127.0.0.1", 443]}
    )
    assert checks["proxy_denied"]["passed"] is True, checks["proxy_denied"]
    await asyncio.sleep(1)
    events = parse_proxy_log((egress.log_dir / "access.log").read_text())
    assert [(e.host, e.port, e.result) for e in events] == [("127.0.0.1", 443, "TCP_DENIED")]


@pytest.fixture
def loopback_service() -> Iterator[int]:
    """An HTTP service bound to the Mac's 127.0.0.1 only, as Ollama is by default."""
    server = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def serve() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            with conn:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    thread.join(5)
    server.close()


async def test_a_service_bound_to_the_macs_loopback_through_the_proxy(
    sandbox: Sandbox, loopback_service: int
) -> None:
    egress = await sandbox.open_egress("loop", (("host.docker.internal", loopback_service),))
    url = f"http://host.docker.internal:{loopback_service}/api/version"
    checks = await _probe_container(sandbox, egress.network, {"token": "t", "allowed_http": [url]})
    assert checks["allowed_http"]["passed"] is True, checks["allowed_http"]


async def test_a_docker_config_with_proxies_fails_the_isolation_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = json.loads(_docker("context", "inspect", "--format", "{{json .}}").stdout)
    host = endpoint["Endpoints"]["docker"]["Host"]
    config_dir = tmp_path / "docker-config"
    config_dir.mkdir()
    proxies = {"proxies": {"default": {"httpsProxy": "http://proxy.invalid:3128"}}}
    (config_dir / "config.json").write_text(json.dumps(proxies))
    monkeypatch.setenv("DOCKER_CONFIG", str(config_dir))
    monkeypatch.setenv("DOCKER_HOST", host)  # the same local daemon, without the context store
    (tmp_path / "run").mkdir()
    box = Sandbox(_sandbox_config(tmp_path, f"tcfg{os.getpid()}"))
    egress = await box.open_egress("cfg", (("api.anthropic.com", 443),))
    family = _job("scripted-anthropic").family
    try:
        with pytest.raises(IsolationProbeFailed, match="HTTPS_PROXY"):
            await isolation_probe(box, egress, job_id="cfg", family=family, system_id="no-docs")
    finally:
        await box.close_egress(egress)
        await box.docker(["docker", "network", "rm", box.outside])


def test_no_container_or_network_with_the_run_label_remains(image: str) -> None:
    containers = _docker("ps", "-a", "--filter", f"label={RUN_LABEL}", "--format", "{{.Names}}")
    networks = _docker("network", "ls", "--filter", f"label={RUN_LABEL}", "--format", "{{.Name}}")
    assert containers.stdout.strip() == "" and networks.stdout.strip() == ""
