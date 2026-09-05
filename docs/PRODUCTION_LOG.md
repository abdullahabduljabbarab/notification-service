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

**Tests:** 25, all against the pure core with no database or network.
- `test_routing.py`: each event's fan-out (settled and failed to email + SMS,
  rejected to email, risk review/block to email, risk allow and uninteresting
  events to nothing, missing account to nothing), message rendering, and
  destination derivation.
- `test_channels.py`: scripted outcomes in order and run-off-the-end behaviour,
  simulated channel at failure rates 0.0 and 1.0, determinism for a fixed seed,
  and reference prefixing.
- `test_status.py`: every transition (sent to delivered at any attempt, failure
  below the limit to retryable, failure at the limit to dead-lettered, a
  configurable limit) and the terminal set.

**Decisions recorded:** ADR-001 (strict sink), ADR-002 ((event_id, channel)
idempotency), ADR-003 (pure fan-out), ADR-004 (bounded retry state machine),
ADR-005 (simulated channels), ADR-006 (no broker client).

**State:** `ruff check` clean, 25 tests passing. No infrastructure yet; that is
Milestone 2 onward.
