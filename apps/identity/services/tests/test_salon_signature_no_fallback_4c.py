"""Подпись салонного бота не проваливается в тенант настройки (DRF-1785, срез 4c DRF-1705).

Решение владельца R4 (а), раздел S ``docs/OWNER_QUESTIONS_2026-09-12.md``: тенант — от
человека; нет рабочей роли — не подставлять ``formula-tela``; никакого fallback. Главное окно
15.09 (Q1): initData, подписанный салонным ботом, без рабочей строки → ``None`` — ни шага
«тенант бота подписи / ``MAX_BOT_TENANT_SLUG``», ни шага «последняя строка любого тенанта».
``/api/v1/me`` не меняется: у него свой путь за ``is_staff_surface``.

Замер пилота 15.09 18:35 UTC: ``MAX_BOT_TENANT_SLUG=formula-tela`` — этот fallback там живой.
"""

from __future__ import annotations

import pytest

from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.identity.services.bot_user_resolver import (
    resolve_bot_user,
    resolve_tenant_slug_for_init_data,
)
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "4785001"


class _Verified:
    """Minimal stand-in for VerifiedInitData."""

    def __init__(self, user_id: str, bot_slug: str = "") -> None:
        self.user_id = user_id
        self.bot_slug = bot_slug


def _salon_bot(tenant_slug: str = "") -> BotEntry:
    return BotEntry(
        slug="salon",
        webhook_secret="wh-salon-4c",  # pragma: allowlist secret
        api_token="tok-salon-4c",  # pragma: allowlist secret
        tenant_slug=tenant_slug,
        stream="max_salon",
    )


CLIENT_BOT = BotEntry(
    slug="client",
    webhook_secret="wh-client-4c",  # pragma: allowlist secret
    api_token="tok-client-4c",  # pragma: allowlist secret
    stream="max_global",
)


@pytest.fixture
def salon() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return obj


class TestTheSalonSignatureReadsNoSettingTenant:
    @pytest.mark.parametrize(
        "entry_tenant", ["", "formula-tela"], ids=["variable-removed", "variable-still-set"]
    )
    def test_the_salon_signature_gives_no_tenant_slug(self, settings, entry_tenant):
        """Красный до правки: подпись салонного бота → ``MAX_BOT_TENANT_SLUG`` (или тенант записи)."""
        settings.MAX_BOT_REGISTRY = (CLIENT_BOT, _salon_bot(entry_tenant))
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"

        # Положительная пара: клиентский бот читает настройку, как раньше.
        client_slug = resolve_tenant_slug_for_init_data(_Verified(CHANNEL_USER_ID, "client"))
        assert client_slug == "formula-tela"
        assert resolve_tenant_slug_for_init_data(_Verified(CHANNEL_USER_ID, "salon")) == ""


class TestASalonStrangerResolvesToNobody:
    def test_a_customer_row_in_the_setting_tenant_is_not_picked(self, settings, salon):
        """Красный до правки: подпись салонного бота без рабочей строки → строка formula-tela."""
        settings.MAX_BOT_REGISTRY = (CLIENT_BOT, _salon_bot())
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"
        BotUser.all_tenants.create(tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID)

        assert BotUser.all_tenants.filter(channel_user_id=CHANNEL_USER_ID).count() == 1
        # Положительная пара: клиентская поверхность находит ту же строку, как раньше.
        assert resolve_bot_user(_Verified(CHANNEL_USER_ID, "client")) is not None
        assert resolve_bot_user(_Verified(CHANNEL_USER_ID, "salon")) is None

    def test_a_working_row_is_still_found_from_the_salon_bot(self, settings, salon):
        """Зелёный до и после: рабочая строка — шаг 0, её правка не трогает."""
        settings.MAX_BOT_REGISTRY = (CLIENT_BOT, _salon_bot())
        settings.MAX_BOT_TENANT_SLUG = ""
        row = BotUser.all_tenants.create(
            tenant=salon, channel="max", channel_user_id=CHANNEL_USER_ID
        )
        TenantStaff.all_tenants.create(
            tenant=salon, bot_user=row, role=TenantStaff.Role.ADMIN, created_by=row
        )

        assert resolve_bot_user(_Verified(CHANNEL_USER_ID, "salon")) == row
