"""Egress policy for the sandbox: allowlists, the Squid config and its access log (design §7.3).

Imports nothing heavier than the model matrix and the runner: the isolation probe imports this
module inside the worker container.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from benchmarks.model_matrix import ModelFamily
from benchmarks.runner import BenchmarkValidationError

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
    """The job's provider hosts (none for a scripted model), then its system's hosts."""
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
        return not self.result.startswith("TCP_DENIED")

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


def read_log(path: Path) -> bytes:
    """A file under a log directory, never through a link: a link, FIFO, directory or any
    other non-regular file is refused, and so is a missing one."""
    try:
        fd = os.open(path, _READ_FLAGS)
    except FileNotFoundError:
        raise BenchmarkValidationError(f"{path.name} is missing") from None
    except OSError as exc:  # ELOOP: a link
        raise BenchmarkValidationError(f"{path.name} was refused ({exc.strerror})") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BenchmarkValidationError(f"{path.name} is not a regular file")
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def write_new(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Replace ``path`` by a new file holding ``data``: never written through the old name.

    The bytes go to a new file in the same directory, created exclusively, then renamed over
    ``path``, which replaces a link itself, never its target.
    """
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temporary, _NEW_FLAGS, 0o600)
    try:
        try:
            os.fchmod(fd, mode)  # exactly ``mode``, whatever the umask
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
        finally:
            os.close(fd)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def forbidden_env_names(environ: Mapping[str, str]) -> list[str]:
    """The proxy and CA variable names set in ``environ``, sorted; never their values."""
    return sorted(name for name in environ if FORBIDDEN_ENV.match(name))
