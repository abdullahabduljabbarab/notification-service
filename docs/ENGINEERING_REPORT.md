# Engineering Report

## What this is

A downstream notification service. It consumes committed payment and risk events and turns the customer-facing ones into messages on simulated email and SMS channels. It owns one thing: reliable delivery of those messages, tracked per channel, without ever touching financial state. It never reads or writes the ledger, never calls the orchestrator or the risk engine, and never moves money. The system is built around one property a side-effect service must have: it can fail, in every way, without changing a financial outcome.

## Architecture

```
                          payment-events        risk-events
                               │                     │
                               │  (Pub/Sub push, OIDC)
                               ▼                     ▼
                           POST /events/pubsub  (one endpoint, routes on type)
                               │
                               ▼
                   fan-out ─▶ per-channel delivery ─▶ simulated email / SMS
                               │
                               ▼
                        notification_deliveries + notification_attempts
                               │
                               ▼
                        GET /notifications/{payment_id}
```

FastAPI and Pydantic handle validation, routing and the OpenAPI spec. SQLAlchemy and Alembic own the schema and migrations. The service keeps its own state (deliveries and attempts) in its own database and never touches another service's tables. Deployed on GCP Cloud Run with auto-scaling, CI/CD via GitHub Actions using keyless Workload Identity Federation, and infrastructure captured in Terraform.

## Key engineering decisions

**A strict sink, never a source.** The service consumes committed events and produces messages, with no path that writes to or blocks any financial system. A notification failure, a provider outage, or the service being entirely down cannot change a payment's outcome (ABS-REQ-006). This is the point of the service; modelling "notified" as part of a payment's state was deliberately kept upstream, where it belongs if it belongs anywhere.

**Idempotency keyed on (event_id, channel), not event_id.** The transport is at-least-once and one event fans out to several channels that succeed and fail independently. The unit of dedup, retry and terminal state is the pair, so a redelivery re-drives only the channels that are not yet terminal. An email that already delivered is never re-sent because its SMS is still retrying.

**A deterministic provider idempotency key.** A database transaction cannot span an external send, so there is a window where a provider succeeds but the process dies before the delivery is recorded. Every send carries a key, `notification:{event_id}:{channel}`; a provider that has already succeeded for a key returns the original result rather than sending again, closing that window. This is the genuinely new systems idea the service contributes to ABS: reliable, idempotent external side effects that can fail independently of the system that caused them.

**Fan-out as a pure function.** Which channels an event notifies, and the message on each, is a pure function of the event, so the whole policy is unit-testable without a database or network and the same event always plans the same notifications.

**Bounded retry then dead-letter, by a pure state machine.** A single function maps a send outcome and attempt count to delivered, retryable or dead-lettered. Application-level dead-lettering (a channel a provider keeps refusing) is distinct from the Pub/Sub transport dead-letter (the service cannot process a message at all), and the design keeps them separate.

**A push consumer, no broker client.** Because the service runs on Cloud Run and only receives, the feed is two Pub/Sub push subscriptions delivering to one authenticated HTTP endpoint. The service declares no message-broker client, since it never publishes.

## Numbers

| Metric | Value |
|--------|-------|
| Test count | 54 |
| Alembic migrations | 1 (deliveries, attempts) |
| API endpoints | 3 (health, read, authenticated consumer) |
| Delivery states | 4 (pending, delivered, failed-retryable, dead-lettered) |
| Upstream topics consumed | 2 (payment-events, risk-events) |
| ABS requirements owned | isolation (006), duplicate tolerance (008), correlation continuity (009) |

## Test categories

| Category | What they prove |
|----------|-----------------|
| Routing | Each event's fan-out is exact (settled and failed to email + SMS, rejected to email, risk review/block to email, allow and uninteresting events to nothing, no account to nothing), messages render, destinations derive from the account |
| Channels | Scripted outcomes in order, simulated failure rates 0.0 and 1.0, determinism for a seed, and provider idempotency: a repeat send under a key does not send again, a failed send is retried for real |
| Status | Every transition of the delivery state machine and the terminal set |
| Service | Fan-out to durable deliveries, dedup across redelivery, retry then success, dead-letter after the limit, and the crash-between-send-and-commit regression |
| Persistence | Deliveries and attempts round-trip against the Alembic-migrated schema; the (event_id, channel) constraint and the foreign key hold |
| API | The push consumer, the read API, the 503 retry signal, ingress authentication, and the 400s for bad envelopes |

## Injected and handled failures

- A redelivered event (at-least-once) re-drives only non-terminal channels and never double-sends a delivered one.
- A provider that succeeds, then a crash before the commit: the provider key makes the redelivery a no-op side effect, so exactly one message is sent.
- A channel that fails transiently retries to a bounded limit; one that keeps failing is dead-lettered, still acked, not looped.
- An event without `account_id` (an older replayed event) is ignored and acked, never crashing the consumer.
- An unauthenticated call to the ingest endpoint is rejected before any work.

## Cloud architecture

| Component | Service | Region |
|-----------|---------|--------|
| API runtime | Cloud Run | europe-west2 (London) |
| Database | Cloud SQL PostgreSQL 16 (own database and user on the shared instance) | europe-west2 |
| Container registry | Artifact Registry | europe-west2 |
| Event bus | Pub/Sub (two push subscriptions, one dead-letter topic; no publish path) | europe-west2 |
| Secrets | Secret Manager | europe-west2 |
| CI/CD | GitHub Actions, keyless via Workload Identity Federation | Ubuntu runners |
| IaC | Terraform | All service resources declared |

## V&V matrix

The full requirement-to-test mapping is in [VV_PLAN.md](VV_PLAN.md). The read-path SLOs and their measurement are in [SLO.md](SLO.md), the STRIDE threat model in [THREAT_MODEL.md](THREAT_MODEL.md), and the decisions and their trade-offs as ADRs in [DECISIONS.md](DECISIONS.md).

## Design trade-offs

**Isolation over integration.** Keeping the service a strict sink costs the ability to model "notified" as part of a payment's state. That is the correct trade: the isolation is what guarantees a notification failure cannot reach money.

**Two idempotency layers over one.** Database uniqueness alone would be simpler but would leave the send/commit crash window open. The provider key adds a small dedup store to each provider and a key on the send contract, and in exchange the correctness story is honest rather than resting on a transaction pretending to include a network call.

**One ingest endpoint for two topics.** Both subscriptions deliver to one `/events/pubsub`, which routes on event type. A third topic later is one more subscription, not new application code.

**Authenticated push, public reads.** The ingest endpoint is protected because it is the only surface that does work: the push subscription attaches a Google OIDC token minted for a dedicated service account, and the endpoint verifies it before applying an event. The read and health endpoints stay public, safe because the service holds nothing that can move money and exposes only its own delivery records.
