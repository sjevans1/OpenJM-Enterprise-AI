"""The editable admin policy must round-trip grants without widening access."""
import json

import pytest
from sqlalchemy import select

from app.api.admin import data_access
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import permissions_for_role
from app.models import AuditRecord, DataSource, Document, Tenant
from app.services import access_governance as governance
from app.services import identity as identity_service


def actor(tenant_id="tnt-local", role="owner"):
    return Principal(principal_id="policy-admin", tenant_id=tenant_id, subject="policy-admin", role=role,
                     membership_id="policy-membership", auth_method="local-dev", permissions=permissions_for_role(role))


@pytest.fixture
async def policy_world(file_db):
    owner = actor()
    async with file_db() as db:
        await identity_service.get_or_create_principal(db, subject=owner.subject, principal_id=owner.principal_id)
        await identity_service.add_membership(db, tenant_id=owner.tenant_id, principal_id=owner.principal_id, role="owner")
        department = await governance.create_department(db, principal=owner, slug="policy-hr", name="HR")
        group = await governance.create_group(db, principal=owner, slug="policy-team", name="HR Team", department_id=department.id)
        policy = dict(tenant_id=owner.tenant_id, user_id=owner.user_id, classification="confidential", department_id=department.id,
                      tenant_visible=False, allowed_group_ids_json=json.dumps([group.id]))
        db.add(Document(id="policy-doc", original_name="HR Policy.md", stored_path="/tmp/unused-policy.md", size_bytes=10, **policy))
        db.add(DataSource(id="policy-source", name="HR database", engine="sqlite", connection_secret="never-return-this", **policy))
        db.add(Tenant(id="foreign-policy", slug="foreign-policy", name="Other company", status="active"))
        await db.flush()
        db.add(Document(id="foreign-document", tenant_id="foreign-policy", user_id="foreign-user", original_name="Secret.md", stored_path="/tmp/foreign.md", size_bytes=10))
        await db.commit()
    return owner, department.id, group.id


async def test_admin_catalog_returns_complete_policy_without_foreign_content_or_secrets(file_db, policy_world):
    owner, department, group = policy_world
    async with file_db() as db:
        result = await data_access(db=db, principal=owner)
    source = next(row for row in result["sources"] if row["id"] == "policy-source")
    document = next(row for row in result["documents"] if row["id"] == "policy-doc")
    for row in (source, document):
        assert row["classification"] == "confidential"
        assert row["department_id"] == department
        assert row["allowed_group_ids"] == [group]
        assert row["tenant_visible"] is False
    assert "foreign-document" not in json.dumps(result)
    assert "never-return-this" not in json.dumps(result)
    assert "stored_path" not in json.dumps(result)


@pytest.mark.parametrize("kind", ["document", "source"])
async def test_policy_clear_department_preserves_groups_and_is_audited(file_db, policy_world, kind):
    owner, department, group = policy_world
    func = governance.set_document_policy if kind == "document" else governance.set_data_source_policy
    key = "document_id" if kind == "document" else "source_id"
    resource_id = "policy-doc" if kind == "document" else "policy-source"
    async with file_db() as db:
        # Existing None/omitted contract is unchanged.
        row = await func(db, principal=owner, **{key: resource_id}, classification="confidential", department_id=None)
        assert row.department_id == department
        row = await func(db, principal=owner, **{key: resource_id}, classification="highly_restricted", clear_department=True)
        await db.commit()
        assert row.department_id is None
        assert json.loads(row.allowed_group_ids_json) == [group]
        assert row.tenant_visible is False
        audits = (await db.execute(select(AuditRecord).where(AuditRecord.resource_id == resource_id))).scalars().all()
        assert len(audits) == 2
    async with file_db() as db:
        with pytest.raises(ValueError, match="not both"):
            await func(db, principal=owner, **{key: resource_id}, classification="internal", department_id=department, clear_department=True)


@pytest.mark.parametrize("kind", ["document", "source"])
async def test_clear_policy_does_not_bypass_authority_or_tenant_boundary(file_db, policy_world, kind):
    owner, _, _ = policy_world
    func = governance.set_document_policy if kind == "document" else governance.set_data_source_policy
    key = "document_id" if kind == "document" else "source_id"
    resource_id = "policy-doc" if kind == "document" else "policy-source"
    async with file_db() as db:
        with pytest.raises(AuthorizationError, match="authority"):
            await func(db, principal=actor(role="editor"), **{key: resource_id}, classification="internal", clear_department=True)
    async with file_db() as db:
        assert await func(db, principal=actor(tenant_id="foreign-policy"), **{key: resource_id}, classification="internal", clear_department=True) is None
