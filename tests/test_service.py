"""Applying events to durable deliveries: fan-out, dedup, retry, dead-letter,
and the crash window between a send and its commit. Runs against the migrated
schema via conftest.
"""

from uuid import UUID, uuid4

import pytest

from app.channels import Channel, ScriptedChannel, SendOutcome
from app.models import NotificationDelivery
from app.service import apply_event


def _channels(email=None, sms=None):
    return {
        Channel.EMAIL: email or ScriptedChannel(Channel.EMAIL),
        Channel.SMS: sms or ScriptedChannel(Channel.SMS),
    }


def _envelope(event_type, with_account=True, **payload_extra):
    payload = {"payment_id": str(uuid4())}
    if with_account:
        payload["account_id"] = str(uuid4())
    payload.update(payload_extra)
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "occurred_at": "2026-09-06T00:00:00+00:00",
        "correlation_id": str(uuid4()),
        "payload": payload,
    }


def _deliveries(db, envelope):
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.event_id == UUID(envelope["event_id"]))
        .all()
    )


def test_settled_delivers_on_both_channels(db):
    env = _envelope("payment.settled")
    result = apply_event(db, env, _channels())

    assert result["status"] == "processed"
    assert result["deliveries"] == 2
    rows = _deliveries(db, env)
    assert {r.channel for r in rows} == {"email", "sms"}
    assert all(r.status == "delivered" for r in rows)
    assert all(r.provider_reference is not None for r in rows)
    assert all(r.delivered_at is not None for r in rows)


def test_delivery_carries_the_correlation_id(db):
    env = _envelope("payment.settled")
    apply_event(db, env, _channels())
    rows = _deliveries(db, env)
    assert all(str(r.correlation_id) == env["correlation_id"] for r in rows)


def test_risk_review_delivers_one_email(db):
    env = _envelope("risk.evaluated", decision="review")
    result = apply_event(db, env, _channels())
    assert result["deliveries"] == 1
    rows = _deliveries(db, env)
    assert [r.channel for r in rows] == ["email"]


def test_uninteresting_event_is_ignored(db):
    env = _envelope("risk.evaluated", decision="allow")
    result = apply_event(db, env, _channels())
    assert result["status"] == "ignored"
    assert result["retry"] is False
    assert _deliveries(db, env) == []


def test_event_without_account_is_ignored_not_crashed(db):
    # An older replayed event that predates the account_id enrichment: consumed,
    # acked, never retried, never crashes the consumer.
    env = _envelope("payment.settled", with_account=False)
    result = apply_event(db, env, _channels())
    assert result["status"] == "ignored"
    assert _deliveries(db, env) == []


def test_redelivery_does_not_send_again(db):
    email = ScriptedChannel(Channel.EMAIL)
    sms = ScriptedChannel(Channel.SMS)
    env = _envelope("payment.settled")

    apply_event(db, env, _channels(email, sms))
    apply_event(db, env, _channels(email, sms))

    assert email.sends == 1
    assert sms.sends == 1
    assert len(_deliveries(db, env)) == 2


def test_a_failed_channel_signals_retry_then_succeeds(db):
    email = ScriptedChannel(Channel.EMAIL, [SendOutcome.FAILED, SendOutcome.SENT])
    env = _envelope("payment.rejected")  # email only

    first = apply_event(db, env, _channels(email))
    assert first["retry"] is True
    assert _deliveries(db, env)[0].status == "failed_retryable"

    second = apply_event(db, env, _channels(email))
    assert second["retry"] is False
    row = _deliveries(db, env)[0]
    assert row.status == "delivered"
    assert row.attempt_count == 2
    assert email.sends == 2


def test_a_channel_that_keeps_failing_is_dead_lettered(db):
    email = ScriptedChannel(
        Channel.EMAIL, [SendOutcome.FAILED, SendOutcome.FAILED, SendOutcome.FAILED]
    )
    env = _envelope("payment.rejected")  # email only

    apply_event(db, env, _channels(email))
    apply_event(db, env, _channels(email))
    final = apply_event(db, env, _channels(email))

    row = _deliveries(db, env)[0]
    assert row.status == "dead_lettered"
    assert row.attempt_count == 3
    assert email.sends == 3
    # Terminal: the message is fully handled, so the broker is not asked to retry.
    assert final["retry"] is False


def test_dead_lettered_delivery_has_its_attempts_recorded(db):
    email = ScriptedChannel(Channel.EMAIL, [SendOutcome.FAILED] * 3)
    env = _envelope("payment.rejected")
    for _ in range(3):
        apply_event(db, env, _channels(email))

    db.refresh(_deliveries(db, env)[0])
    row = _deliveries(db, env)[0]
    assert [a.attempt_number for a in row.attempts] == [1, 2, 3]
    assert all(a.outcome == "failed" for a in row.attempts)


def test_crash_between_send_and_commit_does_not_double_send(db, monkeypatch):
    # The provider succeeds, then the process dies before the delivery is
    # committed. On redelivery the provider must return the original result
    # under the same key, so exactly one message is ever sent.
    email = ScriptedChannel(Channel.EMAIL, [SendOutcome.SENT])
    env = _envelope("payment.rejected")  # email only, one channel

    def boom():
        raise RuntimeError("crash before commit")

    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError):
        apply_event(db, env, _channels(email))
    monkeypatch.undo()
    db.rollback()

    # The delivery row was rolled back with the crash; the provider's own
    # idempotency store is what remembers the send.
    assert _deliveries(db, env) == []

    result = apply_event(db, env, _channels(email))
    assert result["status"] == "processed"
    assert email.sends == 1
    rows = _deliveries(db, env)
    assert len(rows) == 1
    assert rows[0].status == "delivered"
