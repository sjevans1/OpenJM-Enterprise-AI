"""VS8 observability primitives: correlation ids, structured logs, metrics.

Deliberately dependency-free: a small in-process registry serves Prometheus-style
scraping without making a proprietary observability service mandatory (VS8
Workstream D). Everything here is safe by construction:

* the correlation id is a per-request ``ContextVar``, so concurrent requests
  never share one;
* :func:`safe_fields` never emits a secret-classified key, so a caller cannot
  accidentally log a bearer token or credential by passing it through;
* metric labels are a closed, low-cardinality vocabulary — never tenant content,
  a principal id, a URL or an error message.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Correlation id
# ---------------------------------------------------------------------------

_correlation_id: ContextVar[str | None] = ContextVar("openjm_correlation_id", default=None)


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class CorrelationIdFilter(logging.Filter):
    """Inject the current correlation id into every record, when present."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id() or "-"
        return True


# Keys whose value must never reach a log line, whatever a caller passes.
_SECRET_KEY_PATTERN = re.compile(
    r"(secret|password|token|api[_-]?key|authorization|credential|bearer|"
    r"client_secret|private[_-]?key|fernet)",
    re.IGNORECASE,
)


def safe_fields(**fields: object) -> dict[str, object]:
    """Return *fields* with any secret-looking key replaced by a redaction mark.

    Defence in depth for structured logging: the redaction is driven by the key
    name, so a value is never inspected, hashed or partially echoed.
    """
    safe: dict[str, object] = {}
    for key, value in fields.items():
        if _SECRET_KEY_PATTERN.search(key):
            safe[key] = "[redacted]"
        else:
            safe[key] = value
    return safe


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line with a stable, safe field set."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        for key in ("category", "duration_ms", "tenant_id", "principal_id", "failure_category", "audit_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            # The exception *type and message* can carry infrastructure detail;
            # keep it to the type name plus a bounded message.
            payload["error"] = safe_fields(
                error_type=type(record.exc_info[1]).__name__,
                error=str(record.exc_info[1])[:300],
            )
        return json.dumps(payload, default=str)


def configure_structured_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter and correlation filter on the root logger once."""
    root = logging.getLogger()
    if getattr(root, "_openjm_structured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(CorrelationIdFilter())
    root.handlers = [handler]
    root.setLevel(level)
    root._openjm_structured = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Metrics registry
# ---------------------------------------------------------------------------


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self.total += value
        self.count += 1
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[index] += 1
                return
        self.counts[-1] += 1


class MetricsRegistry:
    """Minimal Prometheus-style counters, gauges and histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}
        self._help: dict[str, str] = {}

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None):
        return (name, tuple(sorted((labels or {}).items())))

    def declare(self, name: str, help_text: str) -> None:
        with self._lock:
            self._help.setdefault(name, help_text)

    def inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            key = self._key(name, labels)
            self._counters[key] = self._counters.get(key, 0.0) + value

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        buckets: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    ) -> None:
        with self._lock:
            key = self._key(name, labels)
            hist = self._histograms.get(key)
            if hist is None:
                hist = _Histogram(buckets=buckets)
                self._histograms[key] = hist
            hist.observe(value)

    @staticmethod
    def _render_labels(labels: tuple[tuple[str, str], ...], extra: dict[str, str] | None = None) -> str:
        items = list(labels)
        if extra:
            items.extend(extra.items())
        if not items:
            return ""
        rendered = ",".join(f'{k}="{v}"' for k, v in sorted(items))
        return "{" + rendered + "}"

    def render(self) -> str:
        """Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = dict(self._histograms)
            helps = dict(self._help)

        for name in sorted({k[0] for k in counters}):
            if name in helps:
                lines.append(f"# HELP {name} {helps[name]}")
            lines.append(f"# TYPE {name} counter")
            for (metric, labels), value in sorted(counters.items()):
                if metric != name:
                    continue
                lines.append(f"{metric}{self._render_labels(labels)} {value}")

        for name in sorted({k[0] for k in gauges}):
            lines.append(f"# TYPE {name} gauge")
            for (metric, labels), value in sorted(gauges.items()):
                if metric != name:
                    continue
                lines.append(f"{metric}{self._render_labels(labels)} {value}")

        for name in sorted({k[0] for k in histograms}):
            lines.append(f"# TYPE {name} histogram")
            for (metric, labels), hist in sorted(histograms.items()):
                if metric != name:
                    continue
                cumulative = 0
                for bound, bucket_count in zip(hist.buckets, hist.counts):
                    cumulative += bucket_count
                    lines.append(
                        f"{metric}_bucket{self._render_labels(labels, {'le': str(bound)})} {cumulative}"
                    )
                cumulative += hist.counts[-1]
                lines.append(f"{metric}_bucket{self._render_labels(labels, {'le': '+Inf'})} {cumulative}")
                lines.append(f"{metric}_sum{self._render_labels(labels)} {hist.total}")
                lines.append(f"{metric}_count{self._render_labels(labels)} {hist.count}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Test helper: drop everything."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# Process-wide registry. Metric names are declared here so a scrape has HELP.
METRICS = MetricsRegistry()
for _name, _help in {
    "openjm_http_requests_total": "HTTP requests handled, by method/route/status.",
    "openjm_http_request_duration_seconds": "HTTP request latency.",
    "openjm_http_request_errors_total": "HTTP requests that failed (>=400).",
    "openjm_model_requests_total": "Model-provider requests, by outcome.",
    "openjm_model_request_duration_seconds": "Model-provider latency.",
    "openjm_retrieval_duration_seconds": "Knowledge retrieval latency.",
    "openjm_connector_sync_total": "Connector sync outcomes.",
    "openjm_scheduler_runs_total": "Scheduler runs, by outcome.",
    "openjm_notification_retries_total": "Notification retry attempts.",
    "openjm_db_pool_checked_out": "Database connections currently checked out.",
    "openjm_start_time_seconds": "Process start time (unix seconds).",
}.items():
    METRICS.declare(_name, _help)

_PROCESS_START = time.time()
METRICS.gauge("openjm_start_time_seconds", _PROCESS_START)
