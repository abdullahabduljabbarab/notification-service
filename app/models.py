import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class NotificationDelivery(Base):
    """One delivery of one event on one channel. Unique on (event_id, channel),
    which is the service's idempotency key: a redelivered event finds the
    channel's delivery already here and does not create a second. `status` is a
    plain string, deliberately not a database enum, following the orchestrator's
    one live failure (an enum name/value mismatch); the four delivery states
    carry the same meaning with none of that risk. `provider_reference` is the
    reference the channel returned on success, and `correlation_id` carries the
    originating event's trace id onto the delivery (ABS-REQ-009)."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "channel", name="uq_event_channel"),
        Index("ix_deliveries_payment", "payment_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), nullable=False)
    payment_id = Column(UUID(as_uuid=True), nullable=False)
    account_id = Column(UUID(as_uuid=True), nullable=False)
    channel = Column(String(20), nullable=False)
    destination = Column(String, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    provider_reference = Column(String, nullable=True)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    attempts = relationship(
        "NotificationAttempt",
        back_populates="delivery",
        order_by="NotificationAttempt.attempt_number",
        cascade="all, delete-orphan",
    )


class NotificationAttempt(Base):
    """One send attempt against a delivery. Every attempt is recorded, success
    or failure, so the read API can show exactly what happened rather than a
    bare boolean. `outcome` is "sent" or "failed"; `error` holds the provider's
    reason on a failure."""

    __tablename__ = "notification_attempts"
    __table_args__ = (Index("ix_attempts_delivery", "delivery_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number = Column(Integer, nullable=False)
    outcome = Column(String(20), nullable=False)
    error = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), server_default=func.now())

    delivery = relationship("NotificationDelivery", back_populates="attempts")
