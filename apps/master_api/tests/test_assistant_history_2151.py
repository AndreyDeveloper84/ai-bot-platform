"""«Ayla» мастера показывает только ходы ассистента (DRF-2151, М-0).

Красное листа: живой снимок 20.09 — ``/master/ayla`` рисует «/start
master_invite_2d8bbc4f-…», «Салон «Формула тела».», «/start inv_AYLAUUA6»,
«Слушаю, Денис. Что нужно?» — ленту DM салонного бота с одноразовыми
токенами приглашений (DRF-1228 держит коды вне БД в открытом виде — а тут
они на экране).

* h1 — подсадка в нить команд, токенов, ответов входа и tool-строки →
  ``GET assistant/history``: ни одного вхождения ``/start`` / ``inv_`` /
  ``master_invite`` / menu_header; обычные вопрос и ответ на месте
  (положительная стража рядом с каждым отрицанием);
* h2 — парный фильтр: ответ входа после команды режется, СОДЕРЖАТЕЛЬНЫЙ
  ответ после вставленной команды остаётся (поправка главного окна);
* h3 — лимит считает видимые строки, а не сырые;
* h4 — write-side: ``ask`` с командой / токеном отвечает, но user-реплику в
  нить не пишет; обычный вопрос пишется;
* h5 — admin ``assistant/history`` — тот же фильтр;
* h6 — предикаты: формы команд/токенов и формы ответов входа.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.conversations.models import StaffAssistantMessage, StaffAssistantThread
from apps.conversations.staff_assistant import (
    is_entry_reply,
    is_hidden_staff_turn,
    record_staff_message,
    resolve_active_staff_thread,
    visible_staff_history,
)
from apps.identity.models import BotUser
from apps.master_api.tests.conftest import init_data_header
from apps.master_api.services import assistant as assistant_mod
from apps.master_api.tests.test_assistant_api import FakeResult, _ask
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db

HISTORY_URL = reverse("master_api:assistant_history")


@pytest.fixture
def llm():
    """Подменённый провайдер — та же форма, что в test_assistant_api (fixture не импортируется:
    ruff читает параметр как переопределение импорта)."""
    scripted: list[FakeResult] = []

    def fake_complete(messages, *, tenant, tools=None):
        return scripted.pop(0) if scripted else FakeResult(text="ответ")

    with patch.object(assistant_mod, "_complete", side_effect=fake_complete):
        yield {"script": scripted}


INVITE_TOKEN = f"master_invite_{uuid.uuid4()}"
LEAKED_ROWS = [
    ("user", f"/start {INVITE_TOKEN}"),
    ("assistant", "Салон «Формула тела». Ваш день и кабинет мастера."),
    ("user", "/start inv_AYLAUUA6"),
    ("assistant", "Слушаю, Денис. Что нужно?"),
    ("tool", '{"day": "2026-09-20", "visits": []}'),
    ("user", "что у меня в четверг"),
    ("assistant", "В четверг две записи: 11:00 и 15:30."),
]
FORBIDDEN = ("/start", "inv_", "master_invite", "Салон «", "Слушаю", "Что нужно")


def _seed(bot_user: BotUser, rows) -> StaffAssistantThread:
    with tenant_scope(bot_user.tenant):
        thread = resolve_active_staff_thread(bot_user, role_at_open="master")
        for role, content in rows:
            record_staff_message(thread, role=role, content=content)
    return thread


def _history(client: Client, *, user_id: str = "12345", limit: int | None = None):
    url = HISTORY_URL if limit is None else f"{HISTORY_URL}?limit={limit}"
    resp = client.get(url, HTTP_AUTHORIZATION=init_data_header(user_id))
    assert resp.status_code == 200, resp.content
    return resp.json()["messages"]


# ─── h1: экран без команд и токенов ─────────────────────────────────────────


class TestTheScreenShowsOnlyAssistantTurns:
    def test_seeded_leak_is_invisible_and_the_real_turn_stays(
        self, client, bot_user, accepted_master
    ):
        _seed(bot_user, LEAKED_ROWS)
        messages = _history(client)
        # Присутствие впереди отсутствия: настоящий ход ассистента на месте.
        assert [(m["role"], m["content"]) for m in messages] == [
            ("user", "что у меня в четверг"),
            ("assistant", "В четверг две записи: 11:00 и 15:30."),
        ]
        blob = " ".join(m["content"] for m in messages)
        for needle in FORBIDDEN:
            assert needle not in blob, needle
        assert INVITE_TOKEN not in blob
        assert all(m["role"] in ("user", "assistant") for m in messages)

    def test_a_thread_of_commands_only_renders_empty(self, client, bot_user, accepted_master):
        """Ложный вход: одна подсадка «/start inv_X» → на экране 0 вхождений."""
        _seed(bot_user, [("user", "/start inv_X"), ("assistant", "Салон «Формула тела».")])
        assert _history(client) == []

    def test_the_rows_stay_in_the_database(self, client, bot_user, accepted_master):
        """Предел: скрыто, не стёрто — нить есть лог, чистка — отдельное решение."""
        thread = _seed(bot_user, LEAKED_ROWS)
        _history(client)
        with tenant_scope(bot_user.tenant):
            assert StaffAssistantMessage.objects.filter(thread=thread).count() == len(LEAKED_ROWS)


# ─── h2: парный фильтр ──────────────────────────────────────────────────────


class TestThePairRule:
    def test_entry_reply_after_a_command_is_cut(self, client, bot_user, accepted_master):
        _seed(
            bot_user,
            [("user", "/start inv_ABCD"), ("assistant", "Слушаю, Анна. Что нужно?")],
        )
        assert _history(client) == []

    def test_substantive_reply_after_a_pasted_command_stays(
        self, client, bot_user, accepted_master
    ):
        """Человек вставил «/start» и тут же спросил — ответ его, вопрос нет."""
        _seed(
            bot_user,
            [
                ("user", "/start inv_ABCD что у меня завтра?"),
                ("assistant", "Завтра у вас три записи, первая в 10:00."),
            ],
        )
        assert [(m["role"], m["content"]) for m in _history(client)] == [
            ("assistant", "Завтра у вас три записи, первая в 10:00."),
        ]

    def test_hidden_row_between_command_and_entry_reply_does_not_save_it(
        self, client, bot_user, accepted_master
    ):
        """«С последнего видимого хода была команда» — tool-строка её не забывает."""
        _seed(
            bot_user,
            [
                ("user", "/start inv_ABCD"),
                ("tool", '{"code": "AYLA-7K3M"}'),
                ("assistant", "Салон «Формула тела»."),
            ],
        )
        assert _history(client) == []

    def test_entry_reply_not_after_a_command_stays(self, client, bot_user, accepted_master):
        """Форма входа режется только парой: сама по себе «Слушаю…» на вопрос — ответ."""
        _seed(bot_user, [("user", "ты тут?"), ("assistant", "Слушаю, Анна. Что нужно?")])
        assert [m["content"] for m in _history(client)] == ["ты тут?", "Слушаю, Анна. Что нужно?"]


# ─── h3: лимит по видимым ───────────────────────────────────────────────────


class TestLimitCountsVisibleRows:
    def test_hidden_rows_do_not_eat_the_quota(self, client, bot_user, accepted_master):
        rows = [("user", f"/start inv_T{i:04d}") for i in range(30)]
        rows += [
            ("user", "вопрос раз"),
            ("assistant", "ответ раз"),
            ("user", "вопрос два"),
            ("assistant", "ответ два"),
        ]
        _seed(bot_user, rows)
        assert [m["content"] for m in _history(client, limit=3)] == [
            "ответ раз",
            "вопрос два",
            "ответ два",
        ]
        assert len(_history(client, limit=50)) == 4


# ─── h4: write-side ─────────────────────────────────────────────────────────


class TestCommandsAreNotWrittenAsTurns:
    @pytest.mark.parametrize(
        "text", ["/start inv_AYLAUUA6", f"/start {INVITE_TOKEN}", "AYLA-7K3M", "/whoami"]
    )
    def test_a_pasted_command_is_answered_but_not_recorded(
        self,
        client,
        bot_user,
        accepted_master,
        llm,
        text,
    ):
        llm["script"].append(FakeResult(text="Это похоже на команду бота — спросите про день."))
        resp = _ask(client, text)
        assert resp.status_code == 200, resp.content
        with tenant_scope(bot_user.tenant):
            thread = StaffAssistantThread.objects.get(bot_user=bot_user)
            rows = [
                (r.role, r.content) for r in StaffAssistantMessage.objects.filter(thread=thread)
            ]
        assert all(role != "user" for role, _ in rows), rows
        assert not any(text in content for _, content in rows)

    def test_entry_reply_to_a_pasted_command_is_not_recorded_as_an_orphan(
        self, client, bot_user, accepted_master, llm
    ):
        """Вопроса в нити нет — ответ входа без него стал бы сиротой на экране."""
        llm["script"].append(FakeResult(text="Слушаю, Анна. Что нужно?"))
        assert _ask(client, "/start inv_AYLAUUA6").status_code == 200
        assert _history(client) == []
        with tenant_scope(bot_user.tenant):
            thread = StaffAssistantThread.objects.get(bot_user=bot_user)
            assert StaffAssistantMessage.objects.filter(thread=thread).count() == 0

    def test_substantive_reply_to_a_pasted_command_is_recorded(
        self, client, bot_user, accepted_master, llm
    ):
        llm["script"].append(FakeResult(text="Завтра три записи, первая в 10:00."))
        assert _ask(client, "/start inv_AYLAUUA6 что завтра").status_code == 200
        assert [m["content"] for m in _history(client)] == ["Завтра три записи, первая в 10:00."]

    def test_an_ordinary_question_is_still_recorded(self, client, bot_user, accepted_master, llm):
        """Положительная стража: write-side не запер дверь для вопросов."""
        llm["script"].append(FakeResult(text="Пусто."))
        _ask(client, "что в среду")
        with tenant_scope(bot_user.tenant):
            thread = StaffAssistantThread.objects.get(bot_user=bot_user)
            rows = [
                (r.role, r.content)
                for r in StaffAssistantMessage.objects.filter(thread=thread).order_by("seq")
            ]
        assert rows == [("user", "что в среду"), ("assistant", "Пусто.")]


# ─── h5: admin — тот же фильтр ──────────────────────────────────────────────


class TestAdminHistoryUsesTheSameFilter:
    def test_admin_history_hides_commands(self, tenant, other_bot_user, settings):
        """Владелец салона (TenantStaff) в том же тенанте — admin_api читает ту же нить."""
        from apps.tenancy.models import TenantStaff

        settings.MAX_BOT_TENANT_SLUG = tenant.slug
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=other_bot_user, role=TenantStaff.Role.OWNER
        )
        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(other_bot_user, role_at_open="owner")
            record_staff_message(thread, role="user", content="/start inv_OWNER1")
            record_staff_message(thread, role="assistant", content="Салон «Master API Test Salon».")
            record_staff_message(thread, role="user", content="найди запись Марии")
            record_staff_message(thread, role="assistant", content="Мария К., сегодня 14:00.")
        resp = Client().get(
            reverse("admin_api:assistant_history"), HTTP_AUTHORIZATION=init_data_header("99999")
        )
        assert resp.status_code == 200, resp.content
        contents = [m["content"] for m in resp.json()["messages"]]
        assert contents == ["найди запись Марии", "Мария К., сегодня 14:00."]


# ─── h6: предикаты ──────────────────────────────────────────────────────────


class TestPredicates:
    @pytest.mark.parametrize(
        "content",
        [
            "/start",
            "/start inv_AYLAUUA6",
            f"/start {INVITE_TOKEN}",
            "/whoami",
            "инвайт inv_AYLA7K3M",
            "код AYLA-7K3M",
            "AYLA 7K3M",
            "ayla-7k3m",
            "Ayla_7K3M",
            "7K3M",
            "",
            "   ",
        ],
    )
    def test_hidden_user_turns(self, content):
        assert is_hidden_staff_turn("user", content) is True

    @pytest.mark.parametrize(
        "content",
        [
            "что в четверг",
            "запиши отгул 12/10",
            "клиент написал: инвойс готов",
            "1/2 дня",
            "AYLA Beauty открыт?",
            "AYLA 2026",
            "Ayla, что у меня завтра?",
            "Салон «AYLA Studio». Ваш день и кабинет мастера.",
        ],
    )
    def test_ordinary_turns_are_visible(self, content):
        assert is_hidden_staff_turn("user", content) is False
        assert is_hidden_staff_turn("assistant", content) is False

    def test_tool_and_system_rows_are_hidden_by_role(self):
        assert is_hidden_staff_turn("tool", "{}") is True
        assert is_hidden_staff_turn("system", "prompt") is True

    @pytest.mark.parametrize(
        "content",
        [
            "Салон «Формула тела».",
            "Салон «Формула тела». Ваш день и кабинет мастера.",
            "Слушаю, Денис. Что нужно?",
            "Чем могу помочь? Спрашивай про день и окна.",
        ],
    )
    def test_entry_replies(self, content):
        assert is_entry_reply(content) is True

    @pytest.mark.parametrize(
        "content", ["В четверг две записи.", "Салон «Формула тела» закрыт 1 мая — записей нет."]
    )
    def test_answers_are_not_entry_replies(self, content):
        assert is_entry_reply(content) is False

    def test_visible_history_reads_only_the_screen_roles(self, bot_user):
        thread = _seed(bot_user, [("tool", "{}"), ("user", "вопрос"), ("assistant", "ответ")])
        with tenant_scope(bot_user.tenant):
            rows = visible_staff_history(thread, limit=10)
        assert [r.role for r in rows] == ["user", "assistant"]
