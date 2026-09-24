"""Читатель происхождения порции и карточки чата (DRF-2371).

Словарь провода (DRF-2402): ``provider`` | ``typical`` | ``unknown``.
Узлы держат ещё два случая, которых в словаре нет и которые опаснее
всего: **поля нет** и **значение незнакомо**. Оба обязаны читаться как
«не названо», иначе завтра добавленное четвёртое значение уедет на экран
как подтверждённое — молча.

Отдельно проверены обе карточки чата: скана и оценки текстом. Число в
них называется только тогда, когда вес кто-то назвал; посчитанное по
константе каталога существует, но названным не является.
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla.portion_provenance import (
    PortionProvenance,
    portion_needs_confirmation,
    portion_numbers_are_named,
    portion_provenance_of,
)


class TestEveryWireValue:
    @pytest.mark.parametrize(
        "wire,expected",
        [
            ("provider", PortionProvenance.NAMED),
            ("typical", PortionProvenance.TYPICAL),
            ("unknown", PortionProvenance.UNNAMED),
        ],
    )
    def test_the_dictionary_is_read_as_declared(self, wire, expected) -> None:
        assert portion_provenance_of(wire) is expected

    def test_a_missing_field_is_absent_and_not_named(self) -> None:
        assert portion_provenance_of(None) is PortionProvenance.ABSENT

    @pytest.mark.parametrize("wire", ["confirmed", "", 42, {"portion_source": "provider"}])
    def test_an_unknown_value_is_read_carefully(self, wire) -> None:
        # Ровно тот случай, ради которого узел существует: в словарь
        # добавили значение, а этот код старый.
        assert portion_provenance_of(wire) is PortionProvenance.UNNAMED


class TestWhatTheProvenanceAllows:
    def test_only_a_named_weight_lets_us_name_the_number(self) -> None:
        assert portion_numbers_are_named(PortionProvenance.NAMED) is True
        # Переходный случай: до DRF-2444 числа существуют только при
        # названном весе, старый ответ показывается как прежде.
        assert portion_numbers_are_named(PortionProvenance.ABSENT) is True
        assert portion_numbers_are_named(PortionProvenance.TYPICAL) is False
        assert portion_numbers_are_named(PortionProvenance.UNNAMED) is False

    def test_confirmation_is_asked_where_nobody_named_the_weight(self) -> None:
        assert portion_needs_confirmation(PortionProvenance.TYPICAL) is True
        assert portion_needs_confirmation(PortionProvenance.UNNAMED) is True
        assert portion_needs_confirmation(PortionProvenance.NAMED) is False
        assert portion_needs_confirmation(PortionProvenance.ABSENT) is False

    def test_every_value_is_decided(self) -> None:
        # Сторож полноты: пятое значение не пройдёт молча.
        undecided = [
            p
            for p in PortionProvenance
            if not portion_numbers_are_named(p) and not portion_needs_confirmation(p)
        ]
        assert undecided == []


class TestTheScanCardInChat:
    def _card(self, *, portion_source):
        from apps.integrations.ayla.nutrition_client import ScanResponse
        from apps.skills.food_scanner.skill import _format_scan_card

        # Каталог кладёт признак ВНУТРЬ ``nutrition``
        # (``FoodScanResponseSerializer``), а не на верхний уровень.
        # Первая версия этого стенда клала его в ``raw`` — узлы были
        # зелёными, а по проводу признак до карточки не доезжал.
        nutrition = {"calories": 147, "protein_g": 5, "fat_g": 7, "carbs_g": 20}
        if portion_source is not None:
            nutrition["portion_source"] = portion_source
        return _format_scan_card(
            ScanResponse(
                scan_id="scan-1",
                dish_name="Борщ",
                confidence=0.9,
                portion_g=300,
                nutrition=nutrition,
                provider="test",
                raw={"nutrition": nutrition},
            )
        )

    def test_a_named_weight_still_names_the_number(self) -> None:
        card = self._card(portion_source="provider")

        assert "Борщ" in card
        assert "147 ккал" in card

    def test_an_old_answer_without_the_field_is_unchanged(self) -> None:
        assert "147 ккал" in self._card(portion_source=None)

    @pytest.mark.parametrize("wire", ["unknown", "typical", "confirmed"])
    def test_a_weight_nobody_named_does_not_name_the_number(self, wire) -> None:
        card = self._card(portion_source=wire)

        # Блюдо и вопрос остаются — молчит только число.
        assert "Борщ" in card
        assert "Записать в дневник?" in card
        assert "ккал" not in card


class TestTheEstimateCardInChat:
    def _card(self, *, portion_source):
        from apps.integrations.ayla.nutrition_client import DishEstimate
        from apps.skills.food_clarify.text_entry import render_estimate_card

        raw = {} if portion_source is None else {"portion_source": portion_source}
        return render_estimate_card(
            DishEstimate(
                matched_dish="борщ",
                portion_g=300.0,
                portion_estimated=False,
                kcal=147.0,
                protein_g=5.0,
                fat_g=7.0,
                carbs_g=20.0,
                raw=raw,
            )
        )

    def test_a_named_weight_still_names_the_number(self) -> None:
        assert "147 ккал" in self._card(portion_source="provider")

    @pytest.mark.parametrize("wire", ["unknown", "typical", "confirmed"])
    def test_a_weight_nobody_named_does_not_name_the_number(self, wire) -> None:
        card = self._card(portion_source=wire)

        assert "борщ" in card
        assert "Записать в дневник?" in card
        assert "ккал" not in card
