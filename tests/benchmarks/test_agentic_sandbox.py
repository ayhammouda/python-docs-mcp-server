"""The container sandbox and ContainerLauncher (design §7.2, §7.3), with a recorded FakeDocker.

No test runs docker or reaches a network host: every docker command goes to FakeDocker.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from benchmarks.adapters.guard import PROVIDER_API_KEY_ENV
from benchmarks.agentic.bridge import SystemSpec, load_systems
from benchmarks.agentic.egress import allowlist, squid_conf
from benchmarks.agentic.host import WorkerJob
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

    async def __call__(self, argv: Sequence[str]) -> Any:
        argv = list(argv)
        self.calls.append(argv)
        args = argv[1:]
        if self.fail is not None and self.fail(args):
            return _done(argv, 1, err="Error response from daemon: boom")
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
    launcher = ContainerLauncher(_sandbox(tmp_path, FakeDocker()), probe=Probes())
    argv, _ = launcher.command(_job("scripted-anthropic"))
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
    assert argv[:2] == ["docker", "run"]


def test_only_python_docs_jobs_mount_the_index_read_only(tmp_path: Path) -> None:
    launcher = ContainerLauncher(_sandbox(tmp_path, FakeDocker()), probe=Probes())
    index = f"type=bind,src={tmp_path / 'index.db'},dst=/cache/mcp-python-docs/index.db,readonly"
    for system_id, mounted in (("python-docs", True), ("docs-fetch", False), ("no-docs", False)):
        mounts = _mounts(launcher.command(_job("claude-sonnet-5-5", system_id))[0])
        assert (index in mounts) is mounted
        assert "type=tmpfs,dst=/cache/mcp-python-docs,tmpfs-mode=1777" in mounts
    script = str(FIX / "script-anthropic.yml")
    mounts = _mounts(launcher.command(_job("scripted-anthropic"))[0])
    assert f"type=bind,src={script},dst={script},readonly" in mounts


def test_worker_env_is_the_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    launcher = ContainerLauncher(_sandbox(tmp_path, FakeDocker()), probe=Probes())
    for family_id in ("scripted-anthropic", "claude-sonnet-5-5", "gemma4-12b"):
        argv, env = launcher.command(_job(family_id))
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
    gemma = launcher.command(_job("gemma4-12b"))[0]
    assert "OLLAMA_BASE_URL=http://host.docker.internal:11434/v1" in gemma
    assert "BENCHMARK_LIVE_PROVIDERS_ENABLED=1" in gemma
    scripted = launcher.command(_job("scripted-anthropic"))[0]
    assert "BENCHMARK_LIVE_PROVIDERS_ENABLED=1" not in scripted
    assert "BENCHMARK_EGRESS_URL=http://egress:3128" in scripted


async def test_no_key_in_argv_cli_env_or_container_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in PROVIDER_API_KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    keys = _keys(tmp_path, f"ANTHROPIC_API_KEY={SYNTHETIC}\n")
    docker = FakeDocker()
    sandbox = _sandbox(tmp_path, docker, keys_file=keys)
    launcher = ContainerLauncher(sandbox, probe=Probes())
    job = _job("claude-sonnet-5-5", "python-docs")
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
    launcher = ContainerLauncher(_sandbox(tmp_path, FakeDocker(), keys_file=keys), probe=Probes())
    assert launcher.bootstrap_frame(_job("gpt-6.1-sol")) == {
        "type": "bootstrap",
        "env": {"OPENAI_API_KEY": "sk-o"},
    }
    assert launcher.bootstrap_frame(_job("gemini-3.8-flash"))["env"] == {"GOOGLE_API_KEY": "g-k"}  # type: ignore[index]
    assert launcher.bootstrap_frame(_job("gemma4-12b")) == {"type": "bootstrap", "env": {}}
    assert launcher.bootstrap_frame(_job("scripted-anthropic")) is None


def test_a_live_family_without_a_keys_file_is_refused(tmp_path: Path) -> None:
    launcher = ContainerLauncher(_sandbox(tmp_path, FakeDocker()), probe=Probes())
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
    assert docker.ran("network", "rm", net)
    assert not docker.ran("run") and not docker.ran("network", "connect")


async def test_a_failed_proxy_start_removes_the_proxy_and_the_network(tmp_path: Path) -> None:
    for step in (["run"], ["network", "connect"]):
        root = tmp_path / step[-1]
        root.mkdir()
        docker = FakeDocker(fail=lambda args, step=step: args[: len(step)] == step)
        with pytest.raises(BenchmarkValidationError):
            await _sandbox(root, docker).open_egress("j", (("api.openai.com", 443),))
        assert docker.ran("rm", "-f", f"pdmcp-{RUN}-j-proxy")
        assert docker.ran("network", "rm", f"pdmcp-{RUN}-j-net")


async def test_prepare_builds_one_network_and_proxy_per_job_with_a_file_log(
    tmp_path: Path, umask_077: None
) -> None:
    docker = FakeDocker()
    probes = Probes()
    launcher = ContainerLauncher(_sandbox(tmp_path, docker), probe=probes)
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
        assert log_dir.stat().st_mode & 0o777 == 0o777
        assert (log_dir / "squid.conf").stat().st_mode & 0o777 == 0o644
        (run,) = [r for r in runs if f"pdmcp-{RUN}-{job.job_id}-proxy" in r]
        text = " ".join(run)
        assert f"type=bind,src={log_dir}/squid.conf,dst=/etc/squid/squid.conf,readonly" in text
        assert f"type=bind,src={log_dir},dst=/var/log/squid-bench" in text
        for flag in ("--read-only", "--cap-drop ALL", "--user proxy", "--pull never", "-d"):
            assert flag in text
        assert run[-3:] == ["-N", "-f", "/etc/squid/squid.conf"]
    assert probes.jobs == [job.job_id for job in jobs]
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


async def test_prepare_refuses_a_daemon_that_is_not_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _git_repo(tmp_path / "repo")
    remote = FakeDocker(context_host="tcp://10.0.0.5:2376")
    launcher = ContainerLauncher(_real(tmp_path, remote, repo), probe=Probes())
    with pytest.raises(BenchmarkValidationError, match="unix://"):
        await launcher.prepare([_job("scripted-anthropic")])
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.5:2376")
    local = FakeDocker()
    launcher = ContainerLauncher(_real(tmp_path, local, repo), probe=Probes())
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
    launcher = ContainerLauncher(_real(tmp_path, docker, _git_repo(tmp_path / "r")), Probes())
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
    launcher = ContainerLauncher(_sandbox(tmp_path, docker), probe=Probes())
    await launcher.stop_worker(_job("scripted-anthropic"))
    assert docker.calls == [["docker", "rm", "-f", f"pdmcp-{RUN}-scripted-anthropic--no-docs"]]
    gone = FakeDocker(fail=lambda args: args[:2] == ["rm", "-f"])
    launcher = ContainerLauncher(_sandbox(tmp_path, gone), probe=Probes())
    with pytest.raises(BenchmarkValidationError, match="boom"):
        await launcher.stop_worker(_job("scripted-anthropic"))

    async def no_such(argv: Sequence[str]) -> Any:
        return _done(argv, 1, err="Error response from daemon: No such container: x")

    sandbox = Sandbox(_config(tmp_path), docker=no_such, verify=_verified)
    await ContainerLauncher(sandbox, probe=Probes()).stop_worker(_job("scripted-anthropic"))


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
    _logs(egress.log_dir)
    return sandbox, egress


def _logs(log_dir: Path, access: str = PARTIAL, cache: str = "squid started\n") -> None:
    """The two files a running Squid writes into its bind-mounted log directory."""
    (log_dir / "access.log").write_text(access, encoding="utf-8")
    (log_dir / "cache.log").write_text(cache, encoding="utf-8")


async def test_close_stops_each_proxy_then_removes_what_it_created(tmp_path: Path) -> None:
    docker = FakeDocker()
    launcher = ContainerLauncher(_sandbox(tmp_path, docker), probe=Probes())
    jobs = [_job("claude-sonnet-5-5", "no-docs"), _job("scripted-anthropic", "no-docs")]
    await launcher.prepare(jobs)
    for job in jobs:
        _logs(tmp_path / "run" / "egress" / job.job_id)
    start = len(docker.calls)
    assert await launcher.close() == []
    tail = [" ".join(c[1:3]) + " " + c[-1] for c in docker.calls[start:]]
    expected = []
    for job in jobs:
        proxy, net = f"pdmcp-{RUN}-{job.job_id}-proxy", f"pdmcp-{RUN}-{job.job_id}-net"
        expected += [
            f"inspect --format {proxy}",
            f"stop -t {proxy}",
            f"inspect --format {proxy}",
            f"rm -f {proxy}",
            f"network rm {net}",
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
    assert tail == [*expected, f"network rm pdmcp-{RUN}-out"]
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


async def test_close_redacts_a_key_shaped_proxy_log_and_raises_after_cleanup(
    tmp_path: Path,
) -> None:
    docker = FakeDocker()
    launcher = ContainerLauncher(_sandbox(tmp_path, docker), probe=Probes())
    job = _job("scripted-anthropic")
    await launcher.prepare([job])
    log_dir = tmp_path / "run" / "egress" / job.job_id
    (log_dir / "cache.log").write_text(f"WARNING: header {SYNTHETIC}\n", encoding="utf-8")
    with pytest.raises(KeyLeakError, match="cache.log"):
        await launcher.close()
    assert (log_dir / "cache.log").read_text() == "WARNING: header [redacted]\n"
    assert docker.ran("rm", "-f", f"pdmcp-{RUN}-{job.job_id}-proxy")
    assert docker.ran("network", "rm", f"pdmcp-{RUN}-out")
    assert json.loads((log_dir / "collected.json").read_text())["ok"] is False


async def test_close_keeps_going_after_a_failed_step(tmp_path: Path) -> None:
    docker = FakeDocker(fail=lambda args: args[:1] == ["stop"])
    sandbox, egress = await _opened(tmp_path, docker)
    problems = await sandbox.close_egress(egress)
    assert problems and any("stop" in p for p in problems)
    assert docker.ran("rm", "-f", egress.proxy) and docker.ran("network", "rm", egress.network)
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False


def test_container_launcher_supports_scripted_and_every_live_api(tmp_path: Path) -> None:
    launcher = ContainerLauncher(_sandbox(tmp_path, FakeDocker()), probe=Probes())
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
    access = egress.log_dir / "access.log"
    access.unlink()
    access.symlink_to(canary)
    problems = await sandbox.close_egress(egress)
    assert canary.read_text() == f"host secret {SYNTHETIC}\n"  # unchanged
    assert sandbox.leaks == []  # unread: its key shape was never seen
    assert access.is_symlink() and any("access.log" in p for p in problems)
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False


@pytest.mark.parametrize("kind", ["fifo", "directory"])
async def test_a_cache_log_that_is_not_a_regular_file_is_refused(tmp_path: Path, kind: str) -> None:
    sandbox, egress = await _opened(tmp_path, FakeDocker())
    cache = egress.log_dir / "cache.log"
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
    cache = egress.log_dir / "cache.log"
    cache.write_text(f"WARNING: header {SYNTHETIC}\n", encoding="utf-8")
    original = tmp_path / "original-inode"
    os.link(cache, original)  # a second name for the original inode
    await sandbox.close_egress(egress)
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
    sandbox, egress = await _opened(tmp_path / "late", FakeDocker())
    _logs(elsewhere, cache=f"key {SYNTHETIC}\n")
    moved = egress.log_dir.with_name("moved")
    egress.log_dir.rename(moved)
    egress.log_dir.symlink_to(elsewhere)
    problems = await sandbox.close_egress(egress)
    assert any("link" in p for p in problems) and sandbox.leaks == []
    assert (elsewhere / "cache.log").read_text() == f"key {SYNTHETIC}\n"
    assert not (elsewhere / "collected.json").exists()
