"""UI keyboard builder + callback parser tests (DRF-832 / Sprint 9 / D2)."""

from __future__ import annotations

import pytest

from apps.orchestrator.ui.keyboards import (
    CALLBACK_REQUEST_CONTACT,
    Button,
    anketa_choice_keyboard,
    correction_choice_keyboard,
    food_drink_clarify_keyboard,
    food_recognition_keyboard,
    parse_callback,
    request_contact_keyboard,
)


# ─── Button + contract ────────────────────────────────────────────────────


class TestButton:
    def test_as_dict_contract(self) -> None:
        """Composer contract: {label, callback} — nothing else."""
        b = Button(label="✅ Да", callback="cb:test")
        assert b.as_dict() == {"label": "✅ Да", "callback": "cb:test"}

    def test_frozen(self) -> None:
        """Buttons are immutable. Prevents accidental mutation between
        producer and channel adapter."""
        b = Button(label="x", callback="cb:x")
        with pytest.raises(Exception):  # FrozenInstanceError is dataclass-specific
            b.label = "y"  # type: ignore[misc]


# ─── individual builders ──────────────────────────────────────────────────


class TestFoodDrinkClarifyKeyboard:
    def test_two_buttons_diary_and_typo(self) -> None:
        kb = food_drink_clarify_keyboard()
        assert len(kb) == 2
        callbacks = [b["callback"] for b in kb]
        assert callbacks == ["cb:food:diary", "cb:food:typo"]

    def test_labels_are_user_facing(self) -> None:
        kb = food_drink_clarify_keyboard()
        labels = [b["label"] for b in kb]
        assert any("Дневник" in lab or "дневник" in lab for lab in labels)
        assert any("Опечатка" in lab or "опечатка" in lab for lab in labels)


class TestFoodRecognitionKeyboard:
    def test_three_buttons_embed_scan_id(self) -> None:
        kb = food_recognition_keyboard("abc123")
        callbacks = [b["callback"] for b in kb]
        assert callbacks == [
            "cb:food:to_diary:abc123",
            "cb:food:clarify:abc123",
            "cb:food:reject:abc123",
        ]

    def test_distinct_scan_ids_distinct_callbacks(self) -> None:
        """No accidental scan_id reuse — different scans must produce
        different callback strings."""
        kb1 = food_recognition_keyboard("scan-1")
        kb2 = food_recognition_keyboard("scan-2")
        cbs1 = {b["callback"] for b in kb1}
        cbs2 = {b["callback"] for b in kb2}
        assert cbs1.isdisjoint(cbs2)


class TestCorrectionChoiceKeyboard:
    def test_three_fields(self) -> None:
        kb = correction_choice_keyboard("scan-1")
        callbacks = [b["callback"] for b in kb]
        assert callbacks == [
            "cb:food:correct:grams:scan-1",
            "cb:food:correct:name:scan-1",
            "cb:food:correct:macros:scan-1",
        ]


class TestAnketaChoiceKeyboard:
    def test_renders_options(self) -> None:
        kb = anketa_choice_keyboard(
            step="gender", options=[("Женский", "female"), ("Мужской", "male")]
        )
        assert kb == [
            {"label": "Женский", "callback": "cb:anketa:choice:gender:female"},
            {"label": "Мужской", "callback": "cb:anketa:choice:gender:male"},
        ]

    def test_empty_options_raises(self) -> None:
        """Empty choice keyboard is a programming error — fail loudly,
        don't render a soft-locked widget."""
        with pytest.raises(ValueError, match="no options"):
            anketa_choice_keyboard(step="goal", options=[])


class TestRequestContactKeyboard:
    def test_uses_sentinel_callback(self) -> None:
        """Channel adapter recognises this sentinel and swaps for native
        phone-collect widget. Don't change the constant without updating
        every channel adapter."""
        kb = request_contact_keyboard()
        assert len(kb) == 1
        assert kb[0]["callback"] == CALLBACK_REQUEST_CONTACT


# ─── callback parser ──────────────────────────────────────────────────────


class TestParseCallback:
    def test_with_ref(self) -> None:
        assert parse_callback("cb:food:to_diary:abc123") == {
            "domain": "food",
            "action": "to_diary",
            "ref": "abc123",
        }

    def test_without_ref(self) -> None:
        assert parse_callback("cb:food:diary") == {
            "domain": "food",
            "action": "diary",
            "ref": None,
        }

    def test_compound_ref(self) -> None:
        """``cb:food:correct:grams:abc123`` — ``correct`` is the action,
        ``grams:abc123`` is a compound ref (field+scan_id). Parser uses
        the last colon segment as ref so handlers can split further.
        """
        assert parse_callback("cb:food:correct:grams:abc123") == {
            "domain": "food",
            "action": "correct",
            "ref": "grams:abc123",
        }

    @pytest.mark.parametrize(
        "malformed",
        [
            "",
            "not-a-callback",
            "cb:",
            "cb:food",
            "cb::action",  # empty domain
            "cb:food:",  # empty action
        ],
    )
    def test_malformed_returns_none(self, malformed: str) -> None:
        assert parse_callback(malformed) is None

    def test_round_trip_food_recognition(self) -> None:
        kb = food_recognition_keyboard("xyz")
        parsed = parse_callback(kb[0]["callback"])
        assert parsed is not None
        assert parsed["domain"] == "food"
        assert parsed["action"] == "to_diary"
        assert parsed["ref"] == "xyz"

    def test_round_trip_anketa(self) -> None:
        kb = anketa_choice_keyboard(step="goal", options=[("Похудеть", "lose")])
        parsed = parse_callback(kb[0]["callback"])
        assert parsed is not None
        assert parsed["domain"] == "anketa"
        assert parsed["action"] == "choice"
        # ref encodes step + value as compound — same pattern as food
        # correct callbacks.
        assert parsed["ref"] == "goal:lose"
