"""The product gate must detect losses, including silent removal of cases."""

import sys
from copy import deepcopy

import pytest

import benchmarks.regression as regression
from benchmarks.regression import FORBIDDEN_AUDIT_EVENTS, deny_network, regressions


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


def test_forbidden_audit_events_cover_network_and_process_creation(monkeypatch):
    monkeypatch.setattr(regression, "blocked_attempts", [])
    assert FORBIDDEN_AUDIT_EVENTS == {
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
        "subprocess.Popen",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.startfile",
        "os.system",
        "pty.spawn",
        "webbrowser.open",
    }


@pytest.mark.parametrize("event", ["socket.sendto", "subprocess.Popen"])
def test_deny_network_raises_and_records_the_attempt(monkeypatch, event):
    monkeypatch.setattr(regression, "blocked_attempts", [])
    with pytest.raises(RuntimeError, match="forbidden"):
        deny_network(event, ())
    assert regression.blocked_attempts == [event]


def test_deny_network_lets_local_file_and_database_events_through(monkeypatch):
    monkeypatch.setattr(regression, "blocked_attempts", [])
    deny_network("open", ("index.db", "r", 0))
    deny_network("sqlite3.connect", ("index.db",))
    assert regression.blocked_attempts == []


def test_main_fails_when_library_code_swallowed_a_blocked_attempt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(regression, "blocked_attempts", ["left over from another test"])
    monkeypatch.setattr(sys, "addaudithook", lambda hook: None)  # never hook pytest itself

    def _swallowing_evaluate(index, corpus, schema):
        try:
            deny_network("socket.connect", (None, ("192.0.2.1", 443)))
        except RuntimeError:
            pass  # library code that hides the tripwire
        return {"corpus_sha256": "x", "index_versions": [], "index_documents": 0, "cases": {}}

    monkeypatch.setattr(regression, "evaluate", _swallowing_evaluate)
    output = tmp_path / "evaluation.json"
    monkeypatch.setattr(sys, "argv", ["regression", "--output", str(output), "--record"])
    assert regression.main() == 1
    err = capsys.readouterr().err
    assert "blocked network or process access: socket.connect" in err
    assert "left over" not in err
    assert not output.exists()
