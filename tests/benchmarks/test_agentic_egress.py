"""Egress policy: allowlists, the Squid config, the proxy log and the C1 evidence (design §7.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.agentic.airgap import write_airgap_evidence
from benchmarks.agentic.egress import (
    EGRESS_URL,
    ProxyEvent,
    allowlist,
    forbidden_env_names,
    lookup_attempts,
    parse_proxy_log,
    squid_conf,
    write_egress_summary,
)
from benchmarks.model_matrix import ModelFamily, load_model_matrix
from benchmarks.runner import BenchmarkValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "agentic"
FAMILIES: dict[str, ModelFamily] = {
    f.id: f
    for path in (REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml", FIX / "model-matrix.yml")
    for f in load_model_matrix(path).model_families
}
TUNNEL = "2026-11-04T10:00:00.123Z 172.20.0.3 CONNECT api.anthropic.com 443 200 TCP_TUNNEL\n"
DENIED = "2026-11-04T10:00:01.004Z 172.20.0.3 CONNECT example.com 443 403 TCP_DENIED\n"


@pytest.mark.parametrize(
    "family_id,system_id,expected",
    [
        ("claude-sonnet-5-5", "python-docs", (("api.anthropic.com", 443),)),
        ("gpt-6.1-sol", "docs-fetch", (("api.openai.com", 443), ("docs.python.org", 443))),
        ("gemma4-12b", "python-docs", (("host.docker.internal", 11434),)),
        ("scripted-anthropic", "no-docs", ()),
    ],
)
def test_allowlist_per_job(
    family_id: str, system_id: str, expected: tuple[tuple[str, int], ...]
) -> None:
    assert allowlist(FAMILIES[family_id], system_id) == expected


def test_parse_proxy_log_reads_tunnels_and_denials() -> None:
    tunnel, denied = parse_proxy_log(TUNNEL + DENIED)
    assert tunnel == ProxyEvent(
        "2026-11-04T10:00:00.123Z",
        "172.20.0.3",
        "CONNECT",
        "api.anthropic.com",
        443,
        200,
        "TCP_TUNNEL",
    )
    assert tunnel.allowed and tunnel.target == "api.anthropic.com:443"
    assert not denied.allowed and denied.target == "example.com:443" and denied.status == 403
    assert parse_proxy_log("") == []
    assert EGRESS_URL == "http://egress:3128"


@pytest.mark.parametrize(
    "text,line",
    [
        (TUNNEL + "garbage\n", 2),
        (TUNNEL + DENIED.rstrip("\n"), 2),  # unterminated: the proxy died mid-write
        ("2026-11-04T10:00:00.123Z 172.20.0.3 CONNECT a.example 4x3 200 TCP_TUNNEL\n", 1),
    ],
)
def test_parse_proxy_log_rejects_a_malformed_or_unterminated_line(text: str, line: int) -> None:
    with pytest.raises(BenchmarkValidationError, match=f"line {line}"):
        parse_proxy_log(text)


def test_forbidden_env_names_match_every_case() -> None:
    names = [
        "HTTPS_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    ]
    environ = {name: "user:secret@x" for name in names} | {"PATH": "/bin", "PROXY_NOTE": "x"}
    assert forbidden_env_names(environ) == sorted(names)
    assert forbidden_env_names({"PATH": "/bin", "PROXY_NOTE": "x"}) == []


SQUID_TEMPLATE = """\
http_port 3128
visible_hostname egress
pid_filename none
cache deny all
shutdown_lifetime 1 seconds
httpd_suppress_version_string on
logfile_rotate 0
logformat bench %{%Y-%m-%dT%H:%M:%S}tg.%03tuZ %>a %rm %>rd %>rP %>Hs %Ss %Sh %<Hs
access_log stdio:/var/log/squid-bench/access.log logformat=bench rotate=0
cache_log /var/log/squid-bench/cache.log
acl allow_1_host dstdomain -n api.anthropic.com
acl allow_1_port port 443
http_access allow CONNECT allow_1_host allow_1_port
acl allow_2_host dstdomain -n host.docker.internal
acl allow_2_port port 11434
http_access allow !CONNECT allow_2_host allow_2_port
http_access deny all
"""


def test_squid_conf_matches_the_template() -> None:
    conf = squid_conf([("api.anthropic.com", 443), ("host.docker.internal", 11434)])
    assert conf == SQUID_TEMPLATE


def test_squid_conf_with_no_host_denies_everything() -> None:
    conf = squid_conf([])
    assert "http_access allow" not in conf and "acl " not in conf
    assert conf.endswith("http_access deny all\n")


def test_every_allowlist_acl_disables_lookups() -> None:
    conf = squid_conf([("api.openai.com", 443), ("docs.python.org", 443), ("h.invalid", 8080)])
    acls = [line.split() for line in conf.splitlines() if line.startswith("acl ")]
    domains = [acl for acl in acls if acl[2] == "dstdomain"]
    assert len(domains) == 3 and all(acl[3] == "-n" for acl in domains)
    assert not [acl for acl in acls if acl[2] in ("dst", "dstdom_regex", "dst_as")]
    (access_log,) = [line for line in conf.splitlines() if line.startswith("access_log ")]
    assert access_log.split()[1].startswith("stdio:/var/log/squid-bench/")
    assert "rotate=0" in access_log.split() and "logfile_rotate 0" in conf.splitlines()


# --- C1 evidence: egress/summary.json (#199, owner question 9) ----------------------------------

PROBE_LINES = [
    "2026-11-04T10:00:00.001Z 172.30.0.2 CONNECT example.com 443 403 TCP_DENIED",
    "2026-11-04T10:00:00.002Z 172.30.0.2 GET host.docker.internal 50000 403 TCP_DENIED",
]
ANTHROPIC_LINE = "2026-11-04T10:00:05.000Z 172.30.0.3 CONNECT api.anthropic.com 443 200 TCP_TUNNEL"
PYPI_LINE = "2026-11-04T10:00:06.000Z 172.30.0.3 CONNECT pypi.org 443 403 TCP_DENIED"
CLAUDE = "claude-sonnet-5-5--python-docs"
SCRIPTED = "scripted-anthropic--no-docs"


def _jobs() -> list[Any]:
    from tests.benchmarks.test_agentic_sandbox import _job

    return [_job("claude-sonnet-5-5", "python-docs"), _job("scripted-anthropic", "no-docs")]


def _evidence(
    out: Path,
    job_id: str,
    *,
    log: str | None,
    prefix: list[str] = PROBE_LINES,
    probe_passed: bool = True,
    ok: bool = True,
) -> None:
    log_dir = out / "egress" / job_id
    log_dir.mkdir(parents=True, exist_ok=True)
    if log is not None:
        (log_dir / "access.log").write_text(log, encoding="utf-8")
    (log_dir / "collected.json").write_text(json.dumps({"ok": ok, "problems": []}))
    (out / "probe").mkdir(exist_ok=True)
    probe = {"passed": probe_passed, "failed": [], "proxy_prefix": prefix}
    (out / "probe" / f"{job_id}.json").write_text(json.dumps(probe))
    (out / "probe" / "control.json").write_text(json.dumps({"passed": True}))


def _lines(*lines: str) -> str:
    return "".join(f"{line}\n" for line in lines)


def _ok_settle(job_id: str | None, unit: str = "anthropic-1") -> list[dict[str, Any]]:
    return [
        {"event": "reserve", "unit_id": unit, "provider": "anthropic", "job_id": job_id},
        {"event": "settle", "unit_id": unit, "provider": "anthropic", "outcome": "ok"},
    ]


def _clean_run(tmp_path: Path) -> Path:
    out = tmp_path / "run"
    _evidence(out, CLAUDE, log=_lines(*PROBE_LINES, ANTHROPIC_LINE, PYPI_LINE))
    _evidence(out, SCRIPTED, log=_lines(*PROBE_LINES))
    return out


def test_egress_summary_has_exactly_the_bound_keys(tmp_path: Path) -> None:
    out = _clean_run(tmp_path)
    summary = write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))
    written = json.loads((out / "egress" / "summary.json").read_text())
    assert written == summary
    assert set(written) == {"jobs", "isolation_probe"}
    assert set(written["jobs"]) == {CLAUDE, SCRIPTED}
    assert all(
        set(j) == {"hosts_allowed", "hosts_denied", "lookup_package_docs_attempts"}
        for j in written["jobs"].values()
    )
    assert written["isolation_probe"] == "pass"


def test_probe_lines_never_reach_the_summary(tmp_path: Path) -> None:
    out = _clean_run(tmp_path)
    jobs = write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["jobs"]
    assert jobs[CLAUDE]["hosts_allowed"] == ["api.anthropic.com:443"]
    assert jobs[CLAUDE]["hosts_denied"] == ["pypi.org:443"]
    assert jobs[SCRIPTED] == {
        "hosts_allowed": [],
        "hosts_denied": [],
        "lookup_package_docs_attempts": 0,
    }


@pytest.mark.parametrize(
    "log",
    [
        _lines(*PROBE_LINES, ANTHROPIC_LINE) + PYPI_LINE,  # unterminated last line
        _lines(PYPI_LINE, *PROBE_LINES, ANTHROPIC_LINE),  # a line before the probe's prefix
        _lines(PROBE_LINES[1], ANTHROPIC_LINE),  # the prefix lost a line
    ],
)
def test_a_truncated_or_reprefixed_log_invalidates_the_evidence(tmp_path: Path, log: str) -> None:
    out = _clean_run(tmp_path)
    (out / "egress" / CLAUDE / "access.log").write_text(log, encoding="utf-8")
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"


def test_a_missing_log_invalidates_the_evidence(tmp_path: Path) -> None:
    out = _clean_run(tmp_path)
    (out / "egress" / SCRIPTED / "access.log").unlink()
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"
    out = _clean_run(tmp_path / "linked")
    access = out / "egress" / SCRIPTED / "access.log"
    access.unlink()
    access.symlink_to(out / "egress" / CLAUDE / "access.log")  # a link is never followed
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"


async def test_c1_refuses_a_link_to_a_complete_job_directory(tmp_path: Path) -> None:
    from tests.benchmarks.test_agentic_sandbox import FakeDocker, _opened

    sandbox, egress = await _opened(tmp_path, FakeDocker(), CLAUDE)
    out = sandbox.config.out_dir
    _evidence(out, CLAUDE, log=_lines(*PROBE_LINES, ANTHROPIC_LINE))
    _evidence(out, SCRIPTED, log=_lines(*PROBE_LINES))
    assert await sandbox.close_egress(egress) == []
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "pass"
    job_dir = out / "egress" / CLAUDE
    moved = job_dir.rename(tmp_path / "copied-logs")
    job_dir.symlink_to(moved)
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"


@pytest.mark.parametrize("status", ["503 TCP_MISS", "204 TCP_TUNNEL"])
def test_failed_provider_connect_is_not_evidence_of_a_billed_connection(
    tmp_path: Path, status: str
) -> None:
    out = _clean_run(tmp_path)
    failed = ANTHROPIC_LINE.replace("200 TCP_TUNNEL", status)
    (out / "egress" / CLAUDE / "access.log").write_text(_lines(*PROBE_LINES, failed, PYPI_LINE))
    summary = write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))
    assert summary["isolation_probe"] == "fail"
    assert summary["jobs"][CLAUDE]["hosts_allowed"] == []
    assert summary["jobs"][CLAUDE]["hosts_denied"] == ["pypi.org:443"]


@pytest.mark.parametrize(
    "attempt,isolated",
    [
        ("CONNECT docs.python.org 443 503 NONE_NONE", False),
        ("POST host.docker.internal 11434 503 TCP_MISS", True),
        ("POST host.docker.internal 11434 0 TCP_MISS_ABORTED", True),
        ("POST host.docker.internal 11434 200 TCP_MISS_ABORTED", True),
        ("POST host.docker.internal 11434 302 TCP_MISS_TIMEDOUT_ABORTED", True),
        ("POST external.invalid 11434 200 TCP_MISS_ABORTED", False),
    ],
)
@pytest.mark.parametrize("successful_retry", [False, True])
def test_legacy_failed_attempts_only_preserve_isolation_for_the_permitted_target(
    tmp_path: Path, attempt: str, isolated: bool, successful_retry: bool
) -> None:
    from tests.benchmarks.test_agentic_sandbox import _job

    job = _job("gemma4-12b", "python-docs")
    ollama = "2026-11-04T10:00:04.001Z 172.30.0.3 POST host.docker.internal 11434 200 TCP_MISS"
    failed = f"2026-11-04T10:00:05.000Z 172.30.0.3 {attempt}"
    retry = [ollama] if successful_retry else []
    _evidence(tmp_path, job.job_id, log=_lines(*PROBE_LINES, failed, *retry))
    summary = write_egress_summary(tmp_path, [job], [])
    checks = write_airgap_evidence(tmp_path, rounds=1, broken=False, job_ids=[job.job_id])
    assert checks == {"all_passed": isolated, "checks": 1, "internet_hosts": []}
    assert summary["isolation_probe"] == ("pass" if isolated else "fail")
    assert summary["jobs"][job.job_id]["hosts_allowed"] == (
        ["host.docker.internal:11434"] if successful_retry else []
    )
    assert summary["jobs"][job.job_id]["hosts_denied"] == []


# These fields come from the pinned Squid 6.14 proxy, including "-" for no peer reply.
CURRENT_PREFIX = [line + " HIER_NONE -" for line in PROBE_LINES]
LOCAL = "host.docker.internal:11434"


@pytest.mark.parametrize(
    "attempt,passed,reached,denied",
    [
        ("POST host.docker.internal 11434 500 TCP_MISS HIER_DIRECT 500", True, [LOCAL], []),
        ("POST host.docker.internal 11434 500 TCP_MISS_ABORTED HIER_DIRECT 500", True, [LOCAL], []),
        ("POST host.docker.internal 11434 0 TCP_MISS_ABORTED HIER_DIRECT -", True, [], []),
        ("POST host.docker.internal 11434 0 TCP_MISS_TIMEDOUT_ABORTED HIER_DIRECT -", True, [], []),
        ("POST host.docker.internal 11434 503 TCP_MISS_ABORTED HIER_DIRECT -", True, [], []),
        ("POST host.docker.internal 11434 503 TCP_MISS HIER_NONE -", True, [], []),
        ("CONNECT pypi.org 443 403 TCP_DENIED_ABORTED HIER_NONE -", True, [], ["pypi.org:443"]),
        ("POST outside.invalid 11434 0 TCP_MISS_ABORTED HIER_DIRECT -", False, [], []),
        (
            "POST outside.invalid 11434 500 TCP_MISS HIER_DIRECT 500",
            False,
            ["outside.invalid:11434"],
            [],
        ),
        ("CONNECT outside.invalid 443 200 TCP_DENIED HIER_NONE -", False, [], []),
        ("CONNECT outside.invalid 443 403 TCP_DENIED_BOGUS HIER_NONE -", False, [], []),
        ("CONNECT outside.invalid 443 403 TCP_DENIED_REPLY HIER_DIRECT 403", False, [], []),
        ("POST host.docker.internal 11434 0 TCP_MISS_BOGUS HIER_DIRECT -", False, [], []),
        ("POST host.docker.internal 11434 500 TCP_MISS UNKNOWN 500", False, [], []),
        ("POST host.docker.internal 11434 500 TCP_MISS HIER_NONE 500", False, [], []),
        ("POST host.docker.internal 11434 500 TCP_MISS HIER_DIRECT garbage", False, [], []),
        ("POST host.docker.internal 11434 500 TCP_MISS HIER_DIRECT", False, [], []),
        ("POST host.docker.internal 11434 200 TCP_MISS", False, [], []),  # format changed mid-log
    ],
)
def test_airgap_query_evidence(
    tmp_path: Path, attempt: str, passed: bool, reached: list[str], denied: list[str]
) -> None:
    from tests.benchmarks.test_agentic_sandbox import _job

    job = _job("gemma4-12b", "python-docs")
    query = f"2026-11-04T10:00:05.000Z 172.30.0.3 {attempt}"
    _evidence(tmp_path, job.job_id, prefix=CURRENT_PREFIX, log=_lines(*CURRENT_PREFIX, query))
    summary = write_egress_summary(tmp_path, [job], [])
    entry = summary["jobs"][job.job_id]
    assert summary["isolation_probe"] == ("pass" if passed else "fail")
    assert entry["hosts_allowed"] == reached
    assert entry["hosts_denied"] == denied
    assert write_airgap_evidence(tmp_path, rounds=1, broken=False, job_ids=[job.job_id]) == {
        "all_passed": passed,
        "checks": 1,
        "internet_hosts": [host for host in reached if host != LOCAL],
    }


@pytest.mark.parametrize(
    "reply,passed",
    [
        ("200 TCP_TUNNEL HIER_DIRECT -", True),
        ("200 TCP_TUNNEL_ABORTED HIER_DIRECT -", True),
        ("503 TCP_MISS_ABORTED HIER_DIRECT -", False),
        ("503 NONE_NONE HIER_NONE -", False),
        ("200 TCP_TUNNEL HIER_NONE -", False),
        ("204 TCP_TUNNEL HIER_DIRECT -", False),
    ],
)
def test_billing_needs_an_established_provider_tunnel(
    tmp_path: Path, reply: str, passed: bool
) -> None:
    query = f"2026-11-04T10:00:05.000Z 172.30.0.3 CONNECT api.anthropic.com 443 {reply}"
    _evidence(tmp_path, CLAUDE, prefix=CURRENT_PREFIX, log=_lines(*CURRENT_PREFIX, query))
    summary = write_egress_summary(tmp_path, [_jobs()[0]], _ok_settle(CLAUDE))
    assert summary["isolation_probe"] == ("pass" if passed else "fail")
    assert summary["jobs"][CLAUDE]["hosts_allowed"] == (["api.anthropic.com:443"] if passed else [])


def test_an_empty_probe_prefix_is_not_evidence(tmp_path: Path) -> None:
    _evidence(tmp_path, SCRIPTED, prefix=[], log="")
    assert write_egress_summary(tmp_path, [_jobs()[1]], [])["isolation_probe"] == "fail"


@pytest.mark.parametrize("record", ["control", SCRIPTED])
def test_a_non_object_probe_fails_the_evidence(tmp_path: Path, record: str) -> None:
    out = _clean_run(tmp_path)
    (out / "probe" / f"{record}.json").write_text("[]")
    assert write_egress_summary(out, _jobs(), [])["isolation_probe"] == "fail"


async def test_no_false_clean_when_collection_failed_or_the_provider_never_appears(
    tmp_path: Path,
) -> None:
    from tests.benchmarks.test_agentic_sandbox import RUNNING, FakeDocker, _sandbox

    # (a) A forced kill after our stop: the log is well formed but may be partial.
    killed = {"Running": False, "ExitCode": 137, "OOMKilled": False}
    sandbox = _sandbox(tmp_path, FakeDocker(states=(RUNNING, killed)))
    egress = await sandbox.open_egress(CLAUDE, (("api.anthropic.com", 443),))
    (egress.log_dir / "access.log").write_text(_lines(*PROBE_LINES, ANTHROPIC_LINE))
    (egress.log_dir / "cache.log").write_text("squid\n")
    await sandbox.close_egress(egress)
    out = tmp_path / "run"
    _evidence(out, SCRIPTED, log=_lines(*PROBE_LINES))
    probe = {"passed": True, "proxy_prefix": PROBE_LINES}
    (out / "probe" / f"{CLAUDE}.json").write_text(json.dumps(probe))
    assert json.loads((egress.log_dir / "collected.json").read_text())["ok"] is False
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"
    # (b) A hosted job billed an ok call, but its log shows no provider host.
    out = tmp_path / "b" / "run"
    _evidence(out, CLAUDE, log=_lines(*PROBE_LINES, PYPI_LINE))
    _evidence(out, SCRIPTED, log=_lines(*PROBE_LINES))
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"
    assert write_egress_summary(out, _jobs(), [])["isolation_probe"] == "pass"  # nothing billed


def test_an_ok_settle_from_a_worker_that_died_early_fails_the_evidence(tmp_path: Path) -> None:
    # No transcript or discarded record for the job: the ledger alone attributes the settle.
    out = tmp_path / "run"
    _evidence(out, CLAUDE, log=_lines(*PROBE_LINES))
    _evidence(out, SCRIPTED, log=_lines(*PROBE_LINES))
    assert not (out / "transcripts").exists()
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"


@pytest.mark.parametrize("job_id", [None, "gpt-6.1-sol--python-docs"])
def test_an_unattributed_ok_settle_fails_the_evidence(tmp_path: Path, job_id: str | None) -> None:
    out = _clean_run(tmp_path)
    events = _ok_settle(CLAUDE) + _ok_settle(job_id, unit="anthropic-2")
    assert write_egress_summary(out, _jobs(), events)["isolation_probe"] == "fail"


def test_a_failed_probe_or_control_fails_the_evidence(tmp_path: Path) -> None:
    out = _clean_run(tmp_path)
    (out / "probe" / "control.json").write_text(json.dumps({"passed": False}))
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"
    out = _clean_run(tmp_path / "p")
    (out / "probe" / f"{SCRIPTED}.json").unlink()
    assert write_egress_summary(out, _jobs(), _ok_settle(CLAUDE))["isolation_probe"] == "fail"


def _record(path: Path, tools: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tool_calls": [{"tool": tool} for tool in tools]}))


def test_lookup_attempts_count_scored_and_discarded_records(tmp_path: Path) -> None:
    out = tmp_path / "run"
    lookup = "lookup_package_docs"
    _record(out / "transcripts" / "python-docs--m--r1" / "EX-001.json", [lookup, "x", lookup])
    _record(out / "discarded" / "python-docs--m--r2" / "EX-002--attempt1.json", [lookup])
    _record(out / "transcripts" / "docs-fetch--m--r1" / "EX-001.json", [lookup])
    _record(out / "transcripts" / "python-docs--m-b--r1" / "EX-001.json", [lookup])
    assert lookup_attempts(out, "m", "python-docs") == 3
    assert lookup_attempts(out, "m", "no-docs") == 0


@pytest.mark.parametrize(
    "prefix",
    ["- -", "2026-02-31T10:00:00.123Z 172.30.0.3", "2026-11-04T10:00:00.123Z invalid-client"],
)
def test_malformed_log_identity_cannot_corroborate_a_billed_call(
    tmp_path: Path, prefix: str
) -> None:
    line = f"{prefix} CONNECT api.anthropic.com 443 200 TCP_TUNNEL HIER_DIRECT -"
    probe = [f"{event} HIER_NONE -" for event in PROBE_LINES]
    _evidence(tmp_path, CLAUDE, log=_lines(*probe, line), prefix=probe)
    summary = write_egress_summary(tmp_path, [_jobs()[0]], _ok_settle(CLAUDE))
    assert summary["isolation_probe"] == "fail"
    assert summary["jobs"][CLAUDE]["hosts_allowed"] == []
