"""Real registered coordinates with offline SDK counts and receipt joins."""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx2
import pytest

from benchmarks.agentic.clients import anthropic_sdk
from benchmarks.agentic.count import (
    AnthropicCountClient,
    count_item,
    count_item_from_transcript,
)
from benchmarks.agentic.count_evidence import is_count_coordinate
from benchmarks.agentic.schemas import schemas_sha256
from benchmarks.report_measurements import CountCapture, _identifier, live_count_matches
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.accepted_report_fixture import write

REAL_FAMILIES = [("gpt-6.1-sol", "openai"), ("gemini-3.8-flash", "google")]
LEGACY_IDS = ["legacy-family", "legacy_family", "-legacy", "_legacy", "-", "_"]
UNSAFE_IDS = [
    "",
    ".",
    "..",
    "...",
    ".hidden",
    "../escape",
    "a/../b",
    "a/b",
    "/absolute",
    "a\\b",
    "C:\\absolute",
    "a\n",
    "a b",
    "a%2fb",
    "a\x00b",
    "é",
    "a" * 241,
    None,
    1,
    True,
]


@pytest.mark.parametrize(
    "value",
    [
        *[f for f, _ in REAL_FAMILIES],
        "python-docs--gpt-6.1-sol--r1",
        "python-docs--gemini-3.8-flash--r1",
        "Q.1",
        "v..1",
        "a.",
        "a" * 240,
        "a" * 238 + ".1",
        *LEGACY_IDS,
    ],
)
def test_shared_coordinate_grammar_accepts_registered_and_legacy_values(value: str) -> None:
    assert is_count_coordinate(value)


