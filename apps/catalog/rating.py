"""Оценка мастера, которую видит клиент (DRF-2875)."""

from __future__ import annotations

from decimal import Decimal


def public_rating(rating: Decimal | None, review_count: int | None) -> Decimal | None:
    """Оценка, которую можно показать клиенту, — или ``None``.

    Решение владельца 07.10 (лист решений, п.20; DRF-2875): нет отзывов —
    «Пока нет отзывов», без звезды и числа; оценка без подтверждённого числа
    отзывов за собственный рейтинг Ayla не выдаётся. На пилоте у мастеров
    стояла импортированная оценка 4.4–4.9 при нуле отзывов.

    ``0.00`` — отсутствие оценки, не низкая оценка (DRF-1224): область 1..5.
    Это правило ПОКАЗА; порядок выдачи оно не трогает.
    """
    if rating is None or rating < 1 or not review_count or review_count < 1:
        return None
    return rating


#: Слова владельца (лист решений 07.10, п.20) — у мастера без отзывов.
NO_REVIEWS_LABEL = "Пока нет отзывов"


def review_count_label(count: int) -> str:
    """«1 отзыв» / «3 отзыва» / «12 отзывов» — как ``reviewCountLabel`` в Mini App."""
    mod10, mod100 = count % 10, count % 100
    if mod10 == 1 and mod100 != 11:
        word = "отзыв"
    elif 2 <= mod10 <= 4 and not 10 <= mod100 < 20:
        word = "отзыва"
    else:
        word = "отзывов"
    return f"{count} {word}"


def rating_label(rating: Decimal | None, review_count: int | None) -> str:
    """«★ 4.9 (12 отзывов)» при отзывах, иначе «Пока нет отзывов»."""
    shown = public_rating(rating, review_count)
    if shown is None:
        return NO_REVIEWS_LABEL
    return f"★ {shown} ({review_count_label(int(review_count or 0))})"
