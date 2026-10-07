"""Согласие красной зоны для узлов читателя (DRF-2132).

``RedZoneReader.read`` отдаёт строку ради использования только при действующем
согласии ``memory_red``. Узлы, чей предмет — журнал, GUC или область салона,
ставят его этим помощником, чтобы краснеть на своём предмете, а не на согласии.
"""

from __future__ import annotations

import uuid

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


def grant_red_zone_consent(user_id: uuid.UUID) -> BotUser:
    tenant, _ = Tenant.objects.get_or_create(
        slug="red-zone-consent", defaults={"name": "Red zone consent"}
    )
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"red-{user_id.hex[:12]}",
        ayla_user_id=user_id,
        customer_status=BotUser.CustomerStatus.LINKED,
    )
    ConsentRecord.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        consent_type=ConsentRecord.ConsentType.MEMORY_RED,
        granted=True,
        source="test",
    )
    return bot_user
