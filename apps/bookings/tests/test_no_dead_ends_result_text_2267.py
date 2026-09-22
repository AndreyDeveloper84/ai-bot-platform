"""DRF-2267 срез 6 — отказы ворот записи, чей текст пришёл из скилла.

Три ответа `_dispatch_confirm` отдавали `result.text` без единой кнопки.
Текст у них чужой — его пишет не этот слой, — поэтому кнопка выбиралась по
тому, что человек должен мочь сделать дальше ПО СМЫСЛУ этого текста
(перепись 23.09, решение главного окна):

* каталог не продаёт предложение («записаться на эту услугу онлайн нельзя,
  напишите администратору салона») — альтернатив у человека нет вовсе,
  и настоящий выход один: начать подбор заново;
* «Запись этим способом сейчас недоступна» (медицинская по происхождению
  ветка, никому ничего не обещает) — только «Меню»: звать обратно к записи,
  которую только что закрыли, нельзя;
* «Сервис расписания сейчас недоступен» — «🔄 Выбрать другое время» для той
  же пары мастер+услуга, что в заявке, то есть тот же поток, а не новый.

Ветка с `should_handoff=True` («Передадим запрос специалисту») кнопок не
получает: после передачи бот молчит, и тап упал бы в тишину.

### Почему не «Подобрать услугу» и не «Найти салон»

Ворота записи отвечают на ДВУХ поверхностях: на салонном боте тап доходит
до реестра скиллов, на глобальном — через `handoff.route_booking_callback`.
`DISCOVER_TAP_TEXT` и `cb:catalog:salons` живут только на глобальном пути
(`apps/skills/menu/matching.py` их не знает), и на салонном боте такая
кнопка ответила бы «я вас не понял» — хуже, чем никакой (DRF-1492).
Семейство `cb:menu:*` переходит границу по построению, поэтому «начать
подбор заново» здесь — существующий чип «📅 Записаться» (`cb:menu:book`,
на салонном пути читается как «Хочу записаться»).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.booking.models import PendingBookingAction
from apps.bookings.callbacks import (
    LABEL_ANOTHER_TIME,
    LABEL_BOOK_AGAIN,
    LABEL_MENU,
    BookingGateCallbackSkill,
)
from apps.bookings.pending_actions import create_pending
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.skills.booking.tools import BookingToolResult
from apps.skills.menu.matching import CALLBACK_MENU_BOOK, CALLBACK_MENU_HELP
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MASTER_ID = "11"
SERVICE_ID = "22"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="dead-ends-2267f", name="Dead Ends", manager_chat_id="mgr-1")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="de-2267f",
        chat_id="de-2267f",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    with tenant_scope(tenant):
        return Conversation.objects.create(
            tenant=tenant, bot_user=bot_user, state=Conversation.State.IDLE
        )


def _buttons(result: Any) -> list[tuple[str, str]]:
    attachments = (result.action_data or {}).get("attachments") or []
    return [
        (b["label"], b["callback"])
        for att in attachments
        for b in (att.get("payload") or {}).get("buttons") or []
    ]


def _confirm(
    tenant: Tenant, bot_user: BotUser, conversation: Conversation, outcome: BookingToolResult
) -> Any:
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CONFIRM,
        payload={
            "master_id": MASTER_ID,
            "service_id": SERVICE_ID,
            "slot_datetime": "2026-10-01T10:00:00+00:00",
            "client_phone": "79991234567",
            "client_name": "Anna",
        },
    )
    ctx = SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=f"cb:book:confirm:{token}",
        has_attachments=False,
    )
    with (
        patch("apps.integrations.yclients.get_yclients_client"),
        patch("apps.bookings.callbacks.execute_confirm", return_value=outcome),
    ):
        return BookingGateCallbackSkill().handle(ctx)


class TestTheCatalogueWillNotSellIt:
    def test_the_refusal_offers_to_start_the_search_again(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        from apps.integrations.ayla.offer_refusal import OFFER_NOT_SELLABLE_SLUG, client_text_for

        text = client_text_for(None)
        result = _confirm(
            tenant,
            bot_user,
            conversation,
            BookingToolResult(text=text, error=OFFER_NOT_SELLABLE_SLUG),
        )

        assert result.reply_text == text  # чужой текст не трогаем
        assert _buttons(result) == [
            (LABEL_BOOK_AGAIN, CALLBACK_MENU_BOOK),
            (LABEL_MENU, CALLBACK_MENU_HELP),
        ]


class TestHealthCheckRefusals:
    def test_the_one_that_promises_nobody_offers_only_the_menu(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Ни одной кнопки про запись: её этой дорогой только что закрыли."""
        from apps.integrations.ayla.health_check import NOT_APPLICABLE_TEXT

        result = _confirm(
            tenant,
            bot_user,
            conversation,
            BookingToolResult(
                text=NOT_APPLICABLE_TEXT, error="health_check_handoff", handoff=False
            ),
        )

        assert result.reply_text == NOT_APPLICABLE_TEXT
        assert _buttons(result) == [(LABEL_MENU, CALLBACK_MENU_HELP)]

    def test_the_one_that_hands_over_stays_silent(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        """Передача специалисту: бот молчит, тап упал бы в тишину."""
        from apps.integrations.ayla.health_check import HANDOFF_TEXT

        result = _confirm(
            tenant,
            bot_user,
            conversation,
            BookingToolResult(text=HANDOFF_TEXT, error="health_check_handoff", handoff=True),
        )

        assert result.should_handoff is True  # присутствие: передача объявлена
        assert _buttons(result) == []


class TestScheduleDown:
    def test_it_offers_the_same_pair_again(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        from apps.skills.booking.tools import SCHEDULE_UNAVAILABLE_TEXT

        result = _confirm(
            tenant,
            bot_user,
            conversation,
            BookingToolResult(text=SCHEDULE_UNAVAILABLE_TEXT, error="schedule_unavailable"),
        )

        assert result.reply_text == SCHEDULE_UNAVAILABLE_TEXT
        assert _buttons(result) == [
            (LABEL_ANOTHER_TIME, f"cb:book:pick_master:{MASTER_ID}:{SERVICE_ID}"),
            (LABEL_MENU, CALLBACK_MENU_HELP),
        ]
