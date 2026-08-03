"""C7 passthrough tests — verified customer binding (C7.6), payments,
cards, and the BookingItem payment read-model field.

The Ayla wire is stubbed at the client level. Pins:
* session-resolved identity only — arbitrary client ayla_user_id → 403;
* unlinked BotUser → 403;
* appointment ownership (404 for someone else's / unknown appointment);
* verbatim passthrough of contract fields;
* C1 409 SUBSCRIPTION_PAST_DUE → neutral ``unavailable`` slug;
* idempotent card delete (repeat → 204);
* BookingItem.payment from the PaymentMirror (hold + payment.* events).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient, override_settings

from apps.booking.models import BookingRequest, PaymentMirror, RemoteBookingProxy
from apps.eventbus.consumers.booking import handle_booking_confirmed
from apps.eventbus.consumers.payment import (
    handle_payment_captured,
    upsert_payment_mirror,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.integrations.ayla.payments_client import (
    ClientPaymentsConflictError,
    ClientPaymentsNotFoundError,
    ClientPaymentsTransportError,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
AYLA_UID = uuid.uuid4()
APPT_ID = uuid.uuid4()


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "c7-test"
    # Pre-#246 transition bridge for the consumer-level tests in this file.
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
    # Staging-style fallback for the YooKassa redirect (the FE may also
    # send return_url explicitly — that wins; see the dedicated tests).
    settings.AYLA_CLIENT_PAYMENTS_RETURN_URL = "https://miniapp.test/return"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="c7-test", name="C7 Test")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=AYLA_UID,
    )


@pytest.fixture
def unlinked_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="777",
        chat_id="777",
        ayla_user_id=None,
    )


class _StubC7Client:
    def __init__(self, *, payment_data=None, exc: Exception | None = None) -> None:
        self.payment_data = payment_data or {
            "payment_id": "p-1",
            "confirmation_url": "https://pay.test/c/1",
            "amount": "2000.00",
            "currency": "RUB",
            "capture_state": "authorized",
        }
        self.exc = exc
        self.calls: list[tuple] = []
        self.closed = False

    def create_payment(self, **kwargs):
        self.calls.append(("create_payment", kwargs))
        if self.exc:
            raise self.exc
        return self.payment_data

    def cards_setup(self, **kwargs):
        self.calls.append(("cards_setup", kwargs))
        return {"confirmation_url": "https://pay.test/bind"}

    def list_cards(self, **kwargs):
        self.calls.append(("list_cards", kwargs))
        return [{"id": "c-1", "last4": "4242", "brand": "visa"}]

    def delete_card(self, **kwargs) -> None:
        self.calls.append(("delete_card", kwargs))

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


@pytest.fixture
def stub_client(monkeypatch) -> _StubC7Client:
    stub = _StubC7Client()
    monkeypatch.setattr(
        "apps.miniapp_api.views.AylaClientPaymentsClient",
        lambda: stub,
    )
    return stub


class TestBinding:
    def test_foreign_ayla_user_id_403(self, client: DjangoClient, bot_user, stub_client) -> None:
        foreign = str(uuid.uuid4())
        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID), "ayla_user_id": foreign}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
        assert stub_client.calls == []  # gate fires BEFORE any upstream call

    def test_foreign_ayla_user_id_query_param_403(
        self, client: DjangoClient, bot_user, stub_client
    ) -> None:
        resp = client.get(
            f"/api/v1/customer/me/cards/?ayla_user_id={uuid.uuid4()}",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 403
        assert stub_client.calls == []

    def test_unlinked_user_403(self, client: DjangoClient, unlinked_user, stub_client) -> None:
        resp = client.get(
            "/api/v1/customer/me/cards/",
            HTTP_AUTHORIZATION=_init_data_header("777"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "identity_not_linked"
        assert stub_client.calls == []

    def test_matching_ayla_user_id_ok(self, client: DjangoClient, bot_user, stub_client) -> None:
        resp = client.get(
            f"/api/v1/customer/me/cards/?ayla_user_id={AYLA_UID}",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        (name, kwargs) = stub_client.calls[0]
        assert name == "list_cards"
        assert kwargs["ayla_user_id"] == str(AYLA_UID)
        # IsBotServiceWithVerifiedClient: the resolved-actor header value.
        assert kwargs["external_user_id"] == "bot:max:12345"


class TestCreatePayment:
    def _own_appointment(self, tenant, bot_user) -> None:
        from django.utils import timezone as tz

        RemoteBookingProxy.all_tenants.create(
            appointment_id=APPT_ID,
            tenant=tenant,
            bot_user=bot_user,
            start_at=tz.now(),
            end_at=tz.now(),
            status="confirmed",
        )

    def test_happy_path_verbatim(self, client: DjangoClient, tenant, bot_user, stub_client) -> None:
        self._own_appointment(tenant, bot_user)
        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "payment_id": "p-1",
            "confirmation_url": "https://pay.test/c/1",
            "amount": "2000.00",
            "currency": "RUB",
            "capture_state": "authorized",
        }
        (name, kwargs) = stub_client.calls[0]
        assert name == "create_payment"
        assert kwargs["appointment_id"] == str(APPT_ID)
        # IsBotServiceWithVerifiedClient wire contract (C7.6).
        assert kwargs["external_user_id"] == "bot:max:12345"
        assert kwargs["client_id"] == str(AYLA_UID)
        # No FE return_url → the configured fallback is used.
        assert kwargs["return_url"] == "https://miniapp.test/return"

    def test_return_url_from_fe_wins(
        self, client: DjangoClient, tenant, bot_user, stub_client
    ) -> None:
        self._own_appointment(tenant, bot_user)
        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID), "return_url": "https://fe.test/back"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert stub_client.calls[0][1]["return_url"] == "https://fe.test/back"

    @override_settings(AYLA_CLIENT_PAYMENTS_RETURN_URL="")
    def test_return_url_missing_everywhere_400(
        self, client: DjangoClient, tenant, bot_user, stub_client
    ) -> None:
        self._own_appointment(tenant, bot_user)
        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"
        assert stub_client.calls == []  # validation fires BEFORE any upstream call

    def test_someone_elses_appointment_404(
        self, client: DjangoClient, tenant, bot_user, stub_client
    ) -> None:
        # Appointment exists but belongs to ANOTHER user.
        from django.utils import timezone as tz

        other = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="999", ayla_user_id=uuid.uuid4()
        )
        RemoteBookingProxy.all_tenants.create(
            appointment_id=APPT_ID,
            tenant=tenant,
            bot_user=other,
            start_at=tz.now(),
            end_at=tz.now(),
            status="confirmed",
        )
        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert stub_client.calls == []

    def test_unknown_appointment_404(self, client: DjangoClient, bot_user, stub_client) -> None:
        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(uuid.uuid4())}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert stub_client.calls == []

    def test_c1_conflict_maps_unavailable(
        self, client: DjangoClient, tenant, bot_user, monkeypatch
    ) -> None:
        self._own_appointment(tenant, bot_user)
        stub = _StubC7Client(exc=ClientPaymentsConflictError("409", code="SUBSCRIPTION_PAST_DUE"))
        monkeypatch.setattr("apps.miniapp_api.views.AylaClientPaymentsClient", lambda: stub)

        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "unavailable"
        assert "past_due" not in resp.text
        assert "subscription" not in resp.text.lower()

    def test_upstream_5xx_maps_502(
        self, client: DjangoClient, tenant, bot_user, monkeypatch
    ) -> None:
        self._own_appointment(tenant, bot_user)
        stub = _StubC7Client(exc=ClientPaymentsTransportError("http_500"))
        monkeypatch.setattr("apps.miniapp_api.views.AylaClientPaymentsClient", lambda: stub)

        resp = client.post(
            "/api/v1/customer/me/payments/",
            data=json.dumps({"appointment_id": str(APPT_ID)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "upstream_unavailable"


class TestCards:
    def test_setup_verbatim(self, client: DjangoClient, bot_user, stub_client) -> None:
        resp = client.post(
            "/api/v1/customer/me/cards/setup/",
            data=json.dumps(
                {"consent_version": "offer-1.0", "consented_at": "2026-07-22T12:00:00Z"}
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"confirmation_url": "https://pay.test/bind"}
        (name, kwargs) = stub_client.calls[0]
        assert name == "cards_setup"
        assert kwargs["ayla_user_id"] == str(AYLA_UID)
        assert kwargs["external_user_id"] == "bot:max:12345"
        assert kwargs["consent_version"] == "offer-1.0"
        assert kwargs["return_url"] == "https://miniapp.test/return"

    def test_setup_without_consent_version_400(
        self, client: DjangoClient, bot_user, stub_client
    ) -> None:
        resp = client.post(
            "/api/v1/customer/me/cards/setup/",
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"
        assert stub_client.calls == []

    def test_list_verbatim(self, client: DjangoClient, bot_user, stub_client) -> None:
        resp = client.get(
            "/api/v1/customer/me/cards/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"cards": [{"id": "c-1", "last4": "4242", "brand": "visa"}]}
        (name, kwargs) = stub_client.calls[0]
        assert name == "list_cards"
        assert kwargs["external_user_id"] == "bot:max:12345"

    def test_delete_204_and_repeat(self, client: DjangoClient, bot_user, stub_client) -> None:
        card_id = str(uuid.uuid4())
        for _ in range(2):
            resp = client.delete(
                f"/api/v1/customer/me/cards/{card_id}/",
                HTTP_AUTHORIZATION=_init_data_header("12345"),
            )
            assert resp.status_code == 204
        (name, kwargs) = stub_client.calls[0]
        assert name == "delete_card"
        assert kwargs["card_id"] == card_id
        assert kwargs["external_user_id"] == "bot:max:12345"

    def test_delete_upstream_404_still_204(
        self, client: DjangoClient, bot_user, monkeypatch
    ) -> None:
        stub = _StubC7Client()
        stub.delete_card = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            ClientPaymentsNotFoundError("gone")
        )
        monkeypatch.setattr("apps.miniapp_api.views.AylaClientPaymentsClient", lambda: stub)

        resp = client.delete(
            f"/api/v1/customer/me/cards/{uuid.uuid4()}/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 204


def _make_booking(tenant, bot_user, *, ayla_marker_id=None) -> BookingRequest:
    from django.utils import timezone as tz

    from apps.catalog.models import CatalogMaster, CatalogService

    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Ольга",
        specialization="Маникюр",
        is_active=True,
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Маникюр",
        slug="manikyur",
        duration_min=60,
    )
    comment = ""
    if ayla_marker_id is not None:
        # Ayla-path marker shape (same key across providers).
        comment = f"Bot booking | yclients_record_id={ayla_marker_id}"
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        master=master,
        service=service,
        service_name="Маникюр",
        master_name="Ольга",
        client_name="Test",
        client_phone="+7-000",
        visit_at=tz.now() + timedelta(days=7),
        duration_min=60,
        status="confirmed",
        source="bot",
        booking_source="ai_direct",
        billable=True,
        billing_reason="ai_direct + confirmed",
        attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
        comment=comment,
    )


class TestPaymentMirrorReadModel:
    def test_booking_confirmed_with_payment_id_stamps_hold(self, tenant, bot_user) -> None:
        import datetime as dt

        env = IngestEnvelope(
            event_id="9f8e7d6c-5b4a-3c2d-1e0f-a9b8c7d6e5f4",
            event_name="booking.confirmed",
            event_version=1,
            occurred_at=dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc),
            tenant_id=str(tenant.id),
            user_id=str(AYLA_UID),
            actor="system",
            correlation_id="c1",
            causation_id=None,
            data={
                "appointment_id": str(APPT_ID),
                "payment_id": str(uuid.uuid4()),
                "amount": "2000.00",
            },
        )
        handle_booking_confirmed(env)

        mirror = PaymentMirror.all_tenants.get(appointment_id=APPT_ID)
        assert mirror.capture_state == "authorized"
        assert str(mirror.amount) == "2000.00"

    def test_booking_confirmed_without_payment_id_no_mirror(self, tenant, bot_user) -> None:
        import datetime as dt

        env = IngestEnvelope(
            event_id="8e7d6c5b-4a3b-2c1d-0e9f-b8a7c6d5e4f3",
            event_name="booking.confirmed",
            event_version=1,
            occurred_at=dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc),
            tenant_id=str(tenant.id),
            user_id=str(AYLA_UID),
            actor="system",
            correlation_id="c1",
            causation_id=None,
            data={"appointment_id": str(APPT_ID)},
        )
        handle_booking_confirmed(env)

        assert not PaymentMirror.all_tenants.filter(appointment_id=APPT_ID).exists()

    def test_captured_updates_mirror(self, tenant) -> None:
        import datetime as dt

        upsert_payment_mirror(
            tenant=tenant,
            appointment_id=APPT_ID,
            payment_id=uuid.uuid4(),
            capture_state="authorized",
            amount="2000.00",
            event_id="e1",
        )
        env = IngestEnvelope(
            event_id="7d6c5b4a-3b2c-1d0e-9f8a-c7d6e5f4e3d2",
            event_name="payment.captured",
            event_version=1,
            occurred_at=dt.datetime(2026, 7, 19, 13, 0, tzinfo=dt.timezone.utc),
            tenant_id=str(tenant.id),
            user_id=str(AYLA_UID),
            actor="system",
            correlation_id="c1",
            causation_id=None,
            data={
                "payment_id": str(uuid.uuid4()),
                "appointment_id": str(APPT_ID),
                "amount": "2000.00",
            },
        )
        handle_payment_captured(env)

        mirror = PaymentMirror.all_tenants.get(appointment_id=APPT_ID)
        assert mirror.capture_state == "captured"
        assert PaymentMirror.all_tenants.filter(appointment_id=APPT_ID).count() == 1

    def test_booking_item_carries_payment(self, tenant, bot_user) -> None:
        from apps.miniapp_api.views import _booking_to_dict

        booking = _make_booking(tenant, bot_user, ayla_marker_id=APPT_ID)
        upsert_payment_mirror(
            tenant=tenant,
            appointment_id=APPT_ID,
            capture_state="captured",
            amount="2000.00",
            event_id="e1",
        )

        out = _booking_to_dict(booking)

        assert out["payment"] == {"capture_state": "captured", "amount": "2000.00"}

    def test_booking_item_without_mirror_has_no_payment(self, tenant, bot_user) -> None:
        from apps.miniapp_api.views import _booking_to_dict

        booking = _make_booking(tenant, bot_user)

        assert "payment" not in _booking_to_dict(booking)
