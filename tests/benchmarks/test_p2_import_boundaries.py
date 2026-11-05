"""P2 producers and dispatch must not depend back on their callers."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest


def test_p2_boundaries_have_no_static_return_path() -> None:
    root = Path(__file__).resolve().parents[2]
    modules = {
        ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(".__init__"): path
        for path in (root / "benchmarks").rglob("*.py")
    }
    graph = {}
    for name, path in modules.items():
        edges = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                edges.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"resolve relative import: {path}"
                edges.add(node.module or "")
                edges.update(f"{node.module}.{alias.name}" for alias in node.names)
        graph[name] = edges & modules.keys()

    # Traverse every dependency, including late imports and TYPE_CHECKING edges.
    # These origins extend the established report-root test to the real P2 producers.
    for origin in (
        "benchmarks.agentic.count", "benchmarks.agentic.count_evidence",
        "benchmarks.agentic.host", "benchmarks.agentic.worker", "benchmarks.agentic.run",
        "benchmarks.agentic.cleanup", "benchmarks.agentic.protocol",
        "benchmarks.agentic.types", "benchmarks.agentic.worker_ports",
        "benchmarks.grading.aggregate", "benchmarks.grading.preflight",
        "benchmarks.grading.run", "benchmarks.grading.worker_task",
    ):
        visited = set()

        def visit(name: str, chain: list[str]) -> None:
            assert name != origin or not chain, " -> ".join([*chain, name])
            if name not in visited:
                visited.add(name)
                for target in sorted(graph[name]):
                    visit(target, [*chain, name])

        visit(origin, [])


@pytest.mark.parametrize("reverse", [False, True])
def test_p2_ordinary_import_orders_preserve_shared_objects(reverse: bool) -> None:
    modules = (
        "benchmarks.report_files", "benchmarks.report_measurements",
        "benchmarks.agentic.egress", "benchmarks.agentic.host",
        "benchmarks.agentic.worker", "benchmarks.agentic.count",
        "benchmarks.agentic.count_evidence", "benchmarks.agentic.run",
        "benchmarks.grading.preflight", "benchmarks.grading.aggregate",
        "benchmarks.grading.worker_task", "benchmarks.grading.run", "benchmarks.__main__",
    )
    script = "\n".join(f"import {name}" for name in (modules[::-1] if reverse else modules))
    script += """
from benchmarks.agentic import cleanup, count, count_evidence, host, protocol, run, types, worker
from benchmarks.grading import aggregate, run as grading_run, worker_task
assert count.CountItem is count_evidence.CountItem
assert aggregate.OUTPUT_CAP_STOPS is run.OUTPUT_CAP_STOPS is types.OUTPUT_CAP_STOPS
assert run._until_signalled is grading_run._until_signalled is cleanup.until_signalled
assert host.MAX_RERUNS == worker.MAX_RERUNS == protocol.MAX_RERUNS == 2
assert host.RESULT_CHUNK_BYTES == worker_task.RESULT_CHUNK_BYTES == protocol.MAX_FRAME_BYTES // 4
assert callable(worker.main_worker) and callable(worker.run_worker)
assert callable(count.run_counting) and callable(count.run_count_task)
assert callable(grading_run.run_grading) and callable(worker_task.run_batch_task)
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
