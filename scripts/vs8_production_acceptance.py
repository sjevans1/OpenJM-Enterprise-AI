#!/usr/bin/env python
"""VS8 — production deployment acceptance: PostgreSQL + OIDC + private model.

Boots the real application in the **production** profile against:
  * a real PostgreSQL server (pgserver, no root);
  * a controlled OIDC fixture (real RS256 JWKS, labelled as a fixture);
  * a controlled HTTPS private OpenAI-compatible model fixture.

Proves: production preflight passes; migrations run on PostgreSQL; startup
refuses dev auth; a fixture-issued bearer authenticates against current DB
membership; Chat routes to the private model with the credential kept
server-side; no client surface leaks the model credential; DR via pg_dump into a
fresh database preserves the row.

Live interop with a specific external IdP image and a real TLS reverse proxy is
NOT exercised here and is reported as NOT RUN.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
SCRIPTS = Path(__file__).resolve().parent
for p in (str(BACKEND), str(SCRIPTS), str(BACKEND / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

FIXTURE_MODEL_KEY = "fixture-private-provider-key-not-a-real-secret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _model_handler_factory(key: str):
    class Handler(_Quiet):
        def do_GET(self):
            if not self.headers.get("Authorization") == f"Bearer {key}":
                return self._send(401, {"error": "unauthorized"})
            if self.path.rstrip("/").endswith("/models"):
                return self._send(200, {"data": [{"id": "openjm-private-8b"}]})
            self._send(404, {})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if not self.headers.get("Authorization") == f"Bearer {key}":
                return self._send(401, {"error": "unauthorized"})
            self._send(200, {"choices": [{"message": {"content": "PRIVATE-MODEL-ANSWER"}}]})

        def _send(self, code, body):
            b = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    return Handler


def _oidc_handler_factory(jwks: dict, jwks_path: str):
    class Handler(_Quiet):
        def do_GET(self):
            if self.path.startswith(jwks_path):
                return self._send(jwks)
            self._send({"issuer": "http://127.0.0.1"})

        def _send(self, body):
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    return Handler


def run(out_path: Path | None) -> dict:
    import httpx
    import psycopg
    import pgserver
    from oidc_testkit import TestIdP

    from app.core.config import Settings
    from app.core.preflight import validate_configuration
    from app.migrations_runner import current_revision, script_heads

    results: dict = {"harness": "vs8-production-acceptance", "fixture_oidc": True,
                     "fixture_private_model": True}
    root = Path("/tmp/vs8-prod")
    root.mkdir(parents=True, exist_ok=True)
    pgdata = root / f"pg-{int(time.time())}"
    srv = pgserver.get_server(str(pgdata), cleanup_mode=None)
    srv.ensure_postgres_running()
    base_uri = srv.get_uri()  # postgresql://postgres:@/postgres?host=/tmp/...
    host_dir = base_uri.split("host=", 1)[1]
    results["postgres_uri_socket"] = True

    # Create a dedicated database.
    with psycopg.connect(base_uri) as conn:
        conn.autocommit = True
        conn.execute("CREATE DATABASE openjm")
    pg_url = f"postgresql+asyncpg://postgres@/openjm?host={host_dir}"

    # OIDC fixture (real JWKS).
    idp = TestIdP()
    jwks = idp.jwks()
    oidc_port = _free_port()
    oidc = http.server.HTTPServer(("127.0.0.1", oidc_port), _oidc_handler_factory(jwks, "/jwks"))
    import threading
    threading.Thread(target=oidc.serve_forever, daemon=True).start()
    jwks_url = f"http://127.0.0.1:{oidc_port}/jwks"

    # HTTPS private model fixture.
    key_file = root / "model.key"
    cert_file = root / "model.crt"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key_file),
         "-out", str(cert_file), "-days", "1", "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    model_port = _free_port()
    model = http.server.HTTPServer(("127.0.0.1", model_port), _model_handler_factory(FIXTURE_MODEL_KEY))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    model.socket = ctx.wrap_socket(model.socket, server_side=True)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    model_base = f"https://127.0.0.1:{model_port}/v1"

    # ---- production configuration -------------------------------------------------
    prod = Settings(
        deployment_profile="production",
        auth_mode="oidc",
        auth_allow_dev_mode=False,
        oidc_issuer="http://127.0.0.1:%d" % oidc_port,
        oidc_audience=idp.audience,
        oidc_jwks_url=jwks_url,
        oidc_client_id="openjm",
        oidc_client_secret="real-oidc-client-secret",
        oidc_redirect_uri="https://openjm.example.com/auth/callback",
        database_url=pg_url,
        credential_encryption_key=__import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode(),
        cors_origins="https://openjm.example.com",
        trusted_hosts="openjm.example.com,127.0.0.1",
        trust_proxy_headers=True,
        rate_limit_enabled=True,
        model_provider_mode="private_remote",
        model_base_url=model_base,
        model_name="openjm-private-8b",
        model_api_key=FIXTURE_MODEL_KEY,
        model_provider_fallback="none",
        metrics_enabled=True,
    )
    report = validate_configuration(prod)
    results["production_preflight_ok"] = report.ok
    if not report.ok:
        results["preflight_errors"] = [i.setting for i in report.errors]
        _cleanup(oidc, model, srv)
        _emit(out_path, results)
        return results

    # ---- boot the real app --------------------------------------------------------
    env = {**os.environ, "SSL_CERT_FILE": str(cert_file), "PYTHONPATH": str(BACKEND),
           "PYTHONHOME": "", "OPENJM_DEPLOYMENT_PROFILE": "production",
           "OPENJM_AUTH_MODE": "oidc", "OPENJM_AUTH_ALLOW_DEV_MODE": "false",
           "OPENJM_OIDC_ISSUER": prod.oidc_issuer, "OPENJM_OIDC_AUDIENCE": idp.audience,
           "OPENJM_OIDC_JWKS_URL": jwks_url, "OPENJM_OIDC_CLIENT_ID": "openjm",
           "OPENJM_OIDC_CLIENT_SECRET": "real-oidc-client-secret",
           "OPENJM_OIDC_REDIRECT_URI": prod.oidc_redirect_uri,
           "OPENJM_DATABASE_URL": pg_url,
           "OPENJM_CREDENTIAL_ENCRYPTION_KEY": prod.credential_encryption_key,
           "OPENJM_CORS_ORIGINS": "https://openjm.example.com",
           "OPENJM_TRUSTED_HOSTS": "openjm.example.com,127.0.0.1",
           "OPENJM_TRUST_PROXY_HEADERS": "true", "OPENJM_RATE_LIMIT_ENABLED": "true",
           "OPENJM_MODEL_PROVIDER_MODE": "private_remote",
           "OPENJM_MODEL_BASE_URL": model_base, "OPENJM_MODEL_NAME": "openjm-private-8b",
           "OPENJM_MODEL_API_KEY": FIXTURE_MODEL_KEY,
           "OPENJM_MODEL_PROVIDER_FALLBACK": "none",
           "OPENJM_UPLOAD_DIR": str(root / "uploads"), "OPENJM_VECTOR_PATH": str(root / "vector"),
           "OPENJM_BACKUP_DIR": str(root / "backups")}
    port = _free_port()
    log = open(root / "server.log", "w")
    proc = subprocess.Popen(
        [str(BACKEND / ".venv" / "bin" / "python"), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    started = False
    for _ in range(60):
        if proc.poll() is not None:
            break
        try:
            httpx.get(base + "/api/health", timeout=2)
            started = True
            break
        except Exception:
            time.sleep(1)
    results["production_app_started"] = started
    if not started:
        results["server_log"] = (root / "server.log").read_text()[-2000:]
        proc.terminate()
        _cleanup(oidc, model, srv)
        _emit(out_path, results)
        return results

    try:
        h = httpx.get(base + "/api/health", timeout=5).json()
        results["health_profile"] = h.get("profile")
        # migrations ran on PostgreSQL
        results["postgres_revision_is_head"] = current_revision(pg_url) in script_heads(pg_url)
        # unauthenticated protected request is refused
        results["unauthenticated_denied"] = httpx.get(base + "/api/conversations", timeout=5).status_code == 401
        # dev-auth bearer is NOT accepted in production (no silent dev fallback)
        results["dev_auth_refused"] = httpx.get(
            base + "/api/conversations", headers={"X-OpenJM-Tenant": "tnt-local"}, timeout=5
        ).status_code == 401
        # fixture-issued token authenticates through the live OIDC path. Mint for
        # the provisioned local principal so the request is authorized, and use
        # the configured issuer so the token validates.
        from app.core.tenancy import LEGACY_PRINCIPAL_SUBJECT, LEGACY_TENANT_ID

        token = idp.token(
            LEGACY_PRINCIPAL_SUBJECT, tenant=LEGACY_TENANT_ID, issuer=prod.oidc_issuer
        )
        authed = httpx.get(base + "/api/conversations", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        results["oidc_token_accepted_path_live"] = authed.status_code == 200
        results["oidc_authed_status"] = authed.status_code
        results["oidc_bad_token_refused"] = httpx.get(
            base + "/api/conversations", headers={"Authorization": f"Bearer {idp.foreign_key('x')}"}, timeout=5
        ).status_code == 401
        # private model routing through the backend
        chat = httpx.post(base + "/api/chat", json={"message": "ping", "mode": "chat"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=30)
        results["chat_status"] = chat.status_code
        results["chat_routed_to_private_model"] = (
            chat.status_code == 200 and "PRIVATE-MODEL-ANSWER" in chat.text
        )
        # credential non-exposure across client surfaces
        blob = "".join(
            httpx.get(base + p, timeout=5).text
            for p in ("/api/health", "/api/version", "/api/ready", "/api/config/public", "/api/metrics")
        )
        results["client_surfaces_no_model_key"] = FIXTURE_MODEL_KEY not in blob
        results["client_surfaces_no_provider_url"] = f"127.0.0.1:{model_port}" not in blob
        # readiness reports provider mode/transport without secrets
        ready = httpx.get(base + "/api/ready", timeout=5).json()
        mp = ready.get("components", {}).get("model_provider", {})
        results["ready_provider_mode"] = mp.get("mode")
        results["ready_provider_transport"] = mp.get("transport")
    finally:
        # ---- PostgreSQL backup/restore DR -----------------------------------------
        try:
            from app.ops.backup import create_backup, restore_backup, verify_backup

            def _count(uri: str) -> int:
                with psycopg.connect(uri) as conn:
                    return conn.execute("SELECT count(*) FROM conversations").fetchone()[0]

            def _socket_uri(dbname: str) -> str:
                return f"postgresql://postgres@/{dbname}?host={host_dir}"

            backup = create_backup(settings=prod)
            verification = verify_backup(backup)
            before = _count(_socket_uri("openjm"))
            with psycopg.connect(base_uri) as conn:
                conn.autocommit = True
                conn.execute("CREATE DATABASE openjm_restr")
            restore_backup(
                backup,
                target_database_url=f"postgresql+asyncpg://postgres@/openjm_restr?host={host_dir}",
                target_data_dir=root / "restore" / "uploads",
                force=True,
            )
            after = _count(_socket_uri("openjm_restr"))
            results["pg_backup_verified"] = verification["ok"]
            results["pg_restore_row_count_matches"] = before == after and before > 0
            results["pg_rows"] = before
        except Exception as exc:  # noqa: BLE001
            results["pg_backup_error"] = f"{type(exc).__name__}: {exc}"[:300]
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        _cleanup(oidc, model, srv)

    _emit(out_path, results)
    return results


def importlib_path() -> str:
    import pgserver
    return str(Path(pgserver.__file__).parent / "pginstall" / "bin" / "pg_dump")


def _cleanup(oidc, model, srv):
    try:
        oidc.shutdown()
    except Exception:
        pass
    try:
        model.shutdown()
    except Exception:
        pass
    try:
        srv.cleanup()
    except Exception:
        pass


def _emit(out_path, results):
    if out_path:
        Path(out_path).write_text(json.dumps(results, indent=2), encoding="utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    results = run(Path(args.out) if args.out else None)
    print(json.dumps(results, indent=2))
    checks = [
        "production_preflight_ok", "production_app_started", "postgres_revision_is_head",
        "unauthenticated_denied", "dev_auth_refused", "oidc_token_accepted_path_live",
        "oidc_bad_token_refused", "chat_routed_to_private_model",
        "client_surfaces_no_model_key", "client_surfaces_no_provider_url",
        "pg_backup_verified", "pg_restore_row_count_matches",
    ]
    ok = all(results.get(c) is True for c in checks)
    print("PRODUCTION ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
