# Vision Installer And Setup Safety Implementation Plan (PR 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** While it runs as root, the Vision installer never follows a link
planted in the host user's directories. Root still reads the reviewed
checkout, which is the trust root. App activation finds its variable by
name, the broker and App setup share one permission map, and `pdctl rerun`
offers only workflows that run on push.

**Architecture:** One PR on branch `fix/vision-installer-setup`, rebased over
PR 6 because both edit `ops/vision/control.py`. Task 1 changes
`ops/vision/install.py`:
- File work in the host user's directories runs as that user through
  `runuser`. This is the mitigation CWE-367 names.
- Root keeps `directory()` and `owned()` for root-owned trees.
- Root still reads two things from host-writable paths, the private key's
  metadata and the `uv` binary, and writes one, the `openclaw.json` backup.
  All three go through `O_NOFOLLOW` descriptors. The writes also use
  `O_EXCL`.

Tasks 2 to 4 are small fixes in `configure_apps.py` and `control.py`. Each
finding has its own task and commit.

**Tech Stack:** Python 3.12+ standard library (`os.open` with `O_NOFOLLOW`,
`os.fstat`, `stat`, `base64`, `subprocess`), `runuser`, POSIX `sh`, `gh api`,
PyYAML (already a runtime dependency) in one test, pytest with `monkeypatch`
and `runpy` (the existing pattern).

