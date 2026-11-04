"""The isolation probe: every sandboxed job's network is checked keyless before any worker starts.

Two halves. In a probe container on the job's internal network, ``run_probe_checks`` tries
every way out it should not have (direct connections to the Mac and the network's gateways,
DNS, a denied proxy target) and the one it should (an allowed URL through the proxy). It uses
only the standard library and ``egress``. On the host, a ``Canary`` listener must never see the
probe's token, the proxy's own log must show both denials, and a control container on the
outside network must reach the canary, so a silent canary means something.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from benchmarks.agentic.cleanup import finish
from benchmarks.agentic.egress import (
    EGRESS_ALIAS,
    EGRESS_PORT,
    ProxyEvent,
    forbidden_env_names,
    parse_proxy_log,
    read_log,
    write_scanned_json,
)
from benchmarks.agentic.protocol import KeyLeakError, assert_no_key_shaped, redact_key_shaped
from benchmarks.runner import BenchmarkValidationError

if TYPE_CHECKING:
    from benchmarks.agentic.sandbox import Egress, Sandbox
    from benchmarks.model_matrix import ModelFamily

_CONNECT_S = 2.0
_PROXY_S = 5.0
_OLLAMA_VERSION_URL = "http://host.docker.internal:11434/api/version"
#: The hosts every probe tries directly, besides the Mac's own addresses and the gateways.
_MAC_NAMES = ("host.docker.internal", "gateway.docker.internal")
_OLLAMA_PORT = 11434
#: How long the host waits for the proxy's log lines and the canary's bytes to land.
_SETTLE_S = 3.0
#: Every interface, so the canary is reachable however the Mac is addressed (tests use loopback).
_CANARY_BIND = "0.0.0.0"


# --- in the container --------------------------------------------------------------------------


def _result(check: str, target: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": check, "target": target, "passed": passed, "detail": detail}


def _direct(host: str, port: int, token: str) -> dict[str, Any]:
    """A direct connection must fail; one that succeeds sends the token to whoever listens."""
    target = f"{host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_S) as conn:
            with suppress(OSError):
                conn.sendall(f"GET / HTTP/1.0\r\nAuthorization: Bearer {token}\r\n\r\n".encode())
    except OSError as exc:
        return _result("direct", target, True, type(exc).__name__)
    return _result("direct", target, False, "connected")


def _through_proxy(proxy: tuple[str, int], request: str) -> int | None:
    """The status code the proxy answers ``request`` with; None if it gives none."""
    try:
        with socket.create_connection(proxy, timeout=_PROXY_S) as conn:
            conn.sendall(request.encode())
            data = b""
            while b"\r\n" not in data and (chunk := conn.recv(4096)):
                data += chunk
    except OSError:
        return None
    parts = data.split(b"\r\n", 1)[0].split(b" ")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def run_probe_checks(
    spec: Mapping[str, Any], *, proxy: tuple[str, int] = (EGRESS_ALIAS, EGRESS_PORT)
) -> list[dict[str, Any]]:
    """Every check the spec asks for, each ``{"check", "target", "passed", "detail"}``."""
    token = str(spec.get("token", ""))
    forbidden = forbidden_env_names(os.environ)
    names = ", ".join(forbidden)  # names, never values: a failure names what was injected
    results = [_result("env", names, not forbidden, names)]
    results += [_direct(str(host), int(port), token) for host, port in spec.get("direct", [])]
    if dns_name := spec.get("dns_name"):
        try:
            socket.getaddrinfo(dns_name, 443)
        except OSError as exc:
            results.append(_result("dns", dns_name, True, type(exc).__name__))
        else:
            results.append(_result("dns", dns_name, False, "resolved"))
    if denied := spec.get("denied"):
        target = f"{denied[0]}:{denied[1]}"
        status = _through_proxy(proxy, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n")
        results.append(_result("proxy_denied", target, status == 403, str(status)))
    if canary := spec.get("canary_via_proxy"):
        target = f"{canary[0]}:{canary[1]}"
        status = _through_proxy(
            proxy,
            f"GET http://{target}/ HTTP/1.1\r\nHost: {target}\r\n"
            f"Authorization: Bearer {token}\r\nConnection: close\r\n\r\n",
        )
        results.append(_result("proxy_canary", target, status == 403, str(status)))
    for url in spec.get("allowed_http", []):
        host = urlsplit(url).netloc
        request = f"GET {url} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        status = _through_proxy(proxy, request)
        results.append(_result("allowed_http", url, status == 200, str(status)))
    return results


def run_control(canary: tuple[str, int], token: str) -> dict[str, Any]:
    """From the outside network the canary must be reachable, and receive the token."""
    host, port = canary
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_S) as conn:
            conn.sendall(f"GET / HTTP/1.0\r\nAuthorization: Bearer {token}\r\n\r\n".encode())
    except OSError as exc:
        return _result("control", f"{host}:{port}", False, type(exc).__name__)
    return _result("control", f"{host}:{port}", True, "connected")


def probe_main(argv: list[str]) -> int:
    """``agentic-probe --spec <json>``: one JSON object ``{"checks": [...]}``, exit 0 whatever."""
    parser = argparse.ArgumentParser(prog="python -m benchmarks agentic-probe")
    parser.add_argument("--spec", required=True)
    spec = json.loads(parser.parse_args(argv).spec)
    if "control" in spec:
        checks = [run_control((spec["control"][0], int(spec["control"][1])), spec["token"])]
    else:
        checks = run_probe_checks(spec)
    print(json.dumps({"checks": checks}))
    return 0


# --- on the host --------------------------------------------------------------------------------


class IsolationProbeFailed(BenchmarkValidationError):
    """A sandboxed job's network let something out, or the probe could not show it did not."""


