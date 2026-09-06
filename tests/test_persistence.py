"""Persistence tests, run against the Alembic-migrated schema (see conftest).

These exercise the ORM and the migration together: if a column, constraint, or
default drifts between the model and the migration, these fail rather than a
production insert. That is the ADR-014 discipline carried from the risk engine.
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import NotificationAttempt, NotificationDelivery


def _delivery(**overrides) -> NotificationDelivery:
    base = dict(
        event_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        channel="email",
        destination="a@example.com",
        status="pending",
        attempt_count=0,
        correlation_id=uuid.uuid4(),
    )
    base.update(overrides)
    return NotificationDelivery(**base)


def test_a_delivery_persists_and_reads_back(db):
    delivery = _delivery()
    db.add(delivery)
    db.commit()

    stored = db.get(NotificationDelivery, delivery.id)
    assert stored.channel == "email"
    assert stored.status == "pending"
    assert stored.attempt_count == 0
    assert stored.created_at is not None
    assert stored.delivered_at is None


def test_same_event_and_channel_cannot_be_stored_twice(db):
    event_id = uuid.uuid4()
    db.add(_delivery(event_id=event_id, channel="email"))
    db.commit()

    db.add(_delivery(event_id=event_id, channel="email"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_same_event_fans_out_to_two_channels(db):
    event_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    db.add(_delivery(event_id=event_id, payment_id=payment_id, channel="email"))
    db.add(
        _delivery(
            event_id=event_id,
            payment_id=payment_id,
            channel="sms",
            destination="+100123",
        )
    )
    db.commit()

    rows = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.payment_id == payment_id)
        .all()
    )
    assert {r.channel for r in rows} == {"email", "sms"}


def test_attempts_are_recorded_in_order_under_a_delivery(db):
    delivery = _delivery()
    db.add(delivery)
    db.commit()

    db.add(
        NotificationAttempt(
            delivery_id=delivery.id,
            attempt_number=1,
            outcome="failed",
            error="declined",
        )
    )
    db.add(
        NotificationAttempt(delivery_id=delivery.id, attempt_number=2, outcome="sent")
    )
    db.commit()
    db.refresh(delivery)

    assert [a.attempt_number for a in delivery.attempts] == [1, 2]
    assert delivery.attempts[0].outcome == "failed"
    assert delivery.attempts[0].error == "declined"
    assert delivery.attempts[1].outcome == "sent"


def test_an_attempt_needs_a_real_delivery(db):
    db.add(
        NotificationAttempt(
            delivery_id=uuid.uuid4(), attempt_number=1, outcome="sent"
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_a_delivery_cascades_to_its_attempts(db):
    delivery = _delivery()
    db.add(delivery)
    db.commit()
    db.add(
        NotificationAttempt(delivery_id=delivery.id, attempt_number=1, outcome="sent")
    )
    db.commit()

    db.delete(delivery)
    db.commit()

    assert db.query(NotificationAttempt).count() == 0
