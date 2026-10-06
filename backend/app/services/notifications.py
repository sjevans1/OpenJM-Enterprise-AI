"""VS7 notification abstraction.

A notification is a local record that one principal should be told something
about evidence they are already authoritatively allowed to see. Two rules shape
this module:

1. **Registered implementations only.** A channel names a registered channel
   type, never a destination. There is no URL, endpoint, webhook, host or
   destination field anywhere in this abstraction, because a channel with an
   arbitrary destination is an arbitrary network call wearing a different name.
   Only ``in_app`` is registered, and its implementation performs no network
   access at all.

2. **Re-prove before surfacing.** Authorization can change between the moment a
   notification is queued and the moment it would be shown. Every delivery
   attempt therefore re-proves that the recipient may still access the
   referenced evidence, through one shared rule
   (:func:`notification_evidence_authorized`). A refusal suppresses the
   notification permanently rather than delivering stale content.

   This applies to *every* attempt, not only the first. A retry after a
   transient failure is a fresh opportunity for the recipient's access to have
   been withdrawn in the meantime, so retries re-prove access exactly as an
   initial delivery does. A retry that skipped the check would turn a transient
   failure into a way to deliver evidence the recipient has since lost.

Every state-changing operation appends an audit record through
:func:`~app.services.identity.record_audit` with an action prefixed
``notification.``. The caller commits; nothing here commits on its own.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationChannel
from app.services.identity import record_audit, utcnow

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NotificationError(Exception):
    """A notification operation was refused or failed.

    ``code`` is a stable, safe category suitable for audit and API responses.
    """

    def __init__(self, message: str, *, code: str = "notification_error"):
        super().__init__(message)
        self.code = code


class ChannelUnsupportedError(NotificationError):
    """The named channel type has no registered implementation."""

    def __init__(self, message: str, *, code: str = "channel_type_unregistered"):
        super().__init__(message, code=code)


class ChannelDisabledError(NotificationError):
    """The channel is missing, belongs to another tenant, or is disabled."""

    def __init__(self, message: str, *, code: str = "channel_disabled"):
        super().__init__(message, code=code)


class NotificationSuppressedError(NotificationError):
    """Delivery was refused because the recipient lost access to the evidence."""

    def __init__(self, message: str, *, code: str = "evidence_not_authorized"):
        super().__init__(message, code=code)


# ---------------------------------------------------------------------------
# Channel implementations (explicit registry, no auto-discovery)
# ---------------------------------------------------------------------------


class InAppChannel:
    """The only registered channel type. Stores the notification locally.

    Delivery is a local state change on the channel row. This implementation
    makes no outbound call of any kind, which is the whole point of having a
    closed registry instead of a configurable destination.
    """

    type_id = "in_app"
    network_access = False

    async def deliver(self, *, notification: Notification, channel: NotificationChannel) -> None:
        channel.last_delivery_at = utcnow()
        channel.last_failure_category = None


NOTIFICATION_CHANNEL_TYPES: dict[str, InAppChannel] = {
    "in_app": InAppChannel(),
}

# Config keys that would smuggle a destination into a channel config. A row
# carrying any of these is refused at creation, so the abstraction cannot be
# quietly turned into network access later.
_FORBIDDEN_CONFIG_KEYS: tuple[str, ...] = (
    "url",
    "endpoint",
    "webhook",
    "host",
    "destination",
)


def registered_channel_types() -> tuple[str, ...]:
    """The channel types with a registered implementation, sorted."""
    return tuple(sorted(NOTIFICATION_CHANNEL_TYPES))


def channel_type_supported(channel_type: str) -> bool:
    return channel_type in NOTIFICATION_CHANNEL_TYPES


# ---------------------------------------------------------------------------
# Channel lifecycle
# ---------------------------------------------------------------------------


def _refuse_destination_config(config: dict | None) -> None:
    for key in config or {}:
        if str(key).strip().lower() in _FORBIDDEN_CONFIG_KEYS:
            raise NotificationError(
                f"config key '{key}' names a destination; channels are registered "
                "implementations, not arbitrary endpoints",
                code="arbitrary_destination_refused",
            )


async def create_channel(
    db: AsyncSession,
    *,
    tenant_id: str,
    actor: str,
    name: str,
    channel_type: str,
    config: dict | None = None,
) -> NotificationChannel:
    """Create a notification channel for one tenant.

    Refuses an unregistered channel type and refuses any config key that names
    a destination. The channel starts active and enabled, because enabling is
    only meaningful for a registered type that already passed those checks.
    """
    if not channel_type_supported(channel_type):
        raise ChannelUnsupportedError(
            f"no implementation is registered for channel type '{channel_type}'"
        )
    _refuse_destination_config(config)

    channel = NotificationChannel(
        tenant_id=tenant_id,
        name=name,
        channel_type=channel_type,
        config_json=json.dumps(config or {}) if config else None,
        status="active",
        enabled=True,
    )
    db.add(channel)
    await db.flush()

    await record_audit(
        db,
        principal=None,
        tenant_id=tenant_id,
        action="notification.channel.create",
        decision="allow",
        resource_type="notification_channel",
        resource_id=channel.id,
        metadata={"actor": actor, "channel_type": channel_type, "name": name},
    )
    return channel


async def set_channel_enabled(
    db: AsyncSession, *, channel: NotificationChannel, enabled: bool, actor: str
) -> NotificationChannel:
    """Enable or disable a channel. Disabling is the authorization switch."""
    channel.enabled = bool(enabled)
    channel.status = "active" if enabled else "disabled"
    channel.updated_at = utcnow()

    await record_audit(
        db,
        principal=None,
        tenant_id=channel.tenant_id,
        action="notification.channel.enable" if enabled else "notification.channel.disable",
        decision="allow",
        resource_type="notification_channel",
        resource_id=channel.id,
        metadata={"actor": actor},
    )
    return channel


async def list_channels(db: AsyncSession, *, tenant_id: str) -> list[NotificationChannel]:
    """Channels of one tenant. A foreign channel is indistinguishable from absent."""
    result = await db.execute(
        select(NotificationChannel)
        .where(NotificationChannel.tenant_id == tenant_id)
        .order_by(NotificationChannel.name)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Enqueue and read
# ---------------------------------------------------------------------------


async def enqueue_notification(
    db: AsyncSession,
    *,
    tenant_id: str,
    principal_id: str,
    category: str,
    subject: str,
    body: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    channel_id: str | None = None,
    max_attempts: int = 3,
) -> Notification:
    """Record a pending notification for one recipient.

    Queuing does not surface anything. The notification stays pending until a
    delivery attempt re-proves access to the referenced evidence.
    """
    notification = Notification(
        tenant_id=tenant_id,
        principal_id=principal_id,
        channel_id=channel_id,
        category=category,
        subject=subject,
        body=body,
        resource_type=resource_type,
        resource_id=resource_id,
        status="pending",
        attempts=0,
        max_attempts=max_attempts,
    )
    db.add(notification)
    await db.flush()

    await record_audit(
        db,
        principal=None,
        tenant_id=tenant_id,
        action="notification.enqueue",
        decision="allow",
        resource_type="notification",
        resource_id=notification.id,
        metadata={
            "principal_id": principal_id,
            "category": category,
            "channel_id": channel_id,
        },
    )
    return notification


async def list_notifications(
    db: AsyncSession, *, tenant_id: str, principal_id: str
) -> list[Notification]:
    """Notifications addressed to one principal of one tenant.

    Scoped by tenant AND recipient, so a notification for another principal of
    the same tenant is never listed.
    """
    result = await db.execute(
        select(Notification)
        .where(
            Notification.tenant_id == tenant_id,
            Notification.principal_id == principal_id,
        )
        .order_by(Notification.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def _resolve_channel(
    db: AsyncSession, *, tenant_id: str, channel_id: str | None
) -> NotificationChannel | None:
    """Resolve a channel inside one tenant. A foreign channel resolves to None."""
    if not channel_id:
        return None
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.tenant_id == tenant_id,
        )
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Notification authorization
# ---------------------------------------------------------------------------

# Resource types that name something other than protected connector evidence.
# A notification about one of these is operational: it carries no cached
# provider content, so there is no recipient access to lose and delivery does
# not depend on a provider check.
EVIDENCE_FREE_RESOURCE_TYPES = frozenset(
    {
        "report",
        "run",
        "schedule",
        "connector_instance",
        "connector_sync",
        "notification",
        "notification_channel",
        "operation",
        "system",
    }
)


async def notification_evidence_authorized(
    db: AsyncSession, *, notification: Notification
) -> bool:
    """Re-prove the recipient may currently access this notification's evidence.

    This is the single authorization rule for notifications. The API delivery
    path and the scheduler retry path both use it, so a retry can never be a
    weaker check than a first attempt. It reads the persisted notification row,
    so it does not depend on an HTTP caller being present.

    The recipient is always ``notification.principal_id``. Whoever triggered the
    delivery is irrelevant: a scheduler retry must prove the *recipient's*
    access, never the scheduler's.

    Fails closed. A malformed reference, an unknown resource, a revoked mapping,
    a provider outage, a permission timeout, a disabled connector and a revoked
    credential all yield ``False``, because every one of them is an inability to
    prove access rather than evidence of it.
    """
    resource_type = notification.resource_type

    if resource_type is None or resource_type in EVIDENCE_FREE_RESOURCE_TYPES:
        # Operational notification: no protected evidence reference to re-prove.
        return True

    if resource_type != "connector_resource":
        # A protected or unrecognised evidence type that this module cannot
        # revalidate. Assuming access would be the unsafe default, so refuse.
        return False

    from app.services.connectors.authorization import (
        parse_connector_resource_reference,
        require_current_authorization,
    )

    parsed = parse_connector_resource_reference(notification.resource_id or "")
    if parsed is None:
        return False
    connector_id, external_id = parsed
    outcome = await require_current_authorization(
        db,
        tenant_id=notification.tenant_id,
        connector_instance_id=connector_id,
        external_id=external_id,
        principal_id=notification.principal_id,
    )
    return outcome.allowed


async def _attempt_delivery(db: AsyncSession, *, notification: Notification) -> Notification:
    """One bounded delivery attempt against the notification's channel."""
    notification.attempts = int(notification.attempts) + 1
    notification.last_attempt_at = utcnow()
    notification.updated_at = utcnow()

    channel = await _resolve_channel(
        db, tenant_id=notification.tenant_id, channel_id=notification.channel_id
    )
    if channel is None or not channel.enabled or channel.status != "active":
        notification.status = "failed"
        notification.failure_category = "channel_disabled"
        if channel is not None:
            channel.last_failure_category = "channel_disabled"
            channel.updated_at = utcnow()
        await record_audit(
            db,
            principal=None,
            tenant_id=notification.tenant_id,
            action="notification.deliver",
            decision="deny",
            resource_type="notification",
            resource_id=notification.id,
            reason="channel_disabled",
            metadata={"attempts": notification.attempts},
        )
        raise ChannelDisabledError("notification channel is missing or disabled")

    implementation = NOTIFICATION_CHANNEL_TYPES.get(channel.channel_type)
    if implementation is None:
        notification.status = "failed"
        notification.failure_category = "channel_type_unregistered"
        await record_audit(
            db,
            principal=None,
            tenant_id=notification.tenant_id,
            action="notification.deliver",
            decision="deny",
            resource_type="notification",
            resource_id=notification.id,
            reason="channel_type_unregistered",
            metadata={"attempts": notification.attempts},
        )
        raise ChannelUnsupportedError(
            f"no implementation is registered for channel type '{channel.channel_type}'"
        )

    await implementation.deliver(notification=notification, channel=channel)
    notification.status = "delivered"
    notification.delivered_at = utcnow()
    notification.failure_category = None
    notification.updated_at = utcnow()

    await record_audit(
        db,
        principal=None,
        tenant_id=notification.tenant_id,
        action="notification.deliver",
        decision="allow",
        resource_type="notification",
        resource_id=notification.id,
        metadata={"attempts": notification.attempts, "channel_id": channel.id},
    )
    return notification


