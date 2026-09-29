"""Ключ склейки мастера: (tenant, ayla_user_id).

DRF-1507, пункт 1. До этой миграции единственным ограничением на
``CatalogMaster`` было ``unique_together (tenant, external_id)``, которое не
покрывает ни ``ayla_user_id``, ни ``max_handle``. Приглашение и синхронизация
поэтому давали на одного человека две строки, а ``resolve_master``
(``apps/booking/master_notify.py``) ищет по ``id`` ИЛИ ``ayla_user_id`` и
инвайт-строку с ``ayla_user_id=NULL`` не находил: мастер не получал ни одного
уведомления о записи.

Ограничение частичное. Пустые значения между собой конфликтовать не должны:
``ayla_user_id`` NULL у каждой инвайт-строки, и наивная уникальность
запретила бы второго приглашённого мастера в салоне.

``(tenant, max_handle)`` из объёма задачи здесь НЕТ: его единственный
писатель — ``apps/admin_api/views_invite.py`` — на протухшем приглашении
намеренно заводит вторую строку с тем же handle, и без правки того файла
(занят DRF-1505) ограничение превратило бы повторное приглашение в 500.
Проверка ниже всё равно называет дубли по обоим ключам: знать про них надо
до того, как ограничение появится.

Слияние уже существующих дублей эта миграция НЕ делает — это отдельный шаг с
обоснованием и замером (граница задачи). Вместо этого она сначала называет их
поимённо и останавливается: ограничение, упавшее с сырым
``duplicate key value violates unique constraint``, не говорит, какие строки
чинить, а выкладка (DRF-1516) и без того не накатывает миграции сама.
"""

import logging

from django.db import migrations, models

logger = logging.getLogger(__name__)


def _forbid_existing_duplicates(apps, schema_editor):
    """Назвать дубли до того, как ограничение упадёт молча."""

    from django.db.models import Count

    master = apps.get_model("catalog", "CatalogMaster")
    problems: list[str] = []

    dup_users = (
        master.objects.filter(ayla_user_id__isnull=False)
        .values("tenant_id", "ayla_user_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by()
    )
    for row in dup_users:
        ids = list(
            master.objects.filter(
                tenant_id=row["tenant_id"],
                ayla_user_id=row["ayla_user_id"],
            ).values_list("id", flat=True)
        )
        problems.append(
            f"tenant={row['tenant_id']} ayla_user_id={row['ayla_user_id']} "
            f"rows={[str(i) for i in ids]}"
        )

    # ``max_handle`` только называется, но не блокирует: ограничения на него
    # эта миграция не ставит (см. модуль-докстринг), а падать из-за дублей по
    # ключу, которого нет, значит не пускать выкладку ради чужой задачи.
    dup_handles = (
        master.objects.exclude(max_handle="")
        .values("tenant_id", "max_handle")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by()
    )
    for row in dup_handles:
        logger.warning(
            "DRF-1507: tenant=%s max_handle=%r — на этот аккаунт MAX в салоне "
            "приходится %d строк мастера. Ключ (tenant, max_handle) ждёт "
            "правки apps/admin_api/views_invite.py (DRF-1505) и здесь не "
            "ставится.",
            row["tenant_id"],
            row["max_handle"],
            row["n"],
        )

    if problems:
        raise RuntimeError(
            "DRF-1507: на этих данных уже есть по две строки мастера на одного "
            "человека, и ключ склейки на них не встанет. Слияние дублей — "
            "отдельный обоснованный шаг, миграция его не делает. Починить и "
            "повторить:\n  " + "\n  ".join(problems)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0015_backfill_master_accepted_at"),
        ("identity", "0020_drop_userpreferences_allergies"),
        ("tenancy", "0013_tenant_last_catalog_sync_ok_at"),
    ]

    operations = [
        migrations.RunPython(
            _forbid_existing_duplicates,
            migrations.RunPython.noop,
            elidable=False,
        ),
        migrations.AddConstraint(
            model_name="catalogmaster",
            constraint=models.UniqueConstraint(
                condition=models.Q(("ayla_user_id__isnull", False)),
                fields=("tenant", "ayla_user_id"),
                name="uq_catalog_master_tenant_ayla_user_id",
            ),
        ),
    ]
