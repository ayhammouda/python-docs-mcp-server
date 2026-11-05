"""Normal keyless calibration must reject contradictory tuning before spending."""

import base64
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from benchmarks.agentic.inputs import CapturedInput
from benchmarks.grading import run, score
from benchmarks.grading.calibration import replay_g1
from benchmarks.grading.gates import G1Binding
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.grading_pilot_fixture import block_external_io, forbidden
from tests.benchmarks.test_agentic_run import SAMPLE_CORPUS
from tests.benchmarks.test_grading_financial_preflight import run_grading_jobs, without_signals
from tests.benchmarks.test_grading_run import IDS, PROBES, config, panel_file, read


@pytest.fixture
def normal_tuning(tmp_path, monkeypatch):
    block_external_io(monkeypatch)
    monkeypatch.setattr(run, "run_jobs", run_grading_jobs)
    monkeypatch.setattr(run, "_until_signalled", without_signals)
    monkeypatch.setattr(run, "CORPUS_PATH", SAMPLE_CORPUS)
    probes = tmp_path / "probes.yml"
    suite = yaml.safe_load(PROBES.read_bytes())
    suite["probes"] = [p for p in suite["probes"] if p["type"] == "KEY"]
    probes.write_text(yaml.safe_dump(suite))
    panel = panel_file(tmp_path)
    data = yaml.safe_load(panel.read_bytes())
    for judge in (*IDS, "flash"):
        data["judges"][judge]["effort"] = None
    panel.write_text(yaml.safe_dump(data))
    cfg = config(
        tmp_path,
        "tuning",
        out_dir=tmp_path / "tuning",
        panel_path=panel,
        probes_path=probes,
        judges=("haiku",),
        effort="low",
    )
    run.init_workspace(
        cfg.out_dir,
        "tuning",
        criteria_path=cfg.criteria_path,
        panel_path=panel,
        probes_path=probes,
        tuning_path=cfg.tuning_path,
    )
    for judge in (*IDS, "flash"):
        result = run.run_grading(replace(cfg, judges=(judge,)), confirm=forbidden)
        assert result["records_written"] == 1
    tuning = score.write_tuning(cfg.out_dir)
    for judge in (*IDS, "flash"):
        data["judges"][judge]["effort"] = "low"
    frozen = tmp_path / "frozen-panel.yml"
    frozen.write_text(yaml.safe_dump(data))
    held = replace(
        cfg,
        target="probes",
        out_dir=tmp_path / "held",
        panel_path=frozen,
        judges=None,
        effort=None,
        ledger_dir=tmp_path / "held-ledger",
    )
    return cfg, tuning, held


def initialize(held, tuning):
    return run.init_workspace(
        held.out_dir,
        "held-out",
        criteria_path=held.criteria_path,
        panel_path=held.panel_path,
        probes_path=held.probes_path,
        tuning_evidence=[tuning],
        tuning_path=held.tuning_path,
        judges=held.judges,
    )


def test_normal_workspaces_retain_the_real_tuning_rubric(normal_tuning):
    cfg, tuning, held = normal_tuning
    initialize(held, tuning)
    for root in (cfg.out_dir, held.out_dir):
        assert (root / "inputs/tuning-questions.yml").read_bytes() == cfg.tuning_path.read_bytes()
    assert (held.out_dir / "handoff/tuning-1.json").read_bytes() == tuning.read_bytes()


def test_real_failed_low_trial_then_passing_medium_can_freeze(normal_tuning):
    cfg, _, held = normal_tuning
    panel = yaml.safe_load(cfg.panel_path.read_bytes())
    script = cfg.out_dir.parent / "effort-script.yml"
    good = yaml.safe_load(Path(panel["judges"]["haiku"]["script"]).read_bytes())
    bad = copy.deepcopy(good)
    bad["rules"][0]["output"]["attempted"] = False
    script.write_text(yaml.safe_dump(bad))
    panel["judges"]["haiku"]["script"] = str(script)
    initial = cfg.out_dir.parent / "medium-initial.yml"
    initial.write_text(yaml.safe_dump(panel))
    cfg = replace(cfg, out_dir=cfg.out_dir.parent / "medium-tuning", panel_path=initial)
    run.init_workspace(
        cfg.out_dir,
        "tuning",
        criteria_path=cfg.criteria_path,
        panel_path=initial,
        probes_path=cfg.probes_path,
        tuning_path=cfg.tuning_path,
    )
    assert run.run_grading(cfg, confirm=forbidden)["records_written"] == 1
    script.write_text(yaml.safe_dump(good))
    assert run.run_grading(replace(cfg, effort="medium"), confirm=forbidden)["records_written"] == 1
    for judge in IDS[1:]:
        run.run_grading(replace(cfg, judges=(judge,)), confirm=forbidden)
    tuning = score.write_tuning(cfg.out_dir)
    row = read(tuning)["judges"]["haiku"]
    assert row["tried"] == ["low", "medium"] and row["chosen"] == "medium"
    assert row["efforts"]["low"]["accuracy"] == 0
    assert row["efforts"]["medium"]["accuracy"] == 1
    for judge in IDS:
        panel["judges"][judge]["effort"] = row["chosen"] if judge == "haiku" else "low"
    final = cfg.out_dir.parent / "medium-final.yml"
    final.write_text(yaml.safe_dump(panel))
    initialize(replace(held, panel_path=final), tuning)
    assert not held.ledger_dir.exists()


