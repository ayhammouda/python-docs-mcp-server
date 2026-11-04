"""Systems manifest and MCP stdio bridge (design §5, §6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from benchmarks.adapters.guard import PAID_KEY_ENVS
from benchmarks.agentic.bridge import (
    TRUNCATION_MARKER,
    BridgeCrash,
    SystemSpec,
    canonical_text,
    load_systems,
    open_bridge,
    server_env,
    system_spec_from_raw,
    truncate_utf8,
)
from benchmarks.agentic.protocol import KeyLeakError
from tests.test_mcp_get_docs_cache_smoke import _create_contentful_json_index
from tests.test_stdio_smoke import _isolated_cache_env

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTIC_MANIFEST_PATH = REPO_ROOT / "docs" / "benchmarks" / "agentic-manifest.yml"
FAKE_SERVER = Path(__file__).parent / "fixtures" / "agentic" / "fake_fetch_server.py"


def _fake(env: dict[str, str] | None = None, pass_env: list[str] | None = None) -> SystemSpec:
    return system_spec_from_raw(
        {
            "id": "fake-fetch",
            "adapter": "mcp-stdio",
            "command": ["{python}", str(FAKE_SERVER), "--edge"],
            "env": env or {},
            "pass_env": pass_env or [],
        }
    )


@pytest.fixture
def python_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SystemSpec:
    env, cache_dir = _isolated_cache_env(tmp_path)
    _create_contentful_json_index(cache_dir)
    monkeypatch.setenv("XDG_CACHE_HOME", env["XDG_CACHE_HOME"])
    monkeypatch.setenv("HOME", env["HOME"])
    return {s.id: s for s in load_systems(AGENTIC_MANIFEST_PATH)}["python-docs"]


def test_canonical_text_prefers_compact_structured_content() -> None:
    r = CallToolResult(
        content=[TextContent(type="text", text="ignored")],
        structured_content={"a": "é", "b": [1, 2]},
        is_error=False,
    )
    assert canonical_text(r) == '{"a":"é","b":[1,2]}'


def test_structured_tool_result_refuses_a_key_before_converting_to_text() -> None:
    key = "AIza" + "g" * 35
    result = CallToolResult(content=[], structured_content={"extra": [{"\n" + key: True}]})
    with pytest.raises(KeyLeakError) as caught:
        canonical_text(result)
    assert key not in str(caught.value)


def test_canonical_text_joins_text_blocks_and_marks_other_blocks() -> None:
    r = CallToolResult(
        content=[
            TextContent(type="text", text="one"),
            ImageContent(type="image", data="aGk=", mime_type="image/png"),
            TextContent(type="text", text="two"),
        ],
        structured_content=None,
        is_error=False,
    )
    assert canonical_text(r) == "one\n[image content omitted]\ntwo"


def test_truncate_utf8_cuts_on_a_character_boundary_and_marks_it() -> None:
    text = "a" * 99_999 + "é" + "tail"  # "é" is 2 bytes and straddles byte 100,000
    marker = TRUNCATION_MARKER.format(limit=100_000)
    assert truncate_utf8(text, 100_000) == ("a" * 99_999 + marker, True)
    assert "truncated at 100000 UTF-8 bytes" in marker


def test_truncate_utf8_leaves_a_result_at_the_limit_untouched() -> None:
    assert truncate_utf8("a" * 100_000, 100_000) == ("a" * 100_000, False)


def test_load_systems_reads_the_committed_manifest() -> None:
    systems = {s.id: s for s in load_systems(AGENTIC_MANIFEST_PATH)}
    assert set(systems) == {"python-docs", "docs-fetch", "no-docs"}
    assert systems["python-docs"].command == ("{python}", "-m", "mcp_server_python_docs", "serve")
    assert systems["python-docs"].env == {
        "PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX": "1",
        "HTTPS_PROXY": "{egress}",
        "HTTP_PROXY": "{egress}",
    }
    assert systems["python-docs"].pass_env == ("HOME", "XDG_CACHE_HOME")
    assert systems["docs-fetch"].command == ("mcp-server-fetch",)
    assert systems["docs-fetch"].env == {"HTTPS_PROXY": "{egress}", "HTTP_PROXY": "{egress}"}
    assert systems["docs-fetch"].pass_env == ("HOME",)
    assert systems["no-docs"].transport == "none"
    assert all(system_spec_from_raw(s.raw) == s for s in systems.values())


@pytest.mark.parametrize(
    "raw",
    [
        {"id": "x", "adapter": "mcp-http"},
        {"id": "x", "adapter": "mcp-stdio", "command": []},
        {"id": "x", "adapter": "mcp-stdio", "command": ["a"], "env": {"A": 1}},
        {"id": "x/y", "adapter": "none"},
    ],
)
def test_system_spec_from_raw_refuses_a_bad_entry(raw: dict) -> None:
    from benchmarks.runner import BenchmarkValidationError

    with pytest.raises(BenchmarkValidationError):
        system_spec_from_raw(raw)


_KEY_SHAPE = "sk-ant-api03-" + "A" * 90


@pytest.mark.parametrize(
    "env,pass_env,match",
    [
        ({}, ["HOME", "ANTHROPIC_API_KEY"], "ANTHROPIC_API_KEY"),
        ({}, ["CONTEXT7_API_KEY"], "CONTEXT7_API_KEY"),
        ({"OPENAI_API_KEY": "x"}, [], "OPENAI_API_KEY"),
        ({"REF_API_KEY": "x"}, [], "REF_API_KEY"),
        ({"TOKEN": _KEY_SHAPE}, [], "key-shaped"),
    ],
)
def test_a_system_never_receives_a_provider_key(
    env: dict[str, str], pass_env: list[str], match: str
) -> None:
    from benchmarks.runner import BenchmarkValidationError

    raw = {"id": "x", "adapter": "mcp-stdio", "command": ["a"], "env": env, "pass_env": pass_env}
    with pytest.raises(BenchmarkValidationError, match=match) as caught:
        system_spec_from_raw(raw)
    assert _KEY_SHAPE not in str(caught.value)


@pytest.mark.parametrize("system_id", ["python-docs", "ref-tools", "context7"])
@pytest.mark.parametrize("name", PAID_KEY_ENVS)
@pytest.mark.parametrize("through", ["env", "pass_env"])
def test_a_paid_key_reaches_no_system(system_id: str, name: str, through: str) -> None:
    from benchmarks.runner import BenchmarkValidationError

    raw = {"id": system_id, "adapter": "mcp-stdio", "command": ["a"], "env": {}, "pass_env": []}
    raw[through] = {name: "x"} if through == "env" else [name]
    with pytest.raises(BenchmarkValidationError, match=name):
        system_spec_from_raw(raw)


@pytest.mark.parametrize(
    "system_id,name,allowed",
    [
        ("ref-tools", "REF_API_KEY", True),
        ("python-docs", "REF_API_KEY", False),
        ("context7", "CONTEXT7_API_KEY", True),
        ("ref-tools", "CONTEXT7_API_KEY", False),
    ],
)
def test_a_competitor_key_reaches_only_its_own_system(
    system_id: str, name: str, allowed: bool
) -> None:
    from benchmarks.runner import BenchmarkValidationError

    raw = {"id": system_id, "adapter": "mcp-stdio", "command": ["a"], "pass_env": [name]}
    if allowed:
        assert system_spec_from_raw(raw).pass_env == (name,)
    else:
        with pytest.raises(BenchmarkValidationError, match=name):
            system_spec_from_raw(raw)


async def test_bridge_lists_the_real_server_tools_unfiltered(python_docs: SystemSpec) -> None:
    async with open_bridge(python_docs) as bridge:
        assert {t.name for t in bridge.tools} == {
            "search_docs",
            "get_docs",
            "lookup_package_docs",
            "list_versions",
            "detect_python_version",
            "compare_versions",
            "whatsnew_for_version",
        }
        assert len(bridge.tools_raw) == 7 and "name" in bridge.server_info
        assert all("inputSchema" in t for t in bridge.tools_raw)
        outcome = await bridge.call("get_docs", {"slug": "library/json.html", "version": "3.13"})
        assert outcome.is_error is False and "json module" in outcome.text


async def test_bridge_returns_an_unknown_tool_as_an_error_result(python_docs: SystemSpec) -> None:
    async with open_bridge(python_docs) as bridge:
        outcome = await bridge.call("no_such_tool", {})
        assert outcome.is_error is True and "no_such_tool" in outcome.text


async def test_bridge_returns_invalid_arguments_as_an_error_result(
    python_docs: SystemSpec,
) -> None:
    async with open_bridge(python_docs) as bridge:
        assert (await bridge.call("search_docs", {"query": 5})).is_error is True


async def test_bridge_raises_bridge_crash_when_the_server_dies() -> None:
    async with open_bridge(_fake()) as bridge:
        with pytest.raises(BridgeCrash):
            await bridge.call("die", {})


async def test_server_env_holds_only_the_listed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    async with open_bridge(_fake(env={"MARKER": "1"}, pass_env=["HOME"])) as bridge:
        text = (await bridge.call("env_names", {})).text
    assert '"MARKER"' in text and '"HOME"' in text
    assert "ANTHROPIC_API_KEY" not in text


def _committed(system_id: str) -> SystemSpec:
    return {s.id: s for s in load_systems(AGENTIC_MANIFEST_PATH)}[system_id]


@pytest.mark.parametrize("system_id", ["python-docs", "docs-fetch"])
def test_server_env_points_both_proxy_variables_at_the_egress(system_id: str) -> None:
    env = server_env(_committed(system_id), {"BENCHMARK_EGRESS_URL": "http://egress:3128"})
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == "http://egress:3128"
    if system_id == "python-docs":
        assert env["PYTHON_DOCS_MCP_DISABLE_AUTO_INDEX"] == "1"


@pytest.mark.parametrize("system_id", ["python-docs", "docs-fetch"])
def test_server_env_drops_the_proxy_variables_without_an_egress(system_id: str) -> None:
    env = server_env(_committed(system_id), {"HOME": "/tmp"})
    assert "HTTPS_PROXY" not in env and "HTTP_PROXY" not in env
    assert "{egress}" not in env.values() and env["HOME"] == "/tmp"


def test_no_worker_proxy_variable_reaches_an_mcp_server() -> None:
    environ = {"HTTPS_PROXY": "socks5://x:1", "ALL_PROXY": "socks5://x:1", "HOME": "/tmp"}
    env = server_env(_committed("docs-fetch"), environ)
    assert not [name for name in env if name.upper().endswith("_PROXY")]