class Canary:
    """A TCP listener on every interface of the Mac; it records what each connection sends."""

    def __init__(self) -> None:
        self.port = 0
        self.received: list[bytes] = []
        self._server: asyncio.Server | None = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with suppress(TimeoutError, OSError):
            self.received.append(await asyncio.wait_for(reader.read(4096), _CONNECT_S))
        writer.close()

    async def __aenter__(self) -> Canary:
        self._server = await asyncio.start_server(self._handle, _CANARY_BIND, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self, exc_type: object, exc: BaseException | None, traceback: object
    ) -> None:
        if (server := self._server) is not None:

            async def close() -> None:
                server.close()
                await server.wait_closed()

            await finish(close(), exc)


def host_addresses() -> list[str]:
    """The Mac's non-loopback IPv4 addresses; short or empty offline, never an error."""
    found: list[str] = []
    with suppress(OSError):
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    with suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.connect(("192.0.2.1", 9))  # no packet is sent: this only picks the route
        found.append(udp.getsockname()[0])
    return sorted({a for a in found if not a.startswith("127.")})


def probe_verdict(
    checks: Sequence[Mapping[str, Any]],
    *,
    canary_received: Sequence[bytes],
    token: str,
    proxy_events: Sequence[ProxyEvent],
    denied: tuple[str, int],
    canary: tuple[str, int],
) -> list[str]:
    """What failed: each failed check, a canary that saw the token, a denial not logged."""
    failed = [f"{c['check']} {c['target']}".strip() for c in checks if not c["passed"]]
    if any(token.encode() in data for data in canary_received):
        failed.append("canary_saw_token")
    refused = [(e.method, e.host, e.port) for e in proxy_events if not e.allowed]
    connect_logged = ("CONNECT", *denied) in refused
    canary_logged = any(m != "CONNECT" and (h, p) == canary for m, h, p in refused)
    if not (connect_logged and canary_logged):
        failed.append("denial_not_logged")
    return failed


async def _one_shot(sandbox: Sandbox, argv: list[str], name: str) -> list[dict[str, Any]]:
    """Run a probe container to its end; it is removed even on a timeout or a cancellation."""
    container_id = await sandbox.create_resource(name, *argv[1:])
    try:
        proc = await sandbox.docker(["docker", "start", "--attach", container_id])
    finally:
        await sandbox.remove_container(container_id, pending=sys.exception())
    try:
        if proc.returncode != 0:
            raise ValueError(f"exit {proc.returncode}")
        return list(json.loads(proc.stdout)["checks"])
    except (ValueError, KeyError, TypeError) as exc:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        assert_no_key_shaped(detail, where=f"{name} stderr")
        detail = detail[-300:]
        raise IsolationProbeFailed(f"probe container {name} failed ({exc}): {detail}") from None


