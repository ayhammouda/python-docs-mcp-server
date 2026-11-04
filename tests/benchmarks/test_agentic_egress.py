"""Egress policy: allowlists, the Squid config, the proxy log and the C1 evidence (design §7.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.agentic.egress import (
    EGRESS_URL,
    ProxyEvent,
    allowlist,
    forbidden_env_names,
    parse_proxy_log,
    squid_conf,
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
        "2026-11-04T10:00:00.123Z", "172.20.0.3", "CONNECT", "api.anthropic.com", 443, 200,
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
logformat bench %{%Y-%m-%dT%H:%M:%S}tg.%03tuZ %>a %rm %>rd %>rP %>Hs %Ss
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
