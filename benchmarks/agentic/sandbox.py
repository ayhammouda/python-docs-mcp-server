"""The container sandbox: keyless worker containers behind one logging egress proxy per job.

Design §7.2 and §7.3, as amended on #200: a key never enters a container's environment or
config, the docker CLI's environment, argv or a file other than the keys file. A live
worker's one key reaches it only in the bootstrap frame on its stdin (``bootstrap_frame``).

Imports no provider SDK: ``run.sh`` imports ``SQUID_IMAGE`` from it on the host.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import benchmarks
from benchmarks.adapters.guard import (
    EGRESS_URL_ENV,
    LIVE_LATCH_ENV,
    LIVE_PROVIDERS_ENABLED_ENV,
    PROVIDER_API_KEY_ENV,
)
from benchmarks.agentic.egress import (
    EGRESS_ALIAS,
    EGRESS_URL,
    LOG_DIR_IN_PROXY,
    allowlist,
    open_log_dir,
    pending_collection,
    read_log_at,
    squid_conf,
    write_new,
)
from benchmarks.agentic.protocol import KeyLeakError, redact_key_shaped
from benchmarks.model_matrix import KNOWN_APIS, ModelFamily
from benchmarks.runner import BenchmarkValidationError, _write_json

if TYPE_CHECKING:
    from benchmarks.agentic.host import WorkerJob

SQUID_IMAGE = (
    "ubuntu/squid:6.6-24.04_edge"
    "@sha256:8a3baed477e2c282ab8aa5edad442f69873246964f225c5c2ae8364b6610963c"
)
WORKER_USER = "10001:10001"
PROXY_USER = "proxy"
RUN_LABEL = "org.python-docs-mcp.benchmark-run"
MIN_ENGINE = (28, 0)
REPO_ROOT = Path(benchmarks.__file__).resolve().parent.parent
#: Ollama on the Mac, as a worker container sees it through its egress proxy (design §7.4).
OLLAMA_URL_IN_SANDBOX = "http://host.docker.internal:11434/v1"
_REVISION_LABEL = "org.opencontainers.image.revision"
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
#: The docker CLI child's whole environment, each name when set. Never a key.
_CLI_ENV = (
    "PATH",
    "HOME",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)
_DOCKER_TIMEOUT_S = 120

DockerRun = Callable[[Sequence[str]], Awaitable[subprocess.CompletedProcess[bytes]]]


def read_key(keys_file: Path, name: str) -> str:
    """The value of ``name`` in a ``NAME=VALUE`` keys file; errors name no value.

    ``export`` prefixes, matching quotes, comments and blank lines are handled. A file with
    any group or other permission bit is refused.
    """
    try:
        mode = keys_file.stat().st_mode
        text = keys_file.read_text(encoding="utf-8") if not mode & 0o077 else ""
    except OSError as exc:
        raise BenchmarkValidationError(
            f"keys file {keys_file} cannot be read for {name}: {type(exc).__name__}"
        ) from None
    if mode & 0o077:
        raise BenchmarkValidationError(
            f"keys file {keys_file} is readable beyond its owner (mode {mode & 0o777:o}); "
            f"chmod 600 it. {name} was not read"
        )
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not value:
            raise BenchmarkValidationError(f"keys file {keys_file} sets {name} to an empty value")
        return value
    raise BenchmarkValidationError(f"keys file {keys_file} does not set {name}")


def cli_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The docker CLI's environment: the allowlist only, so it never holds a key or a proxy."""
    return {name: environ[name] for name in _CLI_ENV if name in environ}


