"""Regression: a database outage must never abort the detailed readiness report.

Discovered by ZTK1 (Issue #104) as defect D1: under a real PostgreSQL outage
``GET /api/ready/detail`` returned HTTP 500 because one poisoned transaction
(``PendingRollbackError``) propagated out of the readiness probes instead of being
reported as component state. The readiness service promises that dependency
failures are *reported, never raised*, and that callers always receive a stable
shape.
"""

import asyncio
import json

from sqlalchemy.exc import PendingRollbackError

from app.services import readiness

ALL_COMPONENTS = {
    "database",
    "migrations",
    "storage",
    "knowledge",
    "model_provider",
    "scheduler",
    "connectors",
    "notifications",
}


class PoisonedSession:
    """Stands in for an ``AsyncSession`` whose transaction was invalidated.

    Once the database goes away every statement raises ``PendingRollbackError``
    until the transaction is rolled back, which is exactly the state observed in
    the live defect.
    """

    def __init__(self, fail_from: int = 1) -> None:
        self.fail_from = fail_from
        self.calls = 0
        self.rollbacks = 0

    async def execute(self, *args, **kwargs):  # noqa: D401 - test double
        self.calls += 1
        if self.calls >= self.fail_from:
            raise PendingRollbackError(
                "Can't reconnect until invalid transaction is rolled back"
            )
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


def test_readiness_report_survives_poisoned_transaction():
    """A failed component must not abort the complete report."""
    components = asyncio.run(readiness.readiness_components(PoisonedSession()))

    assert set(components) == ALL_COMPONENTS
    assert components["database"]["status"] in {
        readiness.STATUS_ERROR,
        readiness.STATUS_DEGRADED,
    }
    # The report is complete, so readiness is decided by component state, not by
    # an exception escaping.
    assert readiness.overall_ready(components) is False


def test_db_dependent_components_are_bounded_not_raised():
    """Every DB-dependent component reports a bounded status."""
    components = asyncio.run(readiness.readiness_components(PoisonedSession()))
    for name in ("database", "migrations", "scheduler", "connectors", "notifications"):
        assert components[name]["status"] in {
            readiness.STATUS_OK,
            readiness.STATUS_WARNING,
            readiness.STATUS_DEGRADED,
            readiness.STATUS_ERROR,
            readiness.STATUS_DISABLED,
        }


def test_poisoned_transaction_is_recovered_for_later_probes():
    """The session is reset so a later probe is not poisoned by an earlier one."""
    db = PoisonedSession(fail_from=1)
    asyncio.run(readiness.readiness_components(db))
    assert db.rollbacks >= 1


def test_readiness_report_is_secret_safe():
    """No connection string, password or traceback may enter the response."""
    components = asyncio.run(readiness.readiness_components(PoisonedSession()))
    blob = json.dumps(components)
    for needle in ("postgresql", "://", "password", "Traceback", "asyncpg"):
        assert needle.lower() not in blob.lower()
