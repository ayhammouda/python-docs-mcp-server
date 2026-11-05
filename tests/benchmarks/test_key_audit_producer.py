"""The ordinary key-audit producer must satisfy the portable disclosure contract.

Only local, explicitly synthetic indexes and recording HTTP transports are used.
"""

import hashlib
import json
import shutil
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal

import httpx2
import pytest
import yaml

from benchmarks.agentic.clients import anthropic_sdk
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading import inputs, run, worker_task
from benchmarks.grading.batch import AnthropicBatchClient
from benchmarks.grading.disclosure import audit_request
from benchmarks.grading.panel import load_panel
from benchmarks.grading.probes import cited_sections
from benchmarks.grading.score import score_run_dir
from benchmarks.report import _load_run_bundle
from benchmarks.runner import BenchmarkValidationError
from mcp_server_python_docs.storage.db import get_readwrite_connection
from tests.benchmarks import test_agentic_report as reports
from tests.benchmarks import test_grading_probes as probes_fixture
from tests.benchmarks.accepted_report_fixture import read, write
from tests.benchmarks.test_grading_batch import ANTHROPIC_BATCH, _anthropic_ok
from tests.benchmarks.test_grading_financial_preflight import run_grading_jobs, without_signals
from tests.benchmarks.test_grading_probes import _question
from tests.benchmarks.test_grading_run import config

index_db = probes_fixture.index_db


@pytest.fixture
def audit_input(tmp_path, index_db, monkeypatch):
    cfg = config(tmp_path, "key-audit", index_path=index_db)
    root = cfg.out_dir / "grading/inputs"
    manifest = inputs.snapshot_inputs(
        root, criteria_path=cfg.criteria_path, panel_path=cfg.panel_path,
        probes_path=cfg.probes_path,
    )
    # Make the frozen bytes observably different from any checkout default.
    prompt = b"Frozen audit prompt.\r\nExplain, then decide.\n"
    (root / "key-audit-prompt.md").write_bytes(prompt)
    manifest["key_audit_prompt"] = hashlib.sha256(prompt).hexdigest()
    write(root / "inputs.json", manifest)
    monkeypatch.setattr(run, "_criteria", lambda _: {"Q1": _question()})
    return cfg, manifest, prompt


def test_producer_uses_shared_frozen_request_and_exact_index_sections(audit_input):
    cfg, manifest, prompt = audit_input
    before = cfg.index_path.read_bytes()
    (item,) = run.build_items(cfg, load_panel(cfg.panel_path))
    sections = [asdict(s) for s in cited_sections(_question(), cfg.index_path)]
    expected = audit_request(_question(), sections, prompt=prompt)
    assert item.request.system == expected.system
    assert item.request.user == expected.user
    assert item.request.schema == inputs.key_audit_schema()
    assert item.request.schema_name == "key_audit"
    assert run._item_evidence(item) == expected.item_evidence({
        "corpus_id": "Q1", "cited_sections": sections, "inputs": manifest,
    })
    assert cfg.index_path.read_bytes() == before


@pytest.mark.parametrize("missing", ["page", "anchor", "version", "empty"])
def test_incomplete_index_never_becomes_an_audit_request(audit_input, monkeypatch, missing):
    cfg, _, _ = audit_input
    url = {
        "page": "https://docs.python.org/3.13/library/absent.html",
        "anchor": "https://docs.python.org/3.13/library/json.html#absent",
        "version": "https://docs.python.org/3.12/library/json.html#json.dumps",
        "empty": "https://docs.python.org/3.13/library/json.html#json.dumps",
    }[missing]
    if missing == "empty":
        conn = get_readwrite_connection(cfg.index_path)
        conn.execute("UPDATE sections SET content_text = '   '")
        conn.commit()
        conn.close()
    q = _question(citations=(_question().citations[0], url))
    monkeypatch.setattr(run, "_criteria", lambda _: {q.id: q})
    with pytest.raises(BenchmarkValidationError, match="cited sections"):
        run.build_items(cfg, load_panel(cfg.panel_path))


