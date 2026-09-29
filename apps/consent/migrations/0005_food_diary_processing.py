"""M1 (DRF-1963): согласие дневника/сканера — одна строка реестра вместо колонки.

Три шага, и порядок значим:

1. ``nutrition_diary`` → ``food_diary_processing`` в choices (решение D1: тот же
   тип, переименован, второго рядом нет).
2. Строки старого значения — тем же именем. На пилоте их 0 (readback 15.09
   16:28 UTC): писателя у типа не было, admin строк не создаёт. Шаг есть,
   чтобы миграция была верна на любой базе, а не только на сегодняшней.
3. ``BotUser.food_scanner_consent_at`` → действующая строка
   ``food_diary_processing``. На пилоте непустых колонок 0; путь переноса
   обязателен всё равно. Момент выдачи сохраняется: ``captured_at`` = значению
   колонки, а не времени миграции — иначе журнал солгал бы о дате согласия.
   Версия — ``food-diary-v0``: человек видел текущий текст экрана (D3).

Колонка не трогается: её удаляет вторая половина листа после выкладки и
readback (D4). Обратный ход возвращает имя типа; перенесённые строки
остаются — удалять записи журнала согласий миграцией назад нельзя.
"""

from django.db import migrations, models

OLD = "nutrition_diary"
NEW = "food_diary_processing"
MIGRATION_SOURCE = "migration:food_scanner_consent_at"
DOCUMENT_VERSION = "food-diary-v0"


def rename_type_forward(apps, schema_editor):
    ConsentRecord = apps.get_model("consent", "ConsentRecord")
    ConsentRecord.objects.filter(consent_type=OLD).update(consent_type=NEW)


def rename_type_backward(apps, schema_editor):
    ConsentRecord = apps.get_model("consent", "ConsentRecord")
    ConsentRecord.objects.filter(consent_type=NEW).update(consent_type=OLD)


def column_to_registry(apps, schema_editor):
    BotUser = apps.get_model("identity", "BotUser")
    ConsentRecord = apps.get_model("consent", "ConsentRecord")
    stamped = BotUser.objects.filter(food_scanner_consent_at__isnull=False).values_list(
        "id", "tenant_id", "food_scanner_consent_at"
    )
    for bot_user_id, tenant_id, granted_at in stamped.iterator():
        already = ConsentRecord.objects.filter(
            bot_user_id=bot_user_id,
            consent_type=NEW,
            granted=True,
            withdrawn_at__isnull=True,
        ).exists()
        if already:
            continue
        record = ConsentRecord.objects.create(
            tenant_id=tenant_id,
            bot_user_id=bot_user_id,
            consent_type=NEW,
            granted=True,
            source=MIGRATION_SOURCE,
            document_version=DOCUMENT_VERSION,
        )
        # ``captured_at`` — auto_now_add; дата выдачи ставится вторым шагом.
        ConsentRecord.objects.filter(pk=record.pk).update(captured_at=granted_at)


class Migration(migrations.Migration):
    dependencies = [
        ("consent", "0004_nutrition_consent_types"),
        ("identity", "0013_botuser_food_scanner_consent_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="consentrecord",
            name="consent_type",
            field=models.CharField(
                choices=[
                    ("personal_data", "Personal data (152-ФЗ)"),
                    ("marketing", "Marketing"),
                    ("photo_biometric", "Photo / biometric"),
                    ("health", "Health"),
                    ("memory_green", "Memory — green zone"),
                    ("memory_yellow", "Memory — yellow zone"),
                    ("memory_red", "Memory — red zone (special category)"),
                    (
                        "food_diary_processing",
                        "Food diary processing (food, drinks, photos, voice)",
                    ),
                    (
                        "personal_calculation",
                        "Personal calculation (weight, height, age, sex, activity, goal)",
                    ),
                ],
                help_text="Which consent this row is about. See ConsentType for the 4 Phase-0 categories.",
                max_length=32,
            ),
        ),
        migrations.RunPython(rename_type_forward, rename_type_backward),
        migrations.RunPython(column_to_registry, migrations.RunPython.noop),
    ]
