"""Schedules and notifications API.

Both surfaces are tenant-scoped and permission-guarded. Neither can be used to
reach an arbitrary destination: a schedule may only name a registered operation,
and a notification channel may only be of a registered channel type.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require
from app.core.identity import Permission, Principal
from app.db import get_db
from app.models import Schedule
from app.services import notifications as notifications_service
from app.services import scheduler
from app.services.notifications import NotificationError

router = APIRouter(prefix="/operations", tags=["operations"])


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    schedule_type: str = Field(min_length=1, max_length=32)
    operation: str = Field(min_length=1, max_length=120)
    interval_seconds: int = Field(ge=60)
    timezone_name: str = Field(default="UTC", max_length=64)
    misfire_policy: str = Field(default="skip", max_length=16)
    max_retries: int = Field(default=2, ge=0, le=10)
    target: dict | None = None


class ScheduleStateUpdate(BaseModel):
    enabled: bool | None = None
    status: str | None = Field(default=None, max_length=32)


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    channel_type: str = Field(min_length=1, max_length=64)
    config: dict | None = None


class NotificationCreate(BaseModel):
    principal_id: str = Field(min_length=1, max_length=64)
    category: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=8000)
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=128)
    channel_id: str | None = Field(default=None, max_length=64)


def _schedule_out(schedule: Schedule) -> dict:
    return {
        "id": schedule.id,
        "name": schedule.name,
        "schedule_type": schedule.schedule_type,
        "operation": schedule.operation,
        "status": schedule.status,
        "enabled": schedule.enabled,
        "timezone": schedule.timezone,
        "interval_seconds": schedule.interval_seconds,
        "next_run_at": schedule.next_run_at,
        "last_run_at": schedule.last_run_at,
        "last_result": schedule.last_result,
        "last_failure_category": schedule.last_failure_category,
        "misfire_policy": schedule.misfire_policy,
        "max_retries": schedule.max_retries,
    }


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@router.get("/schedules/operations")
async def list_registered_operations(
    principal: Principal = Depends(require(Permission.SCHEDULES_READ)),
) -> dict:
    """The constrained operation vocabulary a schedule may target."""
    return {"operations": list(scheduler.registered_operations())}


@router.get("/schedules")
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.SCHEDULES_READ)),
) -> dict:
    schedules = await scheduler.list_schedules(db, tenant_id=principal.tenant_id)
    return {"schedules": [_schedule_out(item) for item in schedules]}


@router.post("/schedules", status_code=201)
async def create_schedule(
    payload: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.SCHEDULES_WRITE)),
) -> dict:
    try:
        schedule = await scheduler.create_schedule(
            db,
            tenant_id=principal.tenant_id,
            owner_principal_id=principal.principal_id,
            name=payload.name,
            schedule_type=payload.schedule_type,
            operation=payload.operation,
            interval_seconds=payload.interval_seconds,
            timezone_name=payload.timezone_name,
            misfire_policy=payload.misfire_policy,
            max_retries=payload.max_retries,
            target=payload.target,
            actor=principal.principal_id,
        )
    except scheduler.SchedulerError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    await db.commit()
    return _schedule_out(schedule)


@router.post("/schedules/{schedule_id}/state")
async def update_schedule_state(
    schedule_id: str,
    payload: ScheduleStateUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.SCHEDULES_WRITE)),
) -> dict:
    result = await db.execute(
        select(Schedule).where(
            Schedule.id == schedule_id, Schedule.tenant_id == principal.tenant_id
        )
    )
    schedule = result.scalars().first()
    if schedule is None:
        raise HTTPException(
            status_code=404, detail={"code": "schedule_not_found", "message": "schedule not found"}
        )
    try:
        await scheduler.set_schedule_state(
            db,
            schedule=schedule,
            enabled=payload.enabled,
            status=payload.status,
            actor=principal.principal_id,
        )
    except scheduler.SchedulerError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    await db.commit()
    return _schedule_out(schedule)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@router.get("/notification-channels")
async def list_notification_channels(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.NOTIFICATIONS_READ)),
) -> dict:
    channels = await notifications_service.list_channels(db, tenant_id=principal.tenant_id)
    return {
        "channel_types": list(notifications_service.registered_channel_types()),
        "channels": [
            {
                "id": item.id,
                "name": item.name,
                "channel_type": item.channel_type,
                "status": item.status,
                "enabled": item.enabled,
                "last_delivery_at": item.last_delivery_at,
                "last_failure_category": item.last_failure_category,
            }
            for item in channels
        ],
    }


@router.post("/notification-channels", status_code=201)
async def create_notification_channel(
    payload: ChannelCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.NOTIFICATIONS_WRITE)),
) -> dict:
    try:
        channel = await notifications_service.create_channel(
            db,
            tenant_id=principal.tenant_id,
            actor=principal.principal_id,
            name=payload.name,
            channel_type=payload.channel_type,
            config=payload.config,
        )
    except NotificationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    await db.commit()
    return {"id": channel.id, "name": channel.name, "channel_type": channel.channel_type}


@router.get("/notifications")
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.NOTIFICATIONS_READ)),
) -> dict:
    """List the caller's own notifications. Recipient scoping is enforced by the service."""
    items = await notifications_service.list_notifications(
        db, tenant_id=principal.tenant_id, principal_id=principal.principal_id
    )
    return {
        "notifications": [
            {
                "id": item.id,
                "category": item.category,
                "subject": item.subject,
                "status": item.status,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "failure_category": item.failure_category,
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


@router.post("/notifications/{notification_id}/deliver")
async def deliver_notification(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require(Permission.NOTIFICATIONS_WRITE)),
) -> dict:
    """Deliver one notification after re-proving the recipient's access.

    The authorization check re-runs against current state at delivery time. If
    it fails the notification is suppressed rather than delivered, so a
    notification cannot carry evidence the recipient has lost access to.

    The check itself lives in the notification service, not here, so this path
    and the scheduler retry path apply exactly the same rule.
    """
    from app.models import Notification

    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.tenant_id == principal.tenant_id,
        )
    )
    notification = result.scalars().first()
    if notification is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "notification_not_found", "message": "notification not found"},
        )

    # Recipient access is re-proved from current provider state by the shared
    # notification-authorization rule, which the scheduler retry path uses too.
    # That rule reads the persisted notification row, so it authorizes the
    # recipient rather than whoever is making this request.
    try:
        await notifications_service.deliver(db, notification=notification)
    except NotificationError as exc:
        await db.commit()
        return {"delivered": False, "code": exc.code, "status": notification.status}
    await db.commit()
    return {"delivered": True, "status": notification.status}
