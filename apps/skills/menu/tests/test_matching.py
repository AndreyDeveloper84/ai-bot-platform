"""Matcher + canonical-menu tests (DRF-963 / U-1).

Pins the widened vocabulary from the Wave 1 Validation findings, the
normalisation rules (case / punctuation / ``ё``), and the invariant that
every menu button translates into a phrase an existing skill claims.
"""

from __future__ import annotations

import pytest

from apps.skills.booking.lookup import is_personal_booking_lookup
from apps.skills.menu.matching import (
    CALLBACK_MENU_HELP,
    MENU_CALLBACK_TEXT,
    looks_like_booking_request,
    looks_like_help_request,
    main_menu_action_data,
    main_menu_buttons,
    mentions_service,
    normalize,
)


class TestNormalize:
    def test_lowercases_and_pads(self):
        assert normalize("Массаж") == " массаж "

    def test_folds_yo(self):
        assert normalize("причёска") == normalize("прическа")

    def test_strips_punctuation(self):
        assert normalize("Хочу массаж!!!") == normalize("хочу, массаж")

    def test_collapses_whitespace(self):
        assert normalize("хочу    массаж\n\tспины") == " хочу массаж спины "

    def test_empty_stays_blank(self):
        assert normalize("").strip() == ""
        assert normalize("   ...!!!   ").strip() == ""


class TestBookingRequests:
    """U-1 — live phrasings that used to fall through to echo."""

    @pytest.mark.parametrize(
        "text",
        [
            "Хочу массаж",
            "хочу массаж",
            "ХОЧУ МАССАЖ",
            "Мне бы маникюр",
            "Нужен массаж спины",
            "массаж",
            "Массаж!",
            "можно на маникюр?",
            "Интересует антицеллюлитный массаж",
            "хотелось бы на педикюр",
            "спа программа",
            "есть окошко на завтра?",
            "есть свободное время?",
            "когда можно прийти",
            "есть места?",
        ],
    )
    def test_recognised(self, text):
        assert looks_like_booking_request(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "привет",
            "спасибо",
            "ок",
            "",
            "   ",
            "как вас найти",
            "оператор",
            "ыаывпаып",
        ],
    )
    def test_not_recognised(self, text):
        assert looks_like_booking_request(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            # Regression: the 3-letter «спа» prefix used to swallow these,
            # routing a thank-you / a complaint into the booking flow.
            "спасибо",
            "спасибо большое",
            "спазм в шее",
            "хочу спать",
        ],
    )
    def test_short_prefix_false_positives_are_gone(self, text):
        assert looks_like_booking_request(text) is False

    def test_prefix_stems_are_long_enough(self):
        """Guard the invariant that produced the «спасибо» bug."""
        from apps.skills.menu.matching import _MIN_PREFIX_STEM, _SERVICE_STEMS

        too_short = [s for s in _SERVICE_STEMS if len(s) < _MIN_PREFIX_STEM]
        assert too_short == [], f"move to _SERVICE_WORDS (whole-word match): {too_short}"

    def test_short_service_words_match_as_whole_words(self):
        assert mentions_service("хочу спа") is True
        assert mentions_service("массаж спины") is True

    def test_word_start_anchored_not_arbitrary_substring(self):
        # «промассажировали» contains "массаж" mid-word — a naive substring
        # matcher would route a past-tense comment into a booking flow.
        assert mentions_service("нам всё промассажировали") is False

    def test_suffixes_still_match(self):
        for text in ("массажа", "массажем", "маникюра", "маникюрчик"):
            assert mentions_service(text) is True, text

    def test_extra_stems_widen_coverage(self):
        assert looks_like_booking_request("хочу криолиполиз") is False
        assert looks_like_booking_request("хочу криолиполиз", extra_stems=("криолиполиз",)) is True


class TestHelpRequests:
    @pytest.mark.parametrize(
        "text",
        ["помощь", "Помощь!", "помоги", "что ты умеешь?", "что умеешь", "меню", "справка", "help"],
    )
    def test_recognised(self, text):
        assert looks_like_help_request(text) is True

    @pytest.mark.parametrize("text", ["привет", "хочу массаж", "", "помощник по дому"])
    def test_not_recognised(self, text):
        assert looks_like_help_request(text) is False


class TestMainMenu:
    def test_brief_minimum_actions_present(self):
        labels = " ".join(b["label"] for b in main_menu_buttons())
        assert "Записаться" in labels
        assert "Мои записи" in labels
        assert "Помощь" in labels
        # Linear DRF-963 additionally asks for перенести / отменить.
        assert "Перенести" in labels
        assert "Отменить" in labels

    def test_every_button_has_a_route(self):
        """No dead buttons: each callback is either translated or local."""
        for button in main_menu_buttons():
            callback = button["callback"]
            assert callback in MENU_CALLBACK_TEXT or callback == CALLBACK_MENU_HELP

    def test_callbacks_are_ascii_slugs(self):
        # MAX rejects non-slug callback payloads (HTTP 400 proto.payload),
        # so the canonical Russian phrase must never travel on the wire.
        for button in main_menu_buttons():
            assert button["callback"].isascii()
            assert " " not in button["callback"]

    def test_canonical_phrases_reach_the_booking_skill(self):
        """The whole design rests on this: each menu phrase is claimed by
        the booking skill's OWN untouched matcher."""
        from apps.skills.booking.skill import _legacy_keyword_match

        for callback, phrase in MENU_CALLBACK_TEXT.items():
            claimed = _legacy_keyword_match(phrase) or is_personal_booking_lookup(phrase)
            assert claimed, f"{callback} → {phrase!r} is not claimed by booking"

    def test_action_data_uses_platform_canonical_envelope(self):
        """Flat short-form renders on MAX but silently vanishes on Telegram."""
        data = main_menu_action_data()
        attachments = data["attachments"]
        assert attachments[0]["type"] == "inline_keyboard"
        assert attachments[0]["payload"]["buttons"] == main_menu_buttons()