def test_document_bound_retains_citation_order_and_versions(audit_input, monkeypatch):
    cfg, _, prompt = audit_input
    conn = get_readwrite_connection(cfg.index_path)
    conn.execute("UPDATE sections SET content_text = ?", ("Z" * 25_000,))
    conn.commit()
    conn.close()
    q = _question(citations=(
        "https://docs.python.org/3.13/library/json.html",
        *_question().citations,
    ))
    monkeypatch.setattr(run, "_criteria", lambda _: {q.id: q})
    (item,) = run.build_items(cfg, load_panel(cfg.panel_path))
    sections = json.loads(item.request.user)["cited_sections"]
    assert [s["url"] for s in sections] == list(q.citations)
    assert [s["version"] for s in sections] == ["3.13", "3.13"]
    assert sections[0] == asdict(cited_sections(q, cfg.index_path)[0])
    assert sections[1]["text"] == "Z" * (20_000 - len(sections[0]["text"]))
    assert sum(len(s["text"]) for s in sections) == 20_000
    assert item.request.user == audit_request(q, sections, prompt=prompt).user


def test_document_bound_cannot_silently_omit_a_required_citation(audit_input, monkeypatch):
    cfg, _, _ = audit_input
    conn = get_readwrite_connection(cfg.index_path)
    conn.execute("UPDATE sections SET content_text = ?", ("Z" * 25_000,))
    conn.commit()
    conn.close()
    q = _question(citations=(
        *_question().citations,
        "https://docs.python.org/3.13/library/json.html",
    ))
    monkeypatch.setattr(run, "_criteria", lambda _: {q.id: q})
    (item,) = run.build_items(cfg, load_panel(cfg.panel_path))
    sections = json.loads(item.request.user)["cited_sections"]
    source = cited_sections(q, cfg.index_path)
    assert [s["url"] for s in sections] == list(q.citations)
    assert sum(len(s["text"]) for s in sections) == 20_000
    for original, section in zip(source, sections, strict=True):
        assert section["text"] and original.text.startswith(section["text"])
    shortest = min(range(len(source)), key=lambda i: len(source[i].text))
    assert sections[shortest]["text"] == source[shortest].text



@pytest.fixture(scope="module")
def hosted_template(tmp_path_factory):
    """Fresh small hosted-like export; seed official versioned citations before votes."""
    base = tmp_path_factory.mktemp("audit-hosted-recording")
    fixture = base / "fixtures"
    fixture.mkdir()
    shutil.copytree(reports.GFIX, fixture / "grading")
    (fixture / "agentic").mkdir()
    shutil.copy(reports.FIX / "agentic/prices.yml", fixture / "agentic/prices.yml")
    corpus = yaml.safe_load((reports.FIX / "corpus.sample.yml").read_bytes())
    for q in corpus["questions"]:
        versions = q["python_version"]
        if isinstance(versions, str):
            versions = [versions]
        q["citations"] = [
            f"https://docs.python.org/{v}/library/json.html#json.dumps" for v in versions
        ]
    body = yaml.safe_dump(corpus).encode()
    (fixture / "corpus.sample.yml").write_bytes(body)
    criteria_path = fixture / "grading/criteria.sample.yml"
    criteria = yaml.safe_load(criteria_path.read_bytes())
    criteria["corpus_sha256"] = hashlib.sha256(body).hexdigest()
    criteria_path.write_text(yaml.safe_dump(criteria))
    probes_path = fixture / "grading/probes.sample.yml"
    probes = yaml.safe_load(probes_path.read_bytes())
    probes["header"].update(
        corpus_sha256=criteria["corpus_sha256"],
        criteria_sha256=hashlib.sha256(criteria_path.read_bytes()).hexdigest(),
    )
    probes_path.write_text(yaml.safe_dump(probes))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(reports, "FIX", fixture)
        patch.setattr(reports, "GFIX", fixture / "grading")
        return reports._live_like_run(base)


