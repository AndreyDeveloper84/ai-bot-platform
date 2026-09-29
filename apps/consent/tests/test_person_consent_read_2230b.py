"""DRF-2230 (живой проход 21.09): согласие читается по человеку, а не по строке.

У человека в пилоте две оболочки ``BotUser``: чат глобального бота — под
сентинелом ``global_bot``, Mini App — под ``MAX_BOT_TENANT_SLUG``. Чат пишет
согласие на свою строку; Главная Mini App читала свою — и блок «нужно
согласие» висел после «Готово, согласие есть» в чате.

Правило чтения (``consent.services.has_person_consent``): оболочки человека —
по ``(channel, channel_user_id)``, пустой id — только сама строка; согласие
открыто, если у человека есть активный грант И его последний грант позже
последнего отзыва на любой оболочке. На одной строке это ровно прежнее
правило ``has_global_consent``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value


@pytest.fixture
def shells(db):
    """Один человек, две оболочки: чат (global_bot) и Mini App (салон)."""
    from apps.identity.services.resolver import resolve_or_create_global_bot_user

    salon = Tenant.objects.create(slug="formula-2230c", name="Формула 2230c")
    chat = resolve_or_create_global_bot_user(channel="max", channel_user_id="92240")
    miniapp = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id="92240")
    return chat, miniapp


def _grant(bot_user: BotUser) -> ConsentRecord:
    from apps.consent.services import record_global_consent

    return record_global_consent(bot_user, consent_type=PD, source="test", document_version="v1")


def _open(bot_user: BotUser) -> bool:
    from apps.orchestrator.personal_surface import personal_records_consent_open

    return personal_records_consent_open(bot_user)


class TestReadByPerson:
    def test_grant_on_the_chat_shell_opens_the_miniapp_shell(self, shells) -> None:
        chat, miniapp = shells
        assert _open(miniapp) is False  # положительная пара: до гранта закрыто
        _grant(chat)
        assert _open(miniapp) is True

    def test_grant_before_the_miniapp_shell_exists(self, db) -> None:
        """Ради этого Б лучше А: оболочка Mini App рождается ПОСЛЕ согласия в чате."""
        from apps.identity.services.resolver import resolve_or_create_global_bot_user

        chat = resolve_or_create_global_bot_user(channel="max", channel_user_id="92241")
        _grant(chat)
        salon = Tenant.objects.create(slug="formula-2230d", name="Формула 2230d")
        late = BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id="92241")
        assert _open(late) is True

    def test_withdrawal_on_any_shell_closes_it(self, shells) -> None:
        chat, miniapp = shells
        _grant(chat)
        _grant(miniapp)
        assert _open(miniapp) is True  # положительная пара
        ConsentRecord.all_tenants.filter(bot_user=chat, consent_type=PD).update(
            withdrawn_at=timezone.now()
        )
        # Грант на строке Mini App ещё «активен», но человек отозвал согласие.
        assert _open(miniapp) is False
        assert _open(chat) is False

    def test_regrant_after_withdrawal_reopens(self, shells) -> None:
        chat, miniapp = shells
        _grant(chat)
        ConsentRecord.all_tenants.filter(bot_user=chat, consent_type=PD).update(
            withdrawn_at=timezone.now() - timedelta(minutes=5)
        )
        assert _open(miniapp) is False
        _grant(chat)
        assert _open(miniapp) is True

    def test_empty_channel_id_is_not_an_identity(self, db) -> None:
        """Ложный вход: чужой человек с тем же пустым id согласия не видит."""
        a_tenant = Tenant.objects.create(slug="t-2230-a", name="A")
        b_tenant = Tenant.objects.create(slug="t-2230-b", name="B")
        a = BotUser.all_tenants.create(tenant=a_tenant, channel="max", channel_user_id="")
        b = BotUser.all_tenants.create(tenant=b_tenant, channel="max", channel_user_id="")
        _grant(a)
        assert _open(a) is True  # положительная пара: своя строка открыта
        assert _open(b) is False

    def test_another_channel_is_another_person(self, db) -> None:
        tenant = Tenant.objects.create(slug="t-2230-c", name="C")
        mx = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="777")
        tg = BotUser.all_tenants.create(tenant=tenant, channel="telegram", channel_user_id="777")
        _grant(mx)
        assert _open(mx) is True
        assert _open(tg) is False


class TestTheChatSideReadsByPersonToo:
    def test_consent_given_on_the_miniapp_shell_is_not_asked_again_in_chat(self, shells) -> None:
        """Зеркальный случай: грант только на строке Mini App — чат не просит снова."""
        from apps.channels.max.global_onboarding import _consent_captured

        chat, miniapp = shells
        assert _consent_captured(chat) is False  # положительная пара
        _grant(miniapp)
        assert _consent_captured(chat) is True
