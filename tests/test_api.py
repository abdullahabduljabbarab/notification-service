"""The HTTP surface: health, the authenticated Pub/Sub consumer, and the read
API. Uses the client fixture (scripted all-success channels) unless a test
overrides the channels.
"""

import base64
import json
from uuid import uuid4

from app import config
from app.channels import Channel, ScriptedChannel, SendOutcome
from app.main import app, get_channels


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


def _push(envelope):
    data = base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("utf-8")
    return {"message": {"data": data}, "subscription": "test"}


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_push_delivers_and_read_api_shows_it(client):
    env = _envelope("payment.settled")
    resp = client.post("/events/pubsub", json=_push(env))
    assert resp.status_code == 200
    assert resp.json()["deliveries"] == 2

    payment_id = env["payload"]["payment_id"]
    read = client.get(f"/notifications/{payment_id}")
    assert read.status_code == 200
    body = read.json()
    assert body["payment_id"] == payment_id
    channels = {d["channel"] for d in body["deliveries"]}
    assert channels == {"email", "sms"}
    assert all(d["status"] == "delivered" for d in body["deliveries"])
    assert all(len(d["attempts"]) == 1 for d in body["deliveries"])


def test_push_is_idempotent_across_redelivery(client):
    env = _envelope("payment.settled")
    client.post("/events/pubsub", json=_push(env))
    client.post("/events/pubsub", json=_push(env))

    payment_id = env["payload"]["payment_id"]
    body = client.get(f"/notifications/{payment_id}").json()
    assert len(body["deliveries"]) == 2
    assert all(d["attempt_count"] == 1 for d in body["deliveries"])


def test_push_returns_503_when_a_channel_wants_retry(client):
    app.dependency_overrides[get_channels] = lambda: {
        Channel.EMAIL: ScriptedChannel(Channel.EMAIL, [SendOutcome.FAILED]),
        Channel.SMS: ScriptedChannel(Channel.SMS, [SendOutcome.FAILED]),
    }
    resp = client.post("/events/pubsub", json=_push(_envelope("payment.settled")))
    assert resp.status_code == 503


def test_push_ignores_an_uninteresting_event(client):
    resp = client.post(
        "/events/pubsub", json=_push(_envelope("risk.evaluated", decision="allow"))
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_push_rejects_an_invalid_envelope(client):
    resp = client.post("/events/pubsub", json=_push({"nope": True}))
    assert resp.status_code == 400


def test_push_rejects_missing_message_data(client):
    resp = client.post("/events/pubsub", json={"message": {}})
    assert resp.status_code == 400


def test_push_requires_authentication_when_configured(client, monkeypatch):
    monkeypatch.setattr(
        config, "PUBSUB_PUSH_SA", "notify-push@proj.iam.gserviceaccount.com"
    )
    resp = client.post("/events/pubsub", json=_push(_envelope("payment.settled")))
    assert resp.status_code == 401


def test_read_api_is_empty_for_an_unknown_payment(client):
    resp = client.get(f"/notifications/{uuid4()}")
    assert resp.status_code == 200
    assert resp.json()["deliveries"] == []
