"""``CatalogMaster.catalog_specialist_id`` — каким id строку знает каталог (DRF-1933).

Колонка, заполнение и частичная уникальность — в этом порядке: сначала поле,
потом данные, потом ограничение. Ограничение после данных: если на живой базе
окажутся две строки салона с одним id каталога, миграция не падает на
выкладке, а оставляет вторую пустой и пишет её в лог (пустая колонка — отказ
по имени, а не тихий дубль).

Откуда значение (одно представление факта, решение главного окна 15.09):

1. ``raw["id"]`` — ответ синхронизации. У строки синка он равен первичному
   ключу; у строки приглашения, склеенной DRF-1507, — канонический id, а
   первичный ключ остаётся ``uuid4``.
2. ``SoloIdentityLink.catalog_specialist_id`` — readback провижининга
   соло-кабинета, если синк строку ещё не видел.

Остальные строки остаются пустыми. **Ожидание на пилоте** (замер главного
окна, ruvds-o1mqo, 15.09 07:03 UTC, 34 строки): заполнено 31 (``raw["id"]``
== pk), пусто 3 (formula-tela, приглашения без ответа синка), из
``SoloIdentityLink`` — 0. Главное окно сверяет после выкладки.

Обратная миграция снимает колонку целиком; данные восстановимы из тех же
источников.
"""

import logging
import uuid

from django.db import migrations, models

logger = logging.getLogger(__name__)


def _as_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def backfill_catalog_specialist_id(apps, schema_editor):
    CatalogMaster = apps.get_model("catalog", "CatalogMaster")
    SoloIdentityLink = apps.get_model("identity", "SoloIdentityLink")

    taken: set[tuple] = set()
    plan: dict = {}
    from_raw = from_link = skipped_duplicate = 0

    rows = CatalogMaster.objects.all().only("id", "tenant_id", "raw").order_by("id")
    for row in rows.iterator():
        value = _as_uuid((row.raw or {}).get("id")) if isinstance(row.raw, dict) else None
        if value is None:
            continue
        key = (row.tenant_id, value)
        if key in taken:
            skipped_duplicate += 1
            logger.warning(
                "catalog.0022.duplicate_catalog_id tenant=%s master=%s catalog_id=%s — оставлено пустым",
                row.tenant_id,
                row.id,
                value,
            )
            continue
        taken.add(key)
        plan[row.id] = value
        from_raw += 1

    links = SoloIdentityLink.objects.filter(catalog_specialist_id__isnull=False).select_related(
        "master"
    )
    for link in links.iterator():
        master = link.master
        if master.id in plan:
            continue
        key = (master.tenant_id, link.catalog_specialist_id)
        if key in taken:
            skipped_duplicate += 1
            continue
        taken.add(key)
        plan[master.id] = link.catalog_specialist_id
        from_link += 1

    for master_id, value in plan.items():
        CatalogMaster.objects.filter(id=master_id).update(catalog_specialist_id=value)

    total = CatalogMaster.objects.count()
    logger.info(
        "catalog.0022.backfill rows=%s from_raw=%s from_solo_link=%s empty=%s duplicate_skipped=%s",
        total,
        from_raw,
        from_link,
        total - from_raw - from_link,
        skipped_duplicate,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0021_schedule_confirmation"),
        ("identity", "0029_drf1830_solo_catalog_provisioning_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogmaster",
            name="catalog_specialist_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                null=True,
                help_text="SpecialistProfile.id в каталоге. Пусто — строку каталог не знает (DRF-1933).",
                verbose_name="Id профиля в каталоге",
            ),
        ),
        migrations.RunPython(backfill_catalog_specialist_id, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="catalogmaster",
            constraint=models.UniqueConstraint(
                condition=models.Q(catalog_specialist_id__isnull=False),
                fields=("tenant", "catalog_specialist_id"),
                name="uq_catalog_master_tenant_catalog_specialist_id",
            ),
        ),
    ]
