"""Бэкфилл ``personal_data`` для салонных согласий, выданных до DRF-2016.

Салонное приветствие (S2 «Да, продолжим» при тенанте в скоупе) ставило
``BotUser.consent_at`` и не писало ``ConsentRecord``. Читатели — дневник,
сканер, вода — спрашивают строку реестра у ``BotUser``, столбец не читают;
после #1839 / #1843 реестр — единственное основание. С этого листа салонный
путь пишет строку сам (``record_person_consent``); эта миграция доводит до
той же формы людей, которые уже согласились и второй раз не тапнут.

Правила — по образцу 0003 (DRF-1311) и 0005 (DRF-1963):

* кандидат — ``consent_at IS NOT NULL`` и **ни одной** строки
  ``personal_data`` у этой оболочки — ни активной, ни отозванной. Отзыв
  ``consent_at`` не чистит, и воскрешать согласие миграцией нельзя;
  активная строка (глобальный путь) — оставить как есть;
* ``captured_at = consent_at`` — когда согласились, а не когда выложили;
* ``document_version = ""`` — легаси-конвенция модели: какую версию текста
  видел старый тап, код доказать не может, и утверждать «welcome-s2-v1»
  задним числом было бы утверждением без источника;
* ``source`` помечает происхождение — строка выведена из столбца, а не
  тапнута заново;
* идемпотентна; строка на оболочку, без разворачивания на person-level
  (у сестринских оболочек — свой ``consent_at`` и своя проверка).

Счётчик печатается в stdout: главное окно снимает число кандидатов на
стенде до выкладки SQL-ом (``consent_at IS NOT NULL`` и нет строк) и сверяет
с ``created`` после. Обратный ход снимает только строки этого источника —
журнал согласий миграцией назад не чистится, кроме того, что она сама
минтила.
"""

from __future__ import annotations

from django.db import migrations

PERSONAL_DATA = "personal_data"
BACKFILL_SOURCE = "backfill:drf2016:welcome_s2_tenant"


def backfill_personal_data(apps, schema_editor) -> None:
    """RunPython-обёртка; счётчик печатается внутри :func:`run_backfill`."""
    run_backfill(apps)


def run_backfill(apps) -> int:
    BotUser = apps.get_model("identity", "BotUser")
    ConsentRecord = apps.get_model("consent", "ConsentRecord")

    with_any_row = set(
        ConsentRecord.objects.filter(consent_type=PERSONAL_DATA).values_list(
            "bot_user_id", flat=True
        )
    )
    candidates = (
        BotUser.objects.filter(consent_at__isnull=False)
        .exclude(id__in=with_any_row)
        .values_list("id", "tenant_id", "consent_at")
    )
    created = 0
    total = 0
    for bot_user_id, tenant_id, consented_at in candidates.iterator():
        total += 1
        record = ConsentRecord.objects.create(
            tenant_id=tenant_id,
            bot_user_id=bot_user_id,
            consent_type=PERSONAL_DATA,
            granted=True,
            source=BACKFILL_SOURCE,
            document_version="",
        )
        # ``captured_at`` — auto_now_add; дата выдачи ставится вторым шагом.
        ConsentRecord.objects.filter(pk=record.pk).update(captured_at=consented_at)
        created += 1
    print(f"drf2016 backfill: candidates={total} created={created}")
    return created


def unbackfill_personal_data(apps, schema_editor) -> None:
    """Reverse: снять только строки, которые эта миграция минтила (по source)."""
    ConsentRecord = apps.get_model("consent", "ConsentRecord")
    ConsentRecord.objects.filter(consent_type=PERSONAL_DATA, source=BACKFILL_SOURCE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("consent", "0005_food_diary_processing"),
        ("identity", "0030_ayla_erasure_job"),
    ]

    operations = [
        migrations.RunPython(backfill_personal_data, unbackfill_personal_data),
    ]
