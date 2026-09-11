"""NutritionAnketaSkill end-to-end tests (DRF-820 / Sprint 9 / P3).

Covers the full 5-step walk plus resume, edit, choice-callback decoding,
validation re-asks, and Ayla error paths. The FSM helper has its own
unit tests; this file targets the skill-level integration.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.consent.personal_calculation import ConsentAttestation
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
        # DRF-1686 (§6): без названного происхождения DTO обнуляет ориентиры;
        # этот профиль — посчитанный, и тесты ниже проверяют именно числа.
        targets_source="ayla_calculated",
    )


#: DRF-1658: с этого PR тело POST профиля не собирается без утверждения о
#: согласии (граница каталога #324). Тесты, доходящие до POST, объявляют
#: предусловие «согласие на расчёт дано, версия такая-то» явно — а не
#: через autouse, чтобы было видно, какой тест зависит от согласия.
_ATTESTATION = ConsentAttestation(
    type="personal_calculation", document_version="personal-calculation-v1"
)
_ATTESTATION_LOOKUP = "apps.consent.personal_calculation.current_attestation"


def _consent_granted():
    return patch(_ATTESTATION_LOOKUP, return_value=_ATTESTATION)


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

        with (
            patch(
                "apps.skills.nutrition_anketa.skill.get_nutrition_client",
                return_value=client,
            ),
            _consent_granted(),
        ):
            skill = NutritionAnketaSkill()

            # Turn 1: enter.
            r1 = skill.handle(_ctx("/anketa"))
            assert r1.action_type == "anketa_step_gender"
            assert "buttons" in (r1.action_data or {})

            # Turn 2: gender via callback.
            r2 = skill.handle(_ctx("cb:anketa:choice:gender:female"))
            assert r2.action_type == "anketa_step_age"

            # Turn 3: age (text) → screening, not height. The §7.1 gates
            # come before the anthropometry questions on purpose.
            r3 = skill.handle(_ctx("28"))
            assert r3.action_type == "anketa_step_screening"

            # Turn 4: screening — nothing declared, flow continues.
            r4 = skill.handle(_ctx("cb:anketa:choice:screening:none"))
            assert r4.action_type == "anketa_step_height"

            # Turn 5: height.
            r5 = skill.handle(_ctx("168"))
            assert r5.action_type == "anketa_step_weight"

            # Turn 6: weight.
            r6w = skill.handle(_ctx("62"))
            assert r6w.action_type == "anketa_step_goal"

            # Turn 7: goal → complete.
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
            # DRF-1658: утверждение о согласии в форме границы #324.
            "consent": {
                "type": "personal_calculation",
                "document_version": "personal-calculation-v1",
            },
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

        with (
            patch(
                "apps.skills.nutrition_anketa.skill.get_nutrition_client",
                return_value=client,
            ),
            _consent_granted(),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        assert "минут" in result.reply_text.lower()
        # State preserved — but the FSM is in Completed-state after the
        # final transition, so retry requires user re-walking the last
        # step. Document this — the alternative (rollback FSM) is more
        # surface area for Sprint 9.


# ─── stop scenarios (§7.1) ───────────────────────────────────────────────


def _client_that_must_not_be_called() -> Mock:
    """Ayla client whose every call fails the test.

    A stop scenario must not reach the network at all. Asserting
    ``not called`` afterwards would pass just as well if the code called
    a *different* method, so the guard is on the client itself.
    """
    client = Mock()

    async def _boom(**kwargs):  # pragma: no cover - the point is not to run
        raise AssertionError(f"Ayla was called during a stop scenario: {kwargs}")

    client.upsert_profile = _boom
    return client


class TestStopScenarios:
    """Owner decision §7.1 — no automatic calculation, diary intact."""

    def test_minor_stops_before_weight_is_asked(self) -> None:
        ctx, conversation = _context(
            "15",
            state={
                "nutrition_anketa": {
                    "current_step": "age",
                    "answers": {"gender": "female"},
                    "is_complete": False,
                }
            },
        )
        with patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client",
            return_value=_client_that_must_not_be_called(),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_stop"
        assert result.meta["reply_kind"] == "anketa_stop_minor"
        # The flow ends here: no further step is asked, so neither height
        # nor weight is collected. Asserting on the absence of the words
        # would be worse than useless — «просто» contains «рост».
        assert not result.action_type.startswith("anketa_step_")
        # The diary staying open is the decision, so it must be said.
        assert "дневник" in result.reply_text.lower()
        # State wiped — a parked FSM would re-ask on the next message.
        assert "nutrition_anketa" not in conversation.skill_state

    def test_adult_boundary_continues(self) -> None:
        """18 passes. Without this the gate could be `<= 18` and still look
        correct: every failing case would be a minor, and no test would
        notice that adults were being refused too."""
        ctx, conversation = _context(
            "18",
            state={
                "nutrition_anketa": {
                    "current_step": "age",
                    "answers": {"gender": "female"},
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_step_screening"
        assert conversation.skill_state["nutrition_anketa"]["answers"]["age"] == 18

    def test_declared_condition_stops_and_reaches_no_network(self) -> None:
        ctx, conversation = _context(
            "cb:anketa:choice:screening:pregnancy_nursing",
            state={
                "nutrition_anketa": {
                    "current_step": "screening",
                    "answers": {"gender": "female", "age": 30},
                    "is_complete": False,
                }
            },
        )
        with patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client",
            return_value=_client_that_must_not_be_called(),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_stop"
        assert result.meta["reply_kind"] == "anketa_stop_screening"
        assert "дневник" in result.reply_text.lower()
        assert "nutrition_anketa" not in conversation.skill_state

    def test_declared_condition_is_not_stored_anywhere(self) -> None:
        """Special-category answer (152-ФЗ): decided, then dropped.

        Checks the whole persisted blob rather than one key — the answer
        must not survive under any name, including a stray copy in
        ``answers`` left behind by a future refactor.
        """
        ctx, conversation = _context(
            "cb:anketa:choice:screening:eating_disorder",
            state={
                "nutrition_anketa": {
                    "current_step": "screening",
                    "answers": {"gender": "female", "age": 30},
                    "is_complete": False,
                }
            },
        )
        with patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client",
            return_value=_client_that_must_not_be_called(),
        ):
            result = NutritionAnketaSkill().handle(ctx)

        # Exact, not «not in»: an emptiness check would pass just as well
        # if the whole flow had collapsed, and this test would then be
        # proving that nothing happened rather than that nothing was kept.
        # The stop path clears the bucket, so the whole blob must be {}.
        assert conversation.skill_state == {}

        # Nor echoed back into the chat log by the reply itself. Presence
        # first — otherwise an empty reply passes the negative.
        assert "дневник" in result.reply_text.lower()
        assert "расстройств" not in result.reply_text.lower()

        # Nor smuggled out through the action payload the channel renders.
        assert (result.action_data or {})["reason"] == "screening"
        assert "eating_disorder" not in repr(result.action_data)

    def test_clear_screening_answer_is_not_persisted(self) -> None:
        """«Ничего из этого» is not kept either.

        It is a health statement about the person and nothing downstream
        reads it, so storing it would be storage without a purpose.
        """
        ctx, conversation = _context(
            "cb:anketa:choice:screening:none",
            state={
                "nutrition_anketa": {
                    "current_step": "screening",
                    "answers": {"gender": "female", "age": 30},
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_step_height"
        bucket = conversation.skill_state["nutrition_anketa"]
        # Presence first, on the same data: the answers that ARE needed
        # survive. Without this the negative below would go green on a
        # wiped bucket, i.e. on the flow being broken.
        assert bucket["answers"]["age"] == 30
        assert bucket["answers"]["gender"] == "female"
        assert "screening" not in bucket["answers"]

    def test_unparsable_screening_answer_re_asks_instead_of_stopping(self) -> None:
        """A validation error is not a declaration.

        The stop check reads ``answers`` after the transition; on a re-ask
        nothing was stored, so a naive check would see the *previous*
        answer to this step and stop on an input the person never gave.
        """
        ctx, conversation = _context(
            "ага",
            state={
                "nutrition_anketa": {
                    "current_step": "screening",
                    "answers": {"gender": "female", "age": 30, "screening": "condition"},
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_step_screening"
        assert conversation.skill_state["nutrition_anketa"]["current_step"] == "screening"

    def test_stop_offers_the_diary_it_promises(self) -> None:
        """The branch says the diary stays open, so it has to open it."""
        ctx, _conversation = _context(
            "15",
            state={
                "nutrition_anketa": {
                    "current_step": "age",
                    "answers": {"gender": "female"},
                    "is_complete": False,
                }
            },
        )
        result = NutritionAnketaSkill().handle(ctx)

        buttons = (result.action_data or {}).get("buttons") or []
        assert buttons, "stop scenario left the person with no next move"


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


# ─── карточка норм: ноль не печатается как ориентир ───────────────────────


class TestSummaryCardShowsOnlyRealTargets:
    """Ноль в поле ориентира — отсутствие, а не «ориентир ноль» (§82, §85).

    Карточка печатала пять строк безусловно, и после снятия формулы воды
    выдавала «💧 Вода: 0 мл», а человеку без веса — ещё и «🔥 Калории:
    0 ккал/день». Ни одна из пяти величин не бывает нулём у живого
    человека, поэтому такая карточка не пустая, а лживая — и лжёт сразу
    после слов «Готово, рассчитала твои нормы».
    """

    def test_a_full_calculation_still_shows_every_row(self) -> None:
        """POSITIVE ВПЕРЕДИ: карточка не онемела, строки на месте."""
        from apps.skills.nutrition_anketa.skill import _format_summary

        text = _format_summary(_profile())
        assert "Готово, рассчитала твои нормы:" in text
        assert "🔥 Калории: 1900 ккал/день" in text
        assert "💧 Вода: 2100 мл" in text

    def test_a_zero_row_is_dropped_not_printed(self) -> None:
        from dataclasses import replace

        from apps.skills.nutrition_anketa.skill import _format_summary

        text = _format_summary(replace(_profile(), water_ml=0))
        # PRESENCE ВПЕРЕДИ: карточка отрисована и остальные строки на
        # месте — снимается строка без значения, а не карточка целиком.
        assert "🔥 Калории: 1900 ккал/день" in text
        # ABSENCE: воды нет вовсе — ни подписи, ни нуля.
        assert "Вода" not in text
        assert "0 мл" not in text

    def test_nothing_computed_does_not_pretend_to_be_a_calculation(self) -> None:
        """Человек без веса: расчёта нет, и карточка это признаёт."""
        from dataclasses import replace

        from apps.skills.nutrition_anketa.skill import _format_summary

        empty = replace(
            _profile(),
            daily_kcal=0,
            protein_g=0,
            fat_g=0,
            carbs_g=0,
            water_ml=0,
        )
        text = _format_summary(empty)
        # PRESENCE ВПЕРЕДИ: ответ не пуст — человеку сказано, что
        # дневник работает. Без этого отрицания ниже прошли бы и на
        # пустой строке.
        assert "Дневник готов" in text
        # ABSENCE: расчётом карточка не притворяется и нолей не печатает.
        assert "рассчитала твои нормы" not in text
        assert "0" not in text


# ─── карточка норм: методика и входы показываются человеку (§5.1) ───────────


from dataclasses import replace  # noqa: E402


class TestSummaryCardShowsMethodAndInputs:
    """Решение владельца 11.09.2026 §5.1: «методика и использованные данные
    показываются человеку». Карточка после анкеты печатает, по какой
    методике и от каких ответов человека посчитаны нормы — из снимка,
    который каталог прислал вместе с происхождением (PR #362), а не из
    текущих полей профиля.

    Оговорка о предмете: после #1523 анкета на пилоте закрыта fail-closed
    до экрана согласия, то есть сегодня карточку не увидит никто. Здесь
    стережётся МЕХАНИЗМ, а не наблюдение.
    """

    _SNAPSHOT = {
        "gender": "female",
        "age": 30,
        "height_cm": 168,
        "weight_kg": 62.0,
        "activity_coefficient": 1.375,
        "goal": "maintain",
        "pace": "moderate",
    }

    def test_method_and_inputs_line_is_printed_from_the_snapshot(self) -> None:
        from apps.skills.nutrition_anketa.skill import _format_summary

        profile = replace(
            _profile(),
            targets_method_versions={"calories": "mifflin_st_jeor_v1"},
            targets_input_snapshot=self._SNAPSHOT,
        )
        text = _format_summary(profile)
        assert "Считала по методике Миффлин — Сан Жеор, версия 1 от твоих данных:" in text
        for fact in (
            "пол — женский",
            "возраст — 30",
            "рост — 168 см",
            "вес — 62 кг",
            "активность — 1.375",
            "цель — поддерживать",
            "темп — средний",
        ):
            assert fact in text, fact
        # POSITIVE: строки норм на месте — методика добавлена, не заменила их.
        assert "🔥 Калории: 1900 ккал/день" in text

    def test_snapshot_values_win_over_profile_fields(self) -> None:
        """Печатаются входы РАСЧЁТА, а не текущие поля профиля.

        Если человек между расчётом и показом поменял вес, карточка обязана
        объяснять число тем весом, из которого оно посчитано.
        """
        from apps.skills.nutrition_anketa.skill import _format_summary

        profile = replace(
            _profile(),
            weight_kg=70,
            targets_method_versions={"calories": "mifflin_st_jeor_v1"},
            targets_input_snapshot=self._SNAPSHOT,
        )
        text = _format_summary(profile)
        assert "вес — 62 кг" in text
        assert "вес — 70 кг" not in text

    def test_empty_snapshot_prints_no_method_line(self) -> None:
        """Нет снимка — нет строки. «От твоих данных» без данных — выдумка."""
        from apps.skills.nutrition_anketa.skill import _format_summary

        text = _format_summary(_profile())
        # POSITIVE впереди: карточка отрисована, строки норм на месте —
        # иначе «строки нет» ничего не доказывает (negative_assert_guard).
        assert "🔥 Калории: 1900 ккал/день" in text
        assert "от твоих данных" not in text
        assert "Считала" not in text

    def test_unknown_method_version_is_printed_as_is(self) -> None:
        """Подписи, которой каталог не давал, не изготавливаем."""
        from apps.skills.nutrition_anketa.skill import _format_summary

        profile = replace(
            _profile(),
            targets_method_versions={"calories": "mifflin_st_jeor_v2"},
            targets_input_snapshot=self._SNAPSHOT,
        )
        text = _format_summary(profile)
        assert "Считала по методике mifflin_st_jeor_v2 от твоих данных:" in text


# ─── §5.1: предложение показывается как предложение и подтверждается ──────


def _proposed_profile(**over) -> ProfileResponse:
    """Профиль с ``ayla_proposed``: DTO обнуляет числа, они живут в ``raw``."""
    raw = {
        "norms": {
            "daily_kcal": 1650,
            "daily_protein_g": 100,
            "daily_fat_g": 55,
            "daily_carbs_g": 190,
        },
        "overrides_applied": [],
        "targets_provenance": {
            "source": "ayla_proposed",
            "method_versions": {"calories": "mifflin_st_jeor_v1"},
        },
    }
    raw.update(over.pop("raw", {}))
    kwargs = dict(
        gender="female",
        age=30,
        height_cm=168,
        weight_kg=62,
        goal="maintain",
        daily_kcal=1650,
        protein_g=100,
        fat_g=55,
        carbs_g=190,
        water_ml=None,
        bmr=1400,
        health_flags={},
        disclaimer_acked=None,
        goal_overridden_by=None,
        targets_source="ayla_proposed",
        targets_method_versions={"calories": "mifflin_st_jeor_v1"},
        targets_input_snapshot={
            "gender": "female",
            "age": 30,
            "height_cm": 168,
            "weight_kg": 62.0,
            "activity_coefficient": 1.375,
            "goal": "maintain",
            "pace": "moderate",
        },
        raw=raw,
    )
    kwargs.update(over)
    return ProfileResponse(**kwargs)


class TestProposalCard:
    """Решение владельца 11.09.2026 §5.1: результат становится шагом только
    после подтверждения. Каталог (#369) отдаёт расчёт как ``ayla_proposed``;
    бот обязан показать его КАК ПРЕДЛОЖЕНИЕ с кнопкой подтверждения — а не
    как «ориентиров нет» (так прочитал бы инвариант DTO без этой правки).

    Оговорка о предмете: анкета на пилоте закрыта fail-closed до экрана
    согласия — предложений сегодня не получит никто; стережём механизм.
    """

    def test_proposal_is_rendered_from_raw_with_the_method_line(self) -> None:
        from apps.skills.nutrition_anketa.skill import _format_summary

        profile = _proposed_profile()
        assert profile.daily_kcal is None  # инвариант DTO держится
        text = _format_summary(profile)
        assert text.startswith("Предлагаю ориентиры — посмотри и подтверди:")
        assert "🔥 Калории: 1650 ккал/день" in text
        assert "🍗 Белок: 100 г" in text
        assert "Считала по методике Миффлин — Сан Жеор, версия 1 от твоих данных:" in text
        assert "пока ты его не подтвердишь, в дневнике оно не действует" in text
        assert "Готово, рассчитала твои нормы" not in text
        assert "Дневных ориентиров пока не считаю" not in text

    def test_confirm_chip_comes_first_only_for_a_proposal(self) -> None:
        from apps.skills.nutrition_anketa.skill import CB_CONFIRM_TARGETS, _post_anketa_chips

        chips = _post_anketa_chips(_proposed_profile())
        assert chips[0]["callback"] == CB_CONFIRM_TARGETS
        assert chips[0]["label"] == "✅ Подтвердить ориентиры"
        # Без предложения кнопки нет — она обещала бы действие без предмета.
        for profile in (_profile(), None):
            assert all(c["callback"] != CB_CONFIRM_TARGETS for c in _post_anketa_chips(profile))

    def test_health_factor_refusal_is_named_not_silent(self) -> None:
        from apps.skills.nutrition_anketa.skill import _format_summary

        profile = _proposed_profile(
            targets_source="none",
            raw={
                "norms": {},
                "overrides_applied": [
                    {"reason": "health_factor_pregnant"},
                    {"reason": "health_factor_minor"},
                ],
                "targets_provenance": {"source": "none", "method_versions": {}},
            },
        )
        text = _format_summary(profile)
        assert text.startswith("Норму не считаю: при беременности, возрасте до 18 лет")
        assert "Дневник и вода работают как раньше" in text
        assert "ккал" not in text  # ни одного числа
        assert "Дневных ориентиров пока не считаю" not in text  # не безымянный отказ

    def test_matches_the_confirm_callback(self) -> None:
        from apps.skills.nutrition_anketa.skill import CB_CONFIRM_TARGETS

        ctx, _ = _context(CB_CONFIRM_TARGETS)
        assert NutritionAnketaSkill().matches(ctx)


class TestConfirmTargetsHandler:
    def _run(self, confirm):
        from apps.skills.nutrition_anketa.skill import CB_CONFIRM_TARGETS

        client = Mock()
        client.confirm_targets = confirm
        ctx, _ = _context(CB_CONFIRM_TARGETS)
        with patch("apps.skills.nutrition_anketa.skill.get_nutrition_client", return_value=client):
            return NutritionAnketaSkill().handle(ctx)

    def test_confirmed_shows_the_acting_card_and_no_confirm_chip(self) -> None:
        from apps.skills.nutrition_anketa.skill import CB_CONFIRM_TARGETS

        async def _confirm(**kwargs):
            assert kwargs["external_user_id"]
            return _profile(daily_kcal=1650), "confirmed"

        result = self._run(_confirm)
        assert result.action_type == "anketa_targets_confirmed"
        assert result.reply_text.startswith("Ориентиры подтверждены — теперь действуют в дневнике.")
        assert "🔥 Калории: 1650 ккал/день" in result.reply_text
        assert (result.action_data or {})["outcome"] == "confirmed"
        chips = (result.action_data or {})["buttons"]
        assert all(c["callback"] != CB_CONFIRM_TARGETS for c in chips)

    def test_already_confirmed_is_said_without_a_second_confirmation(self) -> None:
        async def _confirm(**kwargs):
            return _profile(), "already_confirmed"

        result = self._run(_confirm)
        assert result.reply_text.startswith("Ориентиры уже были подтверждены")

    def test_nothing_to_confirm_answers_by_source(self) -> None:
        from apps.integrations.ayla.nutrition_client import NothingToConfirmError

        async def _confirm(**kwargs):
            raise NothingToConfirmError("user_entered")

        result = self._run(_confirm)
        assert result.action_type == "anketa_confirm_targets_nothing"
        assert "заданы тобой вручную" in result.reply_text
        assert (result.action_data or {})["targets_source"] == "user_entered"

    def test_ayla_down_is_the_common_fallback(self) -> None:
        from apps.integrations.ayla import NutritionUnavailableError

        async def _confirm(**kwargs):
            raise NutritionUnavailableError("circuit_open")

        result = self._run(_confirm)
        assert result.meta["reply_kind"] == "anketa_ayla_down"
