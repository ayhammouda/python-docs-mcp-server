#!/usr/bin/python3 -I
"""Root-owned SSH forced command: contributed code sees only its project workspace."""

import os
import pwd
import resource
import sys
from pathlib import Path


def main():
    role = sys.argv[1] if len(sys.argv) == 2 else ""
    if role not in {"implementer", "verifier"}:
        raise ValueError("Unknown isolated worker")
    account = pwd.getpwuid(os.getuid())
    home = Path(f"/var/lib/python-docs/{role}")
    if account.pw_name != f"pd-{role}" or account.pw_dir != str(home):
        raise PermissionError("Wrong worker identity")
    args = [
        "/opt/python-docs-vision/bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    for name in ["/usr", "/bin", "/lib", "/lib64", "/etc"]:
        if Path(name).exists():
            args += ["--ro-bind", name, name]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--bind",
        str(home / "tmp"),
        "/tmp",
        "--tmpfs",
        "/run",
        "--ro-bind",
        str(home),
        str(home),
    ]
    # On systemd hosts /etc/resolv.conf points into /run, which is otherwise hidden.
    resolver = Path("/etc/resolv.conf").resolve()
    if resolver.is_file() and str(resolver).startswith("/run/"):
        args += ["--ro-bind", str(resolver), str(resolver)]
    for name in ["sandboxes", ".cache", ".local", "tmp"]:
        path = str(home / name)
        args += ["--bind", path, path]
    if role == "implementer":
        exchange = "/var/lib/python-docs/exchange/implementation"
        args += ["--bind", exchange, exchange]
    args += [
        "--chdir",
        str(home),
        "--clearenv",
        "--setenv",
        "HOME",
        str(home),
        # Keep temporary build artifacts and cache publications on one mount.
        "--setenv",
        "XDG_CACHE_HOME",
        "/tmp/cache",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "/bin/sh",
        "-c",
        os.environ.get("SSH_ORIGINAL_COMMAND", "exit 1"),
    ]
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (900, 900))
    os.execv(args[0], args)


if __name__ == "__main__":
    main()
