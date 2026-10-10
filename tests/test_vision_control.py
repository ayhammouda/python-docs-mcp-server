"""Negative cases for the fixed-repository privileged broker boundary."""

import base64
import hashlib
import json
import runpy
import subprocess
from pathlib import Path

import pytest

CONTROL = runpy.run_path(str(Path(__file__).parents[1] / "ops/vision/control.py"))


@pytest.mark.parametrize("failure_kind", ["exit", "timeout", "rejected", "malformed"])
def test_review_deadline_and_failure_handoff(tmp_path, monkeypatch, failure_kind):
    from types import SimpleNamespace

    module = CONTROL["review"].__globals__
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    monkeypatch.setitem(module, "STATE", tmp_path)
    monkeypatch.setitem(module, "api", lambda *_: {"tree": {"sha": "c" * 40}})
    cleanups = []
    monkeypatch.setitem(module, "reset_verifier", lambda: cleanups.append(True))
    calls = []

    def fail(args, **kwargs):
        calls.append((args, kwargs))
        if failure_kind == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"], stderr="private-output")
        if failure_kind == "exit":
            raise subprocess.CalledProcessError(1, args, stderr="private-output")
        verdict = {
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "approved": False,
            "blockers": ["token=private-output"],
            "commands": [],
        }
        text = json.dumps(verdict) if failure_kind == "rejected" else "private-output"
        return SimpleNamespace(stdout=json.dumps({"payloads": [{"text": text}]}))

    monkeypatch.setattr(module["subprocess"], "run", fail)
    with pytest.raises(ValueError) as error:
        CONTROL["review"]("a" * 40, "b" * 40, {})
    args, kwargs = calls[0]
    assert args[args.index("--timeout") + 1] == "1500"
    assert kwargs["timeout"] == 1560
    assert len(cleanups) == 2
    status = CONTROL["dispatch"]({"operation": "status"})
    failure = status["review_failures"][0]
    assert failure["failures"] == (1 if failure_kind == "rejected" else 0)
    assert failure["failure_kind"] == (
        "rejection" if failure_kind == "rejected" else "infrastructure"
    )
    assert failure["head_sha"] == "a" * 40
    assert failure["base_sha"] == "b" * 40
    assert failure["session_id"] == args[args.index("--session-id") + 1]
    assert "Independent review" in failure["last_error"]
    assert "private-output" not in json.dumps(status)
    if failure_kind in {"exit", "timeout"}:
        assert "private-output" not in str(error)
    verdict = {
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "approved": True,
        "blockers": [],
        "commands": [
            {"command": command, "exit_code": 0}
            for command in ["uv sync --locked --dev", "ruff check", "pyright", "pytest"]
        ],
    }
    monkeypatch.setattr(
        module["subprocess"],
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps({"payloads": [{"text": json.dumps(verdict)}]})
        ),
    )
    monkeypatch.setattr(module["time"], "time", lambda: 9999999999)
    assert CONTROL["review"]("a" * 40, "b" * 40, {}) == verdict
    assert CONTROL["dispatch"]({"operation": "status"})["review_failures"] == []
    assert json.loads(next(tmp_path.glob("attempts-*.json")).read_text())["failures"] == (
        1 if failure_kind == "rejected" else 0
    )


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


def test_host_mcp_scoping_preserves_other_agents_and_existing_restrictions():
    scope = runpy.run_path(str(Path(__file__).parents[1] / "ops/vision/install.py"))[
        "scope_host_mcp"
    ]
    config = {
        "agents": {"entries": {"main": {}, "piquetbot": {}, "pd-owner": {}}},
        "mcp": {
            "servers": {
                "unscoped": {"url": "https://example.test/mcp"},
                "restricted": {"codex": {"agents": ["main", "future", "PD-Verifier"]}},
                "disabled": {"codex": {"agents": []}},
            }
        },
    }
    scope(config)
    servers = config["mcp"]["servers"]
    assert servers["unscoped"] == {
        "url": "https://example.test/mcp",
        "codex": {"agents": ["main", "piquetbot"]},
    }
    assert servers["restricted"]["codex"]["agents"] == ["main", "future"]
    assert servers["disabled"]["codex"]["agents"] == []
    before = json.dumps(config)
    scope(config)
    assert json.dumps(config) == before


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
    monkeypatch.setitem(module, "CONFIG", tmp_path)
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


