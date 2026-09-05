# Notification Service: Design

## Purpose

The notification service tells people what happened to their payments. It consumes the facts the rest of the ecosystem produces, a payment settled, a payment failed, a payment was held for review, and turns eligible ones into notifications on simulated email and SMS channels. It is the last link in the chain and a pure side effect: it reads events and sends messages, and it never calls back into the ledger, the orchestrator or the risk engine.

Its whole engineering thesis is one system property: **the financial platform keeps working even if every notification in the system is on fire.** A notification that fails, retries forever, or takes the service down must not touch financial state or the payment lifecycle. That isolation is the point (ABS-REQ-006).

## Strictly downstream

There is no upstream path out of this service. It subscribes to `payment-events` and `risk-events`, and it has no client for any other service.

```
   payment-events / risk-events
              │  (Pub/Sub push)
              ▼
     notification-service
              │  fan-out
        ┌─────┴─────┐
        ▼           ▼
      email        sms   (simulated)
```

Because it is downstream only, its failure is contained by construction: it consumes at its own pace, and if it stops, the events it would have consumed simply wait in the subscription while payments keep settling and balances stay unchanged. Nothing upstream waits on it.

## What it notifies

Only events a customer would want to hear about produce a notification. The rest are consumed and ignored.

| Event | Channels | Message |
|-------|----------|---------|
| payment.settled | email, sms | the payment has settled |
| payment.failed | email, sms | the payment could not be completed |
| payment.rejected | email | the payment was rejected |
| risk.evaluated, decision review | email | the payment is being reviewed |
| risk.evaluated, decision block | email | the payment was blocked |

Events like `payment.received`, `payment.approved` or a risk `allow` are consumed and produce nothing, so state stays consistent (every event is accounted for) without noise. Recipients are simulated from the account id, since this is a portfolio and there are no real contact details.

## Per-channel idempotency

Delivery is deduplicated on the pair `(event_id, channel)`, not on `event_id` alone. This is the service's distinct correctness property. One event legitimately fans out to more than one channel:

```
payment.settled  (event_id E)
├── email   (E, email)
└── sms     (E, sms)
```

but Pub/Sub's at-least-once redelivery of event `E` must not produce:

```
email  email  email
```

The `(event_id, channel)` uniqueness makes the first true and the second impossible: a redelivery finds each channel's delivery already recorded and does not send it again.

## Delivery lifecycle

Each `(event_id, channel)` is a delivery that moves through a small state machine, and every send is recorded as an attempt, so the history is fully visible.

```
        PENDING
           │ attempt
     ┌─────┴─────┐
   sent        failed
     │            │
     ▼      ┌─────┴─────┐
 DELIVERED  under max   at max
            │           │
            ▼           ▼
     FAILED_RETRYABLE  DEAD_LETTERED
```

A retryable failure returns a non-2xx from the push endpoint, so Pub/Sub redelivers the event and the service retries only the channels that have not yet delivered. Once every channel for an event is terminal (delivered or dead-lettered), the endpoint returns 2xx and the message is acknowledged. A Pub/Sub dead-letter topic is the outer backstop; the service's own `DEAD_LETTERED` state, reached after a maximum number of attempts, stops it retrying a channel that will never succeed.

## The consumer

Events arrive by Pub/Sub push at `POST /events/pubsub`. The handler validates the ABS envelope, plans the notifications the event is eligible for, and for each channel upserts a delivery keyed on `(event_id, channel)`, attempts the ones not already delivered, records the attempt and the resulting status, and returns 2xx or non-2xx based on whether any channel still wants a retry. Recording the delivery and the attempt is transactional, so the history never disagrees with what was sent.

## Data model

- **notification_deliveries**: one row per `(event_id, channel)`, unique on that pair. `id`, `event_id`, `payment_id`, `account_id`, `channel`, `destination`, `status`, `attempt_count`, `provider_reference`, `correlation_id`, `created_at`, `delivered_at`.
- **notification_attempts**: one row per send attempt. `id`, `delivery_id`, `attempt_number`, `outcome`, `error`, `attempted_at`. This is what makes the read API show exactly what happened rather than a bare boolean.

## The read API

The public surface is deliberately tiny: `GET /health`, and `GET /notifications/{payment_id}`, which returns every delivery for a payment with its channel, status, attempt count, and the attempt history. There are no send commands, no templates, no preference management, and no authentication, because none of that is the point. This repo is about downstream consumption, fan-out, idempotent side effects, retries and isolation.

## Simulated channels

Email and SMS are simulated providers with deterministic failure modes, so retry and dead-letter behaviour can be exercised in tests and demonstrated live without an external service. A provider returns sent or failed; the service treats a failure as retryable until the attempt limit.

## Requirements satisfied

ABS-REQ-006 is owned here: notification failure must not alter financial state, verified by the service being strictly downstream and by killing the consumer while payments still settle. ABS-REQ-008 (consumers tolerate duplicate delivery) is satisfied by the `(event_id, channel)` deduplication, and ABS-REQ-009 (one correlation id across services) by carrying the event's correlation id onto every delivery.

## What this is not

It is not a message-composition or templating system, not a customer-preference service, and not an integration with a real email or SMS provider. It never sends a command to another service, never reads or writes financial state, and holds no credential that could. It is a downstream sink whose only job is to turn facts into messages, safely and idempotently, and to stay out of the way of everything upstream.