async def deliver(
    db: AsyncSession, *, notification: Notification, authorization_check=None
) -> Notification:
    """Attempt one delivery, re-proving the recipient's access first.

    Authorization is re-proved on every attempt through
    :func:`notification_evidence_authorized`, which is the shared rule the
    scheduler retry path uses too. ``authorization_check`` exists so a test can
    substitute a check; production callers leave it unset, so the shared rule is
    always the one that runs.

    When the check returns False the notification is suppressed, a
    ``NotificationSuppressedError`` is raised, and the body is never surfaced or
    marked delivered. When the channel is missing or disabled the attempt is
    recorded as failed with ``failure_category='channel_disabled'``. Attempts
    are bounded by ``max_attempts`` and are never exceeded.
    """
    if notification.status == "suppressed":
        # Suppression is a final, deliberate outcome: it is never retried.
        raise NotificationSuppressedError("notification is suppressed and will not be delivered")
    if notification.status == "delivered":
        return notification

    if authorization_check is None:
        authorized = await notification_evidence_authorized(db, notification=notification)
    else:
        authorized = await authorization_check()
    if not authorized:
        notification.status = "suppressed"
        notification.failure_category = "evidence_not_authorized"
        notification.last_attempt_at = utcnow()
        notification.updated_at = utcnow()
        await record_audit(
            db,
            principal=None,
            tenant_id=notification.tenant_id,
            action="notification.suppress",
            decision="deny",
            resource_type="notification",
            resource_id=notification.id,
            reason="evidence_not_authorized",
        )
        raise NotificationSuppressedError(
            "recipient may no longer access the referenced evidence"
        )

    if int(notification.attempts) >= int(notification.max_attempts):
        raise NotificationError(
            "notification has reached max_attempts", code="attempts_exhausted"
        )

    return await _attempt_delivery(db, notification=notification)


