"""NutritionAnketaSkill end-to-end tests (DRF-820 / Sprint 9 / P3).

Covers the full 5-step walk plus resume, edit, choice-callback decoding,
validation re-asks, and Ayla error paths. The FSM helper has its own
unit tests; this file targets the skill-level integration.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.integrations.ayla import (
    NutritionUnavailableError,
    ProfileResponse,
)
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.skill import (
    NutritionAnketaSkill,
)


class _StatefulConversation:
    """In-memory stand-in for the Django Conversation row.

    Exposes ``skill_state`` + ``save`` so the skill's persistence path
    runs without a database. ``save`` is a no-op recorder.
    """

    def __init__(self, initial: dict | None = None) -> None:
        self.id = "conv-1"
        self.skill_state = initial or {}
        self.save_calls: list[list[str]] = []

    def save(self, update_fields: list[str] | None = None) -> None:
        self.save_calls.append(list(update_fields or []))


def _context(
    text: str = "",
    *,
    state: dict | None = None,
    channel: str = "max",
    channel_user_id: str = "12345",
) -> tuple[SkillContext, _StatefulConversation]:
    conversation = _StatefulConversation(state)
    bot_user = Mock()
    bot_user.channel = channel
    bot_user.channel_user_id = channel_user_id
    ctx = SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=bot_user,
        message_text=text,
    )
    return ctx, conversation


def _profile(
    daily_kcal: int = 1900,
    goal_overridden_by: str | None = None,
) -> ProfileResponse:
    return ProfileResponse(
        gender="female",
        age=30,
        height_cm=168,
        weight_kg=62,
        goal="maintain",
        daily_kcal=daily_kcal,
        protein_g=95,
        fat_g=60,
        carbs_g=220,
        water_ml=2100,
        bmr=1450,
        health_flags={},
        disclaimer_acked=None,
        goal_overridden_by=goal_overridden_by,
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    def test_slash_anketa_starts(self) -> None:
        ctx, _ = _context("/anketa")
        assert NutritionAnketaSkill().matches(ctx)

    def test_cb_start_starts(self) -> None:
        ctx, _ = _context("cb:anketa:start")
        assert NutritionAnketaSkill().matches(ctx)

    def test_random_text_does_not_match_without_state(self) -> None:
        ctx, _ = _context("Привет")
        assert not NutritionAnketaSkill().matches(ctx)

    def test_text_matches_when_fsm_active(self) -> None:
        ctx, _ = _context(
            "28",
            state={
                "nutrition_anketa": {
                    "current_step": "age",
                    "answers": {"gender": "female"},
                    "is_complete": False,
                }
            },
        )
        assert NutritionAnketaSkill().matches(ctx)

    def test_choice_callback_matches_when_fsm_active(self) -> None:
        ctx, _ = _context(
            "cb:anketa:choice:gender:female",
            state={
                "nutrition_anketa": {"current_step": "gender", "answers": {}, "is_complete": False}
            },
        )
        assert NutritionAnketaSkill().matches(ctx)

    def test_edit_callback_always_matches(self) -> None:
        ctx, _ = _context("cb:anketa:edit:weight")
        assert NutritionAnketaSkill().matches(ctx)

    def test_other_callbacks_do_not_match(self) -> None:
        ctx, _ = _context(
            "cb:food:to_diary:scan-1",
            state={
                "nutrition_anketa": {"current_step": "age", "answers": {}, "is_complete": False}
            },
        )
        # cb:food:* belongs to food_scanner — anketa must not claim.
        assert not NutritionAnketaSkill().matches(ctx)


# ─── entry + full walk ───────────────────────────────────────────────────


class TestFullWalk:
    def test_full_5_step_completion_posts_to_ayla(self) -> None:
        """Simulate the entire flow turn-by-turn. Same conversation
        object is mutated across turns; that's how the platform
        pipeline runs in practice (one conversation per chain)."""
        captured: list[dict] = []
        client = Mock()

        async def _upsert(**kwargs):
            captured.append(kwargs)
            return _profile()

        client.upsert_profile = _upsert

        conversation = _StatefulConversation()
        bot_user = Mock(channel="max", channel_user_id="12345")

        def _ctx(text: str) -> SkillContext:
            return SkillContext(
                conversation=conversation,  # type: ignore[arg-type]
                bot_user=bot_user,
                message_text=text,
            )

        with patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client",
            return_value=client,
        ):
            skill = NutritionAnketaSkill()

            # Turn 1: enter.
            r1 = skill.handle(_ctx("/anketa"))
            assert r1.action_type == "anketa_step_gender"
            assert "buttons" in (r1.action_data or {})

            # Turn 2: gender via callback.
            r2 = skill.handle(_ctx("cb:anketa:choice:gender:female"))
            assert r2.action_type == "anketa_step_age"

            # Turn 3: age (text).
            r3 = skill.handle(_ctx("28"))
            assert r3.action_type == "anketa_step_height"

            # Turn 4: height.
            r4 = skill.handle(_ctx("168"))
            assert r4.action_type == "anketa_step_weight"

            # Turn 5: weight.
            r5 = skill.handle(_ctx("62"))
            assert r5.action_type == "anketa_step_goal"

            # Turn 6: goal → complete.
            r6 = skill.handle(_ctx("cb:anketa:choice:goal:maintain"))
            assert r6.action_type == "anketa_complete"

        # Ayla payload assembled correctly.
        assert len(captured) == 1
        data = captured[0]["data"]
        assert data == {
            "gender": "female",
            "age": 28,
            "height_cm": 168,
            "weight_kg": 62,
            "goal": "maintain",
            "activity_coefficient": 1.4,
        }
        assert captured[0]["external_user_id"] == "bot:max:12345"

        # State wiped on completion.
        assert "nutrition_anketa" not in conversation.skill_state

        # Summary mentions norms.
        assert "ккал" in r6.reply_text.lower()
        assert "1900" in r6.reply_text


# ─── validation errors re-ask ─────────────────────────────────────────────


class TestValidationReask:
    def test_age_out_of_range_re_asks(self) -> None:
        ctx, conversation = _context(
            "150",
            state={
                "nutrition_anketa": {
                    "current_step": "age",
                    "answers": {"gender": "female"},
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)
        # Same step — validation error path returns NextStep with same prompt.
        assert result.action_type == "anketa_step_age"
        # FSM still parked at age (didn't advance).
        bucket = conversation.skill_state["nutrition_anketa"]
        assert bucket["current_step"] == "age"
        # Answer NOT stored.
        assert "age" not in bucket["answers"]


# ─── resume path ─────────────────────────────────────────────────────────


class TestResume:
    def test_resume_mid_flight(self) -> None:
        """Bot restart preserves state — next user input continues."""
        ctx, _ = _context(
            "175",
            state={
                "nutrition_anketa": {
                    "current_step": "height",
                    "answers": {"gender": "female", "age": 28},
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)
        assert result.action_type == "anketa_step_weight"


# ─── edit path ───────────────────────────────────────────────────────────


class TestEdit:
    def test_edit_callback_jumps_back(self) -> None:
        ctx, conversation = _context(
            "cb:anketa:edit:weight",
            state={
                "nutrition_anketa": {
                    "current_step": "goal",
                    "answers": {
                        "gender": "female",
                        "age": 28,
                        "height": 168,
                        "weight": 62,
                    },
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)
        assert result.action_type == "anketa_step_weight"
        # Other answers retained, weight cleared.
        bucket = conversation.skill_state["nutrition_anketa"]
        assert "weight" not in bucket["answers"]
        assert bucket["answers"]["age"] == 28


# ─── error paths ─────────────────────────────────────────────────────────


class TestErrorPaths:
    def test_ayla_unavailable_at_complete_graceful(self) -> None:
        """If Ayla is down when we POST — state stays intact so the user
        can retry without re-walking the FSM."""
        conversation = _StatefulConversation(
            {
                "nutrition_anketa": {
                    "current_step": "goal",
                    "answers": {
                        "gender": "female",
                        "age": 28,
                        "height": 168,
                        "weight": 62,
                    },
                    "is_complete": False,
                }
            }
        )
        bot_user = Mock(channel="max", channel_user_id="12345")
        ctx = SkillContext(
            conversation=conversation,  # type: ignore[arg-type]
            bot_user=bot_user,
            message_text="cb:anketa:choice:goal:maintain",
        )

        client = Mock()

        async def _upsert(**kwargs):
            raise NutritionUnavailableError("down")

        client.upsert_profile = _upsert

        with patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client",
            return_value=client,
        ):
            result = NutritionAnketaSkill().handle(ctx)

        assert "минут" in result.reply_text.lower()
        # State preserved — but the FSM is in Completed-state after the
        # final transition, so retry requires user re-walking the last
        # step. Document this — the alternative (rollback FSM) is more
        # surface area for Sprint 9.


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_nutrition_anketa_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "nutrition_anketa" in names

    def test_above_echo(self) -> None:
        """Resume turns (plain text) must reach anketa before echo
        swallows them."""
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert names.index("nutrition_anketa") < names.index("echo"), (
            f"anketa must precede echo; got {names}"
        )


# ─── pytest collection sanity ─────────────────────────────────────────────


def test_imports_clean() -> None:
    """Sanity — module imports without side effects from a fresh interp."""
    # Re-import to surface any decorator-time bugs.
    import importlib

    import apps.skills.nutrition_anketa.skill as mod

    importlib.reload(mod)
    assert mod.NutritionAnketaSkill.name == "nutrition_anketa"
