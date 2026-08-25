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
        assert mentions_service("хочу брови") is True

    @pytest.mark.parametrize("text", ["Устала спина", "Лицо устало после работы", "болит спина"])
    def test_bare_body_parts_are_not_service_words(self, text):
        """A complaint about a body part is not a request for a slot;
        «массаж спины» still matches through «массаж»."""
        assert mentions_service(text) is False

    def test_body_part_with_a_service_still_matches(self):
        assert mentions_service("массаж спины") is True
        assert mentions_service("чистка лица") is True

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


# ---------------------------------------------------------------------------
# DRF-1404 — the length invariant belongs in the MATCHER, not only in a
# test over the seed tuple.
#
# ``_MIN_PREFIX_STEM`` is the rule DRF-963 bought: a stem shorter than 5
# characters may not be matched as a PREFIX, because «спа» then swallows
# «спасибо». Until this patch the rule was enforced in exactly two
# places — a unit test over ``_SERVICE_STEMS`` and the length filter in
# ``tenant_service_stems`` — and in neither of them at match time.
#
# ``extra_stems`` is a public parameter that takes an arbitrary tuple or
# callable, so any caller could hand ``_mentions_stem`` a three-letter
# stem and reproduce DRF-963 verbatim. Measured on 2026-08-25:
# ``mentions_service("спасибо большое", extra_stems=("спа",))`` → True.
#
# The seed vocabulary itself is CLEAN — «промассажировали» has never
# matched, because the stem must begin a word. This class is closed
# already; what was open is the vocabulary growing past the guard.
# ---------------------------------------------------------------------------


class TestDrf1404ShortStemsCannotPrefixMatch:
    """A short stem must be a whole word — wherever it came from."""

    @pytest.mark.parametrize(
        "text",
        ["спасибо большое", "спасибо", "спасите", "спальный район"],
    )
    def test_short_extra_stem_does_not_swallow_a_longer_word(self, text: str) -> None:
        assert mentions_service(text, extra_stems=("спа",)) is False

    def test_short_extra_stem_still_matches_its_own_word(self) -> None:
        """Narrowing must not cost the stem its real hit."""
        assert mentions_service("хочу спа", extra_stems=("спа",)) is True

    def test_callable_extra_stems_are_guarded_too(self) -> None:
        """The tenant catalog arrives as a deferred callable."""
        assert looks_like_booking_request("спасибо большое", extra_stems=lambda: ("спа",)) is False

    def test_long_extra_stem_still_matches_as_a_prefix(self) -> None:
        """The invariant narrows SHORT stems only."""
        assert mentions_service("хочу шугаринга", extra_stems=("шугаринг",)) is True


class TestDrf1404SeedVocabularyStaysWhole:
    """The stem-inside-a-word class, pinned so it cannot come back."""

    @pytest.mark.parametrize(
        "text",
        [
            "промассативали",
            "промассажировали",
            "спасибо большое",
            "карта не проходит",
            "сколько минут ждать",
        ],
    )
    def test_stem_inside_a_word_is_not_a_service(self, text: str) -> None:
        assert mentions_service(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "хочу массаж",
            "массаж спины",
            "запишите на маникюр",
            "чистка лица",
            "хочу спа",
            "мои брови",
        ],
    )
    def test_real_service_mentions_still_match(self, text: str) -> None:
        assert mentions_service(text) is True