def docker_runner(env: Mapping[str, str]) -> DockerRun:
    """Run one docker CLI command with ``env`` as its whole environment."""

    async def run(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        return await asyncio.to_thread(
            subprocess.run,
            list(argv),
            capture_output=True,
            env=dict(env),
            timeout=_DOCKER_TIMEOUT_S,
        )

    return run


@dataclass(frozen=True)
class SandboxConfig:
    image: str  # the worker image's ID, never a tag
    run_id: str
    out_dir: Path
    index_path: Path
    keys_file: Path | None
    repo_commit: str

    def __post_init__(self) -> None:
        if not _IMAGE_ID.fullmatch(self.image):
            raise BenchmarkValidationError(
                f"--image must be an image ID (sha256:<64 hex>), never a tag: {self.image!r}"
            )


@dataclass(frozen=True)
class Egress:
    job_id: str
    network: str  # the job's internal network
    proxy: str  # its Squid container, aliased "egress" on that network
    allow: tuple[tuple[str, int], ...]
    log_dir: Path  # out_dir/egress/<job_id>/: squid.conf, access.log, cache.log, collected.json
    gateways: tuple[str, ...]  # every IPAM gateway of the internal network
    inspect: dict[str, Any]  # its `docker network inspect` record


VerifyFn = Callable[["Sandbox"], Awaitable[dict[str, str]]]
ProbeFn = Callable[..., Awaitable[dict[str, Any]]]
ControlFn = Callable[["Sandbox"], Awaitable[dict[str, Any]]]


def worker_env(family: ModelFamily) -> dict[str, str]:
    """A worker container's whole environment (Global Constraints allowlist): never a key."""
    env = {
        "HOME": "/tmp",
        "XDG_CACHE_HOME": "/cache",
        "PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1",
        EGRESS_URL_ENV: EGRESS_URL,
    }
    if family.api != "scripted":
        env[LIVE_PROVIDERS_ENABLED_ENV] = "1"
    if family.provider in LIVE_LATCH_ENV:
        env[LIVE_LATCH_ENV[family.provider]] = OLLAMA_URL_IN_SANDBOX
    return env


def _tail(proc: subprocess.CompletedProcess[bytes]) -> str:
    lines = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else f"exit {proc.returncode}"


def _engine(version: str) -> tuple[int, int]:
    match = re.match(r"(\d+)\.(\d+)", version.strip())
    return (int(match[1]), int(match[2])) if match else (0, 0)


async def verify_image(sandbox: Sandbox, *, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Check the daemon, both images, the engine and the checkout before any container starts.

    A local ``unix://`` daemon only (round-3 item 4), so a bootstrap frame never crosses a
    network. Writes and returns ``out_dir/sandbox.json``.
    """
    config = sandbox.config
    context = json.loads(await sandbox.output("context", "inspect", "--format", "{{json .}}"))
    endpoint = str(((context.get("Endpoints") or {}).get("docker") or {}).get("Host", ""))
    if not endpoint.startswith("unix://"):
        raise BenchmarkValidationError(
            f"docker context {context.get('Name')!r} points at {endpoint!r}: the sandbox "
            "runs only on a local unix:// daemon"
        )
    host = sandbox.env.get("DOCKER_HOST")
    if host is not None and not host.startswith("unix://"):
        raise BenchmarkValidationError(
            f"DOCKER_HOST is {host!r}: the sandbox runs only on a local unix:// daemon"
        )
    image = json.loads(
        await sandbox.output("image", "inspect", "--format", "{{json .}}", config.image)
    )
    revision = ((image.get("Config") or {}).get("Labels") or {}).get(_REVISION_LABEL)
    if image.get("Id") != config.image:
        raise BenchmarkValidationError(f"image {config.image} inspects as {image.get('Id')}")
    if revision != config.repo_commit:
        raise BenchmarkValidationError(
            f"image {config.image} was built from {revision}, not this checkout's "
            f"{config.repo_commit}: rebuild it with benchmarks/sandbox/run.sh"
        )
    proxy = json.loads(
        await sandbox.output("image", "inspect", "--format", "{{json .}}", SQUID_IMAGE)
    )
    engine = (await sandbox.output("version", "--format", "{{.Server.Version}}")).strip()
    if _engine(engine) < MIN_ENGINE:
        raise BenchmarkValidationError(
            f"Docker Engine {engine} is older than {'.'.join(map(str, MIN_ENGINE))}, which the "
            "isolated gateway mode needs"
        )
    # --untracked-files=normal: a status.showUntrackedFiles=no config cannot hide a file.
    status = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise BenchmarkValidationError(
            f"the checkout at {repo_root} is dirty or unreadable: the image must hold exactly "
            "the committed run commit"
        )
    record = {
        "image": config.image,
        "revision": str(revision),
        "proxy_image": SQUID_IMAGE,
        "proxy_image_id": str(proxy.get("Id")),
        "engine_version": engine,
        "docker_context": str(context.get("Name")),
        "docker_endpoint": endpoint,
    }
    _write_json(config.out_dir / "sandbox.json", record)
    return record


class Sandbox:
    """The run's docker resources: one internal network and one proxy per job."""

    def __init__(
        self,
        config: SandboxConfig,
        docker: DockerRun | None = None,
        verify: VerifyFn = verify_image,
    ) -> None:
        self.config = config
        self.env = cli_env(os.environ)  # the docker CLI's whole environment
        self.docker = docker or docker_runner(self.env)
        self.verify = verify  # the test seam (round-4 item 2); production keeps the real check
        self.leaks: list[str] = []  # key shapes found and redacted in a proxy's logs
        self.outside_created = False

    @property
    def outside(self) -> str:
        return f"pdmcp-{self.config.run_id}-out"

    def worker_name(self, job_id: str) -> str:
        return f"pdmcp-{self.config.run_id}-{job_id}"

    def _label(self) -> list[str]:
        return ["--label", f"{RUN_LABEL}={self.config.run_id}"]

    async def run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        """One docker command; raises BenchmarkValidationError when it fails."""
        proc = await self.docker(["docker", *args])
        if proc.returncode != 0:
            raise BenchmarkValidationError(f"docker {' '.join(args[:2])} failed: {_tail(proc)}")
        return proc

    async def output(self, *args: str) -> str:
        return (await self.run(*args)).stdout.decode("utf-8", errors="replace")

    async def ensure_outside(self) -> None:
        """The run's one outside network, for the proxies and the control probe."""
        if not self.outside_created:
            await self.run("network", "create", *self._label(), self.outside)
            self.outside_created = True

    async def open_egress(self, job_id: str, allow: Sequence[tuple[str, int]]) -> Egress:
        """The job's internal network and its proxy, logging to ``out_dir/egress/<job_id>/``."""
        await self.ensure_outside()
        name = self.worker_name(job_id)
        network, proxy = f"{name}-net", f"{name}-proxy"
        await self.run(
            "network",
            "create",
            "--internal",
            "--ipv6=false",
            "-o",
            "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            *self._label(),
            network,
        )
        try:
            (inspect,) = json.loads(await self.output("network", "inspect", network))
            if inspect.get("EnableIPv6"):
                raise BenchmarkValidationError(
                    f"network {network} kept IPv6 despite --ipv6=false: refused, no fallback"
                )
            gateways = tuple(
                str(c["Gateway"])
                for c in (inspect.get("IPAM") or {}).get("Config") or []
                if c.get("Gateway")
            )
            log_dir = self.config.out_dir / "egress" / job_id
            if os.path.lexists(log_dir):  # a new directory of ours, never a planted link
                raise BenchmarkValidationError(
                    f"log directory {log_dir} already exists (it may be a link): refused"
                )
            log_dir.mkdir(parents=True)
            pending = pending_collection(self.config.out_dir, job_id)
            pending.parent.mkdir(exist_ok=True)
            pending.touch(exist_ok=False)
            conf = log_dir / "squid.conf"
            with open_log_dir(log_dir) as dir_fd:
                # Readable by `proxy` under any umask (round-4 item 4).
                os.fchmod(dir_fd, 0o777)
                write_new(dir_fd, conf.name, squid_conf(allow).encode("utf-8"), mode=0o644)
            await self.run(
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                proxy,
                *self._label(),
                "--network",
                self.outside,
                "--read-only",
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/var/spool/squid",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                PROXY_USER,
                "--memory",
                "256m",
                "--cpus",
                "0.5",
                "--entrypoint",
                "/usr/sbin/squid",
                "--mount",
                f"type=bind,src={conf},dst=/etc/squid/squid.conf,readonly",
                "--mount",
                f"type=bind,src={log_dir},dst={LOG_DIR_IN_PROXY}",
                SQUID_IMAGE,
                "-N",
                "-f",
                "/etc/squid/squid.conf",
            )
            await self.run("network", "connect", "--alias", EGRESS_ALIAS, network, proxy)
        except BaseException:
            # Nothing of this job's may outlive the failure (amendment 2).
            for cleanup in (["rm", "-f", proxy], ["network", "rm", network]):
                await self.docker(["docker", *cleanup])
            raise
        return Egress(job_id, network, proxy, tuple(allow), log_dir, gateways, inspect)

    def container_argv(
        self,
        *,
        name: str,
        network: str,
        env: Mapping[str, str],
        mounts: Sequence[str],
        argv: Sequence[str],
        interactive: bool = True,
    ) -> list[str]:
        """``docker run`` with the design §7.2 hardening. It has no key parameter."""
        return [
            "docker",
            "run",
            "--name",
            name,
            *self._label(),
            "--network",
            network,
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            WORKER_USER,
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "--init",
            "--rm",
            *(["-i"] if interactive else []),
            "--pull",
            "never",
            "--log-driver",
            "none",  # the daemon keeps no second copy of worker output
            *[item for key, value in env.items() for item in ("-e", f"{key}={value}")],
            *[item for mount in mounts for item in ("--mount", mount)],
            self.config.image,
            *argv,
        ]

    async def _state(self, container: str, problems: list[str], when: str) -> dict[str, Any]:
        try:
            out = await self.output("inspect", "--format", "{{json .State}}", container)
            return dict(json.loads(out))
        except Exception as exc:  # noqa: BLE001 - every collection step runs, failures listed
            problems.append(f"inspecting the proxy {when} our stop failed: {exc}")
            return {}

    async def close_egress(self, egress: Egress) -> list[str]:
        """Stop the job's proxy, check how it ended and its logs, remove what was created.

        Every step runs even after a failure; failures are returned as problems and written
        to ``collected.json``, whose ``ok`` is false after any of them.
        """
        problems: list[str] = []
        before = await self._state(egress.proxy, problems, "before")
        if before and (not before.get("Running") or before.get("OOMKilled")):
            problems.append(
                f"the proxy ended before our stop (exit {before.get('ExitCode')}, "
                f"OOMKilled {before.get('OOMKilled')})"
            )
        try:
            await self.run("stop", "-t", "5", egress.proxy)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"docker stop of the proxy failed: {exc}")
        after = await self._state(egress.proxy, problems, "after")
        if after and after.get("Running"):
            problems.append("the proxy was still running after our stop")
        elif after and (after.get("ExitCode") != 0 or after.get("OOMKilled")):
            problems.append(
                f"the proxy exited {after.get('ExitCode')} after our stop "
                f"(OOMKilled {after.get('OOMKilled')}): its log may be partial"
            )
        for args in (["rm", "-f", egress.proxy], ["network", "rm", egress.network]):
            try:
                await self.run(*args)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"docker {' '.join(args[:2])} failed: {exc}")
        try:
            with open_log_dir(egress.log_dir) as dir_fd:
                for name in ("access.log", "cache.log"):
                    try:
                        redacted, pattern = redact_key_shaped(read_log_at(dir_fd, name))
                        if pattern:
                            write_new(dir_fd, name, redacted)
                            self.leaks.append(f"{egress.job_id}/{name} ({pattern} pattern)")
                            problems.append(f"a key-shaped string in {name} was redacted")
                    except (BenchmarkValidationError, OSError) as exc:
                        problems.append(f"{name} could not be collected: {exc}")
                collected = {
                    "ok": not problems,
                    "problems": problems,
                    "state_before_stop": before,
                    "state_after_stop": after,
                }
                text = json.dumps(collected, indent=2, sort_keys=True) + "\n"
                write_new(dir_fd, "collected.json", text.encode("utf-8"), mode=0o644)
            if not problems:
                pending_collection(self.config.out_dir, egress.job_id).unlink()
        except (BenchmarkValidationError, OSError) as exc:
            problems.append(f"collection could not be completed: {exc}")
        return problems


