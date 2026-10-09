"""DRF-2875 — оценка мастера показывается клиенту только вместе с отзывами.

Решение владельца 07.10 (лист решений, п.20), дословно:

    Нет отзывов — показывать «Пока нет отзывов», без звезды и числового
    рейтинга. Есть отзывы, на которых рассчитан рейтинг, — показывать оценку
    и количество. Если оценка импортирована, но её источник и число отзывов
    не подтверждены, не выдавать её за собственный рейтинг Ayla.
    Это решение только об отображении.

На пилоте у мастеров стояла импортированная оценка 4.4–4.9 при нуле отзывов,
и клиент видел «★ 4.9». Правило живёт в одном месте — ``apps.catalog.rating``;
здесь оно и три поверхности, которые через него проходят: карточка чата,
публичная ручка маркетплейса и сериализатор мастера для Mini App.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster
from apps.catalog.rating import (
    NO_REVIEWS_LABEL,
    public_rating,
    rating_label,
    review_count_label,
)
from apps.marketplace.dto import MasterCard
from apps.marketplace.views import _card_to_dict
from apps.miniapp_api.views import _master_to_dict

IMPORTED = Decimal("4.90")


class TestTheRule:
    def test_owner_words_verbatim(self) -> None:
        assert NO_REVIEWS_LABEL == "Пока нет отзывов"

    @pytest.mark.parametrize("review_count", [0, None])
    def test_a_rating_without_reviews_is_not_a_rating(self, review_count: int | None) -> None:
        assert public_rating(IMPORTED, review_count) is None

    def test_one_review_is_enough(self) -> None:
        assert public_rating(IMPORTED, 1) == IMPORTED

    @pytest.mark.parametrize("rating", [None, Decimal("0.00"), Decimal("0.99")])
    def test_reviews_do_not_invent_a_rating(self, rating: Decimal | None) -> None:
        """DRF-1224 — 0.00 это отсутствие оценки; отзывы его не превращают в число."""
        assert public_rating(rating, 12) is None
        # Положительная пара: с настоящей оценкой те же 12 отзывов её показывают.
        assert public_rating(IMPORTED, 12) == IMPORTED


class TestTheLabel:
    def test_with_reviews_rating_and_count(self) -> None:
        assert rating_label(IMPORTED, 12) == "★ 4.90 (12 отзывов)"

    def test_without_reviews_owner_words_and_no_number(self) -> None:
        label = rating_label(IMPORTED, 0)
        assert label == NO_REVIEWS_LABEL
        assert "★" not in label
        assert "4.9" not in label

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (1, "1 отзыв"),
            (2, "2 отзыва"),
            (4, "4 отзыва"),
            (5, "5 отзывов"),
            (11, "11 отзывов"),
            (12, "12 отзывов"),
            (14, "14 отзывов"),
            (21, "21 отзыв"),
            (22, "22 отзыва"),
            (108, "108 отзывов"),
            (111, "111 отзывов"),
        ],
    )
    def test_russian_plural(self, count: int, expected: str) -> None:
        assert review_count_label(count) == expected


def _card(rating: Decimal | None, review_count: int) -> MasterCard:
    return MasterCard(
        tenant_id=uuid4(),
        master_id=uuid4(),
        name="Борис",
        specialization="",
        rating=public_rating(rating, review_count),
        photo_url="",
        city="Пенза",
        review_count=review_count,
    )


class TestThePublicMarketplaceAnswer:
    def test_imported_rating_without_reviews_does_not_leave(self) -> None:
        body = _card_to_dict(_card(IMPORTED, 0))
        assert body["rating"] is None
        assert body["review_count"] == 0

    def test_rating_leaves_together_with_its_count(self) -> None:
        body = _card_to_dict(_card(IMPORTED, 12))
        assert (body["rating"], body["review_count"]) == ("4.90", 12)


def _mirror_row(rating: Decimal | None, review_count: int) -> CatalogMaster:
    # Несохранённая строка: сериализатор читает только поля.
    return CatalogMaster(
        id=uuid4(), name="Борис", rating=rating, review_count=review_count, photo_url=""
    )


class TestTheMiniAppMasterAnswer:
    def test_imported_rating_without_reviews_does_not_leave(self) -> None:
        body = _master_to_dict(_mirror_row(IMPORTED, 0))
        assert body["rating"] is None
        assert body["review_count"] == 0

    def test_rating_leaves_together_with_its_count(self) -> None:
        body = _master_to_dict(_mirror_row(IMPORTED, 12))
        assert (body["rating"], body["review_count"]) == ("4.90", 12)
