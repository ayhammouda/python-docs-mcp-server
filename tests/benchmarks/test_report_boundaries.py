"""Report readers must not import the scoring command's orchestration."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "modules",
    [
        ("benchmarks.grading.publication", "benchmarks.agentic_report"),
        ("benchmarks.agentic_report", "benchmarks.grading.publication"),
        ("benchmarks.grading.disclosure", "benchmarks.agentic_report"),
        ("benchmarks.agentic_report", "benchmarks.grading.disclosure"),
    ],
)
def test_publication_imports_do_not_load_scoring_or_dispatch(modules: tuple[str, ...]) -> None:
    script = (
        "import importlib, sys; "
        f"[importlib.import_module(m) for m in {modules!r}]; "
        "assert 'benchmarks.grading.score' not in sys.modules; "
        "assert 'benchmarks.scoring' not in sys.modules; "
        "assert 'benchmarks.report' not in sys.modules"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("first", range(5))
def test_report_and_scoring_import_order_and_shared_helper_identity(first: int) -> None:
    modules = (
        "benchmarks.report", "benchmarks.agentic_report", "benchmarks.grading.publication",
        "benchmarks.grading.score", "benchmarks.scoring",
    )
    ordered = modules[first:] + modules[:first]
    script = (
        "import importlib; "
        f"[importlib.import_module(m) for m in {ordered!r}]; "
        "from benchmarks import report, report_common; "
        "from benchmarks.grading import score, aggregate, serialization, publication; "
        "assert callable(publication.reconcile_gates); "
        "assert score._own is aggregate.own_grade; "
        "assert score._jsonable is serialization.jsonable; "
        "assert report.RunBundle is report_common.RunBundle; "
        "assert report.CellRecord is report_common.CellRecord; "
        "assert all(getattr(report, n) is getattr(report_common, n) "
        "for n in ('_cell', '_table', '_relative_link', '_percentile'))"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
