"""Task 8 golden paths use scripted worker subprocesses and a Docker test double."""

import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from benchmarks import __main__ as cli
from benchmarks.agentic.host import LocalLauncher
from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic.run import run_agentic
from benchmarks.grading import inputs, run, score
from benchmarks.grading.aggregate import chosen_record, final_grade, load_answer_votes
from benchmarks.grading.gates import BINDING_INPUTS
from benchmarks.grading.inputs import safe_id
from benchmarks.grading.panel import load_panel
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_run import SAMPLE_CORPUS
from tests.benchmarks.test_agentic_run import _config as answering_config
from tests.benchmarks.test_agentic_sandbox import IMAGE, FakeDocker, Probes, _verified
from tests.benchmarks.test_grading_jobs import FREE, PAID, RESERVE, ScriptLauncher, StubLauncher
from tests.benchmarks.test_grading_probes import index_db as index_db

FIX = Path(__file__).parent / "fixtures" / "grading"
PANEL = FIX / "panel.yml"
PROBES = FIX / "probes.sample.yml"
CRITERIA = FIX / "criteria.sample.yml"
IDS = ("haiku", "luna", "flash-lite")


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config(base, target="answers", **kwargs):
    return replace(
        run.GradingConfig(
            target,
            base / "run",
            base / "ledger",
            PANEL,
            FIX / "prices.yml",
            CRITERIA,
            inputs.TUNING_PATH,
            probes_path=PROBES,
        ),
        **kwargs,
    )


@pytest.fixture
def scripted_run(tmp_path):
    run_agentic(
        answering_config(
            tmp_path,
            systems=("no-docs",),
            models=("scripted-anthropic",),
            repeats=1,
            ledger_dir=tmp_path / "answer-ledger",
        )
    )
    return tmp_path / "run"


def panel_file(base, scripts=None, *, live=False):
    data = yaml.safe_load(PANEL.read_text())
    for name, judge in data["judges"].items():
        judge["script"] = str(FIX / judge["script"])
        if scripts and name in scripts:
            path = base / f"{name}.yml"
            path.write_text(yaml.safe_dump(scripts[name]))
            judge["script"] = str(path)
    if live:
        data["judges"]["luna"]["api"] = "openai-responses"
    path = base / "panel.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def cli_args(cfg, *extra):
    return [
        "agentic-grade",
        "--target",
        cfg.target,
        "--run-dir",
        str(cfg.out_dir),
        "--panel",
        str(cfg.panel_path),
        "--prices",
        str(cfg.prices_path),
        "--criteria",
        str(cfg.criteria_path),
        "--probes",
        str(cfg.probes_path),
        "--ledger-dir",
        str(cfg.ledger_dir),
        *extra,
    ]


def test_dry_run_plans_batches_and_spends_nothing(scripted_run):
    cfg = config(scripted_run.parent, panel_path=panel_file(scripted_run.parent, live=True))
    plan = run.run_grading(cfg)
    assert plan["dry_run"] and plan["total_bound"] > 0
    assert plan["judges"]["luna"]["items"] == 2
    assert not cfg.ledger_dir.exists()
    assert read(scripted_run / "grading/plans/answers-pass1.json")["dry_run"]


@pytest.mark.parametrize("amount", [None, Decimal(".01"), Decimal("NaN"), Decimal("Infinity")])
def test_live_requires_a_confirmation_at_or_above_the_planned_bound(scripted_run, amount):
    with pytest.raises(BenchmarkValidationError, match="--confirm-max-spend"):
        run.run_grading(config(scripted_run.parent, live=True, confirm_max_spend=amount))
    assert not (scripted_run.parent / "ledger").exists()


def test_live_grading_passes_pr4s_authorization_gate(scripted_run, monkeypatch):
    base = scripted_run.parent
    cfg = config(
        base,
        live=True,
        panel_path=panel_file(base, live=True),
        grading_run_id="blocked",
        confirm_max_spend=Decimal(10),
    )
    docker = FakeDocker()
    args = cli._build_parser().parse_args(cli_args(cfg, "--image", IMAGE))
    launcher = cli._container_launcher(
        args, "blocked", scripted_run / "grading/sandbox/blocked", docker=docker, verify=_verified
    )
    monkeypatch.setenv("CI", "1")
    with pytest.raises(BenchmarkValidationError, match="CI"):
        run.run_grading(cfg, launcher=launcher, confirm=lambda _: pytest.fail("confirmed in CI"))
    assert docker.calls == []
    assert read(scripted_run / "grading/runs/blocked.json")["status"] == "aborted"


