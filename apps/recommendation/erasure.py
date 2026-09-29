"""Стирание слов человека из записей Recommendation — шаг каскада C5 (DRF-1772).

Запись хранит WHAT, причины и факты, из которых причины собраны, — то есть
сказанное человеком в этом пути (D6: ПДн). По D7 транзакционная часть —
что показано, когда, какая реакция — остаётся tombstone-строкой для
attribution/audit (B13), а слова уходят: ``what``/``subline``/``why``/
``facts``/``alternatives`` обнуляются. Другие подходы — формулировки
каталога, не ПДн, но они часть показанного кадра и уходят вместе с ним:
оставить их значило бы хранить половину карточки без человека, которому
её показали. Идемпотентно: повторный вызов ничего не находит.

DRF-2308 — и ключ цели. ``goal_id`` стирает «забудь всё» (#1980), и удаление
аккаунта обязано стирать не меньше. ``fingerprint`` держит ту же цель: у
карточки «нет рекомендации» — ``absence:<goal_id>`` открытым текстом, у
направления — несолёный sha256 от цели, WHAT и причин, который перебором
малого словаря восстанавливается (класс DRF-2242). Отпечаток уникален вместе с
человеком, поэтому он стирается в ``erased:<id>`` — уникально и без
содержания.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction

from apps.recommendation.models import Recommendation

#: Префикс стёртого отпечатка. Уникальность держит id строки.
ERASED_FINGERPRINT_PREFIX = "erased:"


def anonymize_recommendations(bot_user_ids: Iterable[object]) -> int:
    """Обнулить слова и ключ цели во всех записях оболочек; вернуть число строк."""
    rows = Recommendation.objects.filter(bot_user_id__in=list(bot_user_ids))
    with transaction.atomic():
        updated = rows.update(what="", subline="", why=[], facts={}, alternatives=[], goal_id="")
        # Построчно, а не `Concat(Cast(id))`: текстовый вид UUID у SQLite и
        # Postgres разный, а отпечаток должен быть одинаковым на обеих. Карточек
        # у человека единицы.
        pending = list(
            rows.exclude(fingerprint__startswith=ERASED_FINGERPRINT_PREFIX).values_list(
                "pk", flat=True
            )
        )
        for pk in pending:
            Recommendation.objects.filter(pk=pk).update(
                fingerprint=f"{ERASED_FINGERPRINT_PREFIX}{pk}"
            )
    return updated


__all__ = ["ERASED_FINGERPRINT_PREFIX", "anonymize_recommendations"]
