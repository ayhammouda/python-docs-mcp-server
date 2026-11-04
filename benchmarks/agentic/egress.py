"""Egress policy for the sandbox: allowlists, the Squid config and its access log (design §7.3).

Imports nothing heavier than the model matrix, the runner and the key scan: the isolation probe
imports this module inside the worker container.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from benchmarks.agentic.protocol import assert_no_key_shaped
from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError, _write_json

if TYPE_CHECKING:
    from benchmarks.agentic.host import WorkerJob

EGRESS_ALIAS = "egress"
EGRESS_PORT = 3128
EGRESS_URL = f"http://{EGRESS_ALIAS}:{EGRESS_PORT}"
#: Where the proxy writes its logs, bind-mounted from the run's egress/<job_id>/ directory.
LOG_DIR_IN_PROXY = "/var/log/squid-bench"

#: The pinned base URLs in clients.py, and the air-gapped arm's Ollama (design §7.4).
PROVIDER_HOSTS: dict[str, tuple[tuple[str, int], ...]] = {
    "anthropic": (("api.anthropic.com", 443),),
    "openai": (("api.openai.com", 443),),
    "google": (("generativelanguage.googleapis.com", 443),),
    "ollama": (("host.docker.internal", 11434),),
}
SYSTEM_HOSTS: dict[str, tuple[tuple[str, int], ...]] = {
    "docs-fetch": (("docs.python.org", 443),),
}

#: Proxy and CA variables that could steer a client: never in a worker's environment.
FORBIDDEN_ENV = re.compile(r"(?i)^(?:\w*_proxy|ssl_cert_\w*|requests_ca_bundle)$")


def allowlist(family: ModelFamily, system_id: str | None) -> tuple[tuple[str, int], ...]:
    """Air-gapped jobs may reach only local Ollama. Hosted jobs also get their system's hosts."""
    if family.arm == "air-gapped":
        return () if family.api == "scripted" else PROVIDER_HOSTS["ollama"]
    provider = () if family.api == "scripted" else PROVIDER_HOSTS.get(family.provider, ())
    return provider + SYSTEM_HOSTS.get(system_id or "", ())


@dataclass(frozen=True)
class ProxyEvent:
    """One access-log line: one connection attempt and Squid's decision."""

    at: str
    client: str
    method: str
    host: str
    port: int
    status: int
    result: str

    @property
    def allowed(self) -> bool:
        """The ACL did not deny the request. An allowed attempt may still fail upstream."""
        return not self.result.startswith("TCP_DENIED")

    @property
    def reached(self) -> bool:
        """A successful CONNECT tunnel or forwarded HTTP response in our uncached proxy."""
        expected = "TCP_TUNNEL" if self.method == "CONNECT" else "TCP_MISS"
        return self.result == expected and 200 <= self.status < 300

    @property
    def target(self) -> str:
        return f"{self.host}:{self.port}"


def parse_proxy_log(text: str) -> list[ProxyEvent]:
    """Every line of a Squid access log in the ``bench`` format; any bad line is an error.

    Each line must end with a newline: an unterminated last line means the proxy stopped
    mid-write, so the log cannot be trusted.
    """
    lines = text.split("\n")
    if lines[-1]:
        raise BenchmarkValidationError(f"proxy log line {len(lines)} is unterminated")
    events = []
    for number, line in enumerate(lines[:-1], 1):
        fields = line.split(" ")
        try:
            at, client, method, host, port, status, result = fields
            events.append(ProxyEvent(at, client, method, host, int(port), int(status), result))
        except ValueError as exc:
            raise BenchmarkValidationError(f"proxy log line {number} is malformed") from exc
    return events


_SQUID_HEAD = f"""\
http_port {EGRESS_PORT}
visible_hostname {EGRESS_ALIAS}
pid_filename none
cache deny all
shutdown_lifetime 1 seconds
httpd_suppress_version_string on
logfile_rotate 0
logformat bench %{{%Y-%m-%dT%H:%M:%S}}tg.%03tuZ %>a %rm %>rd %>rP %>Hs %Ss
access_log stdio:{LOG_DIR_IN_PROXY}/access.log logformat=bench rotate=0
cache_log {LOG_DIR_IN_PROXY}/cache.log
"""


