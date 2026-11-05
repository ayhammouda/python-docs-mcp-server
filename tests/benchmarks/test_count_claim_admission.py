"""C2 and G4 must use the count receipts that support public comparisons."""

import json
import socket
from pathlib import Path

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.agentic_report import _RunMetadata
from benchmarks.grading.score import score_run_dir
from benchmarks.report import _load_run_bundle
from tests.benchmarks.accepted_report_fixture import airgapped_recording, digest, read, write
from tests.benchmarks.test_agentic_report import FAMILIES, SCHEMA, _readme, _render
from tests.benchmarks.test_agentic_report import validated_bundle as validated_bundle
from tests.benchmarks.test_agentic_report import validated_template as validated_template
from tests.benchmarks.test_scoring_publication import _snapshot
from tests.benchmarks.test_verified_disclosures import record_audit

QID = "SYN-EX-002"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("count admission must use exported recordings only")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def _score(run: Path):
    return score_run_dir(
        run, schema_path=SCHEMA, g1_paths=sorted((run / "grading/disclosure/g1").glob("*.json")),
    )


def _receipt(run: Path, family: str, qid: str = QID):
    token_path = run / f"tokens/docs-fetch--{family}--r1/{qid}.json"
    token = read(token_path)
    receipt_path = run / f"grading/count-runs/{token['count_run_id']}.json"
    return token_path, token, receipt_path


@pytest.mark.parametrize("families", [(FAMILIES[0],), FAMILIES])
def test_missing_count_invocations_withhold_only_their_c2_before_g4(validated_bundle, families):
    run = validated_bundle
    before = read(run / "grading/summary.json")
    for family in families:
        _, _, path = _receipt(run, family)
        path.unlink()  # Leave the genuine token projection untouched.
    # Cached passing claims must not outrank the current count capture.
    stale = _RunMetadata(_load_run_bundle(run))
    summary = _score(run)
    fresh = _RunMetadata(_load_run_bundle(run))
    expected_g4 = True if len(families) == 1 else None
    for current in (stale.summary, summary, fresh.summary):
        assert current["gates"]["G4"]["passed"] is expected_g4
        for claims in (current["claims"], *current["claims_by_judge"].values()):
            for family, claim in claims["C2"].items():
                assert claim["passed"] is (None if family in families else True)
                assert (claim["interval"] is None) is (family in families)
    assert summary["spend"] == before["spend"]
    assert summary["graded_cells"] == 300
    assert summary["gates"]["G3"] == before["gates"]["G3"]
    assert fresh.grade_capture.graded_answers == 300
    assert fresh.summary["gates"]["G3"]["details"]["units"] == 300
    confusion = fresh.summary["breakdowns"]["G3"]["confusion"]
    assert sum(sum(row.values()) for row in confusion.values()) == 300
    text, readme = _render(run), _readme(run)
    assert ("Grading outcome: Validated" in text) is (len(families) == 1)
    for family in FAMILIES:
        assert (f"- {family}: python-docs-mcp-server uses" in readme) is (family not in families)


@pytest.mark.parametrize("change", [
    "coordinate", "digest", "corrupt", "projection", "missing-result", "frozen-input",
])
def test_count_binding_failures_withhold_primary_and_single_judge_c2(validated_bundle, change):
    run = validated_bundle
    family = FAMILIES[1]
    token_path, token, receipt_path = _receipt(run, family)
    receipt = read(receipt_path)
    relative, = receipt["results"]
    if change == "coordinate":
        receipt["results"] = {"../foreign.json": receipt["results"][relative]}
    elif change == "digest":
        receipt["results"][relative] = "f" * 64
    elif change == "corrupt":
        receipt_path.write_text("{broken recorded invocation")
    elif change == "projection":
        write(token_path, {**token, "client_wrapped_tokens": 1})
    elif change == "missing-result":
        (run / "grading/counts" / token["count_run_id"] / relative).unlink()
    else:
        path = run / "grading/count-inputs.json"
        inputs = read(path)
        inputs.pop(digest(relative[:-5].encode()))
        write(path, inputs)
    if change in {"coordinate", "digest"}:
        write(receipt_path, receipt)
    summary = _score(run)
    meta = _RunMetadata(_load_run_bundle(run))
    for current in (summary, meta.summary):
        assert current["gates"]["G4"]["passed"] is True
        for claims in (current["claims"], *current["claims_by_judge"].values()):
            assert claims["C2"][family]["passed"] is None
            assert claims["C2"][family]["interval"] is None
            assert claims["C2"][FAMILIES[0]]["passed"] is True


