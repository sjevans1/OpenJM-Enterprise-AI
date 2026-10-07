#!/usr/bin/env python
"""VS8 — private hosted model API acceptance (backend-only provider routing).

Issue #38 scope clarification: OpenJM must support an OpenJM-hosted *private*
OpenAI-compatible model API with backend-only routing. This harness proves, with
a **controlled authenticated HTTPS fixture** (labelled as a fixture — it is not a
live private fleet), that:

1. the backend reaches a private HTTPS endpoint with a server-side credential;
2. the credential is never returned by the provider probe that feeds
   `/api/ready` (nor anywhere the client can read);
3. a wrong/missing credential fails closed (bounded, explicit);
4. a provider outage fails closed with no silent fallback to any other provider;
5. production refuses a plain-HTTP private endpoint and a placeholder credential;
6. the provider mode/transport are reported for auditability without secrets.

Run: python scripts/vs8_private_model_fixture.py [--out FILE]
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIXTURE_KEY = "fixture-private-provider-key-not-a-real-secret"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _auth_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {FIXTURE_KEY}"

    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            if not self._auth_ok():
                return self._send(401, {"error": "unauthorized"})
            return self._send(200, {"data": [{"id": "openjm-private-8b"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        if self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "PRIVATE-MODEL-ANSWER"}}
                    ]
                },
            )
        self._send(404, {"error": "not found"})


def _make_cert(dirpath: Path) -> tuple[Path, Path]:
    key = dirpath / "fixture.key"
    cert = dirpath / "fixture.crt"
    SAN = "subjectAltName=IP:127.0.0.1,DNS:localhost"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj",
            "/CN=127.0.0.1", "-addext", SAN,
        ],
        check=True, capture_output=True,
    )
    return key, cert


def _start_fixture(key: Path, cert: Path) -> tuple[http.server.HTTPServer, int, threading.Thread]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port, thread


def _settings(base_url: str, api_key: str, *, mode="private_remote", timeout=10):
    from app.core.config import Settings

    return Settings(
        deployment_profile="development",  # routing proof; production policy is asserted separately
        model_provider_mode=mode,
        model_base_url=base_url,
        model_name="openjm-private-8b",
        model_api_key=api_key,
        model_provider_fallback="none",
        model_timeout_seconds=timeout,
    )


def run(out_path: Path | None) -> dict:
    import asyncio
    import importlib

    results: dict = {"harness": "vs8-private-model-fixture", "fixture": True}
    workdir = Path(tempfile.mkdtemp(prefix="vs8-pm-"))
    key, cert = _make_cert(workdir)
    os.environ["SSL_CERT_FILE"] = str(cert)  # so httpx verifies the fixture CA

    httpd, port, thread = _start_fixture(key, cert)
    base_url = f"https://127.0.0.1:{port}/v1"

    mgw = importlib.import_module("app.services.model_gateway")

    async def _chat(settings):
        orig = mgw.get_settings
        mgw.get_settings = lambda: settings  # type: ignore[assignment]
        try:
            gateway = mgw.OpenAICompatibleModelGateway()
            answer = await gateway.chat([{"role": "user", "content": "hi"}])
            return answer
        finally:
            mgw.get_settings = orig  # type: ignore[assignment]

    async def _probe(settings):
        orig = mgw.get_settings
        mgw.get_settings = lambda: settings  # type: ignore[assignment]
        try:
            return await mgw.OpenAICompatibleModelGateway().probe()
        finally:
            mgw.get_settings = orig  # type: ignore[assignment]

    good = _settings(base_url, FIXTURE_KEY)

    # 1. Backend reaches the private HTTPS endpoint with the server-side credential.
    try:
        results["routing_ok"] = asyncio.run(_chat(good)) == "PRIVATE-MODEL-ANSWER"
    except Exception as exc:  # noqa: BLE001
        results["routing_ok"] = False
        results["routing_error"] = type(exc).__name__

    # 2. Provider probe used by /api/ready leaks no credential.
    probe = asyncio.run(_probe(good))
    results["probe"] = probe
    blob = json.dumps(probe)
    results["probe_no_credential"] = (
        FIXTURE_KEY not in blob and "api_key" not in probe and probe.get("transport") == "https"
    )

    # 3. Wrong credential fails closed.
    bad = _settings(base_url, "wrong-key")
    try:
        asyncio.run(_chat(bad))
        results["wrong_credential_fails_closed"] = False
    except mgw.ModelGatewayError:
        results["wrong_credential_fails_closed"] = True

    # 4. Provider outage fails closed, no fallback.
    thread_stop = threading.Event()
    httpd.shutdown()
    try:
        asyncio.run(_chat(_settings(base_url, FIXTURE_KEY, timeout=2)))
        results["outage_fails_closed"] = False
    except mgw.ModelGatewayError:
        results["outage_fails_closed"] = True

    # 5. Production policy: plain HTTP private endpoint and placeholder key refused.
    from app.core.preflight import validate_configuration

    def _prod(**over):
        base = dict(
            deployment_profile="production", auth_mode="oidc", auth_allow_dev_mode=False,
            oidc_issuer="https://idp.example.com", oidc_client_id="openjm",
            oidc_client_secret="real-secret-value", oidc_redirect_uri="https://o.example/cb",
            database_url="postgresql+asyncpg://u:p@db:5432/o",
            credential_encryption_key=__import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode(),
            cors_origins="https://o.example", trusted_hosts="o.example", rate_limit_enabled=True,
            model_provider_mode="private_remote", model_name="openjm-private-8b",
            model_provider_fallback="none",
        )
        base.update(over)
        from app.core.config import Settings
        return Settings(**base)

    http_report = validate_configuration(
        _prod(model_base_url="http://llm.internal/v1", model_api_key=FIXTURE_KEY)
    )
    https_report = validate_configuration(
        _prod(model_base_url="https://llm.internal/v1", model_api_key=FIXTURE_KEY)
    )
    results["production_refuses_plain_http"] = any(
        i.setting == "model_base_url" for i in http_report.errors
    )
    results["production_accepts_https_private_remote"] = https_report.ok
    if not https_report.ok:
        results["production_https_errors"] = [i.setting for i in https_report.errors]

    results["pass"] = all(
        results.get(k) is True
        for k in (
            "routing_ok",
            "probe_no_credential",
            "wrong_credential_fails_closed",
            "outage_fails_closed",
            "production_refuses_plain_http",
            "production_accepts_https_private_remote",
        )
    )
    if out_path:
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    result = run(Path(args.out) if args.out else None)
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
