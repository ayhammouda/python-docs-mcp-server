"""Competitor manifest eligibility screening (issue #87).

Encodes the methodology's eligibility/honesty rule -- "do not drop failed
or ineligible systems silently" -- as a manifest-*load-time* refusal,
never a per-cell outcome. See ``PLAN-87-competitor-adapters.md`` section
2.4 (mirrored as a comment on issue #87) for the full rationale: the
rejected first design (a per-cell ``BenchmarkCellFailure`` for an ineligible
competitor) would have the runner score every excluded competitor as a
failed cell (``benchmarks.runner._execute_cell`` turns any
``BenchmarkCellFailure`` into ``status: failed``), and failed cells stay in
the correctness/error-rate denominators by design (see
``tests/benchmarks/test_runner.py::test_competitor_cell_failure_is_recorded``)
-- exactly the metrics an exclusion must stay out of. So exclusion instead
means: the manifest never even reaches cell execution.

Convention (manifest authoring, see
``docs/benchmarks/competitor-manifest.template.yml``):

- Every ACTIVE competitor entry (one of the four adapter ids in
  :data:`COMPETITOR_ADAPTER_IDS`, appearing in the manifest's
  ``competitors:`` list) must carry ``pin``, ``terms_check``, and
  ``eligibility`` metadata, validated here.
- A competitor that is not eligible to run belongs in the manifest's
  top-level ``exclusions:`` block instead of ``competitors:`` -- this
  module does not inspect that block (the runner's execution path already
  ignores unknown top-level keys, and ``benchmarks.runner._write_snapshot``
  preserves it byte-for-byte in every run's artifacts regardless, so
  exclusions stay documented and auditable without ever being scored).
- ``eligibility.status: excluded`` on an ACTIVE ``competitors:`` entry is
  therefore always an authoring mistake -- that competitor should have been
  moved to ``exclusions:`` -- and is rejected with a message pointing there.

Only entries whose ``adapter`` is one of the four competitor adapter ids are
validated. This is deliberate: the existing ``fake``/``no-mcp-baseline`` and
``python-docs-mcp-stdio`` adapters (issues #72/#86) carry no pin/terms
metadata and merged fixtures in ``tests/benchmarks/test_runner.py`` build
manifests without it -- scoping validation to the new competitor ids keeps
this change additive and those merged tests untouched.
"""

from __future__ import annotations

from typing import Any

from benchmarks.runner import BenchmarkValidationError

#: The four competitor adapter ids this module screens. Kept in sync with
#: the dispatch entries added to ``benchmarks.runner._ADAPTER_DISPATCH``.
COMPETITOR_ADAPTER_IDS = frozenset({"context7", "gitmcp", "deepwiki", "ref-tools"})

#: Recognized ``eligibility.status`` values for an ACTIVE ``competitors:``
#: entry. ``excluded`` is a valid concept but belongs in the manifest's
#: top-level ``exclusions:`` block, never here -- see module docstring.
_ELIGIBLE_STATUSES = {"eligible", "conditional"}

#: Recognized ``pin.kind`` values (PLAN-87 section 1): an npm-pinned client
#: package + version, or a hosted-endpoint-plus-access-date pin for
#: competitors with no pinnable package/image (GitMCP, DeepWiki).
_VALID_PIN_KINDS = {"npm-version", "endpoint-date"}

#: Recognized ``terms_check.verdict`` values (PLAN-87 section 1 dossiers).
_VALID_TERMS_VERDICTS = {
    "permitted",
    "forbidden-without-permission",
    "unclear-permission-recommended",
}