class ContainerLauncher:
    """Implements host.Launcher: each job's worker in its own hardened, keyless container."""

    def __init__(
        self, sandbox: Sandbox, probe: ProbeFn | None = None, control: ControlFn | None = None
    ) -> None:
        from benchmarks.agentic.probe import control_probe, isolation_probe

        self.sandbox = sandbox
        self._probe: ProbeFn = probe or isolation_probe
        self._control: ControlFn = control or control_probe
        self._egresses: list[Egress] = []

    def supports(self, family: ModelFamily) -> bool:
        return family.api in KNOWN_APIS  # scripted, and every api make_client builds

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        family, config = job.family, self.sandbox.config
        env = worker_env(family)
        mounts = ["type=tmpfs,dst=/cache/mcp-python-docs,tmpfs-mode=1777"]
        if job.system.id == "python-docs":
            index = "/cache/mcp-python-docs/index.db"
            mounts.append(f"type=bind,src={config.index_path},dst={index},readonly")
        if family.api == "scripted":
            script = str(family.raw["script"])
            mounts.append(f"type=bind,src={script},dst={script},readonly")
        name = self.sandbox.worker_name(job.job_id)
        argv = self.sandbox.container_argv(
            name=name,
            network=f"{name}-net",
            env=env,
            mounts=mounts,
            argv=["python", "-m", "benchmarks", "agentic-worker"],
        )
        return argv, cli_env(os.environ)

    def bootstrap_frame(self, job: WorkerJob) -> dict[str, Any] | None:
        """The live worker's one key (none for Ollama), read now, after the gate and probes."""
        family = job.family
        if family.api == "scripted":
            return None
        if family.provider in LIVE_LATCH_ENV:
            return {"type": "bootstrap", "env": {}}
        keys_file = self.sandbox.config.keys_file
        name = PROVIDER_API_KEY_ENV[family.provider]
        if keys_file is None:
            raise BenchmarkValidationError(
                f"model family {family.id!r} needs a keys file for {name}"
            )
        return {"type": "bootstrap", "env": {name: read_key(keys_file, name)}}

    async def prepare(self, jobs: Sequence[WorkerJob]) -> None:
        """Verify, run the control probe once, then open and probe every job's egress."""
        await self.sandbox.verify(self.sandbox)
        await self._control(self.sandbox)  # writes probe/control.json
        for job in jobs:
            allow = allowlist(job.family, job.system.id)
            egress = await self.sandbox.open_egress(job.job_id, allow)
            self._egresses.append(egress)
            await self._probe(
                self.sandbox, egress, job_id=job.job_id, family=job.family, system_id=job.system.id
            )

    async def stop_worker(self, job: WorkerJob) -> None:
        """Remove this job's own container, by its name, and nothing else."""
        name = self.sandbox.worker_name(job.job_id)
        proc = await self.sandbox.docker(["docker", "rm", "-f", name])
        if proc.returncode != 0 and "No such container" not in _tail(proc):
            raise BenchmarkValidationError(f"docker rm -f {name} failed: {_tail(proc)}")

    async def close(self) -> list[str]:
        problems: list[str] = []
        for egress in self._egresses:
            problems += [f"{egress.job_id}: {p}" for p in await self.sandbox.close_egress(egress)]
        if self.sandbox.outside_created:
            try:
                await self.sandbox.run("network", "rm", self.sandbox.outside)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"docker network rm {self.sandbox.outside} failed: {exc}")
        if self.sandbox.leaks:
            raise KeyLeakError(
                f"key-shaped strings were redacted in proxy logs: {', '.join(self.sandbox.leaks)}"
                + (f"; other problems: {'; '.join(problems)}" if problems else "")
            )
        return problems
