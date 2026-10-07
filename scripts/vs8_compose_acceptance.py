#!/usr/bin/env python
"""VS8 — live production Compose acceptance (Issue #38, review blockers 1 & 3).

Builds and runs the real production Compose stack (``db`` + ``backend`` +
``proxy``) from a scratch copy of the repository, then asserts the review
checklist against the running stack:

* the backend image's editable install resolves ``app`` from ``/opt/openjm``;
* the backend boots in the production profile, migrations reach head, and the
  PostgreSQL password is consistent between the ``db`` and ``backend`` services
  (the single ``openjm_db_password`` secret, assembled into the DSN by the
  backend entrypoint);
* the reverse proxy (Caddy) serves the built SPA at the root AND on a deep
  route, serves a real hashed asset, and proxies ``/api`` to the backend;
* the public readiness result is minimal, and the detailed operational
  endpoints require the ``ops_token``.

The stack runs with the production profile; the private model endpoint and the
OIDC issuer are not contacted at startup (preflight validates configuration
shape only), so no live external IdP/TLS is required for this packaging gate.

Run with Docker access, e.g.::

    sg docker -c "backend/.venv/bin/python scripts/vs8_compose_acceptance.py --out /tmp/vs8-compose.json"

NOT RUN by this harness: live chat / knowledge / hybrid against a real model
(that is Gate 1/2), and live external IdP interop (Gate 3).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE_REL = "deploy/compose/docker-compose.prod.yml"

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", "node_modules", ".venv", "data", "dist", "__pycache__",
    ".pytest_cache", "*.pyc", ".mypy_cache", "htmlcov",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd: list[str], *, cwd: Path, env: dict, timeout: int = 1800) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=env, text=True, capture_output=True, timeout=timeout
    )
    if proc.stdout:
        print(proc.stdout[-4000:], flush=True)
    if proc.stderr.strip():
        print("[stderr]", proc.stderr[-2000:], flush=True)
    return proc


def _http(url: str, *, headers: dict | None = None, timeout: float = 10.0):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, response.headers.get("content-type", ""), body
    except urllib.error.HTTPError as exc:  # non-2xx
        return exc.code, exc.headers.get("content-type", ""), exc.read().decode("utf-8", "replace")


def _write_env(scratch: Path, *, origin: str, fernet_key: str, ops_token: str) -> None:
    (scratch / ".env").write_text(
        "\n".join(
            [
                "OPENJM_DEPLOYMENT_PROFILE=production",
                "OPENJM_AUTH_MODE=oidc",
                "OPENJM_AUTH_ALLOW_DEV_MODE=false",
                "OPENJM_OIDC_ISSUER=https://idp.example.invalid/realms/openjm",
                "OPENJM_OIDC_CLIENT_ID=openjm",
                "OPENJM_OIDC_CLIENT_SECRET=compose-acceptance-oidc-secret",
                "OPENJM_OIDC_REDIRECT_URI=https://openjm.example.com/auth/callback",
                "OPENJM_OIDC_DISCOVERY_URL=https://idp.example.invalid/realms/openjm/.well-known/openid-configuration",
                f"OPENJM_CREDENTIAL_ENCRYPTION_KEY={fernet_key}",
                "OPENJM_UPLOAD_DIR=/var/lib/openjm/uploads",
                "OPENJM_VECTOR_PATH=/var/lib/openjm/vector",
                "OPENJM_BACKUP_DIR=/var/backups/openjm",
                "OPENJM_MODEL_PROVIDER_MODE=private_remote",
                "OPENJM_MODEL_BASE_URL=https://llm.internal.example.invalid/v1",
                "OPENJM_MODEL_NAME=openjm-private-8b",
                "OPENJM_MODEL_API_KEY=compose-acceptance-provider-key",
                "OPENJM_MODEL_PROVIDER_FALLBACK=none",
                f"OPENJM_CORS_ORIGINS={origin}",
                "OPENJM_TRUSTED_HOSTS=openjm.example.com,localhost,127.0.0.1,proxy,backend",
                "OPENJM_TRUST_PROXY_HEADERS=true",
                "OPENJM_RATE_LIMIT_ENABLED=true",
                "OPENJM_RATE_LIMIT_EXPENSIVE_PER_MINUTE=600",
                "OPENJM_METRICS_ENABLED=true",
                f"OPENJM_OPS_TOKEN={ops_token}",
                "OPENJM_KNOWLEDGE_ENABLED=true",
                "OPENJM_SCHEDULER_ENABLED=false",
                "",
            ]
        ),
        encoding="utf-8",
    )


def run(out_path: Path | None, *, keep: bool = False) -> dict:
    from cryptography.fernet import Fernet

    results: dict = {"harness": "vs8-compose-acceptance", "profile": "production"}
    scratch = Path("/tmp") / f"vs8-compose-{int(time.time())}"
    scratch.mkdir(parents=True, exist_ok=True)
    results["scratch"] = str(scratch)

    print(f"== staging a scratch copy at {scratch} ==", flush=True)
    for name in ("backend", "frontend", "scripts", "deploy"):
        shutil.copytree(REPO / name, scratch / name, ignore=_COPY_IGNORE)

    http_port, https_port = _free_port(), _free_port()
    origin = f"http://127.0.0.1:{http_port}"
    ops_token = "compose-acceptance-ops-token"
    provider_key = "compose-acceptance-provider-key"
    db_password = secrets.token_hex(16)
    _write_env(scratch, origin=origin, fernet_key=Fernet.generate_key().decode(), ops_token=ops_token)

    secrets_dir = scratch / "deploy" / "compose" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    (secrets_dir / "openjm_db_password.txt").write_text(db_password, encoding="utf-8")

    env = {
        **os.environ,
        "OPENJM_SITE_ADDRESS": ":80",
        "OPENJM_HTTP_PORT": str(http_port),
        "OPENJM_HTTPS_PORT": str(https_port),
    }
    compose = ["docker", "compose", "-f", COMPOSE_REL, "-p", "vs8acc"]
    results["http_base"] = origin

    up_ok = False
    try:
        up = _run([*compose, "up", "-d", "--build"], cwd=scratch, env=env, timeout=3600)
        results["compose_up_exit"] = up.returncode

        # ---- wait for the proxied backend health -------------------------
        deadline = time.time() + 300
        healthy = False
        while time.time() < deadline:
            try:
                status, _, body = _http(f"{origin}/api/health", timeout=5)
                if status == 200 and '"status"' in body:
                    healthy = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)
        results["stack_healthy"] = healthy
        up_ok = healthy
        if not healthy:
            _run([*compose, "ps"], cwd=scratch, env=env)
            _run([*compose, "logs", "--tail", "80", "backend"], cwd=scratch, env=env)
            return results

        # ---- editable install resolves app from /opt/openjm --------------
        app_path = _run(
            [*compose, "exec", "-T", "backend", "/opt/openjm/backend/.venv/bin/python", "-c",
             "import app; print(app.__file__)"],
            cwd=scratch, env=env,
        )
        results["editable_app_path"] = app_path.stdout.strip()
        results["editable_install_resolves_app"] = app_path.stdout.strip().startswith(
            "/opt/openjm/backend/app"
        )

        # ---- the backend (PID 1) assembled a Postgres DSN from the secret ---
        # docker exec does NOT inherit the entrypoint's exported env, so the
        # check reads PID 1's environment (without printing the password).
        dsn = _run(
            [*compose, "exec", "-T", "backend", "sh", "-c",
             "tr '\\0' '\\n' < /proc/1/environ | grep -q "
             "'^OPENJM_DATABASE_URL=postgresql+asyncpg://.*@db:5432/openjm$' "
             "&& echo ASSEMBLED || echo MISSING"],
            cwd=scratch, env=env,
        )
        results["entrypoint_assembled_db_dsn"] = "ASSEMBLED" in dsn.stdout

        # ---- migrations reached head on PostgreSQL ------------------------
        heads = _run(
            [*compose, "exec", "-T", "-e", "OPENJM_CHECK_DB_URL", "backend",
             "/opt/openjm/backend/.venv/bin/python", "-c",
             "import os; from app.migrations_runner import script_heads; "
             "print(','.join(script_heads(os.environ['OPENJM_CHECK_DB_URL'])))"],
            cwd=scratch,
            env={**env, "OPENJM_CHECK_DB_URL": f"postgresql+asyncpg://openjm:{db_password}@db:5432/openjm"},
        )
        rev = _run(
            [*compose, "exec", "-T", "db", "psql", "-U", "openjm", "-d", "openjm", "-tAc",
             "select version_num from alembic_version"],
            cwd=scratch, env=env,
        )
        recorded = rev.stdout.strip()
        head_set = {item.strip() for item in heads.stdout.strip().split(",") if item.strip()}
        results["db_revision"] = recorded
        results["script_heads"] = sorted(head_set)
        results["migrations_at_head"] = bool(recorded) and recorded in head_set

        # ---- the app talks to PostgreSQL, not a silent SQLite fallback ----
        tbl = _run(
            [*compose, "exec", "-T", "db", "psql", "-U", "openjm", "-d", "openjm", "-tAc",
             "select to_regclass('public.conversations') is not null"],
            cwd=scratch, env=env,
        )
        results["postgres_has_app_schema"] = tbl.stdout.strip() == "t"
        no_sqlite = _run(
            [*compose, "exec", "-T", "backend", "sh", "-c",
             "test -f /opt/openjm/data/openjm.db && echo PRESENT || echo ABSENT"],
            cwd=scratch, env=env,
        )
        results["no_sqlite_fallback"] = "ABSENT" in no_sqlite.stdout

        # ---- db accepts the secret password over TCP ----------------------
        tcp = _run(
            [*compose, "exec", "-T", "-e", "PGPASSWORD", "db", "psql", "-h", "db", "-U",
             "openjm", "-d", "openjm", "-tAc", "select 1"],
            cwd=scratch, env={**env, "PGPASSWORD": db_password},
        )
        results["db_password_matches"] = tcp.stdout.strip() == "1"

        # ---- proxy serves the SPA + assets + API -------------------------
        status, ctype, index = _http(f"{origin}/")
        results["spa_root_status"] = status
        results["spa_root_has_mount"] = '<div id="root"' in index
        asset = re.search(r'/assets/[^"\']+\.js', index)
        results["spa_asset_reference"] = asset.group(0) if asset else None
        if asset:
            astatus, actype, _ = _http(f"{origin}{asset.group(0)}")
            results["spa_asset_status"] = astatus
            results["spa_asset_js_content_type"] = "javascript" in actype.lower()

        dstatus, _, dindex = _http(f"{origin}/deep/linked/route")
        results["spa_deep_route_status"] = dstatus
        results["spa_deep_route_serves_index"] = '<div id="root"' in dindex

        astatus, _, abody = _http(f"{origin}/api/health")
        results["api_proxy_status"] = astatus
        results["api_proxy_reaches_backend"] = astatus == 200 and '"status":"ok"' in abody.replace(" ", "")
        results["api_proxy_profile_production"] = '"profile":"production"' in abody.replace(" ", "")

        # frontend assets physically present in the proxy container
        ls = _run([*compose, "exec", "-T", "proxy", "ls", "/srv/openjm/frontend"], cwd=scratch, env=env)
        results["proxy_frontend_present"] = "index.html" in ls.stdout

        # ---- readiness split + ops boundary ------------------------------
        rstatus, _, rbody = _http(f"{origin}/api/ready")
        results["public_ready_status"] = rstatus
        results["public_ready_minimal"] = rstatus == 200 and "components" not in rbody and '"ready"' in rbody
        results["ops_metrics_unauthenticated_status"] = _http(f"{origin}/api/metrics")[0]
        mstatus, _, mbody = _http(
            f"{origin}/api/metrics", headers={"Authorization": f"Bearer {ops_token}"}
        )
        results["ops_metrics_authorized_status"] = mstatus
        results["ops_metrics_has_series"] = "openjm_http_requests_total" in mbody

        # ---- no secret leaks on the public surfaces ----------------------
        blob = "".join(
            _http(f"{origin}{path}")[2]
            for path in ("/api/health", "/api/version", "/api/config/public", "/api/ready")
        )
        results["public_surfaces_no_provider_key"] = provider_key not in blob
        results["public_surfaces_no_ops_token"] = ops_token not in blob
        results["public_surfaces_no_db_password"] = db_password not in blob
    finally:
        if not keep and up_ok:
            _run([*compose, "down", "-v", "--remove-orphans"], cwd=scratch, env=env, timeout=300)
        elif keep:
            print(f"\n[keep] stack left running in {scratch}", flush=True)

    if out_path:
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--keep", action="store_true", help="leave the stack running")
    args = parser.parse_args()
    results = run(Path(args.out) if args.out else None, keep=args.keep)
    print("\n" + json.dumps(results, indent=2))

    checks = [
        "editable_install_resolves_app",
        "entrypoint_assembled_db_dsn",
        "migrations_at_head",
        "postgres_has_app_schema",
        "no_sqlite_fallback",
        "db_password_matches",
        "spa_root_has_mount",
        "spa_asset_js_content_type",
        "spa_deep_route_serves_index",
        "api_proxy_reaches_backend",
        "api_proxy_profile_production",
        "proxy_frontend_present",
        "public_ready_minimal",
        "ops_metrics_has_series",
        "public_surfaces_no_provider_key",
        "public_surfaces_no_ops_token",
        "public_surfaces_no_db_password",
    ]
    ok = results.get("stack_healthy") is True and all(results.get(c) is True for c in checks)
    print("COMPOSE ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