@pytest.mark.parametrize("flagged", ["SYN-EX-001", QID])
def test_retained_counts_use_the_same_admission_and_only_retained_items(validated_bundle, flagged):
    run = validated_bundle
    record_audit(run, flagged=(flagged,))
    _, _, path = _receipt(run, FAMILIES[1])
    path.unlink()
    summary = _score(run)
    meta = _RunMetadata(_load_run_bundle(run))
    assert meta.audit["verified"] is True
    assert meta.tier == "Validated"  # The other family still supplies a valid C2.
    for retained in (summary["claims_without_flagged_keys"], meta.retained):
        assert retained["C2"][FAMILIES[1]]["passed"] is (True if flagged == QID else None)
        assert retained["C2"][FAMILIES[0]]["passed"] is True
    text = _render(run)
    verdict = "pass, 0.500" if flagged == QID else "not evaluable"
    assert f"Without flagged questions, C2/{FAMILIES[1]}: {verdict}" in text


def test_missing_hosted_count_preserves_independently_proved_local_c3(validated_bundle):
    run = validated_bundle
    airgapped_recording(run, FAMILIES[0])
    before = _score(run)
    assert before["tier"] == "Validated"
    _, _, path = _receipt(run, FAMILIES[1])
    path.unlink()
    # Local correctness is independent even of its own normalized count proof.
    local_token = read(run / f"tokens/python-docs--{FAMILIES[0]}--r1/{QID}.json")
    (run / f"grading/count-runs/{local_token['count_run_id']}.json").unlink()
    summary = _score(run)
    meta = _RunMetadata(_load_run_bundle(run))
    for current in (summary, meta.summary):
        assert current["gates"]["G4"]["passed"] is True
        assert current["claims"]["C2"][FAMILIES[1]]["passed"] is None
        assert current["claims"]["C3"] == before["claims"]["C3"]
        for claims in current["claims_by_judge"].values():
            assert claims["C2"][FAMILIES[1]]["passed"] is None
            assert claims["C3"][FAMILIES[0]]["passed"] is True
    assert summary["spend"] == before["spend"]
    assert summary["spend"]["graded_answers"] == 350
    assert summary["gates"]["G3"]["details"]["units"] == 300
    assert "Grading outcome: Validated" in _render(run)
    text = _readme(run)
    assert "fewer normalized tokens" not in text
    assert "answers the corpus better" in text


@pytest.mark.parametrize("where", ["invocation", "result", "input"])
def test_count_credentials_are_refused_before_any_scoring_or_report_write(
    validated_bundle, where, capsys,
):
    run = validated_bundle
    # Keep real prior output sinks; a refusal must leave their bytes and mtimes intact.
    _render(run)
    _readme(run)
    _, token, receipt_path = _receipt(run, FAMILIES[0])
    relative, = read(receipt_path)["results"]
    path = {
        "invocation": receipt_path,
        "result": run / "grading/counts" / token["count_run_id"] / relative,
        "input": run / "grading/count-inputs.json",
    }[where]
    marker = "AIza" + "g" * 35
    value = read(path)
    value["synthetic_marker"] = marker
    path.write_text(json.dumps(value).replace(marker, r"\u0041" + marker[1:]))
    before = _snapshot(run)
    for action in (_score, _render, _readme):
        with pytest.raises(KeyLeakError) as caught:
            action(run)
        assert marker not in str(caught.value)
        assert _snapshot(run) == before
    assert marker not in str(capsys.readouterr())
