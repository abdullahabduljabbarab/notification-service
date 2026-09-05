# Architecture Decision Records

Decisions specific to the notification service. Ecosystem-wide decisions (the
authoritative ledger, the transactional outbox pattern, Workload Identity
Federation for CI) are recorded in the services that own them and are reused
here rather than re-argued.

## ADR-001: The service is a strict sink, never a source

**Status:** Accepted

**Context:** The notification service reacts to payment and risk events. It
would be easy to let it grow a write path back into financial state (marking a
payment "customer notified", say, or gating a settlement on a delivery).

**Decision:** The service is strictly downstream. It consumes committed events
and produces messages. It has no path that writes to or blocks any financial
system, and it never will. Its only persistent state is its own delivery
records.

**Consequences:** A notification failure, a provider outage, or the service
being entirely down cannot change a financial outcome (ABS-REQ-006). This costs
us the ability to model "notified" as part of a payment's state, which is the
correct trade: that concern belongs upstream if it belongs anywhere. The
isolation is the feature.

## ADR-002: Idempotency is keyed on (event_id, channel), not event_id

**Status:** Accepted

**Context:** Transport is at-least-once; the same event arrives more than once.
A single event fans out to multiple channels, and those channels succeed and
fail independently.

**Decision:** The unit of idempotency and of delivery is the pair
(event_id, channel), not the event alone. Dedup, retry, and terminal state are
all per pair.

**Consequences:** Redelivering an event re-drives only the channels that are not
yet terminal. An email that already delivered is never re-sent because its SMS
is still retrying. This is the service's distinct correctness property and the
reason event-level dedup would be wrong here. (ABS-REQ-008)

## ADR-003: Fan-out is a pure function of the event

**Status:** Accepted

**Context:** Which channels an event notifies, and the message on each, is
policy. Policy embedded in handlers and database calls is hard to test and easy
to drift.

**Decision:** Routing is a pure function: `plan_notifications(event_type,
payload)` returns the planned notifications with no I/O. The service layer takes
that plan and persists and delivers it.

**Consequences:** Every fan-out rule is unit-testable without a database or
network, and the full policy is readable in one file. The same event always
plans the same notifications, which is what makes redelivery safe to reason
about.

## ADR-004: Bounded retry then dead-letter, decided by a pure state machine

**Status:** Accepted

**Context:** A channel send can fail transiently. Retrying forever pins a
channel against a destination that may never accept it; not retrying at all
loses recoverable sends.

**Decision:** A small pure function, `next_status(outcome, attempt_count)`,
owns the transition. A success is delivered; a failure below the attempt limit
is retryable; a failure at the limit is dead-lettered. Delivered and
dead-lettered are terminal.

**Consequences:** The retry policy is one testable function with no dependencies,
separate from persistence and from the channels. Changing the attempt limit is a
one-line change with a test. Dead-lettering is an explicit, visible terminal
state, not a silently abandoned row.

## ADR-005: Simulated channels, deterministic in tests and seeded live

**Status:** Accepted

**Context:** Real email and SMS integration is out of scope, but the service's
whole point is behaviour under unreliable delivery. We still need to prove retry
and dead-letter, both in tests and in a live demonstration.

**Decision:** Two channel implementations behind one protocol. A scripted
channel plays a fixed list of outcomes for deterministic tests. A simulated
channel fails a configurable fraction of sends from a seeded RNG for the live
service.

**Consequences:** Tests are deterministic and need no network. The deployed
service visibly exercises retry and dead-letter without any external provider or
cost. Swapping in a real provider later is implementing the same protocol; no
lifecycle or routing code changes.

## ADR-006: No client library for the message broker

**Status:** Accepted

**Context:** The service ingests events from Pub/Sub. The risk engine and
orchestrator carry the `google-cloud-pubsub` client for their outbox publishers.

**Decision:** The notification service takes ingest as an authenticated HTTP
push, handled by the web framework directly. It declares no message-broker
client dependency, because it only receives and never publishes.

**Consequences:** A smaller dependency surface and no gRPC client to run inside
Cloud Run. Push authentication is verified at the application layer, consistent
with the risk engine's ingress. The service has no outbound broker path, which
matches its sink-only design (ADR-001).
