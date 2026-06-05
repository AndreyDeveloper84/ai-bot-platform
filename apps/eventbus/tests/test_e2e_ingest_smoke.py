"""C6 bot-half joint smoke (B1).

Drives the A10 shared contract fixtures through the REAL ingest endpoint
(``/api/v1/internal/events/ingest``) with the production handler registry,
proving the Ayla -> bot delivery path on the consumer side:

    HMAC verify -> parse_envelope -> dispatch -> consumer side-effect
                                              -> IngestDedupe row

Doubled delivery -> exactly one side-effect (idempotency). An unknown
event name (``booking.no_show`` — Ayla emits it but the bot doesn't yet
accept it, issue #946) -> rejected (400 at the parse layer), the guard
that catches a contract gap BEFORE cross-service delivery is switched on.

This is the fixture-driven CI smoke (C6 scope doc §2a). The live
cross-service smoke (§2b) is Block C, after Alpha's HTTP publisher lands.
Fixtures are loaded from the single source of truth (A10), so a green
smoke here means the same bytes Ayla will emit.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time
from uuid import UUID

import pytest
from django.test import Client

from apps.booking.models import RemoteBookingProxy
from apps.conversations.models import Conversation
from apps.eventbus.models import IngestDedupe
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant
from tests.fixtures.contracts import load_contract

INGEST_URL = "/api/v1/internal/events/ingest"
SECRET = "ingest-smoke-secret"  # pragma: allowlist secret

TENANT_ID = "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c"
USER_ID = "f1a2b3c4-d5e6-4789-9abc-def012345678"
APPOINTMENT_ID = "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"

# CRITICAL (per B1 directive): the smoke MUST exercise booking.confirmed +
# payment.captured + payment.failed — not just the original booking.created
# family — so a missing handler / unknown-name regression surfaces as a
# 422/DLQ here, before live delivery is enabled.
CRITICAL_FIXTURES = (
    "booking.confirmed.v1.json",
    "payment.captured.v1.json",
    "payment.failed.v1.json",
)

pytestmark = pytest.mark.django_db


def _post(client: Client, body: bytes):
    ts_ms = str(int(time.time() * 1000))
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        INGEST_URL,
        data=body,
        content_type="application/json",
        HTTP_X_AYLA_EVENT_SIGNATURE=sig,
        HTTP_X_AYLA_EVENT_TIMESTAMP=ts_ms,
    )


def _body(obj: dict) -> bytes:
    return json.dumps(obj).encode()


@pytest.fixture(autouse=True)
def _wired(settings, monkeypatch):
    """Wire the view exactly like test_ingest_view, but register the REAL
    booking + payment handlers — the joint smoke must exercise production
    consumers, not test stubs."""
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = False
    # Pre-#246 tenant-verify bridge (Round-3 NEW-5).
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True

    from apps.eventbus import views as _views
    from apps.eventbus.ingest_dispatcher import dispatch_envelope as _direct

    # SQLite test backend deadlocks on the timeout threadpool (A12).
    monkeypatch.setattr(_views, "dispatch_with_timeout", _direct)

    import apps.eventbus.ingest_dispatcher as dispatcher_module
    from apps.eventbus.consumers.booking import register_booking_handlers
    from apps.eventbus.consumers.payment import register_payment_handlers

    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    register_booking_handlers()
    register_payment_handlers()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def seed() -> Tenant:
    """Tenant + linked BotUser + Conversation + a pending RemoteBookingProxy
    so booking.confirmed and payment.* have real targets to mutate."""
    tenant = Tenant.objects.create(id=TENANT_ID, slug="t-smoke", name="Smoke tenant")
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="9200",
        chat_id="chat-9200",
        ayla_user_id=USER_ID,
    )
    Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        state=Conversation.State.IDLE,
        last_message_at=dt.datetime(2026, 5, 21, 10, 0, tzinfo=dt.timezone.utc),
    )
    RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID),
        tenant=tenant,
        bot_user=None,
        start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
        status="pending_payment",
    )
    return tenant


class TestBotHalfJointSmoke:
    @pytest.mark.parametrize("fixture", CRITICAL_FIXTURES)
    def test_known_fixture_accepted_and_deduped(
        self, client: Client, seed: Tenant, fixture: str
    ) -> None:
        """Each critical event is ACCEPTED (200, not 422/DLQ) and recorded
        once in IngestDedupe — proving its handler is registered."""
        obj = load_contract(fixture)
        resp = _post(client, _body(obj))
        assert resp.status_code == 200, resp.content
        assert IngestDedupe.objects.filter(event_id=obj["event_id"]).count() == 1

    def test_booking_confirmed_consumer_fires(self, client: Client, seed: Tenant) -> None:
        """End-to-end: confirmed event through the endpoint flips the proxy."""
        resp = _post(client, _body(load_contract("booking.confirmed.v1.json")))
        assert resp.status_code == 200, resp.content
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED

    def test_double_delivery_single_side_effect(self, client: Client, seed: Tenant) -> None:
        """DoD: an event delivered twice produces exactly one side-effect."""
        obj = load_contract("booking.confirmed.v1.json")
        body = _body(obj)
        r1 = _post(client, body)
        r2 = _post(client, body)
        assert r1.status_code == 200 and r2.status_code == 200, (r1.content, r2.content)
        # One dedupe row, one confirmed proxy — replay did not double-apply.
        assert IngestDedupe.objects.filter(event_id=obj["event_id"]).count() == 1
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED

    def test_unknown_event_name_rejected(self, client: Client, seed: Tenant) -> None:
        """booking.no_show: Ayla emits it, the bot does not accept it yet
        (issue #946). A name absent from ALLOWED_EVENT_NAMES is rejected at
        the parse layer (400 invalid_event_name) BEFORE dispatch — either
        way it is refused, never silently consumed. This is the early
        warning the directive asked the smoke to carry, before delivery is
        switched on."""
        obj = load_contract("booking.confirmed.v1.json")
        obj["event_name"] = "booking.no_show"
        obj["event_id"] = "01J9NOSHOW00000000000000ZZ"
        resp = _post(client, _body(obj))
        assert resp.status_code == 400, resp.content
        # Refused, not recorded as a successfully-consumed event.
        assert IngestDedupe.objects.filter(event_id=obj["event_id"]).count() == 0