def _probe_argv(
    sandbox: Sandbox, name: str, network: str, spec: dict[str, Any], env: Any
) -> list[str]:
    return sandbox.container_argv(
        name=name,
        network=network,
        env=env,
        mounts=[],
        argv=["python", "-m", "benchmarks", "agentic-probe", "--spec", json.dumps(spec)],
        interactive=False,
        create_only=True,
    )


async def _settle(done: Any) -> None:
    """Wait a bounded time for something the host checks next to land."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETTLE_S
    while not done() and loop.time() < deadline:
        await asyncio.sleep(0.05)


async def isolation_probe(
    sandbox: Sandbox, egress: Egress, *, job_id: str, family: ModelFamily, system_id: str
) -> dict[str, Any]:
    """The default ProbeFn: probe the job's network keyless; write ``probe/<job_id>.json``."""
    from benchmarks.agentic.sandbox import worker_env  # host side only

    token = secrets.token_hex(16)
    denied = ("example.com", 443)
    async with Canary() as canary:
        via_proxy = ("host.docker.internal", canary.port)
        hosts = [*_MAC_NAMES, *host_addresses(), *egress.gateways]
        allowed_http = (
            [_OLLAMA_VERSION_URL] if ("host.docker.internal", _OLLAMA_PORT) in egress.allow else []
        )
        spec = {
            "token": token,
            "direct": [[host, port] for host in hosts for port in (canary.port, _OLLAMA_PORT)],
            "dns_name": "pypi.org",
            "denied": list(denied),
            "canary_via_proxy": list(via_proxy),
            "allowed_http": allowed_http,
        }
        name = f"{sandbox.worker_name(job_id)}-probe"
        argv = _probe_argv(sandbox, name, egress.network, spec, worker_env(family))
        checks = await _one_shot(sandbox, argv, name)
        log = egress.spool_dir / "access.log"

        def verdict() -> tuple[list[str], list[str]]:
            try:
                data, pattern = redact_key_shaped(read_log(log))
                if pattern:
                    raise KeyLeakError(
                        f"key-shaped string ({pattern} pattern) in {job_id}/access.log"
                    )
                text = data.decode("utf-8")
                events = parse_proxy_log(text)
            except KeyLeakError:
                raise
            except (OSError, ValueError) as exc:
                return [f"proxy_log {exc}"], []
            found = probe_verdict(
                checks,
                canary_received=canary.received,
                token=token,
                proxy_events=events,
                denied=denied,
                canary=via_proxy,
            )
            for url in allowed_http:
                target = urlsplit(url).netloc
                if not any(
                    e.method == "GET" and e.target == target and e.status == 200 and e.reached
                    for e in events
                ):
                    found.append(f"allowed_http_not_logged {url}")
            return found, text.splitlines()

        await _settle(lambda: not verdict()[0])
        failed, prefix = verdict()
    record = {
        "job_id": job_id,
        "checks": checks,
        "failed": failed,
        "passed": not failed,
        "network_inspect": egress.inspect,
        "proxy_prefix": prefix,  # the exact access-log lines when the verdict was taken
    }
    write_scanned_json(sandbox.config.out_dir / "probe" / f"{job_id}.json", record)
    if failed:
        raise IsolationProbeFailed(f"isolation probe failed for {job_id}: {', '.join(failed)}")
    return record


async def control_probe(sandbox: Sandbox) -> dict[str, Any]:
    """From the outside network the canary must be reached, else every probe is inconclusive."""
    await sandbox.ensure_outside()
    token = secrets.token_hex(16)
    async with Canary() as canary:
        name = f"pdmcp-{sandbox.config.run_id}-control"
        spec = {"control": ["host.docker.internal", canary.port], "token": token}
        argv = _probe_argv(sandbox, name, sandbox.outside, spec, {"HOME": "/tmp"})
        checks = await _one_shot(sandbox, argv, name)
        await _settle(lambda: any(token.encode() in data for data in canary.received))
        reached = any(token.encode() in data for data in canary.received)
    passed = reached and all(c["passed"] for c in checks)
    record = {"checks": checks, "canary_reached": reached, "passed": passed}
    write_scanned_json(sandbox.config.out_dir / "probe" / "control.json", record)
    if not passed:
        raise IsolationProbeFailed(
            "inconclusive: a control container on the outside network could not reach the "
            "host canary, so a silent canary proves nothing"
        )
    return record
