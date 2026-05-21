"""Booking-skill prompt template tests (DRF-839 / Phase 1 / B3)."""

from __future__ import annotations

from apps.skills.booking.prompts import BrandVoiceConfig, build_booking_prompt


def _voice() -> BrandVoiceConfig:
    return BrandVoiceConfig(
        persona="Алина, администратор",
        tone="дружелюбный",
        forbidden=("гарантирую",),
    )


class TestShape:
    def test_returns_system_then_user(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="запиши на массаж")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "запиши на массаж"

    def test_system_includes_persona(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="x")
        assert "Алина" in messages[0]["content"]

    def test_system_includes_tone(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="x")
        assert "дружелюбный" in messages[0]["content"]

    def test_system_includes_forbidden(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="x")
        assert "гарантирую" in messages[0]["content"]

    def test_system_lists_all_four_tools(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="x")
        body = messages[0]["content"]
        for tool in ("show_masters", "show_slots", "confirm_booking", "show_my_bookings"):
            assert tool in body


class TestLiveDataRenderingRule:
    """The "render NOW, don't say 'wait'" instruction (fix 2026-05-21).

    When any tool result is spliced into the prompt (candidate_masters,
    slots, bookings, etc.), the system text MUST include the explicit
    instruction so gpt-4o-mini doesn't reply with placeholder filler
    ("Один момент!"). Without a tool result present, the instruction is
    absent — first-call prompts stay short.
    """

    def test_rule_present_when_masters_spliced(self) -> None:
        messages = build_booking_prompt(
            brand_voice=_voice(),
            query="запиши",
            candidate_masters=[{"id": 1, "name": "Аня", "specialization": "Массаж"}],
        )
        body = messages[0]["content"]
        assert "ВАЖНО" in body
        assert "НЕ пиши" in body
        assert "«один момент»" in body or "один момент" in body

    def test_rule_present_when_slots_spliced(self) -> None:
        messages = build_booking_prompt(
            brand_voice=_voice(),
            query="запиши",
            available_slots=[{"datetime": "2026-05-22T10:00", "duration_minutes": 60}],
        )
        body = messages[0]["content"]
        assert "ВАЖНО" in body

    def test_rule_present_when_bookings_spliced(self) -> None:
        messages = build_booking_prompt(
            brand_voice=_voice(),
            query="мои записи",
            user_bookings=[],  # empty list is still "data was fetched"
        )
        body = messages[0]["content"]
        assert "ВАЖНО" in body

    def test_rule_absent_on_first_call_no_data(self) -> None:
        """First LLM call (no tool result yet) — instruction must NOT appear.
        Adding it before the model has data would confuse the tool-use loop."""
        messages = build_booking_prompt(brand_voice=_voice(), query="запиши")
        body = messages[0]["content"]
        assert "ВАЖНО" not in body


class TestGroundingBlocks:
    def test_candidate_masters_spliced_in(self) -> None:
        masters = [
            {"id": 11, "name": "Ольга", "specialization": "Массаж"},
            {"id": 12, "name": "Иван", "specialization": "СПА"},
        ]
        messages = build_booking_prompt(
            brand_voice=_voice(),
            query="x",
            candidate_masters=masters,
        )
        body = messages[0]["content"]
        assert "Ольга" in body
        assert "[11]" in body
        assert "Иван" in body

    def test_available_slots_spliced_in(self) -> None:
        slots = [
            {"datetime": "2026-05-20T14:00:00", "duration_minutes": 60},
            {"datetime": "2026-05-20T15:30:00", "duration_minutes": 60},
        ]
        messages = build_booking_prompt(
            brand_voice=_voice(),
            query="x",
            available_slots=slots,
        )
        body = messages[0]["content"]
        assert "2026-05-20T14:00:00" in body
        assert "60 мин" in body

    def test_confirmation_block_spliced_in(self) -> None:
        confirmation = {
            "record_id": 9999,
            "visit_at": "2026-05-20T14:00:00",
            "master_name": "Ольга",
            "service_name": "Массаж спины",
        }
        messages = build_booking_prompt(
            brand_voice=_voice(),
            query="x",
            confirmation=confirmation,
        )
        body = messages[0]["content"]
        assert "Ольга" in body
        assert "9999" in body
        assert "Массаж спины" in body

    def test_user_bookings_block_when_empty(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="x", user_bookings=[])
        body = messages[0]["content"]
        assert "ПРЕДСТОЯЩИЕ ЗАПИСИ" in body

    def test_user_bookings_block_lists_rows(self) -> None:
        bookings = [
            {
                "record_id": 42,
                "visit_at": "2026-05-20T14:00:00",
                "master_name": "Ольга",
                "service_name": "Массаж",
            },
        ]
        messages = build_booking_prompt(brand_voice=_voice(), query="x", user_bookings=bookings)
        body = messages[0]["content"]
        assert "Ольга" in body
        assert "Массаж" in body


class TestAntiHallucination:
    def test_warns_against_inventing_ids(self) -> None:
        messages = build_booking_prompt(brand_voice=_voice(), query="x")
        body = messages[0]["content"]
        assert "Никогда не выдумывай ID" in body or "не выдумывай" in body
