"""VS8 production hardening middleware.

Four independent concerns, each bounded and fail-closed:

* :class:`CorrelationIdMiddleware` — one request id per request (honouring an
  inbound ``X-Correlation-ID`` only up to a safe length), request metrics and
  the response security headers.
* :class:`BodySizeLimitMiddleware` — reject an over-size request with 413 before
  the body is read. File uploads use the (larger) upload bound; other requests
  use the API body bound.
* :class:`RateLimitMiddleware` — a bounded, in-process fixed-window limiter for
  expensive mutations, keyed by client host and path family. Never keyed by, or
  reporting, tenant content.
* a generic exception handler that logs a safe category and returns a stable
  body — no stack trace, no infrastructure detail.

None of these can weaken the authorization layer: they sit in front of it and
only ever refuse (they never grant).
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.observability import (
    METRICS,
    get_correlation_id,
    set_correlation_id,
)

settings = get_settings()

# Header families the app sets on every response. Kept conservative: a strict
# CSP is not set because the SPA is served from a separate origin in dev.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

_EXPENSIVE_PREFIXES = (
    "/api/chat",
    "/api/knowledge",
    "/api/reports",
    "/api/report",
    "/api/connectors",
    "/api/data",
    "/api/actions",
)

_MAX_CORRELATION_ID_LEN = 64


def _category(path: str) -> str:
    """Collapse a path to a low-cardinality family for metrics/limits."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    return path or "/"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        inbound = request.headers.get("X-Correlation-ID", "").strip()
        correlation_id = (
            inbound[:_MAX_CORRELATION_ID_LEN] if inbound else str(uuid.uuid4())
        )
        set_correlation_id(correlation_id)
        started = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - started
        family = _category(request.url.path)
        status_code = response.status_code
        METRICS.inc(
            "openjm_http_requests_total",
            labels={"method": request.method, "route": family, "status": str(status_code)},
        )
        METRICS.histogram(
            "openjm_http_request_duration_seconds",
            duration,
            {"route": family},
        )
        if status_code >= 400:
            METRICS.inc(
                "openjm_http_request_errors_total",
                labels={"route": family, "status": str(status_code)},
            )
        response.headers["X-Correlation-ID"] = correlation_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if settings.security_headers_enabled:
            for key, value in _SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
        # The correlation id is set on the way out; re-assert here so it is
        # present even when CorrelationIdMiddleware is not installed.
        correlation_id = get_correlation_id()
        if correlation_id:
            response.headers.setdefault("X-Correlation-ID", correlation_id)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            content_type = request.headers.get("content-type", "")
            limit = (
                settings.max_upload_bytes
                if content_type.startswith("multipart/form-data")
                else settings.max_request_body_bytes
            )
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > limit:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "request body exceeds the configured limit"},
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=400, content={"detail": "invalid Content-Length"}
                    )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter for expensive mutations. Bounded and best-effort."""

    _WINDOW_SECONDS = 60.0
    _MAX_KEYS = 4096

    def __init__(self, app):  # noqa: D401 - Starlette signature
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_key(self, request: Request) -> str:
        host = request.client.host if request.client else "unknown"
        return host

    async def dispatch(self, request: Request, call_next):
        if (
            not settings.rate_limit_enabled
            or request.method not in {"POST", "PUT", "PATCH"}
            or not request.url.path.startswith(_EXPENSIVE_PREFIXES)
        ):
            return await call_next(request)

        now = time.monotonic()
        key = self._client_key(request)
        limit = max(1, int(settings.rate_limit_expensive_per_minute))
        window = self._hits[key]
        while window and now - window[0] > self._WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded for this endpoint family"},
                headers={"Retry-After": str(int(self._WINDOW_SECONDS))},
            )
        window.append(now)
        if len(self._hits) > self._MAX_KEYS:
            # Drop the oldest key to keep memory bounded under churn.
            oldest = min(self._hits, key=lambda k: self._hits[k][0] if self._hits[k] else now)
            self._hits.pop(oldest, None)
        return await call_next(request)


async def safe_exception_handler(request: Request, exc: Exception) -> Response:
    """Return a stable error body; never leak a stack trace or infra detail."""
    import logging

    logger = logging.getLogger("openjm.error")
    logger.error(
        "unhandled request error",
        extra={"category": "unhandled_error", "failure_category": type(exc).__name__},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "correlation_id": get_correlation_id()},
    )