def test_cli_launcher_flags_match_agentic_run(scripted_run, capsys):
    cfg = config(scripted_run.parent)
    with pytest.raises(SystemExit) as exc:
        cli.main(cli_args(cfg, "--live"))
    assert exc.value.code == 2
    assert cli.main(cli_args(cfg, "--launcher", "container", "--image", IMAGE)) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] and not cfg.ledger_dir.exists()


def test_cli_container_launches_keep_each_grading_run_in_its_own_sandbox_dir(
    scripted_run, monkeypatch
):
    cfg = config(scripted_run.parent)
    original = cli._container_launcher
    seen = []
    c1 = ["egress/summary.json", "probe/control.json", "sandbox.json"]
    for name in c1:
        path = scripted_run / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"answering evidence")

    def wrap(args, run_id, out_dir):
        seen.append(out_dir)

        class ProxyProbes(Probes):
            async def __call__(self, sandbox, egress, **kwargs):
                for name in ("access.log", "cache.log"):
                    (egress.spool_dir / name).touch()
                return await super().__call__(sandbox, egress, **kwargs)

        probes = ProxyProbes()
        launcher = original(
            args, run_id, out_dir, docker=FakeDocker(), verify=_verified, probe=probes
        )
        launcher._control = probes.control
        launcher.command = LocalLauncher().command
        return launcher

    monkeypatch.setattr(cli, "_container_launcher", wrap)
    saved = {}
    for n in (1, 2):
        assert (
            cli.main(
                cli_args(
                    cfg,
                    "--launcher",
                    "container",
                    "--image",
                    IMAGE,
                    "--live",
                    "--confirm-max-spend",
                    "1000",
                    "--pass",
                    str(n),
                    "--grading-run-id",
                    f"g{n}",
                )
            )
            == 0
        )
        assert seen[-1] == scripted_run / f"grading/sandbox/g{n}"
        for judge in IDS:
            for name in ("squid.conf", "collected.json"):
                path = seen[-1] / "egress" / f"{judge}--grading" / name
                saved[path] = path.read_bytes()
    assert all(path.read_bytes() == body for path, body in saved.items())
    assert all((scripted_run / name).read_bytes() == b"answering evidence" for name in c1)


def test_run_targets_need_the_probes_file_and_pin_its_hash(scripted_run, monkeypatch):
    monkeypatch.setattr(run, "PROBES_PATH", scripted_run / "absent.yml")
    cfg = config(scripted_run.parent)
    with pytest.raises(BenchmarkValidationError, match="absent.yml"):
        run.run_grading(replace(cfg, probes_path=None))
    run.run_grading(cfg)
    assert read(scripted_run / "grading/inputs/inputs.json")["probes"] == digest(PROBES)


def test_a_reused_grading_run_id_is_refused_before_the_ledger_opens(scripted_run):
    path = scripted_run / "grading/runs/used.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    cfg = config(scripted_run.parent, grading_run_id="used")
    with pytest.raises(BenchmarkValidationError, match="used"):
        run.run_grading(cfg)
    assert path.read_bytes() == b"original" and not cfg.ledger_dir.exists()


def test_a_live_run_refuses_an_expired_snapshot(scripted_run):
    base = scripted_run.parent
    prices = base / "stale.yml"
    prices.write_text(
        (FIX / "prices.yml").read_text().replace("expires_on: null", 'expires_on: "2026-11-05"')
    )
    cfg = config(base, live=True, confirm_max_spend=Decimal(10), prices_path=prices)
    with pytest.raises(BenchmarkValidationError, match="expires"):
        run.run_grading(
            replace(cfg, panel_path=panel_file(base, live=True)), today=date(2027, 1, 1)
        )
    assert not cfg.ledger_dir.exists()


def test_a_host_holding_a_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-marker")
    with pytest.raises(BenchmarkValidationError, match="host must never"):
        run.run_grading(config(tmp_path))
    assert not (tmp_path / "run").exists()


def test_scripted_panel_grades_a_scripted_run(scripted_run):
    cfg = config(scripted_run.parent)
    summary = run.run_grading(cfg)
    root = scripted_run / "grading"
    assert summary["items"] == 2 and summary["records_written"] == 6
    assert summary["not_run"] == []
    for path in (root / "items/answers").glob("*.json"):
        for judge in IDS:
            rec = chosen_record(
                root,
                target="answers",
                judge_id=judge,
                effort="low",
                pass_number=1,
                item_id=path.stem,
            )
            assert rec["vote"] and rec["input_sha256"] == read(path)["meta"]["input_sha256"]
    reserves = [
        e
        for e in map(json.loads, (root / "ledger-events.jsonl").read_text().splitlines())
        if e["event"] == "reserve"
    ]
    assert {e["job_id"] for e in reserves} == {f"{j}--grading" for j in IDS}
    assert sum(Decimal(e["bound"]) for e in reserves) == Decimal(str(summary["total_bound"]))


