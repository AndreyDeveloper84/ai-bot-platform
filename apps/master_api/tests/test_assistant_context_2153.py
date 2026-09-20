"""Ayla для мастера по макету DRF-1187 (DRF-2153, М-5) — бэкенд.

Красное листа: стартовый экран без контекста дня и подсказок; ответ о
свободном времени — свободный текст; записи через ассистента нет
(``assistant_actions:46-48``); уточнения клиента нет; «данные могли
устареть» не существует как понятие.

* h1 — ``GET assistant/context``: «Сегодня N записей · Следующая — Анна П.
  в 10:30 · Классический массаж · 60 мин» / «Сегодня записей нет»; четыре
  чипа макета; телефона нет;
* h2 — ``ask`` → ``cards``: ``free_windows`` (окна + дверь в М-3 с
  ``?date&from&to``), ``day``; окна — по живой рамке рабочих часов, а при
  отказе источника — ``stale: true`` + «Не удалось проверить актуальное
  расписание. Последние известные данные» (уверенного «свободно» без
  подтверждения — нет);
* h3 — ``prepare_booking`` мастера: карточка клиент/услуга/длительность/
  дата/время + «Подтвердить»; одноимённые → ``clarify_client`` «Анна П. ·
  была 12.05» / «Анна П. · новый клиент» без телефона; ``select`` уточнения
  уходит модели; услуга не названа → короткий вопрос;
* h4 — ``confirm``: создание тем же сервисом, что М-2 (actor — мастер,
  каталожный id); «Запись создана» + «Открыть запись» → детали; «Это время
  занято» + 2–4 варианта, без тихого переноса; ответ не пришёл →
  «Проверяем результат»;
* h5 — ``block_time`` на день с записями → конфликт, не заявка;
* h6 — PII: клиент с телефоном в БД — ни одной цифры в context/ask/confirm.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.integrations.ayla.salon_client import SalonSlotTaken, SalonUnavailable
from apps.master_api.pii import find_forbidden_pii
from apps.master_api.services import assistant_actions as actions_mod
from apps.master_api.tests.conftest import init_data_header
from apps.master_api.services import assistant as assistant_mod
from apps.master_api.tests.test_assistant_api import FakeResult, FakeToolCall
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CONTEXT_URL = reverse("master_api:assistant_context")
ASK_URL = reverse("master_api:assistant_ask")
CONFIRM_URL = reverse("master_api:assistant_confirm")

CUSTOMER_PHONE = "+79997775544"
ANNA_P_AYLA_ID = uuid.UUID("2d8bbc4f-0000-4000-8000-000000002153")
ANNA_S_AYLA_ID = uuid.UUID("2d8bbc4f-0000-4000-8000-000000002154")

CHIPS = [
    "Что у меня сегодня?",
    "Когда я свободен завтра?",
    "Добавить запись",
    "Изменить рабочий день",
]


# ─── фикстуры ───────────────────────────────────────────────────────────────


@pytest.fixture
def llm():
    """Подменённый провайдер — та же форма, что в test_assistant_api (fixture не
    импортируется: ruff читает параметр как переопределение импорта)."""

    calls: list[dict[str, Any]] = []
    scripted: list[FakeResult] = []

    def fake_complete(messages, *, tenant, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        return scripted.pop(0) if scripted else FakeResult(text="ответ")

    with patch.object(assistant_mod, "_complete", side_effect=fake_complete):
        yield {"calls": calls, "script": scripted}


@pytest.fixture
def bridged_service(service: CatalogService, master_service: MasterService) -> CatalogService:
    service.ayla_service_id = uuid.uuid4()
    service.name = "Классический массаж"
    service.save(update_fields=["ayla_service_id", "name"])
    return service


@pytest.fixture
def anna(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="424242",
        display_name="anna_p",
        client_name="Анна Петрова",
        chat_id="424242",
        phone=CUSTOMER_PHONE,
        ayla_user_id=ANNA_P_AYLA_ID,
    )


def _tz(tenant: Tenant) -> ZoneInfo:
    return ZoneInfo(tenant.timezone)


def _today_at(tenant: Tenant, hour: int, minute: int = 0) -> datetime:
    tz = _tz(tenant)
    local = dj_timezone.now().astimezone(tz)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _visit(
    master: CatalogMaster, *, start: datetime, minutes: int = 60, bot_user=None, service=None
):
    return RemoteBookingProxy.all_tenants.create(
        tenant=master.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=master.catalog_specialist_id,
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        status="confirmed",
        bot_user=bot_user,
        service_id=service.ayla_service_id if service else None,
    )


def _context(client: Client):
    return client.get(CONTEXT_URL, HTTP_AUTHORIZATION=init_data_header("12345"))


def _ask_with(client: Client, text: str, select: dict[str, Any] | None = None):
    body: dict[str, Any] = {"text": text}
    if select:
        body["select"] = select
    return client.post(
        ASK_URL,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


def _confirm(client: Client, token: str):
    return client.post(
        CONFIRM_URL,
        data=json.dumps({"token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


class _StubSalon:
    def __init__(self, *, exc: Exception | None = None, result=None, rows=None) -> None:
        self.exc = exc
        self.result = result or {"id": "a-2153"}
        self.rows = rows if rows is not None else []
        self.calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append({"create_appointment": kwargs})
        if self.exc:
            raise self.exc
        return self.result

    def search_customers(self, **kwargs):
        self.calls.append({"search_customers": kwargs})
        return self.rows


class _StubSlots:
    def __init__(self, slots=None) -> None:
        self.slots = slots or []

    def get_available_times(self, **kwargs):
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


def _tomorrow_1230() -> str:
    """Завтра 12:30 по часам салона (Europe/Moscow), не UTC."""

    local = dj_timezone.now().astimezone(ZoneInfo("Europe/Moscow")) + timedelta(days=1)
    return local.replace(hour=12, minute=30, second=0, microsecond=0).isoformat()


def _prepare_call(**over) -> FakeToolCall:
    args = {
        "client_name": "Анна",
        "service": "массаж",
        "start_at": _tomorrow_1230(),
    }
    args.update(over)
    return FakeToolCall(name="prepare_booking", arguments=args)


def _assert_no_phone(raw: str, body: object) -> None:
    assert find_forbidden_pii(body) == []  # empty-assert-ok: тело проверено вызывающим
    assert CUSTOMER_PHONE not in raw
    assert "9997775544" not in raw
    assert "5544" not in raw


# ─── h1: контекст дня ───────────────────────────────────────────────────────


class TestContext:
    def test_today_with_visits_names_the_next_one(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service
    ):
        soon = dj_timezone.now() + timedelta(minutes=45)
        _visit(accepted_master, start=soon, bot_user=anna, service=bridged_service)
        _visit(accepted_master, start=soon + timedelta(hours=3))
        resp = _context(client)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["today"]["count"] == 2
        nxt = body["today"]["next"]
        assert nxt["client_name_initial"] == "Анна П."
        assert nxt["service_name"] == "Классический массаж"
        assert nxt["duration_min"] == 60
        assert nxt["time"] == soon.astimezone(_tz(tenant)).strftime("%H:%M")
        assert body["chips"] == CHIPS
        _assert_no_phone(resp.content.decode(), body)

    def test_today_without_visits(self, client, tenant, bot_user, accepted_master):
        body = _context(client).json()
        assert body["today"]["count"] == 0
        assert body["today"]["next"] is None
        assert body["chips"] == CHIPS


# ─── h2: структурированные ответы ───────────────────────────────────────────


class TestCards:
    def test_free_slots_answer_carries_windows_and_a_booking_door(
        self, client, tenant, bot_user, accepted_master, llm, monkeypatch
    ):
        from datetime import time as time_cls

        tomorrow = (dj_timezone.now().astimezone(_tz(tenant)) + timedelta(days=1)).date()
        # Живая рамка: 10:00–18:00; занято 12:00–15:00 → окна 10:00–12:00 и 15:00–18:00.
        monkeypatch.setattr(
            "apps.master_api.services.assistant_tools._working_block",
            lambda master, day: (time_cls(10, 0), time_cls(18, 0), True),
        )
        _visit(
            accepted_master,
            start=datetime.combine(tomorrow, time_cls(12, 0), tzinfo=_tz(tenant)),
            minutes=180,
        )
        llm["script"].append(
            FakeResult(
                tool_calls=[
                    FakeToolCall(name="free_slots", arguments={"date": tomorrow.isoformat()})
                ]
            )
        )
        llm["script"].append(FakeResult(text="Завтра свободно: 10:00–12:00 · 15:00–18:00"))
        resp = _ask_with(client, "Когда я свободен завтра?")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        cards = [c for c in body["cards"] if c["kind"] == "free_windows"]
        assert len(cards) == 1
        card = cards[0]
        assert card["date"] == tomorrow.isoformat()
        assert card["stale"] is False
        assert [(w["start"], w["end"]) for w in card["windows"]] == [
            ("10:00", "12:00"),
            ("15:00", "18:00"),
        ]
        # Поверхность — /master или /solo (в тестовом салоне один мастер → соло).
        assert re.fullmatch(
            rf"/(master|solo)/booking/new\?date={tomorrow.isoformat()}&from=10%3A00&to=12%3A00",
            card["windows"][0]["book_url"],
        )

    def test_unreadable_frame_is_stale_never_a_confident_free(
        self, client, tenant, bot_user, accepted_master, llm, monkeypatch
    ):
        tomorrow = (dj_timezone.now().astimezone(_tz(tenant)) + timedelta(days=1)).date()

        def _boom(master, day):
            raise SalonUnavailable("timeout")

        monkeypatch.setattr("apps.master_api.services.assistant_tools._working_block", _boom)
        llm["script"].append(
            FakeResult(
                tool_calls=[
                    FakeToolCall(name="free_slots", arguments={"date": tomorrow.isoformat()})
                ]
            )
        )
        llm["script"].append(FakeResult(text="Завтра свободно весь день."))
        body = _ask_with(client, "Когда я свободен завтра?").json()
        card = next(c for c in body["cards"] if c["kind"] == "free_windows")
        assert card["stale"] is True
        assert (
            card["notice"]
            == "Не удалось проверить актуальное расписание. Последние известные данные"
        )
        assert card["recheck"] == "Проверить снова"
        # Присутствие: окна по последним известным данным есть.
        assert len(card["windows"]) >= 1
        # Инструмент сказал модели, что данные не подтверждены.
        tool_note = llm["calls"][1]["messages"][-1]["content"]
        assert '"stale": true' in tool_note

    def test_my_day_answer_carries_a_day_card(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service, llm
    ):
        soon = dj_timezone.now() + timedelta(minutes=45)
        _visit(accepted_master, start=soon, bot_user=anna, service=bridged_service)
        today = dj_timezone.now().astimezone(_tz(tenant)).date().isoformat()
        llm["script"].append(
            FakeResult(tool_calls=[FakeToolCall(name="my_day", arguments={"date": today})])
        )
        llm["script"].append(FakeResult(text="Сегодня одна запись."))
        resp = _ask_with(client, "Что у меня сегодня?")
        body = resp.json()
        card = next(c for c in body["cards"] if c["kind"] == "day")
        assert card["count"] == 1
        assert card["visits"][0]["client"] == "Анна П."
        assert card["visits"][0]["service"] == "Классический массаж"
        _assert_no_phone(resp.content.decode(), body)


# ─── h3: запись через ассистента — предложение ──────────────────────────────


class TestPrepareBooking:
    def test_unique_client_yields_a_card_and_a_confirm(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service, llm, stub_salon
    ):
        stub_salon(_StubSalon(rows=[{"id": str(ANNA_P_AYLA_ID), "name": "Анна Петрова"}]))
        _visit(accepted_master, start=dj_timezone.now() - timedelta(days=12), bot_user=anna)
        RemoteBookingProxy.all_tenants.filter(bot_user=anna).update(status="completed")
        llm["script"].append(FakeResult(tool_calls=[_prepare_call()]))
        resp = _ask_with(client, "Запиши Анну на массаж завтра в 12:30")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        pa = body["pending_action"]
        assert pa["action"] == "prepare_booking"
        assert pa["confirm_label"] == "Подтвердить"
        assert pa["details"]["client"] == "Анна П."
        assert pa["details"]["service"] == "Классический массаж"
        assert pa["details"]["duration_min"] == 60
        assert pa["details"]["time"] == "12:30"
        assert pa["details"]["date"]
        assert pa["token"]
        _assert_no_phone(resp.content.decode(), body)

    def test_two_annas_ask_who_without_a_phone(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service, llm, stub_salon
    ):
        stub_salon(
            _StubSalon(
                rows=[
                    {"id": str(ANNA_P_AYLA_ID), "name": "Анна Петрова"},
                    {"id": str(ANNA_S_AYLA_ID), "name": "Анна Сидорова"},
                ]
            )
        )
        done = _visit(accepted_master, start=dj_timezone.now() - timedelta(days=12), bot_user=anna)
        RemoteBookingProxy.all_tenants.filter(pk=done.pk).update(status="completed")
        llm["script"].append(FakeResult(tool_calls=[_prepare_call()]))
        resp = _ask_with(client, "Запиши Анну на массаж завтра в 12:30")
        body = resp.json()
        assert body["pending_action"] is None
        assert body["answer"] == "Нашла двух клиентов с таким именем. Кого вы имеете в виду?"
        card = next(c for c in body["cards"] if c["kind"] == "clarify_client")
        expected_date = done.start_at.astimezone(_tz(tenant)).strftime("%d.%m")
        assert [o["label"] for o in card["options"]] == [
            f"Анна П. · была {expected_date}",
            "Анна С. · новый клиент",
        ]
        assert card["options"][0]["client_id"] == str(ANNA_P_AYLA_ID)
        _assert_no_phone(resp.content.decode(), body)

    def test_selected_client_reaches_the_model(
        self, client, tenant, bot_user, accepted_master, bridged_service, llm
    ):
        llm["script"].append(FakeResult(text="Уточнил."))
        _ask_with(client, "Анна П. · была 12.05", select={"client_id": str(ANNA_P_AYLA_ID)})
        last_user = [m for m in llm["calls"][0]["messages"] if m["role"] == "user"][-1]["content"]
        assert f"client_id={ANNA_P_AYLA_ID}" in last_user

    def test_missing_service_is_a_short_question(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service, llm, stub_salon
    ):
        stub_salon(_StubSalon(rows=[{"id": str(ANNA_P_AYLA_ID), "name": "Анна Петрова"}]))
        llm["script"].append(FakeResult(tool_calls=[_prepare_call(service="")]))
        body = _ask_with(client, "Запиши Анну завтра в 12:30").json()
        assert body["pending_action"] is None
        assert body["answer"] == "Какая услуга?"

    def test_unknown_client_is_not_invented(
        self, client, tenant, bot_user, accepted_master, bridged_service, llm, stub_salon
    ):
        stub_salon(_StubSalon(rows=[]))
        llm["script"].append(FakeResult(tool_calls=[_prepare_call(client_name="Зинаида")]))
        body = _ask_with(client, "Запиши Зинаиду на массаж завтра в 12:30").json()
        assert body["pending_action"] is None
        assert (
            body["answer"]
            == "Клиента с таким именем нет. Нового клиента можно добавить в форме записи."
        )
        card = next(c for c in body["cards"] if c["kind"] == "open")
        assert re.match(r"^/(master|solo)/booking/new", card["url"])
        assert card["label"] == "Добавить запись"


# ─── h4: подтверждение — тот же сервис, что М-2 ─────────────────────────────


class TestConfirmBooking:
    def _prepared(self, client, llm, stub_salon, *, rows=None, exc=None):
        stub = stub_salon(
            _StubSalon(rows=rows or [{"id": str(ANNA_P_AYLA_ID), "name": "Анна Петрова"}], exc=exc)
        )
        llm["script"].append(FakeResult(tool_calls=[_prepare_call()]))
        token = _ask_with(client, "Запиши Анну на массаж завтра в 12:30").json()["pending_action"][
            "token"
        ]
        return stub, token

    def test_committed_names_the_result_and_opens_the_detail(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service, llm, stub_salon
    ):
        stub, token = self._prepared(client, llm, stub_salon)
        resp = _confirm(client, token)
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["answer"] == "Запись создана"
        assert body["open"]["label"] == "Открыть запись"
        assert re.fullmatch(r"/(master|solo)/bookings/a-2153", body["open"]["url"])
        call = stub.calls[-1]["create_appointment"]
        assert call["actor_external_id"] == "bot:max:12345"
        assert call["specialist_id"] == str(accepted_master.catalog_specialist_id)
        assert call["service_id"] == str(bridged_service.ayla_service_id)
        assert call["client_id"] == str(ANNA_P_AYLA_ID)
        assert call["idempotency_key"]
        _assert_no_phone(resp.content.decode(), body)

    def test_slot_taken_offers_alternatives_and_moves_nothing(
        self,
        client,
        tenant,
        bot_user,
        accepted_master,
        anna,
        bridged_service,
        llm,
        stub_salon,
        stub_slots,
    ):
        stub, token = self._prepared(client, llm, stub_salon, exc=SalonSlotTaken("занято"))
        stub_slots(
            _StubSlots(
                slots=[
                    type(
                        "S",
                        (),
                        {"time": t, "datetime": f"2026-10-21T{t}:00+03:00", "duration_s": 3600},
                    )()
                    for t in ("11:00", "12:30", "14:00", "15:00", "16:00", "17:00")
                ]
            )
        )
        body = _confirm(client, token).json()
        assert body["answer"] == "Это время занято"
        card = next(c for c in body["cards"] if c["kind"] == "slot_taken")
        times = [a["time"] for a in card["alternatives"]]
        assert 2 <= len(times) <= 4
        assert "12:30" not in times
        assert len(stub.calls) == 2  # search + одна попытка создать — тихого переноса нет

    def test_no_answer_is_pending_not_created(
        self, client, tenant, bot_user, accepted_master, anna, bridged_service, llm, stub_salon
    ):
        _stub, token = self._prepared(client, llm, stub_salon, exc=SalonUnavailable("timeout"))
        body = _confirm(client, token).json()
        assert body["answer"] == "Проверяем результат"
        assert body.get("open") is None
        assert "Запись создана" not in json.dumps(body, ensure_ascii=False)


# ─── h5: рабочий день с записями ────────────────────────────────────────────


class TestScheduleChangeConflict:
    def test_block_time_on_a_day_with_visits_names_the_conflict(
        self, client, tenant, bot_user, accepted_master, anna, llm
    ):
        day = dj_timezone.now() + timedelta(days=7)
        _visit(accepted_master, start=day.replace(hour=11, minute=0), bot_user=anna)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        llm["script"].append(
            FakeResult(
                tool_calls=[
                    FakeToolCall(
                        name=actions_mod.ACTION_BLOCK_TIME,
                        arguments={
                            "start": start.isoformat(),
                            "end": (start + timedelta(days=1)).isoformat(),
                            "reason_class": "personal",
                        },
                    )
                ]
            )
        )
        body = _ask_with(client, "В следующую среду я не работаю").json()
        assert body["pending_action"] is None
        assert body["answer"].startswith("На этот день уже есть записи")
        assert "Сначала" in body["answer"]
        card = next(c for c in body["cards"] if c["kind"] == "day")
        assert card["visits"][0]["client"] == "Анна П."


# ─── h6: маршрут классифицирован ────────────────────────────────────────────


class TestRouteIsClassified:
    def test_context_route_is_in_the_pii_registry(self):
        from apps.master_api.tests.test_pii_boundary import NOT_SWEPT_ROUTES, SWEPT_READ_ROUTES

        assert "assistant_context" in set(SWEPT_READ_ROUTES) | set(NOT_SWEPT_ROUTES)
