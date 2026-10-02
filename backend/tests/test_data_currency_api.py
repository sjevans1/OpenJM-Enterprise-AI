"""API coverage for operator-attested data-source currency metadata."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.api.data import _safe_error
from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models import DataSource


@pytest.fixture
async def client(session):
    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_currency_attestation_is_owned_bounded_and_clearable(client, session):
    owner = get_settings().dev_user_id
    owned = DataSource(
        id="owned-source",
        user_id=owner,
        name="Owned",
        engine="sqlite",
        connection_secret="encrypted-secret",
        status="connected",
        enabled=True,
    )
    foreign = DataSource(
        id="foreign-source",
        user_id="another-user",
        name="Foreign",
        engine="sqlite",
        connection_secret="foreign-secret",
        revenue_currency="JMD",
        status="connected",
        enabled=True,
    )
    session.add_all([owned, foreign])
    await session.commit()

    updated = await client.patch(
        "/api/data/sources/owned-source/currency",
        json={"revenue_currency": "USD"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["revenue_currency"] == "USD"
    assert "connection_secret" not in body
    assert "connection_uri" not in body

    invalid = await client.patch(
        "/api/data/sources/owned-source/currency",
        json={"revenue_currency": "EUR"},
    )
    assert invalid.status_code == 422
    await session.refresh(owned)
    assert owned.revenue_currency == "USD"

    forbidden = await client.patch(
        "/api/data/sources/foreign-source/currency",
        json={"revenue_currency": "USD"},
    )
    assert forbidden.status_code == 404
    await session.refresh(foreign)
    assert foreign.revenue_currency == "JMD"

    cleared = await client.patch(
        "/api/data/sources/owned-source/currency",
        json={"revenue_currency": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["revenue_currency"] is None


def test_public_data_source_error_never_contains_driver_or_credential_text():
    secret = "postgresql://admin:super-secret@private-db.internal/acme"
    driver_error = RuntimeError(
        f"authentication failed for admin at private-db.internal using {secret}"
    )
    public = _safe_error(driver_error, secret)
    assert public == "Data source configuration or connection failed"
    assert "admin" not in public
    assert "private-db" not in public
    assert "super-secret" not in public
