from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PubSubPush(BaseModel):
    """The envelope Pub/Sub wraps a pushed message in. `message.data` is the
    base64-encoded ABS event."""

    message: dict
    subscription: str | None = None


class AttemptOut(BaseModel):
    attempt_number: int
    outcome: str
    error: str | None
    attempted_at: datetime | None


class DeliveryOut(BaseModel):
    id: UUID
    event_id: UUID
    channel: str
    destination: str
    status: str
    attempt_count: int
    provider_reference: str | None
    correlation_id: UUID | None
    created_at: datetime | None
    delivered_at: datetime | None
    attempts: list[AttemptOut]


class NotificationsOut(BaseModel):
    payment_id: UUID
    deliveries: list[DeliveryOut]
