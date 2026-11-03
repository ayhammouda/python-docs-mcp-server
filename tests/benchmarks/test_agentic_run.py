"""agentic-run: plan, artifacts, run summary and the mocked end-to-end test (design §6-§12).

Every model is scripted and every worker is keyless: no test reaches a network host.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks.__main__ import main
from benchmarks.agentic.bridge import load_systems
from benchmarks.agentic.clients import PAID_KEY_ENVS
from benchmarks.agentic.host import LocalLauncher
from benchmarks.agentic.loop import MAX_RETRIES
from benchmarks.agentic.protocol import KEY_SHAPED, KeyLeakError
from benchmarks.agentic.run import (
    OUTPUT_CAP_STOPS,
    AgenticConfig,
    cell_id,
    plan_jobs,
    run_agentic,
    tool_model_key,
)
from benchmarks.model_matrix import load_model_matrix
from benchmarks.report import generate_report
from benchmarks.runner import BenchmarkValidationError, _load_questions, _load_yaml_mapping
from benchmarks.scoring import score_run
from tests.test_mcp_get_docs_cache_smoke import _create_contentful_json_index
from tests.test_stdio_smoke import _isolated_cache_env

REPO_ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures" / "agentic"
SAMPLE_CORPUS = Path(__file__).parent / "fixtures" / "corpus.sample.yml"
CORPUS_SCHEMA = REPO_ROOT / "docs" / "benchmarks" / "corpus.schema.json"
METHODOLOGY_PATH = REPO_ROOT / "docs" / "benchmarks" / "PUBLIC-BENCHMARK-METHODOLOGY.md"
COMMITTED_MATRIX = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"
COMMITTED_MANIFEST = REPO_ROOT / "docs" / "benchmarks" / "agentic-manifest.yml"
COMMITTED_PRICES = REPO_ROOT / "docs" / "benchmarks" / "price-snapshot.yml"
FAKE_SERVER = FIX / "fake_fetch_server.py"
# 3 systems x 2 questions x 2 repeats: python-docs and docs-fetch make 2 calls, no-docs 1,
# so 20 calls of 1,000 input tokens at $1/1M and 100 output tokens at $10/1M.
EXPECTED_ANTHROPIC_SPEND = Decimal("0.04")
EX1 = "fixture_symbol_ex_1()"  # a phrase only SYN-EX-001's prompt holds
EX2 = "fixture_symbol_ex_2()"


@pytest.fixture(autouse=True)
def _keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PAID_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


def _write_manifest(tmp_path: Path) -> Path:
    committed = {
        s.id: s.raw for s in load_systems(COMMITTED_MANIFEST)
    }  # python-docs and no-docs as committed
    fetch = {
        "id": "docs-fetch",
        "adapter": "mcp-stdio",
        "command": ["{python}", str(FAKE_SERVER)],
        "env": {},
        "pass_env": ["HOME"],
    }
    path = tmp_path / "manifest.yml"
    competitors = [committed["python-docs"], fetch, committed["no-docs"]]
    path.write_text(yaml.safe_dump({"competitors": competitors}), encoding="utf-8")
    return path


def _isolated_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    env, cache_dir = _isolated_cache_env(tmp_path)
    _create_contentful_json_index(cache_dir)
    monkeypatch.setenv("XDG_CACHE_HOME", env["XDG_CACHE_HOME"])
    monkeypatch.setenv("HOME", env["HOME"])
    return cache_dir


def _config(tmp_path: Path, **overrides: Any) -> AgenticConfig:
    values: dict[str, Any] = {
        "corpus_path": SAMPLE_CORPUS,
        "matrix_path": FIX / "model-matrix.yml",
        "manifest_path": COMMITTED_MANIFEST,
        "prices_path": FIX / "prices.yml",
        "ledger_dir": tmp_path / "ledger",
        "out_dir": tmp_path / "run",
        "questions": ("SYN-EX-001", "SYN-EX-002"),
        "repeats": 2,
        "seed": 7,
    }
    values.update(overrides)
    return AgenticConfig(**values)


def _matrix_with(tmp_path: Path, scripts: dict[str, list[dict[str, Any]]], extra=()) -> Path:
    """The fixture matrix with some families' scripts replaced, plus extra families."""
    data = yaml.safe_load((FIX / "model-matrix.yml").read_text(encoding="utf-8"))
    families = data["model_families"] + [dict(f) for f in extra]
    for family in families:
        if family["id"] in scripts:
            script = tmp_path / f"script-{family['id']}.yml"
            script.write_text(yaml.safe_dump({"steps": scripts[family["id"]]}), encoding="utf-8")
            family["script"] = str(script)
        else:
            family["script"] = str(FIX / family["script"])
    data["model_families"] = families
    path = tmp_path / "matrix.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_agentic_run_end_to_end_with_scripted_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = _isolated_index(tmp_path, monkeypatch)
    manifest = _write_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)  # the relative script paths must not depend on the cwd
    summary = run_agentic(_config(tmp_path, manifest_path=manifest))
    assert summary["planned_cells"] == 36 and summary["succeeded_cells"] == 36
    assert summary["failed_cells"] == 0 and summary["discarded"] == [] and summary["not_run"] == []
    assert summary["scored_cells"] == summary["correctness_denominator_cells"] == 36
    run = tmp_path / "run"

    def tools(system: str) -> list[str]:
        path = run / "transcripts" / f"{system}--scripted-anthropic--r1" / "SYN-EX-001.json"
        return [c["tool"] for c in json.loads(path.read_text())["tool_calls"]]

    assert (tools("python-docs"), tools("docs-fetch"), tools("no-docs")) == (
        ["search_docs"],
        ["fetch"],
        [],
    )
    transcript = json.loads(
        (run / "transcripts" / "no-docs--scripted-openai--r2" / "SYN-EX-002.json").read_text()
    )
    assert transcript["tool_model_key"] == "no-docs:openai/scripted-2"
    assert (transcript["competitor_id"], transcript["model_family_id"]) == (
        "no-docs--scripted-openai--r2",
        "scripted-openai",
    )
    info = json.loads((run / "systems" / "scripted-anthropic--python-docs.json").read_text())
    assert len(info["tools_raw"]) == 7
    assert info["index_sha256"] == hashlib.sha256((cache_dir / "index.db").read_bytes()).hexdigest()
    assert Decimal(summary["ledger"]["anthropic"]["spent_after"]) == EXPECTED_ANTHROPIC_SPEND
    assert Decimal(summary["ledger"]["anthropic"]["spent_before"]) == 0
    assert summary["price_snapshot"] == {"captured_on": "2026-10-31", "expires_on": None}
    assert summary["mode"] == "agentic" and summary["seed"] == 7 and summary["repeats"] == 2
    env = json.loads((run / "environment.json").read_text())
    assert env["mode"] == "agentic" and set(env["client_versions"]) >= {"anthropic", "mcp"}
    for name in ("corpus.yml", "competitor-manifest.yml", "model-matrix.yml"):
        assert (run / "snapshots" / name).is_file()
    assert (run / "snapshots" / "competitor-manifest.yml").read_bytes() == manifest.read_bytes()
    assert (run / "snapshots" / "system-prompt.txt").read_text().startswith("You answer")
    assert (
        len((run / "ledger-events.jsonl").read_text().splitlines()) == 3 * 2 * 20
    )  # reserve+settle
    assert len(list((run / "logs").glob("*.stderr.log"))) == 9
    tokens = json.loads(
        (run / "tokens" / "no-docs--scripted-openai--r2" / "SYN-EX-002.json").read_text()
    )
    assert tokens["status"] == "placeholder" and tokens["count_model"] == "claude-sonnet-5-5"
    assert score_run(run, corpus_path=SAMPLE_CORPUS, schema_path=CORPUS_SCHEMA)["total_cells"] == 36
    report = generate_report(
        run, model_matrix_path=FIX / "model-matrix.yml", methodology_path=METHODOLOGY_PATH
    ).read_text()
    assert "scripted-1" in report
    for path in run.rglob("*"):
        if path.is_file():
            assert KEY_SHAPED.search(path.read_text(errors="replace")) is None


