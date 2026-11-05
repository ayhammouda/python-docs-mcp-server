"""Shared mock-transport grading pilot; production writers own every artifact.

Synthetic HTTP and keys only. These unsigned fixtures prove local consistency,
never a live provider call or billing. Current receipts are never repaired.
"""

import json
import os
import socket
from datetime import date
from decimal import Decimal as D

import httpx
import httpx2
import pytest

from benchmarks import __main__ as cli
from benchmarks.agentic import run as answering
from benchmarks.grading import batch, run, worker_task
from benchmarks.grading.inputs import safe_id
from tests.benchmarks import test_agentic_reference_evidence as reference_fixtures
from tests.benchmarks.test_grading_batch import (
    ANTHROPIC_BATCH,
    Router,
    _anthropic_ok,
    _gemini_job,
    _gemini_response,
    _json,
    _openai_batch,
    _openai_body,
    _text,
)
from tests.benchmarks.test_grading_financial_preflight import run_grading_jobs, without_signals
from tests.benchmarks.test_grading_run import cli_args
from tests.benchmarks.test_grading_score import _vote_text


def read(path):
    return json.loads(path.read_bytes())


class NoLaunch:
    """Planning is allowed; worker preparation after a decline is a failure."""

    def supports(self, family):
        return True

    async def prepare(self, jobs):
        pytest.fail("preflight started workers")


def forbidden(*args, **kwargs):
    pytest.fail("network, signal or interactive confirmation attempted")


def block_external_io(patch):
    patch.setattr(socket.socket, "connect", forbidden)
    patch.setattr(socket, "getaddrinfo", forbidden)
    patch.setattr(os, "kill", forbidden)
    patch.setattr(os, "killpg", forbidden)


def answer_mock_pilot(tmp_path, monkeypatch, *, prices_path=None, answer=None):
    """Produce an ordinary answering reference with mocked worker I/O and HTTP."""
    block_external_io(monkeypatch)
    monkeypatch.setattr(answering, "_until_signalled", without_signals)
    monkeypatch.setattr(reference_fixtures, "_container", lambda _: (NoLaunch(), None))
    with monkeypatch.context() as patch:
        patch.setattr(answering, "authorize_live", lambda text, **kwargs: None)
        if answer is not None:
            patch.setattr(
                reference_fixtures.adapters,
                "OPENAI_MESSAGE",
                {
                    **reference_fixtures.adapters.OPENAI_MESSAGE,
                    "content": [{"type": "output_text", "text": answer, "annotations": []}],
                },
            )
        if prices_path is not None:
            original = reference_fixtures._live_config

            def config(base, **kwargs):
                return original(base, **(kwargs | {"prices_path": prices_path}))

            patch.setattr(reference_fixtures, "_live_config", config)
        return reference_fixtures._hosted_mock_pilot(tmp_path, monkeypatch)


def transports(item_id, *, small_usage=False):
    """Local provider responses, using the SDK wire fixtures and their real usage."""
    vote = _vote_text()
    anthropic_url = "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results"
    ended = {**ANTHROPIC_BATCH, "processing_status": "ended", "results_url": anthropic_url}
    anthropic_line = _anthropic_ok(item_id, vote)
    if small_usage:
        anthropic_line["result"]["message"]["usage"] = {"input_tokens": 100, "output_tokens": 2}
    anthropic = Router(
        [
            ("POST", r"/v1/messages/batches$", _json(httpx2, ANTHROPIC_BATCH)),
            ("GET", r"/msgbatch_1$", _json(httpx2, ended)),
            ("GET", r"/results$", _text(httpx2, json.dumps(anthropic_line) + "\n")),
        ]
    )
    openai_line = _openai_body(item_id)
    openai_line["response"]["body"]["output"][0]["content"][0]["text"] = vote
    if small_usage:
        openai_line["response"]["body"]["usage"] = {"input_tokens": 100, "output_tokens": 2}
    openai = Router(
        [
            ("POST", r"/v1/files$", _json(httpx2, {"id": "file-in"})),
            ("POST", r"/v1/batches$", _json(httpx2, _openai_batch("validating"))),
            (
                "GET",
                r"/v1/batches/batch_1$",
                _json(httpx2, _openai_batch("completed", output_file_id="file-out")),
            ),
            ("GET", r"/file-out/content$", _text(httpx2, json.dumps(openai_line) + "\n")),
        ]
    )
    google_line = _gemini_response(item_id, text=vote)
    if small_usage:
        google_line["response"]["usageMetadata"] = {
            "promptTokenCount": 100,
            "candidatesTokenCount": 2,
        }
    google = Router(
        [
            (
                "POST",
                r"upload_id=u1",
                _json(
                    httpx, {"file": {"name": "files/in"}}, headers={"x-goog-upload-status": "final"}
                ),
            ),
            (
                "POST",
                r"/upload/v1beta/files",
                _json(
                    httpx,
                    {},
                    headers={
                        "x-goog-upload-url": "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=u1"
                    },
                ),
            ),
            ("POST", r"batchGenerateContent$", _json(httpx, _gemini_job("JOB_STATE_PENDING"))),
            ("GET", r"/batches/b1$", _json(httpx, _gemini_job("JOB_STATE_SUCCEEDED", "files/out"))),
            (
                "GET",
                r"files/out:download",
                _text(httpx, json.dumps(google_line) + "\n"),
            ),
        ]
    )
    return {"anthropic": anthropic, "openai": openai, "google": google}


