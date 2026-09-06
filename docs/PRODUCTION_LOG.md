# Production Log

A running record of what was built, in what order, and why. Newest last.

## Milestone 1: Delivery core

**Goal:** The deterministic heart of the service, with no database and no
network, so the fan-out and delivery guarantees are proven before any
infrastructure exists.

**Built:**
- `app/channels.py`: the channel abstraction. A `ChannelProvider` protocol over
  `Channel` (email, SMS), returning a `SendResult` (`SENT` or `FAILED`). Two
  implementations: `ScriptedChannel` (a fixed deque of outcomes, deterministic)
  and `SimulatedChannel` (a seeded RNG failing a configurable fraction of sends).
  Every send carries a deterministic provider idempotency key
  (`notification:{event_id}:{channel}`): a provider that has already succeeded
  for a key returns the original result instead of sending again, closing the
  crash window between a send succeeding and the delivery being committed. A
  failed send is not recorded against the key and is genuinely retried.
- `app/routing.py`: `plan_notifications(event_type, payload)`, a pure function
  from event to the notifications it should produce. Payment events fan out per
  the fan-out table; `risk.evaluated` notifies only on `review`/`block`; events
  a customer would not care about, or events without an account, produce nothing.
- `app/status.py`: the delivery state machine. `next_status(outcome,
  attempt_count)` maps a send outcome and attempt count to pending, delivered,
  failed-retryable, or dead-lettered, with a bounded attempt limit. Delivered and
  dead-lettered are terminal.
- `app/config.py`: environment configuration (database URL on port 5435 to sit
  alongside the other services locally, simulated failure rates, environment).
- Scaffold: `requirements.txt`, `pyproject.toml` (ruff + pytest), `.gitignore`,
  `docker-compose.yml`, `LICENSE`.
- `docs/`: `DESIGN.md`, `REQUIREMENTS.md`, `MVP.md`, `DECISIONS.md`, and this log.

**Tests:** 29, all against the pure core with no database or network.
- `test_routing.py`: each event's fan-out (settled and failed to email + SMS,
  rejected to email, risk review/block to email, risk allow and uninteresting
  events to nothing, missing account to nothing), message rendering, and
  destination derivation.
- `test_channels.py`: scripted outcomes in order and run-off-the-end behaviour,
  simulated channel at failure rates 0.0 and 1.0, determinism for a fixed seed,
  reference prefixing, and provider idempotency: a repeat send under the same
  key does not send again (the crash-window regression at provider level), a
  failed send is retried for real under the same key, and the simulated provider
  is idempotent too.
- `test_status.py`: every transition (sent to delivered at any attempt, failure
  below the limit to retryable, failure at the limit to dead-lettered, a
  configurable limit) and the terminal set.

**Decisions recorded:** ADR-001 (strict sink), ADR-002 ((event_id, channel)
idempotency), ADR-003 (pure fan-out), ADR-004 (bounded retry state machine),
ADR-005 (simulated channels), ADR-006 (no broker client), ADR-007 (deterministic
provider idempotency keys for external side effects), ADR-008 (application
dead-lettering distinct from the transport DLQ).

**Design corrections applied before M2:** softened the "(event_id, channel)
makes a double send impossible" claim to the honest two-layer story (DB
uniqueness plus provider idempotency across the crash window); documented the
distinction between application `DEAD_LETTERED` and the Pub/Sub transport DLQ;
made authenticated Pub/Sub push explicit (`/events/pubsub` verifies the OIDC
identity, read/health stay public); fixed requirement mappings (ABS-REQ-006
isolation, ABS-REQ-008 duplicate tolerance, ABS-REQ-009 correlation continuity;
ABS-REQ-015 is the risk engine's and is not claimed here).

**State:** `ruff check` clean, 29 tests passing. No infrastructure yet; that is
Milestone 2 onward. The full crash-after-send-before-commit regression through
the service and Pub/Sub redelivery lands in M3, once the service layer and
persistence exist; the provider-level primitive and its unit test are in place
now.
