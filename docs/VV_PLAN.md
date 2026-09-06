# Verification and Validation Plan

## Approach

Every requirement the notification service owns is verified by an automated test, and every behaviour that can be driven deterministically is additionally proven against the live Cloud Run deployment. CI runs the full suite (54 tests) against a PostgreSQL service container on every push, and the suite builds its schema by running the Alembic migrations, so the ORM and the migrations are exercised together rather than only apart (ADR-014). The requirements are the ABS system requirements the service is accountable for, defined in [SYSTEM_REQUIREMENTS.md](https://github.com/abdullahabduljabbarab/abs-financial-systems/blob/main/SYSTEM_REQUIREMENTS.md).

## Requirement-to-Test Mapping

| Requirement | Verification | Test / Evidence |
|-------------|-------------|-----------------|
| ABS-REQ-006 (notification failure never touches financial state) | By design + Live | Strict-sink: no write path to any financial system and no broker client anywhere in the service; live, a payment was held in `risk_review` and another settled, both with their notifications failing and retrying independently and neither affecting the payment |
| ABS-REQ-008 (consumers tolerate duplicate delivery) | Automated + Live | `test_redelivery_does_not_send_again`, `test_push_is_idempotent_across_redelivery`, `test_crash_between_send_and_commit_does_not_double_send`; live, redelivered events produced no duplicate sends |
| ABS-REQ-009 (one correlation_id across services) | Automated + Live | `test_delivery_carries_the_correlation_id`; live, a delivery's correlation_id matched the orchestrator payment's and the risk decision's |
| Exactly-once external side effect across the crash window | Automated | `test_crash_between_send_and_commit_does_not_double_send`, `test_repeat_send_with_same_key_does_not_send_again` |

## Behavioural Coverage

| Behaviour | Verification | Test / Evidence |
|-----------|-------------|-----------------|
| Fan-out is exact per event type | Automated | `test_settled_fans_out_to_email_and_sms`, `test_failed_fans_out_to_email_and_sms`, `test_rejected_is_email_only`, `test_risk_review_notifies_by_email`, `test_risk_block_notifies_by_email` |
| Uninteresting events produce nothing | Automated | `test_risk_allow_produces_nothing`, `test_uninteresting_events_produce_nothing`, `test_uninteresting_event_is_ignored` |
| An event without account_id is ignored, not crashed | Automated | `test_event_without_account_produces_nothing`, `test_event_without_account_is_ignored_not_crashed` |
| Destinations derive from the account, messages render | Automated | `test_destinations_are_derived_from_the_account`, `test_message_carries_the_payment_id` |
| Channels report outcomes; simulated failure is deterministic | Automated | `test_scripted_channel_follows_its_script_in_order`, `test_simulated_channel_never_fails_at_zero_rate`, `test_simulated_channel_always_fails_at_full_rate`, `test_simulated_channel_is_deterministic_for_a_seed` |
| A send is idempotent on its provider key | Automated | `test_provider_key_is_deterministic_per_event_and_channel`, `test_repeat_send_with_same_key_does_not_send_again`, `test_a_failed_send_is_retried_for_real_under_the_same_key`, `test_simulated_channel_is_also_idempotent_on_key` |
| The delivery state machine transitions correctly | Automated | `test_a_sent_message_is_delivered`, `test_a_failure_below_the_limit_is_retryable`, `test_a_failure_at_the_limit_is_dead_lettered`, `test_the_attempt_limit_is_configurable`, `test_delivered_and_dead_lettered_are_terminal`, `test_pending_and_retryable_are_not_terminal` |
| An event applies to durable deliveries with per-channel dedup | Automated | `test_settled_delivers_on_both_channels`, `test_risk_review_delivers_one_email`, `test_redelivery_does_not_send_again` |
| A failing channel retries then succeeds; a persistent failure dead-letters | Automated | `test_a_failed_channel_signals_retry_then_succeeds`, `test_a_channel_that_keeps_failing_is_dead_lettered`, `test_dead_lettered_delivery_has_its_attempts_recorded` |
| The (event_id, channel) constraint and the foreign key hold | Automated | `test_same_event_and_channel_cannot_be_stored_twice`, `test_same_event_fans_out_to_two_channels`, `test_an_attempt_needs_a_real_delivery`, `test_deleting_a_delivery_cascades_to_its_attempts` |
| The push consumer, the 503 retry signal, and the read API | Automated | `test_push_delivers_and_read_api_shows_it`, `test_push_returns_503_when_a_channel_wants_retry`, `test_push_ignores_an_uninteresting_event`, `test_read_api_is_empty_for_an_unknown_payment`, `test_health_ok` |
| The ingest endpoint requires authentication | Automated + Live | `test_push_requires_authentication_when_configured`; live, an unauthenticated call returns 401 |
| Bad envelopes are rejected | Automated | `test_push_rejects_an_invalid_envelope`, `test_push_rejects_missing_message_data` |
| The ORM and the migration agree on the schema | Automated | the persistence, service and API tests all run against the Alembic-migrated schema |
| Lint and infrastructure validity | CI evidence | ruff on push; `terraform fmt`, `init`, `validate` in the Terraform workflow |

## Live Verification

Driven against the deployed service and the rest of the live ecosystem:

- `GET /health` returns 200 with the database connected, and migrations run on container start.
- An unauthenticated `POST /events/pubsub` returns 401; both push subscriptions (on `payment-events` and `risk-events`) deliver to the endpoint.
- A **settled** payment created at the orchestrator fanned out over Pub/Sub to an email and an SMS delivery, both delivered, both with the account-derived destination and the payment's correlation_id.
- A **review** payment was notified by email, and the delivery survived a simulated provider failure: attempt one failed, the endpoint returned 503, Pub/Sub redelivered, and attempt two delivered, with only the failed channel retried.
- Throughout, the held payment stayed in `risk_review` with no money moved and the settled payment's money moved regardless of its notifications, the isolation property demonstrated rather than asserted.

## Acceptance Criteria

The notification service passes V&V when:

- Every ABS requirement it owns has an automated test, and every deterministic behaviour is additionally shown live.
- CI is green: ruff, the full suite against the Alembic-migrated PostgreSQL schema, and Terraform validation.
- The live `/health` returns 200, events fan out to the right channels with per-channel idempotency, a failing channel retries and then dead-letters, and no notification behaviour, in success or failure, changes a payment's outcome.
