"""Миграция состояния под DRF-1506 — сначала состояние, потом ужесточение.

Две вещи, и обе обязаны случиться ДО того, как код начнёт спрашивать
про новые столбцы.

1. ``accepted_at`` для тех, кто уже приземлился
----------------------------------------------
``LANDED`` требует ``accepted_at IS NOT NULL``, а ``save()`` штампует
его только на новых приземлениях. Без этой засыпки каждая мастер,
принявшая приглашение до выката, перестала бы быть мастером для
``resolve_role`` и получила бы 403 на каждой ручке мастер-приложения —
ровно тот инцидент (DRF-1080), который эта задача закрывает, только
шире.

Источник даты, в порядке убывания честности: ``invited_at`` (его ставит
путь приглашения — приняли не раньше, чем пригласили), иначе
``synced_at`` (последнее касание строки платформой). Второе — заведомо
поздняя оценка, и это лучше, чем NULL: столбец отвечает на «когда», а
предикат — только на «есть ли».

2. Замер бронируемых до и после
-------------------------------
``bookable()`` получает ``archived_at IS NULL``, которого у него не
было. Молча уронить число бронируемых мастеров на пилоте нельзя, и
проверить это на глаз тоже нельзя — поэтому миграция считает сама и
пишет обе цифры в лог деплоя. Строки, которые ужесточение снимает с
продажи, — это ``archived_at IS NOT NULL`` при ``is_active`` и
ACCEPTED: заархивированная мастер, которая всё это время продавалась.
Деактивация пишет ``is_active=False`` вместе с ``archived_at``
(``master_deactivation``), так что таких строк не ожидается ни одной —
но «не ожидается» и «нет» это разные утверждения, и лог различает их
поимённо.

Ничего не удаляет и не переводит: если такая строка найдётся, она
именно та, которую и надо было снять с продажи. Миграция называет её,
а не чинит молча.
"""

from __future__ import annotations

import logging

from django.db import migrations
from django.db.models import F, Q

logger = logging.getLogger(__name__)

ACCEPTED = "accepted"


def _measure_and_backfill(apps, schema_editor):
    CatalogMaster = apps.get_model("catalog", "CatalogMaster")

    old_bookable = Q(is_active=True, invite_status=ACCEPTED)
    new_bookable = old_bookable & Q(archived_at__isnull=True)

    before = CatalogMaster.objects.filter(old_bookable).count()
    after = CatalogMaster.objects.filter(new_bookable).count()

    logger.info(
        "catalog.0015.bookable_measure before=%s after=%s dropped=%s",
        before,
        after,
        before - after,
    )
    if before != after:
        for row in CatalogMaster.objects.filter(old_bookable & Q(archived_at__isnull=False)).values(
            "id", "tenant_id", "name", "archived_at"
        ):
            logger.warning(
                "catalog.0015.archived_master_was_on_sale id=%s tenant=%s name=%s archived_at=%s",
                row["id"],
                row["tenant_id"],
                row["name"],
                row["archived_at"],
            )

    landed = Q(
        linked_bot_user__isnull=False,
        invite_status=ACCEPTED,
        accepted_at__isnull=True,
    )
    with_invited = CatalogMaster.objects.filter(landed, invited_at__isnull=False).update(
        accepted_at=F("invited_at")
    )
    without_invited = CatalogMaster.objects.filter(landed).update(accepted_at=F("synced_at"))
    logger.info(
        "catalog.0015.accepted_at_backfill from_invited_at=%s from_synced_at=%s",
        with_invited,
        without_invited,
    )


def _noop(apps, schema_editor):
    """Откат ничего не стирает.

    ``accepted_at`` после отката остаётся заполненным, и это правильно:
    столбец фиксирует случившееся событие. Обнулять его на откате —
    значит терять дату, которую восстановить уже неоткуда.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0014_master_accepted_at"),
    ]

    operations = [
        migrations.RunPython(_measure_and_backfill, _noop),
    ]
