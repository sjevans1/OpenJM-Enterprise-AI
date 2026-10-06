"""VS7 notification abstraction acceptance.

The notification abstraction has no configurable destination, so the properties
worth proving are negative ones: a foreign tenant's channel is unusable, a
foreign tenant's notification is unlisted, an unregistered channel type is
refused, a destination-looking config is refused, a disabled channel refuses
delivery, and a stale authorization suppresses delivery instead of surfacing it.
Retry is bounded and never resurrects a suppressed notification.
"""

import pytest

from app.models import Notification, NotificationChannel, Tenant
from app.services import identity as identity_service
from app.services import notifications

TENANT_A = "tnt-a"
TENANT_B = "tnt-b"
PRINCIPAL_A = "pa"
PRINCIPAL_B = "pb"
PRINCIPAL_C = "pc"


async def _seed(maker) -> None:
    async with maker() as db:
        db.add_all(
            [
                Tenant(id=TENANT_A, slug="tenant-a", name="Tenant A", status="active"),
                Tenant(id=TENANT_B, slug="tenant-b", name="Tenant B", status="active"),
            ]
        )
        await db.flush()
        await identity_service.get_or_create_principal(
            db, subject="sub-a", principal_id=PRINCIPAL_A
        )
        await identity_service.get_or_create_principal(
            db, subject="sub-b", principal_id=PRINCIPAL_B
        )
        await identity_service.get_or_create_principal(
            db, subject="sub-c", principal_id=PRINCIPAL_C
        )
        await db.commit()


async def _allow() -> bool:
    return True


async def _deny() -> bool:
    return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_only_in_app_is_registered():
    assert notifications.registered_channel_types() == ("in_app",)
    assert notifications.channel_type_supported("in_app") is True
    assert notifications.channel_type_supported("email") is False
    assert notifications.channel_type_supported("slack") is False
    assert notifications.channel_type_supported("webhook") is False


async def test_unregistered_channel_type_is_refused(file_db):
    await _seed(file_db)
    async with file_db() as db:
        with pytest.raises(notifications.ChannelUnsupportedError) as exc:
            await notifications.create_channel(
                db,
                tenant_id=TENANT_A,
                actor="ops",
                name="smtp",
                channel_type="email",
            )
        assert exc.value.code == "channel_type_unregistered"
        # Nothing was persisted.
        assert await notifications.list_channels(db, tenant_id=TENANT_A) == []


async def test_destination_config_key_is_refused(file_db):
    await _seed(file_db)
    async with file_db() as db:
        for key in ("url", "endpoint", "webhook", "host", "destination"):
            with pytest.raises(notifications.NotificationError) as exc:
                await notifications.create_channel(
                    db,
                    tenant_id=TENANT_A,
                    actor="ops",
                    name=f"chan-{key}",
                    channel_type="in_app",
                    config={key: "https://attacker.invalid/hook"},
                )
            assert exc.value.code == "arbitrary_destination_refused"
        assert await notifications.list_channels(db, tenant_id=TENANT_A) == []


async def test_non_destination_config_is_allowed(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db,
            tenant_id=TENANT_A,
            actor="ops",
            name="in-app",
            channel_type="in_app",
            config={"display_group": "reports"},
        )
        await db.commit()
        assert channel.channel_type == "in_app"
        assert channel.enabled is True


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_channel_of_another_tenant_is_invisible_and_unusable(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel_b = await notifications.create_channel(
            db, tenant_id=TENANT_B, actor="ops-b", name="b-app", channel_type="in_app"
        )
        await db.commit()
        channel_b_id = channel_b.id

    async with file_db() as db:
        # Tenant A never sees tenant B's channel.
        channels_a = await notifications.list_channels(db, tenant_id=TENANT_A)
        assert [c.id for c in channels_a] == []
        # And tenant B does.
        channels_b = await notifications.list_channels(db, tenant_id=TENANT_B)
        assert [c.id for c in channels_b] == [channel_b_id]

        # A tenant A notification that references tenant B's channel cannot use
        # it: the foreign channel resolves to nothing and delivery is refused.
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="confidential body",
            channel_id=channel_b_id,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.ChannelDisabledError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_allow
            )
        await db.commit()
        assert notification.status == "failed"
        assert notification.failure_category == "channel_disabled"
        assert notification.delivered_at is None
        # Tenant B's channel was not touched by tenant A's attempted delivery.
        foreign = await db.get(NotificationChannel, channel_b_id)
        assert foreign.last_failure_category is None
        assert foreign.last_delivery_at is None


