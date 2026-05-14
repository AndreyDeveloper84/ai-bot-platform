"""FoodScannerSkill tests (DRF-818 / Sprint 9 / P1).

Covers:

* matches() — photo turns + only the cb:food:{to_diary,clarify,reject}
  with a scan_id (NOT the simpler cb:food:{diary,typo} owned by P4).
* Photo flow happy path via mocked NutritionClient.scan_photo.
* All three error paths from Ayla: NotRecognized / Unavailable / API
  error → graceful fallbacks; no internal codes leak.
* Callback flow: to_diary writes log, clarify prompts, reject acks.
* Registration order vs food_clarify (P1 owned callbacks must be
  distinguishable; sentinel test asserts both skills are present).
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.integrations.ayla import (
    FoodLogResponse,
    FoodNotRecognizedError,
    NutritionUnavailableError,
    ScanResponse,
)
from apps.skills.base import SkillContext
from apps.skills.food_scanner.skill import (
    AYLA_DOWN_FALLBACK,
    CLARIFY_PROMPT,
    NOT_RECOGNIZED_FALLBACK,
    PHOTO_NO_BYTES,
    REJECTED_ACK,
    FoodScannerSkill,
)


def _context(
    text: str = "",
    *,
    has_attachments: bool = False,
    photo_bytes: bytes | None = None,
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


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_food_scanner_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "food_scanner" in names
        # food_clarify present too — the two own DIFFERENT callbacks
        # (scan_id-bearing vs bare), so order between them is moot.
        assert "food_clarify" in names