def test_plan_jobs_makes_script_paths_absolute_and_orders_each_job_by_seed(
    tmp_path: Path,
) -> None:
    corpus = _load_yaml_mapping(SAMPLE_CORPUS, "corpus")
    questions = [q for q in _load_questions(corpus) if q.id in ("SYN-EX-001", "SYN-EX-002")]
    families = load_model_matrix(FIX / "model-matrix.yml").model_families
    systems = load_systems(COMMITTED_MANIFEST)
    jobs = plan_jobs(questions, families, systems, repeats=2, seed=7, matrix_dir=FIX)
    assert len(jobs) == 9 and all(Path(j.family.raw["script"]).is_absolute() for j in jobs)
    assert jobs[0].job_id == "scripted-anthropic--python-docs"
    for job in jobs:
        canonical = [(q.id, r) for q in questions for r in (1, 2)]
        random.Random(f"7/{job.family.id}/{job.system.id}").shuffle(canonical)
        assert [(t["corpus_id"], t["repeat"]) for t in job.trajectories] == canonical
    again = plan_jobs(questions, families, systems, repeats=2, seed=7, matrix_dir=FIX)
    assert again == jobs


def test_cell_and_tool_model_keys() -> None:
    assert cell_id("no-docs", "gpt-6.1-sol", 3) == "no-docs--gpt-6.1-sol--r3"
    assert tool_model_key("no-docs", "openai", "gpt-6.1-sol") == "no-docs:openai/gpt-6.1-sol"
    assert OUTPUT_CAP_STOPS == {"max_tokens", "max_output_tokens", "MAX_TOKENS", "CONTINUATION"}


