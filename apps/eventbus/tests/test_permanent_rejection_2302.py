"""DRF-2302 — постоянный отказ потребителя: 422 + DLQ, а не 500 + повтор.

Outbox каталога на 5xx повторяет 9 раз за ~4,5 ч и только потом кладёт
событие в dead; на 4xx (кроме 429) — dead сразу, повтор вручную
(``replay_dead_outbox_events``). До этой задачи любой отказ потребителя
был 500, в том числе те, где повтор не поможет никогда: тенанта нет,
тенант или событие вне пилотного allowlist, битый payload. Узлы в обе
стороны: постоянный отказ — 422 и DLQ; временный отказ и настоящий сбой
обработчика — 500, как были. Дедуп при отказе не пишется: повтор после
починки должен пройти.
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.test import Client

from apps.eventbus import ingest_dispatcher
from apps.eventbus.consumers.booking import (
    BookingConfirmedPendingProxyError,
    CanonicalReschedulePayloadError,
    UnknownBookingStatusError,
)
from apps.eventbus.ingest_tenancy import (
    TenantAuthorizationError,
    assert_envelope_tenant_authorized,
)
from apps.eventbus.models import IngestDedupe, IngestDLQ
from apps.eventbus.tests.test_ingest_rate_limit import SECRET, VALID_BODY, _post

pytestmark = pytest.mark.django_db

TENANT_ID = VALID_BODY["tenant_id"]
EVENT_ID = VALID_BODY["event_id"]
KEY = ("booking.created", 1)


@pytest.fixture(autouse=True)
def _ingest(settings):
    settings.EVENT_INGEST_HMAC_SECRET = SECRET
    settings.RATELIMIT_ENABLE = False
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.created"})
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def handler(monkeypatch):
    """Обработчик ``booking.created`` — только проверка тенанта, как у всех
    потребителей, плюс подменяемое поведение после неё."""
    box: dict = {"after": None}

    def run(envelope) -> None:  # noqa: ANN001
        assert_envelope_tenant_authorized(envelope)
        if box["after"] is not None:
            raise box["after"]

    monkeypatch.setitem(ingest_dispatcher._REGISTRY, KEY, run)
    return box


def _send():  # noqa: ANN202
    return _post(Client(), json.dumps(VALID_BODY).encode())


def _dlq_reasons() -> list[str]:
    return list(IngestDLQ.objects.filter(event_id=EVENT_ID).values_list("reason", flat=True))


class TestPermanentRejectionIs422:
    def test_tenant_not_found(self, handler) -> None:
        resp = _send()
        assert resp.status_code == 422, resp.content
        assert json.loads(resp.content) == {"status": "rejected", "reason": "tenant_not_found"}
        assert _dlq_reasons() == ["tenant_not_found"]
        assert not IngestDedupe.objects.filter(event_id=EVENT_ID).exists()

    def test_event_not_allowed(self, handler, settings) -> None:
        settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset({"booking.cancelled"})
        resp = _send()
        assert resp.status_code == 422
        assert json.loads(resp.content)["reason"] == "event_not_allowed"

    def test_tenant_not_allowed(self, handler, settings) -> None:
        settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({"00000000-0000-4000-8000-000000000001"})
        resp = _send()
        assert resp.status_code == 422
        assert json.loads(resp.content)["reason"] == "tenant_not_allowed"

    def test_allowlist_never_configured(self, handler, settings) -> None:
        settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset()
        settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset()
        resp = _send()
        assert resp.status_code == 422
        assert json.loads(resp.content)["reason"] == "relationship_unavailable"

    @pytest.mark.parametrize(
        ("exc", "reason"),
        [
            (UnknownBookingStatusError("status=weird"), "unknown_booking_status"),
            (CanonicalReschedulePayloadError("starts_at missing"), "invalid_payload"),
        ],
    )
    def test_a_broken_payload(self, handler, exc, reason) -> None:
        from apps.tenancy.models import Tenant

        Tenant.objects.create(id=TENANT_ID, slug="rej-2302", name="T")
        handler["after"] = exc
        resp = _send()
        assert resp.status_code == 422
        assert json.loads(resp.content) == {"status": "rejected", "reason": reason}
        assert _dlq_reasons() == [reason]


class TestTransientAndRealFailuresStay500:
    def test_a_tenant_lookup_error(self, handler, monkeypatch) -> None:
        from apps.tenancy.models import Tenant

        def boom(*a, **kw):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("db down")

        monkeypatch.setattr(Tenant.objects, "filter", boom)
        resp = _send()
        assert resp.status_code == 500
        assert json.loads(resp.content)["reason"] == "handler_exception"

    def test_an_event_ahead_of_its_booking(self, handler) -> None:
        from apps.tenancy.models import Tenant

        Tenant.objects.create(id=TENANT_ID, slug="rej-2302-order", name="T")
        handler["after"] = BookingConfirmedPendingProxyError("no proxy yet")
        assert _send().status_code == 500

    def test_a_real_handler_crash(self, handler) -> None:
        from apps.tenancy.models import Tenant

        Tenant.objects.create(id=TENANT_ID, slug="rej-2302-crash", name="T")
        handler["after"] = RuntimeError("bug")
        resp = _send()
        assert resp.status_code == 500
        assert json.loads(resp.content) == {
            "status": "internal_error",
            "reason": "handler_exception",
        }
        assert _dlq_reasons() == []  # empty-assert-ok: первый сбой — до порога DLQ (3), ответ выше


class TestReplayAfterTheFix:
    def test_the_same_event_passes_once_the_tenant_exists(self, handler) -> None:
        """Дедуп при отказе не пишется — иначе повтор получил бы «duplicate»."""
        from apps.tenancy.models import Tenant

        assert _send().status_code == 422
        Tenant.objects.create(id=TENANT_ID, slug="rej-2302-replay", name="T")

        resp = _send()
        assert resp.status_code == 200, resp.content
        assert json.loads(resp.content) == {"status": "ok"}
        assert IngestDedupe.objects.filter(event_id=EVENT_ID).exists()


class TestCompatibility:
    def test_a_permanent_tenant_refusal_is_still_a_tenant_authorization_error(self) -> None:
        """Прежние ``except TenantAuthorizationError`` ловят и постоянный отказ."""
        from apps.eventbus.ingest_tenancy import TenantRejectedError

        assert issubclass(TenantRejectedError, TenantAuthorizationError)
        assert issubclass(TenantRejectedError, ingest_dispatcher.IngestRejection)
        assert not issubclass(TenantAuthorizationError, ingest_dispatcher.IngestRejection)
