#!/usr/bin/python3 -I
"""Operator handoff: GitHub manifests, private capture, then fail-closed activation.

Expose only through an SSH localhost forward. Never prints keys or tokens.
"""

import argparse
import html
import http.server
import json
import os
import re
import runpy
import secrets
import subprocess
import time
import urllib.parse
from pathlib import Path

CONTROL = runpy.run_path("/opt/python-docs-vision/control.py")
CONFIG = Path("/etc/python-docs-vision")
REPO = CONTROL["REPO"]
PERMISSIONS = {
    "owner": {
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
        "workflows": "write",
        "checks": "read",
        "actions": "write",
    },
    "verifier": {"contents": "read", "pull_requests": "read", "checks": "write"},
}
PENDING = {}
SETUP_KEY = secrets.token_urlsafe(32)


def manifest(role, port):
    return {
        "name": f"Aymen Python Docs {role.title()}",
        "url": f"https://github.com/{REPO}",
        "description": f"Scoped autonomous Python docs {role}; no account administration.",
        "public": False,
        "hook_attributes": {"url": f"http://127.0.0.1:{port}/unused", "active": False},
        "redirect_url": f"http://127.0.0.1:{port}/callback",
        "default_events": [],
        "default_permissions": PERMISSIONS[role],
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Callback URLs contain one-use codes; never log them.

    def page(self, content, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content.encode())

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.page("Invalid host", 400)
            return
        url = urllib.parse.urlsplit(self.path)
        try:
            if url.path == "/":
                key = urllib.parse.parse_qs(url.query).get("key", [""])[0]
                if not secrets.compare_digest(key, SETUP_KEY):
                    self.page("Use the setup URL printed in the operator terminal", 403)
                    return
                body = "<h1>Python docs: scoped GitHub identities</h1>"
                body += (
                    "<p>Create each private App, then install it on <strong>only "
                    + REPO
                    + "</strong>.</p>"
                )
                for role in PERMISSIONS:
                    config = CONFIG / f"{role}.json"
                    if config.exists():
                        slug = json.loads(config.read_text())["slug"]
                        body += (
                            f'<p>{role}: registered. <a href="https://github.com/apps/'
                            f'{html.escape(slug)}/installations/new">Install on the project</a></p>'
                        )
                        continue
                    state = secrets.token_urlsafe(32)
                    PENDING[state] = (role, time.time())
                    payload = html.escape(
                        json.dumps(manifest(role, self.server.server_port)), quote=True
                    )
                    body += (
                        '<form method="post" action="https://github.com/settings/apps/new'
                        f'?state={state}"><input type="hidden" name="manifest" value="{payload}">'
                        f"<button>Create {role} App</button></form>"
                        f"<pre>{html.escape(json.dumps(PERMISSIONS[role], indent=2))}</pre>"
                    )
                body += (
                    "<p>After both installations, run configure_apps.py activate "
                    "in the operator SSH session. The worker never receives either private key.</p>"
                )
                self.page(body)
                return
            if url.path != "/callback":
                self.page("Not found", 404)
                return
            query = urllib.parse.parse_qs(url.query)
            state, code = query.get("state", [""])[0], query.get("code", [""])[0]
            role, issued = PENDING.pop(state)
            if time.time() - issued > 3300 or not re.fullmatch(r"[A-Za-z0-9]{20,200}", code):
                raise ValueError("Expired or invalid registration callback")
            if (CONFIG / f"{role}.json").exists():
                raise ValueError("Identity already registered; refusing credential replacement")
            result = CONTROL["request"](
                f"https://api.github.com/app-manifests/{code}/conversions", "", "POST"
            )
            if result["owner"]["login"] != REPO.split("/")[0]:
                raise ValueError("App must be owned by the repository owner")
            key = CONFIG / f"{role}.pem"
            with key.open("x") as output:
                output.write(result["pem"])
            key.chmod(0o600)
            metadata = CONFIG / f"{role}.json"
            metadata.write_text(json.dumps({"app_id": result["id"], "slug": result["slug"]}))
            metadata.chmod(0o600)
            self.page(
                f"<h1>{role.title()} registered</h1>"
                "<p>Private key saved in the root-owned directory.</p>"
                f'<a href="https://github.com/apps/{html.escape(result["slug"])}/installations/new">'
                f"Install on only {REPO}</a>"
                "<p>Return to the setup URL in your operator terminal.</p>"
            )
        except Exception:
            self.page(
                "Setup did not complete. Check registration/installation state before retrying. "
                "No credentials were displayed.",
                400,
            )


def operator_api(path, method="GET", body=None):
    command = [
        "/usr/sbin/runuser",
        "-u",
        "ahammouda",
        "--",
        "/home/linuxbrew/.linuxbrew/bin/gh",
        "api",
        f"repos/{REPO}/{path}",
        "--method",
        method,
    ]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=None if body is None else json.dumps(body),
        capture_output=True,
        text=True,
        check=True,
        env=CONTROL["ENV"],
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def activate():
    (CONFIG / "activated").unlink(missing_ok=True)
    for role, expected in PERMISSIONS.items():
        config_path = CONFIG / f"{role}.json"
        settings = json.loads(config_path.read_text())
        jwt = CONTROL["app_jwt"](role)
        app = CONTROL["request"]("https://api.github.com/app", jwt)
        if app["owner"]["login"] != REPO.split("/")[0]:
            raise ValueError(f"{role}: expected an App owned by {REPO.split('/')[0]}")
        actual = {k: v for k, v in app["permissions"].items() if k != "metadata"}
        if actual != expected:
            raise ValueError(f"{role}: permissions differ from the reviewed manifest")
        installs = CONTROL["request"]("https://api.github.com/app/installations", jwt)
        installs = [i for i in installs if i["account"]["login"] == REPO.split("/")[0]]
        if len(installs) != 1 or installs[0]["repository_selection"] != "selected":
            raise ValueError(f"{role}: install on selected repositories only")
        settings["installation_id"] = installs[0]["id"]
        config_path.write_text(json.dumps(settings))
        unrestricted = CONTROL["request"](
            f"https://api.github.com/app/installations/{installs[0]['id']}/access_tokens",
            jwt,
            "POST",
            {"permissions": {"contents": "read"}},
        )["token"]
        repos = CONTROL["request"]("https://api.github.com/installation/repositories", unrestricted)
        if repos["total_count"] != 1 or repos["repositories"][0]["full_name"] != REPO:
            raise ValueError(f"{role}: installation must contain exactly {REPO}")
    app_id = json.loads((CONFIG / "verifier.json").read_text())["app_id"]
    rules = operator_api("rulesets/15269598")
    checks = next(
        r["parameters"]["required_status_checks"]
        for r in rules["rules"]
        if r["type"] == "required_status_checks"
    )
    checks[:] = [c for c in checks if c["context"] != "Independent verification"]
    checks.append({"context": "Independent verification", "integration_id": app_id})
    body = {
        k: rules[k]
        for k in ["name", "target", "enforcement", "bypass_actors", "conditions", "rules"]
    }
    operator_api("rulesets/15269598", "PUT", body)
    variable = {"name": "VISION_VERIFIER_APP_ID", "value": str(app_id)}
    variables = operator_api("actions/variables")["variables"]
    exists = any(v["name"] == variable["name"] for v in variables)
    operator_api(
        "actions/variables" + ("/VISION_VERIFIER_APP_ID" if exists else ""),
        "PATCH" if exists else "POST",
        variable,
    )
    confirmed = operator_api("rulesets/15269598")
    required = next(
        r["parameters"]["required_status_checks"]
        for r in confirmed["rules"]
        if r["type"] == "required_status_checks"
    )
    if {"context": "Independent verification", "integration_id": app_id} not in required:
        raise ValueError("Verifier check binding was not confirmed")
    owner_id = json.loads((CONFIG / "owner.json").read_text())["app_id"]
    existing = operator_api("rulesets?per_page=100")
    # Creation is limited to the broker App; nobody can move or delete existing tags.
    for name, types, bypass in [
        (
            "Vision release creation",
            ["creation"],
            [{"actor_id": owner_id, "actor_type": "Integration", "bypass_mode": "always"}],
        ),
        ("Immutable release tags", ["update", "deletion"], []),
    ]:
        body = {
            "name": name,
            "target": "tag",
            "enforcement": "active",
            "bypass_actors": bypass,
            "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
            "rules": [{"type": rule} for rule in types],
        }
        found = next((r for r in existing if r["name"] == name), None)
        operator_api(
            f"rulesets/{found['id']}" if found else "rulesets", "PUT" if found else "POST", body
        )
    operator_api(
        "environments/pypi",
        "PUT",
        {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}},
    )
    policies = operator_api("environments/pypi/deployment-branch-policies")["branch_policies"]
    for policy in policies:
        if policy["name"] != "v*" or policy["type"] != "tag":
            operator_api(f"environments/pypi/deployment-branch-policies/{policy['id']}", "DELETE")
    if not any(p["name"] == "v*" and p["type"] == "tag" for p in policies):
        operator_api(
            "environments/pypi/deployment-branch-policies", "POST", {"name": "v*", "type": "tag"}
        )
    (CONFIG / "temporary-token").unlink(missing_ok=True)
    (CONFIG / "activated").write_text(str(int(time.time())))
    print(
        "Both Apps are scoped to one repository. Independent verification is required. "
        "Broker activated."
    )


