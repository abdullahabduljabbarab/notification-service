# Service Level Objectives

The notification service is not on a synchronous money path: nothing waits on it, and its work happens off the request, driven by Pub/Sub. Its request-facing surface is the read API (`GET /notifications/{payment_id}`) and health, and a read is a single indexed query on `payment_id` with its attempts, no cross-service calls. So the SLOs below are for that read path; the event-processing path is measured by its correctness (per-channel idempotency, retry, dead-letter), not by a request latency.

## Defined SLOs

| Metric | Target | Measurement |
|--------|--------|-------------|
| Availability | 99.5% | Percentage of non-5xx responses during load test |
| Read p50 | < 100ms | Median for `GET /notifications/{payment_id}` |
| Read p95 | < 200ms | 95th percentile for `GET /notifications/{payment_id}` |
| Health p50 | < 100ms | Median for `GET /health` |
| Error rate | < 1% | Percentage of 5xx responses |
| Throughput | > 5 req/s sustained | Aggregate under 5 concurrent users |
| Delivery integrity | exactly-once per channel | A redelivered event never produces a second send on a delivered channel |

## Load Test Configuration

- Tool: Locust ([`scripts/loadtest.py`](../scripts/loadtest.py))
- Target: `https://notification-service-eppidgbmxa-nw.a.run.app`
- Users: 5 concurrent
- Duration: 60 seconds
- Workload mix: read a payment's delivery history for a payment with real deliveries (weight 5), read an unknown payment, exercising the empty path (2), health (1)
- The ingest endpoint is not in the mix: it is authenticated (a Pub/Sub OIDC token), so it is exercised live by the real subscriptions rather than by the load tool.

## Load Test Results

Run on 2026-09-06 against the live Cloud Run deployment. 824 requests over 60 seconds at 5 concurrent users, of which 743 were reads.

| Metric | Target | Measured | Status |
|--------|--------|----------|--------|
| Availability | 99.5% | 100% (0 of 824 failed) | pass |
| Read p50 | < 100ms | 45ms | pass |
| Read p95 | < 200ms | 53ms | pass |
| Health p50 | < 100ms | 41ms | pass |
| Error rate (5xx) | < 1% | 0% | pass |
| Throughput | > 5 req/s | 13.82 req/s | pass |

Per-endpoint medians: health 41ms, read a payment's deliveries 45ms. The read p99 (79ms) is steady-state; the single 1587ms maximum is the first request against a cold instance as Cloud Run scaled up from zero, after which reads settle at ~45ms.

The read is fast because it is one indexed lookup on `payment_id` joined to its attempts, with no cross-service calls, no login and no external send on the path. The sends themselves happen on the asynchronous event-processing path, off the request.

## The event-processing path

The consumer is not measured by request latency but by its delivery guarantees, which were verified live:

- A `payment.settled` fanned out to an email and an SMS, both delivered.
- A channel that failed a send returned a non-2xx, Pub/Sub redelivered, and only that channel retried, reaching delivered on the second attempt.
- A redelivered event produced no second send on an already-delivered channel, and the provider idempotency key held even across the send/commit crash window.

Throughput on this path is bounded by Cloud Run autoscaling and Pub/Sub's push concurrency rather than by the service; each delivery is a small transaction plus a simulated send, and multiple push subscriptions are processed concurrently.

## Post-Load Verification

After the load test, `GET /health` returns 200 with the database connected, and the deliveries created during earlier live testing remain readable by their payment id. Because delivery is idempotent per `(event_id, channel)`, replaying any event over the read path or the consumer never changes what was recorded, which is the service's integrity property rather than a balance to reconcile.
