"""The container sandbox and ContainerLauncher (design §7.2, §7.3), with a recorded FakeDocker.

No test runs docker or reaches a network host: every docker command goes to FakeDocker.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import SystemSpec, load_systems
from benchmarks.agentic.egress import allowlist, squid_conf, write_egress_summary
from benchmarks.agentic.host import WorkerJob, run_jobs
from benchmarks.agentic.loop import HOSTED_CAPS
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic.sandbox import (
    RUN_LABEL,
    SQUID_IMAGE,
    ContainerLauncher,
    Egress,
    Sandbox,
    SandboxConfig,
    cli_env,
    read_key,
    verify_image,
)
from benchmarks.model_matrix import ModelFamily, load_model_matrix
from benchmarks.runner import BenchmarkValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "agentic"
IMAGE = "sha256:" + "ab" * 32
COMMIT = "c" * 40
RUN = "r1"
SYNTHETIC = "sk-ant-api03-" + "s" * 90
FAMILIES: dict[str, ModelFamily] = {
    f.id: f
    for path in (REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml", FIX / "model-matrix.yml")
    for f in load_model_matrix(path).model_families
}
SYSTEMS: dict[str, SystemSpec] = {
    s.id: s for s in load_systems(REPO_ROOT / "docs" / "benchmarks" / "agentic-manifest.yml")
}
RUNNING = {"Running": True, "ExitCode": 0, "OOMKilled": False}
STOPPED = {"Running": False, "ExitCode": 0, "OOMKilled": False}


def _done(argv: Sequence[str], code: int = 0, out: str = "", err: str = "") -> Any:
    return subprocess.CompletedProcess(list(argv), code, out.encode(), err.encode())


class FakeDocker:
    """Records every docker argv and answers like a local Docker Desktop, success by default."""

    def __init__(
        self,
        *,
        context_host: str = "unix:///Users/x/.docker/run/docker.sock",
        engine: str = "29.9.2",
        revision: str = COMMIT,
        image_id: str = IMAGE,
        ipv6: bool = False,
        gateways: Sequence[str] = ("172.30.0.1",),
        states: Sequence[dict[str, Any]] = (RUNNING, STOPPED),
        squid_present: bool = True,
        fail: Callable[[list[str]], bool] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.context_host, self.engine, self.revision = context_host, engine, revision
        self.image_id, self.ipv6, self.gateways = image_id, ipv6, gateways
        self.states, self.squid_present, self.fail = states, squid_present, fail
        self._inspected: dict[str, int] = {}
        self.resources: dict[str, set[str]] = {}

    async def __call__(self, argv: Sequence[str]) -> Any:
        argv = list(argv)
        self.calls.append(argv)
        args = argv[1:]
        if self.fail is not None and self.fail(args):
            return _done(argv, 1, err="Error response from daemon: boom")
        if args[:2] == ["network", "create"] or args[0] in {"create", "run"}:
            name = args[args.index("--name") + 1] if "--name" in args else args[-1]
            resource_id = f"id-{name}"
            self.resources[resource_id] = {v for k, v in zip(args, args[1:]) if k == "--label"}
            return _done(argv, out=resource_id + "\n")
        if args[:2] in (["container", "ls"], ["network", "ls"]):
            label = args[args.index("--filter") + 1].removeprefix("label=")
            ids = [i for i, labels in self.resources.items() if label in labels]
            return _done(argv, out="\n".join(ids))
        if args[:2] in (["rm", "-f"], ["network", "rm"]):
            self.resources.pop(args[-1], None)
        if args[:2] == ["context", "inspect"]:
            ctx = {"Name": "desktop-linux", "Endpoints": {"docker": {"Host": self.context_host}}}
            return _done(argv, out=json.dumps(ctx))
        if args[:2] == ["image", "inspect"]:
            if args[-1] == SQUID_IMAGE:
                if not self.squid_present:
                    return _done(argv, 1, err=f"Error: No such image: {SQUID_IMAGE}")
                return _done(argv, out=json.dumps({"Id": "sha256:" + "5" * 64}))
            labels = {"org.opencontainers.image.revision": self.revision}
            return _done(argv, out=json.dumps({"Id": self.image_id, "Config": {"Labels": labels}}))
        if args[:1] == ["version"]:
            return _done(argv, out=self.engine + "\n")
        if args[:2] == ["network", "inspect"]:
            config = [{"Subnet": "172.30.0.0/16", "Gateway": g} for g in self.gateways]
            net = {"Name": args[-1], "EnableIPv6": self.ipv6, "IPAM": {"Config": config}}
            return _done(argv, out=json.dumps([net]))
        if args[:1] == ["inspect"]:
            seen = self._inspected.get(args[-1], 0)
            self._inspected[args[-1]] = seen + 1
            return _done(argv, out=json.dumps(self.states[min(seen, len(self.states) - 1)]))
        return _done(argv)

    def squid_spool(self, job_id: str) -> Path:
        (run,) = [c for c in self.ran("run") if f"pdmcp-{RUN}-{job_id}-proxy" in c]
        (mount,) = [m for m in _mounts(run) if m.endswith(",dst=/var/log/squid-bench")]
        return Path(mount.removeprefix("type=bind,src=").split(",dst=")[0])

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[1 : 1 + len(prefix)] == list(prefix)]


async def _verified(sandbox: Sandbox) -> dict[str, str]:
    return {"image": sandbox.config.image}


class Probes:
    def __init__(self, fail_on: str | None = None) -> None:
        self.jobs: list[str] = []
        self.fail_on = fail_on

    async def __call__(self, sandbox: Sandbox, egress: Egress, **kwargs: Any) -> dict[str, Any]:
        self.jobs.append(kwargs["job_id"])
        if kwargs["job_id"] == self.fail_on:
            raise BenchmarkValidationError(f"isolation probe failed for {kwargs['job_id']}")
        return {"passed": True}

    async def control(self, sandbox: Sandbox) -> dict[str, Any]:
        self.jobs.append("control")
        return {"passed": True}


def _config(tmp_path: Path, keys_file: Path | None = None, **overrides: Any) -> SandboxConfig:
    values: dict[str, Any] = {
        "image": IMAGE,
        "run_id": RUN,
        "out_dir": tmp_path / "run",
        "index_path": tmp_path / "index.db",
        "keys_file": keys_file,
        "repo_commit": COMMIT,
    }
    return SandboxConfig(**(values | overrides))


def _sandbox(tmp_path: Path, docker: FakeDocker, **config: Any) -> Sandbox:
    (tmp_path / "run").mkdir(exist_ok=True)
    return Sandbox(_config(tmp_path, **config), docker=docker, verify=_verified)


def _job(family_id: str, system_id: str = "no-docs") -> WorkerJob:
    family = FAMILIES[family_id]
    if family.api == "scripted":
        family = replace(family, raw={**family.raw, "script": str(FIX / family.raw["script"])})
    trajectories = ({"corpus_id": "Q1", "prompt": "q", "repeat": 1},)
    return WorkerJob(
        f"{family_id}--{system_id}", family, SYSTEMS[system_id], trajectories, HOSTED_CAPS
    )


def _keys(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    path = tmp_path / "keys.env"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _env_names(argv: Sequence[str]) -> list[str]:
    return [argv[i + 1].split("=", 1)[0] for i, arg in enumerate(argv) if arg == "-e"]


def _mounts(argv: Sequence[str]) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == "--mount"]


@pytest.fixture
def umask_077() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


# --- keys --------------------------------------------------------------------------------------


def test_read_key_returns_only_the_named_key(tmp_path: Path) -> None:
    text = (
        "# c\nexport OPENAI_API_KEY_OLD=x\nOPENAI_API_KEY='sk-a'\n\n"
        'export ANTHROPIC_API_KEY="sk-b"\n'
    )
    path = _keys(tmp_path, text)
    assert read_key(path, "OPENAI_API_KEY") == "sk-a"
    assert read_key(path, "ANTHROPIC_API_KEY") == "sk-b"


@pytest.mark.parametrize("mode", [0o640, 0o604])
def test_read_key_refuses_a_group_or_world_readable_file(tmp_path: Path, mode: int) -> None:
    path = _keys(tmp_path, "OPENAI_API_KEY=sk-secret-value\n", mode)
    with pytest.raises(BenchmarkValidationError, match="OPENAI_API_KEY") as caught:
        read_key(path, "OPENAI_API_KEY")
    assert str(path) in str(caught.value) and "sk-secret-value" not in str(caught.value)


@pytest.mark.parametrize("name", ["GOOGLE_API_KEY", "ANTHROPIC_API_KEY"])
def test_read_key_errors_never_hold_a_value(tmp_path: Path, name: str) -> None:
    path = _keys(tmp_path, "OPENAI_API_KEY=sk-secret-value\nANTHROPIC_API_KEY=\n")
    with pytest.raises(BenchmarkValidationError, match=name) as caught:
        read_key(path, name)
    assert str(path) in str(caught.value) and "sk-secret-value" not in str(caught.value)
    with pytest.raises(BenchmarkValidationError, match=name):
        read_key(tmp_path / "missing.env", name)


# --- the worker container ----------------------------------------------------------------------


def test_worker_argv_carries_the_design_hardening(tmp_path: Path) -> None:
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker()), probe=(probes := Probes()), control=probes.control
    )
    argv = launcher.worker_create_argv(_job("scripted-anthropic"))
    text = " ".join(argv)
    for flag in (
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--user 10001:10001",
        "--init",
        "--rm",
        "--pull never",
        "--log-driver none",
        f"--network pdmcp-{RUN}-scripted-anthropic--no-docs-net",
        "--memory 1g",
        "--cpus 1",
        "--pids-limit 256",
        "--tmpfs /tmp",
        f"--label {RUN_LABEL}={RUN}",
        f"--name pdmcp-{RUN}-scripted-anthropic--no-docs",
    ):
        assert flag in text
    assert "-i" in argv and "-t" not in argv and "-it" not in argv
    for banned in ("--privileged", "docker.sock", "--network host", "--network=host"):
        assert banned not in text
    assert argv[argv.index(IMAGE) + 1 :] == ["python", "-m", "benchmarks", "agentic-worker"]
    assert argv[:2] == ["docker", "create"]


def test_only_python_docs_jobs_mount_the_index_read_only(tmp_path: Path) -> None:
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker()), probe=(probes := Probes()), control=probes.control
    )
    index = f"type=bind,src={tmp_path / 'index.db'},dst=/cache/mcp-python-docs/index.db,readonly"
    for system_id, mounted in (("python-docs", True), ("docs-fetch", False), ("no-docs", False)):
        mounts = _mounts(launcher.worker_create_argv(_job("claude-sonnet-5-5", system_id)))
        assert (index in mounts) is mounted
        assert "type=tmpfs,dst=/cache/mcp-python-docs,tmpfs-mode=1777" in mounts
    script = str(FIX / "script-anthropic.yml")
    mounts = _mounts(launcher.worker_create_argv(_job("scripted-anthropic")))
    assert f"type=bind,src={script},dst={script},readonly" in mounts


async def test_worker_env_is_the_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTPS_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "LANG"):
        monkeypatch.setenv(name, "http://user:secret@proxy.invalid:1")
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    allowed = {
        "HOME",
        "XDG_CACHE_HOME",
        "PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX",
        "BENCHMARK_EGRESS_URL",
        "BENCHMARK_LIVE_PROVIDERS_ENABLED",
        "OLLAMA_BASE_URL",
    }
    docker = FakeDocker()
    keys = _keys(tmp_path, "ANTHROPIC_API_KEY=dummy")
    launcher = ContainerLauncher(
        _sandbox(tmp_path, docker, keys_file=keys),
        probe=(probes := Probes()),
        control=probes.control,
    )
    jobs = [_job(family) for family in ("scripted-anthropic", "claude-sonnet-5-5", "gemma4-12b")]
    await launcher.prepare(jobs)
    for job in jobs:
        (argv,) = [c for c in docker.ran("create") if launcher.sandbox.worker_name(job.job_id) in c]
        _, env = launcher.command(job)
        assert set(_env_names(argv)) <= allowed
        assert set(env) <= {
            "PATH",
            "HOME",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "DOCKER_CERT_PATH",
            "DOCKER_TLS_VERIFY",
        }
        assert env["DOCKER_CONTEXT"] == "desktop-linux"
    gemma = launcher.worker_create_argv(_job("gemma4-12b"))
    assert "OLLAMA_BASE_URL=http://host.docker.internal:11434/v1" in gemma
    assert "BENCHMARK_LIVE_PROVIDERS_ENABLED=1" in gemma
    scripted = launcher.worker_create_argv(_job("scripted-anthropic"))
    assert "BENCHMARK_LIVE_PROVIDERS_ENABLED=1" not in scripted
    assert "BENCHMARK_EGRESS_URL=http://egress:3128" in scripted
    await launcher.close()


async def test_no_key_in_argv_cli_env_or_container_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    keys = _keys(tmp_path, f"ANTHROPIC_API_KEY={SYNTHETIC}\n")
    docker = FakeDocker()
    sandbox = _sandbox(tmp_path, docker, keys_file=keys)
    launcher = ContainerLauncher(sandbox, probe=(probes := Probes()), control=probes.control)
    job = _job("claude-sonnet-5-5", "python-docs")
    sandbox.config.index_path.touch()
    await launcher.prepare([job])
    argv, env = launcher.command(job)
    recorded = json.dumps({"calls": docker.calls, "argv": argv, "env": env, "cli": sandbox.env})
    assert SYNTHETIC not in recorded and "ANTHROPIC_API_KEY" not in recorded
    assert launcher.bootstrap_frame(job) == {
        "type": "bootstrap",
        "env": {"ANTHROPIC_API_KEY": SYNTHETIC},
    }
    assert "ANTHROPIC_API_KEY" not in os.environ
    await launcher.close()


def test_each_live_bootstrap_holds_only_its_own_providers_key(tmp_path: Path) -> None:
    keys = _keys(tmp_path, "ANTHROPIC_API_KEY=sk-a\nOPENAI_API_KEY=sk-o\nGOOGLE_API_KEY=g-k\n")
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker(), keys_file=keys),
        probe=(probes := Probes()),
        control=probes.control,
    )
    assert launcher.bootstrap_frame(_job("gpt-6.1-sol")) == {
        "type": "bootstrap",
        "env": {"OPENAI_API_KEY": "sk-o"},
    }
    assert launcher.bootstrap_frame(_job("gemini-3.8-flash"))["env"] == {"GOOGLE_API_KEY": "g-k"}  # type: ignore[index]
    assert launcher.bootstrap_frame(_job("gemma4-12b")) == {"type": "bootstrap", "env": {}}
    assert launcher.bootstrap_frame(_job("scripted-anthropic")) is None


def test_a_live_family_without_a_keys_file_is_refused(tmp_path: Path) -> None:
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker()), probe=(probes := Probes()), control=probes.control
    )
    with pytest.raises(BenchmarkValidationError, match="keys file"):
        launcher.bootstrap_frame(_job("claude-sonnet-5-5"))


# --- networks and proxies ----------------------------------------------------------------------


async def test_internal_networks_are_ipv4_only_with_an_isolated_gateway(tmp_path: Path) -> None:
    docker = FakeDocker(gateways=("172.30.0.1", "fd00::1"))
    sandbox = _sandbox(tmp_path, docker)
    egress = await sandbox.open_egress("j", (("api.anthropic.com", 443),))
    net = f"pdmcp-{RUN}-j-net"
    (create,) = [c for c in docker.ran("network", "create") if c[-1] == net]
    text = " ".join(create)
    assert "--internal --ipv6=false -o com.docker.network.bridge.gateway_mode_ipv4=isolated" in text
    assert f"--label {RUN_LABEL}={RUN}" in text
    assert egress.gateways == ("172.30.0.1", "fd00::1") and egress.network == net
    assert egress.inspect["EnableIPv6"] is False
    docker = FakeDocker(ipv6=True)
    (tmp_path / "v6").mkdir()
    sandbox = _sandbox(tmp_path / "v6", docker)
    with pytest.raises(BenchmarkValidationError, match=net):
        await sandbox.open_egress("j", ())
    assert docker.ran("network", "rm", f"id-{net}")
    assert not docker.ran("run") and not docker.ran("network", "connect")


class LateCreate(FakeDocker):
    """The daemon finishes creating the resource even if the CLI awaiter is cancelled."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.published = asyncio.Event()

    async def __call__(self, argv: Sequence[str]) -> Any:
        if self.name in argv and ("create" in argv or "run" in argv):
            return await asyncio.shield(asyncio.create_task(self._publish(argv)))
        return await super().__call__(argv)

    async def _publish(self, argv: Sequence[str]) -> Any:
        self.started.set()
        await self.release.wait()
        result = await super().__call__(argv)
        self.published.set()
        return result

    async def cancel_create(self, task: asyncio.Task[Any]) -> None:
        await asyncio.wait_for(self.started.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(self.published.wait(), 2)


async def test_worker_published_after_interrupt_is_removed_by_id(tmp_path: Path) -> None:
    job = _job("scripted-anthropic")
    name = f"pdmcp-{RUN}-{job.job_id}"
    docker = LateCreate(name)
    probes = Probes()
    launcher = ContainerLauncher(_sandbox(tmp_path, docker), probes, probes.control)

    async def launch() -> None:
        try:
            await launcher.prepare([job])
            try:
                await docker(launcher.command(job)[0])
            finally:
                await launcher.stop_worker(job)
        finally:
            await launcher.close()

    await docker.cancel_create(asyncio.create_task(launch()))
    assert not docker.resources
    assert docker.ran("rm", "-f", f"id-{name}")
    assert not docker.ran("start")


async def test_workers_attach_and_stop_independently_after_prepare(tmp_path: Path) -> None:
    docker, probes = FakeDocker(), Probes()
    launcher = ContainerLauncher(_sandbox(tmp_path, docker), probes, probes.control)
    jobs = [_job("scripted-anthropic"), _job("scripted-anthropic", "docs-fetch")]
    await launcher.prepare(jobs)
    assert not docker.ran("start")
    for job in jobs:
        name = f"pdmcp-{RUN}-{job.job_id}"
        (create,) = [c for c in docker.ran("create") if name in c]
        assert "-i" in create and "-t" not in create
        assert launcher.command(job)[0] == ["docker", "start", "-ai", f"id-{name}"]
    await launcher.stop_worker(jobs[1])
    first, second = (f"id-pdmcp-{RUN}-{job.job_id}" for job in jobs)
    assert second not in docker.resources and first in docker.resources
    await launcher.close()  # the first worker never started
    assert not docker.resources


async def test_worker_conflict_after_partial_prepare_precedes_key_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = [_job("scripted-anthropic"), _job("claude-sonnet-5-5")]
    name = f"pdmcp-{RUN}-{jobs[1].job_id}"
    docker = FakeDocker(fail=lambda args: name in args and args[0] in {"create", "run"})
    foreign = {f"id-{name}": {f"{RUN_LABEL}={RUN}"}}
    docker.resources.update(foreign)
    probes = Probes()
    box = _sandbox(tmp_path, docker, keys_file=tmp_path / "unused")
    launcher = ContainerLauncher(box, probes, probes.control)

    def premature_read(*args: Any) -> str:
        pytest.fail("the worker create conflict must be detected before reading keys")

    monkeypatch.setattr("benchmarks.agentic.sandbox.read_key", premature_read)
    try:
        with pytest.raises(BenchmarkValidationError, match="boom"):
            await launcher.prepare(jobs)
    finally:
        await launcher.close()
    assert probes.jobs == ["control", *(job.job_id for job in jobs)]
    assert docker.resources == foreign
    assert docker.ran("rm", "-f", f"id-{box.worker_name(jobs[0].job_id)}")
    assert not [c for c in docker.calls if "rm" in c and c[-1] in {name, f"id-{name}"}]
    assert not docker.ran("start")


async def test_keyless_direct_probe_preserves_conflicting_container(tmp_path: Path) -> None:
    from tests.benchmarks.test_agentic_sandbox_docker import _probe_container

    name = f"pdmcp-{RUN}-direct-probe"
    docker = FakeDocker(fail=lambda args: name in args and args[0] in {"run", "create"})
    foreign = {f"id-{name}": {f"{RUN_LABEL}={RUN}"}}
    docker.resources.update(foreign)
    box = _sandbox(tmp_path, docker)
    with pytest.raises(BenchmarkValidationError, match="boom"):
        await _probe_container(box, "net", {})
    assert not docker.ran("rm")
    assert docker.resources == foreign


def test_keyless_fixture_cleans_its_outside_network_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.benchmarks import test_agentic_sandbox_docker as keyless

    monkeypatch.setattr(keyless, "_sandbox_config", _config)
    fixture = keyless.sandbox.__wrapped__(tmp_path)
    box = next(fixture)
    box.docker = docker = FakeDocker()
    asyncio.run(box.ensure_outside())
    assert box.outside_id is not None
    owned = box.outside_id
    # A replacement can acquire the same name between creation and fixture teardown.
    del docker.resources[owned]
    docker.resources[box.outside] = {"foreign"}
    with pytest.raises(StopIteration):
        next(fixture)
    assert docker.ran("network", "rm", owned)
    assert docker.resources == {box.outside: {"foreign"}}


@pytest.mark.parametrize("resource", ["out", "j-net", "j-proxy"])
async def test_cancel_waits_for_docker_to_publish_the_created_resource(
    tmp_path: Path, resource: str
) -> None:
    name = f"pdmcp-{RUN}-{resource}"
    docker = LateCreate(name)
    sandbox = _sandbox(tmp_path, docker)
    await docker.cancel_create(asyncio.create_task(sandbox.open_egress("j", ())))
    await ContainerLauncher(sandbox).close()
    assert docker.resources == {}
    assert any("rm" in argv and argv[-1] == f"id-{name}" for argv in docker.calls)


@pytest.mark.parametrize("resource", ["out", "j-net", "j-proxy"])
async def test_create_conflict_never_removes_the_existing_resource(
    tmp_path: Path, resource: str
) -> None:
    name = f"pdmcp-{RUN}-{resource}"
    docker = FakeDocker(fail=lambda args: name in args and ("create" in args or "run" in args))
    foreign_id = f"id-{name}"
    docker.resources[foreign_id] = {f"{RUN_LABEL}={RUN}"}
    sandbox = _sandbox(tmp_path, docker)
    with pytest.raises(BenchmarkValidationError, match="boom"):
        await sandbox.open_egress("j", ())
    await ContainerLauncher(sandbox).close()
    assert docker.resources == {foreign_id: {f"{RUN_LABEL}={RUN}"}}
    assert not [c for c in docker.calls if "rm" in c and c[-1] in {name, foreign_id}]


async def test_cancelled_create_that_hits_a_conflict_preserves_cancellation(tmp_path: Path) -> None:
    name = f"pdmcp-{RUN}-out"
    docker = LateCreate(name)
    docker.fail = lambda args: args[:2] == ["network", "create"]
    sandbox = _sandbox(tmp_path, docker)
    await docker.cancel_create(asyncio.create_task(sandbox.ensure_outside()))
    assert not docker.ran("network", "rm")


async def test_timed_out_create_can_appear_after_the_docker_cli_exits(tmp_path: Path) -> None:
    name = f"pdmcp-{RUN}-out"

    class TimeoutDocker(FakeDocker):
        pending: Sequence[str] = ()

        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["network", "create"]:
                self.pending = argv
                raise subprocess.TimeoutExpired(argv, 120)
            result = await super().__call__(argv)
            if self.pending and argv[1:3] == ["network", "ls"]:
                # The first list snapshots an empty daemon, then the original POST lands.
                await super().__call__(self.pending)
                self.pending = ()
            return result

    docker = TimeoutDocker()
    sandbox = _sandbox(tmp_path, docker)
    with pytest.raises(subprocess.TimeoutExpired):
        await sandbox.ensure_outside()
    assert docker.ran("network", "rm", f"id-{name}")
    assert not docker.resources


async def test_unobserved_create_timeout_reports_the_ownership_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("benchmarks.agentic.sandbox._RESOURCE_RECOVERY_S", 0)

    class TimeoutDocker(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["network", "create"]:
                raise subprocess.TimeoutExpired(argv, 120)
            return await super().__call__(argv)

    docker = TimeoutDocker()
    with pytest.raises(BenchmarkValidationError, match="outcome is unknown") as caught:
        await _sandbox(tmp_path, docker).ensure_outside()
    assert docker.ran("network", "ls")[0][-1] in str(caught.value)
    assert not docker.ran("network", "rm")


async def test_a_failed_proxy_start_removes_the_proxy_and_the_network(tmp_path: Path) -> None:
    for step in (["run"], ["network", "connect"]):
        root = tmp_path / step[-1]
        root.mkdir()

        class FailedStart(FakeDocker):
            async def __call__(self, argv: Sequence[str]) -> Any:
                result = await super().__call__(argv)
                if list(argv[1 : 1 + len(step)]) == step:
                    # docker run -d can fail its start after the daemon created the proxy.
                    return _done(argv, 1, err="cannot start or connect")
                return result

        docker = FailedStart()
        with pytest.raises(BenchmarkValidationError):
            await _sandbox(root, docker).open_egress("j", (("api.openai.com", 443),))
        assert not docker.squid_spool("j").parent.exists()
        assert docker.ran("rm", "-f", f"id-pdmcp-{RUN}-j-proxy")
        assert docker.ran("network", "rm", f"id-pdmcp-{RUN}-j-net")


async def test_prepare_builds_one_network_and_proxy_per_job_with_a_file_log(
    tmp_path: Path, umask_077: None
) -> None:
    docker = FakeDocker()
    probes = Probes()
    keys = _keys(tmp_path, "ANTHROPIC_API_KEY=dummy")
    launcher = ContainerLauncher(
        _sandbox(tmp_path, docker, keys_file=keys), probe=probes, control=probes.control
    )
    jobs = [_job("claude-sonnet-5-5", "docs-fetch"), _job("scripted-anthropic", "no-docs")]
    await launcher.prepare(jobs)
    outside = f"pdmcp-{RUN}-out"
    assert [c for c in docker.ran("network", "create") if c[-1] == outside] != []
    assert len([c for c in docker.ran("network", "create") if c[-1] == outside]) == 1
    runs = docker.ran("run")
    assert len(runs) == 2 and all(SQUID_IMAGE in run for run in runs)
    assert len(docker.ran("network", "connect", "--alias", "egress")) == 2
    for job in jobs:
        log_dir = tmp_path / "run" / "egress" / job.job_id
        conf = (log_dir / "squid.conf").read_text()
        assert conf == squid_conf(allowlist(job.family, job.system.id))
        spool = docker.squid_spool(job.job_id)
        assert not spool.is_relative_to(tmp_path / "run")
        assert spool.stat().st_mode & 0o777 == 0o777
        assert spool.parent.stat().st_mode & 0o777 == 0o700
        assert log_dir.stat().st_mode & 0o022 == 0
        assert (log_dir / "squid.conf").stat().st_mode & 0o777 == 0o644
        (run,) = [r for r in runs if f"pdmcp-{RUN}-{job.job_id}-proxy" in r]
        text = " ".join(run)
        assert f"type=bind,src={log_dir}/squid.conf,dst=/etc/squid/squid.conf,readonly" in text
        assert f"type=bind,src={spool},dst=/var/log/squid-bench" in text
        for flag in ("--read-only", "--cap-drop ALL", "--user proxy", "--pull never", "-d"):
            assert flag in text
        assert run[-3:] == ["-N", "-f", "/etc/squid/squid.conf"]
    assert probes.jobs == ["control", *[job.job_id for job in jobs]]
    await launcher.close()


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path


def _real(tmp_path: Path, docker: FakeDocker, repo: Path) -> Sandbox:
    async def verify(sandbox: Sandbox) -> dict[str, str]:
        return await verify_image(sandbox, repo_root=repo)

    (tmp_path / "run").mkdir(exist_ok=True)
    return Sandbox(_config(tmp_path), docker=docker, verify=verify)


async def test_the_real_check_passes_and_records_the_sandbox(tmp_path: Path) -> None:
    docker = FakeDocker()
    sandbox = _real(tmp_path, docker, _git_repo(tmp_path / "repo"))
    record = await sandbox.verify(sandbox)
    written = json.loads((tmp_path / "run" / "sandbox.json").read_text())
    assert written == record
    assert record["image"] == IMAGE and record["revision"] == COMMIT
    assert record["engine_version"] == "29.9.2" and record["docker_context"] == "desktop-linux"
    assert record["proxy_image"] == SQUID_IMAGE
    assert docker.ran("image", "inspect", "--format", "{{json .}}", SQUID_IMAGE)


@pytest.mark.parametrize("scheme", ["unix://", "tcp://"])
async def test_sandbox_metadata_refuses_a_key_in_a_daemon_endpoint(
    tmp_path: Path, scheme: str
) -> None:
    key = "AIza" + "Q" * 35
    sandbox = _real(
        tmp_path, FakeDocker(context_host=f"{scheme}/daemon\n{key}"), _git_repo(tmp_path / "repo")
    )
    with pytest.raises(KeyLeakError) as error:
        await sandbox.verify(sandbox)
    assert not (tmp_path / "run" / "sandbox.json").exists()
    assert key not in "".join(traceback.format_exception(error.value))


async def test_prepare_refuses_a_daemon_that_is_not_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    remote = FakeDocker(context_host="tcp://10.0.0.5:2376")
    launcher = ContainerLauncher(
        _real(tmp_path, remote, repo), probe=(probes := Probes()), control=probes.control
    )
    with pytest.raises(BenchmarkValidationError, match="unix://"):
        await launcher.prepare([_job("scripted-anthropic")])
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.5:2376")
    local = FakeDocker()
    launcher = ContainerLauncher(
        _real(tmp_path, local, repo), probe=(probes := Probes()), control=probes.control
    )
    with pytest.raises(BenchmarkValidationError, match="DOCKER_HOST"):
        await launcher.prepare([_job("scripted-anthropic")])
    for docker in (remote, local):
        assert not docker.ran("run") and not docker.ran("network", "create")
        assert not docker.ran("image", "inspect")


async def test_the_real_check_refuses_a_dirty_tree(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    # Amendment 1: a config that hides untracked files cannot hide them from the check.
    subprocess.run(
        ["git", "-C", str(repo), "config", "status.showUntrackedFiles", "no"], check=True
    )
    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    sandbox = _real(tmp_path, FakeDocker(), repo)
    with pytest.raises(BenchmarkValidationError, match="dirty"):
        await sandbox.verify(sandbox)


async def test_prepare_refuses_an_image_from_another_commit(tmp_path: Path) -> None:
    docker = FakeDocker(revision="d" * 40)
    probes = Probes()
    launcher = ContainerLauncher(
        _real(tmp_path, docker, _git_repo(tmp_path / "r")), probes, probes.control
    )
    with pytest.raises(BenchmarkValidationError, match="d" * 40):
        await launcher.prepare([_job("scripted-anthropic")])
    assert not docker.ran("run")
    other = FakeDocker(image_id="sha256:" + "e" * 64)
    sandbox = _real(tmp_path, other, _git_repo(tmp_path / "r2"))
    with pytest.raises(BenchmarkValidationError, match="sha256:e"):
        await sandbox.verify(sandbox)


async def test_prepare_refuses_a_missing_proxy_image(tmp_path: Path) -> None:
    docker = FakeDocker(squid_present=False)
    sandbox = _real(tmp_path, docker, _git_repo(tmp_path / "repo"))
    with pytest.raises(BenchmarkValidationError, match="ubuntu/squid"):
        await sandbox.verify(sandbox)


async def test_prepare_refuses_an_engine_older_than_28(tmp_path: Path) -> None:
    docker = FakeDocker(engine="27.5.1")
    sandbox = _real(tmp_path, docker, _git_repo(tmp_path / "repo"))
    with pytest.raises(BenchmarkValidationError, match="27.5.1"):
        await sandbox.verify(sandbox)


def test_sandbox_config_refuses_a_tag(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="python-docs:latest"):
        _config(tmp_path, image="python-docs:latest")


async def test_stop_worker_removes_only_its_own_container(tmp_path: Path) -> None:
    docker = FakeDocker()
    launcher = ContainerLauncher(
        _sandbox(tmp_path, docker), probe=(probes := Probes()), control=probes.control
    )
    job = _job("scripted-anthropic")
    name = launcher.sandbox.worker_name(job.job_id)
    # Even the same run ID from an earlier invocation is not ours.
    other = ContainerLauncher(_sandbox(tmp_path, docker))
    foreign_id = await other.sandbox.create_resource(name, *other.worker_create_argv(job)[1:])
    await launcher.stop_worker(job)
    assert not docker.ran("rm")
    await other.sandbox.remove_container(foreign_id)
    initial_removals = len(docker.ran("rm"))

    second = _job("scripted-anthropic", "docs-fetch")
    await launcher.prepare([job, second])
    await launcher.stop_worker(job)
    assert docker.ran("rm", "-f", f"id-{name}")
    assert f"id-{name}" not in docker.resources
    await launcher.stop_worker(job)  # repeated teardown has nothing left to remove
    assert len(docker.ran("rm")) == initial_removals + 1

    docker.fail = lambda args: args[:2] == ["rm", "-f"]
    with pytest.raises(BenchmarkValidationError, match="boom"):
        await launcher.stop_worker(second)
    docker.fail = None
    await launcher.close()  # retry the failed remove before closing the networks
    assert not docker.resources


# --- collection and close ----------------------------------------------------------------------

PROBE_PREFIX = (
    "2026-11-04T10:00:00.001Z 172.30.0.2 CONNECT example.com 443 403 TCP_DENIED\n"
    "2026-11-04T10:00:00.002Z 172.30.0.2 GET 192.168.65.254 50000 403 TCP_DENIED\n"
)
PARTIAL = PROBE_PREFIX + (
    "2026-11-04T10:00:01.000Z 172.30.0.3 CONNECT api.anthropic.com 443 200 TCP_TUNNEL\n"
)


async def _opened(tmp_path: Path, docker: FakeDocker, job_id: str = "j") -> tuple[Sandbox, Egress]:
    sandbox = _sandbox(tmp_path, docker)
    egress = await sandbox.open_egress(job_id, (("api.anthropic.com", 443),))
    _logs(docker.squid_spool(job_id))
    return sandbox, egress


def _logs(log_dir: Path, access: str = PARTIAL, cache: str = "squid started\n") -> None:
    """The two files a running Squid writes into its bind-mounted log directory."""
    (log_dir / "access.log").write_text(access, encoding="utf-8")
    (log_dir / "cache.log").write_text(cache, encoding="utf-8")


async def test_network_remove_failure_names_the_owned_id_and_scrubs_daemon_stderr(
    tmp_path: Path,
) -> None:
    sandbox = _sandbox(tmp_path, FakeDocker())
    await sandbox.ensure_outside()
    owned = sandbox.outside_id
    assert owned is not None

    async def disconnected(argv: Sequence[str]) -> Any:
        return _done(argv, 1, err=f"daemon disconnected: {SYNTHETIC}")

    sandbox.docker = disconnected
    problems = await ContainerLauncher(sandbox).close()
    assert len(problems) == 1
    assert owned in problems[0] and "daemon disconnected: [redacted]" in problems[0]
    assert SYNTHETIC not in problems[0]


@pytest.mark.parametrize("cli_raises", [False, True])
async def test_removal_diagnostic_cannot_expose_an_adjacent_key(
    tmp_path: Path, cli_raises: bool
) -> None:
    sandbox = _sandbox(tmp_path, FakeDocker())
    google = "AIza" + "g" * 35

    async def disconnected(argv: Sequence[str]) -> Any:
        detail = f"unavailable: {google}{SYNTHETIC}"
        if cli_raises:
            raise OSError(detail)
        return _done(argv, 1, err=detail)

    sandbox.docker = disconnected
    owned = "b" * 64
    with pytest.raises(BenchmarkValidationError, match=owned) as error:
        await sandbox.run("network", "rm", owned)
    trace = "".join(traceback.format_exception(error.value))
    assert SYNTHETIC not in trace and google not in trace


@pytest.mark.parametrize("kind", ["container", "network"])
async def test_remove_cli_start_failure_identifies_owned_resource_and_scrubs_error(
    tmp_path: Path, kind: str
) -> None:
    sandbox = _sandbox(tmp_path, FakeDocker())
    owned = "a" * 64

    async def unavailable(argv: Sequence[str]) -> Any:
        raise OSError(f"cannot start CLI: {SYNTHETIC}")

    sandbox.docker = unavailable
    with pytest.raises(BenchmarkValidationError, match=owned) as error:
        if kind == "container":
            await sandbox.remove_container(owned)
        else:
            await sandbox.run("network", "rm", owned)
    trace = "".join(traceback.format_exception(error.value))
    assert SYNTHETIC not in trace and "cannot start CLI: [redacted]" in trace


async def test_close_stops_each_proxy_then_removes_what_it_created(tmp_path: Path) -> None:
    docker = FakeDocker()
    keys = _keys(tmp_path, "ANTHROPIC_API_KEY=dummy")
    launcher = ContainerLauncher(
        _sandbox(tmp_path, docker, keys_file=keys),
        probe=(probes := Probes()),
        control=probes.control,
    )
    jobs = [_job("claude-sonnet-5-5", "no-docs"), _job("scripted-anthropic", "no-docs")]
    await launcher.prepare(jobs)
    for job in jobs:
        _logs(docker.squid_spool(job.job_id))
    start = len(docker.calls)
    problems = await launcher.close()
    assert problems == []
    tail = [" ".join(c[1:3]) + " " + c[-1] for c in docker.calls[start:]]
    expected = [f"rm -f id-pdmcp-{RUN}-{job.job_id}" for job in jobs]
    for job in jobs:
        proxy, net = f"pdmcp-{RUN}-{job.job_id}-proxy", f"pdmcp-{RUN}-{job.job_id}-net"
        expected += [
            f"inspect --format id-{proxy}",
            f"stop -t id-{proxy}",
            f"inspect --format id-{proxy}",
            f"rm -f id-{proxy}",
            f"network rm id-{net}",
        ]
        collected = json.loads(
            (tmp_path / "run" / "egress" / job.job_id / "collected.json").read_text()
        )
        assert collected == {
            "ok": True,
            "problems": [],
            "state_before_stop": RUNNING,
            "state_after_stop": STOPPED,
        }
    assert tail == [*expected, f"network rm id-pdmcp-{RUN}-out"]
    assert docker.ran("stop", "-t", "5")


@pytest.mark.parametrize(
    "before,after,named",
    [
        (
            {"Running": False, "ExitCode": 137, "OOMKilled": False},
            {"Running": False, "ExitCode": 137, "OOMKilled": False},
            "137",
        ),
        (
            {"Running": True, "ExitCode": 0, "OOMKilled": True},
            {"Running": False, "ExitCode": 0, "OOMKilled": True},
            "OOMKilled",
        ),
        (RUNNING, {"Running": False, "ExitCode": 137, "OOMKilled": False}, "137"),
        (RUNNING, {"Running": True, "ExitCode": 0, "OOMKilled": False}, "running"),
    ],
)
async def test_an_abnormal_proxy_exit_fails_the_collection(
    tmp_path: Path, before: dict[str, Any], after: dict[str, Any], named: str
) -> None:
    # Every cleanup command succeeds and the log is well formed but may be partial. The third
    # case is a forced kill after our own stop (round-4 item 1); the fourth a proxy that is
    # still running after it (amendment 7).
    sandbox, egress = await _opened(tmp_path, FakeDocker(states=(before, after)))
    problems = await sandbox.close_egress(egress)
    collected = json.loads((egress.log_dir / "collected.json").read_text())
    assert collected["ok"] is False and problems == collected["problems"] != []
    assert any(named in problem for problem in problems)
    assert (collected["state_before_stop"], collected["state_after_stop"]) == (before, after)


@pytest.mark.parametrize(
    "line,key",
    [
        (f"WARNING: header {SYNTHETIC}\n", SYNTHETIC),
        (json.dumps({"error": "bad\n" + "AIza" + "g" * 35}) + "\n", "AIza" + "g" * 35),
    ],
    ids=["plain", "structured"],
)
async def test_close_redacts_a_key_shaped_proxy_log_and_raises_after_cleanup(
    tmp_path: Path, line: str, key: str
) -> None:
    docker = FakeDocker()
    launcher = ContainerLauncher(
        _sandbox(tmp_path, docker), probe=(probes := Probes()), control=probes.control
    )
    job = _job("scripted-anthropic")
    await launcher.prepare([job])
    log_dir = tmp_path / "run" / "egress" / job.job_id
    _logs(docker.squid_spool(job.job_id), cache=line)
    with pytest.raises(KeyLeakError, match="cache.log"):
        await launcher.close()
    assert (log_dir / "cache.log").read_bytes() == line.replace(key, "[redacted]").encode()
    assert docker.ran("rm", "-f", f"id-pdmcp-{RUN}-{job.job_id}-proxy")
    assert docker.ran("network", "rm", f"id-pdmcp-{RUN}-out")
    assert json.loads((log_dir / "collected.json").read_text())["ok"] is False
    assert (tmp_path / "run/probe" / f"{job.job_id}.collection-pending").is_file()


@pytest.mark.parametrize("failure", [MemoryError, PermissionError])
async def test_a_failed_private_read_cannot_publish_or_certify_a_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[Exception]
) -> None:
    from benchmarks.agentic import sandbox as module

    job = _job("scripted-anthropic")
    sandbox, egress = await _opened(tmp_path, FakeDocker(), job.job_id)
    (egress.spool_dir / "cache.log").write_text(SYNTHETIC)
    read = module.read_log

    def unreadable(path: Path) -> bytes:
        if path.name == "cache.log":
            raise failure("cannot read private log")
        return read(path)

    monkeypatch.setattr(module, "read_log", unreadable)
    problems = await sandbox.close_egress(egress)
    assert problems and not sandbox.leaks
    assert not egress.spool_dir.parent.exists()
    assert not (egress.log_dir / "cache.log").exists()
    assert (egress.log_dir / "access.log").read_text() == PARTIAL
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False
    out = sandbox.config.out_dir
    assert (out / "probe" / f"{job.job_id}.collection-pending").is_file()
    assert write_egress_summary(out, [job], [])["isolation_probe"] == "fail"


async def test_scan_memory_error_leaves_only_complete_scanned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.agentic import sandbox as module

    sandbox, egress = await _opened(tmp_path, FakeDocker())
    (egress.spool_dir / "cache.log").write_text(SYNTHETIC)
    scrub = module.redact_key_shaped

    def exhausted(data: bytes) -> tuple[bytes, str | None]:
        if data == SYNTHETIC.encode():
            raise MemoryError("cannot scan")
        return scrub(data)

    monkeypatch.setattr(module, "redact_key_shaped", exhausted)
    assert await sandbox.close_egress(egress)
    assert not (egress.log_dir / "cache.log").exists()
    assert (egress.log_dir / "access.log").read_text() == PARTIAL
    assert (sandbox.config.out_dir / "probe/j.collection-pending").is_file()
    assert not egress.spool_dir.parent.exists()


async def test_a_linked_spool_is_neither_collected_nor_cleaned_through(tmp_path: Path) -> None:
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _logs(elsewhere, cache=SYNTHETIC)
    egress.spool_dir.rename(egress.spool_dir.with_name("moved"))
    egress.spool_dir.symlink_to(elsewhere, target_is_directory=True)
    problems = await sandbox.close_egress(egress)
    assert any("link" in problem for problem in problems)
    assert not sandbox.leaks and not egress.spool_dir.parent.exists()
    assert (elsewhere / "cache.log").read_text() == SYNTHETIC
    assert not (egress.log_dir / "cache.log").exists()
    assert not (egress.log_dir / "access.log").exists()
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False


async def test_only_intended_regular_files_leave_the_spool(tmp_path: Path) -> None:
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    canary = _canary(tmp_path)
    (egress.spool_dir / "collected.json").symlink_to(canary)
    (egress.spool_dir / "squid.conf").write_text(SYNTHETIC)
    (egress.spool_dir / "unexpected").write_text(SYNTHETIC)
    assert await sandbox.close_egress(egress) == []
    assert not egress.spool_dir.parent.exists()
    assert {p.name for p in egress.log_dir.iterdir()} == {
        "cache.log",
        "access.log",
        "squid.conf",
        "collected.json",
    }
    assert canary.read_text() == f"host secret {SYNTHETIC}\n"
    assert all(SYNTHETIC.encode() not in p.read_bytes() for p in egress.log_dir.iterdir())


async def test_close_keeps_going_after_a_failed_step(tmp_path: Path) -> None:
    docker = FakeDocker(fail=lambda args: args[:1] == ["stop"])
    sandbox, egress = await _opened(tmp_path, docker)
    problems = await sandbox.close_egress(egress)
    assert problems and any("stop" in p for p in problems)
    assert docker.ran("rm", "-f", egress.proxy_id)
    assert docker.ran("network", "rm", egress.network_id)
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False


def test_container_launcher_supports_scripted_and_every_live_api(tmp_path: Path) -> None:
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker()), probe=(probes := Probes()), control=probes.control
    )
    assert all(launcher.supports(family) for family in FAMILIES.values())


def test_cli_env_is_the_docker_allowlist() -> None:
    environ = {
        "PATH": "/bin",
        "HOME": "/h",
        "DOCKER_HOST": "unix:///x.sock",
        "ANTHROPIC_API_KEY": "not-a-real-key",
        "HTTPS_PROXY": "http://x:1",
        "LANG": "C",
    }
    assert cli_env(environ) == {"PATH": "/bin", "HOME": "/h", "DOCKER_HOST": "unix:///x.sock"}


def test_the_sandbox_module_loads_no_provider_sdk() -> None:
    # run.sh imports SQUID_IMAGE from it on the host.
    code = (
        "import sys\n"
        "import benchmarks.agentic.sandbox\n"
        "print(sorted(m for m in ('anthropic', 'openai', 'google.genai') if m in sys.modules))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


# --- the 0777 log directory: the host never follows a link in it (security review) -----------


def _canary(tmp_path: Path) -> Path:
    canary = tmp_path / "canary.txt"  # a stand-in for a host file outside the log directory
    canary.write_text(f"host secret {SYNTHETIC}\n", encoding="utf-8")
    return canary


async def test_a_linked_access_log_is_never_followed(tmp_path: Path) -> None:
    canary = _canary(tmp_path)
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    access = egress.spool_dir / "access.log"
    access.unlink()
    access.symlink_to(canary)
    problems = await sandbox.close_egress(egress)
    assert canary.read_text() == f"host secret {SYNTHETIC}\n"  # unchanged
    assert sandbox.leaks == []  # unread: its key shape was never seen
    assert not os.path.lexists(egress.log_dir / "access.log")
    assert any("access.log" in p for p in problems)
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False


@pytest.mark.parametrize("kind", ["fifo", "directory"])
async def test_a_cache_log_that_is_not_a_regular_file_is_refused(tmp_path: Path, kind: str) -> None:
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    cache = egress.spool_dir / "cache.log"
    cache.unlink()
    if kind == "fifo":
        os.mkfifo(cache)
    else:
        cache.mkdir()
    async with asyncio.timeout(10):  # a FIFO must never block the teardown
        problems = await sandbox.close_egress(egress)
    assert any("cache.log" in p for p in problems)
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False


async def test_a_key_shaped_log_is_scrubbed_into_a_new_file(tmp_path: Path) -> None:
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    cache = egress.spool_dir / "cache.log"
    cache.write_text(f"WARNING: header {SYNTHETIC}\n", encoding="utf-8")
    original = tmp_path / "original-inode"
    os.link(cache, original)  # a second name for the original inode
    await sandbox.close_egress(egress)
    cache = egress.log_dir / "cache.log"
    assert cache.read_text() == "WARNING: header [redacted]\n"
    assert original.read_text() == f"WARNING: header {SYNTHETIC}\n"  # never written
    assert cache.stat().st_ino != original.stat().st_ino
    assert sandbox.leaks and [p.name for p in egress.log_dir.glob("*.tmp")] == []


async def test_a_linked_collected_json_is_replaced_not_followed(tmp_path: Path) -> None:
    canary = _canary(tmp_path)
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    collected = egress.log_dir / "collected.json"
    collected.symlink_to(canary)
    await sandbox.close_egress(egress)
    assert canary.read_text() == f"host secret {SYNTHETIC}\n"
    assert not collected.is_symlink() and json.loads(collected.read_text())["ok"] is True


async def test_a_linked_log_dir_is_refused(tmp_path: Path) -> None:
    # Before the proxy starts: a log directory that already exists as a link is refused.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    docker = FakeDocker()
    sandbox = _sandbox(tmp_path, docker)
    (tmp_path / "run" / "egress").mkdir(parents=True)
    (tmp_path / "run" / "egress" / "j").symlink_to(elsewhere)
    with pytest.raises(BenchmarkValidationError, match="link"):
        await sandbox.open_egress("j", (("api.anthropic.com", 443),))
    assert not docker.ran("run") and list(elsewhere.iterdir()) == []
    # At teardown: a log directory swapped for a link is neither read nor written.
    (tmp_path / "late").mkdir()
    job = _job("claude-sonnet-5-5")
    sandbox, egress = await _opened(tmp_path / "late", FakeDocker(), job.job_id)
    out = sandbox.config.out_dir
    probe = out / "probe"
    probe.mkdir(exist_ok=True)
    (probe / "control.json").write_text('{"passed": true}')
    (probe / f"{job.job_id}.json").write_text(
        json.dumps({"passed": True, "proxy_prefix": PROBE_PREFIX.splitlines()})
    )
    _logs(elsewhere, cache=f"key {SYNTHETIC}\n")
    moved = egress.log_dir.with_name("moved")
    egress.log_dir.rename(moved)
    egress.log_dir.symlink_to(elsewhere)
    problems = await sandbox.close_egress(egress)
    assert any("link" in p for p in problems) and sandbox.leaks == []
    assert (elsewhere / "cache.log").read_text() == f"key {SYNTHETIC}\n"
    assert not (elsewhere / "collected.json").exists()
    (elsewhere / "collected.json").write_text('{"ok": true}')
    assert write_egress_summary(out, [job], [])["isolation_probe"] == "fail"
    # A regular directory with exactly the same files cannot undo teardown's refusal.
    egress.log_dir.unlink()
    elsewhere.rename(egress.log_dir)
    assert write_egress_summary(out, [job], [])["isolation_probe"] == "fail"


async def test_missing_docs_index_stops_preparation_before_probes(tmp_path: Path) -> None:
    probes = Probes()
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker()), probe=probes, control=probes.control
    )
    with pytest.raises(BenchmarkValidationError, match="index"):
        await launcher.prepare([_job("scripted-anthropic", "python-docs")])
    assert probes.jobs == []