def test_agentic_run_budget_stop_discards_and_lists_cells_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_index(tmp_path, monkeypatch)
    manifest = _write_manifest(tmp_path)
    caps = {"anthropic": Decimal("0"), "openai": Decimal(65), "google": Decimal(180)}
    summary = run_agentic(_config(tmp_path, manifest_path=manifest), caps=caps)
    run = tmp_path / "run"
    assert len(summary["discarded"]) + len(summary["not_run"]) == 12
    assert {d["reason"] for d in summary["discarded"] + summary["not_run"]} == {"budget_stop"}
    assert summary["succeeded_cells"] == 24 and summary["planned_cells"] == 36
    assert not any((run / "scoring").glob("*--scripted-anthropic--*"))
    assert len(list((run / "discarded").rglob("*--attempt1.json"))) == len(summary["discarded"])
    assert summary["ledger"]["anthropic"]["stopped"] == "cap"


def test_a_live_run_refuses_an_expired_price_snapshot(tmp_path: Path) -> None:
    class EveryFamily(LocalLauncher):
        def supports(self, family: Any) -> bool:
            return True

        def command(self, job: Any) -> Any:
            raise AssertionError("no process may start")

    config = _config(
        tmp_path,
        matrix_path=COMMITTED_MATRIX,
        prices_path=COMMITTED_PRICES,
        models=("claude-sonnet-5-5",),
    )
    with pytest.raises(BenchmarkValidationError, match="2026-12-31"):
        run_agentic(config, launcher=EveryFamily(), today=date(2026, 12, 31))
    assert not (tmp_path / "ledger").exists()


def test_run_summary_counts_retries_and_reruns(tmp_path: Path) -> None:
    limited = {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0}}
    answer = {"text": "ok", "usage": {"input_tokens": 1000, "output_tokens": 100}}
    script = [
        {**limited, "prompt_contains": EX1},
        {**answer, "prompt_contains": EX1},
        *([{**limited, "prompt_contains": EX2}] * 5),
    ]
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic",),
        systems=("no-docs",),
        repeats=1,
    )
    summary = run_agentic(config)
    fam = summary["families"]["scripted-anthropic"]
    assert fam["retries"]["rate_limited"] == 1 + 3 * MAX_RETRIES and fam["reruns"] == 2
    assert [d["attempt"] for d in summary["discarded"]] == [1, 2, 3]
    assert len(summary["not_run"]) == 1 and summary["not_run"][0]["reason"] == "retries_exhausted"
    assert summary["not_run"][0]["cell"] == "no-docs--scripted-anthropic--r1"
    assert summary["succeeded_cells"] == 1 and fam["answer_turns"] == 1


def test_run_summary_wall_time_includes_failed_attempts_backoff_and_grant_waits(
    tmp_path: Path,
) -> None:
    # Two scripted families of provider anthropic, one no-docs job each, so their units contend
    # for the grant. The error step holds the grant for 1.0 s (owner amendment on #184), then
    # 0.3 s of backoff, then a 0.1 s answer.
    script = [
        {"error": {"kind": "rate_limited", "status": 429, "retry_after_s": 0.3}, "delay_s": 1.0},
        {"text": "ok", "delay_s": 0.1, "usage": {"input_tokens": 1000, "output_tokens": 100}},
    ]
    fixture = yaml.safe_load((FIX / "model-matrix.yml").read_text(encoding="utf-8"))
    twin = {**fixture["model_families"][0], "id": "scripted-anthropic-b"}
    matrix = _matrix_with(
        tmp_path, {"scripted-anthropic": script, "scripted-anthropic-b": script}, extra=[twin]
    )
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-anthropic", "scripted-anthropic-b"),
        systems=("no-docs",),
        questions=("SYN-EX-001",),
        repeats=1,
    )
    summary = run_agentic(config)
    assert set(summary["families"]) == {"scripted-anthropic", "scripted-anthropic-b"}
    for fam in summary["families"].values():
        assert fam["failed_attempt_s"] >= 0.2 and fam["retry_wait_s"] >= 0.3
        assert fam["wall_s"] >= 0.6
        assert fam["first_started_at"] < fam["last_completed_at"]
    assert sum(f["grant_wait_s"] for f in summary["families"].values()) > 0