def test_summary_cannot_move_a_raw_low_trial_to_medium_before_spend(normal_tuning):
    _, tuning, held = normal_tuning
    evidence = read(tuning)
    row = evidence["judges"]["haiku"]
    row.update(chosen="medium", tried=["medium"], efforts={"medium": row["efforts"]["low"]})
    tuning.write_text(json.dumps(evidence))
    panel = yaml.safe_load(held.panel_path.read_bytes())
    panel["judges"]["haiku"]["effort"] = "medium"
    held.panel_path.write_text(yaml.safe_dump(panel))
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        initialize(held, tuning)
    assert not held.out_dir.exists()
    assert not held.ledger_dir.exists()


def test_selected_configuration_and_source_cannot_be_swapped(normal_tuning):
    _, tuning, held = normal_tuning
    original = held.panel_path.read_bytes()
    for judge, fields in (
        (
            "haiku",
            {
                "model_id": "different-haiku",
                "max_output_tokens": 8192,
                "max_input_tokens": 100000,
                "framing_allowance_tokens": 128,
            },
        ),
        ("flash", {"model_id": "different-flash", "max_output_tokens": 8192}),
    ):
        ids = ("haiku", "luna", "flash") if judge == "flash" else IDS
        for field, value in fields.items():
            panel = yaml.safe_load(original)
            panel["judges"][judge][field] = value
            held.panel_path.write_text(yaml.safe_dump(panel))
            with pytest.raises(BenchmarkValidationError, match="tuning"):
                initialize(replace(held, judges=ids), tuning)
            assert not held.out_dir.exists()
            assert not held.ledger_dir.exists()
    held.panel_path.write_bytes(original)
    evidence = read(tuning)
    evidence["source"]["files"] = {}
    tuning.write_text(json.dumps(evidence))
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        initialize(held, tuning)


def test_new_workspace_cannot_downgrade_to_summary_only_claims(normal_tuning):
    _, tuning, held = normal_tuning
    evidence = read(tuning)
    del evidence["source"]
    tuning.write_text(json.dumps(evidence))
    with pytest.raises(BenchmarkValidationError, match="tuning attempts require original source"):
        initialize(held, tuning)
    assert not held.out_dir.exists()
    assert not held.ledger_dir.exists()


def test_old_source_with_raw_replies_but_no_execution_receipts_remains_usable(normal_tuning):
    _, tuning, held = normal_tuning
    evidence = read(tuning)
    files = evidence["source"]["files"]
    for name in list(files):
        if name.startswith("executions/"):
            del files[name]
        elif name.startswith("runs/"):
            invocation = json.loads(files[name])
            del invocation["execution_results"]
            files[name] = json.dumps(invocation)
    tuning.write_text(json.dumps(evidence))
    initialize(held, tuning)
    assert run.plan_grading(held)["total_bound"] > 0
    assert not held.ledger_dir.exists()


def test_existing_handoff_is_replayed_before_live_preflight(normal_tuning):
    _, tuning, held = normal_tuning
    initialize(held, tuning)
    handoff = held.out_dir / "handoff/tuning-1.json"
    evidence = read(handoff)
    evidence["judges"]["haiku"]["efforts"]["low"]["accuracy"] = 0.25
    handoff.write_text(json.dumps(evidence))
    meta_path = held.out_dir / "workspace.json"
    meta = read(meta_path)
    meta["handoff"][handoff.name] = hashlib.sha256(handoff.read_bytes()).hexdigest()
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(BenchmarkValidationError, match="tuning"):
        run.run_grading(replace(held, live=True), confirm=forbidden)
    assert not held.ledger_dir.exists()
    assert not (held.out_dir / "items").exists()
    assert not (held.out_dir / "invocations").exists()


