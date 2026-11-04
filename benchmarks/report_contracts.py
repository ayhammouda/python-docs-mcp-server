"""Read-only publication view; no dependency on either report renderer.

Raw exported records remain object-valued until each consumer validates the fields
it uses. Grade and count evidence retain their canonical shared capture types.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from benchmarks.grading.gates import GateResult
from benchmarks.report_common import RunBundle
from benchmarks.report_measurements import CountCapture, GradeCapture


class PublicationRun(Protocol):
    root: Path
    g1_entries: dict[str, dict[str, object]]
    g1_replays: dict[str, tuple[GateResult, GateResult]]
    retest_unknown: bool
    vote_versions: dict[Path, str | None]
    grading_events: list[dict[str, object]]
    probe_dropped: dict[str, int] | None
    sample_allocation: dict[str, object]
    bundle: RunBundle
    summary: dict[str, object]
    inputs: dict[str, str | None]
    frozen: dict[str, bytes]
    judges: dict[str, dict[str, object]]
    grade_capture: GradeCapture
    count_capture: CountCapture
    transcripts: dict[tuple[str, str], dict[str, object]]
    tokens: dict[tuple[str, str], dict[str, object]]
    reference_sample: dict[str, object]
    plan: dict[str, object]
    families: dict[str, dict[str, object]]
    seed: str
    grading: str
    answering: str
    tier: str
    offline_ok: Callable[[], bool | None]
