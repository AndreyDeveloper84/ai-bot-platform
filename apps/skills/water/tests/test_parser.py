"""parse_beverage regex tests (DRF-819 / Sprint 9 / P2).

Covers:

* Each beverage slug (15 total) with a representative alias.
* DRF-358 bug repros (decimal-comma volume, kofe + молоком).
* Unit variants: мл / литр / стакан / бутылка / чашка / etc.
* Default serving when no volume given.
* REFUSED sentinel.
* Edge cases: empty, whitespace, non-string.
"""

from __future__ import annotations

import pytest

from apps.skills.water.parser import (
    REFUSED,
    BeverageMatch,
    parse_beverage,
)


# ─── beverage slug matrix ─────────────────────────────────────────────────


class TestBeverageSlugs:
    @pytest.mark.parametrize(
        "text, expected_slug, expected_ml",
        [
            ("зелёный чай", "chai_zelenyi", 250),
            ("ромашка", "chai_travyanoi", 250),
            ("чёрный чай", "chai_chernyi", 250),
            ("чай", "chai_chernyi", 250),
            ("капучино", "kofe_kapuchino", 250),
            ("латте", "kofe_latte", 350),
            ("эспрессо", "kofe_espresso", 30),
            ("кофе", "kofe_chernyi", 200),
            ("американо", "kofe_chernyi", 200),
            ("боржоми", "voda_mineralnaya", 250),
            ("стакан воды", "voda", 250),
            ("апельсиновый сок", "sok_apelsinovyi", 200),
            ("яблочный сок", "sok_yablochnyi", 200),
            ("молоко", "moloko", 250),
            ("пиво", "pivo", 500),
            ("вино", "vino", 150),
            ("компот", "kompot", 250),
        ],
    )
    def test_default_serving_per_slug(
        self, text: str, expected_slug: str, expected_ml: int
    ) -> None:
        result = parse_beverage(text)
        assert isinstance(result, BeverageMatch)
        assert result.slug == expected_slug
        assert result.ml == expected_ml


# ─── volume extraction ────────────────────────────────────────────────────


class TestVolumeExtraction:
    @pytest.mark.parametrize(
        "text, expected_ml",
        [
            ("250 мл воды", 250),
            ("0.5 л воды", 500),
            ("0,5 л воды", 500),  # decimal comma — DRF-358 bug 1
            ("1 литр воды", 1000),
            ("2 стакана воды", 500),
            ("стакан воды", 250),
            ("бутылка воды", 500),
            ("чашка кофе", 200),
            ("2 бокала вина", 300),
        ],
    )
    def test_volume_extracted(self, text: str, expected_ml: int) -> None:
        result = parse_beverage(text)
        assert isinstance(result, BeverageMatch)
        assert result.ml == expected_ml


# ─── DRF-358 known parser-bug repros ──────────────────────────────────────


class TestDRF358BugRepros:
    def test_sok_decimal_comma_falls_through(self) -> None:
        """Bug 1 from 2026-05-08: «Сок 0,5л» — bare «сок» not in catalog.

        Documented limitation: catalog requires colour (apelsinovyi /
        yablochnyi). Parser returns None; P4 food_clarify catches the
        same text with its diary-or-typo card. The DRF-358 fix is the
        card, not the regex.
        """
        assert parse_beverage("Сок 0,5л") is None

    def test_kofe_s_molokom_hits_kofe(self) -> None:
        """«Кофе с молоком» — the «кофе» stem wins over «молоко»."""
        result = parse_beverage("кофе с молоком")
        assert isinstance(result, BeverageMatch)
        assert result.slug == "kofe_chernyi"

    def test_kofe_250ml_no_space_falls_to_default(self) -> None:
        """«Кофе 250мл» — no space between number and unit.

        Source parser requires unit at word boundary (space-padded),
        so «250мл» (no space) fails unit-match and falls back to
        default-serving (200 ml for kofe_chernyi). Inherited mysite
        limitation; P4 catches the text via the food_clarify card if
        the user expected a precise log.
        """
        result = parse_beverage("Кофе 250мл")
        assert isinstance(result, BeverageMatch)
        assert result.slug == "kofe_chernyi"
        assert result.ml == 200  # default — unit wasn't extracted


# ─── refusal + edge cases ─────────────────────────────────────────────────


class TestRefusalAndEdges:
    @pytest.mark.parametrize(
        "text",
        ["не скажу", "секрет", "🤷", "пропусти"],
    )
    def test_refusal_returns_sentinel(self, text: str) -> None:
        assert parse_beverage(text) == REFUSED

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_empty_returns_none(self, text: str) -> None:
        assert parse_beverage(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "Хочу записаться на массаж",
            "Как у вас работает запись?",
            "Где вы находитесь?",
        ],
    )
    def test_non_beverage_text_returns_none(self, text: str) -> None:
        assert parse_beverage(text) is None


# ─── specificity ordering ─────────────────────────────────────────────────


class TestSpecificityOrdering:
    """More-specific aliases must beat generic ones."""

    def test_zelenyi_beats_chai(self) -> None:
        result = parse_beverage("зелёный чай")
        assert isinstance(result, BeverageMatch)
        assert result.slug == "chai_zelenyi"  # not chai_chernyi

    def test_kapuchino_beats_kofe(self) -> None:
        result = parse_beverage("капучино")
        assert isinstance(result, BeverageMatch)
        assert result.slug == "kofe_kapuchino"

    def test_mineralnaya_beats_voda(self) -> None:
        result = parse_beverage("минералка")
        assert isinstance(result, BeverageMatch)
        assert result.slug == "voda_mineralnaya"
