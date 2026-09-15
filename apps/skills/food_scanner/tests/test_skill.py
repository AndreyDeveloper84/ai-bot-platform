"""FoodScannerSkill tests (DRF-818 / Sprint 9 / P1 + Веха 1 gates).

Covers:

* matches() — photo turns + only the cb:food:{to_diary,clarify,reject}
  with a scan_id (NOT the simpler cb:food:{diary,typo} owned by P4).
* Photo flow happy path via mocked NutritionClient.scan_photo.
* All three error paths from Ayla: NotRecognized / Unavailable / API
  error → graceful fallbacks; no internal codes leak.
* Callback flow: to_diary writes log, clarify prompts, reject acks.
* Registration order vs food_clarify (P1 owned callbacks must be
  distinguishable; sentinel test asserts both skills are present).
* Веха 1 gates: NUTRITION_ENABLED / FOOD_PHOTO_SCAN_ENABLED master
  switches + per-user ``food_scanner_consent_at`` consent gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import (
    FoodLogResponse,
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionUnavailableError,
    ScanResponse,
)
from apps.skills.base import SkillContext
from apps.skills.food_scanner.skill import (
    AYLA_DOWN_FALLBACK,
    CLARIFY_PROMPT,
    CONSENT_REQUIRED_FALLBACK,
    NOT_RECOGNIZED_FALLBACK,
    NUTRITION_OFF_FALLBACK,
    PHOTO_NO_BYTES,
    PHOTO_SCAN_OFF_FALLBACK,
    REJECTED_ACK,
    FoodScannerSkill,
)


@pytest.fixture(autouse=True)
def _enable_nutrition(settings):
    """Default the Веха 1 gates ON so existing behavioural tests are
    unaffected. The dedicated ``TestGates`` class flips them back to
    pin the refusal paths.
    """

    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True


@pytest.fixture(autouse=True)
def _personal_data_granted():
    """DRF-1948: запись дневника требует PERSONAL_DATA (как запись еды текстом).

    Поведенческие тесты идут с выданным согласием; отказ пришпилен в
    ``TestGates`` фикстурой ``no_personal_data``. Без подмены предикат
    fail-closed отказал бы Mock-пользователю и скрыл бы всё остальное.
    """
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
    ) as granted:
        yield granted


@pytest.fixture
def no_personal_data():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
    ) as missing:
        yield missing


def _context(
    text: str = "",
    *,
    has_attachments: bool = False,
    photo_bytes: bytes | None = None,
    consent_at: datetime | None = None,
) -> SkillContext:
    conversation = Mock(id="conv-1")
    if photo_bytes is not None:
        conversation.last_photo_bytes = photo_bytes
    else:
        # del to ensure getattr returns None per skill convention
        del conversation.last_photo_bytes
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "12345"
    # Веха 1: default to a granted consent so the broad test suite
    # exercises the post-gate code paths. The consent-missing case is
    # asserted explicitly in TestGates.
    bot_user.food_scanner_consent_at = (
        consent_at if consent_at is not None else datetime.now(timezone.utc)
    )
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        has_attachments=has_attachments,
    )


def _scan_response(scan_id: str = "scan-1", confidence: float = 0.9) -> ScanResponse:
    return ScanResponse(
        scan_id=scan_id,
        dish_name="Борщ",
        confidence=confidence,
        portion_g=300,
        nutrition={"calories": 250, "protein_g": 12, "fat_g": 8, "carbs_g": 32},
        provider="test",
        raw={},
    )


def _log_response(log_id: str = "log-1") -> FoodLogResponse:
    return FoodLogResponse(
        log_id=log_id,
        dish_name="Борщ",
        meal_type="other",
        calories=250.0,
        raw={},
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    def test_photo_attachment_matches(self) -> None:
        ctx = _context(has_attachments=True)
        assert FoodScannerSkill().matches(ctx)

    def test_text_only_does_not_match(self) -> None:
        ctx = _context("Борщ 300г")
        assert not FoodScannerSkill().matches(ctx)

    def test_cb_to_diary_with_scan_id_matches(self) -> None:
        ctx = _context("cb:food:to_diary:abc123")
        assert FoodScannerSkill().matches(ctx)

    def test_cb_clarify_with_scan_id_matches(self) -> None:
        ctx = _context("cb:food:clarify:abc123")
        assert FoodScannerSkill().matches(ctx)

    def test_cb_reject_with_scan_id_matches(self) -> None:
        ctx = _context("cb:food:reject:abc123")
        assert FoodScannerSkill().matches(ctx)

    def test_cb_diary_no_scan_id_owned_by_p4(self) -> None:
        """``cb:food:diary`` (P4 food_clarify) MUST NOT be claimed here."""
        ctx = _context("cb:food:diary")
        assert not FoodScannerSkill().matches(ctx)

    def test_cb_typo_owned_by_p4(self) -> None:
        ctx = _context("cb:food:typo")
        assert not FoodScannerSkill().matches(ctx)

    def test_unknown_callback_does_not_match(self) -> None:
        assert not FoodScannerSkill().matches(_context("cb:other:action"))


# ─── photo flow ───────────────────────────────────────────────────────────


class TestPhotoFlow:
    def test_no_bytes_returns_graceful_message(self) -> None:
        """When the channel adapter didn't stash bytes — graceful skip."""
        ctx = _context(has_attachments=True, photo_bytes=None)
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == PHOTO_NO_BYTES

    def test_scan_happy_path(self) -> None:
        ctx = _context(has_attachments=True, photo_bytes=b"jpegdata")
        client = Mock()

        async def _scan(**kwargs):
            return _scan_response()

        client.scan_photo = _scan
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            result = FoodScannerSkill().handle(ctx)

        assert "Борщ" in result.reply_text
        assert "250 ккал" in result.reply_text
        assert result.action_type == "food_scan_card"
        assert result.action_data is not None
        assert result.action_data["scan_id"] == "scan-1"
        # Recognition card has 3 buttons.
        callbacks = [b["callback"] for b in result.action_data["buttons"]]
        assert callbacks == [
            "cb:food:to_diary:scan-1",
            "cb:food:clarify:scan-1",
            "cb:food:reject:scan-1",
        ]

    def test_low_confidence_uses_hedge(self) -> None:
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        client = Mock()

        async def _scan(**kwargs):
            return _scan_response(confidence=0.4)

        client.scan_photo = _scan
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            result = FoodScannerSkill().handle(ctx)

        assert result.reply_text.startswith("Похоже на")

    def test_not_recognized_returns_friendly_fallback(self) -> None:
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        client = Mock()

        async def _scan(**kwargs):
            raise FoodNotRecognizedError("low_confidence")

        client.scan_photo = _scan
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            result = FoodScannerSkill().handle(ctx)

        assert result.reply_text == NOT_RECOGNIZED_FALLBACK

    def test_ayla_unavailable_graceful(self) -> None:
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        client = Mock()

        async def _scan(**kwargs):
            raise NutritionUnavailableError("circuit_open")

        client.scan_photo = _scan
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            result = FoodScannerSkill().handle(ctx)

        assert result.reply_text == AYLA_DOWN_FALLBACK
        # No internal codes leak.
        assert "circuit" not in result.reply_text.lower()


