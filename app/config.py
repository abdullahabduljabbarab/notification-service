import os

# The service's own state (deliveries and attempts).
# Port 5435 locally, so the ledger (5432), orchestrator (5433) and risk engine
# (5434) can run alongside it without colliding.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://notify:notify@localhost:5435/notify",
)

# Simulated channel failure rates, so retry and dead-letter behaviour can be
# exercised live. Deterministic in tests, which use scripted channels instead.
EMAIL_FAILURE_RATE = float(os.getenv("EMAIL_FAILURE_RATE", "0.1"))
SMS_FAILURE_RATE = float(os.getenv("SMS_FAILURE_RATE", "0.15"))

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")

# The service account Pub/Sub uses to sign push OIDC tokens. When set, the
# ingest endpoint requires a verified token from this identity; unset locally
# and in tests, where the endpoint is open. Mirrors the risk engine's ingress.
PUBSUB_PUSH_SA = os.getenv("PUBSUB_PUSH_SA", "")