def validate_competitor_eligibility(raw: dict[str, Any]) -> None:
    """Validate one manifest competitor entry's pin/terms_check/eligibility metadata.

    Raises :class:`benchmarks.runner.BenchmarkValidationError`, naming the
    competitor id, when any of the following is missing or malformed:

    - ``pin``: must be a mapping with non-empty ``kind`` (one of
      :data:`_VALID_PIN_KINDS`), ``value``, and ``access_date``.
    - ``terms_check``: must be a mapping with non-empty ``verdict`` (one of
      :data:`_VALID_TERMS_VERDICTS`), ``checked_on``, and ``source_url``.
    - ``eligibility``: must be a mapping whose ``status`` is one of
      :data:`_ELIGIBLE_STATUSES`. ``status: excluded`` is rejected with a
      dedicated message (see module docstring).
    """
    competitor_id = raw.get("id", "<unknown>")

    pin = raw.get("pin")
    if (
        not isinstance(pin, dict)
        or not pin.get("kind")
        or not pin.get("value")
        or not pin.get("access_date")
    ):
        raise BenchmarkValidationError(
            f"competitor {competitor_id!r} is missing required pin metadata: "
            "'pin' must be a mapping with non-empty 'kind', 'value', and 'access_date' "
            "(see docs/benchmarks/competitor-manifest.template.yml)"
        )
    if pin["kind"] not in _VALID_PIN_KINDS:
        raise BenchmarkValidationError(
            f"competitor {competitor_id!r} has unrecognized pin.kind {pin['kind']!r}; "
            f"expected one of {sorted(_VALID_PIN_KINDS)}"
        )

    terms_check = raw.get("terms_check")
    if (
        not isinstance(terms_check, dict)
        or not terms_check.get("verdict")
        or not terms_check.get("checked_on")
        or not terms_check.get("source_url")
    ):
        raise BenchmarkValidationError(
            f"competitor {competitor_id!r} is missing required terms_check metadata: "
            "'terms_check' must be a mapping with non-empty 'verdict', 'checked_on', and "
            "'source_url' (see docs/benchmarks/competitor-manifest.template.yml)"
        )
    if terms_check["verdict"] not in _VALID_TERMS_VERDICTS:
        raise BenchmarkValidationError(
            f"competitor {competitor_id!r} has unrecognized terms_check.verdict "
            f"{terms_check['verdict']!r}; expected one of {sorted(_VALID_TERMS_VERDICTS)}"
        )

    eligibility = raw.get("eligibility")
    status = eligibility.get("status") if isinstance(eligibility, dict) else None
    if status == "excluded":
        raise BenchmarkValidationError(
            f"competitor {competitor_id!r} declares eligibility.status: excluded in the "
            "active 'competitors:' list; move it to the manifest's top-level 'exclusions:' "
            "block instead (see docs/benchmarks/competitor-manifest.template.yml) -- an "
            "excluded competitor must never execute a scored cell"
        )
    if status not in _ELIGIBLE_STATUSES:
        raise BenchmarkValidationError(
            f"competitor {competitor_id!r} must declare eligibility.status as one of "
            f"{sorted(_ELIGIBLE_STATUSES)} (got {status!r})"
        )


def validate_manifest_eligibility(manifest_data: dict[str, Any]) -> None:
    """Validate every ACTIVE competitor-adapter entry in a parsed manifest mapping.

    Called once, on the raw parsed manifest ``dict`` (before
    ``benchmarks.runner._load_competitors`` builds :class:`Competitor`
    instances and before any cell executes) by
    ``benchmarks.runner.run_benchmark``. Entries whose ``adapter`` is not one
    of :data:`COMPETITOR_ADAPTER_IDS` are skipped -- this validator only
    applies to the four competitor docs-MCP adapters (see module docstring).
    Malformed entries in other respects (missing ``id``, missing
    ``adapter``, non-mapping items) are left to
    ``benchmarks.runner._load_competitors``'s own validation, which runs
    afterward.
    """
    competitors = manifest_data.get("competitors")
    if not isinstance(competitors, list):
        return
    for item in competitors:
        if not isinstance(item, dict):
            continue
        if item.get("adapter") in COMPETITOR_ADAPTER_IDS:
            validate_competitor_eligibility(item)
