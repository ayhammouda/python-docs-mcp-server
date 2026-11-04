"""The air-gapped arm: host offline checks, the Ollama preflight and the C3 evidence.

No test touches the network or a real Ollama: connects, routes and HTTP are all fakes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from benchmarks.__main__ import main
from benchmarks.agentic.airgap import (
    MIN_OLLAMA,
    REQUIRED_CONTEXT,
    default_routes,
    host_offline_checks,
    ollama_preflight,
    write_airgap_evidence,
)
from benchmarks.agentic.egress import allowlist
from benchmarks.agentic.run import run_agentic
from benchmarks.model_matrix import load_model_matrix
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_egress import PROBE_LINES, PYPI_LINE, _evidence, _lines
from tests.benchmarks.test_agentic_run import (
    COMMITTED_MATRIX,
    _cli,
    _config,
    _container,
    _EveryFamily,
    _live_config,
)

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


@pytest.mark.parametrize("output", ["", "permission denied"])
def test_unrecognized_netstat_output_cannot_certify_an_offline_host(output: str) -> None:
    checks = host_offline_checks(
        connect=_connect(set()), routes=lambda: default_routes(system="Darwin", read=lambda: output)
    )
    route = next(check for check in checks if check["check"] == "no_default_route")
    assert route["passed"] is False


def test_failed_netstat_is_a_failed_offline_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netstat = tmp_path / "netstat"
    netstat.write_text("#!/bin/sh\nexit 7\n")
    netstat.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    checks = host_offline_checks(
        connect=_connect(set()), routes=lambda: default_routes(system="Darwin")
    )
    route = next(check for check in checks if check["check"] == "no_default_route")
    assert route["passed"] is False
    assert "7" in str(route["detail"])


def test_gemma_cli_defaults_to_the_two_offline_systems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _cli(
        tmp_path,
        "--launcher",
        "container",
        "--image",
        "sha256:" + "0" * 64,
        "--matrix",
        str(COMMITTED_MATRIX),
        "--models",
        "gemma4-12b",
        "--index",
        str(tmp_path / "index"),
    )
    assert main(args) == 0
    assert set(json.loads(capsys.readouterr().out)["systems"]) == {"python-docs", "no-docs"}


def test_air_gapped_run_refuses_an_explicit_online_system(tmp_path: Path) -> None:
    config = _live_config(tmp_path, models=("gemma4-12b",), systems=("python-docs", "docs-fetch"))
    with pytest.raises(BenchmarkValidationError, match="docs-fetch"):
        run_agentic(
            config, launcher=_EveryFamily(), confirm=lambda _: pytest.fail("confirmation reached")
        )
    assert not config.out_dir.exists()


def test_air_gapped_run_refuses_a_hosted_provider_in_a_custom_matrix(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.yml"
    matrix.write_text(COMMITTED_MATRIX.read_text().replace("arm: hosted", "arm: air-gapped"))
    config = _live_config(tmp_path, matrix_path=matrix, models=("gpt-6.1-sol",), dry_run=True)
    with pytest.raises(BenchmarkValidationError, match="Ollama"):
        run_agentic(config, launcher=_EveryFamily())


def test_air_gapped_proxy_never_adds_a_system_upstream() -> None:
    gemma = next(
        f for f in load_model_matrix(COMMITTED_MATRIX).model_families if f.id == "gemma4-12b"
    )
    assert allowlist(gemma, "docs-fetch") == (("host.docker.internal", 11434),)


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


@pytest.mark.parametrize(
    ("kind", "partial"),
    [
        ("transcripts", b'{"tool_calls":'),
        ("discarded", b'{"tool_calls":[null]}'),
        ("transcripts", b"{}"),
    ],
)
def test_partial_record_preserves_abort_and_evidence(
    tmp_path: Path, kind: str, partial: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _local_config(tmp_path, systems=("no-docs", "python-docs"))
    out = config.out_dir
    launcher, _ = _container(tmp_path)
    aborted = OSError("trajectory write interrupted")

    async def interrupted(jobs: Any, **kwargs: Any) -> None:
        for job in jobs:
            _evidence(out, job.job_id, log=_lines(*PROBE_LINES, PYPI_LINE))
        cell = out / kind / "no-docs--scripted-local--r1"
        cell.mkdir(parents=True)
        (cell / "SYN-EX-001.json").write_bytes(partial)
        good = out / "transcripts" / "python-docs--scripted-local--r1"
        good.mkdir(parents=True)
        (good / "SYN-EX-001.json").write_text('{"tool_calls":[{"tool":"lookup_package_docs"}]}')
        raise aborted

    monkeypatch.setattr("benchmarks.agentic.run.run_jobs", interrupted)
    checks, _ = _counting()
    with pytest.raises(OSError) as raised:
        run_agentic(config, launcher=launcher, offline_checks=checks)
    assert raised.value is aborted
    summary = json.loads((out / "run-summary.json").read_text())
    assert (summary["status"], summary["abort_reason"]) == (
        "aborted",
        "OSError: trajectory write interrupted",
    )
    c1 = json.loads((out / "egress" / "summary.json").read_text())
    assert c1["isolation_probe"] == "fail"
    assert c1["jobs"]["scripted-local--no-docs"] == {
        "hosts_allowed": [],
        "hosts_denied": ["pypi.org:443"],
        "lookup_package_docs_attempts": 0,
    }
    assert c1["jobs"]["scripted-local--python-docs"]["lookup_package_docs_attempts"] == 1
    assert json.loads((out / "airgap" / "checks.json").read_text()) == {
        "checks": 1,
        "internet_hosts": [],
        "all_passed": False,
    }


# --- C3 evidence: airgap/checks.json (#199, owner question 9) -----------------------------------

GEMMA = "gemma4-12b--python-docs"


def _egress_summary(out: Path, allowed: list[str], *, probe: str = "pass") -> None:
    jobs = {
        GEMMA: {
            "hosts_allowed": allowed,
            "hosts_denied": ["pypi.org:443"],
            "lookup_package_docs_attempts": 1,
        }
    }
    (out / "egress").mkdir(parents=True, exist_ok=True)
    summary = {"jobs": jobs, "isolation_probe": probe}
    (out / "egress" / "summary.json").write_text(json.dumps(summary))


def test_airgap_evidence_has_exactly_the_bound_keys(tmp_path: Path) -> None:
    _egress_summary(tmp_path, ["host.docker.internal:11434"])
    result = write_airgap_evidence(tmp_path, rounds=4, broken=False, job_ids=[GEMMA])
    written = json.loads((tmp_path / "airgap" / "checks.json").read_text())
    assert written == result and set(written) == {"all_passed", "checks", "internet_hosts"}


def test_a_denied_pypi_attempt_keeps_all_passed_true(tmp_path: Path) -> None:
    _egress_summary(tmp_path, ["host.docker.internal:11434"])
    result = write_airgap_evidence(tmp_path, rounds=4, broken=False, job_ids=[GEMMA])
    assert result == {"all_passed": True, "checks": 4, "internet_hosts": []}


def test_a_reached_internet_host_fails_the_evidence(tmp_path: Path) -> None:
    _egress_summary(tmp_path, ["pypi.org:443", "host.docker.internal:11434"])
    result = write_airgap_evidence(tmp_path, rounds=4, broken=False, job_ids=[GEMMA])
    assert result == {"all_passed": False, "checks": 4, "internet_hosts": ["pypi.org:443"]}


def test_missing_egress_evidence_or_a_failed_probe_fails_the_evidence(tmp_path: Path) -> None:
    result = write_airgap_evidence(tmp_path, rounds=4, broken=False, job_ids=[GEMMA])
    assert result == {"all_passed": False, "checks": 4, "internet_hosts": []}
    _egress_summary(tmp_path / "p", ["host.docker.internal:11434"], probe="fail")
    result = write_airgap_evidence(tmp_path / "p", rounds=4, broken=False, job_ids=[GEMMA])
    assert result["all_passed"] is False
    _egress_summary(tmp_path / "b", ["host.docker.internal:11434"])
    assert write_airgap_evidence(tmp_path / "b", rounds=4, broken=True, job_ids=[GEMMA]) == {
        "all_passed": False,
        "checks": 4,
        "internet_hosts": [],
    }
    _egress_summary(tmp_path / "z", ["host.docker.internal:11434"])
    result = write_airgap_evidence(tmp_path / "z", rounds=0, broken=False, job_ids=[GEMMA])
    assert result["all_passed"] is False  # no round ran: nothing shows the host was offline


def test_an_air_gapped_run_writes_checks_json_even_when_aborted(tmp_path: Path) -> None:
    checks, _ = _counting(fail_on=2)
    with pytest.raises(BenchmarkValidationError):
        run_agentic(_local_config(tmp_path), offline_checks=checks)
    written = json.loads((tmp_path / "run" / "airgap" / "checks.json").read_text())
    assert written == {"all_passed": False, "checks": 2, "internet_hosts": []}