def test_exhausted_items_are_terminal_then_reference_settles_and_a_rerun_resubmits_nothing(
    scripted_run,
):
    base = scripted_run.parent
    flash = yaml.safe_load((FIX / "judge-flash-lite.yml").read_text())
    flash["rules"][0]["output"]["p2_met"] = False
    panel = panel_file(
        base,
        {
            "luna": {"submit_errors": [FREE, PAID, FREE], "rules": [{"match": ".*"}]},
            "flash-lite": flash,
        },
    )
    cfg = config(base, panel_path=panel)
    summary = run.run_grading(cfg, launcher=ScriptLauncher())
    assert len(summary["not_run"]) == 2
    assert all(e["attempt"] == 3 and e["judge_id"] == "luna" for e in summary["not_run"])
    run.run_grading(replace(cfg, target="reference"))
    votes = load_answer_votes(
        scripted_run / "grading", panel=load_panel(panel), judges=dict.fromkeys(IDS, "low")
    )
    questions = inputs.load_criteria(
        CRITERIA, corpus_path=SAMPLE_CORPUS, schema_path=score.CORPUS_SCHEMA_PATH
    )
    for v in votes.values():
        assert v.records["luna"]["terminal"] and v.records["luna"]["attempts"] == 3
        assert final_grade(v, questions[v.corpus_id], inputs={})["grading"]["settled_fields"] == [
            "p2_met"
        ]
    again = run.run_grading(cfg)
    assert again["records_written"] == 0 and again["units"] == []


def test_a_rerun_skips_items_that_already_have_a_final_vote(scripted_run):
    base = scripted_run.parent
    cfg = config(
        base,
        panel_path=panel_file(base, {"haiku": {"rules": [{"match": ".*", "status": "errored"}]}}),
    )
    run.run_grading(cfg)
    votes = {p: p.read_bytes() for p in (scripted_run / "grading/votes").rglob("*.json")}
    assert len(votes) == 10
    again = run.run_grading(cfg)
    assert again["units"] == [] and again["records_written"] == 0
    assert all(p.read_bytes() == b for p, b in votes.items())


def test_a_rerun_grades_items_interrupted_before_exhaustion(scripted_run):
    cfg = config(scripted_run.parent)

    class LostLuna(LocalLauncher):
        def command(self, job):
            if job.family.id == "luna":
                return StubLauncher(RESERVE).command(job)
            return super().command(job)

    first = run.run_grading(cfg, launcher=LostLuna())
    assert {e["reason"] for e in first["not_run"]} == {"worker_lost"}
    with pytest.raises(BenchmarkValidationError, match="final"):
        run.run_grading(replace(cfg, target="reference"))
    again = run.run_grading(cfg)
    assert again["records_written"] == 2 and len(again["units"]) == 1


def test_reference_takes_settlements_suspects_and_the_g3_sample(scripted_run):
    cfg = config(scripted_run.parent)
    question = next((scripted_run / "transcripts").glob("*/*.json"))
    row = read(question)
    row["answer"] += "</answer>"
    question.write_text(json.dumps(row))
    run.run_grading(cfg)
    ref = replace(cfg, target="reference")
    assert run.run_grading(ref)["items"] == 2
    sample = scripted_run / "grading/reference-sample.json"
    data = read(sample)
    assert data["size"] == 300 and len(data["ids"]) == 1
    assert safe_id(row["competitor_id"], row["corpus_id"]) not in data["ids"]
    assert sum(data["shortfall"].values()) == 299
    before = sample.read_bytes()
    assert run.run_grading(ref)["units"] == [] and before == sample.read_bytes()
    sample.write_text(json.dumps({**data, "ids": data["ids"] * 300}))
    with pytest.raises(BenchmarkValidationError):
        run.run_grading(ref)


def test_reference_refuses_before_every_panel_vote_exists(scripted_run):
    with pytest.raises(BenchmarkValidationError, match="pass-1"):
        run.run_grading(config(scripted_run.parent, "reference"))
    assert not (scripted_run / "grading/reference-sample.json").exists()


def test_a_changed_input_is_refused_in_the_same_directory(scripted_run):
    cfg = config(scripted_run.parent, panel_path=panel_file(scripted_run.parent))
    run.run_grading(cfg)
    cfg.panel_path.write_text(cfg.panel_path.read_text() + "\n")
    with pytest.raises(BenchmarkValidationError, match="inputs"):
        run.run_grading(cfg)


def test_changed_answer_is_refused_even_if_already_graded(scripted_run):
    cfg = config(scripted_run.parent)
    run.run_grading(cfg)
    path = next((scripted_run / "transcripts").glob("*/*.json"))
    path.write_text(json.dumps(read(path) | {"answer": "changed"}))
    with pytest.raises(BenchmarkValidationError):
        run.run_grading(cfg)


