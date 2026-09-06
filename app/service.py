"""Applying a consumed event to durable deliveries.

An event is planned into the notifications it should produce (a pure function),
and each is delivered on its channel. Every delivery is keyed on
(event_id, channel), so at-least-once redelivery re-drives only the channels
that are not yet terminal: a channel already delivered or dead-lettered is left
alone, a channel still pending or retryable is attempted again. Each send
carries the channel's provider idempotency key, so a redelivery that follows a
crash between a successful send and its commit produces no second message.

The whole event is applied in one transaction: the deliveries, their attempts,
and the resulting statuses commit together, so the local history never disagrees
with what the service believes it sent, and the provider key covers the one gap
a transaction cannot.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels import Channel, ChannelProvider, provider_key
from app.models import NotificationAttempt, NotificationDelivery
from app.routing import PlannedNotification, plan_notifications
from app.status import DeliveryStatus, is_terminal, next_status

logger = logging.getLogger("notification.service")


def _delivery_for(
    db: Session,
    event_id: UUID,
    planned: PlannedNotification,
    payment_id: str | None,
    account_id: str | None,
    correlation_id: str | None,
) -> NotificationDelivery:
    """The (event_id, channel) delivery, created PENDING on first sight."""
    existing = db.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.event_id == event_id,
            NotificationDelivery.channel == planned.channel.value,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    delivery = NotificationDelivery(
        event_id=event_id,
        payment_id=UUID(payment_id),
        account_id=UUID(account_id),
        channel=planned.channel.value,
        destination=planned.destination,
        status=DeliveryStatus.PENDING.value,
        attempt_count=0,
        correlation_id=UUID(correlation_id) if correlation_id else None,
    )
    db.add(delivery)
    db.flush()
    return delivery


def _attempt(
    db: Session,
    delivery: NotificationDelivery,
    planned: PlannedNotification,
    provider: ChannelProvider,
    event_id: UUID,
) -> DeliveryStatus:
    """Attempt one send, record the attempt, and advance the delivery's status."""
    key = provider_key(str(event_id), planned.channel)
    result = provider.send(planned.destination, planned.message, key)

    delivery.attempt_count += 1
    db.add(
        NotificationAttempt(
            delivery_id=delivery.id,
            attempt_number=delivery.attempt_count,
            outcome=result.outcome.value,
            error=result.error,
        )
    )

    status = next_status(result.outcome, delivery.attempt_count)
    delivery.status = status.value
    if status is DeliveryStatus.DELIVERED:
        delivery.provider_reference = result.reference
        delivery.delivered_at = datetime.now(tz=timezone.utc)
    return status


def apply_event(
    db: Session,
    envelope: dict,
    channels: dict[Channel, ChannelProvider],
) -> dict:
    """Deliver the notifications an event is eligible for. Returns a summary and,
    crucially, whether any channel still wants a retry, which the caller turns
    into a non-2xx so the broker redelivers."""
    event_id = UUID(envelope["event_id"])
    event_type = envelope["event_type"]
    payload = envelope.get("payload") or {}

    plan = plan_notifications(event_type, payload)
    if not plan:
        # Consumed and accounted for: an event we do not notify on, or one that
        # predates the account_id enrichment and so cannot be addressed. Either
        # way it is acked, never retried, and never crashes the consumer.
        return {"status": "ignored", "retry": False, "deliveries": 0}

    payment_id = payload.get("payment_id")
    account_id = payload.get("account_id")
    correlation_id = envelope.get("correlation_id")

    retry = False
    delivered = 0
    for planned in plan:
        delivery = _delivery_for(
            db, event_id, planned, payment_id, account_id, correlation_id
        )
        if is_terminal(DeliveryStatus(delivery.status)):
            continue
        status = _attempt(db, delivery, planned, channels[planned.channel], event_id)
        if status is DeliveryStatus.DELIVERED:
            delivered += 1
        elif status is DeliveryStatus.FAILED_RETRYABLE:
            retry = True

    db.commit()
    return {
        "status": "processed",
        "retry": retry,
        "deliveries": len(plan),
        "delivered": delivered,
    }
