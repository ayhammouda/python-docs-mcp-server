"""The product gate must detect losses, including silent removal of cases."""

from copy import deepcopy

import pytest

from benchmarks.regression import deny_network, regressions


def test_regression_gate_rejects_losses_and_allows_improvements():
    baseline = {
        "corpus_sha256": "abc",
        "index_documents": 3,
        "max_case_seconds": 0.5,
        "cases": {
            "EX-001/3.13": {
                "retrieval_hit_at_5": True,
                "resolved_citations": ["https://docs.python.org/3.13/a.html"],
                "seconds": 0.01,
            }
        },
    }
    assert regressions(deepcopy(baseline), baseline) == []
    improved = deepcopy(baseline)
    improved["cases"]["EX-001/3.13"]["seconds"] = 0.005
    improved["cases"]["EX-001/3.13"]["resolved_citations"].append(
        "https://docs.python.org/3.13/b.html"
    )
    assert regressions(improved, baseline) == []
    for field, value in [("retrieval_hit_at_5", False), ("resolved_citations", []), ("seconds", 1)]:
        changed = deepcopy(baseline)
        changed["cases"]["EX-001/3.13"][field] = value
        assert regressions(changed, baseline)
    for field, value in [("cases", {}), ("corpus_sha256", "changed"), ("index_documents", 0)]:
        changed = deepcopy(baseline)
        changed[field] = value
        assert regressions(changed, baseline)
    with pytest.raises(RuntimeError, match="Network"):
        deny_network("socket.connect", ())
