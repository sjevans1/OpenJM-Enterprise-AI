"""#46 M2 bounded usage aggregation acceptance.

Covers the INF1 "M2 interface agreement": tenant-scoped aggregation over the
immutable M1 ledger, legacy rows grouped as ``legacy_unknown`` and never
dropped, provider-reported and estimated coverage kept separate, retry/fallback
and failure counting, cached/reasoning tokens never folded into the total,
bounded export (refused or clamped), and the platform cross-tenant metadata
surface gated by ``platform:metadata:read`` and free of any customer content.

The tenant-isolation test has a companion mutation test that proves it fails if
the tenant ``WHERE`` predicate is removed; see
``test_removing_tenant_predicate_leaks_other_tenant``.
"""

import csv
import io
import uuid
from datetime import datetime, timezone

import pytest

from app.core.platform import PlatformCapability
from app.core.tenancy import LEGACY_PRINCIPAL_ID, LEGACY_TENANT_ID
from app.models import ModelUsageEvent, Tenant
from app.services import access_governance as governance
from app.services import usage_aggregation as ua

TENANT_A = "tnt-m2-a"
TENANT_B = "tnt-m2-b"

DAY_1 = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
DAY_2 = datetime(2026, 1, 3, 10, 0, tzinfo=timezone.utc)


def _event(
    tenant_id: str,
    *,
    request_id: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int | None = None,
    model_name: str = "model-x",
    provider_route: str = "local",
    usage_source: str = "estimated",
    call_role: str = "primary",
    status: str = "succeeded",
    failure_category: str | None = None,
    cached_input_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    when: datetime = DAY_1,
) -> ModelUsageEvent:
    total = total_tokens if total_tokens is not None else input_tokens + output_tokens
    return ModelUsageEvent(
        tenant_id=tenant_id,
        request_id=request_id,
        provider_route=provider_route,
        model_name=model_name,
        usage_source=usage_source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        call_role=call_role,
        idempotency_key=f"{request_id}:{call_role}:{uuid.uuid4().hex}",
        status=status,
        failure_category=failure_category,
        started_at=when,
        finished_at=when,
    )


async def _seed(maker, events) -> None:
    async with maker() as db:
        db.add_all(events)
        await db.commit()


@pytest.fixture
async def tenants(file_db):
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="m2-a", name="A", status="active"),
                Tenant(id=TENANT_B, slug="m2-b", name="B", status="active"),
            ]
        )
        await db.commit()
    return {}


# ---------------------------------------------------------------------------
# Tenant isolation (and the mutation that proves the guard is load-bearing)
# ---------------------------------------------------------------------------


async def test_aggregation_is_tenant_scoped(tenants, file_db):
    await _seed(
        file_db,
        [
            _event(TENANT_A, request_id="a-1", input_tokens=10, output_tokens=5),
            _event(TENANT_A, request_id="a-2", input_tokens=4, output_tokens=1),
            _event(
                TENANT_B,
                request_id="b-1",
                input_tokens=400,
                output_tokens=599,
                model_name="b-secret-model",
                provider_route="b-secret-route",
            ),
        ],
    )

    async with file_db() as db:
        agg = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")

    assert agg["totals"]["total_tokens"] == 20
    assert agg["totals"]["input_tokens"] == 14
    assert agg["totals"]["output_tokens"] == 6
    assert agg["totals"]["attempts"] == 2

    for bucket in agg["buckets"]:
        assert "b-secret-model" not in bucket["by_model_name"]
        assert "b-secret-route" not in bucket["by_provider_route"]
        # No row identifier from tenant B leaks through any breakdown.
        assert "b-1" not in bucket["by_model_name"]


async def test_tenant_isolation_excludes_other_tenant_export(tenants, file_db):
    await _seed(
        file_db,
        [
            _event(TENANT_A, request_id="a-1", input_tokens=2, output_tokens=2),
            _event(TENANT_B, request_id="b-1", input_tokens=50, output_tokens=50),
        ],
    )
    async with file_db() as db:
        header, rows = await ua.export_rows(db, tenant_id=TENANT_A, period="day", limit=100)
    assert header[0] == "bucket"
    totals = sum(row["total_tokens"] for row in rows)
    assert totals == 4, "tenant A's export must not contain tenant B's tokens"


