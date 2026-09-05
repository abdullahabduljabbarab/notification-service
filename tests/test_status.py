"""The delivery state machine: a send outcome and an attempt count map to a status."""

from app.channels import SendOutcome
from app.status import (
    DeliveryStatus,
    is_terminal,
    next_status,
)


def test_a_sent_message_is_delivered():
    assert next_status(SendOutcome.SENT, attempt_count=1) is DeliveryStatus.DELIVERED


def test_a_send_is_delivered_however_late():
    assert next_status(SendOutcome.SENT, attempt_count=3) is DeliveryStatus.DELIVERED


def test_a_failure_below_the_limit_is_retryable():
    assert (
        next_status(SendOutcome.FAILED, attempt_count=1)
        is DeliveryStatus.FAILED_RETRYABLE
    )
    assert (
        next_status(SendOutcome.FAILED, attempt_count=2)
        is DeliveryStatus.FAILED_RETRYABLE
    )


def test_a_failure_at_the_limit_is_dead_lettered():
    assert (
        next_status(SendOutcome.FAILED, attempt_count=3)
        is DeliveryStatus.DEAD_LETTERED
    )


def test_the_attempt_limit_is_configurable():
    assert (
        next_status(SendOutcome.FAILED, attempt_count=2, max_attempts=2)
        is DeliveryStatus.DEAD_LETTERED
    )


def test_delivered_and_dead_lettered_are_terminal():
    assert is_terminal(DeliveryStatus.DELIVERED)
    assert is_terminal(DeliveryStatus.DEAD_LETTERED)


def test_pending_and_retryable_are_not_terminal():
    assert not is_terminal(DeliveryStatus.PENDING)
    assert not is_terminal(DeliveryStatus.FAILED_RETRYABLE)
