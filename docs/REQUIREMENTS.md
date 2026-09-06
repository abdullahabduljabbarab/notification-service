# Notification Service: Requirements

The notification service is the ecosystem's outbound edge. It consumes events
that other services have already committed and turns the interesting ones into
customer-facing messages over simulated email and SMS channels. It holds no
financial state, makes no financial decisions, and can never move money. Its
job is to notify, reliably and without ever endangering the systems upstream of
it.

These requirements are the contract the rest of the build is measured against.
The system-wide requirements (ABS-REQ-XXX) live in the ecosystem repository;
the ones this service is accountable for are named where they apply.

## Functional requirements

### FR-1: Consume committed events only
The service reacts to events that upstream services have already durably
recorded (payment settled, payment failed, payment rejected, risk evaluated).
It never participates in producing those outcomes and never sits on the path
that decides them. It is strictly downstream. (ABS-REQ-006)

### FR-2: Deterministic fan-out
For a given event type and payload, the set of notifications produced is a
pure function of that event. `payment.settled` and `payment.failed` notify by
email and SMS; `payment.rejected` notifies by email; `risk.evaluated` notifies
by email only when the decision is `review` or `block`. Events a customer would
not care about (a payment received or approved, a risk `allow`) produce nothing.
The same event always plans the same notifications.

### FR-3: Per-channel delivery with independent outcomes
Each planned notification is delivered on its own channel and succeeds or fails
independently. An email failing does not stop the SMS, and vice versa. Each
delivery is tracked in its own right.

### FR-4: Idempotent consumption keyed on (event_id, channel)
The transport is at-least-once, so the same event can arrive more than once.
A delivery is uniquely identified by its event id and its channel. Redelivery
of an event that has already been delivered on a channel must not send a second
message on that channel. This holds in two layers: a database uniqueness
constraint on (event_id, channel) prevents duplicate delivery rows, and a
provider idempotency key derived from (event_id, channel) prevents a duplicate
customer-facing send across the crash window between a provider succeeding and
the delivery being recorded (see FR-8). (ABS-REQ-008)

### FR-5: Bounded retry, then dead-letter
A channel send that fails is retryable up to a fixed attempt limit. Once the
limit is reached the delivery is dead-lettered so the service stops retrying a
channel that will not succeed. A delivery is terminal once it is delivered or
dead-lettered; a message is fully handled once every channel it planned is
terminal.

### FR-6: Read model for delivery history
The service exposes, per payment, the deliveries it produced and their current
status, so an operator or an upstream service can see what was sent, on which
channel, and whether it succeeded. This is the service's audit surface.

### FR-7: Correlation continuity (ABS-REQ-009)
Every delivery preserves the originating event's correlation id, so a
notification remains part of the same end-to-end trace as the payment and risk
decision that caused it. A single correlation id joins the record across every
service it touched, this one included.

### FR-8: Exactly-once external side effects across the crash window
A database transaction cannot span an external send. Each provider send
therefore carries a deterministic idempotency key derived from
(event_id, channel). Repeating a send that has already succeeded returns the
original provider result rather than producing a second customer-facing message;
a send that failed produced no side effect and is genuinely retried. This closes
the window between a provider succeeding and the local delivery being committed.

## Non-functional requirements

### NFR-1: Isolation from financial state (ABS-REQ-006)
This is the service's defining property. A failure inside the notification
service (a provider outage, an exhausted retry, the service being down) must
never propagate back into the ledger, the orchestrator, or the risk engine, and
must never change a financial outcome. Notifications are a consequence of
committed state, never a precondition for it. If this service is dark, money
still moves correctly; only the messages are delayed.

### NFR-2: Explainable delivery
Every delivery records why it exists (the event that caused it), what was
attempted (channel, destination, each attempt and its outcome), and where it
ended (its terminal status). The history is enough to answer "was this customer
told, and if not, why not" without inspecting logs. This is a local property of
the notification service; the ecosystem's explainable-decision requirement
(ABS-REQ-015) is owned by the risk engine and not claimed here.

### NFR-3: Deterministic core, simulated edges
The routing and lifecycle logic is deterministic and unit-testable without a
database or network. The channels themselves are simulated: a scripted provider
for tests and a seeded probabilistic provider for live demonstration of retry
and dead-letter behaviour. Real email and SMS integration is out of scope.

### NFR-4: Authenticated ingress
The event-ingest endpoint (`POST /events/pubsub`) is only reachable by the
platform's own push subscription. Pub/Sub attaches an OIDC token for a dedicated
push service account, and the handler verifies its issuer, audience, and
identity at the application layer before doing any work. The read API and health
check expose nothing privileged and stay public. (This is a local security
property, not one of the numbered ecosystem requirements; delivered in the
deployment milestone.)

## Out of scope

- Real email or SMS provider integration.
- Customer preference management, unsubscribe, or quiet hours.
- Templating beyond a single rendered line per event.
- Any write path back into financial state. There is none, by design.
