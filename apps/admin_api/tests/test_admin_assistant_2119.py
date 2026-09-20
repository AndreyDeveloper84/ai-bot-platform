"""Раздел «Ayla» для администратора — половина А (DRF-2119, §50 п.5).

Замер на нетронутом ``dev 40599a17`` (20.09):

* ``/admin/ayla`` (``SalonPilotAylaScreen``) — каркас: «Разговор салона с Ayla
  сюда пока не приходит»; никуда не ходит;
* ассистент есть только у мастера: ``master_api/services/assistant.py``
  (цикл «один инструмент за ход», safety inbound/outbound, лимиты),
  ``assistant_tools.py`` (my_day / my_week / free_slots — про ЭТОГО мастера),
  ``assistant_actions.py`` (одно действие block_time: предложение с
  подписанным талоном → ``assistant/confirm`` → ``request_availability_change``);
  ручки ``master_api/assistant/{history,ask,confirm}``; нить —
  ``StaffAssistantThread`` (role_at_open=master);
* админских ручек ассистента нет; admin_api умеет: ``day/`` (build_salon_day,
  без телефонов по построению), ``customers/`` (поиск по имени/телефону,
  телефон не отдаёт), ``bookings/`` POST (создать запись), заявки мастеров
  approve/reject; локальной записи графика админом нет — только через заявку.

Сторожа на :mod:`apps.admin_api.services.assistant` + ``views_assistant``:

* p1 — состав инструментов: читающий ``find_booking``; действия
  ``prepare_booking`` и ``prepare_schedule_change``; «свободное время»
  (``free_slots`` и любое упоминание) **не объявляется** до DRF-1637 —
  сторож на состав; у мастера free_slots остаётся (положительная пара);
* p2 — ``find_booking``: по клиенту / мастеру / дате → карточки из того же
  ``build_salon_day``, что «Сегодня»; телефона в данных нет;
* p3 — ``prepare_booking``: черновик → ссылка ``/admin/booking/new?…`` с
  предзаполнением; **ничего не пишет** (узел: строк записей 0 → 0);
  создание — только человеком в форме;
* p4 — ``prepare_schedule_change``: предложение с талоном → до подтверждения
  заявок 0 → после ``execute`` заявка есть и одобрена теми же сервисами, что
  у Mini App (актор — админ); чужой/порченый талон → ошибка, заявок 0;
* p5 — роль: ``assistant/ask`` для владельца/админа 200, для ресепшна и
  мастера 403 (DRF-2115 — у ресепшна тройки нет);
* p6 — PII: телефон, пришедший из модели, до человека не доходит
  (DRF-1039; сторож DRF-2129 распространён на ответы ассистента);
* p7 — каркас один: цикл ассистента мастера переиспользован
  (``run_assistant`` в ``master_api.services.assistant``), не второй.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bookings.tests.test_staff_texts_no_phone_2129 import violates_phone_rule

from .conftest import init_data_header

pytestmark = pytest.mark.django_db

PHONE = "79991234567"
MSK = dt_timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=MSK)


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "call-1"


@dataclass
class FakeResult:
    text: str = ""
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    prompt_tokens: int = 10
    completion_tokens: int = 5
    model: str = "gpt-4o-mini"
    provider: str = "openai"
    finish_reason: str = "stop"


@pytest.fixture
def llm():
    """Подмена провайдера — на ОБЩЕМ цикле ``master_api.services.assistant._complete``."""
    from apps.master_api.services import assistant as mod

    calls: list[dict[str, Any]] = []
    scripted: list[FakeResult] = []

    def fake_complete(messages, *, tenant, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        return scripted.pop(0) if scripted else FakeResult(text="ответ")

    with patch.object(mod, "_complete", side_effect=fake_complete):
        yield {"calls": calls, "script": scripted}


@pytest.fixture
def role_ctx(owner_bot_user):
    from apps.identity.services.role_resolver import resolve_role

    return resolve_role(owner_bot_user)


def _visit(*, master, client_first="Анна", at: datetime, service="Массаж"):
    from apps.admin_api.services.salon_day import DayVisit

    return DayVisit(
        id=str(uuid.uuid4()),
        start_at=at,
        end_at=at + timedelta(minutes=60),
        duration_min=60,
        status="confirmed",
        master_id=str(master.id),
        master_name=master.name,
        service_id=str(uuid.uuid4()),
        service_name=service,
        client_first_name=client_first,
        client_last_initial="К.",
        is_in_progress=False,
    )


def _day(tenant, master, visits):
    from apps.admin_api.services.salon_day import DayMaster, DaySummary, SalonDay

    return SalonDay(
        date=NOW.date(),
        timezone_name="Europe/Moscow",
        masters=[
            DayMaster(master_id=str(master.id), name=master.name, is_active=True, visits=visits)
        ],
        summary=DaySummary(total=len(visits), upcoming=len(visits), completed=0, released=0),
    )


# ── p1 — состав инструментов ─────────────────────────────────────────


class TestP1ToolSet:
    def test_admin_tools_and_actions(self) -> None:
        from apps.admin_api.services import assistant as aa

        assert {s["name"] for s in aa.ADMIN_TOOL_SPECS} == {"find_booking"}
        assert {s["name"] for s in aa.ADMIN_ACTION_SPECS} == {
            "prepare_booking",
            "prepare_schedule_change",
        }

    def test_free_time_is_not_offered_until_1637(self) -> None:
        from apps.admin_api.services import assistant as aa
        from apps.master_api.services.assistant_tools import TOOL_SPECS

        assert any(s["name"] == "free_slots" for s in TOOL_SPECS)  # положительно: у мастера есть
        offered = json.dumps([*aa.ADMIN_TOOL_SPECS, *aa.ADMIN_ACTION_SPECS], ensure_ascii=False)
        assert "find_booking" in offered  # положительно: набор непуст
        for forbidden in ("free_slots", "свободн", "окно", "free_time"):
            assert forbidden not in offered.lower(), forbidden  # empty-assert-ok: пара выше


# ── p2 — найти запись ────────────────────────────────────────────────


class TestP2FindBooking:
    def test_by_client_master_date_without_phone(self, tenant, master) -> None:
        from apps.admin_api.services import assistant as aa

        visits = [
            _visit(master=master, client_first="Анна", at=NOW.replace(hour=10)),
            _visit(master=master, client_first="Мария", at=NOW.replace(hour=14)),
        ]
        with patch.object(aa, "_salon_day", return_value=_day(tenant, master, visits)):
            out = aa.run_admin_tool(
                "find_booking", {"client": "Анна", "date": "2026-09-21"}, tenant=tenant, now=NOW
            )
        assert out.name == "find_booking"
        assert len(out.data["bookings"]) == 1
        row = out.data["bookings"][0]
        assert row["client"].startswith("Анна") and row["master"] == master.name
        assert row["service"] == "Массаж" and row["time"] == "10:00"
        assert not violates_phone_rule(json.dumps(out.data, ensure_ascii=False))
        assert "phone" not in json.dumps(out.data).lower()  # empty-assert-ok: строки выше

    def test_by_master_only(self, tenant, master) -> None:
        from apps.admin_api.services import assistant as aa

        visits = [_visit(master=master, at=NOW.replace(hour=10))]
        with patch.object(aa, "_salon_day", return_value=_day(tenant, master, visits)):
            out = aa.run_admin_tool(
                "find_booking",
                {"master": master.name, "date": "2026-09-21"},
                tenant=tenant,
                now=NOW,
            )
        assert len(out.data["bookings"]) == 1

    def test_source_unavailable_is_named(self, tenant) -> None:
        from apps.admin_api.services import assistant as aa

        with patch.object(aa, "_salon_day", side_effect=RuntimeError("ayla down")):
            with pytest.raises(aa.ToolError):
                aa.run_admin_tool("find_booking", {"date": "2026-09-21"}, tenant=tenant, now=NOW)


# ── p3 — подготовить запись: черновик, не запись ─────────────────────


class TestP3PrepareBookingWritesNothing:
    def test_draft_opens_the_form_prefilled(
        self, tenant, owner_bot_user, master_with_service
    ) -> None:
        from apps.admin_api.services import assistant as aa
        from apps.booking.models import BookingRequest, RemoteBookingProxy

        before = (
            BookingRequest.all_tenants.filter(tenant=tenant).count(),
            RemoteBookingProxy.all_tenants.filter(tenant=tenant).count(),
        )
        proposal = aa.propose_admin_action(
            "prepare_booking",
            {
                "master": master_with_service.name,
                "service": "Массаж",
                "start_at": "2026-09-22T11:00",
                "client_name": "Анна",
            },
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        d = proposal.as_dict()
        assert d["action"] == "prepare_booking" and d["confirm_kind"] == "open"
        assert d["open_url"].startswith("/admin/booking/new?")
        assert f"master_id={master_with_service.id}" in d["open_url"]
        assert (
            "start_at=2026-09-22T11%3A00" in d["open_url"]
            or "start_at=2026-09-22T11:00" in d["open_url"]
        )
        assert "client_name=" in d["open_url"]
        assert "Анна" in d["summary"] and master_with_service.name in d["summary"]
        after = (
            BookingRequest.all_tenants.filter(tenant=tenant).count(),
            RemoteBookingProxy.all_tenants.filter(tenant=tenant).count(),
        )
        assert before == after == (0, 0)


# ── p4 — подготовить изменение графика: талон → подтверждение ────────


class TestP4ScheduleChangeNeedsConfirmation:
    def test_proposal_writes_nothing_and_execute_writes_once(
        self, tenant, owner_bot_user, master, settings
    ) -> None:
        from apps.admin_api.services import assistant as aa
        from apps.scheduling.models import ScheduleChangeRequest

        settings.BOOKING_VIA_AYLA_REST = False
        proposal = aa.propose_admin_action(
            "prepare_schedule_change",
            {
                "master": master.name,
                "start": "2026-09-25T10:00",
                "end": "2026-09-25T12:00",
                "reason_class": "personal",
            },
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        d = proposal.as_dict()
        assert d["confirm_kind"] == "token" and d["token"]
        assert master.name in d["summary"] and "10:00" in d["summary"]
        assert ScheduleChangeRequest.all_tenants.filter(tenant=tenant).count() == 0

        done = aa.execute_admin_action(d["token"], tenant=tenant, bot_user=owner_bot_user)
        rows = list(ScheduleChangeRequest.all_tenants.filter(tenant=tenant))
        assert len(rows) == 1
        assert rows[0].status == ScheduleChangeRequest.Status.APPROVED
        assert rows[0].master_id == master.id
        assert str(rows[0].resolved_by_bot_user_id) == str(owner_bot_user.id)
        assert "график" in done.text.lower() or "готово" in done.text.lower()

    def test_foreign_or_broken_token_is_refused(
        self, tenant, other_tenant, owner_bot_user, master
    ) -> None:
        from apps.admin_api.services import assistant as aa
        from apps.scheduling.models import ScheduleChangeRequest

        proposal = aa.propose_admin_action(
            "prepare_schedule_change",
            {"master": master.name, "start": "2026-09-25T10:00", "end": "2026-09-25T12:00"},
            tenant=tenant,
            bot_user=owner_bot_user,
        )
        with pytest.raises(aa.ActionError):
            aa.execute_admin_action(proposal.token, tenant=other_tenant, bot_user=owner_bot_user)
        with pytest.raises(aa.ActionError):
            aa.execute_admin_action(proposal.token + "x", tenant=tenant, bot_user=owner_bot_user)
        assert ScheduleChangeRequest.all_tenants.count() == 0  # empty-assert-ok: два отказа выше


# ── p5 — роль ────────────────────────────────────────────────────────


class TestP5RoleGate:
    def _ask(self, client: Client, user_id: str):
        return client.post(
            reverse("admin_api:assistant_ask"),
            data=json.dumps({"text": "кто сегодня у Ольги?"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header(user_id),
        )

    def test_owner_and_admin_200_reception_and_master_403(
        self,
        client: Client,
        owner_bot_user,
        admin_bot_user,
        receptionist_bot_user,
        master_only_bot_user,
        llm,
    ) -> None:
        llm["script"].extend(
            [FakeResult(text="Сегодня у Ольги две записи."), FakeResult(text="ок")]
        )
        assert self._ask(client, "5001").status_code == 200
        assert self._ask(client, "5002").status_code == 200
        assert self._ask(client, "5003").status_code == 403
        assert self._ask(client, "5004").status_code == 403

    def test_history_is_the_admin_thread(self, client: Client, owner_bot_user, llm) -> None:
        llm["script"].append(FakeResult(text="Пока тихо."))
        self._ask(client, "5001")
        resp = client.get(
            reverse("admin_api:assistant_history"), HTTP_AUTHORIZATION=init_data_header("5001")
        )
        assert resp.status_code == 200
        roles = [m["role"] for m in resp.json()["messages"]]
        assert roles[-2:] == ["user", "assistant"]


# ── p6 — PII ─────────────────────────────────────────────────────────


class TestP6NoPhoneReachesTheAdmin:
    def test_phone_from_the_model_is_masked(self, tenant, owner_bot_user, role_ctx, llm) -> None:
        from apps.admin_api.services.assistant import answer_admin_question

        llm["script"].append(FakeResult(text=f"Клиент Анна, телефон {PHONE}, придёт в 10:00."))
        reply = answer_admin_question(
            tenant=tenant, bot_user=owner_bot_user, role_ctx=role_ctx, text="кто в 10?", now=NOW
        )
        assert "Анна" in reply.text  # положительно: ответ на месте
        assert not violates_phone_rule(reply.text), reply.text


# ── p7 — один каркас ─────────────────────────────────────────────────


class TestP7OneFramework:
    def test_admin_answer_runs_through_the_shared_loop(
        self, tenant, owner_bot_user, role_ctx, llm, master
    ) -> None:
        from apps.admin_api.services import assistant as aa

        llm["script"].extend(
            [
                FakeResult(tool_calls=[FakeToolCall("find_booking", {"date": "2026-09-21"})]),
                FakeResult(text="Сегодня одна запись: Анна к Ольге в 10:00."),
            ]
        )
        visits = [_visit(master=master, at=NOW.replace(hour=10))]
        with patch.object(aa, "_salon_day", return_value=_day(tenant, master, visits)):
            reply = aa.answer_admin_question(
                tenant=tenant,
                bot_user=owner_bot_user,
                role_ctx=role_ctx,
                text="кто сегодня?",
                now=NOW,
            )
        assert reply.tool_name == "find_booking" and "Анна" in reply.text
        assert len(llm["calls"]) == 2
        offered = {t["name"] for t in (llm["calls"][0]["tools"] or [])}
        assert offered == {"find_booking", "prepare_booking", "prepare_schedule_change"}

    def test_master_assistant_still_works_on_the_same_loop(self, llm) -> None:
        """Положительная пара для рефакторинга: мастерский ответ не сломан."""
        from apps.catalog.models import CatalogMaster
        from apps.master_api.services.assistant import answer_master_question
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="asst-2119", name="Салон", timezone="Europe/Moscow")
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            name="Ольга",
            external_id=None,
            external_updated_at=timezone.now(),
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
        )
        llm["script"].append(FakeResult(text="Пока свободно."))
        reply = answer_master_question(master=master, text="привет", now=NOW)
        assert reply.text == "Пока свободно." and reply.llm_called
