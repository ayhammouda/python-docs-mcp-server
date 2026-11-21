"""Publishing must reject off-main, unverified, spoofed and failing revisions."""

import itertools
import runpy
from pathlib import Path

import pytest

GATE = runpy.run_path(str(Path(__file__).parents[1] / ".github/scripts/release_gate.py"))
SHA, HEAD, OWNER = "a" * 40, "b" * 40, "ayhammouda"
RECORD = (
    "Independent verification: PASS\n"
    f"head: {HEAD}\n"
    "Fable reviewer: PASS. Codex peer review: PASS. ponytail-review: PASS.\n"
    "Gate: green."
)
FAILED = RECORD.replace(": PASS\n", ": FAIL\n", 1)
CREATED, LATER, MERGED = "2026-10-30T10:00:00Z", "2026-10-30T11:00:00Z", "2026-10-30T12:00:00Z"
IDS = itertools.count(1)


def note(body, login=OWNER, created_at=CREATED, updated_at=None):
    return {
        "id": next(IDS),
        "user": {"login": login},
        "body": body,
        "created_at": created_at,
        "updated_at": updated_at or created_at,
    }


def make_state(*pages):
    checks = [
        {"id": n, "name": name, "app": {"id": 15368}, "conclusion": "success"}
        for n, name in enumerate(GATE["REQUIRED"])
    ]
    codeql = {"id": 100, "name": "CodeQL", "app": {"id": 57789}, "conclusion": "success"}
    return {
        f"git/commits/{SHA}": {"parents": [{"sha": "c" * 40}]},
        f"compare/{SHA}...main": {"status": "ahead"},
        f"commits/{SHA}/pulls?per_page=100": [
            {
                "number": 7,
                "merged_at": MERGED,
                "base": {"ref": "main"},
                "merge_commit_sha": SHA,
                "head": {"sha": HEAD},
            }
        ],
        f"commits/{HEAD}/check-runs?per_page=100": {"check_runs": [codeql]},
        f"commits/{SHA}/check-runs?per_page=100": {"check_runs": checks},
        **{
            f"issues/7/comments?per_page=100&page={n}": page
            for n, page in enumerate([*pages, []], start=1)
        },
    }


def test_release_does_not_require_retired_benchmark_status():
    assert "Product regression" not in GATE["REQUIRED"]


def test_release_eligibility_rejects_incomplete_or_wrong_identity_evidence():
    state = make_state([note(RECORD)])
    checks = state[f"commits/{SHA}/check-runs?per_page=100"]["check_runs"]
    codeql = state[f"commits/{HEAD}/check-runs?per_page=100"]["check_runs"][0]
    gate = GATE["require_eligible"]
    gate(state.__getitem__, SHA, OWNER)
    with pytest.raises(ValueError, match="repository owner"):
        gate(state.__getitem__, SHA, "")
    with pytest.raises(ValueError, match="verification record"):
        gate(state.__getitem__, SHA, "someone-else")
    state[f"compare/{SHA}...main"]["status"] = "diverged"
    with pytest.raises(ValueError, match="not on main"):
        gate(state.__getitem__, SHA, OWNER)
    state[f"compare/{SHA}...main"]["status"] = "ahead"
    state[f"git/commits/{SHA}"]["parents"].append({"sha": "d" * 40})
    with pytest.raises(ValueError, match="single-parent commit"):
        gate(state.__getitem__, SHA, OWNER)
    state[f"git/commits/{SHA}"]["parents"].pop()
    codeql["conclusion"] = "failure"
    with pytest.raises(ValueError, match="CodeQL findings"):
        gate(state.__getitem__, SHA, OWNER)
    codeql["conclusion"] = "success"
    checks.pop()
    with pytest.raises(ValueError, match="main checks"):
        gate(state.__getitem__, SHA, OWNER)