# ─── callback flow ────────────────────────────────────────────────────────


class TestCallbackFlow:
    def test_to_diary_logs_and_confirms(self) -> None:
        ctx = _context("cb:food:to_diary:scan-1")
        client = Mock()

        async def _log(**kwargs):
            return _log_response()

        client.log_meal = _log
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            result = FoodScannerSkill().handle(ctx)

        assert "Борщ" in result.reply_text
        assert "250 ккал" in result.reply_text
        assert result.action_type == "food_logged"

    def test_to_diary_uses_idempotency_key(self) -> None:
        """Re-clicks must produce same idempotency key so Ayla returns
        the prior FoodLog row instead of double-logging."""
        ctx = _context("cb:food:to_diary:scan-1")
        client = Mock()
        captured: list[dict] = []

        async def _log(**kwargs):
            captured.append(kwargs)
            return _log_response()

        client.log_meal = _log
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            FoodScannerSkill().handle(ctx)
            FoodScannerSkill().handle(ctx)  # re-click

        assert captured[0]["idempotency_key"] == captured[1]["idempotency_key"]
        assert "scan-1" in captured[0]["idempotency_key"]

    def test_clarify_emits_prompt(self) -> None:
        ctx = _context("cb:food:clarify:scan-1")
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CLARIFY_PROMPT
        # No Ayla call.
        assert result.action_type == ""

    def test_reject_silent_ack(self) -> None:
        ctx = _context("cb:food:reject:scan-1")
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == REJECTED_ACK

    def test_log_unavailable_graceful(self) -> None:
        ctx = _context("cb:food:to_diary:scan-1")
        client = Mock()

        async def _log(**kwargs):
            raise NutritionUnavailableError("down")

        client.log_meal = _log
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == AYLA_DOWN_FALLBACK


