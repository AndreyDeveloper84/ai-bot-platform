"""Стирание слов человека из записей Recommendation — шаг каскада C5 (DRF-1772).

Запись хранит WHAT, причины и факты, из которых причины собраны, — то есть
сказанное человеком в этом пути (D6: ПДн). По D7 транзакционная часть —
что показано, когда, какая реакция — остаётся tombstone-строкой для
attribution/audit (B13), а слова уходят: ``what``/``subline``/``why``/
``facts`` обнуляются. Идемпотентно: повторный вызов ничего не находит.
"""

from __future__ import annotations

from collections.abc import Iterable

from apps.recommendation.models import Recommendation


def anonymize_recommendations(bot_user_ids: Iterable[object]) -> int:
    """Обнулить слова во всех записях оболочек ``bot_user_ids``; вернуть число строк."""
    return Recommendation.objects.filter(bot_user_id__in=list(bot_user_ids)).update(
        what="", subline="", why=[], facts={}
    )


__all__ = ["anonymize_recommendations"]