@pytest.mark.parametrize("second", ["", 'OPENAI_API_KEY="dummy has spaces"'])
async def test_invalid_second_key_refuses_preparation_after_all_probes(
    tmp_path: Path, second: str
) -> None:
    keys = _keys(tmp_path, f"ANTHROPIC_API_KEY=dummy-one\n{second}")
    probes = Probes()
    launcher = ContainerLauncher(
        _sandbox(tmp_path, FakeDocker(), keys_file=keys), probe=probes, control=probes.control
    )
    jobs = [_job("claude-sonnet-5-5"), _job("gpt-6.1-sol")]
    try:
        with pytest.raises(BenchmarkValidationError, match="OPENAI_API_KEY") as error:
            await launcher.prepare(jobs)
        assert "dummy has spaces" not in str(error.value)
        assert probes.jobs == ["control", *(job.job_id for job in jobs)]
    finally:
        await launcher.close()


async def test_worker_cleanup_waits_for_daemon_auto_removal(tmp_path: Path) -> None:
    responses = iter(("removal of container owned-id is already in progress", "No such container"))

    async def removing(argv: Sequence[str]) -> Any:
        assert list(argv) == ["docker", "rm", "-f", "owned-id"]
        return _done(argv, 1, err=next(responses))

    sandbox = Sandbox(_config(tmp_path), docker=removing, verify=_verified)
    await sandbox.remove_container("owned-id")
    assert list(responses) == []


