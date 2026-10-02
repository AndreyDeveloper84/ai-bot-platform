"""Поддержка клиента идёт через клиентский бот — и только через него (DRF-2751).

Решение владельца 02.10.2026: поддержка клиента — через сам клиентский
MAX-бот; внутренний чат сотрудников клиенту не показывается.

* ``TestTheAddressIsDerived`` — адрес поддержки выводится из ссылки клиентского
  бота в реестре; своей настройки «адрес поддержки клиента» нет, вписать
  внутренний чат некуда; адрес и дверь бота держатся за один параметр;
* ``TestTheClientLinkIsChecked`` — сторож на том, что доходит до клиента:
  ссылка клиентской записи обязана быть ссылкой на бота (не приглашением в
  чат, не салонным ботом, не id получателя оповещений) и называть того же
  бота, что ``web_app``. Не прошла — адреса нет;
* ``TestDeployCheck`` — ``support.W002``: о непрошедшей ссылке выкладка
  говорит, но не останавливается; значение в текст не попадает;
* ``TestTheStaffButtonIsNotTouched`` — ``AYLA_SUPPORT_CONTACT`` читает только
  кнопка персонала в салонном боте; этот лист её не меняет;
* ``TestTheDoor`` — «/start support» в клиентском боте: реплика с кнопкой и
  НИ ОДНОЙ задачи; задача — по кнопке или по следующему сообщению;
* ``TestWhereTheTaskLands`` — обращение в поддержку всегда в очереди
  платформы, салон не будится (в отличие от «позовите администратора»);
* ``TestTheDoorDoesNotStayOpen`` — посторонний жест снимает ожидание, без
  двери обычное сообщение задачей не становится.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone

import pytest
from django.core.cache import cache

from apps.channels import support_contact
from apps.channels.bot_registry import BotEntry
from apps.channels.checks import (
    CLIENT_BOT_LINK_CHECK_ID,
    check_client_bot_link_is_the_client_bot,
)
from apps.channels.max import handler as max_handler, support_entry
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CLIENT_LINK = "https://max.ru/ayla_client_bot"
SUPPORT_LINK = "https://max.ru/ayla_client_bot?start=support"
HANDOFF_REPLY = "Передаю менеджеру — ответят в течение 30 минут."
#: Ответ на обращение из двери — литералом: видимый текст, черновик до слова владельца.
SUPPORT_REPLY = "Передаю твой вопрос менеджеру."
#: Получатель внутренних оповещений — вымышленный id чата сотрудников.
STAFF_CHAT_ID = "-70000000000001"
USER_ID = 2751

SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="secret-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    stream="max_salon",
    web_app="ayla_salon_bot",
)
CLIENT_BOT = BotEntry(
    slug="client",
    webhook_secret="secret-client",  # pragma: allowlist secret
    api_token="token-client",  # pragma: allowlist secret
    stream="max_global",
    link=CLIENT_LINK,
    web_app="ayla_client_bot",
)


def _client(link: str, *, web_app: str = "ayla_client_bot") -> BotEntry:
    """Клиентская запись реестра с заданной ссылкой."""
    return BotEntry(
        slug="client",
        webhook_secret="secret-client",  # pragma: allowlist secret
        api_token="token-client",  # pragma: allowlist secret
        stream="max_global",
        link=link,
        web_app=web_app,
    )


@pytest.fixture(autouse=True)
def _contour(settings):
    settings.MAX_BOT_REGISTRY = (SALON_BOT, CLIENT_BOT)
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = [STAFF_CHAT_ID]
    settings.HANDOFF_NOTIFY_MAX_USER_IDS = []
    settings.AYLA_SUPPORT_CONTACT = ""
    cache.clear()
    yield
    cache.clear()


# ─────────────────────────────── адрес и сторож ───────────────────────────────


class TestTheAddressIsDerived:
    def test_it_is_the_client_bot_link_with_the_start_parameter(self) -> None:
        assert support_contact.client_support_link() == SUPPORT_LINK

    def test_no_link_in_the_registry_means_no_address(self, settings) -> None:
        assert support_contact.client_support_link() == SUPPORT_LINK
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client(""))
        assert support_contact.client_support_link() == ""

    def test_the_address_opens_the_door_the_bot_listens_on(self) -> None:
        """Адрес и дверь держатся за один параметр: что строит ссылка, то слушает бот."""
        from urllib.parse import parse_qs, urlsplit

        (payload,) = parse_qs(urlsplit(support_contact.client_support_link()).query)["start"]
        # Так парсер сворачивает ``bot_started.payload`` в текст хода.
        assert f"/start {payload}" == support_entry.SUPPORT_ENTRY_COMMAND == "/start support"

    def test_the_staff_setting_is_not_a_source_of_the_client_address(self, settings) -> None:
        """Что бы ни стояло в настройке персонала, клиентский адрес от неё не зависит."""
        settings.AYLA_SUPPORT_CONTACT = "https://max.ru/join/team-chat-invite"
        assert support_contact.client_support_link() == SUPPORT_LINK


class TestTheClientLinkIsChecked:
    def test_a_bot_link_passes(self) -> None:
        assert support_contact.client_bot_link_problem() is None

    @pytest.mark.parametrize(
        "link",
        [CLIENT_LINK + "/", "https://MAX.RU/ayla_client_bot", f"  {CLIENT_LINK}  "],
    )
    def test_spelling_of_the_same_bot_link_passes(self, settings, link: str) -> None:
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client(link))
        assert support_contact.client_bot_link_problem() is None
        assert support_contact.client_support_link() == SUPPORT_LINK

    @pytest.mark.parametrize(
        ("link", "reason"),
        [
            # получатель внутренних оповещений — тот самый чат сотрудников
            (STAFF_CHAT_ID, support_contact.PROBLEM_STAFF_RECIPIENT),
            # приглашение в чат: каким бы ни был адрес внутреннего чата, он такой
            ("https://max.ru/join/team-chat-invite", support_contact.PROBLEM_NOT_A_BOT_LINK),
            ("https://max.ru/c/-70000000000001/AbCd", support_contact.PROBLEM_NOT_A_BOT_LINK),
            ("https://max.ru/", support_contact.PROBLEM_NOT_A_BOT_LINK),
            ("http://max.ru/ayla_client_bot", support_contact.PROBLEM_NOT_A_BOT_LINK),
            # ссылка в реестре — адрес бота, а не переход с намерением
            (CLIENT_LINK + "?start=inv_AYLA7K3M", support_contact.PROBLEM_NOT_A_BOT_LINK),
            (CLIENT_LINK + "#chat", support_contact.PROBLEM_NOT_A_BOT_LINK),
            # салонный бот — не клиентский
            ("https://max.ru/ayla_salon_bot", support_contact.PROBLEM_SALON_BOT),
            # по форме бот, но не тот, кого запись называет в web_app:
            # публичный чат или канал выглядит именно так
            ("https://max.ru/it_ayla_team", support_contact.PROBLEM_HANDLE_MISMATCH),
            ("https://max.me/aylasupport", support_contact.PROBLEM_HANDLE_MISMATCH),
            # не ссылка вовсе
            ("@ayla_support", support_contact.PROBLEM_NOT_A_LINK),
            ("-70000000000002", support_contact.PROBLEM_NOT_A_LINK),
            ("max.ru/ayla_client_bot", support_contact.PROBLEM_NOT_A_LINK),
        ],
    )
    def test_anything_else_is_refused_and_gives_no_address(
        self, settings, link: str, reason: str
    ) -> None:
        # Положительная пара на том же контуре: до подмены адрес есть.
        assert support_contact.client_support_link() == SUPPORT_LINK
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client(link))
        assert support_contact.client_bot_link_problem() == reason
        assert support_contact.client_support_link() == ""

    def test_without_web_app_only_the_form_is_checked(self, settings) -> None:
        """Предел, названный узлом: имя сверить не с чем — проходит любой «бот» по форме."""
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client("https://max.ru/it_ayla_team"))
        assert support_contact.client_bot_link_problem() == support_contact.PROBLEM_HANDLE_MISMATCH
        settings.MAX_BOT_REGISTRY = (
            SALON_BOT,
            _client("https://max.ru/it_ayla_team", web_app=""),
        )
        assert support_contact.client_bot_link_problem() is None

    def test_an_empty_link_is_not_a_wrong_link(self, settings) -> None:
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client("https://max.ru/join/team-chat-invite"))
        assert support_contact.client_bot_link_problem() is not None
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client(""))
        assert support_contact.client_bot_link_problem() is None


class TestDeployCheck:
    def test_a_link_that_is_not_the_client_bot_is_reported_without_stopping_the_deploy(
        self, settings
    ) -> None:
        settings.DEBUG = False
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client("https://max.ru/join/team-chat-invite"))
        (found,) = check_client_bot_link_is_the_client_bot(None)
        assert found.id == CLIENT_BOT_LINK_CHECK_ID == "support.W002"
        # Предупреждение: ``manage.py check`` / ``migrate`` на выкладке не падают.
        from django.core.checks import WARNING

        assert found.level == WARNING
        assert not found.is_serious()
        # …а адрес при этом не показывается никому.
        assert support_contact.client_support_link() == ""
        assert support_contact.PROBLEM_NOT_A_BOT_LINK in found.msg
        # Имя настройки в подсказке есть — значения нет нигде.
        assert "MAX_BOT_<SLUG>_LINK" in (found.hint or "")
        assert "team-chat-invite" not in found.msg + (found.hint or "")

    def test_the_client_bot_is_silent(self, settings) -> None:
        settings.DEBUG = False
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client("https://max.ru/join/team-chat-invite"))
        assert len(check_client_bot_link_is_the_client_bot(None)) == 1
        settings.MAX_BOT_REGISTRY = (SALON_BOT, CLIENT_BOT)
        assert check_client_bot_link_is_the_client_bot(None) == []

    def test_debug_contour_is_silent(self, settings) -> None:
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client("https://max.ru/join/team-chat-invite"))
        settings.DEBUG = False
        assert len(check_client_bot_link_is_the_client_bot(None)) == 1
        settings.DEBUG = True
        assert check_client_bot_link_is_the_client_bot(None) == []


class TestTheStaffButtonIsNotTouched:
    def test_the_salon_bot_still_names_whatever_the_setting_holds(self, settings) -> None:
        """Адресат для персонала — отдельный вопрос владельцу; здесь он не решается."""
        from apps.channels.max import salon_handler

        settings.AYLA_SUPPORT_CONTACT = "@ayla_support"
        assert salon_handler._support_text() == "Поддержка Ayla: @ayla_support"
        settings.AYLA_SUPPORT_CONTACT = ""
        assert salon_handler._support_text() == salon_handler.SUPPORT_FALLBACK_TEXT

    def test_the_staff_setting_does_not_trip_the_client_check(self, settings) -> None:
        settings.DEBUG = False
        settings.MAX_BOT_REGISTRY = (SALON_BOT, _client("https://max.ru/join/team-chat-invite"))
        settings.AYLA_SUPPORT_CONTACT = "@ayla_support"
        assert len(check_client_bot_link_is_the_client_bot(None)) == 1
        settings.MAX_BOT_REGISTRY = (SALON_BOT, CLIENT_BOT)
        assert check_client_bot_link_is_the_client_bot(None) == []


# ─────────────────────────────── дверь в боте ───────────────────────────────


def _run_global(text: str, *, mid: str, user_id: int = USER_ID) -> None:
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Иван"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        },
        trace_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _callbacks(call: dict) -> list[str]:
    out: list[str] = []
    for attachment in call.get("attachments") or []:
        for row in attachment.get("payload", {}).get("buttons", []):
            out.extend(button.get("payload", "") for button in row)
    return out


def _salon_dialog(slug: str, name: str) -> Conversation:
    tenant = Tenant.objects.create(slug=slug, name=name)
    with tenant_scope(tenant):
        bot_user = BotUser.objects.create(
            tenant=tenant, channel="max", channel_user_id=str(USER_ID)
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    Conversation.all_tenants.filter(pk=conv.pk).update(
        last_message_at=datetime(2026, 9, 20, tzinfo=dt_timezone.utc)
    )
    conv.refresh_from_db()
    return conv


def _global_conversation() -> Conversation:
    return Conversation.all_tenants.get(tenant__slug=GLOBAL_BOT_TENANT_SLUG)


class TestTheDoor:
    def test_the_link_gets_a_reply_with_a_button_and_no_task(self, sent, concierge) -> None:
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="d1")
        assert sent[-1]["text"] == support_entry.SUPPORT_ENTRY_TEXT
        assert _callbacks(sent[-1]) == [support_entry.CB_SUPPORT_CALL]
        assert AdminTask.all_tenants.count() == 0
        assert _global_conversation().state != Conversation.State.HUMAN_HANDOFF

    def test_a_plain_start_is_not_the_door(self, sent, concierge) -> None:
        """Отрицательная пара: «/start» без параметра — не реплика поддержки."""
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="d1")
        assert sent[-1]["text"] == support_entry.SUPPORT_ENTRY_TEXT
        _run_global("/start", mid="d2", user_id=USER_ID + 1)
        assert sent[-1]["text"] != support_entry.SUPPORT_ENTRY_TEXT

    def test_what_the_person_writes_next_becomes_the_task(self, sent, concierge) -> None:
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="d1")
        _run_global("Хочу удалить свои данные", mid="d2")
        task = AdminTask.all_tenants.get()
        assert "Хочу удалить свои данные" in task.reason
        assert sent[-1]["text"] == SUPPORT_REPLY
        assert _global_conversation().state == Conversation.State.HUMAN_HANDOFF
        assert concierge.call_count == 0

    def test_the_button_becomes_the_task_and_history_keeps_its_label(self, sent, concierge) -> None:
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="d1")
        _run_global(support_entry.CB_SUPPORT_CALL, mid="d2")
        assert AdminTask.all_tenants.count() == 1
        assert sent[-1]["text"] == SUPPORT_REPLY
        said = list(
            Message.all_tenants.filter(
                conversation=_global_conversation(), role="user"
            ).values_list("content", flat=True)
        )
        assert support_entry.SUPPORT_CALL_BUTTON in said
        assert support_entry.CB_SUPPORT_CALL not in said

    def test_how_long_the_door_waits_is_a_decided_number(self) -> None:
        # Литералом: смена константы обязана быть видна здесь.
        assert support_entry.SUPPORT_ENTRY_TTL_SECONDS == 900


class TestWhereTheTaskLands:
    def test_support_goes_to_the_platform_even_with_one_salon(self, sent, concierge) -> None:
        """Обращение к Ayla — не дело салона: его диалог не замолкает."""
        salon = _salon_dialog("salon-only-2751", "Салон Один")
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="w1")
        _run_global("Хочу выгрузить свои данные", mid="w2")
        task = AdminTask.all_tenants.get()
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        salon.refresh_from_db()
        assert salon.state != Conversation.State.HUMAN_HANDOFF

    def test_the_ordinary_request_for_a_person_still_goes_to_the_only_salon(
        self, sent, concierge
    ) -> None:
        """Пара к узлу выше: без двери правило DRF-2545 прежнее."""
        salon = _salon_dialog("salon-only-2751", "Салон Один")
        _run_global("позовите администратора, пожалуйста", mid="w1")
        task = AdminTask.all_tenants.get()
        assert task.tenant_id == salon.tenant_id
        assert sent[-1]["text"] == HANDOFF_REPLY


class TestTheDoorDoesNotStayOpen:
    def test_without_the_door_a_plain_message_is_not_a_task(self, sent, concierge) -> None:
        _run_global("Хочу удалить свои данные", mid="n1")
        assert sent, "ход обязан получить ответ — иначе «задачи нет» ни о чём"
        assert AdminTask.all_tenants.count() == 0

    @pytest.mark.parametrize("gesture", ["/whoami", "cb:menu:main"])
    def test_another_gesture_closes_it(self, sent, concierge, gesture) -> None:
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="c1")
        assert support_entry.support_turn("что угодно", _global_conversation()) == (
            support_entry.TURN_SAID
        )
        _run_global(gesture, mid="c2")
        _run_global("запиши меня на маникюр", mid="c3")
        assert AdminTask.all_tenants.count() == 0

    def test_the_door_is_used_once(self, sent, concierge) -> None:
        _run_global(support_entry.SUPPORT_ENTRY_COMMAND, mid="o1")
        _run_global("Хочу удалить свои данные", mid="o2")
        assert AdminTask.all_tenants.count() == 1
        assert support_entry.support_turn("ещё одно", _global_conversation()) is None