def test_continuation_answers_count_as_output_cap_stops(tmp_path: Path) -> None:
    # One scripted google family, no-docs, all 50 sample questions, repeats 2 = 100 answers.
    usage = {"prompt_token_count": 1000, "candidates_token_count": 100}
    script = [
        {"text": "partial", "stop_reason": "CONTINUATION", "prompt_contains": EX1, "usage": usage},
        {"text": "Scripted answer.", "usage": usage},
    ]
    matrix = _matrix_with(tmp_path, {"scripted-google": script})
    config = _config(
        tmp_path,
        matrix_path=matrix,
        models=("scripted-google",),
        systems=("no-docs",),
        questions=None,
        repeats=2,
    )
    summary = run_agentic(config)
    fam = summary["families"]["scripted-google"]
    assert (fam["output_cap_stops"], fam["answer_turns"]) == (2, 100)  # 2%


def test_a_key_shape_in_an_input_file_is_refused_before_any_snapshot(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yml"
    key_shape = "sk-ant-api03-" + "A" * 90
    manifest.write_text(COMMITTED_MANIFEST.read_text() + f"\n# {key_shape}\n", encoding="utf-8")
    with pytest.raises(KeyLeakError) as caught:
        run_agentic(_config(tmp_path, manifest_path=manifest, dry_run=True))
    assert key_shape not in str(caught.value)
    assert not (tmp_path / "run" / "snapshots").exists()


def test_agentic_run_refuses_a_non_empty_out_dir(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "stale.json").write_text("{}")
    with pytest.raises(BenchmarkValidationError, match="not empty"):
        run_agentic(_config(tmp_path))


def test_agentic_run_refuses_an_unknown_family_or_system(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="no-such-model"):
        run_agentic(_config(tmp_path, models=("no-such-model",)))
    with pytest.raises(BenchmarkValidationError, match="context7"):
        run_agentic(_config(tmp_path, systems=("context7",)))


def test_agentic_run_refuses_a_family_whose_cap_excludes_reasoning(tmp_path: Path) -> None:
    fixture = yaml.safe_load((FIX / "model-matrix.yml").read_text(encoding="utf-8"))
    odd = {**fixture["model_families"][0], "id": "no-reasoning-in-cap"}
    odd["output_cap_includes_reasoning"] = False
    matrix = _matrix_with(tmp_path, {}, extra=[odd])
    with pytest.raises(BenchmarkValidationError, match="output_cap_includes_reasoning"):
        run_agentic(_config(tmp_path, matrix_path=matrix, models=("no-reasoning-in-cap",)))


def test_agentic_run_dry_run_writes_the_plan_without_a_ledger(tmp_path: Path) -> None:
    summary = run_agentic(_config(tmp_path, dry_run=True))
    run = tmp_path / "run"
    assert summary["dry_run"] is True and summary["planned_cells"] == 36
    planned = json.loads((run / "planned-cells.json").read_text())["cells"]
    assert len(planned) == 36
    assert {"cell", "competitor_id", "corpus_id", "repeat", "system_id"} <= set(planned[0])
    assert (run / "environment.json").is_file() and (run / "snapshots" / "corpus.yml").is_file()
    assert not (tmp_path / "ledger").exists()
    assert not (run / "transcripts").exists()


def test_cli_agentic_run_exits_2_with_a_provider_key_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x-not-a-real-key")
    with pytest.raises(SystemExit) as caught:
        main(_cli_args(tmp_path))
    assert caught.value.code == 2


def test_cli_agentic_run_exits_3_on_a_key_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leak(*args: Any, **kwargs: Any) -> Any:
        raise KeyLeakError("a key-shaped string was found in a test frame")

    monkeypatch.setattr("benchmarks.agentic.run.run_agentic", leak)
    with pytest.raises(SystemExit) as caught:
        main(_cli_args(tmp_path))
    assert caught.value.code == 3


def test_cli_agentic_run_dry_run_prints_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*_cli_args(tmp_path), "--dry-run", "--repeats", "1", "--run-id", "cli"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["planned_cells"] == 18 and printed["run_id"] == "cli"


def _cli_args(tmp_path: Path) -> list[str]:
    return [
        "agentic-run",
        "--corpus",
        str(SAMPLE_CORPUS),
        "--out",
        str(tmp_path / "run"),
        "--ledger-dir",
        str(tmp_path / "ledger"),
        "--matrix",
        str(FIX / "model-matrix.yml"),
        "--prices",
        str(FIX / "prices.yml"),
        "--questions",
        "SYN-EX-001,SYN-EX-002",
    ]