def squid_conf(allow: Sequence[tuple[str, int]]) -> str:
    """One proxy's config: only ``allow``, logged to a file with no rotation.

    Port 443 allows only CONNECT, any other port only plain HTTP. Every host ACL is
    ``dstdomain -n``: no reverse lookup, so an IP-literal request never matches a name.
    No TLS interception, and no ``dst`` ACL (it resolves names).
    """
    lines = []
    for index, (host, port) in enumerate(allow, 1):
        method = "CONNECT" if port == 443 else "!CONNECT"
        lines += [
            f"acl allow_{index}_host dstdomain -n {host}",
            f"acl allow_{index}_port port {port}",
            f"http_access allow {method} allow_{index}_host allow_{index}_port",
        ]
    return _SQUID_HEAD + "".join(f"{line}\n" for line in lines) + "http_access deny all\n"


# shortcut: a job's log directory is mode 0777 because Squid runs as `proxy` and the host cannot
# chown it on Docker Desktop for macOS. Anything in it may be a link a compromised proxy planted,
# so the host never follows one: read_log opens with O_NOFOLLOW and refuses a non-regular file,
# and write_new writes a new O_EXCL file and renames it over the name. Upgrade to a directory
# chowned to the proxy's uid, mode 0750, if Linux hosts are ever supported.
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC  # a FIFO never blocks
_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


@contextmanager
def open_log_dir(path: Path) -> Iterator[int]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise BenchmarkValidationError(f"log directory is a link or unavailable: {exc}") from exc
    try:
        yield fd
    finally:
        os.close(fd)


def read_log(path: Path) -> bytes:
    with open_log_dir(path.parent) as dir_fd:
        return read_log_at(dir_fd, path.name)


