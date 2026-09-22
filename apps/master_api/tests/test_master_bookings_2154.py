"""Ручки мастера для записей (DRF-2154, М-2 эпика DRF-2150).

Красное листа: ``master_api/urls.py`` — ручек записи нет; мастер не может
ни открыть запись, ни создать её (замер 20.09, DRF-1184/DRF-1185 — «НЕТ»).

* h1 — детали своей записи: поля контракта, ``temporal_state`` по пяти
  состояниям DRF-1185 (по часам сервера, ``completed`` — только по статусу),
  ``minutes_until`` для upcoming; чужая запись того же салона, запись другого
  салона и несуществующая → один 404 одним телом; отменённая — 200 со
  ``status`` из закрытого словаря;
* h2 — клиент в деталях: «Анна П.» + ``last_visit_date`` из зеркала (есть /
  нет визитов / без bot_user → «Гость»), телефона нет ни ключом, ни цифрами;
* h3 — создание: 201 от имени мастера (actor — его external id, specialist —
  каталожный id мастера, не из тела); 409 ``slot_taken`` с ``alternatives`` /
  без них, когда слоты недоступны; 202 ``result_pending`` с ключом; услуга
  не привязана → 409; ровно один из ``client_id``/``client_name``; новый
  гость без телефона → 400; ``master_id`` в теле игнорируется;
* h4 — слоты: форма, 503 при недоступности (никогда пустой список), чужая
  услуга → 404, без ``service_id`` → 400;
* h5 — поиск ``customers?q=``: «Анна П.» + ``last_visit_date`` (join по
  ``ayla_user_id``), ``null`` для нового, телефон → 400, Ayla недоступна →
  503, без ``q`` — прежний ростер;
* h6 — соло-мастер: детали и создание под своим тенантом;
* h7 — тесты admin_api без правок зелёные (гоняются в PR, здесь — что
  admin-вьюхи и мастер зовут один сервис);
* h8 — PII: четыре маршрута классифицированы (``test_pii_boundary``),
  ``client_phone`` — только вход.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.admin_api import views_availability_slots, views_booking_create, views_customers
from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaSlot, BookingUnavailableError
from apps.integrations.ayla.salon_client import (
    SalonSlotTaken,
    SalonUnavailable,
    SalonValidationError,
)
from apps.master_api.pii import FORBIDDEN_PII_KEYS, find_forbidden_pii
from apps.master_api.services import bookings as mod
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant
from tests.support.pii_asserts import visible_text

pytestmark = pytest.mark.django_db

CUSTOMER_PHONE = "+79997775544"
CUSTOMER_PHONE_DIGITS = "9997775544"
CUSTOMER_AYLA_ID = uuid.UUID("2d8bbc4f-0000-4000-8000-000000002154")


# ─── фикстуры и стабы ───────────────────────────────────────────────────────


@pytest.fixture
def bridged_service(service: CatalogService) -> CatalogService:
    service.ayla_service_id = uuid.uuid4()
    service.save(update_fields=["ayla_service_id"])
    return service


@pytest.fixture
def customer(tenant: Tenant) -> BotUser:
    """Клиент мастера — с телефоном в БД, чтобы утечке было откуда взяться."""

    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="424242",
        display_name="anna_p",
        client_name="Анна Петрова",
        chat_id="424242",
        phone=CUSTOMER_PHONE,
        ayla_user_id=CUSTOMER_AYLA_ID,
    )


def _visit(
    master: CatalogMaster,
    *,
    start: datetime,
    minutes: int = 60,
    status: str = "confirmed",
    bot_user: BotUser | None = None,
    service: CatalogService | None = None,
    end: datetime | None = None,
) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        tenant=master.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=master.id,
        start_at=start,
        end_at=end if end is not None else start + timedelta(minutes=minutes),
        status=status,
        bot_user=bot_user,
        service_id=service.ayla_service_id if service else None,
    )


class _StubSalon:
    def __init__(self, *, exc: Exception | None = None, result=None, rows=None) -> None:
        self.exc = exc
        self.result = result or {"id": str(uuid.uuid4())}
        self.rows = rows if rows is not None else []
        self.calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append({"create_appointment": kwargs})
        if self.exc:
            raise self.exc
        return self.result

    def search_customers(self, **kwargs):
        self.calls.append({"search_customers": kwargs})
        if self.exc:
            raise self.exc
        return self.rows


class _StubSlots:
    def __init__(self, *, slots=None, exc: Exception | None = None) -> None:
        self.slots = slots or []
        self.exc = exc
        self.calls: list[dict] = []

    def get_available_times(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.slots


@pytest.fixture
def stub_salon(monkeypatch):
    def _install(stub: _StubSalon) -> _StubSalon:
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: stub)
        return stub

    return _install


@pytest.fixture
def stub_slots(monkeypatch):
    def _install(stub: _StubSlots) -> _StubSlots:
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        return stub

    return _install


def _slot(hhmm: str, iso: str, minutes: int = 60) -> AylaSlot:
    return AylaSlot(time=hhmm, datetime=iso, duration_s=minutes * 60)


def _now() -> datetime:
    return dj_timezone.now().replace(microsecond=0)


def _get_detail(client: Client, appointment_id, *, uid: str = "12345"):
    return client.get(
        reverse("master_api:booking_detail", args=[appointment_id]),
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


def _post(client: Client, service: CatalogService, *, uid: str = "12345", **over):
    body = {
        "service_id": str(service.id),
        "start_at": "2026-10-21T15:00:00+03:00",
        "client_name": "Мария",
        "client_phone": "+79990000000",
    }
    body.update(over)
    return client.post(
        reverse("master_api:create_booking"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


def _search(client: Client, q: str, *, uid: str = "12345"):
    return client.get(
        reverse("master_api:customers_list") + f"?q={q}",
        HTTP_AUTHORIZATION=init_data_header(uid),
    )


def _assert_no_customer_phone(resp) -> None:
    raw = resp.content.decode("utf-8")
    assert find_forbidden_pii(resp.json()) == []  # empty-assert-ok: тело проверено вызывающим
    assert CUSTOMER_PHONE not in raw
    assert CUSTOMER_PHONE_DIGITS not in raw
    assert "5544" not in visible_text(
        resp.json()
    )  # хвост — в видимом тексте: в raw есть случайные id (DRF-2278)


# ─── h1: детали и временные состояния ───────────────────────────────────────


class TestBookingDetail:
    def test_own_upcoming_booking_has_the_contract_shape(
        self, client, tenant, bot_user, accepted_master, customer, bridged_service
    ):
        now = _now()
        row = _visit(
            accepted_master,
            start=now + timedelta(minutes=80),
            bot_user=customer,
            service=bridged_service,
        )
        resp = _get_detail(client, row.appointment_id)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["id"] == str(row.appointment_id)
        assert body["client"] == {"name_initial": "Анна П.", "last_visit_date": None}
        assert body["service"] == {"id": str(bridged_service.id), "name": bridged_service.name}
        assert body["duration_min"] == 60
        assert body["status"] == "confirmed"
        assert body["temporal_state"] == "upcoming"
        assert body["minutes_until"] in (79, 80)
        # Время — со смещением тенанта (Europe/Moscow), не в UTC.
        assert body["start_at"].endswith("+03:00")
        assert body["end_at"].endswith("+03:00")
        assert body["checked_at"].endswith("+03:00")
        assert datetime.fromisoformat(body["start_at"]) == row.start_at
        assert set(body) == {
            "id",
            "client",
            "service",
            "start_at",
            "end_at",
            "duration_min",
            "status",
            "temporal_state",
            "minutes_until",
            "checked_at",
        }

    @pytest.mark.parametrize(
        ("start_offset_min", "status", "expected"),
        [
            (90, "confirmed", "upcoming"),
            (-10, "confirmed", "now"),
            (-70, "confirmed", "after"),  # окончание 10 минут назад — окно 3 ч
            (-60 - 179, "confirmed", "after"),  # окончание 2 ч 59 мин назад
            (-60 - 181, "confirmed", "unknown"),  # окончание 3 ч 1 мин назад — результата нет
            (-60 - 600, "completed", "completed"),
            (90, "completed", "completed"),  # только по статусу, не по часам
        ],
    )
    def test_temporal_state_by_server_clock(
        self,
        client,
        tenant,
        bot_user,
        accepted_master,
        start_offset_min,
        status,
        expected,
    ):
        row = _visit(
            accepted_master, start=_now() + timedelta(minutes=start_offset_min), status=status
        )
        body = _get_detail(client, row.appointment_id).json()
        assert body["temporal_state"] == expected
        if expected == "upcoming":
            assert body["minutes_until"] in (start_offset_min - 1, start_offset_min)
        else:
            assert body["minutes_until"] is None

    @pytest.mark.parametrize("status", ["cancelled", "no_show"])
    def test_a_released_booking_is_200_with_its_status(
        self, client, tenant, bot_user, accepted_master, status
    ):
        """Отменённая существует: 200, ``status`` из словаря, состояние по часам —
        экран решает по ``status`` раньше ``temporal_state``."""

        row = _visit(accepted_master, start=_now() + timedelta(minutes=30), status=status)
        resp = _get_detail(client, row.appointment_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == status
        assert resp.json()["temporal_state"] == "upcoming"

    def test_an_unfamiliar_raw_status_is_closed_to_unknown(
        self, client, tenant, bot_user, accepted_master
    ):
        row = _visit(accepted_master, start=_now() + timedelta(minutes=30), status="rescheduled")
        assert _get_detail(client, row.appointment_id).json()["status"] == "unknown"

    def test_someone_elses_booking_in_the_same_salon_is_404(
        self, client, tenant, bot_user, accepted_master
    ):
        colleague = make_master(tenant, name="Ольга Иванова", external_id=77)
        theirs = _visit(colleague, start=_now() + timedelta(hours=2))
        mine = _visit(accepted_master, start=_now() + timedelta(hours=3))
        # Присутствие: своя открывается — значит, отказ ниже про чужую, не про авторизацию.
        assert _get_detail(client, mine.appointment_id).status_code == 200
        resp = _get_detail(client, theirs.appointment_id)
        assert resp.status_code == 404
        assert resp.json() == {"error": "not_found", "detail": "booking not found"}

    def test_a_booking_of_another_salon_and_a_missing_one_answer_the_same(
        self, client, tenant, other_tenant, bot_user, accepted_master
    ):
        foreign_master = make_master(other_tenant, name="Чужой Мастер", external_id=5)
        foreign = _visit(foreign_master, start=_now() + timedelta(hours=2))
        mine = _visit(accepted_master, start=_now() + timedelta(hours=3))
        assert _get_detail(client, mine.appointment_id).status_code == 200
        foreign_resp = _get_detail(client, foreign.appointment_id)
        missing_resp = _get_detail(client, uuid.uuid4())
        assert foreign_resp.status_code == 404
        assert foreign_resp.json() == missing_resp.json()
        assert foreign_resp.status_code == missing_resp.status_code


# ─── h2: клиент в деталях ───────────────────────────────────────────────────


class TestClientInDetail:
    def test_name_initial_and_last_visit_before_this_booking(
        self, client, tenant, bot_user, accepted_master, customer
    ):
        now = _now()
        _visit(
            accepted_master, start=now - timedelta(days=40), status="completed", bot_user=customer
        )
        last = _visit(
            accepted_master, start=now - timedelta(days=12), status="completed", bot_user=customer
        )
        # Более поздний completed НЕ считается — «была» до этой записи.
        _visit(
            accepted_master, start=now + timedelta(days=1), status="completed", bot_user=customer
        )
        # Отменённый не визит.
        _visit(
            accepted_master, start=now - timedelta(days=2), status="cancelled", bot_user=customer
        )
        row = _visit(accepted_master, start=now + timedelta(hours=1), bot_user=customer)
        body = _get_detail(client, row.appointment_id).json()
        assert body["client"]["name_initial"] == "Анна П."
        expected = last.start_at.astimezone(mod.get_tenant_tz(tenant)).date().isoformat()
        assert body["client"]["last_visit_date"] == expected

    def test_a_visit_with_another_master_does_not_count(
        self, client, tenant, bot_user, accepted_master, customer
    ):
        colleague = make_master(tenant, name="Ольга Иванова", external_id=78)
        _visit(colleague, start=_now() - timedelta(days=3), status="completed", bot_user=customer)
        row = _visit(accepted_master, start=_now() + timedelta(hours=1), bot_user=customer)
        body = _get_detail(client, row.appointment_id).json()
        assert body["client"]["name_initial"] == "Анна П."
        assert body["client"]["last_visit_date"] is None

    def test_an_orphan_row_shows_a_guest(self, client, tenant, bot_user, accepted_master):
        row = _visit(accepted_master, start=_now() + timedelta(hours=1))
        assert _get_detail(client, row.appointment_id).json()["client"] == {
            "name_initial": "Гость",
            "last_visit_date": None,
        }

    def test_display_name_fallback_when_client_name_is_empty(
        self, client, tenant, bot_user, accepted_master, customer
    ):
        customer.client_name = ""
        customer.display_name = "Анна"
        customer.save(update_fields=["client_name", "display_name"])
        row = _visit(accepted_master, start=_now() + timedelta(hours=1), bot_user=customer)
        assert _get_detail(client, row.appointment_id).json()["client"]["name_initial"] == "Анна"

    def test_no_customer_phone_in_any_form(
        self, client, tenant, bot_user, accepted_master, customer, bridged_service
    ):
        row = _visit(
            accepted_master,
            start=_now() + timedelta(hours=1),
            bot_user=customer,
            service=bridged_service,
        )
        resp = _get_detail(client, row.appointment_id)
        # Присутствие: имя клиента в теле — свип идёт по его записи.
        assert resp.json()["client"]["name_initial"] == "Анна П."
        _assert_no_customer_phone(resp)


# ─── h3: создание ───────────────────────────────────────────────────────────


class TestCreateBooking:
    def test_committed_under_the_masters_own_identity(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon
    ):
        stub = stub_salon(_StubSalon(result={"id": "a1b2"}))
        resp = _post(client, bridged_service, client_id="c-1", client_name="", client_phone="")
        assert resp.status_code == 201, resp.content
        assert resp.json() == {
            "outcome": "committed",
            "detail": "appointment created",
            "appointment_id": "a1b2",
        }
        call = stub.calls[0]["create_appointment"]
        assert call["actor_external_id"] == "bot:max:12345"
        assert call["specialist_id"] == str(accepted_master.catalog_specialist_id)
        assert call["service_id"] == str(bridged_service.ayla_service_id)
        assert call["tenant_slug"] == tenant.slug
        assert call["client_id"] == "c-1"
        assert call["idempotency_key"]

    def test_master_id_in_the_body_is_ignored(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon
    ):
        colleague = make_master(tenant, name="Ольга Иванова", external_id=79)
        stub = stub_salon(_StubSalon())
        resp = _post(client, bridged_service, master_id=str(colleague.id))
        assert resp.status_code == 201
        assert stub.calls[0]["create_appointment"]["specialist_id"] == str(
            accepted_master.catalog_specialist_id
        )

    def test_slot_taken_answers_409_with_nearest_alternatives(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon, stub_slots
    ):
        stub_salon(_StubSalon(exc=SalonSlotTaken("это время уже занято")))
        slots = stub_slots(
            _StubSlots(
                slots=[
                    _slot("14:00", "2026-10-21T14:00:00+03:00"),
                    _slot("15:00", "2026-10-21T15:00:00+03:00"),  # само занятое — не вариант
                    _slot("16:00", "2026-10-21T16:00:00+03:00"),
                    _slot("17:00", "2026-10-21T17:00:00+03:00"),
                    _slot("18:00", "2026-10-21T18:00:00+03:00"),
                    _slot("19:00", "2026-10-21T19:00:00+03:00"),
                    _slot("20:00", "2026-10-21T20:00:00+03:00"),
                ]
            )
        )
        resp = _post(client, bridged_service)
        assert resp.status_code == 409
        body = resp.json()
        assert body["outcome"] == "conflict"
        assert body["reason_code"] == "slot_taken"
        assert body["alternatives_unavailable"] is False
        assert [a["time"] for a in body["alternatives"]] == [
            "14:00",
            "16:00",
            "17:00",
            "18:00",
            "19:00",
        ]
        assert body["alternatives"][0] == {
            "time": "14:00",
            "start_at": "2026-10-21T14:00:00+03:00",
            "duration_min": 60,
        }
        assert slots.calls[0]["date"] == "2026-10-21"
        assert slots.calls[0]["specialist_id"] == str(accepted_master.catalog_specialist_id)

    def test_slot_taken_stays_409_when_alternatives_are_unavailable(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon, stub_slots
    ):
        stub_salon(_StubSalon(exc=SalonSlotTaken("занято")))
        stub_slots(_StubSlots(exc=BookingUnavailableError("timeout")))
        resp = _post(client, bridged_service)
        assert resp.status_code == 409
        body = resp.json()
        assert body["reason_code"] == "slot_taken"
        assert body["alternatives_unavailable"] is True
        assert body["alternatives"] is None

    def test_no_answer_is_202_result_pending_with_the_key(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon
    ):
        stub_salon(_StubSalon(exc=SalonUnavailable("timeout")))
        resp = _post(client, bridged_service, idempotency_key="k-2154")
        assert resp.status_code == 202
        assert resp.json() == {
            "outcome": "pending",
            "reason_code": "result_pending",
            "detail": "the schedule did not answer — refresh the day before trying again",
            "idempotency_key": "k-2154",
        }

    def test_ayla_not_found_is_conflict_404_with_outcome_envelope(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon
    ):
        """Ayla не знает клиента как этого салона — приглашение записать его
        новым гостем: конфликт, не тупик (и ``outcome``, не ``error``)."""

        from apps.integrations.ayla.salon_client import SalonNotFound

        stub_salon(_StubSalon(exc=SalonNotFound("client not found")))
        resp = _post(client, bridged_service, client_id="c-404", client_name="", client_phone="")
        assert resp.status_code == 404
        assert resp.json()["outcome"] == "conflict"
        assert "error" not in resp.json()

    def test_alternatives_day_for_a_naive_start_is_the_salons_day(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon, stub_slots
    ):
        """``2026-10-21T23:30`` без смещения — 21-е по Москве, а не 22-е по UTC-хосту."""

        stub_salon(_StubSalon(exc=SalonSlotTaken("занято")))
        slots = stub_slots(_StubSlots(slots=[]))
        resp = _post(client, bridged_service, start_at="2026-10-21T23:30:00")
        assert resp.status_code == 409
        assert slots.calls[0]["date"] == "2026-10-21"

    def test_ayla_validation_is_blocked_400(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon
    ):
        stub_salon(_StubSalon(exc=SalonValidationError("start_datetime is in the past")))
        resp = _post(client, bridged_service)
        assert resp.status_code == 400
        assert resp.json()["outcome"] == "blocked"

    def test_unbridged_service_is_409_before_ayla(
        self, client, tenant, bot_user, accepted_master, service, stub_salon
    ):
        stub = stub_salon(_StubSalon())
        resp = _post(client, service)
        assert resp.status_code == 409
        assert resp.json()["error"] == "service_not_bookable"
        assert stub.calls == []  # empty-assert-ok: 409 выше доказан — до Ayla не дошло

    def test_a_service_of_another_salon_is_404(
        self, client, tenant, other_tenant, bot_user, accepted_master, stub_salon
    ):
        foreign = CatalogService.all_tenants.create(
            tenant=other_tenant,
            external_id=9,
            external_updated_at=datetime.now(tz=timezone.utc),
            slug="foreign",
            name="Чужая",
            duration_min=30,
            is_active=True,
            ayla_service_id=uuid.uuid4(),
        )
        stub_salon(_StubSalon())
        resp = _post(client, foreign)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    @pytest.mark.parametrize(
        ("over", "detail"),
        [
            ({"client_id": "c-1"}, "provide exactly one of client_id or client_name"),
            (
                {"client_name": "", "client_phone": ""},
                "provide exactly one of client_id or client_name",
            ),
            ({"client_phone": ""}, "a new guest needs a name and a phone"),
            ({"start_at": ""}, "required: start_at"),
        ],
    )
    def test_body_validation(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon, over, detail
    ):
        stub = stub_salon(_StubSalon())
        resp = _post(client, bridged_service, **over)
        assert resp.status_code == 400
        assert resp.json()["detail"] == detail
        assert stub.calls == []  # empty-assert-ok: 400 выше доказан — до Ayla не дошло

    def test_the_new_guests_phone_is_input_only(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_salon
    ):
        stub = stub_salon(_StubSalon())
        resp = _post(client, bridged_service, client_phone=CUSTOMER_PHONE)
        assert resp.status_code == 201
        # Вход дошёл до Ayla — телефон нужен ей, чтобы завести гостя.
        assert stub.calls[0]["create_appointment"]["client_phone"] == CUSTOMER_PHONE
        _assert_no_customer_phone(resp)


# ─── h4: слоты ──────────────────────────────────────────────────────────────


class TestBookingSlots:
    def _get(self, client, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(
            reverse("master_api:booking_slots") + (f"?{query}" if query else ""),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

    def test_shape_for_the_masters_own_day(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_slots
    ):
        stub = stub_slots(_StubSlots(slots=[_slot("10:00", "2026-10-21T10:00:00+03:00")]))
        resp = self._get(client, date="2026-10-21", service_id=bridged_service.id)
        assert resp.status_code == 200, resp.content
        assert resp.json() == {
            "date": "2026-10-21",
            "timezone": "Europe/Moscow",
            "service_id": str(bridged_service.id),
            "duration_min": 60,
            "slots": [
                {"time": "10:00", "start_at": "2026-10-21T10:00:00+03:00", "duration_min": 60}
            ],
        }
        assert stub.calls[0]["specialist_id"] == str(accepted_master.catalog_specialist_id)

    def test_unreachable_schedule_is_503_never_an_empty_list(
        self, client, tenant, bot_user, accepted_master, bridged_service, stub_slots
    ):
        stub_slots(_StubSlots(exc=BookingUnavailableError("down")))
        resp = self._get(client, date="2026-10-21", service_id=bridged_service.id)
        assert resp.status_code == 503
        assert resp.json()["error"] == "schedule_unavailable"

    def test_service_id_is_required(self, client, tenant, bot_user, accepted_master, stub_slots):
        stub = stub_slots(_StubSlots())
        resp = self._get(client, date="2026-10-21")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "required: service_id"
        assert stub.calls == []  # empty-assert-ok: 400 выше доказан

    def test_a_service_of_another_salon_is_404(
        self, client, tenant, other_tenant, bot_user, accepted_master, stub_slots
    ):
        foreign = CatalogService.all_tenants.create(
            tenant=other_tenant,
            external_id=9,
            external_updated_at=datetime.now(tz=timezone.utc),
            slug="foreign",
            name="Чужая",
            duration_min=30,
            is_active=True,
            ayla_service_id=uuid.uuid4(),
        )
        stub_slots(_StubSlots())
        resp = self._get(client, date="2026-10-21", service_id=foreign.id)
        assert resp.status_code == 404


# ─── h5: поиск клиента ──────────────────────────────────────────────────────


class TestCustomerSearch:
    def test_name_initial_and_last_visit_with_this_master(
        self, client, tenant, bot_user, accepted_master, customer, stub_salon
    ):
        last = _visit(
            accepted_master,
            start=_now() - timedelta(days=12),
            status="completed",
            bot_user=customer,
        )
        newcomer_id = str(uuid.uuid4())
        stub = stub_salon(
            _StubSalon(
                rows=[
                    {"id": str(CUSTOMER_AYLA_ID), "name": "Анна Петрова"},
                    {"id": newcomer_id, "name": "Анна Сидорова"},
                    {"id": str(uuid.uuid4()), "name": "bot:max:83100000"},
                ]
            )
        )
        resp = _search(client, "Анна")
        assert resp.status_code == 200, resp.content
        expected_date = last.start_at.astimezone(mod.get_tenant_tz(tenant)).date().isoformat()
        assert resp.json()["results"] == [
            {
                "id": str(CUSTOMER_AYLA_ID),
                "name": "Анна П.",
                "named": True,
                "last_visit_date": expected_date,
            },
            {"id": newcomer_id, "name": "Анна С.", "named": True, "last_visit_date": None},
            {
                "id": resp.json()["results"][2]["id"],
                "name": "Без имени",
                "named": False,
                "last_visit_date": None,
            },
        ]
        call = stub.calls[0]["search_customers"]
        assert call["actor_external_id"] == "bot:max:12345"
        assert call["query"] == "Анна"
        _assert_no_customer_phone(resp)

    def test_two_channels_of_one_ayla_user_fold_into_the_latest_visit(
        self, client, tenant, bot_user, accepted_master, customer, stub_salon
    ):
        """У одного Ayla-пользователя две строки BotUser (по каналу) — дата
        последнего визита сводится по обеим, а не по первой попавшейся."""

        telegram_row = BotUser.all_tenants.create(
            tenant=tenant,
            channel="telegram",
            channel_user_id="tg-424242",
            display_name="Анна",
            chat_id="tg-424242",
            ayla_user_id=CUSTOMER_AYLA_ID,
        )
        _visit(
            accepted_master,
            start=_now() - timedelta(days=30),
            status="completed",
            bot_user=customer,
        )
        later = _visit(
            accepted_master,
            start=_now() - timedelta(days=5),
            status="completed",
            bot_user=telegram_row,
        )
        stub_salon(_StubSalon(rows=[{"id": str(CUSTOMER_AYLA_ID), "name": "Анна Петрова"}]))
        results = _search(client, "Анна").json()["results"]
        assert results[0]["name"] == "Анна П."
        expected = later.start_at.astimezone(mod.get_tenant_tz(tenant)).date().isoformat()
        assert results[0]["last_visit_date"] == expected

    def test_a_visit_with_another_master_is_a_new_client_here(
        self, client, tenant, bot_user, accepted_master, customer, stub_salon
    ):
        colleague = make_master(tenant, name="Ольга Иванова", external_id=80)
        _visit(colleague, start=_now() - timedelta(days=3), status="completed", bot_user=customer)
        stub_salon(_StubSalon(rows=[{"id": str(CUSTOMER_AYLA_ID), "name": "Анна Петрова"}]))
        results = _search(client, "Анна").json()["results"]
        assert results[0]["name"] == "Анна П."
        assert results[0]["last_visit_date"] is None

    @pytest.mark.parametrize("q", ["+7 999 777 55 44", "9997775544", "8(999)777-55-44"])
    def test_search_by_phone_is_closed(
        self, client, tenant, bot_user, accepted_master, stub_salon, q
    ):
        stub = stub_salon(_StubSalon(rows=[{"id": str(uuid.uuid4()), "name": "Анна"}]))
        resp = _search(client, q)
        assert resp.status_code == 400
        assert resp.json()["error"] == "phone_search_closed"
        assert stub.calls == []  # empty-assert-ok: 400 выше доказан — до Ayla не дошло

    def test_a_short_query_is_400_from_ayla_rules(
        self, client, tenant, bot_user, accepted_master, stub_salon
    ):
        stub_salon(_StubSalon(exc=SalonValidationError("query must be at least 2 characters")))
        resp = _search(client, "А")
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_unavailable_search_is_503_never_an_empty_list(
        self, client, tenant, bot_user, accepted_master, stub_salon
    ):
        stub_salon(_StubSalon(exc=SalonUnavailable("down")))
        resp = _search(client, "Анна")
        assert resp.status_code == 503
        assert resp.json()["error"] == "unavailable"

    def test_without_q_the_roster_is_unchanged(self, client, tenant, bot_user, accepted_master):
        resp = client.get(
            reverse("master_api:customers_list"), HTTP_AUTHORIZATION=init_data_header("12345")
        )
        assert resp.status_code == 200
        assert "customers" in resp.json()
        assert "results" not in resp.json()


# ─── h6: соло-мастер ────────────────────────────────────────────────────────


class TestSoloMaster:
    @pytest.fixture
    def solo(self, db):
        tenant = Tenant.objects.create(
            slug="solo-2154", name="Кабинет соло", timezone="Europe/Moscow"
        )
        row = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="55555",
            display_name="Соло",
            chat_id="55555",
        )
        master = make_master(
            tenant,
            name="Соло Мастер",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=row,
        )
        # У соло-мастера первичный ключ — uuid4, каталожный id — другой
        # (DRF-1933); событие Ayla пишет в зеркало каталожный.
        master.catalog_specialist_id = uuid.uuid4()
        master.save(update_fields=["catalog_specialist_id"])
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=datetime.now(tz=timezone.utc),
            slug="solo-svc",
            name="Стрижка",
            duration_min=45,
            is_active=True,
            ayla_service_id=uuid.uuid4(),
        )
        return tenant, master, service

    def test_detail_and_create_in_the_own_tenant(self, client, solo, stub_salon):
        """Зеркало знает соло-мастера по каталожному id, не по pk — после
        201 детали своей записи открываются, а не 404 (замечание ревью)."""

        tenant, master, service = solo
        assert master.catalog_specialist_id != master.id
        row = RemoteBookingProxy.all_tenants.create(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            specialist_id=master.catalog_specialist_id,
            start_at=_now() + timedelta(hours=2),
            end_at=_now() + timedelta(hours=3),
            status="confirmed",
            service_id=service.ayla_service_id,
        )
        resp = _get_detail(client, row.appointment_id, uid="55555")
        assert resp.status_code == 200, resp.content
        assert resp.json()["service"]["name"] == "Стрижка"
        assert resp.json()["temporal_state"] == "upcoming"

        stub = stub_salon(_StubSalon())
        created = _post(
            client, service, uid="55555", client_id="c-9", client_name="", client_phone=""
        )
        assert created.status_code == 201, created.content
        call = stub.calls[0]["create_appointment"]
        assert call["tenant_slug"] == "solo-2154"
        assert call["specialist_id"] == str(master.catalog_specialist_id)
        assert call["actor_external_id"] == "bot:max:55555"

    def test_last_visit_date_folds_both_specialist_keys(self, client, solo, stub_salon):
        tenant, master, service = solo
        anna = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="424243",
            client_name="Анна Петрова",
            chat_id="424243",
            ayla_user_id=CUSTOMER_AYLA_ID,
        )
        done = RemoteBookingProxy.all_tenants.create(
            tenant=tenant,
            appointment_id=uuid.uuid4(),
            specialist_id=master.catalog_specialist_id,
            start_at=_now() - timedelta(days=9),
            end_at=_now() - timedelta(days=9) + timedelta(hours=1),
            status="completed",
            bot_user=anna,
        )
        stub_salon(_StubSalon(rows=[{"id": str(CUSTOMER_AYLA_ID), "name": "Анна Петрова"}]))
        results = _search(client, "Анна", uid="55555").json()["results"]
        assert results[0]["name"] == "Анна П."
        expected = done.start_at.astimezone(mod.get_tenant_tz(tenant)).date().isoformat()
        assert results[0]["last_visit_date"] == expected


# ─── h7: один сервис на две поверхности ─────────────────────────────────────


class TestOneServiceForBothSurfaces:
    def test_admin_views_and_master_views_share_the_core(self):
        from apps.admin_api.services import booking as core
        from apps.master_api import views_bookings

        for module in (
            views_booking_create,
            views_availability_slots,
            views_customers,
            views_bookings,
        ):
            assert module.__name__
        assert views_booking_create.create_appointment_as is core.create_appointment_as
        assert views_bookings.create_appointment_as is core.create_appointment_as
        assert views_availability_slots.bookable_starts is core.bookable_starts
        assert views_bookings.bookable_starts is core.bookable_starts
        assert views_customers.search_customers_as is core.search_customers_as
        assert views_bookings.search_customers_as is core.search_customers_as


# ─── h8: PII-граница ────────────────────────────────────────────────────────


class TestPiiBoundary:
    def test_the_four_routes_are_classified(self):
        from apps.master_api.tests.test_pii_boundary import NOT_SWEPT_ROUTES, SWEPT_READ_ROUTES

        classified = set(SWEPT_READ_ROUTES) | set(NOT_SWEPT_ROUTES)
        for name in ("booking_detail", "create_booking", "booking_slots", "customers_list"):
            assert name in classified, name

    def test_client_phone_is_not_a_forbidden_key_but_never_echoed(self):
        """``client_phone`` — вход; список запрещённых ключей его не содержит,
        а ответы проверены выше (``_assert_no_customer_phone``)."""

        assert "phone" in FORBIDDEN_PII_KEYS
        assert "client_phone" not in FORBIDDEN_PII_KEYS


# ─── предикаты ──────────────────────────────────────────────────────────────


class TestHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Анна Петрова", "Анна П."),
            ("Анна", "Анна"),
            ("  Анна   Петрова-Сидорова ", "Анна П."),
            ("", "Гость"),
            (None, "Гость"),
        ],
    )
    def test_name_initial(self, raw, expected):
        assert mod.name_initial(raw) == expected

    @pytest.mark.parametrize("q", ["+7 999 777 55 44", "9997775544", "8 (999) 777-55-44", "12345"])
    def test_phone_shaped(self, q):
        assert mod.looks_like_phone(q) is True

    @pytest.mark.parametrize("q", ["Анна", "Анна 2", "1234", "", "  ", "Мария-Анна"])
    def test_not_phone_shaped(self, q):
        assert mod.looks_like_phone(q) is False