@pytest.mark.parametrize("value", UNSAFE_IDS)
def test_shared_coordinate_grammar_rejects_unsafe_values(value: Any) -> None:
    assert not is_count_coordinate(value)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("coordinate validation attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


async def recorded_bundle(
    root: Path, family: str, provider: str, *, system: str = "python-docs", qid: str = "Q.1"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Synthetic consistency evidence; MockTransport never contacts a provider."""
    cell = f"{system}--{family}--r1"
    arguments = {"query": "json.dumps"}
    raw = (
        {"output": [{"type": "function_call", "arguments": json.dumps(arguments)}]}
        if provider == "openai"
        else {
            "candidates": [
                {
                    "content": {
                        "parts": [{"function_call": {"name": "search_docs", "args": arguments}}]
                    }
                }
            ]
        }
    )
    tools = [{"name": "search_docs", "description": "Search", "inputSchema": {"type": "object"}}]
    prompt = "synthetic coordinate test"
    transcript = {
        "competitor_id": cell,
        "corpus_id": qid,
        "model_family_id": family,
        "system_id": system,
        "provider": provider,
        "repeat": 1,
        "status": "succeeded",
        "tool_model_key": f"{system}:{provider}/{family}",
        "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "tools_sha256": schemas_sha256(
            [
                {
                    "name": "search_docs",
                    "description": "Search",
                    "input_schema": {"type": "object"},
                }
            ]
        ),
        "canonical_messages": [
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "search_docs",
                        "id": "provider-id",
                        "input": arguments,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "provider-id",
                        "content": "documentation",
                    }
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "answer"}]},
        ],
        "model_calls": [
            {"message_count": 1, "tool_calls_requested": 1, "raw_response": raw},
            {"message_count": 3, "tool_calls_requested": 0, "raw_response": {}},
        ],
    }
    tb = write(root / "transcripts" / cell / f"{qid}.json", transcript)
    sb = write(root / "systems" / f"{family}--{system}.json", {"tools_raw": tools})
    write(
        root / "scoring" / cell / f"{qid}.json",
        {k: transcript[k] for k in ("competitor_id", "corpus_id", "tool_model_key")},
    )
    (root / "snapshots").mkdir(exist_ok=True)
    (root / "snapshots/system-prompt.txt").write_text(prompt)
    item = count_item_from_transcript(transcript, system_prompt=prompt, tools_raw=tools)
    assert item.messages[1]["content"][0]["input"] == arguments
    assert item.messages[1]["content"][0]["id"] == "c1_1"
    assert item.messages[2]["content"][0]["tool_use_id"] == "c1_1"
    requests = []

    def record(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages/count_tokens"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={"input_tokens": 123})

    sdk = anthropic_sdk("test-key", 5, transport=httpx2.MockTransport(record))
    try:
        token = await count_item(AnthropicCountClient(sdk, "claude-sonnet-5-5"), item)
    finally:
        await sdk.close()
    assert token["status"] == "counted"
    assert len(requests) == token["count_calls"] == 8
    assert all(r["model"] == "claude-sonnet-5-5" for r in requests)
    frozen = {
        "item": asdict(item),
        "transcript_sha256": hashlib.sha256(tb).hexdigest(),
        "system_sha256": hashlib.sha256(sb).hexdigest(),
    }
    frozen_body = json.dumps(frozen, indent=2, sort_keys=True).encode() + b"\n"
    token.update(count_run_id="count-local", input_sha256=hashlib.sha256(frozen_body).hexdigest())
    write(
        root / "grading/count-inputs.json",
        {
            hashlib.sha256(f"{cell}/{qid}".encode()).hexdigest(): frozen,
        },
    )
    relative = f"{cell}/{qid}.json"
    body = write(root / "tokens" / relative, token)
    write(root / "grading/counts/count-local" / relative, token)
    write(
        root / "grading/count-runs/count-local.json",
        {
            "count_run_id": "count-local",
            "status": "completed",
            "dry_run": False,
            "units": [],
            "not_run": [],
            "worker_errors": [],
            "records_written": 1,
            "items": 1,
            "count_calls": token["count_calls"],
            "results": {relative: hashlib.sha256(body).hexdigest()},
        },
    )
    return transcript, token


@pytest.mark.parametrize("family,provider", REAL_FAMILIES)
async def test_real_family_counts_join_receipts(tmp_path: Path, family: str, provider: str) -> None:
    transcript, token = await recorded_bundle(tmp_path, family, provider)
    assert live_count_matches(tmp_path, transcript, token)


@pytest.mark.parametrize("family,provider", REAL_FAMILIES)
async def test_c2_admission_requires_hosted_receipts_even_for_a_technical_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str, provider: str,
) -> None:
    import benchmarks.report_measurements as measurements

    transcript, token = await recorded_bundle(tmp_path, family, provider, system="docs-fetch")
    key = transcript["competitor_id"], transcript["corpus_id"]
    reads: dict[Path, int] = {}
    reader = measurements.artifact_bytes

    def counted_reader(root: Path, path: Path) -> bytes:
        reads[path] = reads.get(path, 0) + 1
        return reader(root, path)

    monkeypatch.setattr(measurements, "artifact_bytes", counted_reader)
    capture = CountCapture(tmp_path)
    for _ in range(3):
        assert capture.admitted_counts({key: transcript}, {key: token}) == {
            key: token["client_wrapped_tokens"],
        }
    assert reads and max(reads.values()) == 1
    assert capture.admitted_counts(
        {key: transcript}, {key: {**token, "client_wrapped_tokens": 1}},
    ) == {key: None}
    assert max(reads.values()) == 1  # Conflicting projections cannot reopen proof.
    (tmp_path / "grading/count-runs/count-local.json").unlink()
    # A fresh operation must recapture changed files. A scripted answering
    # model cannot legalize a hosted counter's missing proof.
    assert CountCapture(tmp_path).admitted_counts(
        {key: transcript}, {key: token}, scripted_models=[family],
    ) == {key: None}


@pytest.mark.parametrize("method", ["legacy", "scripted"])
def test_explicitly_scripted_models_retain_technical_count_compatibility(
    tmp_path: Path, method: str,
):
    transcript = {
        "competitor_id": "docs-fetch--legacy--r1", "corpus_id": "Q.1",
        "model_family_id": "legacy", "system_id": "docs-fetch", "tool_model_key": "docs:x",
    }
    token = {k: transcript[k] for k in ("competitor_id", "corpus_id", "tool_model_key")}
    token.update(status="counted", client_wrapped_tokens=123)
    if method == "scripted":
        token["execution"] = {"provider": "scripted", "api": "scripted", "model_id": "bytes/4"}
    key = transcript["competitor_id"], transcript["corpus_id"]
    assert CountCapture(tmp_path).admitted_counts(
        {key: transcript}, {key: token}, scripted_models=["legacy"],
    ) == {key: 123}
    assert CountCapture(tmp_path).admitted_counts({key: transcript}, {key: token}) == {key: None}
    assert CountCapture(tmp_path).admitted_counts(
        {key: {**transcript, "execution": {"api": "openai-responses"}}}, {key: token},
        scripted_models=["legacy"],
    ) == {key: None}  # A contradictory scripted declaration cannot legalize hosted counts.
    for field, bad in (("competitor_id", "foreign"), ("corpus_id", "Q.2"),
                       ("tool_model_key", "other"), ("client_wrapped_tokens", True),
                       ("client_wrapped_tokens", -1), ("status", "failed")):
        assert CountCapture(tmp_path).admitted_counts(
            {key: transcript}, {key: {**token, field: bad}}, scripted_models=["legacy"],
        ) == {key: None}


async def test_dotted_system_coordinate_joins_receipts(tmp_path: Path) -> None:
    transcript, token = await recorded_bundle(
        tmp_path, *REAL_FAMILIES[0], system="python.docs", qid="Q.1"
    )
    assert live_count_matches(tmp_path, transcript, token)


@pytest.mark.parametrize("legacy", LEGACY_IDS)
async def test_legacy_coordinates_still_join_receipts(tmp_path: Path, legacy: str) -> None:
    transcript, token = await recorded_bundle(tmp_path, legacy, "openai", system=legacy, qid=legacy)
    assert live_count_matches(tmp_path, transcript, token)


@pytest.mark.parametrize("field", ["competitor_id", "corpus_id", "model_family_id", "system_id"])
@pytest.mark.parametrize("unsafe", UNSAFE_IDS)
async def test_unsafe_coordinate_refused_before_artifact_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, unsafe: Any
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("unsafe coordinate reached artifact reader")

    monkeypatch.setattr("benchmarks.report_measurements.artifact_bytes", forbidden)
    transcript = {
        "competitor_id": "cell",
        "corpus_id": "Q",
        "model_family_id": "family",
        "system_id": "system",
        field: unsafe,
    }
    assert not live_count_matches(tmp_path, transcript, {"count_run_id": "count-1"})


@pytest.mark.parametrize("value", ["run-id", "_legacy", "-", "a" * 240])
def test_measurement_run_and_item_grammar_preserved(value: str) -> None:
    assert _identifier(value) == value


@pytest.mark.parametrize("value", ["run.1", "gpt-6.1-sol", *UNSAFE_IDS])
def test_dots_remain_invalid_for_measurement_run_and_item_ids(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid measurement coordinate"):
        _identifier(value)


@pytest.mark.parametrize("family,provider", REAL_FAMILIES)
async def test_dotted_coordinates_preserve_symlink_confinement(
    tmp_path: Path, family: str, provider: str
) -> None:
    transcript, token = await recorded_bundle(tmp_path, family, provider)
    path = tmp_path / "systems" / f"{family}--python-docs.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(BenchmarkValidationError):
        live_count_matches(tmp_path, transcript, token)


@pytest.mark.parametrize(
    "relative",
    [
        "../Q.json",
        ".hidden/Q.json",
        "/absolute/Q.json",
        "cell/../Q.json",
        "cell/./Q.json",
        "cell//Q.json",
        "cell/.json",
        "cell/..json",
        "cell/.hidden.json",
        "cell/a\\b.json",
        "cell/Q.json/extra",
        "cell/" + "a" * 241 + ".json",
    ],
)
async def test_unsafe_receipt_coordinates_are_unavailable(tmp_path: Path, relative: str) -> None:
    transcript, token = await recorded_bundle(tmp_path, *REAL_FAMILIES[0])
    assert live_count_matches(tmp_path, transcript, token)
    path = tmp_path / "grading/count-runs/count-local.json"
    receipt = json.loads(path.read_bytes())
    receipt["results"] = {relative: "0" * 64}
    write(path, receipt)
    assert not live_count_matches(tmp_path, transcript, token)


@pytest.mark.parametrize("run", ["count.1", ".hidden", "../escape", "/absolute", "a" * 241])
async def test_count_run_id_keeps_its_separate_grammar(tmp_path: Path, run: str) -> None:
    transcript, token = await recorded_bundle(tmp_path, *REAL_FAMILIES[0])
    token["count_run_id"] = run
    assert not live_count_matches(tmp_path, transcript, token)