**Spec:** `docs/research/2026-10-30-audit-fix-best-practices.md`, section
"Ops, security and CI": item 4 (Finding 14, plus N1 and N2 from the "New
issues" table), and item 7(c), 7(d) and 7(e). Binding decisions:
`docs/plans/2026-10-30-audit-fixes-roadmap.md` D11 and PR map row 7.

## Global Constraints

- **Precondition:** `docs/plans/2026-10-30-*.md` and `docs/research/2026-10-30-audit-fix-best-practices.md` are on `main` (roadmap Wave 0.5); stop if not.
- **Base:** `main` after #162 and PR 6 merge. Rebase over PR 6 before you
  start. Never merge `feat/mcp-sdk-v2` into another branch.
- **Line numbers** are cited at `c03e497`. PR 6 does not change
  `install.py`, `configure_apps.py` or the workflow files, but it does shift
  `control.py`. Task 1 here also shifts the lines of `main()` in
  `install.py`. Find every edit by the quoted code or the function name.
- **TDD:** superpowers:test-driven-development. Watch each new test fail
  before the fix. One finding is one commit, with its test and its fix
  together. Refactors stay separate from behaviour changes (Tasks 3 and 4).
- **Gate (`AGENTS.md`), run fresh before the PR:**
  - `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/`
  - `uv run --locked pyright src/ benchmarks/`
  - `uv run --locked pytest --tb=short -q`
- **Existing tests:** never delete one or weaken an assertion. No existing
  test changes in this plan. `test_installer_rejects_worker_symlinks_before_ownership_changes`
  must stay green and unchanged, so `directory()` and `owned()` keep their
  behaviour.
- **Supervisor review is mandatory.** No forbidden-territory path is touched;
  Task 4 only reads `.github/workflows/`. Even so, add the
  `supervisor-review` label and a `## Why this triggered supervisor review`
  section. The section cites: "The owner (Aymen Hammouda) asked on
  2026-10-30 to implement all audit findings; decisions in
  `docs/plans/2026-10-30-audit-fixes-roadmap.md`." It covers three root
  operations that stay in host-writable places:
  - the `openclaw.json` backup, still a root write, now through an
    `O_EXCL | O_NOFOLLOW` descriptor;
  - the root `fstat` check of the private key;
  - the `uv` copy, which now refuses a symlinked `uv`.
  The prototype diff is about 480 lines. Task 5 measures it again and lists
  the size as a second §7 trigger if it is above 500.
- **Policy hash:** every `control.py` edit changes the broker's policy hash.
  That invalidates cached reviews and temporary receipts, by design. PR 6
  changes it too, so the PR body advises deploying both in one reinstall.
- **Dependencies and network:** no new runtime or dev dependency, no new
  runtime network access. The installer gains no download.
- **Tool surface:** unchanged. Nothing under `src/` changes.
- **CHANGELOG:** entries under `## [Unreleased]`, grouped as Fixed and
  Security.
- **Commits and PRs:** conventional prefixes. Every commit ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The PR body
  follows `.github/PULL_REQUEST_TEMPLATE/agent.md` and ends with
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Never
  self-merge.

## Checked on 2026-10-30 (scratch copy of `c03e497` with PR 6 applied)

- With Tasks 1 to 4 applied, the three vision test files pass (123 tests).
- Opening an existing name with `O_CREAT | O_EXCL | O_NOFOLLOW` fails with
  `OSError`, even when the name is a symlink to a writable file. That closes
  the backup race in which a planted link is swapped in after `mkdir`.
- `gh api` exits 1 for a missing resource and prints
  `gh: Not Found (HTTP 404)` on stderr. A permission error prints
  `... (HTTP 403)`. The JSON body goes to stdout.
- macOS `chmod` reads a `--` placed after the mode as a file name and fails.
  The shell snippet in Task 1 therefore passes no `--`. Its paths are
  absolute, so none starts with `-`.
- Opening a symlink with `O_NOFOLLOW` raises `OSError` on Linux and on macOS.
  The errno differs, so the tests assert only `OSError`.
- With `O_NONBLOCK`, opening a FIFO for reading returns at once. The
  `S_ISREG` check then refuses it, so the installer does not hang.
- PyYAML loads a workflow's `on:` key as `True`. Every allowlisted rerun
  workflow declares `push`, except `.github/workflows/e2e.yml`.

## Decisions This Plan Makes

- **Host-user files.** Root writes several files through `owned()` and then
  chowns them to the host user: the SSH known-hosts file, `openclaw.json`
  (write and rollback), the workspaces, `AGENTS.md` and `SKILL.md`. All of
  them move to the host user, and so does root's read of `openclaw.json`.
  The research's preferred fix names only the key, the `.pub` and the
  workspaces, but dropping root's chown and chmod on host-user files covers
  the rest.
- **The backup stays a root write, through a descriptor.** Who owns
  `/srv/openclaw/backups/` is unknown, so a host-user write could fail there.
  Its content (`old`) is host-controlled, and the
  `python-docs-guardrails-<ts>` name is predictable. A host process that can
  write a parent could therefore swap in a symlinked directory and make root
  overwrite a file such as `/root/.bashrc`. `backup_config()` closes that:
  - `mkdir` without `exist_ok`;
  - an `O_CREAT | O_EXCL | O_NOFOLLOW` open, and a write through that
    descriptor;
  - no `write_text` and no path-based `chmod`.
- **Private key check.** Root opens the private key once, read-only, with
  `O_NOFOLLOW`, and calls `fstat` on it. It reads no key bytes and makes no
  chown or chmod. The open makes the research's "symlinked key is refused"
  test possible. The `fstat` refuses a key that `ssh` would reject anyway:
  one that is not a regular file, is not owned by the host user, or can be
  read by group or others.
- **`.pub` check.** The host user reads the `.pub` with `head -c 4096`, which
  bounds the read. `public_key()` checks it once, right after
  `sandbox_key()` and before any `useradd`, `pkill` or `chown`. It requires
  exactly one line, `ssh-ed25519`, and a base64 blob that decodes to the
  51-byte ed25519 key. It returns
  `"ssh-ed25519 " + base64.b64encode(blob).decode()`. `authorized_keys`
  therefore gets the re-encoded bytes that passed the check, never the
  host's text, and the comment is dropped.
- **`uv` copy.** It copies through a descriptor. The research's alternative,
  a pinned and checksummed download, would add network access to the
  installer and a version and digest to keep current. The operator's own
  `uv` stays the trust root.
  - The copy requires `st_uid` to be the host user's, because `O_NOFOLLOW`
    guards only the last path component.
  - It writes an `O_EXCL` temporary file and then calls `os.replace`, so a
    failed copy never leaves a partial `/usr/local/bin/uv`.
  - A pipx-style symlinked `uv` is refused, and the error says what to do.
- **`runuser` output.** `as_host()` captures only stdout. Stderr from
  `runuser`, `ssh-keygen` or `cat` still reaches the operator's terminal.
- **Variable by name.** A `set_variable(name, value)` seam keeps the test
  small. The 404 is read from `gh`'s stderr.
- **Rerun allowlist.** The literal set moves to a module constant in its own
  refactor commit, so the policy test can read it.
- **Test file.** The new installer tests go in a new file,
  `tests/test_vision_install.py`. The two existing installer tests stay where
  they are.

## Review Focus

1. **Re-install over an existing setup.** Expected: an existing key is
   reused without `ssh-keygen`, and an existing `AGENTS.md` at 0644 is
   rewritten at 0600. Pinned by Task 1,
   `test_host_write_creates_private_parents_and_files` and
   `test_sandbox_key_reuses_an_existing_key`.
2. **The host's `uv` is a symlink**, as pipx and mise install it. Expected:
   refused with a message that says how to proceed, and nothing written to
   `/usr/local/bin/uv`. Pinned by Task 1,
   `test_copy_uv_refuses_anything_else[symlink]`.
3. **A `.pub` with a multi-word comment or a CRLF ending.** Expected:
   accepted, and the comment dropped. Pinned by Task 1,
   `test_authorize_writes_the_checked_key_on_one_restricted_line[comment_crlf]`.
4. **A `.pub` with a second key line or a leading `command="..."` option.**
   Expected: refused before any account change, and `authorized_keys` left
   unwritten. Pinned by Task 1,
   `test_public_key_refuses_anything_but_one_ed25519_key`.
5. **`gh` fails with something other than 404** (403, rate limit) while
   reading the variable. Expected: activation stops with no POST or PATCH.
   Pinned by Task 2,
   `test_activation_creates_the_variable_only_when_github_says_404[403]`.

## Not In This Plan

- A descriptor-walking helper (an `openat` chain with `fchown` and
  `fchmod`). Host-user work runs as the host user, so root needs no walk.
- `openat2`: CPython has no wrapper for it.
- A pinned `uv` download (see Decisions).
- Moving the `openclaw.json` backup to the host user. It stays a root write,
  now through a descriptor (see Decisions). Moving it would require knowing
  who owns `/srv/openclaw/backups/`.

---

### Task 1: Stop the installer from following planted links as root (Finding 14, N1, N2)

The roadmap counts this as one finding (14, with N1 and N2), so it gets one
commit. It runs four test cycles before that commit:
- Steps 1 to 5: host-user files;
- Steps 6 to 11: the sandbox key and the `.pub` (N1);
- Steps 12 to 16: `uv` (N2), plus a check that `main()` does no host-file
  work as root;
- Steps 17 to 20: the root-written backup.

**Files:**
- Modify: `ops/vision/install.py`:
  - imports (`:8-15`): add `base64` and `stat`;
  - after `owned()` (`:141-147`), add `as_host`, `host_write`,
    `sandbox_key`, `public_key`, `authorize`, `copy_uv` and `backup_config`;
  - in `main()`, change:
    - `:188-191` (`uv`) and `:192-197` (the key);
    - `:198-201` (known hosts and the `openclaw.json` read);
    - `:244-247` (`authorized_keys`);
    - `:248-264` (workspace, `AGENTS.md`, `SKILL.md`);
    - `:331-334` (backup), `:335` (`openclaw.json` write) and `:347`
      (rollback)
- Create: `tests/test_vision_install.py`

**Interfaces:**
- Consumes: `run`, `owned` and `HOST_USER` (existing).
- Produces:
  - `as_host(*args: str, input: str = "") -> str`. It runs
    `run("/usr/sbin/runuser", "-u", HOST_USER, "--", *args, input=input, stdout=subprocess.PIPE, text=True)`
    and returns its stdout. Stderr is not captured, so the operator sees it.
  - `host_write(path: Path, text: str) -> None`. It runs one
    `as_host("/bin/sh", ...)` call that creates the parents at 0700 and
    writes the file at 0600.
  - `sandbox_key(key: Path, uid: int) -> str`, which returns the `.pub` text
    as the host user reads it.
  - `public_key(text: str) -> str`, which returns
    `"ssh-ed25519 " + base64.b64encode(blob).decode()` or raises
    `ValueError("The sandbox public key must be one ssh-ed25519 key")`.
  - `authorize(ssh: Path, role: str, public: str) -> None`, which writes the
    checked `public` line to `ssh / "authorized_keys"` (root-owned, mode
    0644).
  - `copy_uv(source: Path, target: Path, uid: int) -> None`. On refusal it
    raises a `ValueError` whose message contains
    `copy uv to {target} yourself`.
  - `backup_config(backup: Path, text: str) -> None`, which raises `OSError`
    if `backup / "openclaw.json"` already exists in any form.
- Test module helpers: `INSTALLER`, `MODULE`,
  `as_test_user(*args, input="") -> str` and `mode(path) -> int`.

- [ ] **Step 1: Create `tests/test_vision_install.py`**

```python
"""The installer runs as root; a planted link in a host-user directory must not redirect it."""

import runpy
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

INSTALLER = runpy.run_path(str(Path(__file__).parents[1] / "ops/vision/install.py"))
MODULE = INSTALLER["main"].__globals__


def as_test_user(*args, input=""):
    """Stand-in for runuser: the unprivileged test user plays the host user."""
    return subprocess.run(args, input=input, capture_output=True, text=True, check=True).stdout


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_host_files_are_written_through_runuser(monkeypatch):
    calls = []
    monkeypatch.setattr(
        MODULE["subprocess"],
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(stdout=""),
    )
    INSTALLER["host_write"](Path("/srv/openclaw/.openclaw/workspace-pd-owner/AGENTS.md"), "rules")
    args, kwargs = calls[0]
    assert args[:4] == ("/usr/sbin/runuser", "-u", "ahammouda", "--")
    assert args[-1] == "/srv/openclaw/.openclaw/workspace-pd-owner/AGENTS.md"
    assert kwargs["input"] == "rules"
    assert "stderr" not in kwargs and "capture_output" not in kwargs  # the operator sees errors


def test_host_write_creates_private_parents_and_files(tmp_path, monkeypatch):
    monkeypatch.setitem(MODULE, "as_host", as_test_user)
    target = tmp_path / "workspace-pd-owner/skills/verification-before-completion/SKILL.md"
    INSTALLER["host_write"](target, "first\n")
    assert target.read_text() == "first\n"
    assert mode(target) == 0o600
    assert mode(tmp_path / "workspace-pd-owner") == mode(target.parent) == 0o700
    target.chmod(0o644)
    INSTALLER["host_write"](target, "second\n")  # a re-install rewrites an existing file
    assert target.read_text() == "second\n"
    assert mode(target) == 0o600
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_vision_install.py -q`
Expected: `2 failed` with `KeyError: 'host_write'`

- [ ] **Step 3: Add `as_host` and `host_write` after `owned()`.**
  `host_write` passes the path as `$1` and the text on stdin, so a planted
  link can redirect the write only to a file the host user may write anyway:

```python
def host_write(path, text):
    """Create parents (0700) and write a 0600 file, all as HOST_USER."""
    as_host(
        "/bin/sh", "-c", 'umask 077 && mkdir -p "${1%/*}" && cat > "$1" && chmod 600 "$1"',
        "host_write", str(path), input=text,
    )
```

- [ ] **Step 4: Route `main()`'s host-user files through them.**
  - `:200`: `owned(known, ...)` becomes
    `host_write(known, f"127.0.0.1 {host_key[0]} {host_key[1]}\n")`.
  - `:201`: `old = CONFIG.read_text()` becomes
    `old = as_host("/bin/cat", str(CONFIG))`.
  - `:248-264`: drop the `workspace.mkdir`, the `os.chown`, the
    `skills.mkdir` and both `owned` calls. Write
    `host_write(workspace / "AGENTS.md", COMMON + "\n" + ROLES[role])` and
    `host_write(workspace / "skills/verification-before-completion/SKILL.md", <same text as today>)`.
  - `:335` and `:347`: `owned(CONFIG, <text>, host.pw_uid, host.pw_gid)`
    becomes `host_write(CONFIG, <text>)`.
  - The backup stays a root write, through a new helper
    `backup_config(backup: Path, text: str) -> None`. It runs
    `backup.mkdir(mode=0o700, parents=True)` (no `exist_ok`), then
    `os.open(backup / "openclaw.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)`,
    and writes through that descriptor. There is no `write_text` and no
    path-based `chmod`. Leave the other root-owned writes (`/opt`, `/etc`,
    the worker homes) as they are. Steps 17 to 20 add the helper test-first,
    so leave the backup lines (`:331-334`) alone until then.

- [ ] **Step 5: Run the installer tests**

Run: `uv run --locked pytest tests/test_vision_install.py tests/test_vision_control.py -q -k "host_ or installer or scoping"`
Expected: all passed, including
`test_installer_rejects_worker_symlinks_before_ownership_changes`

- [ ] **Step 6: Add `import base64`, `import os` and `import pytest` to the
  test module's imports (ruff's isort order), then append the key tests**