@pytest.mark.parametrize(
    "comment",
    [
        pytest.param(note(RECORD, login="someone-else"), id="not-owner"),
        pytest.param(note("\n" + RECORD), id="first-line-blank"),
        pytest.param(note(RECORD.replace("PASS\n", "PASS \n", 1)), id="first-line-trailing-space"),
        pytest.param(
            note(RECORD.replace("PASS\n", "PASS (partial)\n", 1)), id="first-line-suffix"
        ),
        pytest.param(note(RECORD.replace(": PASS", ": FAIL", 1)), id="fail-verdict"),
        pytest.param(note(RECORD.replace(HEAD, "d" * 40)), id="other-head"),
        pytest.param(note(RECORD.replace("head: ", "verified head: ")), id="head-not-own-line"),
        pytest.param(note(RECORD.replace(f"head: {HEAD}\n", "")), id="head-missing"),
        pytest.param(note(RECORD, updated_at=LATER), id="edited-pass-alone"),
        pytest.param(note(RECORD, created_at="2026-10-30T13:00:00Z"), id="after-merge"),
        pytest.param({**note(RECORD), "user": None}, id="deleted-author"),
    ],
)
def test_release_eligibility_requires_exact_owner_verification_record(comment):
    state = make_state([comment])
    with pytest.raises(ValueError, match="verification record"):
        GATE["require_eligible"](state.__getitem__, SHA, OWNER)


def test_release_eligibility_ignores_retired_verifier_check_without_record():
    state = make_state()
    retired = {
        "id": 99,
        "name": "Independent verification",
        "app": {"id": 123},
        "conclusion": "success",
        "external_id": "c" * 40 + ":" + HEAD,
    }
    state[f"commits/{HEAD}/check-runs?per_page=100"]["check_runs"].append(retired)
    with pytest.raises(ValueError, match="verification record"):
        GATE["require_eligible"](state.__getitem__, SHA, OWNER)


def test_release_eligibility_accepts_record_among_other_comments_with_crlf():
    deleted_author = note(RECORD)
    deleted_author["user"] = None
    state = make_state(
        [
            deleted_author,
            note(RECORD, login="someone-else"),
            note("Looks good."),
            note(RECORD.replace(f"head: {HEAD}", "head: " + "d" * 40)),
            note(RECORD.replace("\n", "\r\n")),
        ]
    )
    GATE["require_eligible"](state.__getitem__, SHA, OWNER)


def test_release_eligibility_finds_record_after_the_first_page_of_comments():
    state = make_state([note("Looks good.")] * 100, [note(RECORD, created_at=MERGED)])
    GATE["require_eligible"](state.__getitem__, SHA, OWNER)


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param([[note(RECORD), note(FAILED, created_at=LATER)]], id="newer-fail"),
        pytest.param([[note(RECORD)], [note(FAILED, created_at=LATER)]], id="fail-on-page-2"),
        pytest.param([[note(RECORD), note(FAILED)]], id="same-time-higher-id-fail"),
        pytest.param(
            [[note(RECORD), note(FAILED, created_at=LATER, updated_at=MERGED)]],
            id="edited-fail-after-pass",
        ),
    ],
)
def test_release_eligibility_rejects_when_the_newest_record_is_fail(pages):
    state = make_state(*pages)
    with pytest.raises(ValueError, match="verification record"):
        GATE["require_eligible"](state.__getitem__, SHA, OWNER)


def test_release_eligibility_requires_exact_sha_and_unique_merged_pr():
    state = make_state([note(RECORD)])
    gate = GATE["require_eligible"]
    for bad in ["a" * 39, "A" * 40, f"{SHA}\n", "refs/tags/v1.0.0"]:
        with pytest.raises(ValueError, match="Exact commit"):
            gate(state.__getitem__, bad, OWNER)
    state[f"commits/{SHA}/pulls?per_page=100"] = []
    with pytest.raises(ValueError, match="uniquely"):
        gate(state.__getitem__, SHA, OWNER)


def test_release_eligibility_ignores_an_edited_record_for_another_head():
    other = FAILED.replace(HEAD, "d" * 40)
    state = make_state([note(RECORD), note(other, created_at=LATER, updated_at=MERGED)])
    GATE["require_eligible"](state.__getitem__, SHA, OWNER)


def test_release_eligibility_accepts_when_a_newer_pass_follows_a_fail():
    state = make_state([note(FAILED), note(RECORD, created_at=LATER)])
    GATE["require_eligible"](state.__getitem__, SHA, OWNER)


