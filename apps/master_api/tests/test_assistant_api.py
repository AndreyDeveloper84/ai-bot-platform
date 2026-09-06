"""Раздел «Ayla» через REST (DRF-1180).

Ассистент здесь не проверяется — у него есть свой файл
(``test_assistant.py``). Проверяется то, что появилось: маршрут, чужая
дверь, общая с ботом нить и — главное — что действие, меняющее данные,
без подтверждения не выполняется, а с подтверждением выполняется.

### Почему обе половины у каждого отрицания

Отрицание в одиночку зеленеет на пустоте: «записи не появилось» верно и
тогда, когда ассистент вообще не отвечал. Поэтому каждая пара стоит
рядом — сначала показывается, что предложение получено (положительная
стража), и только потом, что база не тронута. То же с чужой дверью:
рядом с «чужому 403» стоит «своему 200» на том же талоне (DRF-1411).

Модель подменена: судить её слова — работа ночного прогона, здесь
судится петля вокруг неё.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.catalog.models import CatalogMaster
from apps.conversations.models import StaffAssistantMessage, StaffAssistantThread
from apps.identity.models import BotUser
from apps.master_api.pii import find_forbidden_pii
from apps.master_api.services import assistant as assistant_mod
from apps.master_api.services.assistant_actions import ACTION_BLOCK_TIME, DONE_TEXT
from apps.master_api.tests.conftest import init_data_header
from apps.scheduling.models import ScheduleChangeRequest
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

HISTORY_URL = reverse("master_api:assistant_history")
ASK_URL = reverse("master_api:assistant_ask")
CONFIRM_URL = reverse("master_api:assistant_confirm")


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = "call-1"


@dataclass
class FakeResult:
    text: str = ""
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    prompt_tokens: int = 11
    completion_tokens: int = 7
    model: str = "gpt-4o-mini"
    provider: str = "openai"
    finish_reason: str = "stop"


@pytest.fixture
def llm():
    """Подменённый провайдер. ``script`` — очередь ответов по порядку."""

    calls: list[dict[str, Any]] = []
    scripted: list[FakeResult] = []

    def fake_complete(messages, *, tenant, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        return scripted.pop(0) if scripted else FakeResult(text="ответ")

    with patch.object(assistant_mod, "_complete", side_effect=fake_complete):
        yield {"calls": calls, "script": scripted}


def _future(*, days: int, hour: int) -> datetime:
    return (datetime.now(tz=dt_timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _block_time_call(*, days: int = 5) -> FakeToolCall:
    return FakeToolCall(
        name=ACTION_BLOCK_TIME,
        arguments={
            "start": _future(days=days, hour=9).isoformat(),
            "end": _future(days=days, hour=18).isoformat(),
            "reason_class": "vacation",
            "reason_text": "поездка",
        },
    )


def _ask(client: Client, text: str, *, user_id: str = "12345"):
    return client.post(
        ASK_URL,
        data={"text": text},
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


@pytest.fixture
def second_master(tenant: Tenant, other_bot_user: BotUser) -> CatalogMaster:
    """Второй мастер того же салона — чужая дверь для первого."""

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Мария Иванова",
        external_id=None,
        external_updated_at=dj_timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=other_bot_user,
    )


class TestTheRouteAnswers:
    def test_a_question_gets_an_answer(self, client, bot_user, accepted_master, llm):
        llm["script"].append(FakeResult(text="Завтра три записи."))

        resp = _ask(client, "что у меня завтра")

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["answer"] == "Завтра три записи."
        assert body["pending_action"] is None

    def test_the_mini_app_is_offered_the_writing_action(
        self, client, bot_user, accepted_master, llm
    ):
        """Салонный бот их не видит, приложение — видит.

        Прямая проверка того, что ``allow_actions`` не декоративен:
        соседний тест ``test_assistant.py`` пришпиливает состав
        инструментов бота к трём читающим.
        """

        _ask(client, "хочу выходной")

        offered = {t["name"] for t in llm["calls"][0]["tools"]}
        assert offered == {"my_day", "my_week", "free_slots", ACTION_BLOCK_TIME}

    def test_the_turn_lands_in_the_thread_the_bot_uses(
        self, client, bot_user, accepted_master, llm
    ):
        """Одна нить на двоих: спросил в боте — дочитал в приложении."""

        llm["script"].append(FakeResult(text="Пока свободно."))
        _ask(client, "что в четверг")

        with tenant_scope(bot_user.tenant):
            thread = StaffAssistantThread.objects.get(bot_user=bot_user)
            rows = list(StaffAssistantMessage.objects.filter(thread=thread).order_by("seq"))
        assert [(r.role, r.content) for r in rows] == [
            ("user", "что в четверг"),
            ("assistant", "Пока свободно."),
        ]

    def test_history_returns_what_was_said(self, client, bot_user, accepted_master, llm):
        llm["script"].append(FakeResult(text="Пока свободно."))
        _ask(client, "что в четверг")

        resp = client.get(HISTORY_URL, HTTP_AUTHORIZATION=init_data_header("12345"))

        assert resp.status_code == 200, resp.content
        messages = resp.json()["messages"]
        assert [(m["role"], m["content"]) for m in messages] == [
            ("user", "что в четверг"),
            ("assistant", "Пока свободно."),
        ]

    def test_history_is_empty_before_the_first_question(self, client, bot_user, accepted_master):
        resp = client.get(HISTORY_URL, HTTP_AUTHORIZATION=init_data_header("12345"))

        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_an_empty_question_is_a_400(self, client, bot_user, accepted_master):
        resp = _ask(client, "   ")

        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"


class TestSomeoneElsesDoor:
    """Чужой к чужому ассистенту не пробивается — с положительной стражей."""

    def test_a_person_without_a_master_card_is_refused(self, client, tenant, other_bot_user):
        resp = _ask(client, "что у меня завтра", user_id="99999")

        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_but_a_linked_master_gets_through(
        self, client, tenant, other_bot_user, second_master, llm
    ):
        """Положительная стража к тесту выше: дверь не заперта наглухо."""

        llm["script"].append(FakeResult(text="Завтра пусто."))

        resp = _ask(client, "что у меня завтра", user_id="99999")

        assert resp.status_code == 200, resp.content
        assert resp.json()["answer"] == "Завтра пусто."

    def test_history_of_one_master_is_invisible_to_another(
        self, client, bot_user, accepted_master, other_bot_user, second_master, llm
    ):
        llm["script"].append(FakeResult(text="Ответ Анне."))
        _ask(client, "секрет Анны", user_id="12345")

        resp = client.get(HISTORY_URL, HTTP_AUTHORIZATION=init_data_header("99999"))

        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_and_the_owner_still_sees_her_own(
        self, client, bot_user, accepted_master, other_bot_user, second_master, llm
    ):
        llm["script"].append(FakeResult(text="Ответ Анне."))
        _ask(client, "секрет Анны", user_id="12345")

        resp = client.get(HISTORY_URL, HTTP_AUTHORIZATION=init_data_header("12345"))

        contents = [m["content"] for m in resp.json()["messages"]]
        assert "секрет Анны" in contents


class TestNothingHappensWithoutConfirmation:
    """Требование эпика DRF-1180, обе половины."""

    def test_the_writing_action_comes_back_as_a_proposal(
        self, client, bot_user, accepted_master, llm
    ):
        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))

        resp = _ask(client, "хочу выходной в пятницу")

        assert resp.status_code == 200, resp.content
        body = resp.json()
        proposal = body["pending_action"]
        assert proposal is not None
        assert proposal["action"] == ACTION_BLOCK_TIME
        assert proposal["token"]
        # Сводка описывает то, что будет сделано, и говорит, что пока
        # ничего не изменилось.
        assert "заявку на нерабочее время" in proposal["summary"]
        assert "Пока вы не подтвердите" in proposal["summary"]
        assert body["answer"] == proposal["summary"]

    def test_and_nothing_is_written_until_it_is_confirmed(
        self, client, bot_user, accepted_master, llm
    ):
        """Отрицание, у которого положительная половина — тест выше."""

        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))

        resp = _ask(client, "хочу выходной в пятницу")

        assert resp.json()["pending_action"] is not None  # стража
        assert ScheduleChangeRequest.all_tenants.count() == 0

    def test_the_second_model_call_never_happens_for_an_action(
        self, client, bot_user, accepted_master, llm
    ):
        """Один заход к модели: предложение собирается нами, не ею."""

        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))

        _ask(client, "хочу выходной в пятницу")

        assert len(llm["calls"]) == 1

    def test_confirming_executes_it(self, client, bot_user, accepted_master, llm):
        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))
        token = _ask(client, "хочу выходной в пятницу").json()["pending_action"]["token"]

        resp = client.post(
            CONFIRM_URL,
            data={"token": token},
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

        assert resp.status_code == 200, resp.content
        assert resp.json()["answer"] == DONE_TEXT
        rows = list(ScheduleChangeRequest.all_tenants.all())
        assert len(rows) == 1
        assert rows[0].master_id == accepted_master.id
        assert rows[0].requested_by_id == bot_user.id
        assert rows[0].reason_class == "vacation"

    def test_the_confirmation_lands_in_the_transcript(self, client, bot_user, accepted_master, llm):
        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))
        token = _ask(client, "хочу выходной в пятницу").json()["pending_action"]["token"]
        client.post(
            CONFIRM_URL,
            data={"token": token},
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

        resp = client.get(HISTORY_URL, HTTP_AUTHORIZATION=init_data_header("12345"))

        assert resp.json()["messages"][-1]["content"] == DONE_TEXT

    def test_a_forged_token_is_refused(self, client, bot_user, accepted_master):
        resp = client.post(
            CONFIRM_URL,
            data={"token": "not-a-real-token"},
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "action_invalid"
        assert ScheduleChangeRequest.all_tenants.count() == 0

    def test_someone_elses_token_does_not_work(
        self, client, bot_user, accepted_master, other_bot_user, second_master, llm
    ):
        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))
        token = _ask(client, "хочу выходной", user_id="12345").json()["pending_action"]["token"]

        resp = client.post(
            CONFIRM_URL,
            data={"token": token},
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("99999"),
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "action_not_yours"
        assert ScheduleChangeRequest.all_tenants.count() == 0

    def test_but_the_same_token_works_for_its_owner(
        self, client, bot_user, accepted_master, other_bot_user, second_master, llm
    ):
        """Положительная стража: талон отвергнут за адресата, не «всегда»."""

        llm["script"].append(FakeResult(tool_calls=[_block_time_call()]))
        token = _ask(client, "хочу выходной", user_id="12345").json()["pending_action"]["token"]

        resp = client.post(
            CONFIRM_URL,
            data={"token": token},
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

        assert resp.status_code == 200, resp.content
        assert ScheduleChangeRequest.all_tenants.count() == 1

    def test_a_window_in_the_past_is_refused_at_proposal_time(
        self, client, bot_user, accepted_master, llm
    ):
        llm["script"].append(FakeResult(tool_calls=[_block_time_call(days=-5)]))

        body = _ask(client, "сними мне прошлую пятницу").json()

        assert body["pending_action"] is None
        assert "Не смог подготовить действие" in body["answer"]
        assert ScheduleChangeRequest.all_tenants.count() == 0


class TestNoCustomerPii:
    """Расшифровка — тоже ответ мастерской поверхности (DRF-1039).

    Классификация ``assistant_history`` в ``test_pii_boundary.py``
    ссылается сюда: подмести пустую нить нельзя, поэтому она сначала
    наполняется, и только потом просматривается.
    """

    CUSTOMER_DIGITS = "79997775544"

    def test_the_transcript_carries_no_forbidden_key_and_no_phone(
        self, client, bot_user, accepted_master, llm
    ):
        llm["script"].append(FakeResult(text="Завтра Ольга в 14:00, маникюр."))
        _ask(client, "что у меня завтра")

        resp = client.get(HISTORY_URL, HTTP_AUTHORIZATION=init_data_header("12345"))
        body = resp.json()
        raw = resp.content.decode("utf-8")

        # Стража: подметать есть что. Ответ ассистента в расшифровке
        # есть — и в разобранном виде, и в сырых байтах, по которым
        # ниже ищется телефон.
        assert any("Ольга" in m["content"] for m in body["messages"])
        assert '"role": "assistant"' in raw
        assert find_forbidden_pii(body) == []
        assert self.CUSTOMER_DIGITS not in raw

    def test_the_reading_tools_never_hand_the_model_a_phone(
        self, client, bot_user, accepted_master, llm
    ):
        """Проверка держится на источнике, а не на словах модели.

        В промпт второго захода уезжает JSON инструмента — там телефона
        быть не должно, иначе он окажется в ответе рано или поздно.
        """

        from apps.booking.models import RemoteBookingProxy

        day = datetime.now(tz=dt_timezone.utc) + timedelta(days=1)
        RemoteBookingProxy.all_tenants.create(
            tenant=accepted_master.tenant,
            appointment_id=uuid.uuid4(),
            bot_user=bot_user,
            specialist_id=accepted_master.id,
            start_at=day.replace(hour=11, minute=0, second=0, microsecond=0),
            end_at=day.replace(hour=12, minute=0, second=0, microsecond=0),
            status="confirmed",
        )
        llm["script"].append(
            FakeResult(
                tool_calls=[FakeToolCall(name="my_day", arguments={"date": day.date().isoformat()})]
            )
        )
        llm["script"].append(FakeResult(text="Завтра одна запись."))

        _ask(client, "что у меня завтра")

        assert len(llm["calls"]) == 2  # стража: инструмент реально отработал
        handed = llm["calls"][1]["messages"][-1]["content"]
        assert "Данные инструмента my_day" in handed
        for banned in ("phone", "телефон", self.CUSTOMER_DIGITS):
            assert banned not in handed
