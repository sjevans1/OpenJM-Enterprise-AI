"""VS8 Workstream D/H/I: observability endpoints, white-label config, hardening."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import middleware as mw
from app.core.observability import METRICS, safe_fields


async def test_version_endpoint_reports_identity(client) -> None:
    response = await client.get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "profile" in body


async def test_health_endpoint_shape(client) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_ready_endpoint_reports_components_without_secrets(client, monkeypatch) -> None:
    async def _stub_provider():
        return {"status": "ok", "mode": "local", "model": "stub"}

    monkeypatch.setattr("app.api.system._model_provider_component", _stub_provider)
    response = await client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert "ready" in body
    for name in ("database", "migrations", "storage", "model_provider", "scheduler", "connectors"):
        assert name in body["components"]
    # No secret value may appear in a readiness payload.
    text = response.text
    assert "credential_encryption_key" not in text
    assert "model_api_key" not in text


async def test_metrics_endpoint_serves_prometheus_text(client) -> None:
    await client.get("/api/version")
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert "openjm_http_requests_total" in response.text


async def test_public_config_exposes_only_display_metadata(client) -> None:
    response = await client.get("/api/config/public")
    assert response.status_code == 200
    body = response.json()
    assert "product_name" in body
    # The provider endpoint and any credential must never be exposed here.
    for forbidden in ("model_base_url", "model_api_key", "model_api_key_present", "oidc_client_secret"):
        assert forbidden not in body


async def test_security_headers_present(client) -> None:
    response = await client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_correlation_id_is_echoed_when_supplied(client) -> None:
    response = await client.get("/api/health", headers={"X-Correlation-ID": "trace-123"})
    assert response.headers.get("X-Correlation-ID") == "trace-123"


async def test_correlation_id_generated_when_absent(client) -> None:
    response = await client.get("/api/health")
    assert response.headers.get("X-Correlation-ID")


def test_safe_fields_redacts_secret_keys() -> None:
    redacted = safe_fields(
        model_api_key="secret", authorization="Bearer x", tenant_id="t1", note="ok"
    )
    assert redacted["model_api_key"] == "[redacted]"
    assert redacted["authorization"] == "[redacted]"
    assert redacted["tenant_id"] == "t1"
    assert redacted["note"] == "ok"


def _mini_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(mw.BodySizeLimitMiddleware)
    app.add_middleware(mw.RateLimitMiddleware)

    @app.post("/api/chat")
    async def chat():
        return {"ok": True}

    return app


async def test_body_size_limit_returns_413(monkeypatch) -> None:
    monkeypatch.setattr(
        mw, "settings", mw.settings.model_copy(update={"max_request_body_bytes": 10})
    )
    app = _mini_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/chat", content=b"x" * 100)
    assert response.status_code == 413


async def test_rate_limit_returns_429_after_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        mw,
        "settings",
        mw.settings.model_copy(
            update={"rate_limit_enabled": True, "rate_limit_expensive_per_minute": 3}
        ),
    )
    app = _mini_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        codes = [(await http.post("/api/chat", content=b"{}")).status_code for _ in range(6)]
    assert codes.count(200) == 3
    assert 429 in codes


def test_metrics_render_has_help_and_type() -> None:
    METRICS.inc("openjm_http_requests_total", labels={"method": "GET", "route": "/api", "status": "200"})
    rendered = METRICS.render()
    assert "# TYPE openjm_http_requests_total counter" in rendered