async def test_notification_of_another_tenant_is_not_listed(file_db):
    await _seed(file_db)
    async with file_db() as db:
        await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="A",
            body="a-body",
        )
        await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_B,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="B",
            body="b-body",
        )
        await db.commit()

    async with file_db() as db:
        listed_a = await notifications.list_notifications(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
        )
        assert [n.subject for n in listed_a] == ["A"]
        listed_b = await notifications.list_notifications(
            db, tenant_id=TENANT_B, principal_id=PRINCIPAL_A
        )
        assert [n.subject for n in listed_b] == ["B"]


async def test_notification_is_scoped_to_recipient_within_a_tenant(file_db):
    await _seed(file_db)
    async with file_db() as db:
        await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="for-a",
            body="a-body",
        )
        await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_B,
            category="report_ready",
            subject="for-b",
            body="b-body",
        )
        await db.commit()

    async with file_db() as db:
        listed_a = await notifications.list_notifications(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
        )
        listed_b = await notifications.list_notifications(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_B
        )
        listed_c = await notifications.list_notifications(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_C
        )
        assert [n.subject for n in listed_a] == ["for-a"]
        assert [n.subject for n in listed_b] == ["for-b"]
        assert listed_c == []


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


async def test_disabled_channel_refuses_delivery(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        await notifications.set_channel_enabled(
            db, channel=channel, enabled=False, actor="ops"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=channel.id,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.ChannelDisabledError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_allow
            )
        await db.commit()
        assert notification.status == "failed"
        assert notification.failure_category == "channel_disabled"
        assert notification.attempts == 1
        assert notification.last_attempt_at is not None
        assert notification.delivered_at is None


async def test_missing_channel_refuses_delivery(file_db):
    await _seed(file_db)
    async with file_db() as db:
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=None,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.ChannelDisabledError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_allow
            )
        await db.commit()
        assert notification.failure_category == "channel_disabled"


async def test_failed_authorization_suppresses_and_never_delivers(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="secret evidence body",
            resource_type="document",
            resource_id="doc-1",
            channel_id=channel.id,
        )
        await db.commit()
        notification_id = notification.id
        channel_id = channel.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.NotificationSuppressedError) as exc:
            await notifications.deliver(
                db, notification=notification, authorization_check=_deny
            )
        assert exc.value.code == "evidence_not_authorized"
        await db.commit()
        assert notification.status == "suppressed"
        assert notification.failure_category == "evidence_not_authorized"
        assert notification.delivered_at is None
        # The body was never surfaced: no delivery, no delivered timestamp.
        assert notification.attempts == 0
        # The channel did not record a delivery.
        channel = await db.get(NotificationChannel, channel_id)
        assert channel.last_delivery_at is None


async def test_delivery_succeeds_with_authorization(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=channel.id,
        )
        await db.commit()
        notification_id = notification.id
        channel_id = channel.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        await notifications.deliver(
            db, notification=notification, authorization_check=_allow
        )
        await db.commit()
        assert notification.status == "delivered"
        assert notification.delivered_at is not None
        assert notification.attempts == 1
        assert notification.failure_category is None
        channel = await db.get(NotificationChannel, channel_id)
        assert channel.last_delivery_at is not None


# ---------------------------------------------------------------------------
# Bounded retry
# ---------------------------------------------------------------------------


