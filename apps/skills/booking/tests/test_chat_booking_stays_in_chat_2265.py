"""Запись, начатая в боте, в боте и заканчивается (DRF-2265, CD §69).

Владелец 21.09, живой проход (скрин ``docs/screens/err-write-bot.png``):
«Записаться к Софья Кулагина» → «Открой приложение — там выберешь время.»
→ Mini App. Решение, дословно: «но запись если начинается в боте, она и
заканчивается в боте». Оно меняет DRF-2178 (#1938): там тап по
специалисту в боте с настроенным Mini App уводил в приложение.

## Признак

Путь записи — это **место, где у человека спрашивают время визита и
подтверждают запись**. Начатый в чате путь делает и то и другое в чате,
при любой настройке бота. Путь из Mini App (C04 → C05, макет DRF-1320)
живёт своей жизнью и этим модулем не проверяется.

## Что заперто

1. главный узел — бот С Mini App: тап по специалисту → выбор даты в чате;
2. бот без Mini App — то же самое; вместе с (1) это «ни при какой
   настройке человек не остаётся без записи в чате» (прежний сторож
   «две стороны одного факта» #1938, перевёрнутый под §69);
3. тап по слоту → подтверждение в чате; занятый слот → «Это время уже
   занято. Выберите другое:» и другие слоты в чате;
4. перепись производителей чипов дат и кнопки «Выбрать дату» (#1938)
   остаётся: новый производитель обязан быть продолжением чатового пути.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.integrations.yclients import AvailableTime
from apps.skills.base import SkillContext
from apps.skills.booking.skill import BookingSkill
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.skills.booking.tests.test_skill import (  # noqa: F401 — _isolated_env: настройки LLM
    BOOKING_DATE,
    FakeYClients,
    _booking_date,
    _isolated_env,
    _patch_provider_complete,
    _patch_yclients,
    _service,
    _staff,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="chat-stays-2265", name="Chat stays 2265")


@pytest.fixture
def context(tenant: Tenant) -> SkillContext:
    bot_user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="u2265", chat_id="u2265"
    )
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return SkillContext(conversation=conv, bot_user=bot_user, message_text="запиши")


@pytest.fixture
def with_miniapp(settings):
    settings.MAX_BOT_WEB_APP = "aylabot"
    return settings


@pytest.fixture
def without_miniapp(settings):
    settings.MAX_BOT_WEB_APP = ""
    settings.MAX_BOT_MINIAPP_URL = ""
    return settings


def _handle(context, tenant, text: str, client: FakeYClients) -> Any:
    ctx = SkillContext(
        conversation=context.conversation, bot_user=context.bot_user, message_text=text
    )
    with _patch_yclients(client), _patch_provider_complete([]):
        with tenant_scope(tenant):
            return BookingSkill().handle(ctx)


def _buttons(result: Any) -> list[dict[str, str]]:
    return result.action_data["attachments"][0]["payload"]["buttons"]


def _client(*times: str) -> FakeYClients:
    client = FakeYClients()
    client.services_rows = [_service(22)]
    client.staff_rows = [_staff(11, "Софья")]
    client.dates = [BOOKING_DATE, _booking_date(1), _booking_date(3)]
    client.times = [
        AvailableTime(time=t, datetime=f"{BOOKING_DATE}T{t}:00", seance_length_s=3600)
        for t in times
    ]
    return client


class TestTheDateIsAskedInChat:
    """Узлы 1 и 2: при любой настройке бота — выбор даты в чате."""

    def test_with_the_app_configured_the_date_is_still_asked_in_chat(
        self, with_miniapp, context, tenant
    ):
        result = _handle(context, tenant, "cb:book:pick_master:11:22", _client("14:00"))

        assert result.reply_text == "Выберите дату:"
        callbacks = [b["callback"] for b in _buttons(result)]
        assert f"cb:book:pick_date:11:{BOOKING_DATE}:22" in callbacks
        # Ни одна кнопка не уводит в приложение.
        assert not any("web_app" in b or "url" in b for b in _buttons(result))

    def test_without_the_app_the_same_chat_path(self, without_miniapp, context, tenant):
        result = _handle(context, tenant, "cb:book:pick_master:11:22", _client("14:00"))

        assert result.reply_text == "Выберите дату:"
        callbacks = [b["callback"] for b in _buttons(result)]
        assert f"cb:book:pick_date:11:{BOOKING_DATE}:22" in callbacks


class TestTheBookingIsConfirmedInChat:
    """Узел 3: слот и подтверждение — в чате, в боте с Mini App."""

    def test_a_slot_tap_is_confirmed_in_chat(self, with_miniapp, context, tenant):
        result = _handle(
            context,
            tenant,
            f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
            _client("14:00"),
        )

        assert "Подтверждаете?" in result.reply_text
        assert result.action_data["pending_action"]["kind"] == "confirm"

    def test_a_taken_slot_is_answered_in_chat_with_the_others(self, with_miniapp, context, tenant):
        result = _handle(
            context,
            tenant,
            f"cb:book:pick_slot:11:22:{BOOKING_DATE}T14:00:00",
            _client("15:00", "16:00"),
        )

        assert result.reply_text == "Это время уже занято. Выберите другое:"
        callbacks = [b["callback"] for b in _buttons(result)]
        assert f"cb:book:pick_slot:11:22:{BOOKING_DATE}T15:00:00" in callbacks
        assert f"cb:book:pick_slot:11:22:{BOOKING_DATE}T16:00:00" in callbacks


class TestTheChatPathHasKnownProducers:
    """Узел 4 — перепись #1938, перевёрнутая под §69.

    Раньше она охраняла предикат «где спрашивать время». Предиката больше
    нет: чат спрашивает всегда. Перепись осталась, потому что охраняет
    другое — у чипов дат и у кнопки «Выбрать дату» известные производители,
    и все они продолжают уже начатый в чате путь. Появится новый
    производитель — автор обязан спросить себя, начат ли путь в чате, и
    если нет, куда он ведёт человека.
    """

    def test_only_the_picker_emits_the_more_dates_button(self):
        import inspect

        from apps.skills.booking import skill as booking_skill

        source = inspect.getsource(booking_skill)
        emitters = [
            line
            for line in source.splitlines()
            if "CALLBACK_BOOK_MORE_DATES_PREFIX" in line and '"callback"' in line
        ]
        assert len(emitters) == 1, (
            "Кнопку «Выбрать дату» рисует кто-то ещё. Проверь, что это "
            "продолжение начатой в чате записи (CD §69)."
        )

    def test_the_producers_of_date_chips_are_the_known_ones(self):
        import inspect

        from apps.skills.booking import skill as booking_skill

        source = inspect.getsource(booking_skill).splitlines()
        callers: set[str] = set()
        current = ""
        for line in source:
            if line.startswith("def "):
                current = line[4:].split("(", 1)[0]
            if "_action_data_for_date_pick(" in line and not line.startswith("def "):
                callers.add(current)
        assert callers == {"_render_other_dates", "_render_date_picker"}, (
            f"Чипы дат собирает кто-то ещё: {sorted(callers)}. Проверь, что это "
            "продолжение начатой в чате записи (CD §69)."
        )