def enable_temporary():
    if any((CONFIG / f"{role}.json").exists() for role in PERMISSIONS):
        raise ValueError("Apps already registered; complete activation instead of downgrading")
    (CONFIG / "activated").unlink(missing_ok=True)
    result = subprocess.run(
        ["/usr/sbin/runuser", "-u", "ahammouda", "--",
         "/home/linuxbrew/.linuxbrew/bin/gh", "auth", "token", "--hostname", "github.com"],
        env=CONTROL["ENV"], capture_output=True, text=True, check=True,
    )
    credential = result.stdout.strip()
    account = CONTROL["request"]("https://api.github.com/user", credential)
    repository = CONTROL["request"](f"https://api.github.com/repos/{REPO}", credential)
    if account["login"] != REPO.split("/")[0] or not repository["permissions"]["push"]:
        raise ValueError("Expected Vision's existing repository-owner credential with write access")
    fd = os.open(
        CONFIG / "temporary-token", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(credential)
    (CONFIG / "activated").write_text(str(int(time.time())))
    print("Temporary broker authentication enabled. Independent review and CI remain required. "
          "Releases still require Apps; workers never receive the credential.")


def main():
    if os.geteuid() != 0 or os.environ.get("SUDO_USER") != "ahammouda":
        raise PermissionError("Operator sudo session required")
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["serve", "activate", "enable-temporary"])
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.operation == "activate":
        activate()
    elif args.operation == "enable-temporary":
        enable_temporary()
    else:
        print(
            f"Operator-only setup: http://127.0.0.1:{args.port}/?key={SETUP_KEY}",
            flush=True,
        )
        http.server.HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