def test_temporary_auth_uses_only_the_broker_and_keeps_releases_disabled(tmp_path, monkeypatch):
    module = CONTROL["dispatch"].__globals__
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    monkeypatch.setitem(module, "STATE", tmp_path)
    (tmp_path / "temporary-token").write_text("test-credential")
    (tmp_path / "activated").write_text("1")
    assert CONTROL["token"].__wrapped__("owner") == "test-credential"
    with pytest.raises(ValueError, match="Unknown identity"):
        CONTROL["token"].__wrapped__("arbitrary")
    status = CONTROL["dispatch"]({"operation": "status"})
    assert status["ready"] is True
    assert status["authentication"] == "temporary-token"
    assert status["releases_enabled"] is False
    assert "test-credential" not in json.dumps(status)
    with pytest.raises(ValueError, match="Releases require GitHub App"):
        CONTROL["release"]("a" * 40, "v1.0.0")
    (tmp_path / "activated").unlink()
    with pytest.raises(ValueError, match="not activated"):
        CONTROL["dispatch"]({"operation": "merge", "pr": 1, "head_sha": "a" * 40})


def test_temporary_verification_requires_current_private_receipt(tmp_path, monkeypatch):
    module = CONTROL["verify"].__globals__
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    monkeypatch.setitem(module, "STATE", tmp_path)
    (tmp_path / "temporary-token").write_text("test-credential")
    head, base = "a" * 40, "b" * 40
    pr = {"number": 1, "head": {"sha": head}, "base": {"ref": "main"}, "state": "open"}
    verdict = {"approved": True, "head_sha": head, "base_sha": base, "blockers": []}
    calls = []

    def api(path, method="GET", data=None, role="owner"):
        calls.append((path, method))
        if path == "pulls/1":
            return pr
        if path == "branches/main":
            return {"commit": {"sha": base}}
        assert path == "issues/1/comments" and method == "POST"
        return {"html_url": "https://github.com/example/evidence"}

    monkeypatch.setitem(module, "api", api)
    monkeypatch.setitem(module, "review", lambda *args: verdict)
    with pytest.raises(ValueError, match="Missing/stale"):
        CONTROL["require_verified"](pr, head, base)
    result = CONTROL["verify"](
        1, head, {"kind": "maintenance", "rationale": "Repair", "acceptance": "Tests"}
    )
    assert result["status"] == "success"
    assert CONTROL["require_verified"](pr, head, base)["verdict"] == verdict
    with pytest.raises(ValueError, match="Missing/stale"):
        CONTROL["require_verified"](pr, head, "c" * 40)
    with pytest.raises(ValueError, match="Missing/stale"):
        CONTROL["require_verified"]({**pr, "head": {"sha": "c" * 40}}, head, base)
    path = tmp_path / f"verify-1-{base}-{head}.json"
    receipt = json.loads(path.read_text())
    assert receipt["policy"] == hashlib.sha256(Path(module["__file__"]).read_bytes()).hexdigest()
    for invalid in [{**receipt, "policy": "outdated"}, {**receipt, "status": "failure"}]:
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError, match="Missing/stale"):
            CONTROL["require_verified"](pr, head, base)
    assert all(not p.startswith("check-runs") for p, _ in calls)


def test_temporary_capture_and_app_activation_lifecycle(tmp_path, monkeypatch, capsys):
    import copy
    from types import SimpleNamespace

    loader = runpy.run_path
    monkeypatch.setattr(runpy, "run_path", lambda _: CONTROL)
    setup = loader(str(Path(__file__).parents[1] / "ops/vision/configure_apps.py"))
    module = setup["activate"].__globals__
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    credential = "private-test-credential"
    rules = {
        "name": "main",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {},
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "Existing CI", "integration_id": 15368}]
                },
            }
        ],
    }

    def run(command, **kwargs):
        assert command[-4:] == ["auth", "token", "--hostname", "github.com"]
        assert kwargs["capture_output"] is True
        return SimpleNamespace(stdout=credential)

    def request(url, token, method="GET", data=None):
        if url.endswith("/user"):
            assert token == credential
            return {"login": "ayhammouda"}
        if url.endswith("/repos/" + CONTROL["REPO"]):
            return {"permissions": {"push": True}}
        if url.endswith("/app"):
            return {"owner": {"login": "ayhammouda"}, "permissions": setup["PERMISSIONS"][token]}
        if url.endswith("/app/installations"):
            return [
                {"id": 1, "account": {"login": "ayhammouda"}, "repository_selection": "selected"}
            ]
        if url.endswith("/access_tokens"):
            return {"token": "scoped-installation-test"}
        assert url.endswith("/installation/repositories")
        return {"total_count": 1, "repositories": [{"full_name": CONTROL["REPO"]}]}

    def operator_api(path, method="GET", body=None):
        if path == "rulesets/15269598":
            if method == "PUT":
                rules.update(copy.deepcopy(body))
            return copy.deepcopy(rules)
        if path == "actions/variables" and method == "GET":
            return {"variables": []}
        if path == "rulesets?per_page=100":
            return []
        if path == "environments/pypi/deployment-branch-policies" and method == "GET":
            return {"branch_policies": []}
        return {}

    monkeypatch.setattr(module["subprocess"], "run", run)
    monkeypatch.setitem(module, "CONTROL", {**CONTROL, "request": request, "app_jwt": lambda r: r})
    monkeypatch.setitem(module, "operator_api", operator_api)
    setup["enable_temporary"]()
    token_path = tmp_path / "temporary-token"
    assert token_path.read_text() == credential
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "activated").exists()
    assert credential not in capsys.readouterr().out
    with pytest.raises(FileNotFoundError):
        setup["activate"]()
    assert not (tmp_path / "activated").exists()
    assert token_path.exists()  # Retained privately, but no automatic reactivation.
    for role, app_id in [("owner", 41), ("verifier", 42)]:
        (tmp_path / f"{role}.json").write_text(json.dumps({"app_id": app_id}))
    with pytest.raises(ValueError, match="instead of downgrading"):
        setup["enable_temporary"]()
    setup["activate"]()
    assert not token_path.exists()
    assert (tmp_path / "activated").exists()
    required = rules["rules"][0]["parameters"]["required_status_checks"]
    assert {"context": "Existing CI", "integration_id": 15368} in required
    assert {"context": "Independent verification", "integration_id": 42} in required


