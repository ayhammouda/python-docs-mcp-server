from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from benchmarks.model_matrix import (
    METHODOLOGY_TOKEN_LABEL,
    REQUIRED_PROVIDERS,
    ModelFamily,
    load_model_matrix,
    tool_model_cells,
    validate_manifest_against_matrix,
)
from benchmarks.runner import BenchmarkValidationError, Competitor

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_MATRIX_PATH = REPO_ROOT / "docs" / "benchmarks" / "model-matrix.yml"


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _family(**overrides: object) -> dict:
    base = {
        "id": "openai-test",
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "client": "openai-python",
        "token_count_method": "claude_normalized_payload",
        "latency_scope": "request_dispatch_to_final_answer",
        "headline_eligible": False,
        "headline_ineligibility_reason": "no live run yet",
    }
    base.update(overrides)
    return base


def _matrix_yaml(path: Path, families: list[dict] | None = None) -> Path:
    return _write_yaml(
        path,
        {
            "methodology_token_label": METHODOLOGY_TOKEN_LABEL,
            "model_families": families
            if families is not None
            else [
                _family(),
                _family(id="google-test", provider="google", model_id="gemini-1.5-flash"),
            ],
        },
    )


def test_real_model_matrix_file_loads_and_covers_required_providers() -> None:
    matrix = load_model_matrix(MODEL_MATRIX_PATH)

    assert matrix.methodology_token_label == METHODOLOGY_TOKEN_LABEL
    providers_present = {family.provider for family in matrix.model_families}
    for provider in REQUIRED_PROVIDERS:
        assert provider in providers_present

    ids = [family.id for family in matrix.model_families]
    assert len(ids) == len(set(ids)), "model family ids must be unique"

    for family in matrix.model_families:
        assert family.provider
        assert family.model_id
        assert family.client
        assert family.token_count_method
        assert family.latency_scope
        assert isinstance(family.headline_eligible, bool)
        if not family.headline_eligible:
            assert family.headline_ineligibility_reason


def test_load_model_matrix_accepts_minimal_valid_file(tmp_path: Path) -> None:
    matrix = load_model_matrix(_matrix_yaml(tmp_path / "matrix.yml"))

    assert len(matrix.model_families) == 2
    assert {family.provider for family in matrix.model_families} == {"openai", "google"}


def test_load_model_matrix_requires_openai_and_google(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="missing.*google"):
        load_model_matrix(_matrix_yaml(tmp_path / "matrix.yml", families=[_family()]))


def test_load_model_matrix_rejects_wrong_token_label(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "matrix.yml",
        {
            "methodology_token_label": "OpenAI Tokens",
            "model_families": [
                _family(),
                _family(id="google-test", provider="google"),
            ],
        },
    )

    with pytest.raises(BenchmarkValidationError, match="Claude Tokens \\(Normalized Payload\\)"):
        load_model_matrix(path)


def test_load_model_matrix_rejects_missing_token_label(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "matrix.yml",
        {"model_families": [_family(), _family(id="g", provider="google")]},
    )

    with pytest.raises(BenchmarkValidationError, match="methodology_token_label"):
        load_model_matrix(path)


def test_load_model_matrix_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = _matrix_yaml(
        tmp_path / "matrix.yml",
        families=[_family(), _family(provider="google")],
    )

    with pytest.raises(BenchmarkValidationError, match="duplicate model family id: openai-test"):
        load_model_matrix(path)


def test_load_model_matrix_rejects_missing_required_field(tmp_path: Path) -> None:
    broken = _family(id="google-test", provider="google")
    del broken["token_count_method"]
    path = _matrix_yaml(tmp_path / "matrix.yml", families=[_family(), broken])

    with pytest.raises(BenchmarkValidationError, match="token_count_method"):
        load_model_matrix(path)


def test_load_model_matrix_requires_ineligibility_reason_when_not_headline_eligible(
    tmp_path: Path,
) -> None:
    broken = _family(id="google-test", provider="google", headline_ineligibility_reason="")
    path = _matrix_yaml(tmp_path / "matrix.yml", families=[_family(), broken])

    with pytest.raises(BenchmarkValidationError, match="headline_ineligibility_reason"):
        load_model_matrix(path)