# ─── Веха 1 gates ─────────────────────────────────────────────────────────


class TestGates:
    """Two-flag + consent gating contract (founder verdict 2026-06-02)."""

    def test_nutrition_off_short_circuits_photo(self, settings) -> None:
        settings.NUTRITION_ENABLED = False
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("Ayla MUST NOT be called when nutrition off"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == NUTRITION_OFF_FALLBACK
        assert result.meta is not None
        assert result.meta.get("reply_kind") == "food_scanner_nutrition_off"

    def test_nutrition_off_short_circuits_callback(self, settings) -> None:
        settings.NUTRITION_ENABLED = False
        ctx = _context("cb:food:to_diary:scan-1")
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("Ayla MUST NOT be called when nutrition off"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == NUTRITION_OFF_FALLBACK

    def test_photo_scan_off_blocks_photo_keeps_callbacks(self, settings) -> None:
        # Master switch ON, cross-border gate OFF — photo refused with
        # manual-entry hint, but to_diary on an existing scan still
        # flows (the photo was already taken before the gate flipped).
        settings.NUTRITION_ENABLED = True
        settings.FOOD_PHOTO_SCAN_ENABLED = False

        ctx_photo = _context(has_attachments=True, photo_bytes=b"jpeg")
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("Ayla scan MUST NOT be called when photo gate off"),
        ):
            photo_result = FoodScannerSkill().handle(ctx_photo)
        assert photo_result.reply_text == PHOTO_SCAN_OFF_FALLBACK
        assert photo_result.meta.get("reply_kind") == "food_scanner_photo_scan_off"

        # Existing-scan callback still flows.
        ctx_cb = _context("cb:food:to_diary:scan-1")
        client = Mock()

        async def _log(**kwargs):
            return _log_response()

        client.log_meal = _log
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            return_value=client,
        ):
            cb_result = FoodScannerSkill().handle(ctx_cb)
        assert cb_result.action_type == "food_logged"

    def test_consent_missing_short_circuits_photo(self, settings) -> None:
        ctx = _context(
            has_attachments=True,
            photo_bytes=b"jpeg",
            consent_at=None,
        )
        # Mock returns a Mock for any unset attr; explicitly set to None
        # since _context default is now() — override here.
        ctx.bot_user.food_scanner_consent_at = None

        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("Ayla MUST NOT be called without consent"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CONSENT_REQUIRED_FALLBACK
        assert result.meta.get("reply_kind") == "food_scanner_consent_required"

    def test_consent_missing_short_circuits_to_diary(self, settings) -> None:
        ctx = _context("cb:food:to_diary:scan-1")
        ctx.bot_user.food_scanner_consent_at = None
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("log_meal MUST NOT run without consent"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CONSENT_REQUIRED_FALLBACK

    def test_reject_callback_works_even_without_consent(self, settings) -> None:
        # ``reject`` is a no-op ack that never touches Ayla or any
        # user data. Gating it just confuses the user.
        ctx = _context("cb:food:reject:scan-1")
        ctx.bot_user.food_scanner_consent_at = None
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == REJECTED_ACK

    def test_gate_order_nutrition_beats_consent(self, settings) -> None:
        # Both off — the more informative «feature off» message wins.
        settings.NUTRITION_ENABLED = False
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        ctx.bot_user.food_scanner_consent_at = None
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == NUTRITION_OFF_FALLBACK

    # ── DRF-1948: PERSONAL_DATA перед согласием сканера ─────────────────────

    def test_scanner_consent_without_personal_data_refuses_the_photo(
        self, settings, no_personal_data
    ) -> None:
        from apps.skills.food_clarify.text_entry import CONSENT_TEXT

        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        assert isinstance(ctx.bot_user.food_scanner_consent_at, datetime)  # согласие сканера есть
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("Ayla MUST NOT be called without PERSONAL_DATA"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CONSENT_TEXT
        assert result.meta.get("reply_kind") == "food_scanner_personal_data_required"

    def test_scanner_consent_without_personal_data_refuses_to_diary(
        self, settings, no_personal_data
    ) -> None:
        from apps.skills.food_clarify.text_entry import CONSENT_TEXT

        ctx = _context("cb:food:to_diary:scan-1")
        assert isinstance(ctx.bot_user.food_scanner_consent_at, datetime)
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("log_meal MUST NOT run without PERSONAL_DATA"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CONSENT_TEXT
        assert result.meta.get("reply_kind") == "food_scanner_personal_data_required"

    def test_personal_data_is_checked_before_the_scanner_consent(
        self, settings, no_personal_data
    ) -> None:
        from apps.skills.food_clarify.text_entry import CONSENT_TEXT

        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        ctx.bot_user.food_scanner_consent_at = None
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CONSENT_TEXT

    def test_nutrition_off_beats_personal_data(self, settings, no_personal_data) -> None:
        settings.NUTRITION_ENABLED = False
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == NUTRITION_OFF_FALLBACK

    def test_photo_scan_off_beats_personal_data(self, settings, no_personal_data) -> None:
        settings.FOOD_PHOTO_SCAN_ENABLED = False
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == PHOTO_SCAN_OFF_FALLBACK

    def test_reject_works_without_personal_data(self, settings, no_personal_data) -> None:
        ctx = _context("cb:food:reject:scan-1")
        result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == REJECTED_ACK

    def test_mock_shaped_consent_does_not_silently_pass(self, settings) -> None:
        # Адверсариальный обзор PRE_PILOT #2 — bare Mock() auto-generates
        # a truthy Mock object on every attr access. Without the
        # isinstance(datetime) guard the gate would silently pass and
        # the skill would call Ayla using a Mock identity. Pin the
        # guard so the refusal path fires whenever the attribute is
        # something other than a real datetime.
        ctx = _context(has_attachments=True, photo_bytes=b"jpeg")
        # Default _context() sets a datetime — drop it back to a bare
        # Mock-shaped attr to simulate a forgotten test fixture.
        ctx.bot_user.food_scanner_consent_at = Mock()
        with patch(
            "apps.skills.food_scanner.skill.get_nutrition_client",
            side_effect=AssertionError("Ayla MUST NOT be called for Mock-shaped consent"),
        ):
            result = FoodScannerSkill().handle(ctx)
        assert result.reply_text == CONSENT_REQUIRED_FALLBACK


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_food_scanner_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "food_scanner" in names
        # food_clarify present too — the two own DIFFERENT callbacks
        # (scan_id-bearing vs bare), so order between them is moot.
        assert "food_clarify" in names


# ─── DRF-1454: memory on the card ────────────────────────────────────────


class TestMemoryOnTheCard:
    def test_card_is_byte_identical_without_memory(self) -> None:
        """The pre-memory output must not move for a person with nothing stored."""
        from apps.skills.food_scanner.skill import _format_scan_card

        assert _format_scan_card(_scan_response()) == (
            "Узнала: Борщ.\nПримерно 300 г.\n250 ккал · Б 12 · Ж 8 · У 32\nЗаписать в дневник?"
        )

    def test_one_line_is_added_when_something_was_clarified(self) -> None:
        from apps.orchestrator.memory.food import FoodRecall
        from apps.skills.food_scanner.skill import _format_scan_card

        card = _format_scan_card(_scan_response(), FoodRecall(dish_name="плов"))

        assert "Помню с прошлого раза: «плов»." in card
        # Ayla's own numbers are printed unchanged — the bot keeps no portion
        # and no macros of its own that could contradict them (REMEMBERED_FIELDS).
        assert "Примерно 300 г." in card
        assert card.endswith("Записать в дневник?")
        assert len(card.splitlines()) == 5

    def test_reject_records_a_recogniser_signal_not_a_dietary_fact(self) -> None:
        """«Не то» must never become «он это не ест» (DRF-1260 fabrication ban)."""
        from apps.orchestrator.memory import food as food_memory

        seen: list[str] = []
        with patch.object(
            food_memory,
            "note_recognition_rejected",
            side_effect=lambda bot_user, *, scan_id: seen.append(scan_id),
        ):
            result = FoodScannerSkill().handle(_context("cb:food:reject:scan-1"))

        assert result.reply_text == REJECTED_ACK
        assert seen == ["scan-1"]


class TestCorrectedGramsReachTheLog:
    """DRF-1579 (F3): граммы, названные на карточке, пишутся в дневник.

    Вес берётся из ``food_scan_grams`` по ``scan_id`` — не из карточки
    последнего фото: новое фото не теряет обещанный вес. Множитель — от порции,
    которую распознал скан. Происхождение — §136 ``photo_user_corrected``.
    Отметку «записано» сканер пишет в свой подключ ``food_scan_logged``.
    """

    def _to_diary(
        self,
        *,
        card: dict | None = None,
        grams_map: dict | None = None,
        echoed_origin: str | None = "photo_user_corrected",
        raise_exc: Exception | None = None,
        logged: dict | None = None,
    ):
        ctx = _context("cb:food:to_diary:scan-1")
        state: dict = {}
        if card is not None:
            state["food_scan"] = card
        if grams_map is not None:
            state["food_scan_grams"] = grams_map
        if logged is not None:
            state["food_scan_logged"] = logged
        ctx.conversation.skill_state = state
        client = Mock()
        captured: list[dict] = []
        written: list = []

        async def _log(**kwargs):
            captured.append(kwargs)
            self.written_at_call = list(written)
            if raise_exc is not None:
                raise raise_exc
            raw = {"entry_origin": echoed_origin} if "entry_origin" in kwargs else {}
            return FoodLogResponse(
                log_id="log-1", dish_name="Борщ", meal_type="other", calories=250.0, raw=raw
            )

        client.log_meal = _log
        with (
            patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client),
            patch(
                "apps.conversations.services.write_skill_state",
                side_effect=lambda conv, key, value: written.append((key, value)),
            ),
        ):
            result = FoodScannerSkill().handle(ctx)
        return result, captured, written

    GRAMS = {"scan-1": {"grams": 500, "portion_g": 250}}

    def test_corrected_grams_set_the_portion_and_the_origin(self) -> None:
        result, captured, written = self._to_diary(grams_map=self.GRAMS)

        assert result.reply_text == "Записала: Борщ — 250 ккал."
        assert captured[0]["scan_id"] == "scan-1"
        assert captured[0]["portion_multiplier"] == 2.0
        assert captured[0]["entry_origin"] == "photo_user_corrected"
        assert ("food_scan_logged", {"scan-1": "log-1"}) in written

    def test_the_scan_is_marked_in_flight_before_the_call(self) -> None:
        # Ответ про граммы, пришедший, пока запись летит, должен это видеть.
        self._to_diary(grams_map=self.GRAMS)

        assert self.written_at_call == [("food_scan_logged", {"scan-1": None})]

    def test_without_a_correction_the_log_call_is_unchanged(self) -> None:
        _, captured, written = self._to_diary(
            card={"scan_id": "scan-1", "dish": "Борщ", "portion_g": 250}
        )

        assert captured[0]["scan_id"] == "scan-1"
        assert "portion_multiplier" not in captured[0]
        assert "entry_origin" not in captured[0]
        assert ("food_scan_logged", {"scan-1": "log-1"}) in written

    def test_a_newer_photo_does_not_lose_the_promised_weight(self) -> None:
        newer = {"scan_id": "scan-2", "dish": "Суп", "portion_g": 300}

        _, captured, _ = self._to_diary(card=newer, grams_map=self.GRAMS)

        assert captured[0]["portion_multiplier"] == 2.0

    def test_a_replayed_key_that_returns_the_old_entry_says_the_weight_did_not_apply(self) -> None:
        # Первый тап ушёл по таймауту, запись уже была: каталог вернул прежнюю строку.
        result, captured, _ = self._to_diary(grams_map=self.GRAMS, echoed_origin=None)

        assert captured[0]["portion_multiplier"] == 2.0
        assert result.reply_text == (
            "Записала: Борщ — 250 ккал. Вес 500 г не применился: это блюдо уже было в "
            "дневнике. Чтобы поменять вес, удали запись и запиши заново текстом."
        )

    def test_a_correction_the_catalogue_cannot_scale_is_not_logged(self) -> None:
        result, captured, _ = self._to_diary(grams_map={"scan-1": {"grams": 5000, "portion_g": 10}})

        assert result.reply_text == (
            "Вес 5000 г слишком далёк от распознанной порции — пересчитать не могу. "
            "Нажми «✏️ Уточнить» → «⚖️ Грамм» и укажи вес ещё раз."
        )
        assert captured == []

    def test_a_correction_for_another_scan_is_not_applied(self) -> None:
        _, captured, _ = self._to_diary(grams_map={"scan-2": {"grams": 500, "portion_g": 250}})

        assert captured[0]["scan_id"] == "scan-1"
        assert "portion_multiplier" not in captured[0]

    def test_an_uncertain_failure_leaves_the_scan_in_flight(self) -> None:
        # Таймаут/5xx: запись могла лечь. Не «записано» и не «свободно».
        result, captured, written = self._to_diary(
            grams_map=self.GRAMS, raise_exc=NutritionUnavailableError("down")
        )

        assert len(captured) == 1
        assert result.meta["reply_kind"] == "food_scanner_log_unavailable"
        assert written == [("food_scan_logged", {"scan-1": None})]

    def test_a_repeat_tap_does_not_downgrade_a_logged_scan(self) -> None:
        # Первый тап записал, человек видел «Записала»; повторный ушёл по таймауту.
        # Скан не должен стать «в полёте» — иначе ответ про вес скажет «не знаю,
        # дошла ли» сразу после «Записала».
        result, captured, written = self._to_diary(
            logged={"scan-1": "log-0"}, raise_exc=NutritionUnavailableError("down")
        )

        assert len(captured) == 1
        assert result.meta["reply_kind"] == "food_scanner_log_unavailable"
        # empty-assert-ok: the scan is already logged and the repeat call failed — no mark to write
        assert not any(value == {"scan-1": None} for _, value in written)

    @pytest.mark.parametrize(
        ("exc", "reply_kind"),
        [
            (FoodNotRecognizedError("nutrition_missing"), "food_scanner_log_not_recognized"),
            (NutritionAPIError("http_400_unknown"), "food_scanner_log_error"),
        ],
    )
    def test_a_definite_refusal_clears_the_in_flight_mark(self, exc, reply_kind) -> None:
        # Каталог ответил отказом — записи нет, вес снова можно назвать.
        result, _, written = self._to_diary(grams_map=self.GRAMS, raise_exc=exc)

        assert result.meta["reply_kind"] == reply_kind
        assert written[0] == ("food_scan_logged", {"scan-1": None})
        assert written[-1] == ("food_scan_logged", {})

    def test_the_card_keeps_the_scan_portion(self) -> None:
        from types import SimpleNamespace

        from apps.skills.food_scanner.skill import _stash_last_card

        ctx = _context("")
        written: list = []
        with patch(
            "apps.conversations.services.write_skill_state",
            side_effect=lambda conv, key, value: written.append((key, value)),
        ):
            _stash_last_card(
                ctx, SimpleNamespace(scan_id="scan-9", dish_name="Плов", portion_g=320.0)
            )

        assert written == [("food_scan", {"scan_id": "scan-9", "dish": "Плов", "portion_g": 320.0})]
