"""DRF-2267 срез 7 — отказ по согласию на дневник не тупик.

«Чтобы показать твои записи, мне нужно согласие на обработку личных данных»
уходило без единой кнопки: человеку назвали условие и не дали его выполнить.

Единственный честный следующий шаг здесь — сам экран согласия, и он уже
существует (DRF-1968: `consent_offer_action_data`, кнопка «Дать согласие»,
которая помнит, откуда человек пришёл). Этот лист добавляет вход `diary`
в ту же механику.

### Почему возврат делается ПОСЛЕ записи согласия, а не в навыке

Журнал 152-ФЗ пишется в `run_onboarding_turn` ПОСЛЕ того, как
`WelcomeSkill.handle` вернул ответ (`_record_consent_journal`). Если бы
дневник рисовался внутри навыка, он спросил бы своё согласие раньше, чем
оно записано, и человек получил бы «мне нужно согласие» сразу после того,
как его дал, — тупик хуже исходного. Поэтому возврат живёт там, где
согласие уже на руках.

### Обхода ворот нет

Дневник рисуется своим обычным путём и спрашивает своё согласие сам. Если
согласия по-прежнему нет (журнал не записался), человек видит отказ, а не
записи: узел ``TestTheButtonDoesNotBypassTheGate`` — обязательный.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.personal_surface import CONSENT_CLOSED_TEXT
from apps.skills.menu.matching import CALLBACK_MENU_HELP
from apps.skills.welcome.skill import CONSENT_OFFER_LABEL, CONSENT_RECOVERY_FAILED_TEXT

pytestmark = pytest.mark.django_db(transaction=True)

CONSENT_ORIGIN = "diary"
OFFER_CALLBACK = f"cb:welcome:consent_offer_{CONSENT_ORIGIN}"
GRANT_CALLBACK = f"cb:welcome:consent_yes_{CONSENT_ORIGIN}"


def _callbacks(action_data: Any) -> list[str]:
    return [b.get("callback", "") for b in (action_data or {}).get("buttons") or []]


def _closed_diary(monkeypatch) -> None:
    """Согласия на личные данные нет — дневник читать нельзя."""
    monkeypatch.setattr(
        "apps.orchestrator.personal_surface.personal_records_consent_open", lambda _u: False
    )


@pytest.fixture
def tenant(db):
    from apps.tenancy.models import Tenant

    return Tenant.objects.create(slug="dead-ends-2267g", name="Salon", timezone="Europe/Moscow")


@pytest.fixture
def global_user():
    from apps.identity.services import resolve_or_create_global_bot_user

    return resolve_or_create_global_bot_user(channel="max", channel_user_id="dead-2267g")


@pytest.fixture
def conversation(global_user):
    from apps.conversations.services import resolve_active_global_conversation

    return resolve_active_global_conversation(global_user)


# ── отказ несёт кнопку согласия ──────────────────────────────────────────


class TestTheRefusalOffersTheConsentScreen:
    def test_diary_without_consent_offers_to_give_it(self, monkeypatch, settings) -> None:
        settings.NUTRITION_ENABLED = True
        _closed_diary(monkeypatch)
        from apps.identity.services.global_tenant import get_global_bot_tenant
        from apps.orchestrator.personal_surface import render_diary
        from apps.tenancy.context import tenant_scope

        with tenant_scope(get_global_bot_tenant()):
            reply = render_diary(object())

        assert reply.text == CONSENT_CLOSED_TEXT  # текст согласия не трогаем
        assert reply.action_data is not None
        assert _callbacks(reply.action_data) == [OFFER_CALLBACK]
        assert [b["label"] for b in reply.action_data["buttons"]] == [CONSENT_OFFER_LABEL]

    def test_off_the_global_path_the_menu_instead(self, monkeypatch, settings, tenant) -> None:
        """Кнопка согласия живёт только на глобальном пути (DRF-1968)."""
        settings.NUTRITION_ENABLED = True
        _closed_diary(monkeypatch)
        from apps.orchestrator.personal_surface import render_diary
        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            reply = render_diary(object())

        assert reply.text == CONSENT_CLOSED_TEXT
        assert _callbacks(reply.action_data) == [CALLBACK_MENU_HELP]


# ── согласие выдано из отказа → человек видит дневник ────────────────────


class TestGrantReturnsToTheDiary:
    def test_the_grant_turn_answers_with_the_diary_itself(self, global_user, conversation) -> None:
        from apps.channels.max import global_onboarding

        diary = DiscoveryReply(text="Итоги дня: …", action_data={"buttons": []})
        with (
            patch.object(global_onboarding, "_record_consent_journal", return_value=True),
            patch(
                "apps.orchestrator.personal_surface.render_diary", return_value=diary
            ) as rendered,
        ):
            reply = global_onboarding.run_onboarding_turn(conversation, global_user, GRANT_CALLBACK)

        assert rendered.called  # дневник рисовался своим обычным путём
        assert reply.text == diary.text

    def test_a_failed_journal_says_so_and_shows_nothing(self, global_user, conversation) -> None:
        """Согласие не записалось — обещать «готово» и показывать записи нельзя."""
        from apps.channels.max import global_onboarding

        with (
            patch.object(global_onboarding, "_record_consent_journal", return_value=False),
            patch("apps.orchestrator.personal_surface.render_diary") as rendered,
        ):
            reply = global_onboarding.run_onboarding_turn(conversation, global_user, GRANT_CALLBACK)

        assert reply.text == CONSENT_RECOVERY_FAILED_TEXT
        assert not rendered.called


# ── обязательный узел: кнопка не проносит мимо ворот ─────────────────────


class TestTheButtonDoesNotBypassTheGate:
    def test_without_consent_the_return_shows_the_refusal_not_the_records(
        self, monkeypatch, global_user, conversation, settings
    ) -> None:
        """Журнал сказал «записано», а ворота дневника — «нет»: побеждают ворота.

        Дневник спрашивает согласие сам и здесь исключений не получает: по
        этой кнопке человек не может увидеть ничего, что закрыто.
        """
        settings.NUTRITION_ENABLED = True
        _closed_diary(monkeypatch)
        from apps.channels.max import global_onboarding

        with patch.object(global_onboarding, "_record_consent_journal", return_value=True):
            reply = global_onboarding.run_onboarding_turn(conversation, global_user, GRANT_CALLBACK)

        assert reply.text == CONSENT_CLOSED_TEXT
        assert "ккал" not in reply.text