def test_retained_rubric_and_handoff_cannot_change_on_resume(normal_tuning):
    cfg, tuning, held = normal_tuning
    initialize(held, tuning)
    for target, writer in ((cfg, score.write_tuning), (held, score.write_g1)):
        path = target.out_dir / "inputs/tuning-questions.yml"
        original = path.read_bytes()
        path.write_bytes(original + b"\n")
        with pytest.raises(BenchmarkValidationError, match="tuning"):
            run.plan_grading(target)
        with pytest.raises(BenchmarkValidationError, match="tuning"):
            writer(target.out_dir)
        path.unlink()
        with pytest.raises(BenchmarkValidationError, match="frozen tuning"):
            writer(target.out_dir)
        path.write_bytes(original)
    changed = held.out_dir.parent / "changed-tuning.json"
    changed.write_bytes(tuning.read_bytes() + b"\n")
    with pytest.raises(BenchmarkValidationError, match="immutable tuning handoff"):
        run.check_tuning_handoff(held.out_dir, [changed])
    run.check_tuning_handoff(held.out_dir, [tuning])
    assert not held.ledger_dir.exists()


@pytest.mark.parametrize("fallback", [False, True])
def test_normal_unfrozen_tuning_to_frozen_g1_and_portable_replay(normal_tuning, fallback):
    cfg, tuning, held = normal_tuning
    if fallback:
        held = replace(held, judges=("haiku", "luna", "flash"))
    initialize(held, tuning)
    # Actual --live preflight, without a spending amount, must stop before any approval.
    with pytest.raises(BenchmarkValidationError, match="--confirm-max-spend"):
        run.run_grading(replace(held, live=True), confirm=forbidden)
    assert not held.ledger_dir.exists()
    for pass_number in (1, 2):
        result = run.run_grading(replace(held, pass_number=pass_number), confirm=forbidden)
        assert result["records_written"] == 6
    run.run_grading(replace(held, target="reference-probes", judges=None), confirm=forbidden)
    ((key, entry),) = read(score.write_g1(held.out_dir))["entries"].items()
    captured = {
        name: CapturedInput.read(held.out_dir / "inputs" / filename, name, name)
        for name, filename in (
            ("panel", "panel.yml"),
            ("probes", "probes.yml"),
            ("criteria", "grading-criteria.yml"),
        )
    }
    ids = ("haiku", "luna", "flash" if fallback else "flash-lite")
    gate, _ = replay_g1(
        {key: copy.deepcopy(entry)},
        G1Binding({j: entry["binding"]["composition"][j] for j in ids}, entry["binding"]["inputs"]),
        **captured,
    )
    assert not gate.details["calibration_unknown"]
    selected = "flash" if fallback else "flash-lite"
    assert gate.details["tuning"][0]["judges"][selected]["chosen"] == "low"
    for judge in entry["binding"]["composition"]:
        assert gate.details["held_out_test_retest"][judge]["items"] == 2
    assert read(tuning)["source"]["inputs"]["tuning"] == cfg.tuning_path.read_text()
    for name, body in read(tuning)["source"]["files"].items():
        if name.startswith("votes/"):
            assert json.loads(body)["effort"] == "low"


@pytest.mark.parametrize("packed", [False, True])
def test_nested_unused_tuning_reply_cannot_enter_held_out_workspace(
    normal_tuning, tmp_path, packed
):
    from benchmarks.agentic.protocol import KeyLeakError

    _, tuning, held = normal_tuning
    data = read(tuning)
    marker = "AIza" + "q" * 35
    reply = json.dumps({"note": marker}).replace(marker, "".join(
        f"\\u{ord(character):04x}" for character in marker
    ))
    data["source"]["files"]["votes/tuning/unused/low/pass1/extra/historic--a1.json"] = json.dumps(
        {"text": reply}
    )
    if packed:
        from benchmarks.grading.calibration_storage import pack_files

        data["source"].update(pack_files(data["source"]["files"]))
    incoming = tmp_path / "portable-tuning.json"
    incoming.write_text(json.dumps(data))

    with pytest.raises(KeyLeakError) as error:
        initialize(held, incoming)
    assert marker not in str(error.value)
    assert not held.out_dir.exists() and not held.ledger_dir.exists()


