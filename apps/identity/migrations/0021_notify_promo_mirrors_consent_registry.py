"""Свести ``UserPreferences.notify_promo`` к реестру согласий (DRF-1520).

С этой миграции колонка — зеркало ``ConsentRecord(MARKETING)``, а не
самостоятельная запись (см. ``apps.consent.customer``). Строки, накопленные
до неё, зеркалом не являются: колонку писал ``PATCH /me`` напрямую, а в
реестр не писал никто, поэтому у любой строки с ``notify_promo=True`` нет и
не может быть подтверждающей записи о согласии.

### Почему приводим колонку к реестру, а не наоборот

Обратное направление — дописать в реестр ``granted=True`` для каждой такой
строки — означало бы, что систематическая запись проставляет юридический
факт «этот человек дал согласие на маркетинг» с выдуманным ``captured_at``
(настоящий момент нажатия нигде не сохранён) и источником, которого не было.
Это ровно то, что запрещено: согласие выдаёт человек своим действием, а не
миграция за него.

Обратная сторона честного направления названа прямо: у того, кто включил
промо-уведомления тумблером до DRF-1520, они выключатся. Восстанавливается
одним нажатием, и на момент миграции ни одна рассылка эту колонку не читает
— в платформе нет отправителя маркетинга: ``notify_promo`` до сих пор
встречается только в профиле, экспорте и покрытии полей. То есть цена
приведения — состояние тумблера на экране, а не пропущенные сообщения.

Обратная миграция — no-op: восстанавливать «согласие», которого мы не
можем доказать, нельзя ни в одну сторону.
"""

from __future__ import annotations

from django.db import migrations


def align_notify_promo_to_registry(apps, schema_editor):
    """Свести колонку к реестру в обе стороны.

    ``apps.get_model`` отдаёт историческую модель с обычным ``Manager``
    (``use_in_migrations`` в репозитории не выставлен нигде), поэтому
    ``objects`` здесь НЕ ограничен тенантом и миграция видит все строки.
    Это важно настолько, что закреплено тестом: tenant-scoped менеджер
    молча привёл бы к реестру одну площадку и отрапортовал успех.
    """
    UserPreferences = apps.get_model("identity", "UserPreferences")
    ConsentRecord = apps.get_model("consent", "ConsentRecord")

    proven = set(
        ConsentRecord.objects.filter(
            consent_type="marketing",
            granted=True,
            withdrawn_at__isnull=True,
        ).values_list("bot_user_id", flat=True)
    )
    # Нет доказанного согласия → тумблер гасится (см. докстринг модуля).
    UserPreferences.objects.filter(notify_promo=True).exclude(bot_user_id__in=proven).update(
        notify_promo=False
    )
    # Есть доказанное согласие → тумблер включается. Симметричная половина:
    # зеркало обязано совпасть с реестром в обе стороны, иначе «главный
    # источник» остаётся утверждением, а не свойством данных. На момент
    # миграции таких строк нет (в MARKETING не писал никто), но правило не
    # должно держаться на этом факте.
    UserPreferences.objects.filter(notify_promo=False, bot_user_id__in=proven).update(
        notify_promo=True
    )


def noop_reverse(apps, schema_editor):
    """Назад не восстанавливаем: недоказуемое согласие не воскрешают."""


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0020_drop_userpreferences_allergies"),
        ("consent", "0003_backfill_memory_green_consent"),
    ]

    operations = [
        migrations.RunPython(align_notify_promo_to_registry, noop_reverse),
    ]
