import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import (
    actions,
    auth,
    chat,
    connectors,
    data,
    knowledge,
    operations,
    report_exports,
    report_definitions,
    report_runs,
    reports,
    system,
)
from app.core.config import get_settings
from app.core.middleware import (
    BodySizeLimitMiddleware,
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    safe_exception_handler,
)
from app.core.observability import configure_structured_logging, METRICS
from app.core.preflight import assert_configuration_or_raise
from app.db import init_db
from app.version import PRODUCT_VERSION

settings = get_settings()
logger = logging.getLogger("openjm")


async def _scheduler_loop(stop: asyncio.Event) -> None:
    """Bounded in-process scheduler tick. Opt-in via OPENJM_SCHEDULER_ENABLED."""
    from app.db import SessionLocal
    from app.services import scheduler

    while not stop.is_set():
        try:
            async with SessionLocal() as db:
                ran = await scheduler.run_due(db, limit=settings.scheduler_tick_limit)
            METRICS.gauge("openjm_scheduler_last_tick_timestamp", _now())
            METRICS.inc("openjm_scheduler_runs_total", labels={"outcome": "ok"}, value=ran)
            if ran:
                logger.info("scheduler tick", extra={"category": "scheduler", "runs": ran})
        except Exception as exc:  # noqa: BLE001 - a tick must never kill the loop
            METRICS.inc("openjm_scheduler_runs_total", labels={"outcome": "error"})
            logger.warning(
                "scheduler tick failed",
                extra={"category": "scheduler", "failure_category": type(exc).__name__},
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_tick_seconds)


def _now() -> float:
    import time

    return time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_structured_logging()
    # Fail closed before serving: an unsafe production configuration refuses to
    # start. Development is validated structurally and only logs warnings.
    report = assert_configuration_or_raise(settings)
    for warning in report.warnings:
        logger.warning(str(warning), extra={"category": "config"})

    await init_db()
    # Register the connector types this deployment knows about. Registration is
    # explicit and happens once at start-up: an importable package cannot add
    # network access by being present.
    from app.services.actions.builtin import registry as tool_registry  # noqa: F401
    from app.services.connectors.workspace import register_workspace_connector

    register_workspace_connector()

    stop = asyncio.Event()
    task: asyncio.Task | None = None
    if settings.scheduler_enabled:
        task = asyncio.create_task(_scheduler_loop(stop))
    try:
        yield
    finally:
        if task is not None:
            stop.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=5)


app = FastAPI(
    title=settings.product_name,
    version=PRODUCT_VERSION,
    lifespan=lifespan,
)

app.add_exception_handler(Exception, safe_exception_handler)

# Middleware runs outside-in in reverse registration order; CorrelationId is
# registered last so it is outermost and its id is available to every other
# layer and to the security-header middleware.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
if settings.trusted_host_list and "*" not in settings.trusted_host_list:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CorrelationIdMiddleware)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "product": settings.product_name,
        "version": PRODUCT_VERSION,
        "knowledge_engine": "DB-GPT" if settings.knowledge_enabled else "disabled",
        "model": settings.model_name,
        "profile": settings.deployment_profile,
    }


app.include_router(system.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(connectors.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(data.router, prefix="/api")

app.include_router(reports.router, prefix="/api")
app.include_router(report_definitions.router, prefix="/api")
app.include_router(report_runs.router, prefix="/api")
app.include_router(report_exports.router, prefix="/api")
