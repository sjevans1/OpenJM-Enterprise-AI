"""Client administration API (BV3-B).

The business-facing administration plane for a tenant. Everything here requires
the tenant ``tenant:admin`` permission (admin or owner); no platform capability
is accepted, so an OpenJM operator gains nothing here unless the tenant grants
them a membership. Departments, groups, group membership, steward grants and
source/document policy reuse the governance services accepted in BV1-A/BV1-C;
this module adds the membership lifecycle and the safe preferences surface.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.identity import AuthorizationError, Principal
from app.core.permissions import Permission
from app.db import get_db
from app.services import access_governance as governance
from app.services import client_admin
from app.services import usage_aggregation

router = APIRouter(prefix="/admin", tags=["client-admin"])

_ADMIN = require(Permission.TENANT_ADMIN)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MemberOut(BaseModel):
    principal_id: str
    subject: str
    role: str
    status: str
    email: str | None = None
    display_name: str | None = None
    department_ids: list[str] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)


class MemberProvisionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    subject: str = Field(min_length=1, max_length=255)
    role: str = Field(pattern="^(viewer|editor|admin|owner)$")
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=240)
    issuer: str | None = Field(default=None, max_length=255)


class RoleChangeRequest(BaseModel):
    model_config = {"extra": "forbid"}

    role: str = Field(pattern="^(viewer|editor|admin|owner)$")


class DepartmentCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)


class DepartmentOut(BaseModel):
    id: str
    slug: str
    name: str
    status: str


class StatusChangeRequest(BaseModel):
    model_config = {"extra": "forbid"}

    status: str = Field(min_length=1, max_length=32)


class GroupCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    department_id: str | None = None


class GroupOut(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    department_id: str | None = None


class GroupMemberRequest(BaseModel):
    model_config = {"extra": "forbid"}

    principal_id: str = Field(min_length=1, max_length=64)


class StewardGrantRequest(BaseModel):
    model_config = {"extra": "forbid"}

    principal_id: str = Field(min_length=1, max_length=64)
    scope_type: str = Field(pattern="^(tenant|department|group)$")
    scope_id: str = Field(min_length=1, max_length=64)


class StewardOut(BaseModel):
    principal_id: str
    scope_type: str
    scope_id: str
    status: str


class PreferencesOut(BaseModel):
    preferences: dict


class PreferencesUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    preferences: dict = Field(default_factory=dict)


class UsageSummaryOut(BaseModel):
    tenant_id: str
    usage: dict
    aggregate: dict | None = None
    plan: dict | None = None
    entitlements: dict | None = None
    note: str


def _http_for(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail="Invalid administration request")


def _member_out(record) -> MemberOut:
    payload = vars(record)
    payload["department_ids"] = list(payload.get("department_ids") or ())
    payload["group_ids"] = list(payload.get("group_ids") or ())
    return MemberOut(**payload)


async def _guard(db, action):
    """Run a privileged mutation, mapping domain errors onto HTTP."""
    try:
        return await action()
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


# ---------------------------------------------------------------------------
# Users (members)
# ---------------------------------------------------------------------------


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    return [_member_out(row) for row in await client_admin.list_members(db, principal=principal)]


@router.post("/members", response_model=MemberOut)
async def provision_member(
    payload: MemberProvisionRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    record = await _guard(
        db,
        lambda: client_admin.provision_member(
            db,
            principal=principal,
            subject=payload.subject,
            role=payload.role,
            email=payload.email,
            display_name=payload.display_name,
            issuer=payload.issuer,
        ),
    )
    await db.commit()
    return _member_out(record)


@router.patch("/members/{principal_id}", response_model=MemberOut)
async def change_member_role(
    principal_id: str,
    payload: RoleChangeRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    record = await _guard(
        db,
        lambda: client_admin.change_role(
            db, principal=principal, member_principal_id=principal_id, role=payload.role
        ),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.commit()
    return _member_out(record)


@router.post("/members/{principal_id}/revoke", response_model=MemberOut)
async def revoke_member(
    principal_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    return await _set_member_status(db, principal, principal_id, "revoked")


@router.post("/members/{principal_id}/reactivate", response_model=MemberOut)
async def reactivate_member(
    principal_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    return await _set_member_status(db, principal, principal_id, "active")


async def _set_member_status(db, principal, principal_id, status):
    record = await _guard(
        db,
        lambda: client_admin.set_membership_status(
            db, principal=principal, member_principal_id=principal_id, status=status
        ),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Member not found")
    await db.commit()
    return _member_out(record)


@router.post("/members/{principal_id}/sessions/revoke")
async def revoke_member_sessions(
    principal_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    count = await _guard(
        db,
        lambda: client_admin.revoke_member_sessions(
            db, principal=principal, member_principal_id=principal_id
        ),
    )
    await db.commit()
    return {"revoked": count}


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    rows = await governance.list_departments(db, principal=principal)
    return [
        DepartmentOut(id=row.id, slug=row.slug, name=row.name, status=row.status)
        for row in rows
    ]


@router.post("/departments", response_model=DepartmentOut)
async def create_department(
    payload: DepartmentCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    row = await _guard(
        db,
        lambda: governance.create_department(
            db, principal=principal, slug=payload.slug, name=payload.name
        ),
    )
    await db.commit()
    return DepartmentOut(id=row.id, slug=row.slug, name=row.name, status=row.status)


@router.patch("/departments/{department_id}/status", response_model=DepartmentOut)
async def set_department_status(
    department_id: str,
    payload: StatusChangeRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    row = await _guard(
        db,
        lambda: governance.set_department_status(
            db, principal=principal, department_id=department_id, status=payload.status
        ),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Department not found")
    await db.commit()
    return DepartmentOut(id=row.id, slug=row.slug, name=row.name, status=row.status)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


def _group_out(row) -> GroupOut:
    return GroupOut(
        id=row.id,
        slug=row.slug,
        name=row.name,
        status=row.status,
        department_id=getattr(row, "department_id", None),
    )


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    return [_group_out(row) for row in await governance.list_groups(db, principal=principal)]


@router.post("/groups", response_model=GroupOut)
async def create_group(
    payload: GroupCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    row = await _guard(
        db,
        lambda: governance.create_group(
            db,
            principal=principal,
            slug=payload.slug,
            name=payload.name,
            department_id=payload.department_id,
        ),
    )
    await db.commit()
    return _group_out(row)


@router.patch("/groups/{group_id}/status", response_model=GroupOut)
async def set_group_status(
    group_id: str,
    payload: StatusChangeRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    row = await _guard(
        db,
        lambda: governance.set_group_status(
            db, principal=principal, group_id=group_id, status=payload.status
        ),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.commit()
    return _group_out(row)


@router.get("/groups/{group_id}/members")
async def list_group_members(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    rows = await governance.list_group_members(db, principal=principal, group_id=group_id)
    return {"group_id": group_id, "members": [row.principal_id for row in rows]}


@router.post("/groups/{group_id}/members")
async def add_group_member(
    group_id: str,
    payload: GroupMemberRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    row = await _guard(
        db,
        lambda: governance.add_group_member(
            db, principal=principal, group_id=group_id, member_principal_id=payload.principal_id
        ),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.commit()
    return {"group_id": group_id, "principal_id": payload.principal_id, "status": "active"}


@router.delete("/groups/{group_id}/members/{principal_id}")
async def remove_group_member(
    group_id: str,
    principal_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    removed = await _guard(
        db,
        lambda: governance.remove_group_member(
            db, principal=principal, group_id=group_id, member_principal_id=principal_id
        ),
    )
    await db.commit()
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Data stewards
# ---------------------------------------------------------------------------


@router.get("/stewards", response_model=list[StewardOut])
async def list_stewards(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    rows = await governance.list_stewards(db, principal=principal)
    return [
        StewardOut(
            principal_id=row.principal_id,
            scope_type=row.scope_type,
            scope_id=row.scope_id,
            status=row.status,
        )
        for row in rows
    ]


@router.post("/stewards", response_model=StewardOut)
async def grant_steward(
    payload: StewardGrantRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    row = await _guard(
        db,
        lambda: governance.grant_steward(
            db,
            principal=principal,
            steward_principal_id=payload.principal_id,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
        ),
    )
    await db.commit()
    return StewardOut(
        principal_id=row.principal_id,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        status=row.status,
    )


@router.delete("/stewards")
async def revoke_steward(
    principal_id: str = Query(min_length=1),
    scope_type: str = Query(pattern="^(tenant|department|group)$"),
    scope_id: str = Query(min_length=1),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    revoked = await _guard(
        db,
        lambda: governance.revoke_steward(
            db,
            principal=principal,
            steward_principal_id=principal_id,
            scope_type=scope_type,
            scope_id=scope_id,
        ),
    )
    await db.commit()
    return {"revoked": revoked}


# ---------------------------------------------------------------------------
# Data access (classification surfaces)
# ---------------------------------------------------------------------------


@router.get("/data-access")
async def data_access(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    """Governed sources and documents with their classification, for the admin UI."""
    from sqlalchemy import select as _select

    from app.models import DataSource, Document

    sources = (
        await db.execute(
            _select(DataSource).where(DataSource.tenant_id == principal.tenant_id)
        )
    ).scalars().all()
    documents = (
        await db.execute(_select(Document).where(Document.tenant_id == principal.tenant_id))
    ).scalars().all()
    return {
        "sources": [
            {
                "id": row.id,
                "name": row.name,
                "classification": getattr(row, "classification", None),
                "department_id": getattr(row, "department_id", None),
                "tenant_visible": getattr(row, "tenant_visible", None),
            }
            for row in sources
        ],
        "documents": [
            {
                "id": row.id,
                "title": getattr(row, "original_name", None),
                "classification": getattr(row, "classification", None),
            }
            for row in documents
        ],
    }


# ---------------------------------------------------------------------------
# Preferences and usage
# ---------------------------------------------------------------------------


@router.get("/preferences", response_model=PreferencesOut)
async def read_preferences(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    return PreferencesOut(preferences=await client_admin.read_preferences(db, principal=principal))


@router.patch("/preferences", response_model=PreferencesOut)
async def update_preferences(
    payload: PreferencesUpdateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    values = await _guard(
        db,
        lambda: client_admin.update_preferences(
            db, principal=principal, changes=payload.preferences
        ),
    )
    await db.commit()
    return PreferencesOut(preferences=values)


@router.get("/usage", response_model=UsageSummaryOut)
async def usage(
    period: str = Query(default="day", pattern="^(day|month|total)$"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    """Real M2 aggregates for the caller's tenant (requires tenant:admin)."""
    try:
        summary = await client_admin.usage_summary(
            db, principal=principal, period=period, start=start, end=end
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc
    return UsageSummaryOut(**summary)


@router.get("/usage/export")
async def export_usage(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    period: str = Query(default="day", pattern="^(day|month)$"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=usage_aggregation.MAX_EXPORT_ROWS, ge=1),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    """Bounded usage export for the caller's tenant (requires tenant:admin).

    The window is capped at ``MAX_EXPORT_WINDOW_DAYS`` days and the row count at
    ``MAX_EXPORT_ROWS``; an over-large request is refused with HTTP 400. CSV is
    generated in-process and no file is written, and no network call is made.
    """
    try:
        header, rows = await usage_aggregation.export_rows(
            db,
            tenant_id=principal.tenant_id,
            period=period,
            start=start,
            end=end,
            limit=min(limit, usage_aggregation.MAX_EXPORT_ROWS),
        )
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc

    if format == "json":
        return {
            "tenant_id": principal.tenant_id,
            "period": period,
            "schema_version": usage_aggregation.SCHEMA_VERSION,
            "attribution": usage_aggregation.LEGACY_ATTRIBUTION,
            "generated_at": usage_aggregation.utcnow().isoformat(),
            "header": header,
            "rows": rows,
        }

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"X-OpenJM-Schema-Version": usage_aggregation.SCHEMA_VERSION},
    )


@router.get("/entitlements")
async def entitlements_summary(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    """Bounded, tenant-scoped plan, allowance and soft-threshold read surface.

    Requires ``tenant:admin`` and is scoped to the caller's tenant by a SQL
    predicate, so it can never read another tenant's plan, allowance or credits.
    """
    try:
        return await client_admin.entitlement_summary(db, principal=principal)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _http_for(exc) from exc


@router.get("/audit")
async def recent_audit(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(_ADMIN),
):
    """Tenant-scoped governance audit for the administration surface."""
    from sqlalchemy import select as _select

    from app.models import AuditRecord

    rows = (
        await db.execute(
            _select(AuditRecord)
            .where(AuditRecord.tenant_id == principal.tenant_id)
            .order_by(AuditRecord.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "entries": [
            {
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "decision": row.decision,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }
