"""DRF-1952 — адрес салона в превью записи в чате (``confirm_booking``).

Адрес — тенанта ЭТОЙ записи (``tenant``, в котором создаётся pending), из
зеркала ``Tenant.address``; пусто — фраза ``visit-address.ts``.
"""

from __future__ import annotations

import pytest

from apps.identity.models import BotUser
from apps.skills.booking.tools import confirm_booking
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _preview(tenant: Tenant) -> str:
    bot_user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="pv-1", chat_id="pv-1", client_name="Anna"
    )
    with tenant_scope(tenant):
        result = confirm_booking(
            client=None,
            arguments={"master_id": 11, "service_id": 22, "slot_datetime": "2026-05-20T14:00:00"},
            tenant=tenant,
            bot_user=bot_user,
            allowed_master_ids={11},
            allowed_service_ids={22},
            master_lookup={11: "Ольга"},
            service_lookup={22: "Массаж"},
        )
    assert "Записываю:" in result.text  # превью действительно построено
    return result.text


def test_preview_names_the_salon_address() -> None:
    tenant = Tenant.objects.create(slug="pv-addr", name="Салон", address="ул. Карпинского, 33А")
    assert "• Адрес: ул. Карпинского, 33А" in _preview(tenant)


def test_preview_without_an_address_says_to_ask_the_salon() -> None:
    tenant = Tenant.objects.create(slug="pv-noaddr", name="Салон")
    assert "• Адрес: Уточните адрес в салоне" in _preview(tenant)