async def test_worker_cleanup_reports_stalled_daemon_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("benchmarks.agentic.sandbox._RESOURCE_RECOVERY_S", 0)

    async def stalled(argv: Sequence[str]) -> Any:
        return _done(argv, 1, err="removal of container owned-id is already in progress")

    sandbox = Sandbox(_config(tmp_path), docker=stalled, verify=_verified)
    with pytest.raises(BenchmarkValidationError, match="already in progress"):
        await sandbox.remove_container("owned-id")


@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_cleanup_failure_preserves_pending_cancellation(
    tmp_path: Path, repeat_cancel: bool
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def failing(argv: Sequence[str]) -> Any:
        started.set()
        await release.wait()
        return _done(argv, 1, err="daemon disconnected")

    box = Sandbox(_config(tmp_path), docker=failing, verify=_verified)
    cleanup = asyncio.create_task(box.remove_container("owned-id"))
    await asyncio.wait_for(started.wait(), 1)
    cleanup.cancel("operator interrupt")
    if repeat_cancel:
        await asyncio.sleep(0)
        cleanup.cancel("second interrupt")
    release.set()
    with pytest.raises(asyncio.CancelledError, match="operator interrupt") as error:
        await cleanup
    assert isinstance(error.value.__cause__, BenchmarkValidationError)
    assert "daemon disconnected" in str(error.value.__cause__)


@pytest.mark.parametrize("resource", ["network", "container", "partial"])
async def test_cancelled_create_preserves_signal_when_rollback_fails(
    tmp_path: Path, resource: str
) -> None:
    name = "late"
    docker = LateCreate(name)
    # A failed create is recovered by label. Its first removal must not skip the next ID.
    docker.fail = lambda args: (
        args[0] == "create" and resource == "partial" or ("rm" in args and args[-1] == "id-late")
    )
    box = _sandbox(tmp_path, docker)
    if resource == "partial":
        docker.resources.update(dict.fromkeys(("id-late", "id-extra"), {box._ownership(name)}))
    args = ("network", "create", name) if resource == "network" else ("create", "--name", name)
    create = asyncio.create_task(box.create_resource(name, *args))
    await asyncio.wait_for(docker.started.wait(), 2)
    create.cancel("signal SIGTERM")
    docker.release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await create
    assert isinstance(error.value.__cause__, BenchmarkValidationError)
    assert "rm" in str(error.value.__cause__) and "boom" in str(error.value.__cause__)
    assert "id-extra" not in docker.resources


@pytest.mark.parametrize("failed_rm", ["proxy", "net"])
async def test_cancelled_open_egress_finishes_both_removals(tmp_path: Path, failed_rm: str) -> None:
    connecting, connected = asyncio.Event(), asyncio.Event()
    removing, release = asyncio.Event(), asyncio.Event()

    class InterruptedConnect(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["network", "connect"]:
                connecting.set()
                await connected.wait()
            if argv[1:3] == ["rm", "-f"]:
                removing.set()
                await release.wait()
            return await super().__call__(argv)

    docker = InterruptedConnect(fail=lambda args: "rm" in args and args[-1].endswith(failed_rm))
    opening = asyncio.create_task(_sandbox(tmp_path, docker).open_egress("j", ()))
    await asyncio.wait_for(connecting.wait(), 2)
    opening.cancel("signal SIGTERM")
    connected.set()
    await asyncio.wait_for(removing.wait(), 2)
    opening.cancel("signal SIGHUP")
    await asyncio.sleep(0)
    assert not opening.done()
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await opening
    assert isinstance(error.value.__cause__, BenchmarkValidationError)
    assert "boom" in str(error.value.__cause__)
    assert docker.ran("rm", "-f", "id-pdmcp-r1-j-proxy")
    assert docker.ran("network", "rm", "id-pdmcp-r1-j-net")


async def test_launcher_close_preserves_both_failed_network_ids_on_cancellation(
    tmp_path: Path,
) -> None:
    job = _job("scripted-anthropic")
    network = f"pdmcp-{RUN}-{job.job_id}-net"
    docker = LateCreate(network)
    docker.fail = lambda args: args[:2] == ["network", "rm"]
    sandbox = _sandbox(tmp_path, docker)
    probes = Probes()
    interrupted: list[asyncio.CancelledError] = []

    class Launcher(ContainerLauncher):
        async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
            try:
                await super().prepare(jobs)
            except asyncio.CancelledError as exc:
                interrupted.append(exc)
                raise

    task = asyncio.create_task(
        run_jobs(
            [job],
            launcher=Launcher(sandbox, probe=probes, control=probes.control),
            ledger=Mock(),
            on_message=AsyncMock(),
            log_dir=tmp_path / "logs",
            cwd=tmp_path,
        )
    )
    await asyncio.wait_for(docker.started.wait(), 2)
    task.cancel("signal SIGTERM")
    docker.release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    assert error.value is interrupted[0]
    trace = "".join(traceback.format_exception(error.value))
    for name in (network, sandbox.outside):
        assert docker.ran("network", "rm", f"id-{name}")
        assert f"id-{name}" in trace


async def test_cancelled_create_preserves_signal_when_cli_cannot_start(tmp_path: Path) -> None:
    starting, release = asyncio.Event(), asyncio.Event()
    failure = OSError("cannot start docker")

    async def unavailable(argv: Sequence[str]) -> Any:
        starting.set()
        await release.wait()
        raise failure

    box = Sandbox(_config(tmp_path), docker=unavailable)
    task = asyncio.create_task(box.ensure_outside())
    await asyncio.wait_for(starting.wait(), 2)
    task.cancel("signal SIGTERM")
    release.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as error:
        await task
    assert error.value.__cause__ is failure


def test_docker_rollback_helper_isolated_from_the_foreground_group() -> None:
    # Only this test's new session can receive the group signal.
    host = """
import asyncio, json, signal, sys
from benchmarks.agentic.sandbox import docker_runner
received = []
signal.signal(signal.SIGHUP, lambda sig, frame: received.append(sig))
result = asyncio.run(docker_runner({})([sys.executable, "-c", sys.argv[1]]))
print(json.dumps([result.returncode, result.stdout.decode().strip(), bool(received)]))
"""
    helper = """
import os, signal, sys
parent = os.getppid()
assert parent > 1 and os.getpgid(parent) == os.getsid(parent) == parent
os.killpg(parent, signal.SIGHUP)
print("rollback complete" if sys.stdin.read(1) == "" else "read host input")
"""
    run = subprocess.run(
        [sys.executable, "-c", host, helper],
        start_new_session=True,
        input="host input",
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(run.stdout) == [0, "rollback complete", True]


async def _run_prepare(box: Sandbox, job: WorkerJob) -> None:
    probes = Probes()
    await run_jobs(
        [job],
        launcher=ContainerLauncher(box, probe=probes, control=probes.control),
        ledger=Mock(),
        on_message=AsyncMock(),
        log_dir=box.config.out_dir / "logs",
        cwd=box.config.out_dir,
    )


@pytest.mark.parametrize("owned_ids", [("owned-a",), ("owned-a", "owned-b"), ()])
async def test_lost_network_create_response_during_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned_ids: tuple[str, ...]
) -> None:
    monkeypatch.setattr("benchmarks.agentic.sandbox._RESOURCE_RECOVERY_S", 0.2)

    class LostResponse(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["network", "create"]:
                self.calls.append(list(argv))
                return _done(argv, 1, err="lost create response")
            result = await super().__call__(argv)
            if argv[1:3] == ["network", "ls"] and len(self.ran("network", "ls")) == 1:
                assert not result.stdout
                # The daemon publishes after the first list has taken its snapshot.
                (create,) = self.ran("network", "create")
                labels = {v for k, v in zip(create, create[1:]) if k == "--label"}
                self.resources.update({i: labels for i in owned_ids})
            return result

    docker = LostResponse()
    box = _sandbox(tmp_path, docker)
    foreign = {"foreign-id": {f"{RUN_LABEL}={RUN}"}}
    docker.resources.update(foreign)
    with pytest.raises(BenchmarkValidationError, match="lost create response") as caught:
        await _run_prepare(box, _job("scripted-anthropic"))
    selector = f"label={box._ownership(box.outside)}"
    lookups = docker.ran("network", "ls")
    assert 2 <= len(lookups) <= 4
    assert all(c[3:] == ["--no-trunc", "--quiet", "--filter", selector] for c in lookups)
    assert [c[-1] for c in docker.ran("network", "rm")] == list(owned_ids)
    assert docker.resources == foreign
    if not owned_ids:
        assert "outcome is unknown" in str(caught.value)
        assert selector in str(caught.value)


@pytest.mark.parametrize("network_failure", ["exit", "start", "timeout", "spool"])
def test_failed_connect_reports_both_removals_to_caller_and_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    network_failure: str,
) -> None:
    from benchmarks.__main__ import main

    if network_failure == "spool":
        cleanup = tempfile.TemporaryDirectory.cleanup

        def denied(staging: tempfile.TemporaryDirectory[str]) -> None:
            cleanup(staging)
            raise OSError(f"private spool failure: {SYNTHETIC}")

        monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", denied)

    job = _job("scripted-anthropic")

    class Disconnected(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            args = argv[1:3]
            if (
                args == ["network", "connect"]
                or args == ["rm", "-f"]
                or (args == ["network", "rm"] and argv[-1].endswith("-net"))
            ):
                self.calls.append(list(argv))
                if args == ["network", "rm"]:
                    if network_failure == "start":
                        raise OSError(f"cannot start {SYNTHETIC}")
                    if network_failure == "timeout":
                        raise subprocess.TimeoutExpired(["docker", SYNTHETIC], 120)
                return _done(argv, 1, err=f"{args[-1]} disconnected {SYNTHETIC}")
            return await super().__call__(argv)

    docker = Disconnected()
    box = _sandbox(tmp_path, docker)
    failures = []

    def run(*args: Any, **kwargs: Any) -> None:
        try:
            asyncio.run(_run_prepare(box, job))
        except BenchmarkValidationError as exc:
            failures.append(exc)
            raise

    monkeypatch.setattr("benchmarks.agentic.run.run_agentic", run)
    with pytest.raises(SystemExit) as stopped:
        main(["agentic-run", "--corpus", "unused", "--out", str(tmp_path)])
    assert stopped.value.code == 2
    summary = capsys.readouterr().err
    (failure,) = failures
    proxy = f"id-{box.worker_name(job.job_id)}-proxy"
    network = f"id-{box.worker_name(job.job_id)}-net"
    assert [c[-1] for c in docker.calls if "rm" in c] == [proxy, network, f"id-{box.outside}"]
    for message in (str(failure), summary):
        assert proxy in message and network in message and "connect disconnected" in message
        if network_failure == "spool":
            assert "private spool failure: [redacted]" in message
        assert SYNTHETIC not in message
    assert SYNTHETIC not in "".join(traceback.format_exception(failure))


@pytest.mark.parametrize("lookup_failure", ["exit", "start", "timeout", "multiple ids"])
async def test_partial_proxy_recovery_reports_safe_selector_or_all_failed_ids(
    tmp_path: Path,
    lookup_failure: str,
) -> None:
    job = _job("scripted-anthropic")

    class PartialProxy(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["run", "-d"]:
                await super().__call__(argv)  # created, but the caller did not receive an ID
                return _done(argv, 1, err="proxy start failed")
            if argv[1:3] == ["container", "ls"]:
                self.calls.append(list(argv))
                if lookup_failure == "start":
                    raise OSError(f"cannot start {SYNTHETIC}")
                if lookup_failure == "timeout":
                    raise subprocess.TimeoutExpired(["docker", SYNTHETIC], 120)
                if lookup_failure == "exit":
                    return _done(argv, 1, err=f"daemon unavailable {SYNTHETIC}")
                owned = next(i for i in self.resources if i.endswith("-proxy"))
                self.resources["second-id"] = self.resources[owned]
                return _done(argv, out=f"{owned}\nsecond-id")
            if argv[1:3] == ["rm", "-f"]:
                self.calls.append(list(argv))
                return _done(argv, 1, err=f"removal failed {SYNTHETIC}")
            return await super().__call__(argv)

    docker = PartialProxy()
    box = _sandbox(tmp_path, docker)
    with pytest.raises(BenchmarkValidationError) as caught:
        await _run_prepare(box, job)
    proxy = f"{box.worker_name(job.job_id)}-proxy"
    selector = f"label={box._ownership(proxy)}"
    assert docker.ran("container", "ls")[0][-1] == selector
    message = str(caught.value)
    assert "proxy start failed" in message
    if lookup_failure == "multiple ids":
        ids = [f"id-{proxy}", "second-id"]
        assert [c[-1] for c in docker.ran("rm", "-f")] == ids
        assert all(i in message for i in ids)
    else:
        assert not docker.ran("rm", "-f")
        assert selector in message
        reason = {"exit": "daemon unavailable", "start": "cannot start", "timeout": "timed out"}
        assert reason[lookup_failure] in message
    assert SYNTHETIC not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("cancel_during_create", [True, False])
async def test_repeated_interrupt_during_partial_proxy_recovery_retains_selector(
    tmp_path: Path,
    cancel_during_create: bool,
) -> None:
    job = _job("scripted-anthropic")
    proxy = f"pdmcp-{RUN}-{job.job_id}-proxy"
    removing, removed = asyncio.Event(), asyncio.Event()

    class InterruptedLookup(LateCreate):
        async def __call__(self, argv: Sequence[str]) -> Any:
            if argv[1:3] == ["container", "ls"]:
                self.calls.append(list(argv))
                raise OSError(f"daemon unavailable {SYNTHETIC}")
            if argv[1:3] == ["network", "rm"]:
                removing.set()
                await removed.wait()
            return await super().__call__(argv)

    docker = InterruptedLookup(proxy)
    docker.fail = lambda args: (
        args[:2] == ["run", "-d"]
        or (args[:2] == ["network", "rm"] and (cancel_during_create or args[-1].endswith("-out")))
    )
    box = _sandbox(tmp_path, docker)
    task = asyncio.create_task(_run_prepare(box, job))
    await asyncio.wait_for(docker.started.wait(), 2)
    if cancel_during_create:
        task.cancel("signal SIGTERM")
    docker.release.set()
    await asyncio.wait_for(removing.wait(), 2)
    if not cancel_during_create:
        task.cancel("signal SIGTERM")
        await asyncio.sleep(0)
    task.cancel("second interrupt")
    removed.set()
    with pytest.raises(asyncio.CancelledError, match="signal SIGTERM") as caught:
        await task
    trace = "".join(traceback.format_exception(caught.value))
    assert docker.ran("container", "ls")[0][-1] == f"label={box._ownership(proxy)}"
    assert f"label={box._ownership(proxy)}" in trace and "daemon unavailable" in trace
    assert SYNTHETIC not in trace
    assert [c[-1] for c in docker.ran("network", "rm")] == [
        f"id-{box.worker_name(job.job_id)}-net",
        f"id-{box.outside}",
    ]
    assert f"id-{box.outside}" in trace
    if cancel_during_create:
        assert f"id-{box.worker_name(job.job_id)}-net" in trace


def test_cli_keeps_rollback_ids_when_an_earlier_proxy_log_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from benchmarks.__main__ import main

    first = _job("scripted-anthropic")
    second = _job("scripted-anthropic", "docs-fetch")
    docker = FakeDocker()
    box = _sandbox(tmp_path, docker)
    failed_proxy = f"id-{box.worker_name(second.job_id)}-proxy"
    failed_network = f"id-{box.worker_name(second.job_id)}-net"
    docker.fail = lambda args: (
        args[-1] in (failed_proxy, failed_network)
        and (args[:2] == ["network", "connect"] or "rm" in args)
    )

    async def probe(sandbox: Sandbox, egress: Egress, **kwargs: Any) -> dict[str, Any]:
        _logs(egress.spool_dir, cache=f"warning {SYNTHETIC}")
        return {"passed": True}

    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(
            run_jobs(
                [first, second],
                launcher=ContainerLauncher(box, probe=probe, control=Probes().control),
                ledger=Mock(),
                on_message=AsyncMock(),
                log_dir=tmp_path / "logs",
                cwd=tmp_path,
            )
        )

    monkeypatch.setattr("benchmarks.agentic.run.run_agentic", run)
    with pytest.raises(SystemExit) as stopped:
        main(["agentic-run", "--corpus", "unused", "--out", str(tmp_path)])
    assert stopped.value.code == 3
    message = capsys.readouterr().err
    assert failed_proxy in message and failed_network in message
    assert "cache.log" in message and SYNTHETIC not in message


# Exercise real prepare, close and evidence reporting with scripted jobs and no Docker process.
_PAIR = [_job("scripted-anthropic"), replace(_job("scripted-anthropic"), job_id="second")]
_FIRST = _PAIR[0].job_id
_PROXY = f"id-pdmcp-{RUN}-{_FIRST}-proxy"
_NETWORK = f"id-pdmcp-{RUN}-{_FIRST}-net"
_GOOGLE = "AIza" + "g" * 35
_PRIVATE_DETAIL = "private synthetic detail"


async def _run_collection_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    docker: FakeDocker,
    *,
    setup_first: Callable[[Path], None] | None = None,
    interruption: asyncio.CancelledError | None = None,
) -> None:
    box = _sandbox(tmp_path, docker)
    out = box.config.out_dir

    async def probe(sandbox: Sandbox, egress: Egress, **kwargs: Any) -> dict[str, Any]:
        _logs(egress.spool_dir, access=PROBE_PREFIX)
        record = {"passed": True, "proxy_prefix": PROBE_PREFIX.splitlines()}
        (out / "probe/control.json").write_text('{"passed":true}')
        (out / "probe" / f"{egress.job_id}.json").write_text(json.dumps(record))
        if setup_first is not None and egress.job_id == _FIRST:
            setup_first(egress.spool_dir)
        return record

    monkeypatch.setattr("benchmarks.agentic.host._supervise", AsyncMock(side_effect=interruption))
    try:
        await run_jobs(
            _PAIR,
            launcher=ContainerLauncher(box, probe=probe, control=Probes().control),
            ledger=Mock(),
            on_message=AsyncMock(),
            log_dir=out / "logs",
            cwd=tmp_path,
        )
    finally:
        for job in _PAIR:
            assert not docker.squid_spool(job.job_id).parent.exists()
        assert docker.ran("network", "rm", f"id-{box.outside}")
        assert json.loads((out / "egress/second/collected.json").read_text())["ok"] is True
        assert not (out / "probe/second.collection-pending").exists()


def _check_first_collection(tmp_path: Path, *, ok: bool) -> Path:
    out = tmp_path / "run"
    assert (out / "probe" / f"{_FIRST}.collection-pending").exists() == (not ok)
    assert write_egress_summary(out, _PAIR, [])["isolation_probe"] == ("pass" if ok else "fail")
    return out / "egress" / _FIRST


@pytest.mark.parametrize(
    "shape,interrupted",
    [("newline", False), ("adjacent", False), ("field-name", False), ("adjacent", True)],
)
async def test_run_jobs_withholds_key_shaped_collection_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, interrupted: bool
) -> None:
    class Docker(FakeDocker):
        async def __call__(self, argv: Sequence[str]) -> Any:
            result = await super().__call__(argv)
            if argv[1] == "inspect" and argv[-1] == _PROXY:
                state = json.loads(result.stdout)
                field = _PRIVATE_DETAIL + "\n" + _GOOGLE
                if shape == "field-name":
                    state["Error"] = {field: _PRIVATE_DETAIL}
                else:
                    state["Error"] = field + (SYNTHETIC if shape == "adjacent" else "")
                result.stdout = json.dumps(state).encode()
            return result

    failed_ids = {_PROXY, _NETWORK} if shape == "adjacent" else set()
    docker = Docker(fail=lambda args: "rm" in args and args[-1] in failed_ids)
    interrupt = asyncio.CancelledError("original test interruption") if interrupted else None
    with pytest.raises(asyncio.CancelledError if interrupted else KeyLeakError) as caught:
        await _run_collection_pair(tmp_path, monkeypatch, docker, interruption=interrupt)
    if interrupted:
        assert caught.value is interrupt and isinstance(caught.value.__cause__, KeyLeakError)
    record = (_check_first_collection(tmp_path, ok=False) / "collected.json").read_text()
    assert json.loads(record)["ok"] is False
    trace = "".join(traceback.format_exception(caught.value))
    assert "collected.json" in trace
    for output in (trace, record):
        assert all(private not in output for private in (_PRIVATE_DETAIL, _GOOGLE, SYNTHETIC))
        assert all(owned in output for owned in failed_ids)
    assert set(docker.resources) == failed_ids


@pytest.mark.parametrize(
    "fault", ["memory", "encoding", "publish", "publish-leak", "collector", "interrupted", "cancel"]
)
async def test_run_jobs_preserves_removal_ids_after_collection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from benchmarks.agentic import sandbox as module

    docker = FakeDocker(fail=lambda args: "rm" in args and args[-1] in {_PROXY, _NETWORK})
    interrupt = asyncio.CancelledError("original test interruption")
    if fault in {"encoding", "memory"}:
        dumps = json.dumps

        def fail_encode(value: Any, **kwargs: Any) -> str:
            if isinstance(value, dict) and "state_before_stop" in value and value["problems"]:
                raise (ValueError if fault == "encoding" else MemoryError)(_PRIVATE_DETAIL)
            return dumps(value, **kwargs)

        monkeypatch.setattr(json, "dumps", fail_encode)
    else:
        write = module.write_new
        fail_name = "collected.json" if fault.startswith("publish") else "cache.log"

        def fail_write(dir_fd: int, name: str, data: bytes, **kwargs: Any) -> None:
            if name == fail_name and len(docker.ran("stop")) == 1:
                if fault == "cancel":
                    raise interrupt
                if fault == "publish-leak":
                    raise KeyLeakError(_PRIVATE_DETAIL + _GOOGLE)
                raise (OSError if fault == "publish" else RuntimeError)(_PRIVATE_DETAIL + _GOOGLE)
            write(dir_fd, name, data, **kwargs)

        monkeypatch.setattr(module, "write_new", fail_write)
    cancelled = fault in {"interrupted", "cancel"}
    with pytest.raises(asyncio.CancelledError if cancelled else BenchmarkValidationError) as caught:
        await _run_collection_pair(
            tmp_path,
            monkeypatch,
            docker,
            interruption=interrupt if fault == "interrupted" else None,
        )
    if cancelled:
        assert caught.value is interrupt
    elif fault == "publish-leak":
        assert isinstance(caught.value, KeyLeakError)
    trace = "".join(traceback.format_exception(caught.value))
    assert _PROXY in trace and _NETWORK in trace
    assert _PRIVATE_DETAIL not in trace and _GOOGLE not in trace
    assert set(docker.resources) == {_PROXY, _NETWORK}
    _check_first_collection(tmp_path, ok=False)


@pytest.mark.parametrize("size_case", ["sparse", "growing", "limit"])
async def test_run_jobs_bounds_squid_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size_case: str
) -> None:
    limit = 8 * 1024 * 1024
    read_sizes: list[int] = []

    def prepare_log(spool: Path) -> None:
        path = spool / "cache.log"
        size = {"sparse": limit + 1, "growing": 1, "limit": limit}[size_case]
        with path.open("r+b") as stream:
            stream.truncate(size)
        inode = path.stat().st_ino
        read = os.read

        def instrument(fd: int, count: int) -> bytes:
            if os.fstat(fd).st_ino == inode:
                read_sizes.append(count)
                if size_case == "growing" and len(read_sizes) == 1:
                    with path.open("r+b") as writer:
                        writer.truncate(limit + 1)
            return read(fd, count)

        monkeypatch.setattr(os, "read", instrument)

    docker = FakeDocker()
    run = _run_collection_pair(tmp_path, monkeypatch, docker, setup_first=prepare_log)
    if size_case == "limit":
        await run
        directory = _check_first_collection(tmp_path, ok=True)
        assert (directory / "cache.log").stat().st_size == limit
    else:
        with pytest.raises(BenchmarkValidationError, match="cache.log"):
            await run
        directory = _check_first_collection(tmp_path, ok=False)
        assert not (directory / "cache.log").exists()
        if size_case == "sparse":
            assert not read_sizes  # refuse the descriptor before its first read
        else:
            assert 0 < sum(read_sizes) <= 2  # at most the original byte and one overflow byte
    assert docker.resources == {}
