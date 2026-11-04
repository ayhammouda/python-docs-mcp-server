"""The air-gapped arm: host offline checks around every trajectory, and the Ollama preflight.

Design §7.4: the Mac is offline for the whole arm. A check round runs before the first
trajectory, after each reported trajectory or discard (before the worker starts its next),
and after the last; the first failed check stops the arm.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
import socket
import subprocess
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.runner import BenchmarkValidationError, _write_json

#: A name (DNS must be down) and an address (IP must be down too): both must fail.
OUTSIDE_TARGETS = (("pypi.org", 443), ("1.1.1.1", 443))
REQUIRED_CONTEXT = 32_768
MIN_OLLAMA = (0, 40, 14)  # older releases override packaged sampler defaults (#200 ruling 6)
OLLAMA_LOCAL = "http://127.0.0.1:11434"
_CONNECT_S = 5.0

Fetch = Callable[[str, bytes | None], dict[str, Any]]


def _read_routes(system: str) -> str:
    if system == "Darwin":
        return subprocess.run(["netstat", "-rn"], capture_output=True, text=True).stdout
    return "".join(Path(p).read_text() for p in ("/proc/net/route", "/proc/net/ipv6_route"))


def default_routes(
    *, system: str | None = None, read: Callable[[], str] | None = None
) -> list[str]:
    """Every default route, IPv4 or IPv6: a VPN's or Private Relay's ``utun`` one included."""
    system = system or platform.system()
    text = read() if read is not None else _read_routes(system)
    found = []
    for line in text.splitlines():
        fields = line.split()
        if system == "Darwin":
            default = fields[:1] == ["default"]
        elif len(fields) == 10:  # /proc/net/ipv6_route: ::/0, not the kernel's reject on lo
            default = fields[0] == "0" * 32 and fields[1] == "00" and fields[9] != "lo"
        else:  # /proc/net/route
            default = len(fields) > 1 and fields[1] == "00000000"
        if default:
            found.append(line.strip())
    return found


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def host_offline_checks(
    *,
    connect: Callable[..., Any] = socket.create_connection,
    routes: Callable[[], list[str]] = default_routes,
) -> list[dict[str, Any]]:
    """One check round: no outside connection, by name or by address, and no default route."""
    records = []
    for host, port in OUTSIDE_TARGETS:
        try:
            connect((host, port), _CONNECT_S).close()
        except OSError as exc:
            passed, detail = True, type(exc).__name__
        else:
            passed, detail = False, "connected"
        records.append(
            {"check": f"outside_connect:{host}", "passed": passed, "detail": detail, "at": _now()}
        )
    lines = routes()
    records.append(
        {"check": "no_default_route", "passed": not lines, "detail": lines, "at": _now()}
    )
    return records


def _fetch(url: str, body: bytes | None) -> dict[str, Any]:
    # ProxyHandler({}): no host proxy variable can send a request anywhere but local Ollama.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = urllib.request.Request(url, data=body, headers=headers)
    with opener.open(request, timeout=600) as response:
        return dict(json.loads(response.read()))


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text)[:3])


def _named(models: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    return next((m for m in models if model_id in (m.get("name"), m.get("model"))), None)


def ollama_preflight(
    model_id: str, *, base: str = OLLAMA_LOCAL, fetch: Fetch | None = None
) -> dict[str, Any]:
    """Ollama's version, the model's digest and details, and its loaded context, recorded.

    Refuses a version before MIN_OLLAMA, a missing model, or a context that is not 32,768.
    """
    get = fetch or _fetch
    version = str(get(f"{base}/api/version", None).get("version", ""))
    if _version(version) < MIN_OLLAMA:
        raise BenchmarkValidationError(
            f"Ollama {version} is older than {'.'.join(map(str, MIN_OLLAMA))}, which the "
            "air-gapped arm needs (Ollama defaults)"
        )
    tag = _named(list(get(f"{base}/api/tags", None).get("models") or []), model_id)
    if tag is None:
        raise BenchmarkValidationError(f"Ollama has no model {model_id}: ollama pull it first")
    load = {"model": model_id, "prompt": "", "keep_alive": "60m"}
    get(f"{base}/api/generate", json.dumps(load).encode())
    running = _named(list(get(f"{base}/api/ps", None).get("models") or []), model_id) or {}
    context = running.get("context_length")
    if context != REQUIRED_CONTEXT:
        raise BenchmarkValidationError(
            f"{model_id} is loaded with a {context}-token context, not {REQUIRED_CONTEXT}: start "
            f"Ollama with OLLAMA_CONTEXT_LENGTH={REQUIRED_CONTEXT}"
        )
    shown = get(f"{base}/api/show", json.dumps({"model": model_id}).encode())
    return {
        "version": version,
        "model": model_id,
        "digest": tag.get("digest"),
        "details": tag.get("details"),
        "context_length": context,
        "parameters": shown.get("parameters"),
    }


class AirGapBroken(BenchmarkValidationError):
    """A host offline check failed: the air-gapped arm stops."""


class AirGapWatch:
    """Runs check rounds, appends each record to ``airgap/records.jsonl``, stops on a failure."""

    def __init__(
        self, out_dir: Path, checks: Callable[[], list[dict[str, Any]]] = host_offline_checks
    ) -> None:
        self._path = out_dir / "airgap" / "records.jsonl"
        self._checks = checks
        self.rounds = 0
        self.broken = False

    async def check(self, when: str) -> None:
        records = await asyncio.to_thread(self._checks)  # connects block for up to 5 s
        self.rounds += 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps({**record, "when": when}, sort_keys=True) + "\n")
        failed = [r["check"] for r in records if not r["passed"]]
        if failed:
            self.broken = True
            raise AirGapBroken(f"air-gap check {failed[0]} failed {when}: the arm stops")


#: The arm's own Ollama, reached through its proxy: never an internet host (round-2 ruling C7).
_LOCAL_OLLAMA = "host.docker.internal:11434"


def write_airgap_evidence(
    out_dir: Path, *, rounds: int, broken: bool, job_ids: Sequence[str]
) -> dict[str, Any]:
    """``out_dir/airgap/checks.json``: the C3 evidence, with exactly the keys #199 binds.

    ``internet_hosts`` is every host the air-gapped jobs actually reached (their
    ``hosts_allowed`` in egress/summary.json) but local Ollama; a denied attempt left nothing
    and stays in ``hosts_denied``. ``all_passed`` needs at least one round, every round
    passed, the arm not stopped, a passing isolation probe and no internet host.
    """
    try:
        egress = json.loads((out_dir / "egress" / "summary.json").read_text(encoding="utf-8"))
        jobs = egress["jobs"]
        reached = {h for job_id in job_ids for h in jobs.get(job_id, {}).get("hosts_allowed", [])}
        probe_passed = egress.get("isolation_probe") == "pass" and set(job_ids) <= set(jobs)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        reached, probe_passed = set(), False
    internet = sorted(reached - {_LOCAL_OLLAMA})
    result = {
        "all_passed": rounds > 0 and not broken and probe_passed and not internet,
        "checks": rounds,
        "internet_hosts": internet,
    }
    assert set(result) == {"all_passed", "checks", "internet_hosts"}  # round-2 ruling E18
    _write_json(out_dir / "airgap" / "checks.json", result)
    return result
