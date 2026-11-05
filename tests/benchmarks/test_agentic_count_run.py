"""Task 9b: real worker/CLI paths, synthetic counts, no network or durable ledger."""

import asyncio
import hashlib
import importlib.util
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

from benchmarks import __main__ as cli
from benchmarks.agentic import count
from benchmarks.agentic.host import (
    GRADING_CAPS,
    GRADING_SYSTEM,
    ItemSource,
    LocalLauncher,
    WorkerJob,
    run_jobs,
)
from benchmarks.agentic.ledger import PlannedItem
from benchmarks.agentic.protocol import KeyLeakError, read_frame_fd
from benchmarks.agentic.run import run_agentic
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_agentic_count import TOOLS_RAW
from tests.benchmarks.test_agentic_run import _config
from tests.benchmarks.test_grading_jobs import ScriptLauncher, StubLauncher


def read(path):
    return json.loads(path.read_bytes())


@pytest.fixture
def answering(tmp_path):
    run_agentic(_config(tmp_path, systems=("no-docs",), models=("scripted-anthropic",), repeats=1))
    return tmp_path / "run"


@pytest.fixture
def family(tmp_path):
    script = tmp_path / "counts.yml"
    script.write_text("{}\n")
    return count.count_family(script)


def tokens(root):
    return sorted((root / "tokens").glob("*/*.json"))


def test_run_counting_writes_every_scored_cell_and_skips_discarded(answering, family):
    discarded = answering / "discarded/foreign/Q.json"
    discarded.parent.mkdir(parents=True)
    discarded.write_text("not even JSON")
    result = count.run_counting(answering, live=False, family=family)
    assert result["records_written"] == 2
    assert result["units"] == []
    assert all(read(p)["status"] == "counted" for p in tokens(answering))
    for path in tokens(answering):
        row = read(path)
        assert row["execution"] == {
            "provider": "scripted",
            "api": "scripted",
            "model_id": "bytes/4",
        }
        assert row["count_model"] == "bytes/4" and row["approximation"] is True
    assert not (answering / "tokens/foreign").exists()
    before = {p: p.read_bytes() for p in tokens(answering)}
    assert count.run_counting(answering, live=False, family=family)["records_written"] == 0
    assert before == {p: p.read_bytes() for p in tokens(answering)}


def test_run_counting_without_live_only_plans(answering):
    before = {p: p.read_bytes() for p in tokens(answering)}
    result = count.run_counting(answering, live=False)
    assert result["dry_run"] and result["items"] == 2 and result["count_calls"] == 6
    assert before == {p: p.read_bytes() for p in tokens(answering)}


@pytest.mark.parametrize("ci,confirmed,match", [("1", True, "CI"), ("0", False, "confirmed")])
def test_live_counting_passes_pr4s_authorization_gate(answering, monkeypatch, ci, confirmed, match):
    class NoStart(LocalLauncher):
        def supports(self, family):
            return True

        async def prepare(self, jobs):
            pytest.fail("authorization must precede prepare/key bootstrap")

    monkeypatch.setenv("CI", ci)
    seen = []
    with pytest.raises(BenchmarkValidationError, match=match):
        count.run_counting(
            answering,
            live=True,
            launcher=NoStart(),
            confirm=lambda text: seen.append(text) or confirmed,
        )
    if ci == "0":
        assert "free" in seen[0] and "no ledger unit" in seen[0] and "6" in seen[0]


@pytest.mark.parametrize("which", ["prompt", "tools", "transcript", "coordinates", "deleted"])
def test_stale_inputs_are_refused_even_for_already_counted_cells(answering, family, which):
    count.run_counting(answering, live=False, family=family)
    before = {p: p.read_bytes() for p in tokens(answering)}
    transcript = next((answering / "transcripts").glob("*/*.json"))
    if which == "prompt":
        path = answering / "snapshots/system-prompt.txt"
        path.write_text(path.read_text() + "changed")
    elif which == "tools":
        path = next((answering / "systems").glob("*.json"))
        path.write_text(json.dumps(read(path) | {"tools_raw": TOOLS_RAW}))
    elif which == "deleted":
        transcript.unlink()
    else:
        transcript.write_text(
            json.dumps(
                read(transcript) | {"answer" if which == "transcript" else "corpus_id": "changed"}
            )
        )
    with pytest.raises(BenchmarkValidationError):
        count.run_counting(answering, live=False, family=family)
    assert before == {p: p.read_bytes() for p in tokens(answering)}