def read_log_at(dir_fd: int, name: str) -> bytes:
    """Read a regular file without following a link or blocking on a FIFO."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        raise BenchmarkValidationError(f"{name} is missing") from None
    except OSError as exc:  # ELOOP: a link
        raise BenchmarkValidationError(f"{name} was refused ({exc.strerror})") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BenchmarkValidationError(f"{name} is not a regular file")
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def write_new(dir_fd: int, name: str, data: bytes, *, mode: int = 0o600) -> None:
    """Replace ``name`` by a new file holding ``data``: never written through the old name.

    The bytes go to a new file in the same directory, created exclusively, then renamed over
    ``name``, which replaces a link itself, never its target.
    """
    temporary = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temporary, _NEW_FLAGS, 0o600, dir_fd=dir_fd)
    try:
        try:
            os.fchmod(fd, mode)  # exactly ``mode``, whatever the umask
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
        finally:
            os.close(fd)
        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=dir_fd)
        raise


def forbidden_env_names(environ: Mapping[str, str]) -> list[str]:
    """The proxy and CA variable names set in ``environ``, sorted; never their values."""
    return sorted(name for name in environ if FORBIDDEN_ENV.match(name))


# --- C1 evidence: egress/summary.json, in the shape PR 5 (#199) reads ------------------------


def lookup_attempts(out_dir: Path, family_id: str, system_id: str) -> int:
    """The model's ``lookup_package_docs`` calls in one job's scored and discarded records."""
    cell = re.compile(rf"{re.escape(system_id)}--{re.escape(family_id)}--r\d+")
    count = 0
    for kind in ("transcripts", "discarded"):
        root = out_dir / kind
        cells = [c for c in root.iterdir() if cell.fullmatch(c.name)] if root.is_dir() else []
        for path in (p for c in cells for p in sorted(c.glob("*.json"))):
            calls = json.loads(path.read_text(encoding="utf-8")).get("tool_calls") or []
            count += sum(call.get("tool") == "lookup_package_docs" for call in calls)
    return count


def pending_collection(out_dir: Path, job_id: str) -> Path:
    """Host-owned marker, outside the proxy mount, until collection completes cleanly."""
    return out_dir / "probe" / f"{job_id}.collection-pending"


def _job_hosts(out_dir: Path, job_id: str) -> tuple[bool, list[ProxyEvent]]:
    """Whether the job's collection, probe and log all hold, and its query-time events.

    Query time is every access-log line after the probe's verified ``proxy_prefix``: probe
    traffic never reaches the summary. Anything unreadable gives (False, []).
    """
    log_dir = out_dir / "egress" / job_id
    try:
        probe = json.loads((out_dir / "probe" / f"{job_id}.json").read_text(encoding="utf-8"))
        if pending_collection(out_dir, job_id).exists():
            return False, []
        with open_log_dir(log_dir) as dir_fd:
            collected = json.loads(read_log_at(dir_fd, "collected.json"))
            text = read_log_at(dir_fd, "access.log").decode("utf-8")
        events = parse_proxy_log(text)
    except (OSError, ValueError, BenchmarkValidationError):
        return False, []
    prefix = probe.get("proxy_prefix")
    if not isinstance(prefix, list) or text.splitlines()[: len(prefix)] != prefix:
        return False, []  # truncated, or lines before the probe's own
    ok = (
        probe.get("passed") is True
        and isinstance(collected, dict)
        and collected.get("ok") is True
        and all(not e.allowed or e.reached for e in events)
    )
    return ok, events[len(prefix) :]


def write_egress_summary(
    out_dir: Path, jobs: Sequence[WorkerJob], ledger_events: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """``out_dir/egress/summary.json``: the C1 evidence, with exactly the keys #199 binds.

    ``isolation_probe`` is "pass" only when the control and every job's probe passed, every
    collection was clean, every log holds its probe prefix and has only conclusive events,
    every ``ok`` settle in the ledger
    is attributed to a planned job, and every hosted live job with an ``ok`` settle reached its
    provider through its proxy (round-2 rulings C5, C6; round-3 items 1, 2).
    """
    planned = {job.job_id for job in jobs}
    reserved = {e["unit_id"]: e.get("job_id") for e in ledger_events if e["event"] == "reserve"}
    billed: set[str] = set()
    attributed = True
    for event in ledger_events:
        if event["event"] == "settle" and event.get("outcome") == "ok":
            job_id = reserved.get(event["unit_id"])
            if job_id in planned:
                billed.add(job_id)
            else:  # no reserve, a reserve without a job id, or a job outside the plan
                attributed = False
    try:
        control = json.loads((out_dir / "probe" / "control.json").read_text(encoding="utf-8"))
        passed = control.get("passed") is True and attributed
    except (OSError, ValueError):
        passed = False
    entries: dict[str, Any] = {}
    for job in jobs:
        ok, events = _job_hosts(out_dir, job.job_id)
        allowed = sorted({e.target for e in events if e.reached})
        family = job.family
        if family.arm == "hosted" and family.api != "scripted" and job.job_id in billed:
            provider = {f"{h}:{p}" for h, p in PROVIDER_HOSTS.get(family.provider, ())}
            ok = ok and bool(provider & set(allowed))  # billed, so it must show its provider
        passed = passed and ok
        entries[job.job_id] = {
            "hosts_allowed": allowed,
            "hosts_denied": sorted({e.target for e in events if not e.allowed}),
            "lookup_package_docs_attempts": lookup_attempts(out_dir, family.id, job.system.id),
        }
    summary = {"jobs": entries, "isolation_probe": "pass" if passed else "fail"}
    # The shape #199 reads, and nothing else (round-2 ruling E18).
    assert set(summary) == {"jobs", "isolation_probe"}
    assert all(
        set(j) == {"hosts_allowed", "hosts_denied", "lookup_package_docs_attempts"}
        for j in summary["jobs"].values()
    )
    path = out_dir / "egress" / "summary.json"
    assert_no_key_shaped(json.dumps(summary, sort_keys=True), where=str(path))
    _write_json(path, summary)
    return summary