def grade_mock_pilot(cfg, monkeypatch, *, small_usage=False):
    """Run the ordinary CLI, batch SDKs, host, ledger, collector and score writer."""
    root = cfg.out_dir
    block_external_io(monkeypatch)
    monkeypatch.setattr(run, "_until_signalled", without_signals)
    transcript = read(next((root / "transcripts").glob("*/*.json")))
    item_id = safe_id(transcript["competitor_id"], transcript["corpus_id"])
    routers = transports(item_id, small_usage=small_usage)

    def mock_client(family, **kwargs):
        http = httpx if family.provider == "google" else httpx2
        return batch.make_batch_client(
            family, **kwargs, transport=http.MockTransport(routers[family.provider])
        )

    # Supply a dummy key only at the SDK construction boundary, never auth/env.
    monkeypatch.setattr(batch, "live_key", lambda family: (family.provider, "test-key"))
    monkeypatch.setattr(worker_task, "make_batch_client", mock_client)
    monkeypatch.setattr(run, "run_jobs", run_grading_jobs)
    grading_confirmations = []
    monkeypatch.setattr(
        run.live, "authorize_live", lambda text, **kwargs: grading_confirmations.append(text)
    )
    monkeypatch.setattr(cli, "_container_launcher", lambda *args: NoLaunch())
    ordinary_grading = run.run_grading

    def invoke_grading(*args, **kwargs):
        return ordinary_grading(*args, **kwargs, today=date(2026, 11, 4), confirm=forbidden)

    monkeypatch.setattr(run, "run_grading", invoke_grading)
    assert (
        cli.main(
            cli_args(
                cfg,
                "--launcher",
                "container",
                "--image",
                "synthetic-unused-image",
                "--live",
                "--confirm-max-spend",
                str(cfg.confirm_max_spend),
                "--grading-run-id",
                cfg.grading_run_id,
            )
        )
        == 0
    )
    invocation = read(root / f"grading/runs/{cfg.grading_run_id}.json")
    assert len(grading_confirmations) == 1
    for provider, estimate in invocation["batch_estimates"].items():
        assert f"{provider}: planned standard-rate bound" in grading_confirmations[0]
        assert f"batch-rate estimate {estimate}" in grading_confirmations[0]
        actual = D(invocation["spent_after"][provider]) - D(invocation["spent_before"][provider])
        assert D(invocation["provider_bounds"][provider]) == 2 * D(estimate)
        assert 0 < actual < D(estimate)
    assert invocation["status"] == "completed" and not invocation["dry_run"]
    assert invocation["items"] == 1 and invocation["records_written"] == 3
    assert invocation["not_run"] == [] and invocation["withheld"] == {}
    before = {p: p.read_bytes() for p in (root / "grading").rglob("*") if p.is_file()}
    assert (
        cli.main(
            [
                "agentic-score",
                "--run-dir",
                str(root),
                "--panel",
                str(cfg.panel_path),
                "--criteria",
                str(cfg.criteria_path),
            ]
        )
        == 0
    )
    assert all(p.read_bytes() == body for p, body in before.items())
    return routers