async def test_retry_is_bounded_and_stops_at_max_attempts(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        await notifications.set_channel_enabled(
            db, channel=channel, enabled=False, actor="ops"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=channel.id,
            max_attempts=3,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.ChannelDisabledError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_allow
            )
        await db.commit()
        assert notification.attempts == 1
        assert notification.status == "failed"

    async with file_db() as db:
        assert await notifications.retry_failed(db, tenant_id=TENANT_A) == 1
        await db.commit()
    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        assert notification.attempts == 2
    async with file_db() as db:
        assert await notifications.retry_failed(db, tenant_id=TENANT_A) == 1
        await db.commit()
    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        assert notification.attempts == 3
        # At max_attempts the bound stops any further retry.
        assert await notifications.retry_failed(db, tenant_id=TENANT_A) == 0
        await db.commit()
    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        assert notification.attempts == 3
        assert notification.status == "failed"
        # A direct delivery is refused once attempts are exhausted.
        with pytest.raises(notifications.NotificationError) as exc:
            await notifications.deliver(
                db, notification=notification, authorization_check=_allow
            )
        assert exc.value.code == "attempts_exhausted"


async def test_retry_respects_the_limit(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        await notifications.set_channel_enabled(
            db, channel=channel, enabled=False, actor="ops"
        )
        for index in range(4):
            await notifications.enqueue_notification(
                db,
                tenant_id=TENANT_A,
                principal_id=PRINCIPAL_A,
                category="report_ready",
                subject=f"n-{index}",
                body="body",
                channel_id=channel.id,
                max_attempts=3,
            )
        await db.commit()

    async with file_db() as db:
        result = await notifications.list_notifications(
            db, tenant_id=TENANT_A, principal_id=PRINCIPAL_A
        )
        for notification in result:
            with pytest.raises(notifications.ChannelDisabledError):
                await notifications.deliver(
                    db, notification=notification, authorization_check=_allow
                )
        await db.commit()

    async with file_db() as db:
        assert await notifications.retry_failed(db, tenant_id=TENANT_A, limit=2) == 2
        await db.commit()


async def test_retry_does_not_resurrect_suppressed_notifications(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=channel.id,
            max_attempts=3,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.NotificationSuppressedError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_deny
            )
        await db.commit()

    async with file_db() as db:
        assert await notifications.retry_failed(db, tenant_id=TENANT_A) == 0
        await db.commit()
    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        assert notification.status == "suppressed"
        assert notification.failure_category == "evidence_not_authorized"
        assert notification.attempts == 0
        assert notification.delivered_at is None


async def test_delivery_of_a_suppressed_notification_is_refused(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=channel.id,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        with pytest.raises(notifications.NotificationSuppressedError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_deny
            )
        await db.commit()

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        # Even with authorization restored, a suppressed notification is final.
        with pytest.raises(notifications.NotificationSuppressedError):
            await notifications.deliver(
                db, notification=notification, authorization_check=_allow
            )
        assert notification.status == "suppressed"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def test_state_changes_write_notification_audit(file_db):
    await _seed(file_db)
    async with file_db() as db:
        channel = await notifications.create_channel(
            db, tenant_id=TENANT_A, actor="ops", name="in-app", channel_type="in_app"
        )
        await notifications.set_channel_enabled(
            db, channel=channel, enabled=False, actor="ops"
        )
        await notifications.set_channel_enabled(
            db, channel=channel, enabled=True, actor="ops"
        )
        notification = await notifications.enqueue_notification(
            db,
            tenant_id=TENANT_A,
            principal_id=PRINCIPAL_A,
            category="report_ready",
            subject="Report ready",
            body="body",
            channel_id=channel.id,
        )
        await db.commit()
        notification_id = notification.id

    async with file_db() as db:
        notification = await db.get(Notification, notification_id)
        await notifications.deliver(
            db, notification=notification, authorization_check=_allow
        )
        await db.commit()

    from sqlalchemy import select

    from app.models import AuditRecord

    async with file_db() as db:
        actions = (
            (await db.execute(select(AuditRecord.action).order_by(AuditRecord.created_at)))
            .scalars()
            .all()
        )
        assert "notification.channel.create" in actions
        assert "notification.channel.disable" in actions
        assert "notification.channel.enable" in actions
        assert "notification.enqueue" in actions
        assert "notification.deliver" in actions
        assert all(action.startswith("notification.") for action in actions)
