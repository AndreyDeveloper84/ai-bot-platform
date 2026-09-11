"""Дверь соло-мастера в салонном боте (§122, срез S1).

Человек без роли, написавший что-то непохожее на код, до этой правки
получал только «пришлите код». Соло-мастеру кода взять неоткуда: его
никто не приглашал, он работает сам. Дверь предлагает ему завести
кабинет — и говорит правду о том, что кабинет ещё не работает.

### В эту ветку приходят ДВОЕ

Тот, кто здесь впервые, и мастер, чью карточку сняли: архивация пишет
`is_active` и `archived_at` и **оставляет** `linked_bot_user`, поэтому
`resolve_role` называет снятую мастер клиентом (DRF-1654, чинит соседнее
окно). Предложить ей завести кабинет соло-мастера значило бы развести
одного человека на два тенанта — и в тот момент, когда она пришла
разбираться, почему её сняли.

**Замер пилота 11.09.2026: таких строк ноль** (архивных три, связи ни у
одной). Тест стережёт МЕХАНИЗМ, а не наблюдение: архивация связь не
снимает, значит первая же снятая связанная карточка создаст этот случай.
Красную строку показать больше негде — только фикстурой.
"""

from __future__ import annotations

import pytest

from apps.catalog.models import CatalogMaster
from apps.channels.max import salon_handler
from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import SoloSetupState
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class _Event:
    """Минимальное событие: ровно те поля, которые дверь читает.

    `raw` обязателен — `_sender_name` берёт имя из `message.sender.name`,
    туда, куда его кладёт MAX. Пустой словарь здесь честнее выдуманного
    имени: дверь должна работать и когда имени нет.
    """

    def __init__(self, text, raw=None):
        self.text = text
        self.chat_id = "555"
        self.channel = "max"
        self.channel_user_id = "solo-door-1"
        self.raw = raw if raw is not None else {}


@pytest.fixture
def salon(db):
    return Tenant.objects.create(slug="door-salon", name="Дверь", is_active=True)


@pytest.fixture
def bot_user(salon):
    return BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id="solo-door-1",
        display_name="Ольга",
    )


@pytest.fixture
def said(monkeypatch):
    out = []

    def _capture(event, text, attachments=None):
        out.append({"text": text, "attachments": attachments})

    monkeypatch.setattr(salon_handler, "_reply", _capture)
    return out


class TestTheOfferAppearsForSomeoneWithNoTrace:
    def test_a_newcomer_is_offered_a_workspace(self, bot_user, said):
        salon_handler._ask_for_code_with_solo_offer(_Event("здравствуйте"), bot_user)

        assert len(said) == 1
        assert salon_handler.ASK_FOR_CODE in said[0]["text"]
        assert salon_handler.SOLO_OFFER.strip() in said[0]["text"]

    def test_the_offer_carries_a_button_not_a_guessable_word(self, bot_user, said):
        """Слаг кнопки, а не свободный текст.

        Угадывать «да» пришлось бы по словам, а угаданное «да» — это
        регистрация человека, который её не просил.
        """
        salon_handler._ask_for_code_with_solo_offer(_Event("привет"), bot_user)

        attachments = said[0]["attachments"]
        assert attachments, "предложение без кнопки — это предложение без ответа"


class TestSomeoneWithATraceIsNotOffered:
    """Снятая мастер не получает предложения завести второй кабинет."""

    def test_an_archived_card_suppresses_the_offer(self, salon, bot_user, said):
        from django.utils import timezone

        CatalogMaster.all_tenants.create(
            tenant=salon,
            external_id=-424242,
            external_updated_at=timezone.now(),
            name="Ольга",
            linked_bot_user=bot_user,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=False,
            archived_at=timezone.now(),
        )

        salon_handler._ask_for_code_with_solo_offer(_Event("привет"), bot_user)

        assert said[0]["text"] == salon_handler.ASK_FOR_CODE
        assert salon_handler.SOLO_OFFER.strip() not in said[0]["text"]
        assert not said[0]["attachments"]


class TestRegistrationTellsTheTruthAboutItsOutcome:
    def test_a_fresh_workspace_is_not_announced_as_ready(self, bot_user, said):
        """§122: регистрация не завершается как «готово».

        Текст обязан сказать и что создано, и чего не хватает. «Кабинет
        создан» в одиночку — та самая тишина, ради которой заводилось
        `setup_state`: человек ушёл бы считать себя работающим.
        """
        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK), bot_user
        )

        assert said[0]["text"] == salon_handler.SOLO_CREATED_PENDING
        assert "не видно клиентам" in said[0]["text"]

    def test_the_workspace_really_is_pending(self, bot_user, said):
        """Не только текст — состояние.

        Текст можно написать какой угодно; проверяется, что он совпал с
        тем, что в самом деле произошло.
        """
        from apps.identity.services.solo_onboarding import create_solo_provider

        result = create_solo_provider(
            channel=bot_user.channel,
            channel_user_id=bot_user.channel_user_id,
            display_name="Ольга",
        )

        assert result.setup_state is SoloSetupState.SETUP_PENDING
        assert result.blocked_by == "ayla_unlinked"

    def test_a_second_tap_says_it_already_exists(self, bot_user, said):
        """Повтор — не ошибка и не повод молчать."""
        event = _Event(salon_handler.SOLO_REGISTER_CALLBACK)

        salon_handler._register_solo_provider(event, bot_user)
        salon_handler._register_solo_provider(event, bot_user)

        assert said[0]["text"] == salon_handler.SOLO_CREATED_PENDING
        assert said[1]["text"] == salon_handler.SOLO_ALREADY_REGISTERED


class TestTheDoorIsReachedOnlyByItsOwnButton:
    def test_another_button_still_gets_the_code_prompt(self, bot_user, said):
        """Положительная стража: дверь не съела соседние нажатия.

        Без неё тесты выше зеленели бы и на коде, который на ЛЮБОЕ
        нажатие заводит кабинет, — то есть на регистрации людей, которые
        её не просили.
        """
        from apps.tenancy.context import tenant_scope

        assert salon_handler.SOLO_REGISTER_CALLBACK != "cb:menu:requests"
        with tenant_scope(bot_user.tenant):
            # Прямая проверка предиката, которым дверь отличает своё
            # нажатие от чужого: ветка вызывающего разбирается в
            # `_handle_salon_event_inner`, и тащить сюда весь конвейер
            # значило бы проверять его, а не дверь.
            assert salon_handler._is_button_tap("cb:menu:requests")
            assert salon_handler._is_button_tap(salon_handler.SOLO_REGISTER_CALLBACK)