def test_all_keeps_immutable_results_and_failed_rerun_keeps_good_counts(answering, family):
    count.run_counting(answering, live=False, family=family)
    before = {p: p.read_bytes() for p in tokens(answering)}
    result = count.run_counting(answering, live=False, family=family, only_missing=False)
    assert result["records_written"] == 2
    history = sorted((answering / "grading/counts").glob("*/*/*.json"))
    assert len(history) == 4
    old = {p: p.read_bytes() for p in history}
    Path(family.raw["script"]).write_text("errors:\n  - {kind: rejected, status: 400}\n")
    count.run_counting(answering, live=False, family=family, only_missing=False)
    assert all(read(p)["status"] == "counted" for p in before)
    assert old == {p: p.read_bytes() for p in old}
    assert any(
        read(p)["status"] == "failed" for p in (answering / "grading/counts").glob("*/*/*.json")
    )


def test_legacy_projection_can_be_recounted_without_relabeling_history(answering, family):
    summary = count.run_counting(answering, live=False, family=family)
    run_id = summary["count_run_id"]
    receipt = answering / f"grading/count-runs/{run_id}.json"
    history = {}
    for projection in tokens(answering):
        relative = projection.relative_to(answering / "tokens")
        archive = answering / "grading/counts" / run_id / relative
        row = read(projection)
        row.pop("execution", None)
        row.update(
            count_model=count.DEFAULT_COUNT_MODEL,
            approximation=False,
            anthropic_version=count.ANTHROPIC_API_VERSION,
            token_label=count.METHODOLOGY_TOKEN_LABEL,
            notes=count.NOTES,
        )
        history[archive] = json.dumps(row).encode()
        archive.write_bytes(history[archive])
        projection.write_bytes(history[archive])
        summary["results"][str(relative)] = hashlib.sha256(history[archive]).hexdigest()
    receipt.write_text(json.dumps(summary))

    assert count.run_counting(answering, live=False)["items"] == 2  # plan only
    assert all("execution" not in read(path) for path in tokens(answering))
    assert count.run_counting(answering, live=False, family=family)["records_written"] == 2
    assert all(read(path)["execution"]["api"] == "scripted" for path in tokens(answering))
    assert history == {path: path.read_bytes() for path in history}


def test_counted_record_tampering_is_refused_on_resume(answering, family):
    count.run_counting(answering, live=False, family=family)
    path = tokens(answering)[0]
    path.write_text(json.dumps(read(path) | {"client_wrapped_tokens": 999999}))
    with pytest.raises(BenchmarkValidationError):
        count.run_counting(answering, live=False, family=family)


def test_partial_counts_survive_a_worker_error_and_resume(answering, family):
    launcher = ScriptLauncher(
        extra="""
from benchmarks.agentic import count
original = count.count_item
calls = 0
async def limited(*args, **kwargs):
    global calls
    calls += 1
    if calls == 2: raise RuntimeError('synthetic crash')
    return await original(*args, **kwargs)
count.count_item = limited
"""
    )
    first = count.run_counting(answering, live=False, family=family, launcher=launcher)
    assert first["records_written"] == 1 and first["not_run"] and first["worker_errors"]
    saved = {p: p.read_bytes() for p in tokens(answering) if read(p)["status"] == "counted"}
    assert count.run_counting(answering, live=False, family=family)["records_written"] == 1
    assert saved == {p: p.read_bytes() for p in saved}


@pytest.mark.parametrize(
    "field,value",
    [
        ("competitor_id", "foreign"),
        ("corpus_id", "foreign"),
        ("tool_model_key", "foreign"),
        ("system_prompt_sha256", "foreign"),
        ("tools_sha256", "foreign"),
        ("client_wrapped_tokens", True),
        ("execution", None),
        (
            "execution",
            {"provider": "anthropic", "api": "anthropic-messages", "model_id": "claude-sonnet-5-5"},
        ),
        ("execution", {"provider": "scripted", "api": "scripted", "model_id": "wrong"}),
        ("execution", {"provider": "unknown", "api": "unknown", "model_id": "unknown"}),
        ("approximation", False),
    ],
)
def test_foreign_or_invalid_worker_record_cannot_choose_output(answering, family, field, value):
    launcher = ScriptLauncher(
        extra=f"""
from benchmarks.agentic import count
original = count.count_item
async def forged(*args, **kwargs):
    row = await original(*args, **kwargs)
    if {value is None!r}:
        row.pop({field!r}, None)
    else:
        row[{field!r}] = {value!r}
    return row
count.count_item = forged
"""
    )
    with pytest.raises(BenchmarkValidationError):
        count.run_counting(answering, live=False, family=family, launcher=launcher)
    assert all(read(p)["status"] == "placeholder" for p in tokens(answering))