async def test_removing_tenant_predicate_leaks_other_tenant(tenants, file_db, monkeypatch):
    """Mutation evidence: dropping the SQL tenant predicate leaks B into A.

    This is the RED half of the isolation RED->GREEN pair. With the real
    predicate, tenant A sees only its own 20 tokens. With ``_filters`` mutated to
    return no tenant clause, the same call sees tenant A plus tenant B (620),
    which proves the ``WHERE tenant_id`` guard is what enforces isolation and is
    not incidental.
    """
    await _seed(
        file_db,
        [
            _event(TENANT_A, request_id="a-1", input_tokens=10, output_tokens=5),
            _event(TENANT_A, request_id="a-2", input_tokens=4, output_tokens=1),
            _event(TENANT_B, request_id="b-1", input_tokens=400, output_tokens=200),
        ],
    )
    async with file_db() as db:
        scoped = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")
    assert scoped["totals"]["total_tokens"] == 20

    monkeypatch.setattr(ua, "_filters", lambda tenant_id, start, end: [])
    async with file_db() as db:
        leaked = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")
    assert leaked["totals"]["total_tokens"] == 620, "mutation must expose the leak"


# ---------------------------------------------------------------------------
# Legacy attribution
# ---------------------------------------------------------------------------


async def test_legacy_rows_grouped_as_legacy_unknown_and_not_dropped(tenants, file_db):
    await _seed(
        file_db,
        [_event(TENANT_A, request_id=f"a-{i}", input_tokens=1, output_tokens=1) for i in range(3)],
    )
    async with file_db() as db:
        agg = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")

    assert agg["attribution"] == ua.LEGACY_ATTRIBUTION == "legacy_unknown"
    assert agg["coverage"]["attribution"] == "legacy_unknown"
    assert agg["totals"]["attempts"] == 3, "no unattributed history may be dropped"
    assert all(b["attribution"] == "legacy_unknown" for b in agg["buckets"])


# ---------------------------------------------------------------------------
# Provider-reported vs estimated stay separate
# ---------------------------------------------------------------------------


async def test_provider_reported_and_estimated_are_kept_separate(tenants, file_db):
    await _seed(
        file_db,
        [
            _event(
                TENANT_A,
                request_id="p-1",
                input_tokens=100,
                output_tokens=20,
                usage_source="provider_reported",
            ),
            _event(
                TENANT_A,
                request_id="e-1",
                input_tokens=40,
                output_tokens=10,
                usage_source="estimated",
            ),
        ],
    )
    async with file_db() as db:
        agg = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")

    by_source = agg["coverage"]["by_usage_source"]
    assert by_source["provider_reported"]["total_tokens"] == 120
    assert by_source["estimated"]["total_tokens"] == 50
    # The two coverages are distinct objects, never summed into one
    # provider-grade number.
    assert by_source["provider_reported"] != by_source["estimated"]
    assert by_source["provider_reported"]["total_tokens"] != 170


# ---------------------------------------------------------------------------
# Retry / fallback / failure counting
# ---------------------------------------------------------------------------


async def test_retry_fallback_and_failure_counting(tenants, file_db):
    await _seed(
        file_db,
        [
            _event(TENANT_A, request_id="r-0", call_role="primary", status="succeeded"),
            _event(
                TENANT_A,
                request_id="r-1",
                call_role="retry",
                status="failed",
                failure_category="timeout",
            ),
            _event(
                TENANT_A,
                request_id="r-2",
                call_role="fallback",
                status="failed",
                failure_category="provider_error",
            ),
        ],
    )
    async with file_db() as db:
        agg = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")

    totals = agg["totals"]
    assert totals["attempts"] == 3
    assert totals["succeeded"] == 1
    assert totals["failed"] == 2
    assert totals["retries"] == 1
    assert totals["fallbacks"] == 1
    assert set(totals["by_status"]) == {"succeeded", "failed"}
    assert set(totals["by_failure_category"]) == {"(none)", "timeout", "provider_error"}