async def retry_failed(db: AsyncSession, *, tenant_id: str, limit: int = 50) -> int:
    """Re-attempt failed notifications that still have attempts left.

    Each retry goes through :func:`deliver`, so it re-proves the recipient's
    current access exactly as a first attempt does. A retry is not a cheaper
    check: if the recipient lost access between the initial failure and the
    retry, the notification is suppressed instead of delivered.

    Bounded on both axes: only notifications in ``failed`` status with
    ``attempts`` below ``max_attempts`` are selected, and at most ``limit`` of
    them. Suppressed notifications are never selected, because suppression is a
    final deliberate outcome rather than a transient failure.
    """
    result = await db.execute(
        select(Notification)
        .where(
            Notification.tenant_id == tenant_id,
            Notification.status == "failed",
            Notification.attempts < Notification.max_attempts,
        )
        .order_by(Notification.created_at)
        .limit(limit)
    )
    notifications = list(result.scalars().all())

    attempted = 0
    for notification in notifications:
        try:
            await deliver(db, notification=notification)
        except NotificationError:
            # A refused attempt is already recorded on the notification row.
            pass
        attempted += 1
    return attempted


__all__ = [
    "EVIDENCE_FREE_RESOURCE_TYPES",
    "NOTIFICATION_CHANNEL_TYPES",
    "ChannelDisabledError",
    "ChannelUnsupportedError",
    "InAppChannel",
    "NotificationError",
    "NotificationSuppressedError",
    "channel_type_supported",
    "create_channel",
    "deliver",
    "enqueue_notification",
    "list_channels",
    "list_notifications",
    "notification_evidence_authorized",
    "registered_channel_types",
    "retry_failed",
    "set_channel_enabled",
]