def test_cli_flags_all_and_dry_run_work(answering, family, monkeypatch, capsys):
    monkeypatch.setattr(count, "count_family", lambda: family)
    args = ["agentic-count-tokens", "--run-dir", str(answering)]
    assert cli.main([*args, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["records_written"] == 2
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["records_written"] == 0
    assert cli.main([*args, "--all"]) == 0
    assert json.loads(capsys.readouterr().out)["records_written"] == 2
    with pytest.raises(SystemExit) as exc:
        cli.main([*args, "--live"])
    assert exc.value.code == 2


def test_cli_container_count_invocations_keep_answering_evidence(answering, monkeypatch, capsys):
    seen = []
    markers = ["sandbox.json", "egress/summary.json", "probe/original.json"]
    for name in markers:
        path = answering / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("original")

    def launcher(args, run_id, out_dir):
        seen.append((run_id, out_dir))
        return LocalLauncher()

    monkeypatch.setattr(cli, "_container_launcher", launcher)
    for _ in range(2):
        assert (
            cli.main(
                [
                    "agentic-count-tokens",
                    "--run-dir",
                    str(answering),
                    "--launcher",
                    "container",
                    "--image",
                    "sha256:synthetic",
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["dry_run"]
    assert seen[0][0] != seen[1][0]
    assert all(path == answering / "grading/sandbox" / ident for ident, path in seen)
    assert all((answering / p).read_text() == "original" for p in markers)


async def test_a_raw_envelope_item_is_counted_once(tmp_path, family):
    envelope = count.Envelope("", None, [{"role": "user", "content": "hello"}])
    source = ItemSource(
        [
            (
                PlannedItem("raw", 0, 0, 0),
                {"kind": "envelope", "name": "raw", "envelope": asdict(envelope)},
            )
        ]
    )
    job = WorkerJob(
        "counter--grading", family, GRADING_SYSTEM, (), GRADING_CAPS, task="items", source=source
    )
    frames = []

    async def collect(job, frame):
        frames.append(frame)

    await run_jobs(
        [job],
        launcher=LocalLauncher(),
        ledger=None,
        on_message=collect,
        log_dir=tmp_path / "logs",
        cwd=Path.cwd(),
    )
    counted = [m for m in frames if m["type"] == "counted"]
    assert len(counted) == 1 and counted[0]["name"] == "raw"
    assert counted[0]["tokens"] == count.ScriptedCountClient.tokens(envelope)
    assert len([m for m in frames if m["type"] == "heartbeat"]) == 1


def premise_module():
    path = Path("ops/benchmarks/count_tokens_premise_check.py")
    spec = importlib.util.spec_from_file_location("count_premise", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_premise_script_builds_seven_envelopes_and_passes_the_gate(tmp_path, monkeypatch):
    module = premise_module()
    assert [i["name"] for i in module.premise_items()] == [
        "real-tools",
        "undefined-tool-no-stub",
        "undefined-tool-stub",
        "stand-in-tool-turn",
        "stand-in-text-turn",
        "trailing-assistant",
        "repeated-ids",
    ]
    monkeypatch.setattr(
        cli, "_container_launcher", lambda *a: pytest.fail("no launcher before gate")
    )
    args = [
        "--scratch",
        str(tmp_path),
        "--image",
        "sha256:synthetic",
        "--keys-file",
        str(tmp_path / "unread"),
    ]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-key")
    assert module.main(args) == 2
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("CI", "1")
    assert module.main(args) == 2


def test_counts_ignore_spend_caps_and_never_open_a_ledger(answering, family, monkeypatch):
    from benchmarks.agentic.ledger import Ledger

    ledger = answering.parent / "ledger"
    before = {p: p.read_bytes() for p in ledger.rglob("*") if p.is_file()}
    monkeypatch.setattr(Ledger, "open", lambda *a, **kw: pytest.fail("free count opened ledger"))
    result = count.run_counting(answering, live=False, family=family)
    assert result["units"] == [] and result["records_written"] == 2
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("location", ["prompt", "transcript", "shadowed-transcript"])
def test_key_shaped_inputs_never_reach_a_worker(answering, family, location):
    key = "sk-" + "X" * 42
    # No real credential: synthetic data checks scans before any publication/worker frame.
    if location == "prompt":
        path = answering / "snapshots/system-prompt.txt"
        path.write_text(key)
    else:
        path = next((answering / "transcripts").glob("*/*.json"))
        if location == "shadowed-transcript":
            path.write_text(
                path.read_text().rstrip()[:-1]
                + ',"ignored":"'
                + r"\u0073"
                + key[1:]
                + '","ignored":"clean"}'
            )
        else:
            path.write_text(json.dumps(read(path) | {"answer": key}))
    with pytest.raises(KeyLeakError):
        count.run_counting(answering, live=False, family=family)
    assert not (answering / "grading/count-inputs.json").exists()


def test_provider_error_aborts_before_later_counts_or_worker_frame(answering, family):
    calls = answering / "endpoint-attempts"
    launcher = ScriptLauncher(
        extra=f"""
from benchmarks.agentic import count
from benchmarks.agentic.types import ProviderCallError
from pathlib import Path
class Client:
    async def count(self, envelope):
        with Path({str(calls)!r}).open('a') as file:
            file.write('count\\n')
        raise ProviderCallError('rejected', 'sk-' + 'X' * 42, status=400)
count.make_count_client = lambda *a, **kw: Client()
"""
    )
    with pytest.raises(KeyLeakError):
        count.run_counting(answering, live=False, family=family, launcher=launcher)
    assert calls.read_text().splitlines() == ["count"]
    assert all(read(p)["status"] == "placeholder" for p in tokens(answering))
    assert all(
        b"sk-" + b"X" * 42 not in p.read_bytes()
        for p in (answering / "grading").rglob("*")
        if p.is_file()
    )


@pytest.mark.parametrize("directory", ["tokens", "grading/counts"])
def test_linked_output_directory_is_refused_before_writes(answering, family, directory):
    import shutil

    outside = answering.parent / "outside"
    outside.mkdir()
    path = answering / directory
    if path.exists():
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BenchmarkValidationError):
        count.run_counting(answering, live=False, family=family)
    assert not list(outside.iterdir())


def test_unassigned_item_and_duplicate_counted_frames_are_refused(answering, family):
    body = "req({'type':'next_item'})\nsend({'type':'counted','item_id':'foreign','record':{}})\n"
    with pytest.raises(BenchmarkValidationError, match="unassigned"):
        count.run_counting(answering, live=False, family=family, launcher=StubLauncher(body))
    assert all(read(p)["status"] == "placeholder" for p in tokens(answering))


def test_archived_result_tampering_is_refused_even_after_an_all_rerun(answering, family):
    count.run_counting(answering, live=False, family=family)
    history = next((answering / "grading/counts").glob("*/*/*.json"))
    count.run_counting(answering, live=False, family=family, only_missing=False)
    history.write_text(json.dumps(read(history) | {"client_wrapped_tokens": 123}))
    with pytest.raises(BenchmarkValidationError, match="immutable count result"):
        count.run_counting(answering, live=False, family=family)


def test_count_run_id_is_single_use_even_for_dry_runs(answering):
    count.run_counting(answering, live=False, count_run_id="same")
    before = (answering / "grading/count-invocations/same/plan.json").read_bytes()
    with pytest.raises(BenchmarkValidationError):
        count.run_counting(answering, live=False, count_run_id="same")
    assert before == (answering / "grading/count-invocations/same/plan.json").read_bytes()


def test_cli_key_leak_and_validation_exit_codes(tmp_path, monkeypatch):
    args = ["agentic-count-tokens", "--run-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    monkeypatch.setattr(
        count, "run_counting", lambda *a, **kw: (_ for _ in ()).throw(KeyLeakError("synthetic"))
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 3


async def test_live_items_without_bootstrap_are_refused_before_client_creation(monkeypatch):
    from benchmarks.agentic.worker import ProtocolChannel, run_worker

    monkeypatch.setattr(
        count, "make_count_client", lambda *a, **kw: pytest.fail("client before bootstrap")
    )
    job = {
        "type": "job",
        "family": count.count_family().raw,
        "system": GRADING_SYSTEM.raw,
        "caps": asdict(GRADING_CAPS),
        "task": "items",
    }
    with tempfile.TemporaryFile() as output:
        channel = ProtocolChannel(output.fileno(), (), -1, None, ())
        assert await run_worker(channel, asyncio.StreamReader(), job) == 2
        output.seek(0)
        frame = read_frame_fd(output.fileno())
        assert frame and frame["type"] == "error" and "bootstrap" in frame["reason"]
        assert read_frame_fd(output.fileno()) is None


MOCK_COUNT_SDK = """
import os
import json
import httpx2
from benchmarks.agentic import count
from benchmarks.agentic.clients import anthropic_sdk
os.environ['BENCHMARK_LIVE_PROVIDERS_ENABLED'] = '1'
os.environ['BENCHMARK_EGRESS_URL'] = 'http://synthetic-egress:3128'
def handler(request):
    assert request.url.host == 'api.anthropic.com'
    assert request.url.path == '/v1/messages/count_tokens'
    assert request.headers['x-api-key'] == 'synthetic-bootstrap-only'
    body = json.loads(request.content)
    assert 'max_tokens' not in body and body['model'] == 'claude-sonnet-5-5'
    return httpx2.Response(200, json={'input_tokens': 123})
def sdk(key, timeout_s, *, transport=None, proxy=None):
    assert proxy == 'http://synthetic-egress:3128'
    return anthropic_sdk(key, timeout_s, transport=httpx2.MockTransport(handler), proxy=proxy)
count.anthropic_sdk = sdk
"""


def fake_container_launcher(monkeypatch, worker_setup=MOCK_COUNT_SDK):
    from tests.benchmarks.test_agentic_sandbox import FakeDocker, Probes, _verified

    original = cli._container_launcher
    created = []

    def build(args, run_id, out_dir):
        class ProxyProbes(Probes):
            async def __call__(self, sandbox, egress, **kwargs):
                for name in ("access.log", "cache.log"):
                    (egress.spool_dir / name).touch()
                return await super().__call__(sandbox, egress, **kwargs)

        probes = ProxyProbes()
        docker = FakeDocker()
        launcher = original(args, run_id, out_dir, docker=docker, verify=_verified, probe=probes)
        launcher._control = probes.control
        launcher.command = ScriptLauncher(extra=worker_setup).command
        created.append((out_dir, docker))
        return launcher

    monkeypatch.setattr(cli, "_container_launcher", build)
    return created


def recording_cli(answering, monkeypatch, worker_setup=MOCK_COUNT_SDK):
    from benchmarks.agentic import live
    from tests.benchmarks.test_agentic_sandbox import IMAGE

    keys = answering.parent / "synthetic-keys"
    keys.write_text("ANTHROPIC_API_KEY=synthetic-bootstrap-only\n")
    keys.chmod(0o600)
    created = fake_container_launcher(monkeypatch, worker_setup)
    original = live.authorize_live
    confirmations = []
    monkeypatch.setenv("CI", "0")

    def authorized(text, **kwargs):
        original(text, confirm=lambda t: confirmations.append(t) or True)

    monkeypatch.setattr(live, "authorize_live", authorized)
    args = [
        "agentic-count-tokens",
        "--run-dir",
        str(answering),
        "--launcher",
        "container",
        "--image",
        IMAGE,
        "--keys-file",
        str(keys),
        "--live",
    ]
    return args, created, confirmations


@pytest.mark.parametrize("substitution", ["scripted", "other-model"])
def test_live_assignment_refuses_another_counter(answering, monkeypatch, capsys, substitution):
    if substitution == "scripted":
        setup = (
            MOCK_COUNT_SDK
            + """
count.make_count_client = lambda *a, **kw: count.ScriptedCountClient()
"""
        )
    else:
        setup = (
            MOCK_COUNT_SDK.replace("'claude-sonnet-5-5'", "'other-model'")
            + """
make = count.make_count_client
def alternate(*args, **kwargs):
    client = make(*args, **kwargs)
    client.model_id = 'other-model'
    return client
count.make_count_client = alternate
"""
        )
    args, _, _ = recording_cli(answering, monkeypatch, setup)
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2 and "disagrees with assigned item" in capsys.readouterr().err
    assert all(read(p)["status"] == "placeholder" for p in tokens(answering))
    assert not list((answering / "grading/counts").glob("*/*/*.json"))


def test_live_cli_uses_bootstrap_proxy_and_separate_sandbox_evidence(
    answering, family, monkeypatch, capsys
):
    args, created, confirmations = recording_cli(answering, monkeypatch)
    c1 = ["sandbox.json", "egress/summary.json", "probe/checks.json"]
    for name in c1:
        path = answering / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"answering evidence")
    # A diagnostic script run must not satisfy a later endpoint assignment.
    assert count.run_counting(answering, live=False, family=family)["records_written"] == 2
    saved = {}
    for flags in ([], ["--all"]):
        assert cli.main([*args, *flags]) == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["records_written"] == 2 and summary["units"] == []
        for path in tokens(answering):
            row = read(path)
            assert row["execution"] == {
                "provider": "anthropic",
                "api": "anthropic-messages",
                "model_id": "claude-sonnet-5-5",
            }
            assert row["count_model"] == "claude-sonnet-5-5" and row["approximation"] is False
            assert row["client_wrapped_tokens"] == 123
        out, docker = created[-1]
        assert out == answering / "grading/sandbox" / summary["count_run_id"]
        assert docker.calls
        assert (out / "egress/token-counter--grading/collected.json").exists()
        saved.update({p: p.read_bytes() for p in out.rglob("*") if p.is_file()})
    assert created[0][0] != created[1][0] and len(confirmations) == 2
    assert saved == {p: p.read_bytes() for p in saved}
    assert all((answering / name).read_bytes() == b"answering evidence" for name in c1)


def test_premise_runs_seven_counts_via_shared_launcher_with_synthetic_sdk(
    tmp_path, monkeypatch, capsys
):
    from benchmarks.agentic import live
    from tests.benchmarks.test_agentic_sandbox import IMAGE

    keys = tmp_path / "synthetic-keys"
    keys.write_text("ANTHROPIC_API_KEY=synthetic-bootstrap-only\n")
    keys.chmod(0o600)
    fake_container_launcher(monkeypatch)
    monkeypatch.setenv("CI", "0")
    original = live.authorize_live
    monkeypatch.setattr(live, "authorize_live", lambda text: original(text, confirm=lambda _: True))
    assert (
        premise_module().main(
            ["--scratch", str(tmp_path / "scratch"), "--image", IMAGE, "--keys-file", str(keys)]
        )
        == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 7 and all(line.endswith(": 123") for line in lines)
    assert len(list((tmp_path / "scratch/premise/results").glob("*.json"))) == 7


def test_failed_trajectories_and_user_model_call_cap_are_counted(tmp_path, family, monkeypatch):
    from tests.benchmarks.test_agentic_run import _isolated_index, _matrix_with, _write_manifest

    _isolated_index(tmp_path, monkeypatch)

    # The actual answering worker stops on the user's cap, leaving a final tool_use.
    matrix = _matrix_with(
        tmp_path,
        {
            "scripted-anthropic": [
                {"tools": [{"name": "search_docs", "arguments": {"query": "json"}}]}
            ]
        },
    )
    manifest = _write_manifest(tmp_path)

    class Capped(LocalLauncher):
        def command(self, job):
            return ScriptLauncher(
                extra="""
from benchmarks.agentic import worker
original = worker._run_job
async def capped(channel, reader, first=None, **kwargs):
    from benchmarks.agentic.protocol import read_frame
    first = first or await read_frame(reader)
    first['caps']['model_calls'] = 1
    return await original(channel, reader, first, **kwargs)
worker._run_job = capped
"""
            ).command(job)

    run_agentic(
        _config(
            tmp_path,
            models=("scripted-anthropic",),
            systems=("python-docs",),
            repeats=1,
            matrix_path=matrix,
            manifest_path=manifest,
        ),
        launcher=Capped(),
    )
    root = tmp_path / "run"
    transcripts = [read(p) for p in (root / "transcripts").glob("*/*.json")]
    assert len(transcripts) == 2
    assert all(
        row["status"] == "failed" and row["error"]["category"] == "cap_model_calls"
        for row in transcripts
    )
    assert all(
        row["canonical_messages"][-1]["content"][-1]["type"] == "tool_use" for row in transcripts
    )
    assert count.run_counting(root, live=False, family=family)["records_written"] == 2
    assert all(read(p)["status"] == "counted" for p in tokens(root))


def test_premise_cannot_reuse_even_an_empty_evidence_directory(tmp_path, monkeypatch):
    from benchmarks.agentic import live

    out = tmp_path / "premise"
    out.mkdir()
    monkeypatch.setattr(live, "authorize_live", lambda _: None)
    monkeypatch.setattr(cli, "_container_launcher", lambda *a: pytest.fail("reused scratch"))
    assert (
        premise_module().main(
            [
                "--scratch",
                str(tmp_path),
                "--image",
                "synthetic",
                "--keys-file",
                str(tmp_path / "unread"),
            ]
        )
        == 2
    )