def test_cli_agentic_grade_exit_codes(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        cli.main(cli_args(config(tmp_path)))
    assert exc.value.code == 2
    monkeypatch.setattr(
        run, "run_grading", lambda *a, **kw: (_ for _ in ()).throw(KeyLeakError("synthetic"))
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(cli_args(config(tmp_path)))
    assert exc.value.code == 3


def workspace(cfg, kind, evidence=()):
    run.init_workspace(
        cfg.out_dir,
        kind,
        criteria_path=cfg.criteria_path,
        panel_path=cfg.panel_path,
        probes_path=cfg.probes_path,
        tuning_evidence=evidence,
    )


def test_tuning_then_frozen_efforts_then_g1_with_one_prompt_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "CORPUS_PATH", SAMPLE_CORPUS)
    probe_file = tmp_path / "probes.yml"
    data = yaml.safe_load(PROBES.read_text())
    data["probes"] = [p for p in data["probes"] if p["type"] == "KEY"]
    probe_file.write_text(yaml.safe_dump(data))
    cfg = config(
        tmp_path,
        "tuning",
        out_dir=tmp_path / "t1",
        probes_path=probe_file,
        judges=("haiku",),
        effort="low",
    )
    workspace(cfg, "tuning")
    run.run_grading(cfg)
    revised = tmp_path / "prompt.md"
    revised.write_text(inputs.judge_prompt() + "\nRevised.\n")
    monkeypatch.setattr(inputs, "JUDGE_PROMPT_PATH", revised)
    with pytest.raises(BenchmarkValidationError, match="inputs"):
        run.run_grading(cfg)
    cfg = replace(cfg, out_dir=tmp_path / "t2")
    workspace(cfg, "tuning")
    for judge in IDS:
        run.run_grading(replace(cfg, judges=(judge,)))
    evidence = score.write_tuning(cfg.out_dir, panel_path=PANEL)
    with pytest.raises(BenchmarkValidationError, match="workspace"):
        run.run_grading(replace(cfg, target="probes", judges=None, effort=None))
    held = replace(cfg, out_dir=tmp_path / "held", target="probes", judges=None, effort=None)
    wrong = tmp_path / "wrong.yml"
    wrong.write_text(PANEL.read_text().replace("effort: low", "effort: medium", 1))
    with pytest.raises(BenchmarkValidationError, match="effort"):
        workspace(replace(held, panel_path=wrong), "held-out", [evidence])
    workspace(held, "held-out", [evidence])
    assert (held.out_dir / "inputs/corpus.yml").read_bytes() == SAMPLE_CORPUS.read_bytes()
    # New held-out workspaces replay from the retained document even after relocation.
    monkeypatch.setattr(run, "CORPUS_PATH", tmp_path / "absent-source-corpus.yml")
    run.run_grading(held)
    run.run_grading(replace(held, pass_number=2))
    run.run_grading(replace(held, target="reference-probes"))
    [entry] = read(score.write_g1(held.out_dir, panel_path=PANEL))["entries"].values()
    repeats = entry["gate"]["details"]["held_out_test_retest"]
    assert set(repeats) == set(IDS)
    for metric in repeats.values():
        assert metric["items"] == metric["pass2_records"] == metric["expected"] == 2
        assert metric["alpha"] is None  # These two KEY probes have identical grades.
    assert entry["binding"]["inputs"]["judge_prompt"] == digest(revised)
    assert entry["binding"]["inputs"]["probes"] == digest(probe_file)
    assert set(entry["binding"]["inputs"]) == set(BINDING_INPUTS)
    assert {v["effort"] for v in entry["binding"]["composition"].values()} == {"low"}
    assert entry["gate"]["passed"] is None
    assert entry["reference_check"]["passed"] is None
    assert entry["reference_check"]["details"]["overall"]["n"] == 2
    # A genuine stage-produced record cannot certify a contradictory raw reply.
    export = (held.out_dir / "g1.json").read_bytes()
    (vote_path,) = (held.out_dir / "votes/probes/haiku/low/pass1/KEY--SYN-EX-001").glob("*.json")
    original = vote_path.read_bytes()
    record = read(vote_path)
    reply = json.loads(record["text"])
    reply["attempted"] = False
    record["text"] = json.dumps(reply)
    vote_path.write_text(json.dumps(record))
    receipt = held.out_dir / "executions" / record["grading_run_id"] / "haiku" / (
        f"{record['item_id']}--a{record['attempt']}.json"
    )
    assert read(receipt)["vote_sha256"] == hashlib.sha256(original).hexdigest()
    assert read(receipt)["vote_sha256"] != digest(vote_path)
    with pytest.raises(BenchmarkValidationError, match="vote inventory receipt binding differs"):
        score.write_g1(held.out_dir, panel_path=PANEL)
    assert (held.out_dir / "g1.json").read_bytes() == export
    vote_path.write_bytes(original)
    with pytest.raises(BenchmarkValidationError, match="workspace"):
        run.run_grading(replace(cfg, out_dir=held.out_dir))


@pytest.mark.parametrize("judges,effort", [(None, "low"), (IDS, "low"), (("haiku",), None)])
def test_tuning_needs_one_judge_and_an_effort(tmp_path, judges, effort):
    cfg = config(tmp_path, "tuning", out_dir=tmp_path / "ws", judges=judges, effort=effort)
    workspace(cfg, "tuning")
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        run.run_grading(cfg)


def test_held_out_targets_refuse_unfrozen_efforts(scripted_run):
    panel = scripted_run.parent / "panel.yml"
    panel.write_text(PANEL.read_text().replace("effort: low", "effort: null", 1))
    with pytest.raises(BenchmarkValidationError, match="effort"):
        run.run_grading(config(scripted_run.parent, panel_path=panel))


def test_probe_gen_round_2_regenerates_only_failed_checks(tmp_path, monkeypatch, index_db):
    monkeypatch.setattr(run, "CORPUS_PATH", SAMPLE_CORPUS)
    script = yaml.safe_load((FIX / "judge-sol.yml").read_text())
    script["rules"].insert(
        0,
        {
            "match": "^chk--PARA--SYN-EX-001--r1$",
            "output": {"meets_spec": False, "explanation": "wrong"},
        },
    )
    panel = panel_file(tmp_path, {"sol": script})
    cfg = config(
        tmp_path, "probe-gen", out_dir=tmp_path / "build", panel_path=panel, probes_path=None
    )
    workspace(cfg, "probe-build")
    first = run.run_grading(cfg)
    assert first["records_written"] > 7
    run.run_grading(replace(cfg, target="probe-check"))
    checker = next((cfg.out_dir / "votes/probe-check").glob(
        "*/*/pass1/chk--PARA--SYN-EX-001--r1/*.json"))
    original = checker.read_bytes()
    record = read(checker)
    assert record["parsed"]["meets_spec"] is False
    record["parsed"]["meets_spec"] = True
    checker.write_text(json.dumps(record))
    try:
        with pytest.raises(BenchmarkValidationError):
            run.build_items(replace(cfg, round_number=2), load_panel(panel))
    finally:
        checker.write_bytes(original)
    again = run.run_grading(replace(cfg, round_number=2))
    assert again["records_written"] == 1
    assert (cfg.out_dir / "items/probe-gen/gen--PARA--SYN-EX-001--r2.json").is_file()
    run.run_grading(replace(cfg, target="probe-check", round_number=2))
    def assemble():
        return score.assemble_workspace_probes(
            cfg.out_dir, criteria_path=cfg.criteria_path, tuning_path=cfg.tuning_path,
            panel_path=panel, index_path=index_db, corpus_path=SAMPLE_CORPUS,
        )
    suite = yaml.safe_load(assemble().read_bytes())
    para = next(p for p in suite["probes"] if p["id"] == "PARA--SYN-EX-001")
    assert para["built_by"]["round"] == 2
    assert len(para["built_by"]["failed_checks"]) == 1
    export = assemble().read_bytes()
    # These are ordinary recorded producer receipts, not invented attestations.
    selected = next((cfg.out_dir / "votes/probe-check").glob(
        "*/*/pass1/chk--PARA--SYN-EX-001--r2/*.json"))
    # Rebind ordinary unsigned digests after each cached mutation: the raw
    # reply/schema comparison must still reject it before replacing the export.
    from benchmarks.grading import aggregate
    from tests.benchmarks.test_grading_refusal_integrity import _receipt

    # Resolve coordinates from this selected round, rather than round one's record.
    record = read(selected)
    receipt_relative = f"{record['judge_id']}/{record['item_id']}--a{record['attempt']}.json"
    history_path = cfg.out_dir / "executions" / record["grading_run_id"] / receipt_relative
    saved_inv = (cfg.out_dir / "runs" / (record["grading_run_id"] + ".json")).read_bytes()
    saved_history = history_path.read_bytes()
    # One selected vote and receipt snapshot per assembly.
    reads = []
    original_read = aggregate.artifact_bytes
    def capture(root, path):
        reads.append(path)
        return original_read(root, path)
    with monkeypatch.context() as scope:
        scope.setattr(aggregate, "artifact_bytes", capture)
        assert assemble().read_bytes() == export
    assert reads.count(selected) == 1
    assert reads.count(history_path) == 1
    generated = next((cfg.out_dir / "votes/probe-gen").glob(
        "*/*/pass1/gen--PARA--SYN-EX-001--r2/*.json"))
    for path, key, value in ((selected, "meets_spec", False),
                             (generated, "probe_text", "altered cached candidate")):
        saved = path.read_bytes()
        current = read(path)
        current["parsed"][key] = value
        path.write_text(json.dumps(current))
        run_path = cfg.out_dir / "runs" / (current["grading_run_id"] + ".json")
        saved_run = run_path.read_bytes()
        relative = f"{current['judge_id']}/{current['item_id']}--a{current['attempt']}.json"
        execution = cfg.out_dir / "executions" / current["grading_run_id"] / relative
        saved_execution = execution.read_bytes()
        result = run.BatchItemResult(**read(execution)["result"])
        try:
            _receipt(cfg.out_dir, path, result, panel=load_panel(panel))
            with pytest.raises(BenchmarkValidationError, match="stored parsed differs"):
                assemble()
            assert (cfg.out_dir / "probes.yml").read_bytes() == export
        finally:
            path.write_bytes(saved)
            run_path.write_bytes(saved_run)
            execution.write_bytes(saved_execution)
    assert history_path.read_bytes() == saved_history
    assert (cfg.out_dir / "runs" / (record["grading_run_id"] + ".json")).read_bytes() == saved_inv
    invocation = cfg.out_dir / "runs" / (record["grading_run_id"] + ".json")
    saved_invocation = invocation.read_bytes()
    evidence = read(invocation)
    evidence["execution_results"] = {}
    invocation.write_text(json.dumps(evidence))
    try:
        with pytest.raises(BenchmarkValidationError, match="receipt"):
            assemble()
        assert (cfg.out_dir / "probes.yml").read_bytes() == export
        evidence.pop("execution_results")
        invocation.write_text(json.dumps(evidence))
        # Supported old producer archives retain exact item requests and raw text.
        assert assemble().read_bytes() == export
    finally:
        invocation.write_bytes(saved_invocation)


@pytest.mark.parametrize("kind", ["probes", "tuning"])
def test_raw_handoff_scans_shadowed_and_escaped_strings_before_publication(tmp_path, kind):
    key = "sk-ant-" + "A" * 40
    encoded = '"' + r"\u0073" + key[1:] + '"'
    cfg = config(tmp_path, "tuning", out_dir=tmp_path / "ws")
    if kind == "probes":
        source = tmp_path / "probes.yml"
        source.write_text(PROBES.read_text() + "\nignored: " + encoded + "\nignored: clean\n")
        cfg = replace(cfg, probes_path=source)
        evidence = []
    else:
        source = tmp_path / "tuning.json"
        source.write_text('{"ignored":' + encoded + ',"ignored":"clean"}')
        evidence = [source]
    with pytest.raises(KeyLeakError):
        workspace(cfg, "held-out" if evidence else "tuning", evidence)
    assert not cfg.out_dir.exists()


def test_key_audit_reads_citations_and_stores_the_parsed_verdict(scripted_run, monkeypatch):
    from benchmarks.grading.probes import CitedSection

    monkeypatch.setattr(
        run,
        "cited_sections",
        lambda q, p: [
            CitedSection(url, q.version.split(" and ")[0], "page", None, "heading", "Z" * 25000)
            for url in q.citations
        ],
    )
    script = {
        "rules": [
            {
                "match": ".*",
                "output": {"explanation": "supported", "key_supported": True},
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ]
    }
    cfg = config(
        scripted_run.parent,
        "key-audit",
        panel_path=panel_file(scripted_run.parent, {"reference": script}),
        index_path=scripted_run / "index.db",
    )
    cfg.index_path.touch()
    run.run_grading(cfg)
    root = scripted_run / "grading"
    rec = chosen_record(
        root,
        target="key-audit",
        judge_id="reference",
        effort="high",
        pass_number=1,
        item_id="SYN-EX-001",
    )
    assert rec["vote"] is None and rec["parsed"]["key_supported"]
    user = read(root / "items/key-audit/SYN-EX-001.json")["user"]
    assert 0 < user.count("Z") <= 20000


def test_malformed_probe_handoff_is_refused_before_workspace_publication(tmp_path):
    cfg = config(tmp_path, "tuning", out_dir=tmp_path / "ws", probes_path=tmp_path / "bad.data")
    cfg.probes_path.write_text("schema_version: 1\nprobes: definitely-wrong\n")
    with pytest.raises(BenchmarkValidationError, match="probe"):
        workspace(cfg, "tuning")
    assert not cfg.out_dir.exists()


def test_probe_scan_uses_the_handoff_format_even_without_a_yaml_suffix(tmp_path):
    cfg = config(tmp_path, "tuning", out_dir=tmp_path / "ws", probes_path=tmp_path / "probes.data")
    cfg.probes_path.write_text(
        PROBES.read_text() + '\nignored: "' + r"\u0073" + "k-" + "X" * 42 + '"\n'
    )
    with pytest.raises(KeyLeakError):
        workspace(cfg, "tuning")
    assert not cfg.out_dir.exists()


def test_a_linked_item_directory_is_refused_before_any_evidence_is_published(scripted_run):
    root = scripted_run / "grading"
    outside = scripted_run.parent / "external"
    outside.mkdir()
    (root / "items").mkdir(parents=True)
    (root / "items/answers").symlink_to(outside, target_is_directory=True)
    with pytest.raises(BenchmarkValidationError):
        run.run_grading(config(scripted_run.parent))
    assert not list(outside.iterdir())


def test_retry_does_not_publish_a_changed_reference_input(scripted_run):
    cfg = config(scripted_run.parent)
    run.run_grading(cfg)
    path = next((scripted_run / "transcripts").glob("*/*.json"))
    original = path.read_bytes()
    path.write_text(json.dumps(read(path) | {"answer": "out-of-band edit"}))
    with pytest.raises(BenchmarkValidationError):
        run.run_grading(replace(cfg, target="reference"))
    assert not list((scripted_run / "grading/items/reference").glob("*.json"))
    path.write_bytes(original)
    assert run.run_grading(replace(cfg, target="reference"))["records_written"] == 2


def test_probe_build_rejects_changed_tuning_questions(tmp_path):
    cfg = config(
        tmp_path,
        "probe-gen",
        out_dir=tmp_path / "ws",
        criteria_path=inputs.CRITERIA_PATH,
        probes_path=None,
        tuning_path=tmp_path / "questions.yml",
    )
    cfg.tuning_path.write_text(inputs.TUNING_PATH.read_text())
    run.init_workspace(
        cfg.out_dir,
        "probe-build",
        criteria_path=cfg.criteria_path,
        panel_path=cfg.panel_path,
        probes_path=None,
        tuning_path=cfg.tuning_path,
    )
    cfg.tuning_path.write_text(cfg.tuning_path.read_text() + "\n")
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        run.plan_grading(cfg)


def test_paid_retries_cannot_start_a_unit_beyond_confirmed_spend(scripted_run):
    script = {"submit_errors": [PAID], "rules": [{"match": ".*", "text": "ok"}]}
    cfg = config(
        scripted_run.parent, panel_path=panel_file(scripted_run.parent, dict.fromkeys(IDS, script))
    )
    approval = sum(Decimal(v) for v in run.plan_grading(cfg)["provider_bounds"].values())
    summary = run.run_grading(
        replace(cfg, live=True, confirm_max_spend=approval), launcher=ScriptLauncher()
    )
    held = {}
    spent = Decimal(0)
    for e in map(
        json.loads, (scripted_run / "grading/ledger-events.jsonl").read_text().splitlines()
    ):
        if e["event"] == "reserve":
            held[e["unit_id"]] = Decimal(e["bound"])
            assert spent + sum(held.values()) <= approval
        if e["event"] == "settle":
            held.pop(e["unit_id"])
            spent += Decimal(e["booked"])
    assert "budget_stop" in {e["reason"] for e in summary["not_run"]}


def test_stale_prices_do_not_block_scripted_live_isolation(scripted_run):
    cfg = config(scripted_run.parent, live=True, confirm_max_spend=Decimal(20))
    path = scripted_run.parent / "stale.yml"
    path.write_text(
        cfg.prices_path.read_text().replace("expires_on: null", 'expires_on: "2026-11-05"')
    )

    def refuse_confirm(_):
        pytest.fail("scripted jobs must not ask for live authorization")

    assert (
        run.run_grading(
            replace(cfg, prices_path=path), today=date(2027, 1, 1), confirm=refuse_confirm
        )["records_written"]
        == 6
    )


def test_the_free_and_whole_batch_stops_are_per_job_and_still_block_reference(scripted_run):
    panel = panel_file(
        scripted_run.parent,
        {
            "luna": {"submit_errors": [FREE] * 3, "rules": [{"match": ".*"}]},
            "flash-lite": {"rules": [{"match": ".*", "status": "missing"}]},
        },
    )
    cfg = config(scripted_run.parent, panel_path=panel)
    first = run.run_grading(cfg, launcher=ScriptLauncher())
    assert {(e["judge_id"], e["reason"], e["attempt"]) for e in first["not_run"]} == {
        ("luna", "submit_rejected", 3),
        ("flash-lite", "whole_batch_failures", 2),
    }
    assert all(
        not rec["terminal"] for rec in map(read, (scripted_run / "grading/votes").rglob("*.json"))
    )
    with pytest.raises(BenchmarkValidationError, match="final"):
        run.run_grading(replace(cfg, target="reference"))
    for j in ("luna", "flash-lite"):
        (scripted_run.parent / f"{j}.yml").write_bytes((FIX / f"judge-{j}.yml").read_bytes())
    assert run.run_grading(cfg)["records_written"] == 4
    assert run.run_grading(replace(cfg, target="reference"))["records_written"] == 2


def test_g1_composition_key_uses_provider_order_and_pins_fallback(scripted_run):
    cfg = config(scripted_run.parent, judges=("luna", "flash", "haiku"))
    run.run_grading(cfg)
    binding = score.run_binding(scripted_run)
    assert tuple(binding.composition) == ("haiku", "luna", "flash")
    assert binding.inputs["probes"] == digest(PROBES)
    votes = list((scripted_run / "grading/votes/answers").iterdir())
    assert {p.name for p in votes} == {"haiku", "luna", "flash"}
    with pytest.raises(BenchmarkValidationError, match="composition"):
        run.run_grading(replace(cfg, pass_number=2, judges=None))
    assert run.run_grading(replace(cfg, target="reference", judges=None))["records_written"] == 2


def test_panel_change_while_loading_cannot_freeze_different_judge_settings(
    scripted_run, monkeypatch
):
    cfg = config(scripted_run.parent, panel_path=panel_file(scripted_run.parent, live=True))
    original = run.load_panel

    def change_after_read(path):
        panel = original(path)
        path.write_text(path.read_text().replace("effort: low", "effort: medium", 1))
        return panel

    monkeypatch.setattr(run, "load_panel", change_after_read)
    with pytest.raises(BenchmarkValidationError, match="inputs"):
        run.run_grading(cfg)
    assert not cfg.ledger_dir.exists()


@pytest.mark.parametrize("bad_probe", ["malformed", None, {"id": "KEY--TUNE-001"}])
def test_invalid_probe_entries_are_cli_validation_errors(tmp_path, bad_probe):
    source = tmp_path / "probes.yml"
    data = yaml.safe_load(PROBES.read_text())
    data["probes"] = [bad_probe]
    source.write_text(yaml.safe_dump(data))
    args = [
        "agentic-grade",
        "--target",
        "tuning",
        "--workspace",
        str(tmp_path / "workspace"),
        "--workspace-kind",
        "tuning",
        "--panel",
        str(PANEL),
        "--criteria",
        str(CRITERIA),
        "--probes",
        str(source),
        "--judges",
        "haiku",
        "--effort",
        "low",
        "--ledger-dir",
        str(tmp_path / "ledger"),
    ]
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    assert not (tmp_path / "workspace").exists()
    assert not (tmp_path / "ledger").exists()


def test_malformed_key_audit_index_is_a_cli_validation_error(scripted_run):
    index = scripted_run.parent / "broken.db"
    index.write_bytes(b"not sqlite")
    corpus = scripted_run / "snapshots/corpus.yml"
    data = yaml.safe_load(corpus.read_text())
    data["questions"][0]["citations"] = [
        "https://docs.python.org/3.13/library/synthetic.html#synthetic"
    ]
    corpus.write_text(yaml.safe_dump(data))
    criteria = scripted_run.parent / "criteria.yml"
    criteria.write_text(CRITERIA.read_text().replace(digest(SAMPLE_CORPUS), digest(corpus)))
    cfg = config(scripted_run.parent, "key-audit", criteria_path=criteria)
    with pytest.raises(SystemExit) as exc:
        cli.main(cli_args(cfg, "--index", str(index)))
    assert exc.value.code == 2
    assert not cfg.ledger_dir.exists()


def test_malformed_tuning_handoff_is_a_cli_validation_error(tmp_path):
    manifest = inputs.inputs_manifest(criteria_path=CRITERIA, panel_path=PANEL, probes_path=PROBES)
    evidence = tmp_path / "tuning.json"
    evidence.write_text(
        json.dumps(
            {"inputs": manifest, "prompt_sha256": manifest["judge_prompt"], "judges": "malformed"}
        )
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "agentic-grade",
                "--target",
                "probes",
                "--workspace",
                str(tmp_path / "workspace"),
                "--workspace-kind",
                "held-out",
                "--handoff-tuning",
                str(evidence),
                "--panel",
                str(PANEL),
                "--criteria",
                str(CRITERIA),
                "--probes",
                str(PROBES),
                "--ledger-dir",
                str(tmp_path / "ledger"),
            ]
        )
    assert exc.value.code == 2
    assert not (tmp_path / "workspace").exists()
    assert not (tmp_path / "ledger").exists()
