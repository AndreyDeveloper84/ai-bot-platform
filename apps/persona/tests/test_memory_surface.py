"""Personal-context surfacing renderer tests (M-C1 / #1101)."""

from __future__ import annotations

from apps.identity.services.memory_reader import GreenFact, PersonalContextView
from apps.persona.memory_surface import render_personal_context


class TestRenderPersonalContext:
    def test_empty_view_returns_none(self):
        assert render_personal_context(PersonalContextView()) is None

    def test_renders_summary(self):
        out = render_personal_context(PersonalContextView(summary="Любит вечерние слоты"))
        assert out is not None
        assert "Любит вечерние слоты" in out
        assert "помню, что ты" in out  # the natural-surfacing instruction

    def test_renders_known_diet_fact(self):
        view = PersonalContextView(
            green_facts=[GreenFact(kind="lifestyle", content={"key": "diet", "value": "vegan"})]
        )
        out = render_personal_context(view)
        assert out is not None
        assert "веганского питания" in out

    def test_unknown_key_falls_back_to_display(self):
        view = PersonalContextView(
            green_facts=[
                GreenFact(kind="preference", content={"key": "mood", "display": "любит тишину"})
            ]
        )
        out = render_personal_context(view)
        assert out is not None
        assert "любит тишину" in out

    def test_unrenderable_fact_is_skipped(self):
        # No known renderer and no display → nothing to surface.
        view = PersonalContextView(
            green_facts=[GreenFact(kind="other", content={"key": "x", "value": "y"})]
        )
        assert render_personal_context(view) is None

    def test_combines_summary_and_facts(self):
        view = PersonalContextView(
            summary="Ищет маникюр",
            green_facts=[
                GreenFact(kind="lifestyle", content={"key": "diet", "value": "vegetarian"})
            ],
        )
        out = render_personal_context(view)
        assert out is not None
        assert "Ищет маникюр" in out
        assert "вегетарианского питания" in out


class TestProvenanceInTheSurfacedParagraph:
    """P0-3 — эта поверхность велит модели говорить «помню, что ты…».
    Выведенный факт, попавший сюда без пометки, становится репликой,
    приписанной человеку (`OD_C04_GROUNDED_WHY.md` §1)."""

    _STATED_LEAD = "Что ты уже знаешь об этом клиенте"
    _DERIVED_LEAD = "Это мы вывели сами, клиент этого НЕ говорил"

    def _view(self, source: str) -> PersonalContextView:
        return PersonalContextView(
            green_facts=[
                GreenFact(
                    kind="lifestyle",
                    content={"key": "diet", "value": "vegan"},
                    source=source,
                )
            ]
        )

    def _render(self, source: str) -> str:
        """Render, asserting a paragraph came back — «нечего показать» is its
        own outcome and would silently pass every `in` check below."""
        out = render_personal_context(self._view(source))
        assert out is not None
        return out

    def test_stated_and_derived_are_not_the_same_paragraph(self) -> None:
        stated = self._render("explicit")
        derived = self._render("inferred")
        assert stated != derived
        assert "веганск" in stated and "веганск" in derived

    def test_derived_fact_carries_the_prohibition(self) -> None:
        out = self._render("inferred")
        assert self._DERIVED_LEAD in out
        # И не попадает в «что ты знаешь» — там его цитировать разрешено.
        assert self._STATED_LEAD not in out

    def test_stated_fact_is_unchanged(self) -> None:
        out = self._render("explicit")
        assert self._STATED_LEAD in out
        assert self._DERIVED_LEAD not in out

    def test_signal_counts_as_derived(self) -> None:
        assert self._DERIVED_LEAD in self._render("signal")

    def test_mixed_view_splits_the_two(self) -> None:
        view = PersonalContextView(
            summary="любит тишину",
            green_facts=[
                GreenFact(
                    kind="lifestyle",
                    content={"key": "diet", "value": "vegan"},
                    source="explicit",
                ),
                GreenFact(
                    kind="preference",
                    content={"key": "preferred_time_slots", "value": "evening"},
                    source="inferred",
                ),
            ],
        )
        out = render_personal_context(view)
        assert out is not None
        stated, _, derived = out.partition(self._DERIVED_LEAD)
        assert "веганск" in stated and "веганск" not in derived
        assert "любит тишину" in stated

    def test_all_stated_paragraph_is_byte_identical_to_the_old_one(self) -> None:
        """Отрицательный: то, что уже помечено верно, не изменилось."""
        view = PersonalContextView(
            summary="любит тишину",
            green_facts=[
                GreenFact(
                    kind="lifestyle",
                    content={"key": "diet", "value": "vegan"},
                    source="explicit",
                )
            ],
        )
        assert render_personal_context(view) == (
            "Что ты уже знаешь об этом клиенте (используй естественно и только когда "
            "уместно — например «помню, что ты…»; НЕ перечисляй списком и НЕ "
            "придумывай ничего сверх этого): любит тишину; придерживается веганского "
            "питания."
        )


class TestFoodScannerRowsStayOutOfTheConciergePrompt:
    """Ревью DRF-1454, оси architecture + persistence (найдено независимо
    двумя осями): запомненные правки еды попадали в системный промпт консьержа
    на каждом ходе любого разговора — до 20 блюд × 3 поля = 60 фраз вида
    «порция „борщ“ — 500 г», включая разговоры, где еды нет вовсе. Их читает
    карточка сканера через recall_corrections; в списке «покажи, что помнишь»
    они остаются — эта поверхность модели, та — человека."""

    def test_food_rows_are_not_surfaced_in_the_prompt(self):
        view = PersonalContextView(
            green_facts=[
                GreenFact(
                    kind="preference",
                    content={
                        "key": "food_portion:борщ",
                        "value": 500,
                        "display": "порция «борщ» — 500 г",
                    },
                    source="explicit",
                ),
                GreenFact(
                    kind="lifestyle",
                    content={"key": "diet", "value": "vegan"},
                    source="explicit",
                ),
            ]
        )
        out = render_personal_context(view)
        assert out is not None
        assert "веганского питания" in out  # соседние домены не тронуты
        assert "борщ" not in out

    def test_a_view_with_only_food_rows_renders_nothing(self):
        view = PersonalContextView(
            green_facts=[
                GreenFact(
                    kind="preference",
                    content={
                        "key": "food_dish_name:борщ",
                        "value": "Свекольник",
                        "display": "блюдо «борщ» называет «Свекольник»",
                    },
                    source="explicit",
                ),
            ]
        )
        assert render_personal_context(view) is None

    def test_food_rows_still_render_in_the_show_list(self):
        """«Покажи, что помнишь» — поверхность человека: там строки видимы,
        иначе это запись, которую мы не имели права писать."""
        from apps.persona.memory_surface import describe_green_content

        assert (
            describe_green_content(
                {"key": "food_portion:борщ", "value": 500, "display": "порция «борщ» — 500 г"}
            )
            == "порция «борщ» — 500 г"
        )