def portable_tuning(tuning, packed):
    from benchmarks.grading.calibration_storage import pack_files

    if packed:
        data = read(tuning)
        data["source"].update(pack_files(data["source"]["files"]))
        tuning.write_text(json.dumps(data))
    return tuning.read_bytes()


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("boundary", ["initialize", "selection", "resume"])
def test_normal_tuning_transport_has_its_own_read_bound(
    normal_tuning, monkeypatch, packed, boundary,
):
    from benchmarks.agentic import inputs as captured_inputs
    from benchmarks.grading import inputs

    cfg, tuning, held = normal_tuning
    body = portable_tuning(tuning, packed)
    if boundary != "initialize":
        initialize(held, tuning)
    originals = sorted((cfg.out_dir / "inputs").iterdir()) + [
        held.panel_path, cfg.tuning_path, SAMPLE_CORPUS,
    ]
    # The genuine export exceeds this scaled frozen-file limit; every original
    # frozen input still fits. Neither the transport nor source limits change.
    limit = max(p.stat().st_size for p in originals)
    assert len(body) > limit
    print(f"transport packed={packed} boundary={boundary} frozen_limit={limit} "
          f"size={len(body)} sha256={hashlib.sha256(body).hexdigest()}")
    for path in originals:
        content = path.read_bytes()
        print(f"original {path.name} size={len(content)} "
              f"sha256={hashlib.sha256(content).hexdigest()}")
    monkeypatch.setattr(captured_inputs, "MAX_FRAME_BYTES", limit)
    monkeypatch.setattr(inputs, "MAX_FRAME_BYTES", limit)
    if boundary == "initialize":
        meta = initialize(held, tuning)
        assert meta["handoff"]["tuning-1.json"] == hashlib.sha256(body).hexdigest()
    elif boundary == "selection":
        run.check_tuning_handoff(held.out_dir, [tuning])
    else:
        assert run.plan_grading(held)["total_bound"] > 0
    assert (held.out_dir / "handoff/tuning-1.json").read_bytes() == body
    assert not held.ledger_dir.exists()


@pytest.mark.parametrize("packed", [False, True])
@pytest.mark.parametrize("encoding", ["escapes", "attachment"])
def test_source_text_keeps_its_full_decoding_budget(normal_tuning, packed, encoding):
    _, tuning, held = normal_tuning
    text = r"\u0061"
    for _ in range(3):
        text = text.replace("\\", r"\u005c")
    if encoding == "attachment":
        text = "ordinary source note"
        for _ in range(4):
            text = "data:text/plain;base64," + base64.b64encode(text.encode()).decode()
    data = read(tuning)
    data["source"]["files"]["items/unused.json"] = json.dumps({"user": text})
    tuning.write_text(json.dumps(data))
    body = portable_tuning(tuning, packed)
    initialize(held, tuning)
    run.check_tuning_handoff(held.out_dir, [tuning])
    assert run.plan_grading(held)["total_bound"] > 0
    assert (held.out_dir / "handoff/tuning-1.json").read_bytes() == body
    assert not held.ledger_dir.exists()


@pytest.mark.parametrize("boundary", ["initialize", "selection", "resume"])
@pytest.mark.parametrize("damage", [
    "duplicate", "malformed", "raw-key", "metadata-key", "source-key",
])
def test_transport_validation_precedes_workspace_or_spend_mutation(
    normal_tuning, monkeypatch, boundary, damage,
):
    from benchmarks.agentic.protocol import KeyLeakError
    from tests.benchmarks.grading_pilot_fixture import NoLaunch

    _, tuning, held = normal_tuning
    initialize(held, tuning)
    data = read(tuning)
    marker = "AIza" + "q" * 35
    escaped = "".join(f"\\u{ord(c):04x}" for c in marker)
    if damage == "duplicate":
        body = ('{"prompt_sha256":"discarded",' + tuning.read_text().lstrip()[1:]).encode()
    elif damage == "malformed":
        body = tuning.read_bytes()[:-2]
    elif damage == "raw-key":
        body = b"invalid JSON " + marker.encode()
    else:
        if damage == "metadata-key":
            data["note"] = escaped
        else:
            data["source"]["files"]["items/unused.json"] = json.dumps({"user": escaped})
        body = json.dumps(data).encode()
    target = tuning
    if boundary == "resume":
        target = held.out_dir / "handoff/tuning-1.json"
    target.write_bytes(body)
    if boundary in ("selection", "resume"):
        # Even a matching digest cannot authorize unsafe/ambiguous evidence.
        meta_path = held.out_dir / "workspace.json"
        meta = read(meta_path)
        meta["handoff"]["tuning-1.json"] = hashlib.sha256(body).hexdigest()
        meta_path.write_text(json.dumps(meta))
    before = {p: p.read_bytes() for p in held.out_dir.rglob("*") if p.is_file()}
    monkeypatch.setattr(run.live, "authorize_live", forbidden)
    monkeypatch.setattr(run, "Ledger", forbidden)
    monkeypatch.setattr(run, "_until_signalled", forbidden)
    error = KeyLeakError if damage.endswith("key") else BenchmarkValidationError
    with pytest.raises(error) as failure:
        if boundary == "initialize":
            fresh = replace(held, out_dir=held.out_dir.parent / "new-held")
            initialize(fresh, tuning)
        elif boundary == "selection":
            run.check_tuning_handoff(held.out_dir, [tuning])
        else:
            run.run_grading(replace(held, live=True), launcher=NoLaunch(), confirm=forbidden)
    if boundary == "initialize":
        assert not fresh.out_dir.exists()
    assert marker not in str(failure.value)
    assert {p: p.read_bytes() for p in held.out_dir.rglob("*") if p.is_file()} == before
    assert not held.ledger_dir.exists()


