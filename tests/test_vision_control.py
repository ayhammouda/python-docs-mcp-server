"""Negative cases for the fixed-repository privileged broker boundary."""

import base64
import runpy
from pathlib import Path

import pytest

CONTROL = runpy.run_path(str(Path(__file__).parents[1] / "ops/vision/control.py"))


def test_api_rejects_credentials_protections_checks_merges_and_other_repositories():
    validate = CONTROL["validate_api"]
    validate("GET", "issues?state=open", None)
    validate("POST", "issues/1/comments", {"body": "Evidence-based update"})
    validate("POST", "pulls", {"base": "main", "head": "codex/fix", "title": "Fix"})
    for method, path in [
        ("GET", "../../user"),
        ("GET", "https://evil.example"),
        ("POST", "check-runs"),
        ("POST", "git/refs"),
        ("PUT", "pulls/1/merge"),
        ("PATCH", "rulesets/1"),
        ("GET", "actions/secrets"),
        ("GET", "%2e%2e/user"),
        ("DELETE", "issues/1"),
    ]:
        with pytest.raises(ValueError):
            validate(method, path, {})


def test_publication_rejects_unsafe_refs_paths_and_file_modes():
    validate = CONTROL["validate_publication"]
    data = {
        "branch": "codex/fix",
        "base_sha": "a" * 40,
        "message": "Fix",
        "changes": [
            {
                "path": "src/example.py",
                "mode": "100644",
                "content": base64.b64encode(b"print('ok')").decode(),
            }
        ],
    }
    validate(data)
    for branch in ["main", "refs/tags/v1", "codex/../main"]:
        with pytest.raises(ValueError):
            validate({**data, "branch": branch})
    for path in ["../secret", "/etc/passwd", ".git/config", "a//b", "a\\b"]:
        with pytest.raises(ValueError):
            validate({**data, "changes": [{"path": path}]})
    with pytest.raises(ValueError):
        validate({**data, "changes": [{"path": "link", "mode": "120000", "content": "eA=="}]})


def test_feature_work_requires_a_user_problem_and_dated_sources():
    validate = CONTROL["evidence"]
    with pytest.raises(ValueError):
        validate({"kind": "feature", "rationale": "Trending on social media"})
    data = {
        "kind": "feature",
        "user_problem": "A reproducible task fails",
        "sources": [{"url": "https://docs.python.org/3.13/", "date": "2026-10-01"}],
        "baseline": "Current failing case",
        "target": "Case succeeds",
        "acceptance": "Test",
        "non_goals": "Other versions",
        "review_date": "2026-10-15",
        "revisit_condition": "No demonstrated task improvement",
    }
    validate(data)
    with pytest.raises(ValueError):
        validate({**data, "sources": ["popularity"]})


def test_unreviewed_publication_never_creates_an_executable_ref(monkeypatch):
    module = CONTROL["publish"].__globals__
    calls = []

    def api(path, method="GET", data=None, role="owner"):
        calls.append((path, method))
        if path.startswith("git/commits/"):
            return {"tree": {"sha": "t" * 40}}
        if path == "branches/main":
            return {"commit": {"sha": "a" * 40}}
        return {"sha": "b" * 40}

    def reject(*args):
        raise ValueError("Independent review rejected workflow")

    monkeypatch.setitem(module, "api", api)
    monkeypatch.setitem(module, "review", reject)
    with pytest.raises(ValueError, match="review rejected"):
        CONTROL["publish"](
            {
                "branch": "codex/change",
                "base_sha": "a" * 40,
                "message": "Change",
                "decision": {
                    "kind": "policy",
                    "rationale": "CI change",
                    "acceptance": "Tests",
                    "security_impact": "Workflow review required",
                },
                "changes": [{"path": ".github/workflows/ci.yml", "content": "eA=="}],
            }
        )
    assert not any(path.startswith("git/ref") for path, _ in calls)


def test_activation_missing_and_stale_or_forged_review_fail_closed(tmp_path, monkeypatch):
    module = CONTROL["dispatch"].__globals__
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    with pytest.raises(ValueError, match="not activated"):
        CONTROL["dispatch"]({"operation": "merge", "pr": 1, "head_sha": "a" * 40})
    import json

    (tmp_path / "verifier.json").write_text(json.dumps({"app_id": 42}))
    pr = {"state": "open", "head": {"sha": "a" * 40}, "base": {"ref": "main"}}
    check = {
        "id": 1,
        "name": "Independent verification",
        "app": {"id": 42},
        "conclusion": "success",
        "external_id": "b" * 40 + ":" + "a" * 40,
    }
    monkeypatch.setitem(module, "api", lambda *_: {"check_runs": [check]})
    assert CONTROL["require_verified"](pr, "a" * 40, "b" * 40) == check
    for field, value in [
        ("app", {"id": 15368}),
        ("external_id", "stale"),
        ("conclusion", "failure"),
    ]:
        wrong = {**check, field: value}
        monkeypatch.setitem(module, "api", lambda *_, wrong=wrong: {"check_runs": [wrong]})
        with pytest.raises(ValueError, match="Missing/stale"):
            CONTROL["require_verified"](pr, "a" * 40, "b" * 40)


def test_installer_rejects_worker_symlinks_before_ownership_changes(tmp_path):
    installer = runpy.run_path(str(Path(__file__).parents[1] / "ops/vision/install.py"))
    privileged = tmp_path / "privileged"
    privileged.mkdir()
    link = tmp_path / ".cache"
    link.symlink_to(privileged)
    with pytest.raises(ValueError, match="symlink"):
        installer["directory"](link)
    secret = privileged / "config"
    secret.write_text("unchanged")
    target = tmp_path / "authorized_keys"
    target.symlink_to(secret)
    import os

    with pytest.raises(OSError):
        installer["owned"](target, "replacement", os.getuid(), os.getgid())
    assert secret.read_text() == "unchanged"


def test_thread_resolution_rejects_a_thread_from_another_pr(monkeypatch):
    module = CONTROL["resolve_thread"].__globals__
    monkeypatch.setitem(module, "api", lambda *args: {"commit": {"sha": "b" * 40}})
    monkeypatch.setitem(module, "require_verified", lambda *args: {})
    monkeypatch.setitem(module, "token", lambda *args: "test")
    monkeypatch.setitem(
        module,
        "request",
        lambda *args: {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": [{"id": "own", "isResolved": False}]}
                    }
                }
            }
        },
    )
    with pytest.raises(ValueError, match="must belong"):
        CONTROL["resolve_thread"](1, "a" * 40, "other", "Reviewed and fixed")


def test_malformed_verifier_result_completes_check_as_failure(tmp_path, monkeypatch):
    module = CONTROL["verify"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    calls = []

    def api(path, method="GET", data=None, role="owner"):
        if path == "pulls/1":
            return {"head": {"sha": "a" * 40}, "base": {"ref": "main"}, "state": "open"}
        if path == "branches/main":
            return {"commit": {"sha": "b" * 40}}
        calls.append(data)
        return {"id": 42}

    def malformed(*args):
        raise TypeError("Malformed verifier payload")

    monkeypatch.setitem(module, "api", api)
    monkeypatch.setitem(module, "review", malformed)
    CONTROL["verify"](
        1, "a" * 40, {"kind": "maintenance", "rationale": "Repair", "acceptance": "Test"}
    )
    assert calls[-1]["status"] == "completed"
    assert calls[-1]["conclusion"] == "failure"
