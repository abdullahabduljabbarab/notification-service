"""The delivery state machine.

Each (event_id, channel) delivery moves through this small machine as it is
attempted. A send that succeeds is DELIVERED. A send that fails is retryable
until the attempt limit, after which it is DEAD_LETTERED so the service does not
retry a channel that will never succeed. Both DELIVERED and DEAD_LETTERED are
terminal: once every channel for an event is terminal, the message is acked.
"""

import enum

from app.channels import SendOutcome

MAX_ATTEMPTS = 3


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED_RETRYABLE = "failed_retryable"
    DEAD_LETTERED = "dead_lettered"


TERMINAL: frozenset[DeliveryStatus] = frozenset(
    {DeliveryStatus.DELIVERED, DeliveryStatus.DEAD_LETTERED}
)


def next_status(
    outcome: SendOutcome, attempt_count: int, max_attempts: int = MAX_ATTEMPTS
) -> DeliveryStatus:
    """The status after an attempt, given its outcome and how many attempts
    (including this one) have now been made."""
    if outcome is SendOutcome.SENT:
        return DeliveryStatus.DELIVERED
    if attempt_count >= max_attempts:
        return DeliveryStatus.DEAD_LETTERED
    return DeliveryStatus.FAILED_RETRYABLE


def is_terminal(status: DeliveryStatus) -> bool:
    return status in TERMINAL