# ---------------------------------------------------------------------------
# Cached and reasoning tokens are never folded into the total
# ---------------------------------------------------------------------------


async def test_cached_and_reasoning_tokens_never_double_counted(tenants, file_db):
    await _seed(
        file_db,
        [
            _event(
                TENANT_A,
                request_id="c-1",
                input_tokens=4,
                output_tokens=1,
                total_tokens=5,
                cached_input_tokens=1000,
                reasoning_tokens=2000,
                usage_source="provider_reported",
            ),
        ],
    )
    async with file_db() as db:
        agg = await ua.aggregate_usage(db, tenant_id=TENANT_A, period="total")

    totals = agg["totals"]
    assert totals["total_tokens"] == 5, "cached/reasoning must not inflate the total"
    assert totals["cached_input_tokens"] == 1000
    assert totals["reasoning_tokens"] == 2000
    assert totals["total_tokens"] != 5 + 1000 + 2000


# ---------------------------------------------------------------------------
# Bounded aggregation
# ---------------------------------------------------------------------------


async def test_aggregate_rejects_an_over_large_window(tenants, file_db):
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ua.aggregate_usage(
                db,
                tenant_id=TENANT_A,
                period="day",
                start=datetime(2020, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )


async def test_export_rows_rejects_over_large_limit(tenants, file_db):
    async with file_db() as db:
        with pytest.raises(ValueError):
            await ua.export_rows(
                db, tenant_id=TENANT_A, period="day", limit=ua.MAX_EXPORT_ROWS + 1
            )


# ---------------------------------------------------------------------------
# Client-admin usage endpoint (tenant:admin, tenant-scoped)
# ---------------------------------------------------------------------------


async def test_admin_usage_endpoint_returns_real_tenant_aggregate(client, file_db):
    await _seed(
        file_db,
        [
            _event(LEGACY_TENANT_ID, request_id="l-1", input_tokens=10, output_tokens=5),
            _event(LEGACY_TENANT_ID, request_id="l-2", input_tokens=4, output_tokens=1),
            _event(TENANT_B, request_id="b-1", input_tokens=900, output_tokens=900),
        ],
    )
    response = await client.get("/api/admin/usage", params={"period": "total"})
    assert response.status_code == 200, response.text
    body = response.json()

    # Backward-compatible fields are preserved.
    assert body["tenant_id"] == LEGACY_TENANT_ID
    assert "usage" in body and body["usage"]["total_tokens"] == 20
    assert body["plan"] is None and body["entitlements"] is None
    assert "note" in body

    # New aggregate carries only this tenant's rows.
    assert body["aggregate"]["totals"]["total_tokens"] == 20
    assert body["aggregate"]["attribution"] == "legacy_unknown"
    assert body["aggregate"]["schema_version"] == ua.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Bounded export endpoint
# ---------------------------------------------------------------------------


async def test_export_csv_parses_with_stable_header_and_row_count(client, file_db):
    await _seed(
        file_db,
        [
            _event(LEGACY_TENANT_ID, request_id="d1", input_tokens=1, output_tokens=1, when=DAY_1),
            _event(LEGACY_TENANT_ID, request_id="d2", input_tokens=2, output_tokens=2, when=DAY_2),
        ],
    )
    response = await client.get("/api/admin/usage/export", params={"format": "csv"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")

    reader = list(csv.DictReader(io.StringIO(response.text)))
    assert len(reader) == 2, "one row per day bucket"

    header = response.text.splitlines()[0]
    assert header.split(",") == [
        "bucket",
        "attribution",
        "attempts",
        "succeeded",
        "failed",
        "retries",
        "fallbacks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "provider_reported_total_tokens",
        "estimated_total_tokens",
    ]
    assert {row["bucket"] for row in reader} == {"2026-01-02", "2026-01-03"}


async def test_export_limit_is_clamped(client, file_db):
    await _seed(
        file_db,
        [
            _event(LEGACY_TENANT_ID, request_id="d1", when=DAY_1),
            _event(LEGACY_TENANT_ID, request_id="d2", when=DAY_2),
        ],
    )
    response = await client.get(
        "/api/admin/usage/export", params={"format": "csv", "limit": 1}
    )
    assert response.status_code == 200, response.text
    reader = list(csv.DictReader(io.StringIO(response.text)))
    assert len(reader) == 1, "an over-large row request is clamped to the limit"


async def test_export_refuses_over_large_window(client, file_db):
    response = await client.get(
        "/api/admin/usage/export",
        params={
            "format": "csv",
            "period": "day",
            "start": "2020-01-01T00:00:00Z",
            "end": "2024-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 400, response.text


async def test_export_json_shape(client, file_db):
    await _seed(file_db, [_event(LEGACY_TENANT_ID, request_id="j1", when=DAY_1)])
    response = await client.get("/api/admin/usage/export", params={"format": "json"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == ua.SCHEMA_VERSION
    assert body["attribution"] == "legacy_unknown"
    assert body["header"][0] == "bucket"
    assert body["rows"][0]["bucket"] == "2026-01-02"


# ---------------------------------------------------------------------------
# Platform cross-tenant metadata (capability-gated, aggregate only)
# ---------------------------------------------------------------------------


async def _grant(file_db, *capabilities) -> None:
    async with file_db() as db:
        await governance.grant_platform_operator(
            db, principal_id=LEGACY_PRINCIPAL_ID, capabilities=list(capabilities)
        )
        await db.commit()


async def test_tenant_owner_is_refused_on_platform_usage(client, file_db):
    await _seed(file_db, [_event(TENANT_B, request_id="b-1")])
    response = await client.get("/api/platform/usage/tenants")
    assert response.status_code == 403, response.text


async def test_platform_metadata_operator_reads_aggregate_only(client, file_db):
    await _seed(
        file_db,
        [
            _event(TENANT_A, request_id="a-1", input_tokens=10, output_tokens=5),
            _event(TENANT_B, request_id="b-1", input_tokens=7, output_tokens=3),
        ],
    )
    await _grant(file_db, PlatformCapability.METADATA_READ)

    response = await client.get("/api/platform/usage/tenants")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["schema_version"] == ua.SCHEMA_VERSION
    assert body["attribution"] == "legacy_unknown"
    assert "generated_at" in body
    assert body["total_tenants"] == 2
    tenant_ids = {item["tenant_id"] for item in body["tenants"]}
    assert tenant_ids == {TENANT_A, TENANT_B}

    # Aggregate-only: no content, no per-message identifiers anywhere.
    allowed_keys = {
        "tenant_id",
        "attempts",
        "succeeded",
        "failed",
        "retries",
        "fallbacks",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    }
    for item in body["tenants"]:
        assert set(item) <= allowed_keys

    serialized = response.text
    for forbidden in ("message_id", "conversation_id", "request_id", "a-1", "b-1", "content"):
        assert forbidden not in serialized


async def test_platform_usage_single_tenant_is_metadata_only(client, file_db):
    await _seed(
        file_db,
        [_event(TENANT_A, request_id="a-1", input_tokens=10, output_tokens=5)],
    )
    await _grant(file_db, PlatformCapability.METADATA_READ)
    response = await client.get(f"/api/platform/usage/tenants/{TENANT_A}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == TENANT_A
    assert body["usage"]["total_tokens"] == 15
    assert "message_id" not in response.text
    assert "request_id" not in response.text


async def test_platform_usage_unknown_tenant_is_404(client, file_db):
    await _grant(file_db, PlatformCapability.METADATA_READ)
    response = await client.get("/api/platform/usage/tenants/nope")
    assert response.status_code == 404