def test_rerun_is_bounded_audited_and_fixed_to_current_main(tmp_path, monkeypatch):
    module = CONTROL["rerun"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    run = {"id": 7, "head_sha": "a" * 40, "head_branch": "main", "event": "push",
           "repository": {"full_name": CONTROL["REPO"]},
           "head_repository": {"full_name": CONTROL["REPO"]}, "pull_requests": [],
           "path": ".github/workflows/ci.yml", "status": "completed", "conclusion": "failure"}
    posts = []

    def api(path, method="GET", data=None):
        if method == "POST":
            posts.append(path)
            return {}
        return run if path.startswith("actions/") else {"commit": {"sha": "a" * 40}}

    monkeypatch.setitem(module, "api", api)
    for key, bad in [("head_sha", "b" * 40), ("event", "pull_request"),
                     ("path", ".github/workflows/release.yml"), ("conclusion", "success"),
                     ("head_repository", {"full_name": "other/repo"})]:
        original = run[key]
        run[key] = bad
        with pytest.raises(ValueError):
            CONTROL["rerun"](7)
        run[key] = original
    assert posts == []
    for number in [True, -1, "7"]:
        with pytest.raises(ValueError):
            CONTROL["rerun"](number)
    assert CONTROL["rerun"](7)["status"] == "accepted"
    with pytest.raises(ValueError, match="cooldown"):
        CONTROL["rerun"](7)
    for now in [9999999999, 9999999999 + 1000]:
        monkeypatch.setattr(module["time"], "time", lambda now=now: now)
        CONTROL["rerun"](7)
    monkeypatch.setattr(module["time"], "time", lambda: 9999999999 + 2000)
    with pytest.raises(ValueError, match="limit"):
        CONTROL["rerun"](7)
    assert posts == ["actions/runs/7/rerun-failed-jobs"] * 3


@pytest.mark.parametrize("kind,limit", [("rejection", 2), ("infrastructure", 3)])
def test_review_retry_circuit_distinguishes_failures(tmp_path, monkeypatch, kind, limit):
    module = CONTROL["review"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    monkeypatch.setitem(module, "api", lambda *_: {"tree": {"sha": "c" * 40}})
    monkeypatch.setitem(module, "reset_verifier", lambda: None)
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise CONTROL["ReviewFailure"](kind, {"reason": "test"})

    monkeypatch.setattr(module["subprocess"], "run", fail)
    for attempt in range(limit + 1):
        monkeypatch.setattr(module["time"], "time", lambda attempt=attempt: attempt * 1000)
        with pytest.raises(ValueError):
            CONTROL["review"]("a" * 40, "b" * 40, {})
    assert len(calls) == limit


def test_status_omits_raw_verdict_and_owner_rationale(tmp_path, monkeypatch):
    module = CONTROL["dispatch"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    (tmp_path / "verify-test.json").write_text(json.dumps({
        "status": "failure", "decision": {"secret": "private"},
        "verdict": {"summary": "private"}, "head_sha": "a" * 40,
    }))
    status = CONTROL["dispatch"]({"operation": "status"})
    assert "private" not in json.dumps(status)
    assert status["blocked_verifications"][0]["head_sha"] == "a" * 40


def test_rerun_refuses_main_race_without_post(tmp_path, monkeypatch):
    module = CONTROL["rerun"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    reads = []

    def api(path, method="GET", data=None):
        assert method == "GET"
        if path == "branches/main":
            reads.append(True)
            return {"commit": {"sha": ("a" if len(reads) == 1 else "b") * 40}}
        return {"id": 7, "head_sha": "a" * 40, "head_branch": "main", "event": "push",
                "repository": {"full_name": CONTROL["REPO"]},
                "head_repository": {"full_name": CONTROL["REPO"]}, "pull_requests": [],
                "path": ".github/workflows/ci.yml", "status": "completed",
                "conclusion": "cancelled"}

    monkeypatch.setitem(module, "api", api)
    with pytest.raises(ValueError, match="Main changed"):
        CONTROL["rerun"](7)
    assert not list(tmp_path.glob("rerun-*"))


def test_owner_token_requests_only_scoped_actions_write(tmp_path, monkeypatch):
    module = CONTROL["token"].__wrapped__.__globals__
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    (tmp_path / "owner.json").write_text(json.dumps({"installation_id": 1}))
    monkeypatch.setitem(module, "app_jwt", lambda role: "test")
    requests = []
    monkeypatch.setitem(module, "request", lambda *args: requests.append(args) or {"token": "test"})
    CONTROL["token"].__wrapped__("owner")
    body = requests[0][3]
    assert body["repositories"] == ["python-docs-mcp-server"]
    assert body["permissions"]["actions"] == "write"
    assert "administration" not in body["permissions"]


def test_diagnostics_redact_secrets_preserve_useful_failures_and_bound_legacy_status(
    tmp_path, monkeypatch
):
    module = CONTROL["dispatch"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    dangerous = ("doctor index missing; Bearer supersecret token=private-output "
                 "ghp_privatevalue -----BEGIN PRIVATE KEY-----hiddenmaterial"
                 "-----END PRIVATE KEY-----\x00")
    diagnostics = {"summary": dangerous, "blockers": [dangerous] * 20,
                   "failed_commands": [{"command": "doctor --token=private-output",
                                        "exit_code": 2}] * 20,
                   "arbitrary_secret": "must not escape"}
    for number in range(55):
        (tmp_path / f"verify-{number}.json").write_text(json.dumps({
            "status": "failure", "diagnostics": diagnostics,
        }))
    results = CONTROL["dispatch"]({"operation": "status"})["blocked_verifications"]
    assert len(results) == 50
    text = json.dumps(results)
    for secret in ["supersecret", "private-output", "ghp_privatevalue", "hiddenmaterial",
                   "must not escape"]:
        assert secret not in text
    assert "doctor index missing" in text
    result = results[0]["diagnostics"]
    assert len(result["blockers"]) == len(result["failed_commands"]) == 8
    assert result["failed_commands"][0]["exit_code"] == 2
    assert len(CONTROL["diagnostic_text"]("x " * 10000)) <= 400


@pytest.mark.parametrize("temporary", [True, False])
@pytest.mark.parametrize("approved", [True, False])
def test_public_verification_never_publishes_freeform_findings(
    tmp_path, monkeypatch, temporary, approved
):
    module = CONTROL["verify"].__globals__
    monkeypatch.setitem(module, "STATE", tmp_path)
    monkeypatch.setitem(module, "CONFIG", tmp_path)
    if temporary:
        (tmp_path / "temporary-token").write_text("test")
    sentinel = "innocent looking freeform secret sentinel"
    head, base = "a" * 40, "b" * 40
    posts = []

    def api(path, method="GET", data=None, role="owner"):
        if path == "pulls/1":
            return {"head": {"sha": head}, "base": {"ref": "main"}, "state": "open"}
        if path == "branches/main":
            return {"commit": {"sha": base}}
        posts.append(data)
        return {"id": 42, "html_url": "https://example.test/evidence"}

    def review(*args):
        if not approved:
            raise CONTROL["ReviewFailure"]("rejection", {
                "summary": sentinel, "blockers": [sentinel], "reason": sentinel,
                "reasons": [sentinel, "failed_commands"], "failed_checks": [sentinel, "pytest"],
                "failed_commands": [{"command": sentinel, "exit_code": 1}], "blocker_count": 1,
            })
        return {"approved": True, "summary": sentinel, "head_sha": head, "base_sha": base,
                "blockers": [], "commands": [{"command": "pytest " + sentinel, "exit_code": 0}]}

    monkeypatch.setitem(module, "api", api)
    monkeypatch.setitem(module, "review", review)
    CONTROL["verify"](1, head, {"kind": "maintenance", "rationale": "Repair", "acceptance": "Test"})
    assert sentinel not in json.dumps(posts)
    assert sentinel in (tmp_path / f"verify-1-{base}-{head}.json").read_text()
    assert "pytest" in json.dumps(posts)
    assert CONTROL["diagnostic_text"]("password is abc123") == "credential=[REDACTED]"
