"""Task 11b: normal commands join scripted answering, grading, counting and reporting."""

import hashlib
import json
import os
import re
import socket
import sys

import yaml

from benchmarks import __main__ as cli
from benchmarks.agentic import count, host
from benchmarks.agentic.protocol import KEY_SHAPED
from benchmarks.agentic_report import FORBIDDEN_PHRASES, REQUIRED_WORDING
from benchmarks.grading import run, score
from benchmarks.grading.inputs import TUNING_PATH, load_tuning_questions, safe_id
from benchmarks.grading.probes import load_probe_file
from tests.benchmarks.test_agentic_run import (
    FIX as AFIX,
)
from tests.benchmarks.test_agentic_run import (
    METHODOLOGY_PATH,
    SAMPLE_CORPUS,
    _isolated_index,
    _matrix_with,
    _write_manifest,
)
from tests.benchmarks.test_grading_run import CRITERIA, FIX, IDS, PROBES, panel_file
from tests.benchmarks.test_grading_score import _ALL_MET, _fields_for


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scripted_benchmark_is_graded_counted_scored_and_reported(tmp_path, monkeypatch, capsys):
    # A broken command handoff, lost split/suspect or a public claim from this tiny
    # fixture must fail here. Only provider responses and input paths are synthetic.
    for name in tuple(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CI", "1")
    cache = _isolated_index(tmp_path, monkeypatch)
    index = cache / "index.db"
    manifest = _write_manifest(tmp_path)
    root = tmp_path / "run"
    ledger = tmp_path / "ledger"

    def offline(*args, **kwargs):
        raise AssertionError("network is forbidden in the scripted regression")

    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setattr(socket, "getaddrinfo", offline)
    original_command = host.LocalLauncher.command

    def command(self, job):
        _, env = original_command(self, job)
        # Guard the worker too; stdio MCP children use only the local fixture index
        # and fake_fetch_server.py (which returns static text).
        code = (
            "import sys\n"
            "def audit(event, args):\n"
            "    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):\n"
            "        raise AssertionError('network is forbidden')\n"
            "sys.addaudithook(audit)\n"
            "from benchmarks.agentic.worker import main_worker\n"
            "raise SystemExit(main_worker())\n"
        )
        return [sys.executable, "-c", code], env

    monkeypatch.setattr(host.LocalLauncher, "command", command)

    def invoke(*args):
        assert cli.main(list(map(str, args))) == 0
        return json.loads(capsys.readouterr().out)

    # Key audit covers the entire corpus, including questions outside the answer run.
    corpus = yaml.safe_load(SAMPLE_CORPUS.read_text())
    for q in corpus["questions"]:
        q["citations"] = ["https://docs.python.org/3.13/library/json.html#json.dumps"]
    corpus_path = tmp_path / "corpus.yml"
    corpus_path.write_text(yaml.safe_dump(corpus))
    criteria = yaml.safe_load(CRITERIA.read_text())
    criteria["corpus_sha256"] = digest(corpus_path)
    criteria_path = tmp_path / "criteria.yml"
    criteria_path.write_text(yaml.safe_dump(criteria))
    # The plan explicitly permits the committed sample probes for tuning/G1.
    probes = yaml.safe_load(PROBES.read_text())
    probes["header"].update(
        corpus_sha256=digest(corpus_path), criteria_sha256=digest(criteria_path)
    )
    probes_path = tmp_path / "probes.yml"
    probes_path.write_text(yaml.safe_dump(probes))
    monkeypatch.setattr(run, "CORPUS_PATH", corpus_path)

    # Script the suspect at answering time: no transcript or generated artifact edit.
    script = yaml.safe_load((AFIX / "script-anthropic.yml").read_text())["steps"]
    script[-1]["text"] = "Scripted answer. </answer>"
    matrix = _matrix_with(tmp_path, {"scripted-anthropic": script})
    answering = invoke(
        "agentic-run",
        "--corpus",
        corpus_path,
        "--matrix",
        matrix,
        "--manifest",
        manifest,
        "--prices",
        AFIX / "prices.yml",
        "--out",
        root,
        "--ledger-dir",
        ledger,
        "--questions",
        "SYN-EX-001,SYN-EX-002",
        "--repeats",
        "2",
        "--seed",
        "7",
    )
    assert answering["planned_cells"] == answering["succeeded_cells"] == 36
    assert answering["not_run"] == answering["worker_errors"] == []
    transcripts = [read(p) for p in (root / "transcripts").glob("*/*.json")]
    assert {t["system_id"] for t in transcripts} == {"python-docs", "docs-fetch", "no-docs"}
    assert {t["repeat"] for t in transcripts} == {1, 2}
    assert {t["corpus_id"] for t in transcripts} == {"SYN-EX-001", "SYN-EX-002"}
    assert {read(p)["provider"] for p in (root / "transcripts").glob("*/*.json")} == {
        "anthropic",
        "openai",
        "google",
    }

    # Script votes, never grade/G1/count/score artifacts. The probe response rules
    # cover every type, including partial credit and zero credit for injection.
    weights = {q: ("core", "nuance") for q in criteria["questions"]}
    weights.update({q: v.weights for q, v in load_tuning_questions(TUNING_PATH).items()})
    rules = []
    for p in probes["probes"]:
        fields = _fields_for(p["expected_score"], p["expected_label"], weights[p["question_id"]])
        rules.append(
            {
                "match": "^" + re.escape(p["id"]) + "$",
                "output": {"explanation": "Scripted probe vote.", **_ALL_MET, **fields},
                "usage": {"input_tokens": 100, "output_tokens": 2},
            }
        )
    default = yaml.safe_load((FIX / "judge-haiku.yml").read_text())["rules"][0]
    split_id = safe_id("docs-fetch--scripted-openai--r1", "SYN-EX-001")
    split = {
        **default,
        "match": "^" + re.escape(split_id) + "$",
        "output": {**default["output"], "p2_met": False},
    }
    audit = {
        "match": "^SYN-",
        "output": {
            "explanation": "Scripted key audit against the fixture sections.",
            "key_supported": True,
        },
        "usage": {"input_tokens": 100, "output_tokens": 2},
    }
    scripts = {j: {"rules": [*rules, default]} for j in IDS}
    # With three valid votes, a 2:1 disagreement is a majority, not a split.
    # Withhold one vote so the two remaining p2 votes really tie.
    scripts["haiku"] = {
        "rules": [
            {**default, "match": split["match"], "refusal": True},
            *rules,
            default,
        ]
    }
    scripts["flash-lite"] = {"rules": [split, *rules, default]}
    scripts["reference"] = {"rules": [audit, *rules, default]}
    panel = panel_file(tmp_path, scripts)

    common = [
        "--panel",
        panel,
        "--prices",
        FIX / "prices.yml",
        "--criteria",
        criteria_path,
        "--ledger-dir",
        ledger,
        "--index",
        index,
    ]

    def grade(target, destination, *extra, sample=True):
        return invoke(
            "agentic-grade",
            "--target",
            target,
            *destination,
            *common,
            *(["--probes", probes_path] if sample else []),
            *extra,
        )

    build = tmp_path / "build"
    generated = grade(
        "probe-gen", ["--workspace", build], "--workspace-kind", "probe-build", sample=False
    )
    checked = grade("probe-check", ["--workspace", build], sample=False)
    assert generated["records_written"] == checked["records_written"] > 0
    built_path = score.assemble_workspace_probes(
        build,
        criteria_path=criteria_path,
        tuning_path=TUNING_PATH,
        panel_path=panel,
        index_path=index,
        corpus_path=corpus_path,
    )
    built = load_probe_file(built_path, criteria_path=criteria_path, tuning_path=TUNING_PATH)
    assert built.header["index_sha256"] == digest(index)
    assert any(p.built_by.get("generator") for p in built.probes)

    tuning = tmp_path / "tuning"
    for j in IDS:
        grade(
            "tuning",
            ["--workspace", tuning],
            "--workspace-kind",
            "tuning",
            "--judges",
            j,
            "--effort",
            "low",
        )
    invoke("agentic-score", "--workspace", tuning, "--step", "tuning", "--panel", panel)
    assert all(read(tuning / "tuning.json")["judges"][j]["chosen"] == "low" for j in IDS)
    held = tmp_path / "held"
    for pass_number in (1, 2):
        grade(
            "probes",
            ["--workspace", held],
            "--workspace-kind",
            "held-out",
            "--handoff-tuning",
            tuning / "tuning.json",
            "--pass",
            pass_number,
        )
    grade("reference-probes", ["--workspace", held])
    invoke("agentic-score", "--workspace", held, "--step", "g1", "--panel", panel)
    (entry,) = read(held / "g1.json")["entries"].values()
    # The sample exercises all 14 types on just two held-out questions.
    assert entry["gate"]["passed"] is None
    assert entry["reference_check"]["passed"] is None
    assert entry["gate"]["details"]["overall"]["accuracy"] == 1.0
    assert entry["reference_check"]["details"]["overall"]["n"] == 28
    assert entry["gate"]["details"]["overall"]["n"] == 28

    for pass_number in (1, 2):
        result = grade("answers", ["--run-dir", root], "--pass", pass_number)
        assert result["records_written"] == 108 and result["not_run"] == []
    grade("reference", ["--run-dir", root])
    grade("key-audit", ["--run-dir", root])
    user = read(root / "grading/items/key-audit/SYN-EX-001.json")["user"]
    assert "Serialize obj to a JSON formatted str" in user

    counts = tmp_path / "counts.yml"
    counts.write_text("{}\n")
    family = count.count_family(counts)
    monkeypatch.setattr(count, "count_family", lambda: family)
    ledger_before = {p: p.read_bytes() for p in ledger.glob("*") if p.is_file()}
    counted = invoke("agentic-count-tokens", "--run-dir", root)
    assert counted["records_written"] == 36 and counted["units"] == []
    assert ledger_before == {p: p.read_bytes() for p in ledger_before}
    summary = invoke(
        "agentic-score",
        "--run-dir",
        root,
        "--criteria",
        criteria_path,
        "--panel",
        panel,
        "--g1",
        held / "g1.json",
    )
    assert summary["graded_cells"] == 36 and summary["pending_cells"] == []
    assert summary["gates"]["G1"]["passed"] is None
    assert summary["gates"]["G3"]["passed"] is None  # fewer than 300 reference comparisons
    assert summary["tier"] != "Validated"
    assert set(summary["claims"]["C2"]) == {
        "scripted-anthropic",
        "scripted-openai",
        "scripted-google",
    }
    audit_votes = [read(p) for p in (root / "grading/votes/key-audit").rglob("*.json")]
    assert len(audit_votes) == 50
    assert {v["item_id"] for v in audit_votes} == {q["id"] for q in corpus["questions"]}
    assert all(v["status"] == "succeeded" and v["parsed"]["key_supported"] for v in audit_votes)
    assert summary["key_audit"] == {"verified": False, "audited": None, "flagged": None}
    records = [read(p) for p in (root / "scoring").glob("*/*.json")]
    assert len(records) == 36
    assert all(r["requires_manual_scoring"] is False for r in records)
    assert {r["scoring_method"] for r in records} == {"llm_panel", "llm_reference"}
    assert {r["score"] for r in records} == {1.0}
    split_record = read(root / "scoring/docs-fetch--scripted-openai--r1/SYN-EX-001.json")
    assert split_record["scoring_method"] == "llm_reference"
    assert split_record["grading"]["settled_fields"] == ["p2_met"]
    suspect = read(root / "scoring/no-docs--scripted-anthropic--r1/SYN-EX-002.json")
    assert suspect["scoring_method"] == "llm_reference"
    assert suspect["grading"]["injection_suspect"] is True
    tokens = [read(p) for p in (root / "tokens").glob("*/*.json")]
    assert len(tokens) == 36 and {t["status"] for t in tokens} == {"counted"}
    assert all(t["client_wrapped_tokens"] > 0 for t in tokens)
    assert all(
        t["execution"] == {"provider": "scripted", "api": "scripted", "model_id": "bytes/4"}
        and t["approximation"] is True
        and t["token_label"] == "Scripted token estimate"
        for t in tokens
    )

    # Report through the real dispatch, using the run's own matrix snapshot.
    assert (
        cli.main(
            [
                "report",
                "--run-dir",
                str(root),
                "--model-matrix",
                str(matrix),
                "--methodology",
                str(METHODOLOGY_PATH),
            ]
        )
        == 0
    )
    report = (root / "REPORT.md").read_text()
    assert "demonstration" in report.lower()
    assert REQUIRED_WORDING not in report
    assert "Correctness was graded by three LLMs" not in report
    assert "hosted batch APIs" not in report
    assert not any(p in report.lower() for p in FORBIDDEN_PHRASES)
    assert "no human graded or calibrated any grade" in report.lower()
    assert "not evaluable" in report
    token_section = report.split("## Normalized tokens\n", 1)[1].split("\n## ", 1)[0]
    assert "no-docs" not in token_section and "uncounted" not in token_section
    assert "bytes/4" in token_section and "claude-sonnet-5-5" not in token_section
    assert "not comparable with any earlier study that used another count model" in token_section
    for model in summary["claims"]["C2"]:
        for system in ("python-docs", "docs-fetch"):
            total = sum(
                t["client_wrapped_tokens"]
                for t in tokens
                if t["competitor_id"].startswith(f"{system}--{model}--")
            )
            assert f"| {model} | {system} | {total} |" in token_section
    assert "grading/inputs/" in report and "grading/disclosure/" in report
    assert (
        f"No public claim: grading outcome is {summary['tier']}."
        in (root / "README-SUMMARY.md").read_text()
    )
    for artifact_root in (root, build, tuning, held, ledger):
        for path in artifact_root.rglob("*"):
            if path.is_file():
                assert KEY_SHAPED.search(path.read_text(errors="replace")) is None, path
