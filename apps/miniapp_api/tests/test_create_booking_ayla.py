"""Miniapp create_booking on the Ayla path (AMD-019 / D6).

Pins: payment_required passthrough (default FALSE for miniapp),
verbatim Ayla status in the response, C1-neutral 409 on
SUBSCRIPTION_PAST_DUE, fail-closed grounding, and the local default
path staying untouched when BOOKING_VIA_AYLA_REST is off.

DRF-1057 adds the identity-resolve pins: an unlinked person gets the
link established here (same ``ensure_ayla_link`` the chat path uses)
instead of an unconditional 403; a linked person costs no network; a
failed resolve still refuses honestly; and the subject is never taken
from the request body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client as DjangoClient, override_settings

from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    BookingBadRequestError,
)
from apps.integrations.ayla.health_check import (
    HEALTH_CHECK_NOT_APPLICABLE,
    HEALTH_CHECK_REQUIRED,
    HEALTH_CHECK_UNKNOWN,
    OUTWARD_HANDOFF,
    OUTWARD_UNAVAILABLE,
)
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.tenancy.models import Tenant

#: Три кода отказа гейта здоровья и два имени, под которыми они выходят
#: наружу (§98: REQUIRED и UNKNOWN — одно имя для человека).
ALL_HEALTH_CODES = [HEALTH_CHECK_REQUIRED, HEALTH_CHECK_UNKNOWN, HEALTH_CHECK_NOT_APPLICABLE]
HEALTH_SLUGS = {OUTWARD_HANDOFF, OUTWARD_UNAVAILABLE}


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
AYLA_UID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()

SALON_TZ = ZoneInfo("Europe/Moscow")


def visit_at() -> datetime:
    """The visit time every request in this file books, 14:00 salon-local.

    Relative on purpose. This used to be the literal
    ``"2026-08-01T14:00:00+03:00"``, which stopped being a future date on
    2026-08-02 — after which ``create_booking`` rejected it as a visit in
    the past and three tests went red without anyone touching the code.
    Nobody noticed for a fortnight because CI did not run this suite
    until #1189 turned the full ``apps/`` run on.

    A pinned future date is a test that schedules its own failure. The
    only thing this one needs is «comfortably in the future», so it says
    that instead of naming a day.
    """

    return (datetime.now(SALON_TZ) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )


MASTER_AYLA_ID = uuid.uuid4()


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
    settings.MAX_BOT_TENANT_SLUG = "ayla-create-test"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-create-test", name="Ayla Create")


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
def master(tenant) -> CatalogMaster:
    from django.utils import timezone as tz

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Ольга",
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=MASTER_AYLA_ID,
    )


@pytest.fixture
def service(tenant, master) -> CatalogService:
    from django.utils import timezone as tz

    from apps.catalog.models import MasterService

    svc = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name="Маникюр",
        slug="manikyur",
        duration_min=60,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )
    # DRF-1164 — the create view now refuses a service nobody performs,
    # BEFORE the local/Ayla branch. This module's baseline service is a
    # normal, bookable one, so it carries its performer mapping; without
    # it every passthrough test below would be measuring the new gate
    # instead of the Ayla call it means to assert.
    MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)
    return svc


class _StubAylaClient:
    def __init__(self, *, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": kwargs.get("_status", "confirmed")},
        )


@pytest.fixture
def stub_client(monkeypatch) -> _StubAylaClient:
    stub = _StubAylaClient()
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client",
        lambda: stub,
    )
    return stub


@pytest.fixture
def stub_resolve(monkeypatch) -> Any:
    """Patch the identity HTTP leg (DRF-1057).

    Patched at the client module, because ``ensure_ayla_link`` imports
    ``resolve_identity`` lazily from there — the same convention as
    ``apps/identity/services/tests/test_ayla_link.py``.
    """

    calls: list[str] = []
    state: dict[str, Any] = {"uuid": uuid.uuid4(), "error": None}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        if state["error"] is not None:
            raise state["error"]
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=True)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return type("Stub", (), {"calls": calls, "state": state})()


def _post_as(
    client: DjangoClient,
    service,
    master,
    *,
    user_id: str,
    extra: dict | None = None,
):
    body = {
        "service_id": str(service.id),
        "master_id": str(master.id),
        "visit_at": visit_at().isoformat(),
    }
    if extra:
        body.update(extra)
    return client.post(
        "/api/v1/customer/bookings",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(user_id),
    )


def _post(client: DjangoClient, service, master, extra: dict | None = None):
    return _post_as(client, service, master, user_id="12345", extra=extra)


class TestPassthrough:
    def test_default_false(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master)
        assert resp.status_code == 201
        call = stub_client.calls[0]
        assert call["payment_required"] is False
        assert call["specialist_id"] == str(master.id)
        # #1027: SpecialistProfile UUID (= CatalogMaster.id), NEVER the
        # Ayla User UUID (that one is the AMD-005 billing key only).
        assert call["specialist_id"] != str(master.ayla_user_id)
        assert call["service_id"] == str(SERVICE_AYLA_ID)
        assert call["client_id"] == str(AYLA_UID)
        assert resp.json()["booking"]["status"] == "confirmed"

    def test_true_passed_verbatim(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert stub_client.calls[0]["payment_required"] is True

    def test_false_passed_verbatim(self, client, bot_user, service, master, stub_client) -> None:
        resp = _post(client, service, master, {"payment_required": False})
        assert resp.status_code == 201
        assert stub_client.calls[0]["payment_required"] is False

    def test_awaiting_payment_status_verbatim(
        self, client, bot_user, service, master, monkeypatch
    ) -> None:
        record = AylaBookingRecord(
            appointment_id=str(uuid.uuid4()),
            raw={"id": str(uuid.uuid4()), "status": "awaiting_payment"},
        )
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client",
            lambda: type("C", (), {"create_appointment": lambda self, **kw: record})(),
        )
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert resp.json()["booking"]["status"] == "awaiting_payment"

    def test_idempotency_key_differs_by_payment_required(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        _post(client, service, master, {"payment_required": False})
        _post(client, service, master, {"payment_required": True})
        keys = [c["idempotency_key"] for c in stub_client.calls]
        assert len(keys) == 2 and keys[0] != keys[1]


class TestErrors:
    def test_c1_conflict_maps_unavailable(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        stub_client.exc = BookingBadRequestError(
            "http_409_subscription_past_due",
            status_code=409,
            code="SUBSCRIPTION_PAST_DUE",
        )
        resp = _post(client, service, master)
        assert resp.status_code == 409
        assert resp.json()["error"] == "unavailable"
        assert "past_due" not in resp.text
        assert "subscription" not in resp.text.lower()

    def test_grounding_miss_fails_closed(
        self, client, bot_user, tenant, master, stub_client
    ) -> None:
        from django.utils import timezone as tz

        from apps.catalog.models import MasterService

        unlinked = CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=tz.now(),
            name="Не синкано",
            slug="unsynced",
            is_active=True,
            ayla_service_id=None,
        )
        # Performer present on purpose (DRF-1164): the refusal under test
        # is the GROUNDING miss, and both refusals answer 409
        # `service_unbookable`. Without the mapping this test would go
        # green off the wrong gate and stop guarding #1034.
        MasterService.all_tenants.create(tenant=tenant, master=master, service=unlinked)
        resp = _post(client, unlinked, master)
        assert resp.status_code == 409
        assert resp.json()["error"] == "service_unbookable"
        assert stub_client.calls == []

    def test_unresolvable_user_403(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        """DRF-1057: 403 is the LAST resort — only after the resolve is
        attempted and Ayla could not answer."""
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )
        stub_resolve.state["error"] = IdentityResolveError("network: ReadTimeout")

        resp = _post_as(client, service, master, user_id="777")

        assert resp.status_code == 403
        assert resp.json()["error"] == "identity_not_linked"
        assert stub_client.calls == []
        # …and the resolve really was attempted (the pre-1057 defect was
        # refusing without ever trying).
        assert stub_resolve.calls == ["bot:max:777"]


class TestIdentityResolve:
    """DRF-1057 — the Mini App must establish the Ayla link the same way the
    chat path does (``get_booking_provider`` → ``ensure_ayla_link``)."""

    def test_unlinked_user_gets_linked_and_books(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        unlinked = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )

        resp = _post_as(client, service, master, user_id="777")

        assert resp.status_code == 201
        # The booking really went to Ayla, bound to the freshly resolved subject.
        assert stub_client.calls[0]["client_id"] == str(stub_resolve.state["uuid"])
        # …and the link was persisted, so the next call costs no network.
        unlinked.refresh_from_db()
        assert unlinked.ayla_user_id == stub_resolve.state["uuid"]

    def test_second_booking_costs_no_network(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )

        _post_as(client, service, master, user_id="777")
        _post_as(client, service, master, user_id="777", extra={"payment_required": True})

        assert len(stub_client.calls) == 2
        assert len(stub_resolve.calls) == 1  # resolved once, then cache_hit

    def test_already_linked_user_never_resolves(
        self, client, bot_user, service, master, stub_client, stub_resolve
    ) -> None:
        resp = _post(client, service, master)

        assert resp.status_code == 201
        assert stub_client.calls[0]["client_id"] == str(AYLA_UID)
        assert stub_resolve.calls == []  # no network for a linked person

    def test_body_supplied_identity_is_ignored(
        self, client, bot_user, service, master, stub_client, stub_resolve
    ) -> None:
        """DRF-1036 boundary: the subject comes from the SESSION BotUser.

        A client-supplied ``ayla_user_id`` in the body must never select the
        subject — the create still binds to the session identity.
        """
        foreign = str(uuid.uuid4())

        resp = _post(client, service, master, {"ayla_user_id": foreign})

        assert resp.status_code == 201
        assert stub_client.calls[0]["client_id"] == str(AYLA_UID)
        assert stub_client.calls[0]["client_id"] != foreign
        assert stub_client.calls[0]["external_user_id"] == "bot:max:12345"
        assert stub_resolve.calls == []

    def test_unlinked_body_supplied_identity_is_ignored(
        self, client, tenant, service, master, stub_client, stub_resolve
    ) -> None:
        """Same boundary on the resolve path: a body id cannot pre-empt or
        steer the resolve, and cannot end up on the wire."""
        BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )
        foreign = str(uuid.uuid4())

        resp = _post_as(client, service, master, user_id="777", extra={"ayla_user_id": foreign})

        assert resp.status_code == 201
        assert stub_resolve.calls == ["bot:max:777"]  # subject from the session row
        assert stub_client.calls[0]["client_id"] == str(stub_resolve.state["uuid"])
        assert stub_client.calls[0]["client_id"] != foreign


class TestLocalPathUnchanged:
    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_flag_off_keeps_local_path(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        """payment_required is accepted but inert when the flag is off —
        the local create path is untouched and Ayla is never called."""
        import datetime as dt

        from apps.scheduling.models import Weekday, WorkingHours

        # (The MasterService mapping now comes with the `service` fixture.)
        # Open the booked day for the slot check. The weekday is derived
        # from the visit rather than named, so the setup follows the date
        # instead of quietly disagreeing with it. `Weekday` is Mon=0…Sun=6,
        # matching `date.weekday()` — see apps/scheduling/models.py.
        WorkingHours.all_tenants.create(
            tenant=bot_user.tenant,
            master=master,
            day_of_week=Weekday(visit_at().date().weekday()),
            start_time=dt.time(9, 0),
            end_time=dt.time(18, 0),
            is_working=True,
        )
        resp = _post(client, service, master, {"payment_required": True})
        assert resp.status_code == 201
        assert stub_client.calls == []
        assert resp.json()["booking"]["status"] == "confirmed"


class TestHealthCheckHandoff:
    """Медицинская передача на клиентской поверхности — §98 / §100.

    Шесть утверждений владельца. До DRF-1614 все три кода уезжали сюда
    как `_error("bad_request", "booking rejected", 400)`: человек читал
    «что-то пошло не так» про систему, которая только что приняла о нём
    решение намеренно.
    """

    @staticmethod
    def _refuse(stub_client, code: str) -> None:
        stub_client.exc = BookingBadRequestError(
            f"http_422_{code.lower()}",
            status_code=422,
            code=code,
        )

    @pytest.mark.parametrize("code", ALL_HEALTH_CODES)
    def test_the_refusal_keeps_ayla_status_and_names_the_code(
        self, client, bot_user, service, master, stub_client, code: str
    ) -> None:
        """422 зеркалится, слаг несёт машинный код (§98 п.1–3).

        Статус не выводится заново, а слаг не проза: §100 требует, чтобы
        причину не приходилось восстанавливать по тексту или статусу.
        Заодно это то, на чём стоит ветвление SPA.
        """
        self._refuse(stub_client, code)
        resp = _post(client, service, master)

        assert resp.status_code == 422, f"{code}: статус должен зеркалить отказ Ayla"
        assert resp.json()["error"] in HEALTH_SLUGS, f"{code}: получен {resp.json()['error']!r}"
        assert resp.json()["error"] != "bad_request"

    @pytest.mark.parametrize("code", ALL_HEALTH_CODES)
    def test_nothing_claims_a_booking_was_created(
        self, client, bot_user, service, master, stub_client, code: str
    ) -> None:
        """«Не обещать, что запись создана» (§98 п.4).

        Положительная стража впереди: успешный ответ этой поверхности
        действительно кладёт идентификатор в `booking.id`, иначе
        «идентификатора нет» было бы верно и при переименованном ключе.
        """
        ok = _post(client, service, master)
        assert ok.status_code == 201 and ok.json()["booking"]["id"], (
            "успешный ответ перестал нести идентификатор — проверка ниже потеряла предмет"
        )

        self._refuse(stub_client, code)
        body = _post(client, service, master).json()

        # Стража присутствия на тех же данных: в теле есть ключ отказа,
        # значит «брони нет» сказано про разобранный непустой ответ, а не
        # про пустой словарь.
        assert "error" in body, "ответ не тот — проверка ниже была бы про пустоту"
        assert body["error"] in HEALTH_SLUGS
        assert "booking" not in body, f"{code}: поверхность вернула запись"
        assert "Вы записаны" not in json.dumps(body, ensure_ascii=False)

    @pytest.mark.parametrize("code", ALL_HEALTH_CODES)
    def test_the_person_reads_no_technical_text(
        self, client, bot_user, service, master, stub_client, code: str
    ) -> None:
        """Человеку не показывается машинное (§98 п.5).

        `detail` — единственное поле, которое видит человек. Код живёт в
        слаге, для экрана, и в журнале, для нас.
        """
        self._refuse(stub_client, code)
        detail = _post(client, service, master).json()["detail"]

        assert detail, "поверхность не дала человеку ни слова — проверять нечего"
        assert "HEALTH_CHECK" not in detail
        assert "http_422" not in detail
        assert "booking rejected" != detail

    def test_not_applicable_promises_no_specialist(
        self, client, bot_user, service, master, stub_client
    ) -> None:
        """Отказ, которому некого назначить, ничего не обещает (§100.A)."""
        self._refuse(stub_client, HEALTH_CHECK_REQUIRED)
        promised = _post(client, service, master).json()["detail"]
        assert "специалист" in promised.lower(), (
            "фраза передачи перестала обещать специалиста — сравнение ниже потеряло предмет"
        )

        self._refuse(stub_client, HEALTH_CHECK_NOT_APPLICABLE)
        refused = _post(client, service, master).json()["detail"]

        assert refused, "второй отказ пуст — сравнение ниже без предмета"
        assert "специалист" not in refused.lower()
        assert refused != promised

    def test_unknown_is_countable_apart_from_required(
        self, client, bot_user, service, master, stub_client, caplog
    ) -> None:
        """Счётчик UNKNOWN отделим от REQUIRED (§98 п.6).

        Очередь разметки услуг приоритизируется числом UNKNOWN; слитый
        счётчик оставляет её без критерия. Проверяется различимость двух
        строк журнала, а не факт записи.
        """
        import logging

        def _lines(code: str) -> list[str]:
            self._refuse(stub_client, code)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger="apps.miniapp_api.views"):
                _post(client, service, master)
            return [
                r.getMessage() for r in caplog.records if "health_check_handoff" in r.getMessage()
            ]

        required = _lines(HEALTH_CHECK_REQUIRED)
        unknown = _lines(HEALTH_CHECK_UNKNOWN)

        assert required, "передача не оставила следа в журнале — считать нечего"
        assert unknown, "передача не оставила следа в журнале — считать нечего"
        assert any(HEALTH_CHECK_UNKNOWN in m for m in unknown)
        assert not any(HEALTH_CHECK_UNKNOWN in m for m in required), (
            "REQUIRED пишется как UNKNOWN — счётчик разметки будет завышен"
        )