```python
KEY_B64 = base64.b64encode(
    b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
).decode()
WORKER_LINE = 'restrict,command="/opt/python-docs-vision/worker_shell.py verifier" ssh-ed25519 '


def planted_key(tmp_path):
    """A 0644 file and a sandbox key path that is a symlink to it."""
    secret = tmp_path / "secret"
    secret.write_text("root-only material")
    secret.chmod(0o644)
    key = tmp_path / "python-docs-sandbox"
    key.symlink_to(secret)
    return key, secret


def test_sandbox_key_refuses_a_symlink_and_leaves_its_target_alone(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(MODULE, "as_host", lambda *args, **kwargs: calls.append(args) or "")
    key, secret = planted_key(tmp_path)
    with pytest.raises(OSError):
        INSTALLER["sandbox_key"](key, os.getuid())
    assert mode(secret) == 0o644
    assert calls == []


def test_sandbox_key_fails_closed_when_the_link_appears_after_the_check(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(MODULE, "as_host", lambda *args, **kwargs: calls.append(args) or "")
    key, secret = planted_key(tmp_path)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    with pytest.raises(OSError):
        INSTALLER["sandbox_key"](key, os.getuid())
    assert mode(secret) == 0o644
    assert calls[0][0] == "/usr/bin/ssh-keygen"


@pytest.mark.parametrize("problem", ["directory", "readable", "other_owner"])
def test_sandbox_key_refuses_an_unsafe_key(tmp_path, monkeypatch, problem):
    monkeypatch.setitem(MODULE, "as_host", as_test_user)
    key = tmp_path / "python-docs-sandbox"
    if problem == "directory":
        key.mkdir()
    else:
        key.write_text("private")
        key.chmod(0o644 if problem == "readable" else 0o600)
    uid = os.getuid() + (1 if problem == "other_owner" else 0)
    with pytest.raises(ValueError, match="private regular file"):
        INSTALLER["sandbox_key"](key, uid)


def test_sandbox_key_reuses_an_existing_key(tmp_path, monkeypatch):
    calls = []

    def host(*args, **kwargs):
        calls.append(args)
        return as_test_user(*args, **kwargs)

    monkeypatch.setitem(MODULE, "as_host", host)
    key = tmp_path / "python-docs-sandbox"
    key.write_text("private")
    key.chmod(0o600)
    public = f"ssh-ed25519 {KEY_B64} ahammouda@vision\n"
    Path(f"{key}.pub").write_text(public)
    assert INSTALLER["sandbox_key"](key, os.getuid()) == public
    assert calls == [("/usr/bin/head", "-c", "4096", f"{key}.pub")]


def test_pub_linked_to_a_private_key_never_reaches_authorized_keys(tmp_path, monkeypatch):
    monkeypatch.setitem(MODULE, "as_host", as_test_user)
    key = tmp_path / "python-docs-sandbox"
    key.write_text("private")
    key.chmod(0o600)
    pem = tmp_path / "verifier.pem"
    pem.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK\n-----END RSA PRIVATE KEY-----\n")
    Path(f"{key}.pub").symlink_to(pem)
    ssh = tmp_path / "ssh"
    ssh.mkdir()
    public = INSTALLER["sandbox_key"](key, os.getuid())
    with pytest.raises(ValueError, match="one ssh-ed25519 key"):
        INSTALLER["authorize"](ssh, "verifier", INSTALLER["public_key"](public))
    assert not (ssh / "authorized_keys").exists()


@pytest.mark.parametrize(
    "public",
    [
        "",
        f"ssh-rsa {KEY_B64}",
        f"ssh-ed25519 {KEY_B64}\nssh-ed25519 {KEY_B64}",
        f'command="/bin/sh" ssh-ed25519 {KEY_B64}',
        "ssh-ed25519 not-base64!",
        "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519" + bytes(36)).decode(),
    ],
    ids=["empty", "rsa", "two_lines", "options", "not_base64", "wrong_blob"],
)
def test_public_key_refuses_anything_but_one_ed25519_key(public):
    with pytest.raises(ValueError, match="one ssh-ed25519 key"):
        INSTALLER["public_key"](public)


@pytest.mark.parametrize(
    "public",
    [f"ssh-ed25519 {KEY_B64}\n", f"ssh-ed25519 {KEY_B64} my laptop key\r\n"],
    ids=["bare", "comment_crlf"],
)
def test_authorize_writes_the_checked_key_on_one_restricted_line(tmp_path, monkeypatch, public):
    writes = []
    monkeypatch.setitem(MODULE, "owned", lambda *args, **kwargs: writes.append((args, kwargs)))
    INSTALLER["authorize"](tmp_path, "verifier", INSTALLER["public_key"](public))
    line = f"{WORKER_LINE}{KEY_B64}\n"
    assert writes == [((tmp_path / "authorized_keys", line, 0, 0), {"mode": 0o644})]
```

