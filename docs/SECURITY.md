# Security

## What this project is

A portfolio demonstration of downstream event consumption and reliable side-effect delivery. It is not a production notification system, uses simulated email and SMS providers, derives recipient addresses synthetically from account ids, and handles no real personal or contact data.

## Boundaries

**Secrets management.** The database connection string is held in GCP Secret Manager, provisioned alongside the rest of the infrastructure and injected into Cloud Run at runtime, never as a plaintext env var and never committed. `.gitignore` excludes `.env` and Terraform state.

**Keyless CI.** The deploy pipeline authenticates to GCP through Workload Identity Federation: GitHub Actions presents a short-lived OIDC token that GCP exchanges to impersonate a dedicated, repository-scoped deploy service account. No long-lived service-account key is stored in the repository, which removes the most common cloud-credential leak.

**No access to financial state.** The service never reads or writes the ledger, the orchestrator or the risk engine. It holds no credential to any of them and has no outbound broker path, so a compromise of the service cannot move money or alter a payment; its blast radius is its own delivery records.

**Input validation.** The Pub/Sub push envelope and the read-API path parameter are validated through Pydantic and FastAPI before the service layer. A missing `message.data`, an undecodable payload, or an envelope without the required fields is rejected with a 400.

**Idempotency and replay.** Delivery is deduplicated on `(event_id, channel)`, and each external send carries a deterministic idempotency key, so a redelivered event produces neither a duplicate delivery row nor a duplicate customer-facing message.

**SQL injection.** All database access is through the SQLAlchemy ORM with parameterised queries. No raw string interpolation.

**Identifiers.** Deliveries, payments and accounts use UUIDs; no sequential ids are exposed.

**Error responses and logging.** Structured JSON errors with no stack traces; logs record metadata, not message bodies or recipient details.

**HTTPS.** Terminated by Cloud Run. The application does not handle TLS.

## Authentication

The ingest endpoint is authenticated; the read and health endpoints are open by scope decision. `POST /events/pubsub` is the only surface that does work and produces external side effects, so it is protected: the push subscription attaches a Google OIDC token minted for a dedicated push service account, and the endpoint verifies that token and the account it was issued to before applying any event, so an anonymous caller cannot post forged events to trigger notifications. `GET /health`, `/docs` and `GET /notifications/{payment_id}` stay public, a deliberate scope decision for a portfolio, and safe because the service moves no money and the read API exposes only its own delivery records.

## Known limitations

- No authentication on the read endpoint. In production a delivery history would be scoped to an authenticated owner; today it exposes only synthetic delivery metadata for simulated recipients.
- The email and SMS providers are simulated. A real integration would hold provider credentials in Secret Manager and use the provider's own idempotency mechanism, which the deterministic key already models.
- Recipient addresses are derived synthetically from the account id. A production service would resolve real contact details from an authoritative source, subject to consent and preference management.
- No rate limiting. Would be added via Cloud Armor or middleware.
- Encryption at rest relies on the managed encryption provided by Cloud SQL. Customer-managed keys are outside project scope.
