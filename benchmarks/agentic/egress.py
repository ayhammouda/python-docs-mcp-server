"""Egress policy for the sandbox: allowlists, the Squid config and its access log (design §7.3).

Imports nothing heavier than the model matrix and the runner: the isolation probe imports this
module inside the worker container.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

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


def forbidden_env_names(environ: Mapping[str, str]) -> list[str]:
    """The proxy and CA variable names set in ``environ``, sorted; never their values."""
    return sorted(name for name in environ if FORBIDDEN_ENV.match(name))
