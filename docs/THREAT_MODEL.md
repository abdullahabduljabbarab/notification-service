# Threat Model (STRIDE)

## Scope

This threat model covers the notification service: a downstream side-effect service deployed on GCP Cloud Run with Cloud SQL PostgreSQL, fed by two Pub/Sub push subscriptions and exposing a read API. It does not cover the ledger's, orchestrator's or risk engine's own threat models, network-level DDoS, or physical security of cloud infrastructure.

## Assets

| Asset | Sensitivity | Location |
|-------|-------------|----------|
| Delivery records and attempt history | Medium (audit of what was sent) | Notification Cloud SQL |
| The integrity of delivery (exactly one message per channel per event) | High | Enforced by (event_id, channel) uniqueness and the provider key |
| Database credentials | Critical | Secret Manager |
| Deploy identity (WIF, deploy service account) | Critical | GCP IAM, no key stored |
| Isolation from financial state | Critical (the defining property) | Architecture (no write path exists) |

## Threat Analysis

### S: Spoofing

| Threat | Mitigation | Test |
|--------|------------|------|
| Attacker forges events to trigger notifications | The ingest endpoint requires a Google OIDC token minted for the dedicated push service account, so a delivery without a valid token is rejected before any event is applied. | `test_push_requires_authentication_when_configured`; live, an unauthenticated call returns 401 |
| Attacker replays events to send duplicate messages | Delivery is deduplicated on `(event_id, channel)` and each send carries a deterministic provider key, so a redelivery sends nothing again. | `test_push_is_idempotent_across_redelivery`, `test_redelivery_does_not_send_again`, `test_crash_between_send_and_commit_does_not_double_send` |

### T: Tampering

| Threat | Mitigation | Test |
|--------|------------|------|
| Forging a payload to reach a chosen recipient | Recipients are derived deterministically from the event's `account_id`, not taken from the payload, so a payload cannot redirect a message to an arbitrary address. | `test_destinations_are_derived_from_the_account` |
| SQL injection via API parameters | SQLAlchemy ORM with parameterised queries; FastAPI validates and coerces the UUID path first. | `test_read_api_is_empty_for_an_unknown_payment` |
| Malformed or undecodable envelopes | The envelope is validated and the data decoded before any work; a bad envelope is a 400. | `test_push_rejects_an_invalid_envelope`, `test_push_rejects_missing_message_data` |

### R: Repudiation

| Threat | Mitigation | Test |
|--------|------------|------|
| Denial that a customer was or was not notified | Every delivery records the event that caused it, each attempt and its outcome, and its terminal status, all readable per payment. | `test_dead_lettered_delivery_has_its_attempts_recorded`, `test_push_delivers_and_read_api_shows_it` |
| A delivery cannot be tied to a request | Every delivery carries the originating event's `correlation_id`, so it traces across services. | `test_delivery_carries_the_correlation_id`; live, the delivery's correlation_id matched the orchestrator payment's |

### I: Information Disclosure

| Threat | Mitigation | Test |
|--------|------------|------|
| Enumeration of deliveries | Deliveries, payments and accounts are keyed by UUID; the read API is fetched by payment id, not a sequential id. | `test_read_api_is_empty_for_an_unknown_payment` |
| Stack traces or internal state in responses | Structured JSON errors, no stack traces. | FastAPI exception handling |
| Recipient or message leakage in logs | Logs record metadata, not message bodies or recipient addresses. | Log usage in `app/main.py`, `app/service.py` |

### D: Denial of Service

| Threat | Mitigation | Test |
|--------|------------|------|
| A poison event retried forever against the ingest endpoint | A dead-letter policy on both subscriptions bounds delivery attempts, so a message that can never be processed is parked, not looped. | Dead-letter policy in `terraform/` |
| A channel that keeps failing, retried forever | The delivery state machine dead-letters a channel after a bounded number of attempts, an in-band terminal state. | `test_a_channel_that_keeps_failing_is_dead_lettered` |
| Malicious ingest payloads | The envelope is validated before the database; an undecodable or incomplete message is a 400. | `test_push_rejects_an_invalid_envelope` |

### E: Elevation of Privilege

| Threat | Mitigation | Test |
|--------|------------|------|
| Compromise of the service to move money | The service holds no financial credential and has no path to financial state; there is no write path to compromise. | Strict-sink design (ABS-REQ-006) |
| Compromise of the deploy identity | The deploy is keyless: a repository-scoped Workload Identity binding, not a stored key, so there is no long-lived credential to steal. | WIF binding in `terraform/` |

## Mitigations Not Yet Implemented

| Gap | Risk | Priority |
|-----|------|----------|
| Authentication on the read endpoint | Low: exposes only synthetic delivery metadata | Would add in production, scoped to an owner |
| Real provider integration with its own idempotency | Low: providers are simulated | Would add with a real provider; the deterministic key already models it |
| Rate limiting | Low: API abuse | Would add via Cloud Armor or middleware |

## Requirement-to-Test Traceability

| Requirement | Tests |
|-------------|-------|
| Notification failure never touches financial state (ABS-REQ-006) | strict-sink design (no write path, no broker client); live, held and settled payments were unaffected by their notifications failing and retrying |
| Consumers tolerate duplicate delivery (ABS-REQ-008) | `test_redelivery_does_not_send_again`, `test_push_is_idempotent_across_redelivery`, `test_crash_between_send_and_commit_does_not_double_send` |
| One correlation id across services (ABS-REQ-009) | `test_delivery_carries_the_correlation_id`; live correlation match |
| Bounded retry then dead-letter | `test_a_failed_channel_signals_retry_then_succeeds`, `test_a_channel_that_keeps_failing_is_dead_lettered` |
