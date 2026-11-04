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
import secrets
import subprocess
import tempfile
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
    valid_api_key,
)
from benchmarks.agentic.cleanup import add_cleanup_cause, finish, settle
from benchmarks.agentic.egress import (
    EGRESS_ALIAS,
    EGRESS_URL,
    LOG_DIR_IN_PROXY,
    allowlist,
    open_log_dir,
    pending_collection,
    read_log,
    squid_conf,
    write_new,
    write_scanned_json,
)
from benchmarks.agentic.protocol import (
    KeyLeakError,
    assert_no_key_shaped,
    assert_no_key_shaped_json,
    redact_key_shaped,
    redact_key_shaped_text,
    scanned_json_bytes,
)
from benchmarks.model_matrix import KNOWN_APIS, ModelFamily
from benchmarks.runner import BenchmarkValidationError

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
_RESOURCE_RECOVERY_S = 5.0

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
        if not valid_api_key(value):
            raise BenchmarkValidationError(f"keys file contains invalid characters for {name}")
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
            stdin=subprocess.DEVNULL,
            process_group=0,  # the host's foreground signals must not interrupt rollback
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
    network_id: str
    proxy_id: str
    staging: tempfile.TemporaryDirectory[str]

    @property
    def spool_dir(self) -> Path:
        return Path(self.staging.name) / "logs"


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
    scrubbed, _ = redact_key_shaped(proc.stderr)
    lines = scrubbed.decode("utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else f"exit {proc.returncode}"


async def _remove(docker: DockerRun, *args: str) -> subprocess.CompletedProcess[bytes]:
    # Even if the CLI cannot start or times out, the operator needs the immutable ID.
    try:
        return await docker(["docker", *args])
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail = redact_key_shaped_text(str(exc))
        raise BenchmarkValidationError(f"docker {' '.join(args)} failed: {detail}") from None


async def _rollback(
    operation: Awaitable[None],
    setup_error: BaseException,
    cancelled: asyncio.CancelledError | None = None,
) -> None:
    """Finish rollback with the original cancellation or a CLI-visible setup and recovery error."""
    try:
        await finish(operation, cancelled or setup_error)
    except asyncio.CancelledError as interrupt:
        if cancelled is None and interrupt is not setup_error:
            # A signal arrived after setup failed. Later close failures can hide its context.
            reason = redact_key_shaped_text(str(setup_error))
            add_cleanup_cause(interrupt, BenchmarkValidationError(reason))
        raise
    except BenchmarkValidationError as exc:
        reason = redact_key_shaped_text(str(setup_error))
        raise BenchmarkValidationError(f"{reason}; rollback failed: {exc}") from None


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
    assert_no_key_shaped_json([context.get("Name"), endpoint], where="Docker context")
    if not endpoint.startswith("unix://"):
        raise BenchmarkValidationError(
            f"docker context {context.get('Name')!r} points at {endpoint!r}: the sandbox "
            "runs only on a local unix:// daemon"
        )
    host = sandbox.env.get("DOCKER_HOST")
    if host is not None:
        assert_no_key_shaped(host, where="DOCKER_HOST")
    if host is not None and not host.startswith("unix://"):
        raise BenchmarkValidationError(
            f"DOCKER_HOST is {host!r}: the sandbox runs only on a local unix:// daemon"
        )
    image = json.loads(
        await sandbox.output("image", "inspect", "--format", "{{json .}}", config.image)
    )
    revision = ((image.get("Config") or {}).get("Labels") or {}).get(_REVISION_LABEL)
    assert_no_key_shaped_json([image.get("Id"), revision], where="Docker worker image")
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
    assert_no_key_shaped(engine, where="Docker Engine version")
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
    write_scanned_json(config.out_dir / "sandbox.json", record)
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
        self.outside_id: str | None = None
        self._invocation = secrets.token_hex(16)

    @property
    def outside_created(self) -> bool:
        return self.outside_id is not None

    @property
    def outside(self) -> str:
        return f"pdmcp-{self.config.run_id}-out"

    def worker_name(self, job_id: str) -> str:
        return f"pdmcp-{self.config.run_id}-{job_id}"

    def _ownership(self, name: str) -> str:
        return f"org.python-docs-mcp.benchmark-resource={self._invocation}.{name}"

    def _label(self, name: str) -> list[str]:
        return ["--label", f"{RUN_LABEL}={self.config.run_id}", "--label", self._ownership(name)]

    async def run(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        """One docker command; raises BenchmarkValidationError when it fails."""
        removing = args[:2] in (("network", "rm"), ("rm", "-f"))
        proc = (
            await _remove(self.docker, *args) if removing else await self.docker(["docker", *args])
        )
        if proc.returncode != 0:
            operation = args if removing else args[:2]
            raise BenchmarkValidationError(f"docker {' '.join(operation)} failed: {_tail(proc)}")
        return proc

    async def output(self, *args: str) -> str:
        return (await self.run(*args)).stdout.decode("utf-8", errors="replace")

    async def remove_container(
        self, container_id: str, *, pending: BaseException | None = None
    ) -> None:
        """Finish removing this ID even if the caller is cancelled. --rm may have won."""

        async def remove() -> None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _RESOURCE_RECOVERY_S
            while True:
                proc = await _remove(self.docker, "rm", "-f", container_id)
                detail = _tail(proc)
                if proc.returncode == 0 or "No such container" in detail:
                    return
                conflict = f"removal of container {container_id} is already in progress"
                if conflict not in detail or loop.time() >= deadline:
                    raise BenchmarkValidationError(f"docker rm -f {container_id} failed: {detail}")
                await asyncio.sleep(0.1)

        await finish(remove(), pending)

    async def _remove_resources(
        self,
        *,
        containers: Sequence[str] = (),
        networks: Sequence[str] = (),
        staging: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        """Remove every owned resource, even after a sibling removal fails."""
        failures: list[str] = []
        for network, ids in ((False, containers), (True, networks)):
            for resource_id in ids:
                try:
                    if network:
                        await self.run("network", "rm", resource_id)
                    else:
                        await self.remove_container(resource_id)
                except Exception as exc:  # noqa: BLE001 - still remove the other owned IDs
                    failures.append(f"{resource_id}: {redact_key_shaped_text(str(exc))}")
        if staging is not None:
            try:
                staging.cleanup()
            except (OSError, MemoryError) as exc:
                failures.append(
                    f"private proxy spool {staging.name}: {redact_key_shaped_text(str(exc))}"
                )
        if failures:
            raise BenchmarkValidationError("; ".join(failures))

    async def remove_owned(self, name: str, *, network: bool = False) -> None:
        """Recover an ambiguous create by its invocation label, never by its name."""
        ls = ("network", "ls") if network else ("container", "ls", "--all")
        label_filter = f"label={self._ownership(name)}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _RESOURCE_RECOVERY_S
        while True:
            try:
                ids = await self.output(*ls, "--no-trunc", "--quiet", "--filter", label_filter)
            except (BenchmarkValidationError, OSError, subprocess.TimeoutExpired) as exc:
                detail = redact_key_shaped_text(str(exc))
                raise BenchmarkValidationError(
                    f"Docker lookup for {label_filter} failed: {detail}"
                ) from None
            if ids.strip():
                break
            # A CLI failure can precede publication of a create the daemon already accepted.
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise BenchmarkValidationError(
                    f"Docker create outcome is unknown for {name}; "
                    f"check the daemon for {label_filter}"
                )
            await asyncio.sleep(min(0.1, remaining))
        if network:
            await self._remove_resources(networks=ids.split())
        else:
            await self._remove_resources(containers=ids.split())

    async def create_resource(self, name: str, *args: str) -> str:
        """Wait for create before handing off its ID or rolling it back on cancellation."""
        network = args[0] == "network"
        task = asyncio.create_task(self.output(*args))
        cancelled = await settle(task)
        try:
            resource_id = task.result().strip()
            if not resource_id:
                raise BenchmarkValidationError(f"Docker returned no ID for {name}")
        except BaseException as exc:
            if isinstance(exc, (subprocess.TimeoutExpired, BenchmarkValidationError)):
                await _rollback(self.remove_owned(name, network=network), exc, cancelled)
            if cancelled is not None:
                raise cancelled from exc
            raise
        if cancelled is not None:
            if network:
                await finish(self.run("network", "rm", resource_id), cancelled)
            else:
                await self.remove_container(resource_id, pending=cancelled)
            raise cancelled
        return resource_id

    async def ensure_outside(self) -> None:
        """The run's one outside network, for the proxies and the control probe."""
        if not self.outside_created:
            self.outside_id = await self.create_resource(
                self.outside, "network", "create", *self._label(self.outside), self.outside
            )

    async def open_egress(self, job_id: str, allow: Sequence[tuple[str, int]]) -> Egress:
        """The job's internal network and its proxy, logging to ``out_dir/egress/<job_id>/``."""
        await self.ensure_outside()
        name = self.worker_name(job_id)
        network, proxy = f"{name}-net", f"{name}-proxy"
        network_id = await self.create_resource(
            network,
            "network",
            "create",
            "--internal",
            "--ipv6=false",
            "-o",
            "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            *self._label(network),
            network,
        )
        proxy_id = None
        staging = None
        try:
            (inspect,) = json.loads(await self.output("network", "inspect", network_id))
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
            log_dir.mkdir(mode=0o700, parents=True)
            pending = pending_collection(self.config.out_dir, job_id)
            pending.parent.mkdir(exist_ok=True)
            pending.touch(exist_ok=False)
            conf = log_dir / "squid.conf"
            with open_log_dir(log_dir) as dir_fd:
                # Readable by `proxy` under any umask (round-4 item 4).
                config_text = squid_conf(allow)
                assert_no_key_shaped(config_text, where="squid.conf")
                write_new(dir_fd, conf.name, config_text.encode(), mode=0o644)
            temp_root = Path(tempfile.gettempdir()).resolve()
            if temp_root.is_relative_to(self.config.out_dir.resolve()):
                raise BenchmarkValidationError("proxy staging must be outside the artifact tree")
            staging = tempfile.TemporaryDirectory(prefix="pdmcp-squid-", dir=temp_root)
            spool = Path(staging.name) / "logs"
            spool.mkdir()
            spool.chmod(0o777)  # Docker Desktop maps Squid's proxy uid into this mount
            proxy_id = await self.create_resource(
                proxy,
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                proxy,
                *self._label(proxy),
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
                f"type=bind,src={spool},dst={LOG_DIR_IN_PROXY}",
                SQUID_IMAGE,
                "-N",
                "-f",
                "/etc/squid/squid.conf",
            )
            await finish(
                self.run("network", "connect", "--alias", EGRESS_ALIAS, network_id, proxy_id)
            )
        except BaseException as exc:
            # Nothing of this job's may outlive the failure (amendment 2).
            await _rollback(
                self._remove_resources(
                    containers=[proxy_id] if proxy_id else [],
                    networks=[network_id],
                    staging=staging,
                ),
                exc,
            )
            raise
        return Egress(
            job_id,
            network,
            proxy,
            tuple(allow),
            log_dir,
            gateways,
            inspect,
            network_id,
            proxy_id,
            staging,
        )

    def container_argv(
        self,
        *,
        name: str,
        network: str,
        env: Mapping[str, str],
        mounts: Sequence[str],
        argv: Sequence[str],
        interactive: bool = True,
        create_only: bool = False,
    ) -> list[str]:
        """``docker run`` with the design §7.2 hardening. It has no key parameter."""
        return [
            "docker",
            "create" if create_only else "run",
            "--name",
            name,
            *self._label(name),
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
        # These messages contain only the removal command and its scrubbed immutable ID.
        # Keep them independent of state/log diagnostics that might have to be withheld.
        removal_failures: list[str] = []
        try:
            if any(leak.startswith(f"{egress.job_id}/") for leak in self.leaks):
                problems.append("a key-shaped string was found during the job probe")
            before, after = await self._stop_egress(egress, problems, removal_failures)
            problems.extend(removal_failures)
            for name in ("access.log", "cache.log"):
                try:
                    data, pattern = redact_key_shaped(read_log(egress.spool_dir / name))
                    if pattern:
                        self.leaks.append(f"{egress.job_id}/{name} ({pattern} pattern)")
                        problems.append(f"a key-shaped string was found in {name}")
                    with open_log_dir(egress.log_dir) as dir_fd:
                        write_new(dir_fd, name, data)
                except KeyLeakError:
                    self.leaks.append(f"{egress.job_id}/{name}")
                    problems.append(f"a key-shaped string was found in {name}")
                except BenchmarkValidationError as exc:
                    problems.append(f"{name} could not be collected: {exc}")
                except (OSError, MemoryError) as exc:
                    problems.append(f"{name} could not be collected: {type(exc).__name__}")
        finally:
            try:
                egress.staging.cleanup()
            except (OSError, MemoryError) as exc:
                problems.append(f"private log cleanup failed: {type(exc).__name__}")

        collected: dict[str, Any] = {
            "ok": not problems,
            "problems": problems,
            "state_before_stop": before,
            "state_after_stop": after,
        }

        def encode_record() -> bytes:
            return (
                scanned_json_bytes(
                    collected, where="collection diagnostics", indent=2, sort_keys=True
                )
                + b"\n"
            )

        try:
            try:
                data = encode_record()
            except KeyLeakError:
                self.leaks.append(f"{egress.job_id}/collected.json")
                problems = [
                    *removal_failures,
                    "a key-shaped string was found in collection diagnostics",
                ]
                collected.update(
                    ok=False, problems=problems, state_before_stop={}, state_after_stop={}
                )
                data = encode_record()
        except Exception as exc:  # noqa: BLE001 - unscanned diagnostics cannot leave collection
            return [
                *removal_failures,
                f"collection diagnostics could not be scanned: {type(exc).__name__}",
            ]
        try:
            with open_log_dir(egress.log_dir) as dir_fd:
                write_new(dir_fd, "collected.json", data, mode=0o644)
                if not problems:
                    try:
                        pending_collection(self.config.out_dir, egress.job_id).unlink()
                    except OSError as exc:
                        problems.append(
                            f"could not clear the collection marker: {type(exc).__name__}"
                        )
                        collected.update(ok=False, problems=problems)
                        write_new(dir_fd, "collected.json", encode_record(), mode=0o644)
        except KeyLeakError:
            self.leaks.append(f"{egress.job_id}/collected.json")
            problems.append("a key-shaped string was found during collection publication")
        except Exception as exc:  # noqa: BLE001 - preserve removal IDs on publication failure
            problems.append(f"collection could not be completed: {type(exc).__name__}")
        return problems

    async def _stop_egress(
        self, egress: Egress, problems: list[str], removal_failures: list[str]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        before = await self._state(egress.proxy_id, problems, "before")
        if before and (not before.get("Running") or before.get("OOMKilled")):
            problems.append(
                f"the proxy ended before our stop (exit {before.get('ExitCode')}, "
                f"OOMKilled {before.get('OOMKilled')})"
            )
        try:
            await self.run("stop", "-t", "5", egress.proxy_id)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"docker stop of the proxy failed: {exc}")
        after = await self._state(egress.proxy_id, problems, "after")
        if after and after.get("Running"):
            problems.append("the proxy was still running after our stop")
        elif after and (after.get("ExitCode") != 0 or after.get("OOMKilled")):
            problems.append(
                f"the proxy exited {after.get('ExitCode')} after our stop "
                f"(OOMKilled {after.get('OOMKilled')}): its log may be partial"
            )
        for args in (["rm", "-f", egress.proxy_id], ["network", "rm", egress.network_id]):
            try:
                await self.run(*args)
            except Exception:  # noqa: BLE001 - report the ID independently of daemon output
                removal_failures.append(
                    f"docker {' '.join(args[:2])} {redact_key_shaped_text(args[-1])} failed"
                )
        return before, after


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
        self._worker_ids: dict[str, str] = {}

    def supports(self, family: ModelFamily) -> bool:
        return family.api in KNOWN_APIS  # scripted, and every api make_client builds

    def worker_create_argv(self, job: WorkerJob) -> list[str]:
        """Configure the stopped worker, including the program that will read host frames."""
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
        return self.sandbox.container_argv(
            name=name,
            network=f"{name}-net",
            env=env,
            mounts=mounts,
            argv=["python", "-m", "benchmarks", "agentic-worker"],
            create_only=True,
        )

    def command(self, job: WorkerJob) -> tuple[list[str], dict[str, str]]:
        return ["docker", "start", "-ai", self._worker_ids[job.job_id]], self.sandbox.env

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
        index = self.sandbox.config.index_path
        if any(job.system.id == "python-docs" for job in jobs) and not index.is_file():
            raise BenchmarkValidationError(f"python-docs requires a local index file at {index}")
        await self._control(self.sandbox)  # writes probe/control.json
        for job in jobs:
            allow = allowlist(job.family, job.system.id)
            egress = await self.sandbox.open_egress(job.job_id, allow)
            self._egresses.append(egress)
            try:
                await self._probe(
                    self.sandbox,
                    egress,
                    job_id=job.job_id,
                    family=job.family,
                    system_id=job.system.id,
                )
            except KeyLeakError:
                self.sandbox.leaks.append(f"{job.job_id}/probe")
                raise

        # Own every stopped worker before reading keys or letting any supervisor start one.
        for job in jobs:
            argv = self.worker_create_argv(job)
            self._worker_ids[job.job_id] = await self.sandbox.create_resource(
                self.sandbox.worker_name(job.job_id), *argv[1:]
            )

        # Validate all required keys after isolation and before any worker can spend.
        for job in jobs:
            self.bootstrap_frame(job)

    async def stop_worker(self, job: WorkerJob) -> None:
        """Remove a surviving worker only if this invocation created it."""
        await self._remove_worker(job.job_id)

    async def _remove_worker(self, job_id: str) -> None:
        if (container_id := self._worker_ids.get(job_id)) is not None:
            await self.sandbox.remove_container(container_id)
            del self._worker_ids[job_id]

    async def close(self) -> list[str]:
        problems: list[str] = []
        interrupted: asyncio.CancelledError | None = None

        def removal_failure(command: str, resource_id: str, exc: Exception) -> str:
            detail = (
                redact_key_shaped_text(str(exc))
                if isinstance(exc, BenchmarkValidationError)
                else type(exc).__name__
            )
            return f"{command} {redact_key_shaped_text(resource_id)} failed: {detail}"

        # Partial prepare and spawn failures can leave workers without a supervisor.
        for job_id, worker_id in list(self._worker_ids.items()):
            try:
                await self._remove_worker(job_id)
            except Exception as exc:  # noqa: BLE001 - continue releasing the other resources
                problems.append(removal_failure(f"{job_id}: docker rm -f", worker_id, exc))
        for egress in self._egresses:
            try:
                issues = await self.sandbox.close_egress(egress)
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
                # Collection may have stopped on either side of the removal commands.
                # Report both IDs for recovery without echoing the unverified exception.
                if isinstance(exc, asyncio.CancelledError) and interrupted is None:
                    interrupted = exc
                if isinstance(exc, KeyLeakError):
                    self.sandbox.leaks.append(f"{egress.job_id}/collection")
                issues = [
                    f"collection failed ({type(exc).__name__}); check proxy "
                    f"{redact_key_shaped_text(egress.proxy_id)} and network "
                    f"{redact_key_shaped_text(egress.network_id)}"
                ]
            problems.extend(f"{egress.job_id}: {p}" for p in issues)
        if (outside_id := self.sandbox.outside_id) is not None:
            try:
                await self.sandbox.run("network", "rm", outside_id)
                self.sandbox.outside_id = None
            except Exception as exc:  # noqa: BLE001
                problems.append(removal_failure("docker network rm", outside_id, exc))
        if self.sandbox.leaks:
            leak = KeyLeakError(
                "key-shaped strings were detected during proxy collection: "
                + ", ".join(self.sandbox.leaks)
                + (f"; other problems: {'; '.join(problems)}" if problems else "")
            )
            if interrupted is None:
                raise leak
            add_cleanup_cause(interrupted, leak)
        elif interrupted is not None:
            add_cleanup_cause(interrupted, BenchmarkValidationError("; ".join(problems)))
        if interrupted is not None:
            raise interrupted
        return problems
