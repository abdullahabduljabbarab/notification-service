"""Fan-out: turning an event into the notifications it should produce.

A pure function of the event. Given the event type and payload, it returns the
deliveries the event is eligible for, one per channel, with a rendered message
and a simulated destination. Events a customer would not care about (a payment
being received or approved, a risk allow) produce nothing, so every event is
accounted for without noise.
"""

from dataclasses import dataclass

from app.channels import Channel


@dataclass(frozen=True)
class PlannedNotification:
    channel: Channel
    destination: str
    message: str


# Event type to (message template, channels). risk.evaluated depends on the
# decision, so it is handled separately below.
_PAYMENT_SPECS: dict[str, tuple[str, list[Channel]]] = {
    "payment.settled": (
        "Your payment {payment_id} has settled.",
        [Channel.EMAIL, Channel.SMS],
    ),
    "payment.failed": (
        "Your payment {payment_id} could not be completed.",
        [Channel.EMAIL, Channel.SMS],
    ),
    "payment.rejected": (
        "Your payment {payment_id} was rejected.",
        [Channel.EMAIL],
    ),
}

_RISK_SPECS: dict[str, tuple[str, list[Channel]]] = {
    "review": ("Your payment {payment_id} is being reviewed.", [Channel.EMAIL]),
    "block": ("Your payment {payment_id} was blocked.", [Channel.EMAIL]),
}


def _email(account_id: str) -> str:
    return f"{account_id}@example.com"


def _sms(account_id: str) -> str:
    return "+100" + str(account_id).replace("-", "")[:10]


def _spec(event_type: str, payload: dict) -> tuple[str, list[Channel]] | None:
    if event_type in _PAYMENT_SPECS:
        return _PAYMENT_SPECS[event_type]
    if event_type == "risk.evaluated":
        return _RISK_SPECS.get(payload.get("decision"))
    return None


def plan_notifications(event_type: str, payload: dict) -> list[PlannedNotification]:
    account_id = payload.get("account_id")
    if account_id is None:
        return []
    spec = _spec(event_type, payload)
    if spec is None:
        return []
    template, channels = spec
    message = template.format(payment_id=payload.get("payment_id"))
    destination = {Channel.EMAIL: _email(account_id), Channel.SMS: _sms(account_id)}
    return [PlannedNotification(c, destination[c], message) for c in channels]
