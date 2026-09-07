"""DRF-1542 — вето скрининга считается по словам ЧЕЛОВЕКА, а не модели.

## Что было

``execute_nutrition_tool`` строил контекст проверки из аргумента, который
подставила МОДЕЛЬ::

    text = str(args.get("symptom_text") or "").strip()   # ← вывод модели
    context = _build_context(message_text=text, ...)
    if not skill.matches(context):
        return None

Модель писала в ``symptom_text`` «болит поясница», классификатор её
матчил, навык отвечал — **даже на «Что ты понимаешь?»**. Вето не может
наложить вето на того, кто его породил. Замер на боевом контуре, тот же
``classify()`` на настоящих репликах пяти ходов::

    '…тянет поясницу'                        -> SOFT
    'После тренировки'                       -> NONE
    'Поясница'                               -> NONE
    'Ну и как тебе донести, что болит спина?'-> SOFT
    'Что ты понимаешь?'                      -> NONE

Три хода из пяти классификатор честно вернул ``NONE`` — а ответ пришёл
всё равно.

## Что здесь закреплено

Докстринг ``execute_nutrition_tool`` всё это время обещал дословно:
«*The user's own phrase is passed through as ``message_text``*». Код
этого не делал. Тесты ниже держат код у его собственного описания —
это не новое поведение.

Сужено ровно до ``health_screening``:
:class:`TestTheNeighboursAreUntouched` — стража на то, что ``log_water``
и ``clarify_food_entry`` остались судиться по фразе модели, и довод
почему (нормализация «и водички дёрнул стакан» → «стакан воды» их
парсерам помогает, а подмена на живую реплику их бы сузила).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, nutrition_global
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.nutrition_global import execute_nutrition_tool
from apps.skills.base import SkillResult
from apps.skills.health_screening.skill import RED_FLAG_REPLY, SOFT_PAIN_REPLY

pytestmark = pytest.mark.django_db(transaction=True)

# Пять реплик человека из боевого диалога 06.09, в том же порядке.
LIVE_TURNS = (
    "Что-то тянет поясницу",
    "После тренировки",
    "Поясница",
    "Ну и как тебе донести, что болит спина?",
    "Что ты понимаешь?",
)

# То, что модель клала в symptom_text каждый из пяти раз.
MODEL_SYMPTOM_TEXT = "болит поясница"

# Собственный текст модели, произведённый рядом с вызовом инструмента.
# Именно он должен доехать до человека, когда вето сработало.
MODEL_OWN_TEXT = "Поясница после нагрузки — понял. Посмотрю, кто рядом с тобой этим занимается."


def _bot_user_and_conversation(suffix: str):
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=f"drf1542-{suffix}-uid",
        chat_id=f"drf1542-{suffix}-chat",
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _screening_call(text: str = MODEL_OWN_TEXT, symptom: str = MODEL_SYMPTOM_TEXT):
    return CompletionResult(
        text=text,
        tool_calls=[
            ToolCall(id="t1", name="health_screening", arguments={"symptom_text": symptom})
        ],
        prompt_tokens=10,
        completion_tokens=5,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )


def _router_returning(provider):
    router = Mock()
    router.get_provider.return_value = provider
    return router


class TestTheVetoIsNoLongerTautological:
    """Вето по реплике человека — на одном и том же вызове инструмента."""

    def _run(self, *, message_text: str, symptom: str = MODEL_SYMPTOM_TEXT):
        return execute_nutrition_tool(
            "health_screening",
            {"symptom_text": symptom},
            bot_user=SimpleNamespace(id="bu"),
            conversation=SimpleNamespace(id="c", skill_state={}),
            trace_id="t-1542",
            message_text=message_text,
        )

    def test_model_symptom_text_not_matching_the_person_words_is_vetoed(self) -> None:
        """Пятый ход боевого диалога: «Что ты понимаешь?» — не жалоба."""

        assert self._run(message_text="Что ты понимаешь?") is None

    @pytest.mark.parametrize("text", ["После тренировки", "Поясница"])
    def test_the_two_other_none_turns_are_vetoed_too(self, text: str) -> None:
        assert self._run(message_text=text) is None

    def test_the_persons_own_complaint_still_reaches_the_screening(self) -> None:
        """Положительная стража на тех же данных (DRF-1411, DRF-358 T04).

        Тот же вызов, тот же ``symptom_text``; меняется только реплика
        человека. Без этого «вето срабатывает» зеленело бы и на вето,
        которое срабатывает всегда.
        """

        result = self._run(message_text=LIVE_TURNS[0])

        assert result is not None
        assert result.reply_text == SOFT_PAIN_REPLY

    def test_a_red_flag_in_the_persons_words_survives_a_model_paraphrase(self) -> None:
        """Навык исполняется по словам человека, а не по пересказу.

        Человек: «онемела рука» (RED_FLAG). Модель пересказывает как
        «болит рука» (SOFT). Считали бы по модели — человек получил бы
        диагностические вопросы вместо «сначала к врачу».
        """

        result = self._run(message_text="Болит спина и онемела рука", symptom="болит рука")

        assert result is not None
        assert result.reply_text == RED_FLAG_REPLY

    def test_an_empty_person_turn_is_vetoed(self) -> None:
        """Фото без подписи: симптома в этом ходу человек не называл."""

        assert self._run(message_text="") is None


class TestTheNeighboursAreUntouched:
    """``log_water`` и ``clarify_food_entry`` судятся по фразе МОДЕЛИ.

    Объём вето сужен до скрининга намеренно (см. тело PR): у соседей
    парсеры разбирают грамматику, и нормализация модели им помогает.
    Реплика человека «и водички дёрнул стакан» их парсерам не по зубам —
    подставить её значило бы сузить соседей там, где они работают.

    Проверяется не «ответ пришёл», а ЧЬЯ фраза доехала до навыка:
    ``_run_skill`` подменён, чтобы не трогать запись в дневник (это I/O
    к Ayla, к предмету тикета отношения не имеющее).
    """

    def _seen_message_text(self, name: str, args: dict, *, message_text: str) -> str | None:
        seen: dict[str, str] = {}

        def _spy(skill, ctx):
            seen["text"] = ctx.message_text
            return SkillResult(reply_text="ok")

        with patch.object(nutrition_global, "_run_skill", side_effect=_spy):
            result = execute_nutrition_tool(
                name,
                args,
                bot_user=Mock(),
                conversation=SimpleNamespace(id="c", skill_state={}),
                trace_id="t-1542",
                message_text=message_text,
            )
        if result is None:
            return None
        return seen.get("text")

    def test_log_water_still_reads_the_models_normalisation(self) -> None:
        assert (
            self._seen_message_text(
                "log_water",
                {"drink_text": "стакан воды"},
                message_text="и водички дёрнул стакан",
            )
            == "стакан воды"
        )

    def test_clarify_food_entry_still_reads_the_models_normalisation(self) -> None:
        assert (
            self._seen_message_text(
                "clarify_food_entry",
                {"food_text": "борщ 300г"},
                message_text="ну и борщеца навернул тарелку",
            )
            == "борщ 300г"
        )

    def test_health_screening_is_the_only_one_switched_to_the_person(self) -> None:
        """Парная положительная стража: у скрининга — наоборот."""

        assert (
            self._seen_message_text(
                "health_screening",
                {"symptom_text": MODEL_SYMPTOM_TEXT},
                message_text=LIVE_TURNS[0],
            )
            == LIVE_TURNS[0]
        )

    def test_log_water_still_refuses_a_phrase_its_parser_cannot_read(self) -> None:
        """Парная отрицательная половина: старое вето осталось на месте."""

        assert (
            self._seen_message_text(
                "log_water",
                {"drink_text": "привет, как дела у тебя сегодня"},
                message_text="привет, как дела у тебя сегодня",
            )
            is None
        )


class TestTheLiveDialogueDoesNotRepeatItself:
    """Регресс на сам дефект — пять ходов целиком, через консьерж.

    Провайдер на каждом ходу зовёт ``health_screening`` с одним и тем же
    ``symptom_text``: ровно то, что делала боевая модель. До правки все
    пять ответов были байт-в-байт одинаковы.
    """

    def _replay(self, monkeypatch, suffix: str) -> list[str]:
        provider = AsyncMock()
        provider.complete.return_value = _screening_call()
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation(suffix)

        return [
            generate_concierge_reply(turn, bot_user=bot_user, conversation=conversation).text
            for turn in LIVE_TURNS
        ]

    def test_five_turns_do_not_give_five_identical_screens(self, monkeypatch) -> None:
        replies = self._replay(monkeypatch, "loop")

        assert len(set(replies)) > 1, "пять ответов байт-в-байт — это тот самый дефект"

    def test_only_the_first_turn_gets_the_screening_questions(self, monkeypatch) -> None:
        replies = self._replay(monkeypatch, "loop-first")

        assert replies[0] == SOFT_PAIN_REPLY
        assert SOFT_PAIN_REPLY not in replies[1:]

    def test_the_turn_goes_back_to_the_model_after_the_veto(self, monkeypatch) -> None:
        """Возврат ``None`` — это не «бот замолчал».

        ``concierge`` на ``result is None`` отдаёт ``dto.content``, то
        есть собственный текст модели, произведённый рядом с вызовом
        инструмента. Доделывать транспорт не потребовалось; здесь это
        закреплено.
        """

        replies = self._replay(monkeypatch, "loop-model")

        assert replies[1] == MODEL_OWN_TEXT

    def test_a_red_flag_on_a_later_turn_is_still_answered(self, monkeypatch) -> None:
        """§35 п.5 на полном пути: память не глушит тревожный признак.

        Первый ход занимает памятку, второй — красный флаг. Если бы
        память читалась раньше классификации, человек получил бы вместо
        «сначала к врачу» болтовню модели.
        """

        provider = AsyncMock()
        provider.complete.return_value = _screening_call()
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation("red-flag")

        first = generate_concierge_reply(
            LIVE_TURNS[0], bot_user=bot_user, conversation=conversation
        )
        second = generate_concierge_reply(
            "Спина болит и рука онемела", bot_user=bot_user, conversation=conversation
        )

        assert first.text == SOFT_PAIN_REPLY
        assert second.text == RED_FLAG_REPLY
