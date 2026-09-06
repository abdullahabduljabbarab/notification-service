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

The `(event_id, channel)` uniqueness handles the ordinary case: a redelivery finds each channel's delivery already recorded and does not send it again.

### The crash window between send and commit

Database uniqueness alone is not enough, and it would be dishonest to claim it makes a double send impossible. A transaction cannot span an external network call, so this sequence exists:

```
1. delivery row is PENDING
2. call the email provider  →  provider sends the message
3. process crashes here      ✗  (before the commit)
4. delivery is never marked DELIVERED
5. Pub/Sub redelivers event E
6. the service calls the email provider again
```

The row-level guard sees a delivery that is not yet terminal and drives the send a second time. Two real emails.

The fix is provider-level idempotency layered on top of the database guard. Every send carries a deterministic key, `notification:{event_id}:{channel}`. A provider that has already succeeded for a key returns the original result rather than sending again, so the redelivery in step 6 produces no second message. A failed send is not recorded against the key, because it produced no side effect and must be genuinely retried.

```
app-level:       UNIQUE (event_id, channel)   prevents duplicate delivery rows
provider-level:  key notification:{event_id}:{channel}   prevents duplicate side effect
                 ─────────────────────────────────────
                 together: safe redelivery across the send/commit crash window
```

This mirrors the ledger and orchestrator discipline: correctness does not rest on a database transaction pretending to include something it cannot.

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

A retryable failure returns a non-2xx from the push endpoint, so Pub/Sub redelivers the event and the service retries only the channels that have not yet delivered. Once every channel for an event is terminal (delivered or dead-lettered), the endpoint returns 2xx and the message is acknowledged.

### Two kinds of dead-lettering

These are different levels and should not be confused.

`DEAD_LETTERED` is an application state on a single channel's delivery. It means the service tried to send on that channel up to the attempt limit and the provider kept refusing, so the service gives up on that channel and stops retrying something that will not succeed. A message whose channels all reach a terminal state, including one that dead-lettered, is fully handled: the endpoint still returns 2xx and Pub/Sub still acks it. A dead-lettered email does not go to the Pub/Sub dead-letter topic; it is a recorded business outcome.

```
payment.settled E
├── email → DELIVERED
└── sms   → DEAD_LETTERED   (provider refused every attempt)
all channels terminal → HTTP 2xx → Pub/Sub ACK
```

The Pub/Sub dead-letter topic is a transport-level backstop for a different failure: the service cannot process the message at all. The endpoint keeps returning non-2xx across the subscription's delivery attempts because the service is crashing, the database is unavailable, or the envelope is invalid. Only then does the broker route the message to its dead-letter topic for inspection. Channel exhaustion is normal and handled in-band; the broker DLQ is for the service being unable to do its job.

## The consumer

Events arrive by Pub/Sub push at `POST /events/pubsub`. The handler validates the ABS envelope, plans the notifications the event is eligible for, and for each channel upserts a delivery keyed on `(event_id, channel)`, attempts the ones not already delivered with their provider idempotency key, records the attempt and the resulting status, and returns 2xx or non-2xx based on whether any channel still wants a retry. Recording the delivery and the attempt is transactional, so the local history never disagrees with what the service believes it sent, and the provider key protects the one gap a transaction cannot cover.

### Authenticated ingress

`POST /events/pubsub` is the only privileged surface, and it is reachable only by the platform's own push subscription. Pub/Sub is configured to attach an OIDC token minted for a dedicated push service account, and the handler verifies that token at the application layer before doing any work.

```
Pub/Sub push subscription
        │  OIDC token (push service account identity)
        ▼
POST /events/pubsub
        │  verify token: issuer, audience, and that the email is the
        │  expected push service account  →  else 401
        ▼
validate envelope → plan → deliver
```

This is the same ingress hardening the risk engine uses. The health check and the read API carry no such requirement and stay public, because they expose nothing that can move money or trigger a send.

## Data model

- **notification_deliveries**: one row per `(event_id, channel)`, unique on that pair. `id`, `event_id`, `payment_id`, `account_id`, `channel`, `destination`, `status`, `attempt_count`, `provider_reference`, `correlation_id`, `created_at`, `delivered_at`.
- **notification_attempts**: one row per send attempt. `id`, `delivery_id`, `attempt_number`, `outcome`, `error`, `attempted_at`. This is what makes the read API show exactly what happened rather than a bare boolean.

## The read API

The public surface is deliberately tiny: `GET /health`, and `GET /notifications/{payment_id}`, which returns every delivery for a payment with its channel, status, attempt count, and the attempt history. There are no send commands, no templates and no preference management, because none of that is the point. The only authenticated surface is the Pub/Sub ingest endpoint above; the read API is read-only over the service's own delivery records and stays public. This repo is about downstream consumption, fan-out, idempotent side effects, retries and isolation.

## Simulated channels

Email and SMS are simulated providers with deterministic failure modes, so retry and dead-letter behaviour can be exercised in tests and demonstrated live without an external service. A provider returns sent or failed; the service treats a failure as retryable until the attempt limit.

## Requirements satisfied

ABS-REQ-006 is owned here: notification failure must not alter financial state, verified by the service being strictly downstream and by killing the consumer while payments still settle. ABS-REQ-008 (consumers tolerate duplicate delivery) is satisfied by the `(event_id, channel)` deduplication, and ABS-REQ-009 (one correlation id across services) by carrying the event's correlation id onto every delivery.

## What this is not

It is not a message-composition or templating system, not a customer-preference service, and not an integration with a real email or SMS provider. It never sends a command to another service, never reads or writes financial state, and holds no credential that could. It is a downstream sink whose only job is to turn facts into messages, safely and idempotently, and to stay out of the way of everything upstream.
