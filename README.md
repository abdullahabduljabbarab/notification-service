# Notification Service

[![CI](https://github.com/abdullahabduljabbarab/notification-service/actions/workflows/ci.yml/badge.svg)](https://github.com/abdullahabduljabbarab/notification-service/actions/workflows/ci.yml)
[![Terraform](https://github.com/abdullahabduljabbarab/notification-service/actions/workflows/terraform.yml/badge.svg?branch=main)](https://github.com/abdullahabduljabbarab/notification-service/actions/workflows/terraform.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Downstream notification delivery for the ABS platform. The service consumes committed payment and risk events and turns the customer-facing ones into messages on simulated email and SMS channels. It is a **strict sink**: it never writes to or blocks financial state, so a notification failure, a provider outage, or the service being down cannot change a payment's outcome. Delivery is idempotent per `(event_id, channel)` so at-least-once redelivery never double-notifies a customer, and each external send carries a deterministic idempotency key that closes the crash window between a provider succeeding and the delivery being recorded. It is deployed on Google Cloud Run, its infrastructure is captured in Terraform, and it ships through a keyless CI pipeline that authenticates with Workload Identity Federation.

It is one service in [ABS Financial Systems](https://github.com/abdullahabduljabbarab/abs-financial-systems): the ledger owns money, the orchestrator owns the payment lifecycle, the risk engine owns decisioning, and the notification service owns the downstream side effects. Pub/Sub moves facts between them.

**Live service**

- Interactive API reference (Swagger UI): https://notification-service-eppidgbmxa-nw.a.run.app/docs
- Health probe: https://notification-service-eppidgbmxa-nw.a.run.app/health

![Swagger overview](docs/images/01-swagger-overview.png)

---

## Contents

- [What it does](#what-it-does)
- [Strictly downstream](#strictly-downstream)
- [Per-channel idempotency](#per-channel-idempotency)
- [The delivery lifecycle](#the-delivery-lifecycle)
- [Architecture](#architecture)
- [Verification and evidence](#verification-and-evidence)
- [Running it locally](#running-it-locally)
- [Design decisions](#design-decisions)
- [Project layout](#project-layout)

---

## What it does

The service reacts to events the rest of the ecosystem has already committed and notifies the customer about the ones they would care about. Fan-out is a pure function of the event: `payment.settled` and `payment.failed` notify by email and SMS, `payment.rejected` by email, and `risk.evaluated` by email only when the decision is `review` or `block`. Events a customer would not care about (a payment being received or approved, a risk `allow`) are consumed and produce nothing, so every event is accounted for without noise.

A payment that settled, notified on both channels. One `payment.settled` event fans out to an email and an SMS, each a delivery in its own right, both derived from the account and both delivered:

![Settled fan-out to email and SMS](docs/images/02b-settled-fanout.png)

A payment held for review, notified by email, and this one shows the delivery guarantees. The simulated provider failed the first attempt, so the endpoint returned a non-2xx, Pub/Sub redelivered, and the second attempt sent. The delivery records every attempt and carries the originating `correlation_id`, so it stays part of the same end-to-end trace as the payment and the risk decision that caused it:

![A review notification delivered after a retry](docs/images/02-notification-delivered.png)

---

## Strictly downstream

There is no upstream path out of this service. It subscribes to `payment-events` and `risk-events`, and it has no client for any other service and no way to write financial state.

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

Its whole engineering thesis is one system property: **the financial platform keeps working even if every notification in the system is on fire.** A notification that fails, retries to exhaustion, or takes the service down must not touch financial state or the payment lifecycle (ABS-REQ-006). This is why the customer-facing payment events carry `account_id`: a downstream consumer must be able to tell who to notify from the event alone, never by calling back into the orchestrator, which would break the isolation. The service was proven live to be exactly this passive: while it held payments in `risk_review` and delivered their notifications, no money moved on their account, and a settled payment's money moved regardless of what its notifications did.

---

## Per-channel idempotency

Delivery is deduplicated on the pair `(event_id, channel)`, not on `event_id` alone, which is the service's distinct correctness property. One event legitimately fans out to more than one channel, but Pub/Sub's at-least-once redelivery must not send the same channel twice. A redelivery finds each channel's delivery already recorded and re-drives only the ones not yet terminal.

Database uniqueness alone is not enough, and the service does not pretend it is. A transaction cannot span an external send, so there is a window where a provider succeeds but the process dies before the delivery is recorded; the event is then redelivered and the send would repeat. Every send therefore carries a deterministic provider idempotency key, `notification:{event_id}:{channel}`. A provider that has already succeeded for a key returns the original result instead of sending again, so the redelivery produces no second message. A failed send is not recorded against the key, because it produced no side effect and must be genuinely retried.

```
app-level:       UNIQUE (event_id, channel)              prevents duplicate delivery rows
provider-level:  key notification:{event_id}:{channel}   prevents a duplicate side effect
                 ────────────────────────────────────────
                 together: safe redelivery across the send/commit crash window
```

This mirrors the ledger and orchestrator discipline: correctness never rests on a database transaction pretending to include something it cannot.

---

## The delivery lifecycle

Each `(event_id, channel)` is a delivery that moves through a small state machine, and every send is recorded as an attempt, so the history is fully visible in the read API.

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

A retryable failure returns a non-2xx from the push endpoint, so Pub/Sub redelivers the event and the service retries only the channels that have not yet delivered. Once every channel for an event is terminal, the endpoint returns 2xx and the message is acknowledged.

There are two distinct kinds of dead-lettering, and the design keeps them separate. `DEAD_LETTERED` is an application state on one channel's delivery, reached after the attempt limit: the service gives up on a channel a provider keeps refusing, but the message is still fully handled and still acked. The Pub/Sub dead-letter topic is a transport-level backstop for a different failure: the service cannot process a message at all (it is crashing, the database is down, the envelope is invalid). Channel exhaustion is handled in-band; the broker DLQ is for the service being unable to do its job.

---

## Architecture

```mermaid
flowchart TD
    O[Payment Orchestrator] -->|payment.settled / failed / rejected| PE[Pub/Sub payment-events]
    R[Risk Engine] -->|risk.evaluated| RE[Pub/Sub risk-events]

    PE -->|push subscription| E[POST /events/pubsub]
    RE -->|push subscription| E
    E --> S[Service: fan-out, per-channel dedup, provider-key send]
    S --> DB[(PostgreSQL 16 / Cloud SQL)]
    S --> CH[Simulated email and SMS channels]
    DB --> API[GET /notifications/payment_id]

    subgraph GCP [Google Cloud, europe-west2]
        E
        S
        DB
        CH
        API
    end
```

FastAPI and Pydantic handle validation, routing and the OpenAPI spec. SQLAlchemy and Alembic own the schema and migrations. The service keeps its own state (`notification_deliveries`, `notification_attempts`) in its own `notify` database and user on the ledger's shared Cloud SQL instance, and never touches another service's tables.

![Cloud Run service](docs/images/04-cloud-run.png)

The full GCP stack is captured as code in [`terraform/`](terraform/): the database and user, Artifact Registry, the injected secret, the Cloud Run service, a push identity, the dead-letter topic, and a push subscription on each of the two upstream topics.

| Its own database | Secret injected, not plaintext | Image from the keyless pipeline |
|---|---|---|
| ![Cloud SQL databases](docs/images/06-cloudsql-databases.png) | ![Secret Manager](docs/images/07-secret-manager.png) | ![Artifact Registry](docs/images/08-artifact-registry.png) |

**It fans in from both upstream services.** Two push subscriptions, one on the orchestrator's `payment-events` and one on the risk engine's `risk-events`, deliver to the single `/events/pubsub` endpoint, which routes on the event type and does not care which topic a message came from.

![Push subscriptions](docs/images/05-push-subscriptions.png)

Because the ingest endpoint is the only surface that does work, it is authenticated: Pub/Sub attaches a Google OIDC token minted for a dedicated push service account, and the consumer verifies it before applying any event, so only authenticated deliveries reach it. The read and health endpoints are public by scope decision. An unauthenticated call to the ingest endpoint is refused:

![Ingress authentication rejects an unauthenticated call](docs/images/03-push-auth-401.png)

**Deployment is keyless.** GitHub Actions lints, runs the full suite against a PostgreSQL service container built from the Alembic migrations, and validates the Terraform. On green it builds the image, pushes to Artifact Registry and deploys to Cloud Run, authenticating through **Workload Identity Federation**: GitHub presents a short-lived OIDC token that GCP exchanges to impersonate a repository-scoped deploy service account, so no long-lived key is stored in the repository.

| Keyless identity (WIF) | Only this repo can deploy |
|---|---|
| ![Workload Identity Federation](docs/images/09-workload-identity.png) | ![Deploy service account bound to the repo](docs/images/10-deploy-service-account.png) |

---

## Verification and evidence

54 automated tests cover the fan-out routing, the channel providers and their idempotency, the delivery state machine, the apply-event service, the persistence, and the API. The suite builds its schema from the Alembic migrations, so the ORM and the migrations are exercised together, not only apart. On top of the unit tests the service was proven against its live deployment and the rest of the ecosystem.

**The full ecosystem loop, live.** A payment created at the orchestrator flows through risk decisioning, and the customer-facing outcome flows over Pub/Sub to this service, which delivers it:

- A **settled** payment fanned out to an **email and an SMS**, both delivered, both derived from the account, both carrying the payment's `correlation_id`.
- A payment held for **review** was notified by **email**, and the delivery survived a simulated provider failure: attempt one failed, the endpoint returned a non-2xx, Pub/Sub redelivered, and attempt two delivered. Only the failed channel retried.
- Throughout, the held payments stayed in `risk_review` with no money moved, and the settled payment's money moved regardless of its notifications, the sink property demonstrated rather than asserted.

```
orchestrator payment  ->  settled     (email + sms delivered)
orchestrator payment  ->  risk_review (email delivered after 1 retry, no money moved)
correlation_id matches across the orchestrator, the risk engine and the notification
```

**Requirement to evidence:**

| Requirement | How it is verified |
|-------------|--------------------|
| Notification failure never touches financial state (ABS-REQ-006) | strict-sink design (no write path, no broker client); live payments settled and were held with their notifications failing and retrying independently |
| Consumers tolerate duplicate delivery (ABS-REQ-008) | `(event_id, channel)` uniqueness plus the provider idempotency key; `test_redelivery_does_not_send_again`, `test_crash_between_send_and_commit_does_not_double_send` |
| One correlation id across services (ABS-REQ-009) | every delivery carries the event's `correlation_id`; matched live across all three services |
| Exactly-once external side effect across the crash window | deterministic provider key; `test_crash_between_send_and_commit_does_not_double_send` |
| Bounded retry then dead-letter | `test_a_failed_channel_signals_retry_then_succeeds`, `test_a_channel_that_keeps_failing_is_dead_lettered`; retry shown live |
| The ORM and the migration agree | the persistence, service and API tests run against the Alembic-migrated schema (ADR-014) |

The design, requirements, decisions and build history are in [`docs/`](docs/): [`DESIGN.md`](docs/DESIGN.md), [`REQUIREMENTS.md`](docs/REQUIREMENTS.md), [`MVP.md`](docs/MVP.md), [`DECISIONS.md`](docs/DECISIONS.md) and [`PRODUCTION_LOG.md`](docs/PRODUCTION_LOG.md).

---

## Running it locally

Requires Docker and Python 3.12.

```bash
# 1. start PostgreSQL
docker compose up -d

# 2. install dependencies
pip install -r requirements.txt

# 3. point the app at the database and apply migrations
export DATABASE_URL=postgresql://notify:notify@localhost:5435/notify
alembic upgrade head

# 4. run the service
uvicorn app.main:app --reload
```

Then open http://localhost:8000/docs for Swagger. With `PUBSUB_PUSH_SA` unset, the ingest endpoint is open, so you can post a Pub/Sub-shaped envelope to `/events/pubsub` and watch a delivery appear via `/notifications/{payment_id}`, no cloud credentials needed. The channels are simulated, so retry and dead-letter can be exercised with the configured failure rates.

**Running the tests** (the suite creates its own test database and migrates it):

```bash
export TEST_DATABASE_URL=postgresql://notify:notify@localhost:5435/notify_test
pytest
```

---

## Design decisions

**A strict sink.** The service consumes committed events and produces messages, with no path that writes to or blocks any financial system. A notification failure can never change a financial outcome. This is the point of the service, not a limitation of it.

**Idempotency in two layers.** Database uniqueness on `(event_id, channel)` stops duplicate delivery rows; a deterministic provider idempotency key stops a duplicate customer-facing send across the crash window a transaction cannot cover. The genuinely new idea this service contributes to ABS is reliable, idempotent external side effects that can fail independently of the system that caused them.

**Pure fan-out.** Which channels an event notifies, and the message on each, is a pure function of the event, so the whole policy is unit-testable without a database or network and the same event always plans the same notifications.

**Keyless deploy.** Workload Identity Federation removes the long-lived service-account key from CI entirely; GitHub proves its identity with a short-lived token, scoped to this repository.

More decisions and their trade-offs are in [`docs/DECISIONS.md`](docs/DECISIONS.md).

---

## Project layout

```
app/            FastAPI application, routing, channels, delivery state machine, service, consumer
migrations/     Alembic migration (deliveries, attempts)
terraform/      Notification service infrastructure as code, including the keyless WIF deploy identity
tests/          54 tests: routing, channels, status, service, persistence, API
docs/           Design, requirements, MVP, decisions, build log, evidence
```

---
