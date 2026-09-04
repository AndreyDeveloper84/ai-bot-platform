"""DRF-1268 — nutrition skills as concierge tools on the global path.

Covers the two transfer layers:

- deterministic structured routing (``try_handle_structured_nutrition_turn``)
  — cb:* taps, /anketa, active-FSM answers, photo turns; free text must
  never be claimed here;
- model-callable tools (``execute_nutrition_tool`` + concierge wiring) —
  tool selection executes the same skill classes the registry would have.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, nutrition_global
from apps.orchestrator.concierge import _dispatch_tool, generate_concierge_reply
from apps.orchestrator.nutrition_global import (
    NUTRITION_TOOL_ACTIONS,
    NUTRITION_TOOL_SPECS,
    execute_nutrition_tool,
    is_structured_nutrition_turn,
    food_tap_labels,
    resolve_anketa_tap,
    resolve_food_tap,
    resolve_nutri_stop_tap,
    try_handle_structured_nutrition_turn,
)
from apps.skills.base import SkillResult

pytestmark = pytest.mark.django_db(transaction=True)


def _conversation(skill_state=None):
    return SimpleNamespace(skill_state=skill_state or {})


class TestStructuredPredicate:
    @pytest.mark.parametrize(
        "text",
        [
            "/anketa",
            "cb:anketa:start",
            "cb:anketa:choice:gender:male",
            "cb:food:diary",
            "cb:food:to_diary:42",
        ],
    )
    def test_structured_texts_claimed(self, text):
        assert is_structured_nutrition_turn(
            text=text, has_attachments=False, conversation=_conversation()
        )

    @pytest.mark.parametrize(
        "text",
        ["стакан воды", "борщ 300г", "болит спина", "хочу пройти анкету", "покажи мастеров"],
    )
    def test_free_text_never_claimed(self, text):
        assert not is_structured_nutrition_turn(
            text=text, has_attachments=False, conversation=_conversation()
        )

    def test_photo_only_turn_claimed(self):
        assert is_structured_nutrition_turn(
            text="", has_attachments=True, conversation=_conversation()
        )

    def test_active_fsm_claims_any_text(self):
        conversation = _conversation(skill_state={"nutrition_anketa": {"step": "age"}})
        assert is_structured_nutrition_turn(
            text="25", has_attachments=False, conversation=conversation
        )


class TestStructuredDispatch:
    """Order is load-bearing (apps/skills/apps.py): the cb:food:* family
    wins over an active anketa FSM, which claims ANY text while running."""

    def _fake_skill(self, name, matches):
        skill = Mock()
        skill.name = name
        skill.matches.side_effect = matches
        skill.handle.return_value = SkillResult(reply_text=f"handled by {name}")
        return skill

    def _dispatch(self, text, *, fakes, attachments=None, skill_state=None):
        by_name = {f.name: f for f in fakes}
        with (
            patch.object(nutrition_global, "_skill_by_name", side_effect=lambda n: by_name.get(n)),
            patch.object(
                nutrition_global, "_run_skill", side_effect=lambda skill, ctx: skill.handle(ctx)
            ),
        ):
            return try_handle_structured_nutrition_turn(
                text=text,
                attachments=attachments,
                bot_user=Mock(),
                conversation=_conversation(skill_state=skill_state),
                trace_id="t-1",
            )

    def test_free_text_returns_none_without_touching_skills(self):
        scanner = self._fake_skill("food_scanner", lambda ctx: True)
        result = self._dispatch("стакан воды", fakes=[scanner])
        assert result is None
        scanner.matches.assert_not_called()

    def test_cb_food_wins_over_active_anketa_fsm(self):
        scanner = self._fake_skill(
            "food_scanner", lambda ctx: ctx.message_text.startswith("cb:food:to_diary")
        )
        anketa = self._fake_skill("nutrition_anketa", lambda ctx: True)
        result = self._dispatch(
            "cb:food:to_diary:7",
            fakes=[scanner, anketa],
            skill_state={"nutrition_anketa": {"step": "age"}},
        )
        assert result is not None
        assert result.reply_text == "handled by food_scanner"
        anketa.handle.assert_not_called()

    def test_anketa_command_routes_to_anketa(self):
        anketa = self._fake_skill("nutrition_anketa", lambda ctx: ctx.message_text == "/anketa")
        result = self._dispatch("/anketa", fakes=[anketa])
        assert result is not None
        assert result.reply_text == "handled by nutrition_anketa"

    def test_no_match_returns_none(self):
        scanner = self._fake_skill("food_scanner", lambda ctx: False)
        result = self._dispatch("cb:food:unknown:x", fakes=[scanner])
        assert result is None


class TestExecuteNutritionTool:
    def test_unknown_tool_returns_none(self):
        assert (
            execute_nutrition_tool(
                "order_pizza", {}, bot_user=Mock(), conversation=Mock(), trace_id="t"
            )
            is None
        )

    def test_empty_text_returns_none(self):
        assert (
            execute_nutrition_tool(
                "log_water",
                {"drink_text": "  "},
                bot_user=Mock(),
                conversation=Mock(),
                trace_id="t",
            )
            is None
        )

    def test_parser_refusal_returns_none(self):
        # A real registry skill (water) with a phrase its parser rejects —
        # no network is reached: matches() screens first.
        result = execute_nutrition_tool(
            "log_water",
            {"drink_text": "привет, как дела у тебя сегодня"},
            bot_user=Mock(),
            conversation=Mock(),
            trace_id="t",
        )
        assert result is None

    def test_health_screening_executes_real_skill(self):
        # Network-free skill: the deterministic diagnostic reply must come
        # from the same class the per-tenant registry would have run.
        result = execute_nutrition_tool(
            "health_screening",
            {"symptom_text": "болит спина"},
            bot_user=Mock(),
            conversation=Mock(),
            trace_id="t",
        )
        assert result is not None
        assert result.reply_text


class TestToolSpecs:
    def test_four_nutrition_tools(self):
        assert {s["name"] for s in NUTRITION_TOOL_SPECS} == set(NUTRITION_TOOL_ACTIONS)
        assert len(NUTRITION_TOOL_SPECS) == 4

    def test_health_screening_declared_first(self):
        # DRF-358 T04 priority survives as declaration order.
        assert NUTRITION_TOOL_SPECS[0]["name"] == "health_screening"

    def test_dispatch_tool_maps_nutrition_call(self):
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="log_water", arguments='{"drink_text": "стакан воды"}')
        )
        result = _dispatch_tool(tool_call, None)
        assert result.action_type == "log_water"
        assert result.action_data["arguments"] == {"drink_text": "стакан воды"}

    def test_dispatch_tool_unknown_still_degrades(self):
        tool_call = SimpleNamespace(function=SimpleNamespace(name="book_now", arguments="{}"))
        result = _dispatch_tool(tool_call, None)
        assert result.action_type == "ask_clarification"
        assert result.action_data["reason"] == "unknown_tool:book_now"


def _router_returning(provider):
    router = Mock()
    router.get_provider.return_value = provider
    return router


class TestConciergeNutritionTurn:
    def _bot_user_and_conversation(self):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="drf1268-e2e-uid",
            chat_id="drf1268-e2e-chat",
        )
        conversation = resolve_active_global_conversation(bot_user)
        return bot_user, conversation

    def test_nutrition_tool_specs_reach_the_model(self, monkeypatch):
        captured: dict = {}

        async def _complete(messages, model: str = "", tools=None):
            captured["tools"] = tools
            return CompletionResult(text="ok")

        provider = AsyncMock()
        provider.complete.side_effect = _complete
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        tool_names = {t["name"] for t in captured["tools"]}
        assert NUTRITION_TOOL_ACTIONS <= tool_names

    def test_health_screening_tool_call_returns_skill_reply(self, monkeypatch):
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="health_screening",
                    arguments={"symptom_text": "болит спина"},
                )
            ],
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = self._bot_user_and_conversation()

        reply = generate_concierge_reply(
            "болит спина", bot_user=bot_user, conversation=conversation
        )

        assert reply.persisted is True
        # The deterministic screening text (the skill's own wording),
        # not a model improvisation.
        assert "Где именно болит" in reply.text

    def test_system_prompt_carries_nutrition_priority(self):
        prompt = concierge.build_concierge_system_prompt()
        assert "health_screening" in prompt
        assert "log_water" in prompt
        assert "start_nutrition_anketa" in prompt


class TestAnketaOnGlobalPath:
    """DRF-1225 core: the anketa FSM must run against the sentinel-scoped
    global conversation — the tenant_scope(sentinel) execution wrapper is
    what makes write_skill_state accept the global row."""

    def _bot_user_and_conversation(self):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id="drf1268-anketa-uid",
            chat_id="drf1268-anketa-chat",
        )
        conversation = resolve_active_global_conversation(bot_user)
        return bot_user, conversation

    def test_anketa_start_writes_fsm_state(self):
        bot_user, conversation = self._bot_user_and_conversation()

        result = try_handle_structured_nutrition_turn(
            text="/anketa",
            attachments=[],
            bot_user=bot_user,
            conversation=conversation,
            trace_id="t-anketa",
        )

        assert result is not None
        assert result.reply_text
        conversation.refresh_from_db()
        assert conversation.skill_state.get("nutrition_anketa")

    def test_anketa_resume_turn_claimed_by_fsm(self):
        bot_user, conversation = self._bot_user_and_conversation()
        try_handle_structured_nutrition_turn(
            text="/anketa",
            attachments=[],
            bot_user=bot_user,
            conversation=conversation,
            trace_id="t-anketa",
        )
        conversation.refresh_from_db()

        # An active FSM claims the free-text answer turn deterministically —
        # the concierge LLM never sees it.
        result = try_handle_structured_nutrition_turn(
            text="мужской",
            attachments=[],
            bot_user=bot_user,
            conversation=conversation,
            trace_id="t-anketa",
        )
        assert result is not None
        assert result.reply_text


class TestAnketaTapAsAHistoryTurn:
    """DRF-990 — чем тап анкеты является КАК РЕПЛИКА.

    Маршрутизация (класс выше) и персистенс — разные читатели одного
    события: там payload обязан доехать до навыка нетронутым, здесь он не
    должен выглядеть как то, что человек написал словами.
    """

    def test_choice_tap_becomes_the_label_the_person_pressed(self):
        from apps.skills.nutrition_anketa.fsm import GENDER_CHOICES, GOAL_CHOICES

        assert (
            resolve_anketa_tap("cb:anketa:choice:gender:female").history_text
            == (GENDER_CHOICES["female"])
        )
        assert (
            resolve_anketa_tap("cb:anketa:choice:goal:gain").history_text == (GOAL_CHOICES["gain"])
        )

    def test_the_label_table_is_the_keyboard_own_table(self):
        """Сторож сторожа: метки берутся из ТОЙ ЖЕ функции, что строит кнопки.

        Если таблица опустеет, проверка выше стала бы «None == None».
        """
        from apps.skills.nutrition_anketa.fsm import CHOICE_STEPS, choice_keyboard_options

        assert CHOICE_STEPS
        for step in CHOICE_STEPS:
            options = choice_keyboard_options(step)
            assert options, step
            assert not [(lbl, slug) for lbl, slug in options if not lbl or not slug], step

    @pytest.mark.parametrize("step", ["age", "height", "weight"])
    def test_text_input_steps_have_no_label_to_substitute(self, step):
        """Шаг без клавиатуры не может прийти как ``choice`` — и не подставляется."""
        tap = resolve_anketa_tap(f"cb:anketa:choice:{step}:whatever")
        assert tap is not None
        assert tap.history_text is None

    @pytest.mark.parametrize(
        "payload",
        ["cb:anketa:start", "cb:anketa:edit:weight", "cb:anketa:edit:gender"],
    )
    def test_navigation_taps_are_not_a_reply_at_all(self, payload):
        tap = resolve_anketa_tap(payload)
        assert tap is not None
        assert tap.history_text is None

    @pytest.mark.parametrize(
        "payload",
        [
            "cb:anketa:choice:gender:retired",  # значение сняли
            "cb:anketa:choice:pace:slow",  # шаг, которого в FSM нет
            "cb:anketa:whatever",  # кнопка из будущего/прошлого
        ],
    )
    def test_unrecognised_but_well_formed_payload_never_reaches_history(self, payload):
        """Выдумывать за человека фразу нечем, сырой ``cb:`` — запрещён."""
        tap = resolve_anketa_tap(payload)
        assert tap is not None
        assert tap.history_text is None

    @pytest.mark.parametrize(
        "text",
        [
            "cb:anketa: это я просто так написала",
            "смотри что нашла: cb:anketa:start",
            "Женский",
            "",
        ],
    )
    def test_plain_text_is_left_alone(self, text):
        """None — «это не тап»: вызывающий пишет в историю ровно то, что есть."""
        assert resolve_anketa_tap(text) is None

    def test_other_cb_families_are_not_claimed(self):
        for payload in ("cb:food:diary", "cb:book:confirm:1", "cb:catalog:services:x"):
            assert resolve_anketa_tap(payload) is None


class TestFoodTapAsAHistoryTurn:
    """DRF-990, продолжение — то же самое для ``cb:food:*``.

    Буквальный близнец анкеты: тот же вход
    (``_STRUCTURED_CALLBACK_PREFIXES``), та же маршрутизация по payload'у.
    Разница только в источнике метки — три строителя клавиатур вместо
    таблицы шагов.
    """

    SCAN = "scan-1"

    def test_every_shipped_food_button_resolves_to_its_own_label(self):
        """Таблица не переписана сюда — она читается из самой клавиатуры.

        Кнопка, добавленная через месяц без метки, падает здесь, а не в
        истории у человека в чате.
        """
        labels = food_tap_labels(self.SCAN)
        assert labels, "клавиатуры еды пусты — проверка ниже ни о чём"
        for payload, label in labels.items():
            tap = resolve_food_tap(payload)
            assert tap is not None, payload
            assert tap.history_text == label, payload

    def test_the_confirmation_and_the_rejection_are_both_kept(self):
        """«✅ В дневник» и «❌ Не то» — подтверждение и поправка о себе."""
        labels = food_tap_labels(self.SCAN)

        assert (
            resolve_food_tap(f"cb:food:to_diary:{self.SCAN}").history_text
            == (labels[f"cb:food:to_diary:{self.SCAN}"])
        )
        assert (
            resolve_food_tap(f"cb:food:reject:{self.SCAN}").history_text
            == (labels[f"cb:food:reject:{self.SCAN}"])
        )

    def test_the_refless_pair_resolves_by_the_same_machinery(self):
        """``cb:food:diary`` / ``cb:food:typo`` идут без ``scan_id``."""
        assert resolve_food_tap("cb:food:diary").history_text == "📔 В дневник"
        assert resolve_food_tap("cb:food:typo").history_text == "❌ Опечатка"

    @pytest.mark.parametrize(
        "payload",
        [
            "cb:food:correct:nope:scan-1",  # поля нет в клавиатуре
            "cb:food:to_diary",  # без scan_id клавиатура такого не выкладывает
            "cb:food:whatever",  # кнопка из будущего/прошлого
        ],
    )
    def test_unrecognised_but_well_formed_payload_never_reaches_history(self, payload):
        """Выдумывать за человека фразу нечем, сырой ``cb:`` — запрещён."""
        tap = resolve_food_tap(payload)
        assert tap is not None
        assert tap.history_text is None

    @pytest.mark.parametrize(
        "text",
        [
            "cb:food: это я просто так написала",
            "смотри что нашла: cb:food:diary",
            "борщ 300г",
            "",
        ],
    )
    def test_plain_text_is_left_alone(self, text):
        """None — «это не тап»: вызывающий пишет в историю ровно то, что есть."""
        assert resolve_food_tap(text) is None

    def test_other_cb_families_are_not_claimed(self):
        for payload in (
            "cb:anketa:start",
            "cb:welcome:consent_yes",
            "cb:book:confirm:1",
            "cb:catalog:services:x",
        ):
            assert resolve_food_tap(payload) is None

    def test_the_scan_id_itself_never_reaches_history(self):
        """В историю идёт метка, а не id распознавания."""
        tap = resolve_food_tap("cb:food:to_diary:abc-xyz-789")
        assert tap is not None
        assert tap.history_text is not None
        assert "abc-xyz-789" not in tap.history_text


class TestNutriStopTapAsAHistoryTurn:
    """DRF-1468 — тап «Не присылать» (``cb:nutri:stop:*``).

    Кнопка отписки — высказывание кнопкой, но фразы за ней нет: метка
    одна на все поверхности, а смысл тапа целиком в payload'е. В историю
    не идёт ничего — ход виден по ответу-подтверждению бота, ровно как у
    навигационных тапов анкеты и ``cb:catalog:*``.
    """

    @pytest.mark.parametrize("surface", ["report", "water"])
    def test_a_stop_tap_never_reaches_history(self, surface):
        tap = resolve_nutri_stop_tap(f"cb:nutri:stop:{surface}")
        assert tap is not None
        assert tap.history_text is None

    def test_an_unknown_but_well_formed_surface_is_still_a_tap(self):
        """Старая кнопка (поверхность, которой больше нет) — тоже не фраза."""
        tap = resolve_nutri_stop_tap("cb:nutri:stop:hint")
        assert tap is not None
        assert tap.history_text is None

    @pytest.mark.parametrize(
        "text",
        [
            "cb:nutri:stop",  # без поверхности — не наша кнопка
            "cb:nutri:stop:вода",  # набрано руками — не payload кнопки
            "cb:nutri:stop:report extra",
            "cb:nutri:delete:report",  # другой глагол
            "cb:food:diary",
            "не присылать",
            "",
        ],
    )
    def test_anything_else_is_not_ours(self, text):
        assert resolve_nutri_stop_tap(text) is None
