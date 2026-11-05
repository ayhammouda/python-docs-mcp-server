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
        "from benchmarks import report, report_common, agentic_report, report_measurements; "
        "assert agentic_report._GradeCapture is report_measurements.GradeCapture; "
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


def test_report_import_graph_is_acyclic_including_typing_edges() -> None:
    """Moving a reverse edge under TYPE_CHECKING must not recreate a dependency cycle."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    modules = {}
    for path in (root / "benchmarks").rglob("*.py"):
        name = ".".join(path.relative_to(root).with_suffix("").parts)
        if name.endswith(".__init__"):
            name = name.removesuffix(".__init__")
        modules[name] = path
    graph = {}
    for name, path in modules.items():
        edges = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                edges.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"resolve relative import in graph test: {path}"
                module = node.module or ""
                edges.add(module)
                edges.update(f"{module}.{alias.name}" for alias in node.names)
        graph[name] = edges & modules.keys()
    visited = set()

    def visit(module, chain, origin):
        assert module != origin or not chain, " -> ".join([*chain, module])
        if module not in visited:
            visited.add(module)
            for imported in sorted(graph[module]):
                visit(imported, [*chain, module], origin)

    for name in (
        "benchmarks.report", "benchmarks.agentic_report", "benchmarks.grading.publication",
        "benchmarks.grading.disclosure", "benchmarks.report_common",
        "benchmarks.report_measurements", "benchmarks.report_contracts", "benchmarks.report_files",
        "benchmarks.grading.aggregate", "benchmarks.grading.vote_capture",
        "benchmarks.grading.calibration", "benchmarks.grading.calibration_history",
    ):
        visited.clear()
        visit(name, [], name)


def test_malformed_gate_container_is_unavailable(tmp_path) -> None:
    from types import SimpleNamespace

    from benchmarks.grading.publication import reconcile_gates
    from benchmarks.report_measurements import CountCapture

    run = SimpleNamespace(
        summary={"gates": ["malformed export"]},
        count_capture=CountCapture(tmp_path),
        transcripts={}, tokens={}, families={},
    )
    gates = reconcile_gates(run)
    assert set(gates) == {"G1", "G2", "G3", "G4"}
    assert all(gate.passed is None for gate in gates.values())
    assert run.counts == {}