def recovery_state():
    import json

    state = make_state([note(RECORD)])
    original = json.loads(json.dumps(make_state([note(RECORD)]))
                          .replace(SHA, GATE["RECOVERY_SHA"])
                          .replace(HEAD, "e" * 40)
                          .replace("issues/7/", "issues/8/"))
    original[f"commits/{GATE['RECOVERY_SHA']}/pulls?per_page=100"][0]["number"] = 8
    original[f"commits/{GATE['RECOVERY_SHA']}/check-runs?per_page=100"]["check_runs"].append(
        {"id": 100, "name": "Product regression", "app": {"id": 15368}, "conclusion": "success"}
    )
    state.update(original)
    state["git/ref/tags/v0.5.0-recovery.1"] = {"object": {"type": "commit", "sha": SHA}}
    state["git/ref/tags/v0.5.0"] = {"object": {"type": "tag", "sha": "f" * 40}}
    state[f"git/tags/{'f' * 40}"] = {
        "object": {"type": "commit", "sha": GATE["RECOVERY_SHA"]}
    }
    env = {
        "GITHUB_SHA": SHA, "GITHUB_REF_NAME": "v0.5.0-recovery.1",
        "GITHUB_REPOSITORY_OWNER": OWNER, "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/tags/v0.5.0-recovery.1", "GITHUB_REF_PROTECTED": "true",
    }
    return state, env


def test_only_exact_recovery_event_selects_original_release():
    state, env = recovery_state()
    target = GATE["release_target"]
    assert target(state.__getitem__, env) == (GATE["RECOVERY_SHA"], "v0.5.0")
    for tag in ("v0.5.1", "v0.5.0-recovery.2", "v0.5.0-recovery.1-extra"):
        assert target(state.__getitem__, {**env, "GITHUB_REF_NAME": tag}) == (SHA, tag)


@pytest.mark.parametrize(("key", "value"), [
    ("GITHUB_EVENT_NAME", "workflow_dispatch"),
    ("GITHUB_REF", "refs/heads/v0.5.0-recovery.1"),
    ("GITHUB_REF_PROTECTED", "false"),
])
def test_recovery_requires_protected_tag_push(key, value):
    state, env = recovery_state()
    with pytest.raises(ValueError, match="protected tag push"):
        GATE["release_target"](state.__getitem__, {**env, key: value})


@pytest.mark.parametrize("tag", ["v0.5.0", "v0.5.0-recovery.1"])
def test_recovery_rejects_moved_tags(tag):
    state, env = recovery_state()
    state[f"git/ref/tags/{tag}"] = {"object": {"type": "commit", "sha": "d" * 40}}
    with pytest.raises(ValueError, match="tag"):
        GATE["release_target"](state.__getitem__, env)


@pytest.mark.parametrize("original", [False, True])
@pytest.mark.parametrize("failure", ["main", "checks", "record", "codeql"])
def test_recovery_rechecks_both_commits(original, failure):
    state, env = recovery_state()
    sha, head, pr = (GATE["RECOVERY_SHA"], "e" * 40, 8) if original else (SHA, HEAD, 7)
    if failure == "main":
        state[f"compare/{sha}...main"]["status"] = "diverged"
    elif failure == "checks":
        # For the original this also exercises its frozen Product regression gate.
        state[f"commits/{sha}/check-runs?per_page=100"]["check_runs"].pop()
    elif failure == "record":
        state[f"issues/{pr}/comments?per_page=100&page=1"][0]["body"] = FAILED.replace(HEAD, head)
    else:
        state[f"commits/{head}/check-runs?per_page=100"]["check_runs"][0]["conclusion"] = "failure"
    with pytest.raises(ValueError):
        GATE["release_target"](state.__getitem__, env)


@pytest.mark.parametrize("kind", ["blob", "tree", "tag"])
def test_tag_peeling_rejects_non_commit_or_cyclic_objects(kind):
    state = {"git/ref/tags/v0.5.0": {"object": {"type": kind, "sha": SHA}}}
    state[f"git/tags/{SHA}"] = state["git/ref/tags/v0.5.0"]
    with pytest.raises(ValueError, match="exact commit"):
        GATE["tag_commit"](state.__getitem__, "v0.5.0")
