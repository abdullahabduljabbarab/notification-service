"""
Load test harness for the Notification Service.

Run against the live GCP deployment:
    locust -f scripts/loadtest.py --host https://notification-service-eppidgbmxa-nw.a.run.app

Then open http://localhost:8089 to configure users and start the test, or run
headless with -u / -r / -t. Set CA_BUNDLE to a certificate bundle if a local
TLS-inspecting proxy breaks certificate verification.

The ingest endpoint is authenticated (it requires a Pub/Sub OIDC token), so this
exercises the public read path: the delivery-history read API and health. A
PAYMENT_IDS env var (comma-separated) seeds ids that have real deliveries; unset,
it reads random ids, which exercise the empty-result path.
"""

import os
import uuid

from locust import HttpUser, between, task

CA_BUNDLE = os.getenv("CA_BUNDLE")
PAYMENT_IDS = [p for p in os.getenv("PAYMENT_IDS", "").split(",") if p]


class NotificationUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        if CA_BUNDLE:
            self.client.verify = CA_BUNDLE

    @task(5)
    def read_deliveries(self):
        pid = PAYMENT_IDS[0] if PAYMENT_IDS else str(uuid.uuid4())
        self.client.get(f"/notifications/{pid}", name="/notifications/[id]")

    @task(2)
    def read_unknown(self):
        self.client.get(f"/notifications/{uuid.uuid4()}", name="/notifications/[id]")

    @task(1)
    def health(self):
        self.client.get("/health")
