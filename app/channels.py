"""Simulated delivery channels.

A channel is asked to send a message to a destination and reports whether it
succeeded. A failure is treated as retryable by the delivery lifecycle. Real
email or SMS integration is out of scope; these simulate providers with
deterministic (scripted) or probabilistic (rate-based) failure modes, so retry
and dead-letter behaviour can be tested and demonstrated without an external
service.

Every send carries a deterministic idempotency key. A database transaction
cannot span an external send, so there is a window where a provider succeeds but
the process dies before the delivery is recorded; the event is then redelivered
and the send repeats. The key closes that window: a provider that has already
succeeded for a key returns the original result instead of sending a second
customer-facing message. A failed send is not recorded against the key, because
it produced no side effect and must be genuinely retried. Real providers expose
exactly this (an idempotency key on the send API); the simulation mirrors it.
"""

import enum
import random
from collections import deque
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


class Channel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"


class SendOutcome(str, enum.Enum):
    SENT = "sent"
    FAILED = "failed"


@dataclass
class SendResult:
    outcome: SendOutcome
    reference: str | None = None
    error: str | None = None


class ChannelProvider(Protocol):
    channel: Channel

    def send(
        self, destination: str, message: str, idempotency_key: str | None = None
    ) -> SendResult: ...


def provider_key(event_id: str, channel: Channel) -> str:
    """The deterministic idempotency key for a send, derived from the event and
    channel so the same delivery always presents the same key to the provider."""
    return f"notification:{event_id}:{channel.value}"


def _reference(channel: Channel) -> str:
    return f"{channel.value}-{uuid4().hex[:12]}"


class ScriptedChannel:
    """A channel whose outcomes are fixed in advance, for deterministic tests."""

    def __init__(self, channel: Channel, outcomes: list[SendOutcome] | None = None):
        self.channel = channel
        self._outcomes: deque[SendOutcome] = deque(outcomes or [SendOutcome.SENT])
        self._delivered: dict[str, SendResult] = {}
        self.sends = 0

    def send(
        self, destination: str, message: str, idempotency_key: str | None = None
    ) -> SendResult:
        if idempotency_key is not None and idempotency_key in self._delivered:
            return self._delivered[idempotency_key]
        self.sends += 1
        outcome = self._outcomes.popleft() if self._outcomes else SendOutcome.SENT
        if outcome is SendOutcome.SENT:
            result = SendResult(SendOutcome.SENT, _reference(self.channel))
            if idempotency_key is not None:
                self._delivered[idempotency_key] = result
            return result
        return SendResult(SendOutcome.FAILED, None, "provider declined")


class SimulatedChannel:
    """A channel that fails a configurable fraction of the time, so the live
    service exercises retry and dead-letter without a real provider."""

    def __init__(self, channel: Channel, failure_rate: float = 0.0, seed: int | None = None):
        self.channel = channel
        self._failure_rate = failure_rate
        self._rng = random.Random(seed)
        self._delivered: dict[str, SendResult] = {}

    def send(
        self, destination: str, message: str, idempotency_key: str | None = None
    ) -> SendResult:
        if idempotency_key is not None and idempotency_key in self._delivered:
            return self._delivered[idempotency_key]
        if self._rng.random() < self._failure_rate:
            return SendResult(SendOutcome.FAILED, None, "simulated provider failure")
        result = SendResult(SendOutcome.SENT, _reference(self.channel))
        if idempotency_key is not None:
            self._delivered[idempotency_key] = result
        return result