- [ ] **Step 7: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_vision_install.py -q -k "sandbox_key or public_key or authorize or pub_linked"`
Expected: `15 failed` with `KeyError` for `sandbox_key`, `public_key` or
`authorize`

- [ ] **Step 8: Implement `sandbox_key(key, uid)`.** In this order:
  1. If `not key.exists()`, call
     `as_host("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key))`.
     The empty stdin makes an unexpected overwrite prompt fail instead of
     waiting.
  2. Call `os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)`,
     then `os.fstat`, then close the descriptor in a `finally`. A symlink
     raises `OSError` here, whatever step 1 saw.
  3. Unless `stat.S_ISREG(st_mode)`, `st_uid == uid` and
     `st_mode & 0o077 == 0` all hold, raise
     `ValueError(f"{key} must be a private regular file owned by {HOST_USER}")`.
  4. Return `as_host("/usr/bin/head", "-c", "4096", f"{key}.pub")`.

- [ ] **Step 9: Implement `public_key(text)` and `authorize(ssh, role, public)`.**
  - `public_key`:
    - Strip `text`, require exactly one line, and split it with
      `maxsplit=2`.
    - Require the first field to be `ssh-ed25519`.
    - Require the second field to decode with
      `base64.b64decode(field, validate=True)` to exactly 51 bytes that start
      with `b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"`.
    - Treat a missing field and a decode error the same way. On any failure,
      raise `ValueError("The sandbox public key must be one ssh-ed25519 key")`.
    - Return `"ssh-ed25519 " + base64.b64encode(blob).decode()`: the
      re-encoded bytes that passed the check, without the comment.
  - `authorize` does not check again. It holds today's restriction text
    (`:244-246`), unchanged: `restrict`, plus
    `,command="/opt/python-docs-vision/worker_shell.py {role}"` for any role
    other than `owner`. It writes with
    `owned(ssh / "authorized_keys", f"{restriction} {public}\n", 0, 0, mode=0o644)`.

- [ ] **Step 10: Use them in `main()`.**
  - Keep `key = HOST_HOME / ".ssh/python-docs-sandbox"`; `identityFile`
    (`:296`) still needs it.
  - Replace `:193-197` (`ssh-keygen` as root, `os.chown`, `key.chmod` and
    the root read of `.pub`) with
    `public = public_key(sandbox_key(key, host.pw_uid))`. That checks the key
    once, before the role loop and so before any `useradd`, `pkill` or
    `chown`.
  - Replace `:244-247` with `authorize(ssh, role, public)`.

- [ ] **Step 11: Run the installer tests**

Run: `uv run --locked pytest tests/test_vision_install.py tests/test_vision_control.py -q`
Expected: all passed

- [ ] **Step 12: Append the `uv` tests and the `main()` check.** The last test
  stays red until Step 15, because `main()` still copies `uv` by path until
  then.

```python
def test_copy_uv_copies_a_regular_file_the_host_user_owns(tmp_path):
    source, target = tmp_path / "uv", tmp_path / "bin-uv"
    source.write_bytes(b"\x7fELF uv")
    source.chmod(0o700)
    INSTALLER["copy_uv"](source, target, os.getuid())
    assert target.read_bytes() == b"\x7fELF uv"
    assert mode(target) == 0o755
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin-uv", "uv"]  # no temp left


@pytest.mark.parametrize("problem", ["symlink", "fifo", "other_owner", "missing"])
def test_copy_uv_refuses_anything_else(tmp_path, problem):
    source, target = tmp_path / "uv", tmp_path / "bin-uv"
    real = tmp_path / "real-uv"
    real.write_bytes(b"\x7fELF uv")
    if problem == "symlink":
        source.symlink_to(real)
    elif problem == "fifo":
        os.mkfifo(source)
    elif problem == "other_owner":
        source.write_bytes(b"\x7fELF uv")
    uid = os.getuid() + (1 if problem == "other_owner" else 0)
    with pytest.raises(ValueError, match="copy uv to"):
        INSTALLER["copy_uv"](source, target, uid)
    assert not target.exists()


def test_main_does_no_host_file_work_as_root():
    import inspect

    source = inspect.getsource(MODULE["main"])
    for root_host_write in [
        "host.pw_gid", "CONFIG.read_text", "shutil.copyfile(HOST_HOME", "key.chmod"
    ]:
        assert root_host_write not in source, root_host_write
```

- [ ] **Step 13: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_vision_install.py -q -k "copy_uv or no_host_file_work"`
Expected: `6 failed`. The five `copy_uv` tests fail with
`KeyError: 'copy_uv'`. The `main()` check fails with
`AssertionError: shutil.copyfile(HOST_HOME`.

- [ ] **Step 14: Implement `copy_uv(source, target, uid)`.**
  - Build one message:
    `f"{source} must be a regular file owned by {HOST_USER}; copy uv to {target} yourself"`.
  - Open the source with
    `os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC`. Turn an
    `OSError` there (link, missing file) into `ValueError(message)`, chained
    `from` the original.
  - `fstat` the descriptor. Raise `ValueError(message)` unless
    `stat.S_ISREG(st_mode)` and `st_uid == uid`. Check both before anything
    is created.
  - Create a temporary file next to the target,
    `target.with_name(f".{target.name}.{os.getpid()}")`, with
    `os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC` and
    mode `0o755`.
  - Copy with `shutil.copyfileobj`, then call `os.fchmod(fd, 0o755)` so that
    the umask cannot narrow the mode, and close the file.
  - Finish with `os.replace(temporary, target)`. If anything fails after the
    temporary file exists, unlink it and re-raise. A failed copy then never
    leaves a partial `/usr/local/bin/uv`.

- [ ] **Step 15: Use it in `main()`.** `:188-191` becomes:

```python
    uv = Path("/usr/local/bin/uv")
    if not uv.exists():
        copy_uv(HOST_HOME / ".local/bin/uv", uv, host.pw_uid)
```

- [ ] **Step 16: Run the installer tests**

Run: `uv run --locked pytest tests/test_vision_install.py tests/test_vision_control.py -q`
Expected: all passed. `tests/test_vision_install.py` alone reports `23 passed`.

- [ ] **Step 17: Append the backup test**

```python
def test_config_backup_never_writes_through_a_planted_link(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_text("root-only")
    secret.chmod(0o644)
    backup = tmp_path / "python-docs-guardrails-1"
    backup.mkdir()
    (backup / "openclaw.json").symlink_to(secret)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)  # the swap lands after mkdir
    with pytest.raises(OSError):
        INSTALLER["backup_config"](backup, "{}")
    assert secret.read_text() == "root-only"
    assert mode(secret) == 0o644
```

- [ ] **Step 18: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_vision_install.py -q -k config_backup`
Expected: `1 failed` with `KeyError: 'backup_config'`

- [ ] **Step 19: Implement `backup_config(backup, text)` and use it in
  `main()`.**
  - Body, in this order:
    1. `backup.mkdir(mode=0o700, parents=True)`, without `exist_ok`;
    2. `os.open(backup / "openclaw.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)`;
    3. write `text` through that descriptor (`os.fdopen(fd, "w")`).
  - No `write_text`, and no path-based `chmod`: the `0o600` passed to
    `os.open` sets the mode.
  - In `main()`, replace the three backup lines after `backup = Path(...)`
    (`:332-334`: `mkdir`, `write_text`, `chmod`) with
    `backup_config(backup, old)`.

- [ ] **Step 20: Run the installer tests and lint**

Run: `uv run --locked pytest tests/test_vision_install.py tests/test_vision_control.py -q`
Expected: all passed. `tests/test_vision_install.py` alone reports `24 passed`.

Run: `uv run --locked ruff check ops/ tests/`
Expected: `All checks passed!`

- [ ] **Step 21: Commit**

```bash
git add ops/vision/install.py tests/test_vision_install.py
git commit -m "fix: stop the Vision installer from following planted links as root" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 2: Find the verifier variable by name (research 7c)

**Files:**
- Modify: `ops/vision/configure_apps.py`: add `set_variable` above
  `activate()` (`:168`); replace `:209-216`
- Test: `tests/test_vision_control.py` (append)

**Interfaces:**
- Produces:
  - `set_variable(name: str, value: str) -> dict`;
  - for Task 3, the test helper `load_setup(monkeypatch) -> dict`, which
    loads `configure_apps.py` against the test's `CONTROL`.

- [ ] **Step 1: Append the tests**

```python
def load_setup(monkeypatch):
    loader = runpy.run_path
    monkeypatch.setattr(runpy, "run_path", lambda _: CONTROL)
    return loader(str(Path(__file__).parents[1] / "ops/vision/configure_apps.py"))


def test_activation_updates_the_verifier_variable_found_by_name(monkeypatch):
    setup = load_setup(monkeypatch)
    calls = []

    def operator_api(path, method="GET", body=None):
        calls.append((path, method, body))
        if path == "actions/variables" and method == "GET":  # page 1 of 2 lacks the target
            return {"total_count": 11, "variables": [{"name": f"V{n}"} for n in range(10)]}
        if path == "actions/variables/VISION_VERIFIER_APP_ID" and method == "GET":
            return {"name": "VISION_VERIFIER_APP_ID", "value": "41"}
        return {}

    monkeypatch.setitem(setup["set_variable"].__globals__, "operator_api", operator_api)
    setup["set_variable"]("VISION_VERIFIER_APP_ID", "42")
    assert calls[-1] == (
        "actions/variables/VISION_VERIFIER_APP_ID",
        "PATCH",
        {"name": "VISION_VERIFIER_APP_ID", "value": "42"},
    )
    assert all(method != "POST" for _, method, _ in calls)


@pytest.mark.parametrize(
    "status,write",
    [
        ("404", ("actions/variables", "POST", {"name": "VISION_VERIFIER_APP_ID", "value": "42"})),
        ("403", None),
    ],
)
def test_activation_creates_the_variable_only_when_github_says_404(monkeypatch, status, write):
    setup = load_setup(monkeypatch)
    calls = []

    def operator_api(path, method="GET", body=None):
        calls.append((path, method, body))
        if method == "GET":
            raise subprocess.CalledProcessError(
                1, ["gh"], output='{"message": "x"}', stderr=f"gh: x (HTTP {status})\n"
            )
        return {}

    monkeypatch.setitem(setup["set_variable"].__globals__, "operator_api", operator_api)
    if write is None:
        with pytest.raises(subprocess.CalledProcessError):
            setup["set_variable"]("VISION_VERIFIER_APP_ID", "42")
        assert [method for _, method, _ in calls] == ["GET"]
    else:
        setup["set_variable"]("VISION_VERIFIER_APP_ID", "42")
        assert calls[-1] == write


def test_activation_reads_the_variable_by_name(monkeypatch):
    import inspect

    source = inspect.getsource(load_setup(monkeypatch)["activate"])
    assert 'set_variable("VISION_VERIFIER_APP_ID", str(app_id))' in source
    assert 'operator_api("actions/variables")' not in source
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k "variable"`
Expected: `4 failed`. Three fail with `KeyError: 'set_variable'`, and the
`activate()` check fails on its first assertion.

- [ ] **Step 3: Implement `set_variable(name, value)`.**
  - Build `body = {"name": name, "value": value}`.
  - Call `operator_api(f"actions/variables/{name}")`.
    - If that raises `subprocess.CalledProcessError` and `"(HTTP 404)"` is in
      its `stderr`, return `operator_api("actions/variables", "POST", body)`.
    - Re-raise any other error.
  - After a successful read, return
    `operator_api(f"actions/variables/{name}", "PATCH", body)`.

- [ ] **Step 4: Use it in `activate()`.** Replace `:209-216` (the variable
  dict, the unpaged listing and the POST-or-PATCH call) with
  `set_variable("VISION_VERIFIER_APP_ID", str(app_id))`.

- [ ] **Step 5: Run the setup tests**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k "variable or activation or manifests"`
Expected: all passed, including
`test_temporary_capture_and_app_activation_lifecycle`. Its fake answers the
by-name GET with `{}`, so it takes the PATCH path.

- [ ] **Step 6: Commit**

```bash
git add ops/vision/configure_apps.py tests/test_vision_control.py
git commit -m "fix: find the verifier App variable by name during activation" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: Share one App permission map (research 7d)

**Files:**
- Modify: `ops/vision/control.py`: add a module-level `PERMISSIONS` after
  `REVIEW_TIMEOUT`, and in `token()` delete the inline map
  (`c03e497:96-107`) and use `PERMISSIONS[role]` in the request body
  (`c03e497:112`)
- Modify: `ops/vision/configure_apps.py:23-33`, which becomes `PERMISSIONS = CONTROL["PERMISSIONS"]`
- Test: `tests/test_vision_control.py` (append)

**Interfaces:**
- Consumes: `load_setup` (Task 2).
- Produces: `PERMISSIONS: dict[str, dict[str, str]]` in `control.py`, with
  the same two maps as today:
  - `owner`: `contents`, `pull_requests`, `issues`, `workflows` and
    `actions` set to `write`, and `checks` set to `read`;
  - `verifier`: `contents` and `pull_requests` set to `read`, and `checks`
    set to `write`.

- [ ] **Step 1: Append the test**

```python
def test_app_setup_and_broker_share_one_permission_map(monkeypatch):
    assert load_setup(monkeypatch)["PERMISSIONS"] is CONTROL["PERMISSIONS"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k "one_permission_map"`
Expected: `1 failed` with `KeyError: 'PERMISSIONS'`

- [ ] **Step 3: Move the map.** Cut the two dicts out of `token()` into the
  module-level `PERMISSIONS`, without changing them. Point `token()` and
  `configure_apps.py` at it.

- [ ] **Step 4: Run the permission tests**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k "permission or manifests or token or activation"`
Expected: all passed, including `test_owner_token_requests_only_scoped_actions_write`
and `test_app_manifests_disable_unused_webhooks`

- [ ] **Step 5: Commit**

```bash
git add ops/vision/control.py ops/vision/configure_apps.py tests/test_vision_control.py
git commit -m "refactor: share one App permission map between broker and setup" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Offer only push-triggered workflows for reruns (research 7e)

**Files:**
- Modify: `ops/vision/control.py`: add a module-level `RERUN_WORKFLOWS`
  after `PERMISSIONS`, and in `rerun()` replace the inline set
  (`c03e497:443-448`)
- Test: `tests/test_vision_control.py` (append)

**Interfaces:**
- Produces: `RERUN_WORKFLOWS: frozenset[str]` of repo-relative workflow
  paths.

- [ ] **Step 1: Hoist the allowlist without changing it.** Add
  `RERUN_WORKFLOWS = frozenset({...})` holding today's six paths, `e2e.yml`
  included. Make `rerun()` test `run.get("path") not in RERUN_WORKFLOWS`.

- [ ] **Step 2: Run the rerun tests**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k rerun`
Expected: `2 passed`

- [ ] **Step 3: Commit the refactor**

```bash
git add ops/vision/control.py
git commit -m "refactor: name the Vision rerun workflow allowlist" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Append the policy test**

```python
def test_rerun_allowlist_names_only_push_triggered_workflows():
    import yaml

    for path in sorted(CONTROL["RERUN_WORKFLOWS"]):
        workflow = yaml.safe_load((Path(__file__).parents[1] / path).read_text())
        triggers = workflow.get("on", workflow.get(True))  # PyYAML reads a bare `on` key as True
        assert "push" in triggers, path
```

- [ ] **Step 5: Run it to verify it fails**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k push_triggered`
Expected: `1 failed` with `AssertionError: .github/workflows/e2e.yml`

- [ ] **Step 6: Remove `.github/workflows/e2e.yml` from `RERUN_WORKFLOWS`.**
  `e2e.yml` runs on `workflow_dispatch`, `workflow_call` and `schedule`.
  `rerun()` already requires `event == "push"`, so the entry could never
  match.

- [ ] **Step 7: Run the rerun tests**

Run: `uv run --locked pytest tests/test_vision_control.py -q -k "rerun or push_triggered"`
Expected: `3 passed`

- [ ] **Step 8: Commit**

```bash
git add ops/vision/control.py tests/test_vision_control.py
git commit -m "fix: stop offering e2e.yml for Vision current-main reruns" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Document, run the gate, open the PR

**Files:**
- Modify: `ops/vision/README.md:158-162`, `CHANGELOG.md` (`## [Unreleased]`)

- [ ] **Step 1: Update the operator README.** After the paragraph that ends
  "The installer does not change the existing job or activate Apps."
  (`ops/vision/README.md:162`), add:
  > The installer does the host user's file work as `ahammouda` through
  > `runuser`: the sandbox SSH key, its known-hosts file, the OpenClaw
  > configuration and the project workspaces. A first install therefore
  > needs `/srv/openclaw/.openclaw` writable by `ahammouda`. The installer
  > refuses a symlinked or group-readable sandbox key and accepts exactly one
  > `ssh-ed25519` public key. It copies `uv` only from a regular file that
  > `ahammouda` owns. If `~/.local/bin/uv` is a link (pipx or mise install it
  > that way), install uv at `/usr/local/bin/uv` yourself and rerun the
  > installer. The configuration backup is still written by root, into a
  > new directory, and never through an existing name or link.

- [ ] **Step 2: Add the changelog entries** under `## [Unreleased]`, in the
  existing `### Fixed` heading and a `### Security` heading:

```markdown
### Fixed

- Vision operations: App activation reads `VISION_VERIFIER_APP_ID` by name
  instead of from the first page of variables. The broker and App setup
  share one permission map. `pdctl rerun` no longer offers `e2e.yml`, which
  never runs on push.

### Security

- Vision operations: the installer no longer follows links planted in the
  host user's directories while it runs as root. It writes host-user files
  as that user and checks the sandbox key without following links. It
  accepts only one `ssh-ed25519` public key, and copies `uv` only from a
  regular file the host user owns. The OpenClaw configuration backup is
  written through an exclusive, no-follow descriptor.
```

- [ ] **Step 3: Run the gate in a fresh shell**

| Run | Expected |
|---|---|
| `uv run --locked ruff check src/ tests/ benchmarks/ ops/ .github/scripts/` | `All checks passed!` |
| `uv run --locked pyright src/ benchmarks/` | `0 errors` |
| `uv run --locked pytest --tb=short -q` | all passed |

- [ ] **Step 4: Measure the diff**

Run: `git diff --shortstat main...HEAD -- ops tests`
Expected: about 480 insertions plus deletions. The label applies whatever
the size. Above 500, list the size as a second §7 trigger in the section.

- [ ] **Step 5: Push and open the PR with the `supervisor-review` label.**
  Title:
  `fix: keep the Vision installer and App setup from following planted state`.
  Fill in `.github/PULL_REQUEST_TEMPLATE/agent.md` with
  `Refs docs/plans/2026-10-30-audit-fixes-roadmap.md (PR 7)`. The body
  states:
  - the acceptance criteria, with test names as evidence:
    - the symlinked key is refused and its target keeps mode 0644;
    - the race case still fails closed;
    - a `.pub` linked to a PEM never reaches `authorized_keys`;
    - the backup never writes through a planted link;
    - `main()` does no host-file work as root;
    - the symlinked `uv` is refused;
    - the variable on page 2 gets a PATCH, and `activate()` reads it by name;
    - the permission map is shared by identity;
    - every allowlisted rerun workflow runs on push;
  - a `## Why this triggered supervisor review` section. It quotes the owner
    sentence from Global Constraints and covers the three root operations
    that remain in host-writable places:
    - the `openclaw.json` backup, still a root write, now through
      `backup_config()`;
    - the root `fstat` check of the private key;
    - the `uv` copy, which refuses a symlinked `uv`;
    - the diff size, if Step 4 found more than 500 lines;
  - "Every `control.py` edit changes the broker's policy hash. Cached reviews
    and temporary receipts are invalidated by design."
  - Deployment:
    - The operator reinstalls with `sudo python3 ops/vision/install.py`.
    - Before a first install, `/srv/openclaw/.openclaw` must be writable by
      `ahammouda`, because the workspaces are now created as that user.
    - An operator whose `uv` is a pipx link copies `uv` to
      `/usr/local/bin/uv` first.
    - Re-runs are idempotent.
    - PR 6 also changes the policy hash, so deploy both PRs in one
      reinstall. Otherwise every open PR in temporary-token mode needs
      `pdctl verify` twice.
  - the gate output and the CodeRabbit triage once it comments;
  - the closing line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