@pytest.mark.parametrize("boundary", ["initialize", "selection"])
def test_explicit_external_tuning_file_aliases_keep_exact_bytes(normal_tuning, boundary):
    _, tuning, held = normal_tuning
    body = tuning.read_bytes()
    alias = tuning.parent / "selected-tuning.json"
    alias.symlink_to(tuning)
    initialize(held, alias if boundary == "initialize" else tuning)
    run.check_tuning_handoff(held.out_dir, [alias])
    assert run.plan_grading(held)["total_bound"] > 0
    assert (held.out_dir / "handoff/tuning-1.json").read_bytes() == body
    assert tuning.read_bytes() == body
    assert not held.ledger_dir.exists()


def test_external_tuning_alias_cycle_refuses_before_workspace_creation(normal_tuning):
    _, tuning, held = normal_tuning
    alias = tuning.parent / "cyclic-selection.json"
    alias.symlink_to(alias)
    with pytest.raises(BenchmarkValidationError):
        initialize(held, alias)
    assert not held.out_dir.exists() and not held.ledger_dir.exists()


@pytest.mark.parametrize("boundary,damage", [
    (boundary, damage)
    for boundary in ("initialize", "selection", "resume")
    for damage in ("missing", "transport", "metadata", "unpacked", "packed-hash")
] + [("resume", "symlink")])
def test_calibration_transport_safety_applies_at_every_handoff_boundary(
    normal_tuning, monkeypatch, boundary, damage,
):
    from benchmarks.grading import calibration_scan, calibration_storage

    _, tuning, held = normal_tuning
    portable_tuning(tuning, True)
    initialize(held, tuning)
    target = tuning if boundary != "resume" else held.out_dir / "handoff/tuning-1.json"
    body = target.read_bytes()
    if damage == "missing":
        target.unlink()
    elif damage == "symlink":
        sibling = tuning.parent / "redirected.json"
        sibling.write_bytes(body)
        target.unlink()
        target.symlink_to(sibling)
    elif damage == "transport":
        monkeypatch.setattr(calibration_storage, "MAX_CALIBRATION_BYTES", len(body) - 1)
    elif damage == "metadata":
        monkeypatch.setattr(calibration_storage, "MAX_METADATA_BYTES", 100)
    elif damage == "unpacked":
        monkeypatch.setattr(calibration_storage, "MAX_SOURCE_BYTES", 100)
        monkeypatch.setattr(calibration_scan, "MAX_SOURCE_BYTES", 100)
    else:
        data = json.loads(body)
        next(iter(data["source"]["blobs"].values()))["size"] -= 1
        target.write_text(json.dumps(data))
        body = target.read_bytes()
        if boundary in ("selection", "resume"):
            path = held.out_dir / "workspace.json"
            meta = read(path)
            meta["handoff"]["tuning-1.json"] = hashlib.sha256(body).hexdigest()
            path.write_text(json.dumps(meta))
    monkeypatch.setattr(run.live, "authorize_live", forbidden)
    monkeypatch.setattr(run, "Ledger", forbidden)
    monkeypatch.setattr(run, "_until_signalled", forbidden)
    if boundary == "initialize":
        held = replace(held, out_dir=held.out_dir.parent / "new-held")
    before = {p: p.read_bytes() for p in held.out_dir.rglob("*")
              if p.is_file() and not p.is_symlink()}
    with pytest.raises(BenchmarkValidationError):
        if boundary == "initialize":
            initialize(held, tuning)
        elif boundary == "selection":
            run.check_tuning_handoff(held.out_dir, [tuning])
        else:
            run.run_grading(replace(held, live=True), confirm=forbidden)
    assert {p: p.read_bytes() for p in held.out_dir.rglob("*")
            if p.is_file() and not p.is_symlink()} == before
    if boundary == "initialize":
        assert not held.out_dir.exists()
    assert not held.ledger_dir.exists()