@pytest.fixture
def hosted_audit(tmp_path, hosted_template, index_db, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    out = shutil.copytree(hosted_template, tmp_path / "run")
    # Add the second genuinely distinct version to the local test index.
    conn = get_readwrite_connection(index_db)
    conn.execute(
        "INSERT INTO doc_sets (source, version, language, label, is_default, base_url) "
        "VALUES ('python-docs', '3.12', 'en', 'Python 3.12', 0, 'https://docs.python.org/3.12/')"
    )
    ds = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    text = "Serialize obj to a JSON formatted str (recorded Python 3.12 fixture)."
    conn.execute(
        "INSERT INTO documents (doc_set_id, uri, slug, title, content_text, char_count) "
        "VALUES (?, 'library/json.html', 'library/json', 'json', ?, ?)", (ds, text, len(text)),
    )
    doc = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO sections (document_id, uri, anchor, heading, level, ordinal, content_text, "
        "char_count) VALUES (?, 'library/json.html#json.dumps', 'json.dumps', 'json.dumps', "
        "3, 1, ?, ?)", (doc, text, len(text)),
    )
    conn.commit()
    conn.close()
    frozen = out / "grading/inputs"
    cfg = config(
        tmp_path, "key-audit", panel_path=frozen / "panel.yml",
        criteria_path=frozen / "grading-criteria.yml", probes_path=frozen / "probes.yml",
        prices_path=reports.REPO_ROOT / "docs/benchmarks/price-snapshot.yml",
        index_path=index_db, live=True, confirm_max_spend=Decimal("20"),
        grading_run_id="producer-audit",
    )
    seen, submitted = [], []
    judge = load_panel(cfg.panel_path).judges["reference"]

    def transport(request):
        seen.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/messages/batches"):
            submitted.extend(json.loads(request.content)["requests"])
            return httpx2.Response(200, json=ANTHROPIC_BATCH)
        if request.url.path.endswith("/results"):
            lines = []
            for item in submitted:
                supported = item["custom_id"] != "SYN-EX-001"
                line = _anthropic_ok(item["custom_id"], json.dumps({
                    "explanation": "Recorded audit verdict.", "key_supported": supported,
                }))
                line["result"]["message"]["model"] = judge.model_id
                lines.append(json.dumps(line))
            return httpx2.Response(200, text="\n".join(lines) + "\n")
        if request.method == "GET" and request.url.path.endswith("/msgbatch_1"):
            return httpx2.Response(200, json={**ANTHROPIC_BATCH, "processing_status": "ended"})
        pytest.fail(f"unexpected recording request: {request.method} {request.url.path}")

    async def jobs(*args, **kwargs):
        async with anthropic_sdk(
            "synthetic-worker-only-key", 5, transport=httpx2.MockTransport(transport)
        ) as sdk:
            client = AnthropicBatchClient(
                sdk, judge.model_id, judge.max_output_tokens, judge.effort
            )
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(worker_task, "make_batch_client", lambda *a, **k: client)
                await run_grading_jobs(*args, **kwargs)

    monkeypatch.setattr(run, "run_jobs", jobs)
    monkeypatch.setattr(run, "_until_signalled", without_signals)
    return cfg, seen, submitted


def _score_and_capture(cfg):
    summary = score_run_dir(cfg.out_dir, schema_path=reports.SCHEMA)
    return summary, _RunMetadata(_load_run_bundle(cfg.out_dir))


def test_normal_producer_is_verified_by_scorer_and_report_with_actual_booked_cost(hosted_audit):
    cfg, seen, submitted = hosted_audit
    before = cfg.index_path.read_bytes()
    confirmations = []
    result = run.run_grading(
        cfg, today=date(2026, 11, 4), confirm=lambda s: confirmations.append(s) or True
    )
    assert result["status"] == "completed" and result["records_written"] == 50
    assert confirmations and len(seen) == 3 and len(submitted) == 50
    summary, captured = _score_and_capture(cfg)
    audit = {"verified": True, "audited": 50, "flagged": ["SYN-EX-001"]}
    assert summary["key_audit"] == captured.audit == audit
    assert captured.grading == "live"
    cross = read(cfg.out_dir / "grading/items/key-audit/SYN-XV-001.json")
    assert [s["version"] for s in cross["meta"]["cited_sections"]] == ["3.12", "3.13"]
    events = [
        json.loads(line)
        for line in (cfg.out_dir / "grading/ledger-events.jsonl").read_text().splitlines()
    ]
    audit_booked = sum(
        Decimal(e["booked"]) for e in events
        if e["event"] == "settle" and e["run_id"] == cfg.grading_run_id
    )
    # 50 recorded replies at 1,200 input / 300 output tokens, Opus $4/$20
    # per million, with the frozen 0.5 batch multiplier. No provider was called.
    assert audit_booked == Decimal("0.270000")
    assert Decimal(result["spent_after"]["anthropic"]) == audit_booked
    assert summary["spend"] == captured.grading_spend
    assert Decimal(summary["spend"]["by_provider"]["anthropic"]) == sum(
        Decimal(e["booked"]) for e in events
        if e["event"] == "settle" and e["provider"] == "anthropic"
    )
    assert "Answer-key audit: 50 keys audited" in reports._render(cfg.out_dir)
    assert cfg.index_path.read_bytes() == before
    old_votes = {p: p.read_bytes() for p in (cfg.out_dir / "grading/votes").rglob("*.json")}
    again = run.run_grading(replace(cfg, grading_run_id="audit-resume"), confirm=lambda _: True)
    assert again["records_written"] == 0 and again["judges"]["reference"]["skipped"] == 50
    assert len(seen) == 3
    assert all(p.read_bytes() == body for p, body in old_votes.items())


@pytest.mark.parametrize("mutation", [
    "key", "citation", "receipt", "missing-proof", "missing-ledger", "legacy",
])
def test_current_audit_changes_are_detected_without_recreating_votes(hosted_audit, mutation):
    cfg, _, _ = hosted_audit
    run.run_grading(cfg, today=date(2026, 11, 4), confirm=lambda _: True)
    assert _score_and_capture(cfg)[0]["key_audit"]["verified"] is True
    root = cfg.out_dir / "grading"
    if mutation == "key":
        # Change an actual frozen corpus key outside the tiny answer cohort. Do
        # not rewrite any old request/vote to bless the newly edited input.
        corpus_path = cfg.out_dir / "snapshots/corpus.yml"
        corpus = yaml.safe_load(corpus_path.read_bytes())
        corpus["questions"][-1]["answer_key"] += " A changed key."
        corpus_path.write_text(yaml.safe_dump(corpus))
        criteria = yaml.safe_load(cfg.criteria_path.read_bytes())
        criteria["corpus_sha256"] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        cfg.criteria_path.write_text(yaml.safe_dump(criteria))
        manifest = read(root / "inputs/inputs.json")
        manifest["criteria"] = hashlib.sha256(cfg.criteria_path.read_bytes()).hexdigest()
        write(root / "inputs/inputs.json", manifest)
    elif mutation == "citation":
        path = root / "items/key-audit/SYN-EX-001.json"
        item = read(path)
        item["meta"]["cited_sections"][0]["text"] += " Altered citation."
        write(path, item)
    elif mutation == "legacy":
        write(root / "items/key-audit/SYN-EX-001.json", {
            "user": "<question>legacy XML</question>", "meta": {"question_id": "SYN-EX-001"},
        })
    elif mutation == "missing-ledger":
        (root / "ledger-events.jsonl").unlink()
    else:
        path = root / "executions/producer-audit/reference/SYN-EX-001--a1.json"
        if mutation == "missing-proof":
            path.unlink()
        else:
            receipt = read(path)
            receipt["result"]["text"] = "changed receipt"
            write(path, receipt)
    votes = {p: p.read_bytes() for p in (root / "votes").rglob("*.json")}
    summary, captured = _score_and_capture(cfg)
    assert summary["key_audit"]["verified"] is False
    assert captured.audit["verified"] is False
    if mutation == "missing-ledger":
        for spend in (summary["spend"], captured.grading_spend):
            assert spend["verified"] is False and spend["reason"] == "missing ledger"
            assert spend["by_provider"] == {} and spend["per_graded_answer"] == {}
        assert captured.grading_spend["graded_answers"] is None
    assert "Answer-key audit: unverified" in reports._render(cfg.out_dir)
    assert all(p.read_bytes() == body for p, body in votes.items())
    if mutation == "legacy":
        with pytest.raises(BenchmarkValidationError, match="immutable grading evidence differs"):
            run.run_grading(replace(cfg, grading_run_id="legacy-resume"), confirm=lambda _: True)
        assert all(p.read_bytes() == body for p, body in votes.items())


@pytest.mark.parametrize("prefix", ["", "Z" * 25_000])
def test_index_secret_scan_precedes_item_publication_and_ledger(audit_input, monkeypatch, prefix):
    cfg, _, _ = audit_input
    conn = get_readwrite_connection(cfg.index_path)
    conn.execute("UPDATE sections SET content_text = ?", (prefix + " sk-" + "X" * 42,))
    conn.commit()
    conn.close()
    # Keep the real freeze/CLI path; the one-question criteria seam is local to this fixture.
    monkeypatch.setattr(run, "_frozen", lambda _: read(cfg.out_dir / "grading/inputs/inputs.json"))
    (cfg.out_dir / "snapshots").mkdir()
    (cfg.out_dir / "snapshots/corpus.yml").write_text("fixture")
    with pytest.raises(KeyLeakError):
        run.run_grading(cfg)
    assert not (cfg.out_dir / "grading/items").exists()
    assert not cfg.ledger_dir.exists()