def test_load_model_matrix_allows_headline_eligible_without_reason(tmp_path: Path) -> None:
    eligible = _family(
        id="google-test",
        provider="google",
        headline_eligible=True,
        headline_ineligibility_reason=None,
    )
    path = _matrix_yaml(tmp_path / "matrix.yml", families=[_family(), eligible])

    matrix = load_model_matrix(path)

    google_family = next(f for f in matrix.model_families if f.provider == "google")
    assert google_family.headline_eligible is True
    assert google_family.headline_ineligibility_reason is None


def test_load_model_matrix_rejects_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "matrix.yml"
    path.write_text("model_families: [unterminated\n", encoding="utf-8")

    with pytest.raises(BenchmarkValidationError, match="not valid YAML"):
        load_model_matrix(path)


def test_load_model_matrix_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkValidationError, match="does not exist"):
        load_model_matrix(tmp_path / "missing.yml")


def test_tool_model_cells_cross_multiplies_without_averaging() -> None:
    families = [
        ModelFamily(
            id="openai-test",
            provider="openai",
            model_id="gpt-4o-mini",
            client="openai-python",
            token_count_method="claude_normalized_payload",
            latency_scope="request_dispatch_to_final_answer",
            headline_eligible=False,
            headline_ineligibility_reason="no live run yet",
            raw={},
        ),
        ModelFamily(
            id="google-test",
            provider="google",
            model_id="gemini-1.5-flash",
            client="google-genai",
            token_count_method="claude_normalized_payload",
            latency_scope="request_dispatch_to_final_answer",
            headline_eligible=False,
            headline_ineligibility_reason="no live run yet",
            raw={},
        ),
    ]

    cells = tool_model_cells(["python-docs-mcp-server", "no-mcp-baseline"], families)

    assert len(cells) == 4
    assert len(set(cells)) == 4, "every (tool, model) pair must be a distinct cell"
    assert ("python-docs-mcp-server", "openai-test") in cells
    assert ("python-docs-mcp-server", "google-test") in cells
    assert ("no-mcp-baseline", "openai-test") in cells
    assert ("no-mcp-baseline", "google-test") in cells


# --- validate_manifest_against_matrix (issue #86) --------------------------
#
# Confirmed composition decision: the competitor manifest enumerates one
# entry per tool x model pairing; a manifest entry whose declared
# provider/model pair is absent from the model matrix must fail validation
# with a clean BenchmarkValidationError.


def test_validate_manifest_against_matrix_accepts_a_known_pairing(tmp_path: Path) -> None:
    matrix = load_model_matrix(_matrix_yaml(tmp_path / "matrix.yml"))
    competitors = [
        Competitor(
            id="python-docs-mcp-server",
            adapter="python-docs-mcp-stdio",
            raw={"provider": "openai", "model": "gpt-4o-mini"},
        )
    ]

    validate_manifest_against_matrix(competitors, matrix)  # must not raise


def test_validate_manifest_against_matrix_rejects_an_unknown_pairing(tmp_path: Path) -> None:
    matrix = load_model_matrix(_matrix_yaml(tmp_path / "matrix.yml"))
    competitors = [
        Competitor(
            id="python-docs-mcp-server",
            adapter="python-docs-mcp-stdio",
            raw={"provider": "openai", "model": "gpt-4o-does-not-exist"},
        )
    ]

    with pytest.raises(BenchmarkValidationError) as exc_info:
        validate_manifest_against_matrix(competitors, matrix)

    assert "python-docs-mcp-server" in str(exc_info.value)
    assert "openai/gpt-4o-does-not-exist" in str(exc_info.value)


def test_validate_manifest_against_matrix_skips_entries_without_a_full_pairing(
    tmp_path: Path,
) -> None:
    matrix = load_model_matrix(_matrix_yaml(tmp_path / "matrix.yml"))
    competitors = [
        Competitor(id="no-mcp-baseline", adapter="no-mcp-baseline", raw={}),
        Competitor(id="partial", adapter="fake", raw={"provider": "openai"}),
    ]

    validate_manifest_against_matrix(competitors, matrix)  # must not raise: no full pairing


def test_validate_manifest_against_matrix_against_the_real_model_matrix_file() -> None:
    matrix = load_model_matrix(MODEL_MATRIX_PATH)
    competitors = [
        Competitor(
            id="python-docs-mcp-server",
            adapter="python-docs-mcp-stdio",
            raw={"provider": "openai", "model": "gpt-4o-mini"},
        )
    ]

    validate_manifest_against_matrix(competitors, matrix)  # must not raise
