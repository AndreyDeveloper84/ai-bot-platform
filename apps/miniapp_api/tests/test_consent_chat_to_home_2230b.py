"""DRF-2230, живой проход владельца 21.09: согласие дано в чате — а Главная
по-прежнему показывает блок «нужно согласие».

Воспроизведение полного пути, без угадывания:

1. человек нажимает в чате глобального бота «Да, продолжим» с происхождением
   ``miniapp`` — ровно то, что пришло по приглашению с Главной;
2. глобальный онбординг пишет журнал согласия (``_record_consent_journal`` →
   ``record_global_consent``) — на ТУ строку ``BotUser``, что вела разговор:
   под сентинелом ``global_bot``;
3. Mini App резолвит СВОЮ строку того же человека — под
   ``MAX_BOT_TENANT_SLUG`` — и ``wellness/today`` читает согласие построчно
   (``personal_records_consent_open`` → ``has_global_consent(bot_user=…)``).

Разные строки ``unique_together (tenant, channel, channel_user_id)`` — одна
выдача, другое чтение. Здесь это закреплено красным.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_wellness_today import _init_data_header
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

HUMAN = "92231"


@pytest.fixture(autouse=True)
def _env(settings):
    from apps.miniapp_api.tests.test_wellness_today import BOT_TOKEN

    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.NUTRITION_ENABLED = True
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture
def miniapp_tenant(settings) -> Tenant:
    tenant = Tenant.objects.create(slug="formula-2230b", name="Формула 2230b")
    settings.MAX_BOT_TENANT_SLUG = "formula-2230b"
    return tenant


def _grant_in_chat() -> BotUser:
    """Тап «Да, продолжим» (origin=miniapp) в чате глобального бота — боевым путём."""
    from apps.channels.max.global_onboarding import run_onboarding_turn
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services.resolver import resolve_or_create_global_bot_user

    chat_row = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=HUMAN, chat_id="8888"
    )
    conversation = resolve_active_global_conversation(chat_row)
    run_onboarding_turn(conversation, chat_row, "cb:welcome:consent_yes_miniapp")
    return chat_row


def _home(client: Client) -> dict:
    response = client.get(
        reverse("miniapp_api:customer_wellness_today"),
        HTTP_AUTHORIZATION=_init_data_header(HUMAN),
    )
    assert response.status_code == 200, response.content
    return response.json()


def test_consent_given_in_chat_clears_the_home_block(client, miniapp_tenant) -> None:
    # До согласия блок на месте — иначе «после согласия блока нет» пусто.
    assert _home(client).get("consent_required") is True

    chat_row = _grant_in_chat()

    # Положительная пара: чат согласие действительно записал — на свою строку.
    assert ConsentRecord.all_tenants.filter(
        bot_user=chat_row,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        withdrawn_at__isnull=True,
    ).exists()

    # Это тот же человек, но ДРУГАЯ строка: Mini App резолвит свою.
    miniapp_row = BotUser.all_tenants.get(tenant=miniapp_tenant, channel="max", channel_user_id=HUMAN)
    assert miniapp_row.pk != chat_row.pk

    # Главная после согласия в чате не должна требовать согласия снова.
    assert "consent_required" not in _home(client), "блок висит после согласия в чате"
