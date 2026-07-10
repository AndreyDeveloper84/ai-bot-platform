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
