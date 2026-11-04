"""The air-gapped arm: host offline checks, the Ollama preflight and the C3 evidence.

No test touches the network or a real Ollama: connects, routes and HTTP are all fakes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agentic.airgap import (
    MIN_OLLAMA,
    REQUIRED_CONTEXT,
    default_routes,
    host_offline_checks,
    ollama_preflight,
)
from benchmarks.agentic.run import run_agentic
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_run import _config

FIX = Path(__file__).parent / "fixtures" / "agentic"


@pytest.mark.parametrize(
    "system,text,expected",
    [
        (
            "Darwin",
            "Routing tables\n\nInternet:\nDestination Gateway Flags Netif\n"
            "127.0.0.1 127.0.0.1 UH lo0\n",
            [],
        ),
        (
            "Darwin",
            "Internet6:\ndefault fe80::%utun0 UGcIg utun0\n",
            ["default fe80::%utun0 UGcIg utun0"],
        ),
        (
            "Linux",
            "Iface\tDestination\tGateway\neth0\t00000000\t0100A8C0\n",
            ["eth0\t00000000\t0100A8C0"],
        ),
        (
            "Linux",
            "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
            "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
            "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 ffffffff 00000001 00000001 00200200 lo\n",
            [
                "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
                "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0"
            ],
        ),
    ],
)
def test_default_routes_on_macos_and_linux(system: str, text: str, expected: list[str]) -> None:
    assert default_routes(system=system, read=lambda: text) == expected


class _Conn:
    def close(self) -> None:
        return None


def _connect(reachable: set[str]) -> Callable[..., Any]:
    def connect(address: tuple[str, int], timeout: float) -> _Conn:
        if address[0] in reachable:
            return _Conn()
        raise OSError(51, "Network is unreachable")

    return connect


def test_offline_checks_need_both_a_name_and_an_address_to_fail() -> None:
    checks = host_offline_checks(connect=_connect({"1.1.1.1"}), routes=lambda: [])
    by_name = {c["check"]: c for c in checks}
    assert by_name["outside_connect:pypi.org"]["passed"] is True  # DNS is down
    assert by_name["outside_connect:1.1.1.1"]["passed"] is False  # but IP still works
    assert by_name["no_default_route"]["passed"] is True
    assert all("at" in c for c in checks)
    offline = host_offline_checks(connect=_connect(set()), routes=lambda: ["default x UGScg en0"])
    route = {c["check"]: c for c in offline}["no_default_route"]
    assert route["passed"] is False and route["detail"] == ["default x UGScg en0"]


def _ollama(version: str = "0.40.14", context: int = REQUIRED_CONTEXT, present: bool = True):
    calls: list[tuple[str, Any]] = []
    tags = [
        {
            "name": "gemma4:12b",
            "model": "gemma4:12b",
            "digest": "c7597fc90b86",
            "details": {"format": "mlx"},
        }
    ]

    def fetch(url: str, body: bytes | None) -> dict[str, Any]:
        calls.append((url, None if body is None else json.loads(body)))
        path = url.removeprefix("http://127.0.0.1:11434")
        if path == "/api/version":
            return {"version": version}
        if path == "/api/tags":
            return {"models": tags if present else []}
        if path == "/api/generate":
            return {"model": "gemma4:12b", "response": "", "done": True}
        if path == "/api/ps":
            return {
                "models": [{"name": "gemma4:12b", "model": "gemma4:12b", "context_length": context}]
            }
        if path == "/api/show":
            return {"parameters": "stop <end_of_turn>", "model_info": {}}
        raise AssertionError(url)

    return fetch, calls


def test_ollama_preflight_requires_the_32768_context() -> None:
    fetch, _ = _ollama(context=4096)
    with pytest.raises(BenchmarkValidationError, match="4096"):
        ollama_preflight("gemma4:12b", fetch=fetch)
    fetch, calls = _ollama()
    record = ollama_preflight("gemma4:12b", fetch=fetch)
    assert record["context_length"] == 32_768 and record["version"] == "0.40.14"
    assert (
        "http://127.0.0.1:11434/api/generate",
        {"model": "gemma4:12b", "prompt": "", "keep_alive": "60m"},
    ) in calls
    assert record["parameters"] == "stop <end_of_turn>"


def test_ollama_preflight_refuses_a_version_before_0_40_14() -> None:
    assert MIN_OLLAMA == (0, 40, 14)
    fetch, calls = _ollama(version="0.40.8")
    with pytest.raises(BenchmarkValidationError, match="0.40.8"):
        ollama_preflight("gemma4:12b", fetch=fetch)
    assert [url for url, _ in calls] == ["http://127.0.0.1:11434/api/version"]  # nothing loaded
    fetch, _ = _ollama(present=False)
    with pytest.raises(BenchmarkValidationError, match="gemma4:12b"):
        ollama_preflight("gemma4:12b", fetch=fetch)


def test_ollama_preflight_records_the_reported_digest() -> None:
    fetch, _ = _ollama()
    record = ollama_preflight("gemma4:12b", fetch=fetch)
    assert record["digest"] == "c7597fc90b86" and record["details"] == {"format": "mlx"}
    assert record["model"] == "gemma4:12b"


def _local_config(tmp_path: Path, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "matrix_path": FIX / "model-matrix-local.yml",
        "models": ("scripted-local",),
        "systems": ("no-docs",),
        "questions": ("SYN-EX-001", "SYN-EX-002"),
        "repeats": 1,
    }
    return _config(tmp_path, **(values | overrides))


def _counting(fail_on: int | None = None) -> tuple[Callable[[], list[dict[str, Any]]], list[int]]:
    rounds: list[int] = []

    def checks() -> list[dict[str, Any]]:
        rounds.append(len(rounds) + 1)
        passed = len(rounds) != fail_on
        return [
            {"check": "outside_connect:pypi.org", "passed": True, "detail": "", "at": "t"},
            {"check": "no_default_route", "passed": passed, "detail": [], "at": "t"},
        ]

    return checks, rounds


def test_the_arm_checks_the_host_before_each_trajectory_and_after_the_last(tmp_path: Path) -> None:
    checks, rounds = _counting()
    summary = run_agentic(_local_config(tmp_path), offline_checks=checks)
    assert summary["status"] == "completed" and summary["succeeded_cells"] == 2
    assert rounds == [1, 2, 3, 4]  # before the first, after each of 2 trajectories, after the last
    assert summary["airgap"] == {"checks": 4, "broken": False}
    records = (tmp_path / "run" / "airgap" / "records.jsonl").read_text().splitlines()
    whens = [json.loads(line)["when"] for line in records]
    assert len(whens) == 8  # two records per round
    rounds_seen = list(dict.fromkeys(whens))
    assert rounds_seen[0] == "before_first" and rounds_seen[-1] == "after_last"
    assert sorted(rounds_seen[1:-1]) == [
        "after no-docs--scripted-local--r1/SYN-EX-001",
        "after no-docs--scripted-local--r1/SYN-EX-002",
    ]


def test_the_first_failed_check_stops_the_arm(tmp_path: Path) -> None:
    checks, rounds = _counting(fail_on=2)
    with pytest.raises(BenchmarkValidationError, match="no_default_route"):
        run_agentic(_local_config(tmp_path), offline_checks=checks)
    summary = json.loads((tmp_path / "run" / "run-summary.json").read_text())
    assert summary["status"] == "aborted" and "no_default_route" in summary["abort_reason"]
    assert summary["airgap"] == {"checks": 2, "broken": True}
    last = json.loads((tmp_path / "run" / "airgap" / "records.jsonl").read_text().splitlines()[-1])
    assert last["passed"] is False and last["check"] == "no_default_route"


def test_a_hosted_run_makes_no_offline_check(tmp_path: Path) -> None:
    def never() -> list[dict[str, Any]]:
        raise AssertionError("a hosted run is online")

    config = _config(tmp_path, models=("scripted-anthropic",), systems=("no-docs",), repeats=1)
    summary = run_agentic(config, offline_checks=never)
    assert summary["status"] == "completed" and "airgap" not in summary
