"""Fan-out is a pure function of the event: the right channels, or nothing."""

import uuid

from app.channels import Channel
from app.routing import plan_notifications


def _payload(**overrides):
    base = {"payment_id": str(uuid.uuid4()), "account_id": str(uuid.uuid4())}
    base.update(overrides)
    return base


def _channels(plan):
    return sorted(p.channel for p in plan)


def test_settled_fans_out_to_email_and_sms():
    plan = plan_notifications("payment.settled", _payload())
    assert _channels(plan) == sorted([Channel.EMAIL, Channel.SMS])
    assert all("has settled" in p.message for p in plan)


def test_failed_fans_out_to_email_and_sms():
    plan = plan_notifications("payment.failed", _payload())
    assert _channels(plan) == sorted([Channel.EMAIL, Channel.SMS])


def test_rejected_is_email_only():
    plan = plan_notifications("payment.rejected", _payload())
    assert _channels(plan) == [Channel.EMAIL]


def test_risk_review_notifies_by_email():
    plan = plan_notifications("risk.evaluated", _payload(decision="review"))
    assert _channels(plan) == [Channel.EMAIL]
    assert "being reviewed" in plan[0].message


def test_risk_block_notifies_by_email():
    plan = plan_notifications("risk.evaluated", _payload(decision="block"))
    assert _channels(plan) == [Channel.EMAIL]


def test_risk_allow_produces_nothing():
    assert plan_notifications("risk.evaluated", _payload(decision="allow")) == []


def test_uninteresting_events_produce_nothing():
    assert plan_notifications("payment.received", _payload()) == []
    assert plan_notifications("payment.approved", _payload()) == []


def test_event_without_account_produces_nothing():
    assert plan_notifications("payment.settled", {"payment_id": str(uuid.uuid4())}) == []


def test_message_carries_the_payment_id():
    pid = str(uuid.uuid4())
    plan = plan_notifications("payment.settled", _payload(payment_id=pid))
    assert all(pid in p.message for p in plan)


def test_destinations_are_derived_from_the_account():
    account_id = str(uuid.uuid4())
    plan = plan_notifications("payment.settled", _payload(account_id=account_id))
    by_channel = {p.channel: p.destination for p in plan}
    assert by_channel[Channel.EMAIL] == f"{account_id}@example.com"
    assert by_channel[Channel.SMS].startswith("+100")
