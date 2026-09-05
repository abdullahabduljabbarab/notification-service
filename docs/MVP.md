# Notification Service: MVP

## Thesis

Prove that a downstream service can react to committed financial events,
deliver customer notifications reliably over unreliable channels, and remain so
isolated that nothing it does can ever touch financial state. The interesting
engineering is not the sending; it is the boundary and the delivery guarantees
around it.

## In scope

1. **Deterministic fan-out** from event to notifications (email, SMS), one
   delivery per channel, decided purely from the event.
2. **Per-(event_id, channel) idempotency**, so at-least-once delivery of the
   same event never double-notifies a customer on a channel.
3. **A delivery lifecycle**: pending, delivered, failed-retryable, dead-lettered,
   with a bounded attempt limit and a per-attempt audit trail.
4. **Simulated channels**: a scripted provider for deterministic tests and a
   seeded probabilistic provider so retry and dead-letter can be shown live.
5. **A small API**: a Pub/Sub push consumer for ingest, a read endpoint for a
   payment's delivery history, and a health check.
6. **Keyless deployment** to Cloud Run behind an authenticated push
   subscription, consuming both payment and risk event topics.

## Out of scope

- Real provider integration, delivery receipts, customer preferences.
- Any path that writes to or blocks financial state.
- Rich templating, localisation, batching, or scheduling.

## Milestones

- **M1: Delivery core.** Pure fan-out, channel providers, and the delivery
  state machine, with unit tests and the design-time docs. No database, no
  network. (This milestone.)
- **M2: Persistence.** `notification_deliveries` and `notification_attempts`
  models, an Alembic migration, and tests running against the migrated schema.
- **M3: Service and API.** Apply an event to durable deliveries with per-channel
  dedup; the Pub/Sub push consumer, the read endpoint, and health.
- **M4: Deployment.** Dockerfile, CI against the migrated schema, and Terraform:
  Cloud Run, Cloud SQL, push subscriptions on the payment and risk topics with a
  dead-letter, and Workload Identity Federation for keyless CI.
- **M5: Evidence.** README, live evidence of fan-out, dedup, retry, and
  dead-letter, and the production log.

## Definition of done

- Fan-out and lifecycle are deterministic and covered by tests that need no
  database or network.
- Redelivery of an event never produces a second send on an already-delivered
  channel, proven by a test and shown live.
- A failing channel retries to its limit and then dead-letters, proven by a test
  and shown live with the seeded provider.
- The service can be down or failing without any effect on a payment's outcome.
- CI runs the suite against the Alembic-migrated schema, and deployment uses no
  long-lived key.
