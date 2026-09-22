"""DRF-2267 срез 8 — отказ по согласию у списка памяти не тупик.

Тот же отказ, что был у дневника (#2015), и то же решение: человек шёл
смотреть, что бот о нём помнит, — «Меню» вернуло бы его в начало искать это
место заново. Поэтому свой вход возврата ``memory``, который возвращает сам
список памяти.

Механика та же, что у ``diary``: кнопка «Дать согласие» (DRF-1968), вход
объявлен в ``CONSENT_RECOVERY_RESUMED_ORIGINS`` — том же множестве, которое
читает возврат, — и возврат рисуется ПОСЛЕ записи журнала 152-ФЗ.

### Две закрытые двери, и они не пересекаются

`render_memory` отказывает дважды: нет согласия PERSONAL_DATA и закрыт
доступ к контексту человека (§2.4, S2-2). Вторая дверь на глобальном пути
не запирается вовсе (``person_context_access`` пропускает сентинел), а на
салонном кнопки согласия и так нет — она бы вернула к тому же отказу. Узел
``test_on_the_salon_path_only_the_menu`` держит эту половину.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.personal_surface import CONSENT_CLOSED_TEXT
from apps.skills.menu.matching import CALLBACK_MENU_HELP
from apps.skills.welcome.skill import CONSENT_OFFER_LABEL

pytestmark = pytest.mark.django_db(transaction=True)

OFFER_CALLBACK = "cb:welcome:consent_offer_memory"
GRANT_CALLBACK = "cb:welcome:consent_yes_memory"


def _callbacks(action_data: Any) -> list[str]:
    return [b.get("callback", "") for b in (action_data or {}).get("buttons") or []]


@pytest.fixture
def tenant(db):
    from apps.tenancy.models import Tenant

    return Tenant.objects.create(slug="dead-ends-2267h", name="Salon", timezone="Europe/Moscow")


@pytest.fixture
def global_user():
    from apps.identity.services import resolve_or_create_global_bot_user

    return resolve_or_create_global_bot_user(channel="max", channel_user_id="dead-2267h")


@pytest.fixture
def conversation(global_user):
    from apps.conversations.services import resolve_active_global_conversation

    return resolve_active_global_conversation(global_user)


def _closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.orchestrator.personal_surface.personal_records_consent_open", lambda _u: False
    )


class TestTheRefusalOffersTheConsentScreen:
    def test_memory_without_consent_offers_to_give_it(self, monkeypatch) -> None:
        _closed(monkeypatch)
        from apps.identity.services.global_tenant import get_global_bot_tenant
        from apps.orchestrator.personal_surface import render_memory
        from apps.tenancy.context import tenant_scope

        with tenant_scope(get_global_bot_tenant()):
            reply = render_memory(object())

        assert reply.text == CONSENT_CLOSED_TEXT  # текст согласия не трогаем
        assert reply.action_data is not None
        assert _callbacks(reply.action_data) == [OFFER_CALLBACK]
        assert [b["label"] for b in reply.action_data["buttons"]] == [CONSENT_OFFER_LABEL]

    def test_on_the_salon_path_only_the_menu(self, monkeypatch, tenant) -> None:
        """Там, где кнопка согласия вернула бы к тому же отказу, её нет."""
        _closed(monkeypatch)
        from apps.orchestrator.personal_surface import render_memory
        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            reply = render_memory(object())

        assert reply.text == CONSENT_CLOSED_TEXT
        assert _callbacks(reply.action_data) == [CALLBACK_MENU_HELP]


class TestGrantReturnsToTheMemoryList:
    def test_the_grant_turn_answers_with_the_list_itself(self, global_user, conversation) -> None:
        from apps.channels.max import global_onboarding

        remembered = DiscoveryReply(text="Помню: цель — высыпаться.", action_data={"buttons": []})
        with (
            patch.object(global_onboarding, "_record_consent_journal", return_value=True),
            patch(
                "apps.orchestrator.personal_surface.render_memory", return_value=remembered
            ) as rendered,
        ):
            reply = global_onboarding.run_onboarding_turn(conversation, global_user, GRANT_CALLBACK)

        assert rendered.called  # список рисовался своим обычным путём
        assert reply.text == remembered.text

    def test_the_diary_origin_still_returns_the_diary(self, global_user, conversation) -> None:
        """Положительная пара: входы не перепутаны — каждый в свой поток."""
        from apps.channels.max import global_onboarding

        diary = DiscoveryReply(text="Итоги дня: …")
        with (
            patch.object(global_onboarding, "_record_consent_journal", return_value=True),
            patch("apps.orchestrator.personal_surface.render_diary", return_value=diary),
            patch("apps.orchestrator.personal_surface.render_memory") as memory,
        ):
            reply = global_onboarding.run_onboarding_turn(
                conversation, global_user, "cb:welcome:consent_yes_diary"
            )

        assert reply.text == diary.text
        assert not memory.called


class TestTheButtonDoesNotBypassTheGate:
    def test_without_consent_the_return_shows_the_refusal_not_the_memories(
        self, monkeypatch, global_user, conversation
    ) -> None:
        """Ворота списка памяти спрашивают согласие сами и здесь не отключены."""
        _closed(monkeypatch)
        from apps.channels.max import global_onboarding

        with patch.object(global_onboarding, "_record_consent_journal", return_value=True):
            reply = global_onboarding.run_onboarding_turn(conversation, global_user, GRANT_CALLBACK)

        assert reply.text == CONSENT_CLOSED_TEXT
