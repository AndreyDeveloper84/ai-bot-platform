"""Miniapp read model on the Ayla path (W4 escalation №3).

BOOKING_VIA_AYLA_REST ON: list/detail read RemoteBookingProxy — the
Ayla-first create deliberately never writes BookingRequest (no
dual-write). Cancel routes through the Ayla seam; the proxy row flips
to ``cancelled`` ONLY via the booking.cancelled round-trip event, the
view never mutates it. Flag-off keeps the local BookingRequest read
model untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import timedelta
from urllib.parse import quote, urlencode

import pytest
from django.test import Client as DjangoClient, override_settings
from django.utils import timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
AYLA_UID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()

LIST_URL = "/api/v1/customer/bookings/list"


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
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-read-test"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-read-test", name="Ayla Read")


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
def other_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="777",
        chat_id="777",
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def master(tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        name="Ольга",
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def service(tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        name="Маникюр",
        slug="manikyur",
        duration_min=60,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )


def _proxy(
    tenant,
    bot_user,
    *,
    status: str = "confirmed",
    days_ahead: float = 7,
    duration_min: int = 90,
    service_id=None,
    specialist_id=None,
) -> RemoteBookingProxy:
    start = timezone.now() + timedelta(days=days_ahead)
    return RemoteBookingProxy.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=uuid.uuid4(),
        start_at=start,
        end_at=start + timedelta(minutes=duration_min),
        status=status,
        service_id=service_id,
        specialist_id=specialist_id,
    )


def _get(client: DjangoClient, url: str, user_id: str = "12345"):
    return client.get(url, HTTP_AUTHORIZATION=_init_data_header(user_id))


def _post(client: DjangoClient, url: str, user_id: str = "12345", body: dict | None = None):
    return client.post(
        url,
        data=json.dumps(body or {}),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(user_id),
    )


class _StubCancelClient:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def cancel_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return True


@pytest.fixture
def stub_cancel(monkeypatch) -> _StubCancelClient:
    stub = _StubCancelClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client",
        lambda: stub,
    )
    return stub


class TestList:
    def test_lists_only_own_tenant_proxies(
        self, client, tenant, bot_user, other_user, service, master
    ) -> None:
        own1 = _proxy(tenant, bot_user, service_id=SERVICE_AYLA_ID, specialist_id=master.id)
        own2 = _proxy(
            tenant,
            bot_user,
            status="pending_payment",
            days_ahead=8,
            service_id=SERVICE_AYLA_ID,
            specialist_id=master.id,
        )
        _proxy(tenant, other_user)  # same tenant, foreign user
        other_tenant = Tenant.objects.create(slug="other-tenant", name="Other")
        _proxy(other_tenant, None)  # orphan in another tenant

        resp = _get(client, LIST_URL)
        assert resp.status_code == 200
        items = resp.json()["items"]
        ids = [i["id"] for i in items]
        # Upcoming order: earliest start_at first.
        assert ids == [str(own1.appointment_id), str(own2.appointment_id)]

    def test_shape_and_names_from_mirrors(self, client, tenant, bot_user, service, master) -> None:
        proxy = _proxy(
            tenant,
            bot_user,
            duration_min=90,
            service_id=SERVICE_AYLA_ID,
            specialist_id=master.id,
        )
        resp = _get(client, LIST_URL)
        assert resp.status_code == 200
        (item,) = resp.json()["items"]
        assert item["id"] == str(proxy.appointment_id)
        assert item["status"] == "confirmed"  # verbatim from the proxy
        assert item["service_id"] == str(SERVICE_AYLA_ID)
        assert item["service_name"] == "Маникюр"  # via ayla_service_id mirror
        assert item["master_id"] == str(master.id)
        assert item["master_name"] == "Ольга"  # via CatalogMaster.id mirror
        assert item["visit_at"] == proxy.start_at.isoformat()
        assert item["duration_min"] == 90  # end - start
        # Immediate-cancel surface: no two-step undo on the Ayla path.
        assert item["cancel_requested_at"] is None
        assert item["undo_window_seconds"] == 0
        assert item["cancellable"] is True
        assert item["reschedulable"] is False
        assert item["rating"] is None
        assert item["can_rate"] is False
        # Optional C7.3 block absent without a PaymentMirror row.
        assert "payment" not in item

    def test_optional_payment_block_from_mirror(
        self, client, tenant, bot_user, service, master
    ) -> None:
        from decimal import Decimal

        from apps.booking.models import PaymentMirror

        proxy = _proxy(tenant, bot_user, service_id=SERVICE_AYLA_ID, specialist_id=master.id)
        PaymentMirror.all_tenants.create(
            tenant=tenant,
            appointment_id=proxy.appointment_id,
            capture_state=PaymentMirror.CaptureState.AUTHORIZED,
            amount=Decimal("1500.00"),
        )
        resp = _get(client, LIST_URL)
        (item,) = resp.json()["items"]
        assert item["payment"] == {"capture_state": "authorized", "amount": "1500.00"}

    def test_terminal_statuses_in_past_view(
        self, client, tenant, bot_user, service, master
    ) -> None:
        cancelled = _proxy(tenant, bot_user, status="cancelled", days_ahead=3)
        no_show = _proxy(tenant, bot_user, status="no_show", days_ahead=-1)
        upcoming = _proxy(tenant, bot_user, status="confirmed", days_ahead=5)

        resp = _get(client, f"{LIST_URL}?status=past")
        assert resp.status_code == 200
        ids = {i["id"] for i in resp.json()["items"]}
        assert ids == {str(cancelled.appointment_id), str(no_show.appointment_id)}
        # …and the upcoming default view hides terminal rows.
        resp = _get(client, LIST_URL)
        ids = {i["id"] for i in resp.json()["items"]}
        assert ids == {str(upcoming.appointment_id)}

    def test_status_filter_verbatim(self, client, tenant, bot_user) -> None:
        want = _proxy(tenant, bot_user, status="pending_payment", days_ahead=4)
        _proxy(tenant, bot_user, status="confirmed", days_ahead=4)
        resp = _get(client, f"{LIST_URL}?status=pending_payment")
        ids = [i["id"] for i in resp.json()["items"]]
        assert ids == [str(want.appointment_id)]

    def test_limit_validation(self, client, bot_user) -> None:
        assert _get(client, f"{LIST_URL}?limit=0").status_code == 400
        assert _get(client, f"{LIST_URL}?limit=abc").status_code == 400
        assert _get(client, f"{LIST_URL}?limit=51").status_code == 400

    def test_before_cursor_paginates(self, client, tenant, bot_user) -> None:
        # The before-cursor paginates the descending (past) view — same
        # contract as the local list.
        proxies = [_proxy(tenant, bot_user, status="cancelled", days_ahead=d) for d in (3, 5, 7)]
        resp = _get(client, f"{LIST_URL}?status=past&limit=2")
        payload = resp.json()
        assert [i["id"] for i in payload["items"]] == [
            str(proxies[2].appointment_id),
            str(proxies[1].appointment_id),
        ]
        cursor = payload["next_cursor"]
        assert cursor is not None
        # The ISO cursor carries a tz offset — clients must urlencode it
        # ('+' would otherwise decode as a space), same as the local list.
        resp2 = _get(client, f"{LIST_URL}?status=past&limit=2&before={quote(cursor)}")
        ids2 = [i["id"] for i in resp2.json()["items"]]
        assert ids2 == [str(proxies[0].appointment_id)]


class TestDetail:
    def _url(self, appointment_id) -> str:
        return f"/api/v1/customer/bookings/{appointment_id}"

    def test_detail_own_200(self, client, tenant, bot_user, service, master) -> None:
        proxy = _proxy(tenant, bot_user, service_id=SERVICE_AYLA_ID, specialist_id=master.id)
        resp = _get(client, self._url(proxy.appointment_id))
        assert resp.status_code == 200
        booking = resp.json()["booking"]
        assert booking["id"] == str(proxy.appointment_id)
        assert booking["status"] == "confirmed"
        assert booking["master_name"] == "Ольга"

    def test_detail_foreign_user_404(self, client, tenant, bot_user, other_user) -> None:
        proxy = _proxy(tenant, other_user)
        resp = _get(client, self._url(proxy.appointment_id))
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_detail_missing_404(self, client, bot_user) -> None:
        resp = _get(client, self._url(uuid.uuid4()))
        assert resp.status_code == 404

    def test_detail_orphan_proxy_404(self, client, tenant, bot_user) -> None:
        orphan = _proxy(tenant, None)
        resp = _get(client, self._url(orphan.appointment_id))
        assert resp.status_code == 404


class TestCancel:
    def _url(self, appointment_id) -> str:
        return f"/api/v1/customer/bookings/{appointment_id}/cancel"

    def test_cancel_calls_seam_and_keeps_proxy_readonly(
        self, client, tenant, bot_user, stub_cancel
    ) -> None:
        proxy = _proxy(tenant, bot_user)
        resp = _post(client, self._url(proxy.appointment_id))
        assert resp.status_code == 200
        (call,) = stub_cancel.calls
        assert call["appointment_id"] == str(proxy.appointment_id)
        assert call["external_user_id"] == "bot:max:12345"
        assert len(call["idempotency_key"]) == 32
        # The proxy is NOT mutated by the view — the booking.cancelled
        # round-trip event owns the status flip.
        proxy.refresh_from_db()
        assert proxy.status == "confirmed"
        # The response mirrors the current (still-confirmed) row verbatim.
        assert resp.json()["booking"]["status"] == "confirmed"

    def test_cancel_idempotency_key_deterministic(
        self, client, tenant, bot_user, stub_cancel
    ) -> None:
        proxy = _proxy(tenant, bot_user)
        _post(client, self._url(proxy.appointment_id))
        _post(client, self._url(proxy.appointment_id))
        keys = [c["idempotency_key"] for c in stub_cancel.calls]
        assert len(keys) == 2 and keys[0] == keys[1]

    def test_cancel_foreign_user_404_seam_untouched(
        self, client, tenant, bot_user, other_user, stub_cancel
    ) -> None:
        proxy = _proxy(tenant, other_user)
        resp = _post(client, self._url(proxy.appointment_id))
        assert resp.status_code == 404
        assert stub_cancel.calls == []

    def test_cancel_missing_404(self, client, bot_user, stub_cancel) -> None:
        resp = _post(client, self._url(uuid.uuid4()))
        assert resp.status_code == 404
        assert stub_cancel.calls == []

    def test_cancel_bad_request_maps_409(self, client, tenant, bot_user, stub_cancel) -> None:
        stub_cancel.exc = BookingBadRequestError(
            "http_409_invalid_state", status_code=409, code="INVALID_STATE"
        )
        proxy = _proxy(tenant, bot_user)
        resp = _post(client, self._url(proxy.appointment_id))
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_state"

    def test_cancel_upstream_unavailable_502(self, client, tenant, bot_user, stub_cancel) -> None:
        stub_cancel.exc = BookingUnavailableError("http_500")
        proxy = _proxy(tenant, bot_user)
        resp = _post(client, self._url(proxy.appointment_id))
        assert resp.status_code == 502
        assert resp.json()["error"] == "upstream_unavailable"

    def test_cancel_passes_specialist_service_date_for_cache_invalidation(
        self, client, tenant, bot_user, master, service, stub_cancel
    ) -> None:
        """DRF-997: the view must supply enough context for the Ayla booking
        client to invalidate the affected slot/dates cache on cancel."""
        from datetime import timedelta

        from django.utils import timezone

        start = timezone.now() + timedelta(days=7)
        proxy = _proxy(
            tenant,
            bot_user,
            service_id=SERVICE_AYLA_ID,
            specialist_id=master.id,
            days_ahead=7,
        )
        # Override the auto-generated start_at with a known date.
        proxy.start_at = start
        proxy.save()
        resp = _post(client, self._url(proxy.appointment_id))
        assert resp.status_code == 200
        (call,) = stub_cancel.calls
        assert call["specialist_id"] == str(master.id)
        assert call["service_id"] == str(SERVICE_AYLA_ID)
        assert call["date"] == start.date().isoformat()

    def test_cancel_confirm_409_on_ayla_path(self, client, tenant, bot_user) -> None:
        proxy = _proxy(tenant, bot_user)
        resp = _post(client, f"/api/v1/customer/bookings/{proxy.appointment_id}/cancel/confirm")
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_state"

    def test_cancel_undo_409_on_ayla_path(self, client, tenant, bot_user) -> None:
        proxy = _proxy(tenant, bot_user)
        resp = _post(client, f"/api/v1/customer/bookings/{proxy.appointment_id}/cancel/undo")
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_state"


class TestFlagOff:
    """BOOKING_VIA_AYLA_REST=False — the local BookingRequest read model
    behaves exactly as before; proxy rows are invisible to the views."""

    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_list_reads_local_booking_request(
        self, client, tenant, bot_user, service, master
    ) -> None:
        local = BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service=service,
            master=master,
            service_name=service.name,
            master_name=master.name,
            client_name="Test",
            client_phone="+7-000",
            visit_at=timezone.now() + timedelta(days=7),
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            source="bot",
            booking_source="ai_direct",
            billable=True,
            attribution_metadata={
                "actor_type": "customer",
                "created_by": "execute_confirm",
            },
        )
        _proxy(tenant, bot_user)  # invisible on the local path

        resp = _get(client, LIST_URL)
        assert resp.status_code == 200
        ids = [i["id"] for i in resp.json()["items"]]
        assert ids == [str(local.id)]

    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_detail_of_proxy_id_404s(self, client, tenant, bot_user) -> None:
        proxy = _proxy(tenant, bot_user)
        resp = _get(client, f"/api/v1/customer/bookings/{proxy.appointment_id}")
        assert resp.status_code == 404
