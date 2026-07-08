"""Model/client matrix loader for the v0.5.0 public benchmark.

Loads and validates ``docs/benchmarks/model-matrix.yml``: the MODEL axis of
the benchmark's evaluation matrix (see that file's header comment and
``docs/benchmarks/PUBLIC-BENCHMARK-METHODOLOGY.md``). The TOOL axis is the
existing competitor manifest consumed by ``benchmarks.runner``.

This module intentionally does not execute anything against
``benchmarks.runner``. The maintainer confirmed (issue #86, 2026-07-08) how
tool x model composition should affect the artifact shapes already covered
by ``tests/benchmarks/test_runner.py``: cell composition stays competitor x
question (no cell-shape or artifact-layout change); a manifest instead
enumerates one entry per tool x model pairing, and this module gains a
manifest<->matrix validator (:func:`validate_manifest_against_matrix`) so a
manifest entry's declared provider/model pairing can be checked against
this file before a run. Wiring model-matrix cells directly into the
runner's per-cell execution path remains out of scope. This package
defines the matrix, the cross-multiplication contract, the manifest<->matrix
validator, and mocked provider adapters (see ``benchmarks.adapters``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from benchmarks.runner import BenchmarkValidationError, Competitor

#: Label for tokens counted by the benchmark's own methodology tokenizer
#: (Claude token counting after client-side rewrap, per
#: PUBLIC-BENCHMARK-METHODOLOGY.md "Token Measurement" / roadmap decision
#: 5.8). Any report or record using this metric must use this exact label so
#: it is never confused with a provider's own OpenAI/Google billing tokens.
METHODOLOGY_TOKEN_LABEL = "Claude Tokens (Normalized Payload)"

#: Providers with at least one model family entry required by issue #73.
REQUIRED_PROVIDERS = ("openai", "google")

_REQUIRED_STRING_FIELDS = (
    "provider",
    "model_id",
    "client",
    "token_count_method",
    "latency_scope",
)


@dataclass(frozen=True)
class ModelFamily:
    """One validated ``model-matrix.yml`` entry."""

    id: str
    provider: str
    model_id: str
    client: str
    token_count_method: str
    latency_scope: str
    headline_eligible: bool
    headline_ineligibility_reason: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class ModelMatrix:
    """The full validated model matrix."""

    methodology_token_label: str
    model_families: list[ModelFamily]

    def by_provider(self, provider: str) -> list[ModelFamily]:
        return [family for family in self.model_families if family.provider == provider]


def load_model_matrix(path: Path) -> ModelMatrix:
    """Load and validate a ``model-matrix.yml`` file.

    Raises ``BenchmarkValidationError`` (the same validation-error type used
    by ``benchmarks.runner``) on any structural problem, so callers can rely
    on one exception type across the benchmark's YAML-config surface.
    """
    if not path.exists():
        raise BenchmarkValidationError(f"model matrix file does not exist: {path}")
    with path.open("r", encoding="utf-8") as file:
        try:
            data = yaml.safe_load(file)
        except yaml.YAMLError as exc:
            raise BenchmarkValidationError(f"model matrix file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkValidationError("model matrix file must contain a YAML mapping")

    token_label = data.get("methodology_token_label")
    if not isinstance(token_label, str) or not token_label.strip():
        raise BenchmarkValidationError(
            "model matrix file must set a non-empty 'methodology_token_label'"
        )
    if token_label != METHODOLOGY_TOKEN_LABEL:
        raise BenchmarkValidationError(
            "model matrix 'methodology_token_label' must be exactly "
            f"{METHODOLOGY_TOKEN_LABEL!r} so methodology-tokenizer counts are never "
            f"confused with provider billing tokens; got {token_label!r}"
        )

    families = _load_model_families(data)
    providers_present = {family.provider for family in families}
    missing = [provider for provider in REQUIRED_PROVIDERS if provider not in providers_present]
    if missing:
        raise BenchmarkValidationError(
            "model matrix must include at least one model family for each required "
            f"provider {list(REQUIRED_PROVIDERS)}; missing: {missing}"
        )

    return ModelMatrix(methodology_token_label=token_label, model_families=families)


def _load_model_families(data: dict[str, Any]) -> list[ModelFamily]:
    items = data.get("model_families")
    if not isinstance(items, list) or not items:
        raise BenchmarkValidationError(
            "model matrix must contain a non-empty 'model_families' list"
        )

    families: list[ModelFamily] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        label = f"model family at index {index}"
        if not isinstance(item, dict):
            raise BenchmarkValidationError(f"{label} must be a mapping")

        family_id = item.get("id")
        if not isinstance(family_id, str) or not family_id.strip():
            raise BenchmarkValidationError(f"{label} is missing required 'id'")
        if family_id in seen:
            raise BenchmarkValidationError(f"duplicate model family id: {family_id}")
        seen.add(family_id)

        for field in _REQUIRED_STRING_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise BenchmarkValidationError(
                    f"model family {family_id!r} is missing required {field!r}"
                )

        headline_eligible = item.get("headline_eligible")
        if not isinstance(headline_eligible, bool):
            raise BenchmarkValidationError(
                f"model family {family_id!r} must set 'headline_eligible' to true or false"
            )
        headline_ineligibility_reason = item.get("headline_ineligibility_reason")
        if not headline_eligible and (
            not isinstance(headline_ineligibility_reason, str)
            or not headline_ineligibility_reason.strip()
        ):
            raise BenchmarkValidationError(
                f"model family {family_id!r} has headline_eligible: false and must include a "
                "non-empty 'headline_ineligibility_reason'"
            )
        if headline_eligible:
            headline_ineligibility_reason = None

        families.append(
            ModelFamily(
                id=family_id,
                provider=item["provider"],
                model_id=item["model_id"],
                client=item["client"],
                token_count_method=item["token_count_method"],
                latency_scope=item["latency_scope"],
                headline_eligible=headline_eligible,
                headline_ineligibility_reason=headline_ineligibility_reason,
                raw=item,
            )
        )
    return families


def tool_model_cells(
    competitor_ids: list[str], model_families: list[ModelFamily]
) -> list[tuple[str, str]]:
    """Cross-multiply the tool axis and the model axis into evaluation cells.

    Every eligible tool (``competitor_ids``, from the existing competitor
    manifest) is paired with every model family independently: the returned
    list has ``len(competitor_ids) * len(model_families)`` entries, one per
    ``(competitor_id, model_family_id)`` pair.

    Callers must never average a score across the model-family axis when
    reducing these cells into a report. Only within-cell aggregation (e.g.
    across corpus questions, for a fixed ``(competitor_id, model_family_id)``
    pair) is valid; any aggregate across model families must be computed
    separately from these per-cell results and labeled as an aggregate.
    """
    return [
        (competitor_id, family.id)
        for competitor_id in competitor_ids
        for family in model_families
    ]


def validate_manifest_against_matrix(
    competitors: list[Competitor], matrix: ModelMatrix
) -> None:
    """Validate a competitor manifest's tool x model pairings against the matrix.

    Per the confirmed composition decision (issue #86, 2026-07-08): the
    competitor manifest enumerates one entry per tool x model pairing, so a
    manifest entry that declares both a ``provider`` and a ``model`` field
    (the pairing this validator checks) must correspond to a
    ``docs/benchmarks/model-matrix.yml`` entry with that exact
    ``(provider, model_id)`` combination. Raises
    ``BenchmarkValidationError`` naming the offending competitor id and
    pairing when no match is found.

    Manifest entries that omit ``provider`` or ``model`` (e.g. the
    model-agnostic no-MCP baseline) are not a tool x model pairing and are
    skipped -- this validator only checks pairings a manifest actually
    declares.

    This function is not invoked automatically by ``benchmarks.runner``;
    callers (a CLI, a maintainer script, or a test) run it explicitly
    before a benchmark run. See this module's docstring for why the
    runner's per-cell dispatch does not consume the model matrix directly.
    """
    known_pairs = {(family.provider, family.model_id) for family in matrix.model_families}
    for competitor in competitors:
        provider = competitor.raw.get("provider")
        model = competitor.raw.get("model")
        has_provider = isinstance(provider, str) and bool(provider)
        has_model = isinstance(model, str) and bool(model)
        if not (has_provider and has_model):
            continue
        if (provider, model) not in known_pairs:
            raise BenchmarkValidationError(
                f"competitor {competitor.id!r} declares provider/model pairing "
                f"{provider}/{model} that is not present in the model matrix; add a "
                "matching model_families entry to docs/benchmarks/model-matrix.yml or "
                "fix the manifest"
            )
