"""Agentic benchmark harness (docs/benchmarks/AGENTIC-BENCHMARK-DESIGN.md).

Real models choose tools in a loop over the systems under test. A host
orchestrator holds the spend ledger and never a provider key; it runs one
worker process per (model, system) pair. ``python -m benchmarks agentic-run``
is the entry point.
"""
