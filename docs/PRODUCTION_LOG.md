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

## Milestone 2: Persistence

**Goal:** Durable delivery records and their attempt history, with the schema
built by a migration and the tests run against that migrated schema so the ORM
and the migration cannot silently drift apart.

**Built:**
- `app/models.py`: two tables.
  - `notification_deliveries`: one row per (event_id, channel), unique on that
    pair, which is the idempotency key that makes redelivery safe. Columns for
    the routing context (payment_id, account_id, channel, destination), the
    lifecycle (status, attempt_count, provider_reference, delivered_at), and the
    trace (correlation_id, ABS-REQ-009). `status` is a plain string, not a
    database enum, following the orchestrator's one live enum failure. An index
    on payment_id backs the read API.
  - `notification_attempts`: one row per send attempt (attempt_number, outcome,
    error, attempted_at), foreign-keyed to its delivery with an ON DELETE
    CASCADE and an index on delivery_id. This is the audit trail the read API
    exposes.
- `app/database.py`: engine and session factory (`pool_pre_ping`), and a
  `get_db` dependency, matching the other services.
- `alembic.ini`, `migrations/env.py`, `migrations/versions/001_initial.py`: the
  migration that creates both tables, their unique constraint, foreign key, and
  indexes. `env.py` prefers a harness-supplied URL, else the app's.
- `tests/conftest.py`: self-bootstrapping test database on port 5435
  (`notify_test`), dropped and re-migrated before each test via
  `alembic upgrade head`, so every test runs against the migrated schema
  (ADR-014). No app/client fixture yet; that arrives with the API in M3.

**Tests:** +6 persistence tests (35 total).
- A delivery persists and reads back with its defaults (pending, attempt_count
  0, created_at set, delivered_at null).
- The (event_id, channel) unique constraint rejects a duplicate on the same
  channel and allows the same event on a second channel (the fan-out case).
- Attempts are recorded in order under a delivery and read back through the
  relationship.
- An attempt against a non-existent delivery is rejected by the foreign key.
- Deleting a delivery cascades to its attempts.

**State:** `ruff check` clean, 35 tests passing, PostgreSQL 16 via
docker-compose on port 5435. Schema is created by the migration and validated by
the ORM in the same run. Next: M3, the service layer that applies an event to
deliveries with per-channel dedup and provider-key sends, plus the API.
