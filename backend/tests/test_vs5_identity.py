"""VS5 trusted-identity acceptance.

These tests run the real FastAPI dependency, the real OIDC validator and the
real membership lookup against a real (SQLite) database. Only the identity
provider's network hop is replaced, by a local test IdP that signs genuine
RS256 tokens.
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import PrincipalAccount, Tenant, TenantMembership
from app.services import identity as identity_service
from app.services.oidc import oidc_client
from tests.oidc_testkit import AUDIENCE, ISSUER, TestIdP

settings = get_settings()

TENANT_A = "tnt-acme"
TENANT_B = "tnt-globex"
SUBJECT_A = "user-a"
SUBJECT_B = "user-b"
SUBJECT_ORPHAN = "user-orphan"


@pytest.fixture
def idp():
    return TestIdP()


@pytest.fixture
def oidc_mode(monkeypatch, idp):
    """Switch the deployment to OIDC mode backed by the test IdP."""
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://idp.test.invalid/keys")
    monkeypatch.setattr(settings, "tenant_resolution", "claim")
    monkeypatch.setattr(oidc_client, "settings", settings)

    async def fake_fetch(url):
        return idp.jwks()

    monkeypatch.setattr(oidc_client, "_fetch_json", fake_fetch)
    idp._jwks = type(idp._jwks)({})  # drop any cached keys between tests
    oidc_client._jwks = type(oidc_client._jwks)({})
    oidc_client._discovery = type(oidc_client._discovery)({})
    return idp


@pytest.fixture
async def tenants(file_db):
    """Two tenants, two memberships, one principal with no membership."""
    async with file_db() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="acme", name="Acme", status="active"),
                Tenant(id=TENANT_B, slug="globex", name="Globex", status="active"),
            ]
        )
        await db.flush()
        account_a = await identity_service.get_or_create_principal(
            db, subject=SUBJECT_A, email="a@acme.test", principal_id="pa"
        )
        account_b = await identity_service.get_or_create_principal(
            db, subject=SUBJECT_B, email="b@globex.test", principal_id="pb"
        )
        await identity_service.get_or_create_principal(
            db, subject=SUBJECT_ORPHAN, principal_id="po"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_A, principal_id=account_a.id, role="editor"
        )
        await identity_service.add_membership(
            db, tenant_id=TENANT_B, principal_id=account_b.id, role="owner"
        )
        await db.commit()
    return {"A": "pa", "B": "pb", "orphan": "po"}


def auth(token: str, tenant: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if tenant:
        headers["X-OpenJM-Tenant"] = tenant
    return headers


# ---------------------------------------------------------------------------
# Credential failures — every one must be 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        "anonymous",
        "malformed",
        "garbage",
        "expired",
        "not_yet_valid",
        "unsigned",
        "wrong_issuer",
        "wrong_audience",
        "foreign_key",
        "unknown_kid",
    ],
)
async def test_invalid_credentials_are_denied(client, oidc_mode, tenants, case):
    idp = oidc_mode
    if case == "anonymous":
        response = await client.get("/api/auth/me")
    else:
        token = {
            "malformed": idp.opaque(),
            "garbage": idp.garbage(),
            "expired": idp.expired(SUBJECT_A, tenant="acme"),
            "not_yet_valid": idp.not_yet_valid(SUBJECT_A, tenant="acme"),
            "unsigned": idp.unsigned(SUBJECT_A, tenant="acme"),
            "wrong_issuer": idp.wrong_issuer(SUBJECT_A, tenant="acme"),
            "wrong_audience": idp.wrong_audience(SUBJECT_A, tenant="acme"),
            "foreign_key": idp.foreign_key(SUBJECT_A, tenant="acme"),
            "unknown_kid": idp.unknown_kid(SUBJECT_A, tenant="acme"),
        }[case]
        response = await client.get("/api/auth/me", headers=auth(token))
    assert response.status_code == 401, response.text


async def test_valid_user_in_correct_tenant_succeeds(client, oidc_mode, tenants):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    response = await client.get("/api/auth/me", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == TENANT_A
    assert body["role"] == "editor"
    assert body["principal_id"] == tenants["A"]


async def test_token_for_subject_without_membership_is_denied(
    client, oidc_mode, tenants
):
    token = oidc_mode.token(SUBJECT_ORPHAN, tenant="acme")
    response = await client.get("/api/auth/me", headers=auth(token))
    assert response.status_code == 403, response.text


async def test_same_principal_targeting_unauthorized_tenant_is_denied(
    client, oidc_mode, tenants
):
    """A valid token cannot widen scope by naming another tenant."""
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    response = await client.get(
        "/api/auth/me", headers=auth(token, tenant="globex")
    )
    assert response.status_code == 403, response.text
    # And it cannot silently fall back to the token's own tenant.
    assert "globex" not in response.text


async def test_different_tenant_cannot_read_another_tenants_documents(
    client, oidc_mode, tenants, file_db
):
    from app.models import Document

    async with file_db() as db:
        db.add(
            Document(
                id="doc-a",
                tenant_id=TENANT_A,
                user_id=tenants["A"],
                original_name="acme-secret.txt",
                stored_path="/tmp/does-not-matter",
                size_bytes=10,
                status="ready",
                indexed=True,
                lifecycle_state="ready",
            )
        )
        await db.commit()

    token_b = oidc_mode.token(SUBJECT_B, tenant="globex")
    response = await client.get("/api/knowledge/documents", headers=auth(token_b))
    assert response.status_code == 200
    assert response.json() == [], "tenant B must not see tenant A documents"

    # And a direct delete by id must not reach it either.
    deleted = await client.delete("/api/knowledge/documents/doc-a", headers=auth(token_b))
    assert deleted.status_code == 404

    token_a = oidc_mode.token(SUBJECT_A, tenant="acme")
    own = await client.get("/api/knowledge/documents", headers=auth(token_a))
    assert [d["id"] for d in own.json()] == ["doc-a"]


# ---------------------------------------------------------------------------
# Authorization changes take effect immediately
# ---------------------------------------------------------------------------


async def test_role_downgrade_takes_effect_on_the_next_request(
    client, oidc_mode, tenants, file_db
):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    assert (await client.get("/api/data/sources", headers=auth(token))).status_code == 200
    assert (await client.post("/api/data/sources", headers=auth(token), json={})).status_code in (
        400,
        422,
    )

    async with file_db() as db:
        await identity_service.set_membership_role(
            db, tenant_id=TENANT_A, principal_id=tenants["A"], role="viewer"
        )
        await db.commit()

    # Same token, no re-issue: the downgrade must already apply.
    assert (await client.get("/api/data/sources", headers=auth(token))).status_code == 200
    after = await client.post("/api/data/sources", headers=auth(token), json={})
    assert after.status_code == 403, after.text


async def test_membership_revocation_takes_effect_immediately(
    client, oidc_mode, tenants, file_db
):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    assert (await client.get("/api/auth/me", headers=auth(token))).status_code == 200

    async with file_db() as db:
        await identity_service.revoke_membership(
            db, tenant_id=TENANT_A, principal_id=tenants["A"]
        )
        await db.commit()

    response = await client.get("/api/auth/me", headers=auth(token))
    assert response.status_code == 403, response.text


async def test_account_disable_denies_access(client, oidc_mode, tenants, file_db):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    assert (await client.get("/api/auth/me", headers=auth(token))).status_code == 200
    async with file_db() as db:
        account = await db.get(PrincipalAccount, tenants["A"])
        account.status = "disabled"
        await db.commit()
    assert (await client.get("/api/auth/me", headers=auth(token))).status_code == 403


async def test_tenant_suspension_denies_access(client, oidc_mode, tenants, file_db):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    async with file_db() as db:
        tenant = await db.get(Tenant, TENANT_A)
        tenant.status = "suspended"
        await db.commit()
    assert (await client.get("/api/auth/me", headers=auth(token))).status_code == 403


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def test_session_token_round_trip_and_revocation(client, oidc_mode, tenants):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    exchanged = await client.post("/api/auth/token/exchange", json={"token": token})
    assert exchanged.status_code == 200, exchanged.text
    session = exchanged.json()["token"]

    assert (await client.get("/api/auth/me", headers=auth(session))).status_code == 200
    assert (await client.post("/api/auth/logout", headers=auth(session))).status_code == 200
    assert (await client.get("/api/auth/me", headers=auth(session))).status_code == 401


async def test_revoked_membership_also_voids_a_live_session(
    client, oidc_mode, tenants, file_db
):
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    session = (
        await client.post("/api/auth/token/exchange", json={"token": token})
    ).json()["token"]
    assert (await client.get("/api/auth/me", headers=auth(session))).status_code == 200

    async with file_db() as db:
        await identity_service.revoke_membership(
            db, tenant_id=TENANT_A, principal_id=tenants["A"]
        )
        await db.commit()

    assert (await client.get("/api/auth/me", headers=auth(session))).status_code == 403


async def test_oidc_mode_rejects_a_forged_opaque_token(client, oidc_mode, tenants):
    """Dev identity must not be reachable when the deployment is in OIDC mode."""
    response = await client.get("/api/auth/me", headers=auth("totally-made-up"))
    assert response.status_code == 401


async def test_auth_config_exposes_no_secrets(client):
    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    body = response.json()
    assert "client_secret" not in response.text
    assert "client_secret" not in body


async def test_documents_require_the_permission_not_just_a_valid_token(
    client, oidc_mode, tenants, file_db
):
    async with file_db() as db:
        await identity_service.set_membership_role(
            db, tenant_id=TENANT_A, principal_id=tenants["A"], role="viewer"
        )
        await db.commit()
    token = oidc_mode.token(SUBJECT_A, tenant="acme")
    upload = await client.post(
        "/api/knowledge/documents",
        headers=auth(token),
        files={"file": ("x.txt", b"hello", "text/plain")},
    )
    assert upload.status_code == 403, upload.text
