import base64
import json
import logging
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import config
from app.channels import Channel, ChannelProvider
from app.database import get_db
from app.models import NotificationDelivery
from app.providers import default_channels
from app.schemas import AttemptOut, DeliveryOut, NotificationsOut, PubSubPush
from app.service import apply_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("notification.api")

DESCRIPTION = """
Downstream notification delivery for the ABS platform.

The service consumes committed payment and risk events and turns the
customer-facing ones into messages on simulated email and SMS channels. It is a
**strict sink**: it never writes to or blocks financial state, so a notification
failure, a provider outage, or the service being down cannot change a payment's
outcome (ABS-REQ-006).

**Correctness properties**

- Fan-out is a deterministic function of the event; uninteresting events produce
  nothing
- Delivery is idempotent per **(event_id, channel)**, so at-least-once
  redelivery never double-notifies a customer on a channel
- Each external send carries a deterministic idempotency key, closing the crash
  window between a provider succeeding and the delivery being recorded
- A failing channel retries to a bounded limit and is then dead-lettered, an
  in-band terminal state, not the broker's transport dead-letter
"""

TAGS = [
    {"name": "System", "description": "Health and service status."},
    {"name": "Delivery", "description": "Read the deliveries produced for a payment."},
    {"name": "Event Delivery", "description": "Authenticated Pub/Sub push consumer."},
]

app = FastAPI(
    title="Notification Service",
    version="0.1.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
)

# Process-level singleton, so each provider's idempotency store survives across
# requests and protects the crash window. Tests override get_channels.
_channels = default_channels()


def get_channels() -> dict[Channel, ChannelProvider]:
    return _channels


@app.get("/health", tags=["System"], summary="Liveness and database probe")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


def _to_delivery_out(d: NotificationDelivery) -> DeliveryOut:
    return DeliveryOut(
        id=d.id,
        event_id=d.event_id,
        channel=d.channel,
        destination=d.destination,
        status=d.status,
        attempt_count=d.attempt_count,
        provider_reference=d.provider_reference,
        correlation_id=d.correlation_id,
        created_at=d.created_at,
        delivered_at=d.delivered_at,
        attempts=[
            AttemptOut(
                attempt_number=a.attempt_number,
                outcome=a.outcome,
                error=a.error,
                attempted_at=a.attempted_at,
            )
            for a in d.attempts
        ],
    )


@app.get(
    "/notifications/{payment_id}",
    response_model=NotificationsOut,
    tags=["Delivery"],
    summary="Deliveries produced for a payment",
)
def get_notifications(payment_id: UUID, db: Session = Depends(get_db)):
    rows = (
        db.execute(
            select(NotificationDelivery)
            .where(NotificationDelivery.payment_id == payment_id)
            .order_by(NotificationDelivery.created_at.asc())
        )
        .scalars()
        .all()
    )
    return NotificationsOut(
        payment_id=payment_id, deliveries=[_to_delivery_out(d) for d in rows]
    )


_google_request = google_requests.Request()


def _verify_push_identity(authorization: str | None) -> None:
    """Require a Google OIDC token minted for the configured push service
    account, so only an authenticated Pub/Sub push can feed the consumer.
    Skipped when PUBSUB_PUSH_SA is unset (local and tests)."""
    if not config.PUBSUB_PUSH_SA:
        return
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing push authentication")
    token = authorization.split(" ", 1)[1]
    try:
        claims = google_id_token.verify_oauth2_token(token, _google_request)
    except Exception as e:
        raise HTTPException(status_code=403, detail="invalid push token") from e
    if not claims.get("email_verified") or claims.get("email") != config.PUBSUB_PUSH_SA:
        raise HTTPException(status_code=403, detail="unauthorized push identity")


@app.post(
    "/events/pubsub",
    tags=["Event Delivery"],
    summary="Authenticated Pub/Sub push consumer",
)
def pubsub_push(
    body: PubSubPush,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    channels: dict[Channel, ChannelProvider] = Depends(get_channels),
):
    _verify_push_identity(authorization)
    data = body.message.get("data")
    if not data:
        raise HTTPException(status_code=400, detail="missing message.data")
    try:
        envelope = json.loads(base64.b64decode(data).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid message data")
    if not all(k in envelope for k in ("event_id", "event_type")):
        raise HTTPException(status_code=400, detail="invalid envelope")

    result = apply_event(db, envelope, channels)
    if result["retry"]:
        # A channel is still retryable: signal the broker to redeliver. The
        # delivered channels are already terminal and will be skipped next time.
        raise HTTPException(status_code=503, detail="delivery incomplete, retry")
    return result
