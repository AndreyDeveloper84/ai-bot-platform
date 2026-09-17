"""DRF-1994 (решение U владельца) / DRF-1295 — единый выключатель контура питания.

Владелец, дословно: «Добавить единый серверный выключатель анкеты и закрыть им
все входы … До пилота доказать тестом: выключенный health-контур не позволяет
открыть ни дневник, ни анкету, ни получить содержательную рекомендацию о
питании. В Core Pilot бот содержательно о питании не говорит. Допустима только
нейтральная заглушка: „функция пока недоступна"».

Здесь — ОДИН флаг (``NUTRITION_ENABLED``, второго нет), ТРИ талии и один
буквальный текст. Каждое отрицание стоит РЯДОМ со своим положительным
контролем (тот же вход при включённом флаге даёт НЕ заглушку), иначе тест
зеленел бы и на выключателе, который сломал вход вовсе.

Чего этот файл НЕ доказывает, и это названо, а не спрятано:

* **свободный текст модели о питании** («что мне есть?» без инструмента) —
  не по устройству. Закрыты детерминированные пути (навыки, инструменты,
  реклама инструментов в промпте); инструкция модели молчать о еде — отдельная
  правка промпта и решение владельца (DRF-1295), см. ``_nutrition_tools_prompt_block``;
* **вход, обходящий навык** — сегодня таких нет (перепись входов в теле
  PR); появится — не упрётся, и это предел талии, а не флага.

Перепись входов, на которую опирается параметризация ниже: ``DRF-1994``
перечислял три входа в анкету; измерено ДЕВЯТЬ (все в одном ``matches``),
плюс вода как вход в дневник. Оценка «~1–2 SP» верна по цене — талия одна —
и неверна по счёту.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from apps.orchestrator import nutrition_global, personal_surface
from apps.orchestrator.concierge import (
    _NUTRITION_TOOLS_PROMPT_LINES,
    _SCREENING_PROMPT_LINE,
    _nutrition_tools_prompt_block,
    _tools_offered,
    build_concierge_system_prompt,
)
from apps.orchestrator.nutrition_global import (
    NUTRITION_ONLY_TOOL_NAMES,
    execute_nutrition_tool,
    try_handle_structured_nutrition_turn,
)
from apps.orchestrator.personal_surface import (
    CONSENT_CLOSED_TEXT,
    execute_personal_tool,
    render_diary,
)
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT, evaluate_inbound
from apps.skills.base import SkillContext, SkillResult
from apps.skills.health_screening.skill import RED_FLAG_REPLY, HealthScreeningSkill
from apps.skills.menu.marketplace import NUTRITION_UNAVAILABLE_TEXT
from apps.skills.nutrition_anketa.skill import (
    CB_CONFIRM_TARGETS,
    CONSENT_DECLINE_CALLBACK,
    CONSENT_GRANT_CALLBACK,
    ENTRY_PHRASE,
    WITHDRAW_CALLBACK,
    WITHDRAW_CONFIRM_CALLBACK,
    WITHDRAW_KEEP_CALLBACK,
    NutritionAnketaSkill,
)
from apps.skills.water.skill import WaterSkill

STUB = NUTRITION_UNAVAILABLE_TEXT

# ---------------------------------------------------------------------------
# Общие мелочи
# ---------------------------------------------------------------------------


def _ctx(text: str, *, skill_state: dict | None = None) -> SkillContext:
    # Тот же приём, что в apps/skills/nutrition_anketa/tests/test_skill.py:
    # навыку от разговора нужны только ``id`` и ``skill_state``.
    conversation = SimpleNamespace(id=1, skill_state=skill_state or {})
    return SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=Mock(),
        message_text=text,
    )


@pytest.fixture
def nutrition_off(settings):
    settings.NUTRITION_ENABLED = False
    return settings


@pytest.fixture
def nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    return settings


# ---------------------------------------------------------------------------
# 1. Анкета — каждый вход, одна талия
# ---------------------------------------------------------------------------

#: Девять форм, которые принимает ``NutritionAnketaSkill.matches`` (перепись в
#: теле PR). ``DRF-1994`` называл первые три.
ANKETA_ENTRIES: tuple[tuple[str, str, dict | None], ...] = (
    ("typed_command", "/anketa", None),
    ("start_callback", "cb:anketa:start", None),
    ("entry_phrase", ENTRY_PHRASE, None),
    ("edit_callback", "cb:anketa:edit:weight", None),
    ("consent_grant", CONSENT_GRANT_CALLBACK, None),
    ("consent_decline", CONSENT_DECLINE_CALLBACK, None),
    ("withdraw", WITHDRAW_CALLBACK, None),
    ("withdraw_confirm", WITHDRAW_CONFIRM_CALLBACK, None),
    ("withdraw_keep", WITHDRAW_KEEP_CALLBACK, None),
    ("confirm_targets", CB_CONFIRM_TARGETS, None),
    # Продолжение FSM: любой не-``cb:`` текст, пока анкета в полёте.
    ("fsm_resume_plain_text", "30", {"nutrition_anketa": {"current_step": "age"}}),
    (
        "fsm_resume_choice",
        "cb:anketa:choice:gender:female",
        {"nutrition_anketa": {"current_step": "gender"}},
    ),
)


class TestAnketaEveryEntryAnswersTheStub:
    @pytest.mark.parametrize(
        "label, text, state", ANKETA_ENTRIES, ids=[e[0] for e in ANKETA_ENTRIES]
    )
    def test_the_skill_still_claims_the_turn_and_answers_the_stub(
        self, nutrition_off, label, text, state
    ):
        """Самая важная строка выключателя: ``matches`` остаётся True.

        Верни он False при выключенном флаге — «/anketa» уехал бы модели, и
        модель заговорила бы о питании сама (ровно то, что DRF-1295
        запрещает). Поэтому оба утверждения вместе: навык ЗАБРАЛ ход И
        ответил буквальной заглушкой.
        """
        skill = NutritionAnketaSkill()
        context = _ctx(text, skill_state=state)

        assert skill.matches(context) is True
        result = skill.handle(context)
        assert result.reply_text == STUB
        assert result.meta["reply_kind"] == "nutrition_anketa_nutrition_off"

    def test_positive_control_the_switch_is_the_only_thing_that_changed(self, nutrition_on):
        """Тот же вход при включённом флаге даёт НЕ заглушку.

        Без этого узла тесты выше зеленели бы и на навыке, который перестал
        работать вовсе. Что именно он отвечает (согласие / первый вопрос) —
        не предмет: предмет — что это не заглушка.
        """
        skill = NutritionAnketaSkill()
        context = _ctx("/anketa")

        assert skill.matches(context) is True
        with patch.object(skill, "_on_enter", return_value=SkillResult(reply_text="первый вопрос")):
            result = skill.handle(context)
        assert result.reply_text == "первый вопрос"
        assert result.reply_text != STUB


# ---------------------------------------------------------------------------
# 2. Дневник — талия чтения ``render_diary`` + вода как запись
# ---------------------------------------------------------------------------


class TestDiaryEveryEntryAnswersTheStub:
    def test_render_diary_is_the_waist(self, nutrition_off):
        reply = render_diary(Mock())
        assert reply.text == STUB
        assert reply.action_data in (None, {}) or not reply.action_data.get("buttons")

    def test_typed_diary_request_route(self, nutrition_off):
        result = try_handle_structured_nutrition_turn(
            text="что я ел сегодня",
            attachments=None,
            bot_user=Mock(),
            conversation=SimpleNamespace(skill_state={}),
            trace_id="t-1994",
        )
        assert result is not None
        assert result.reply_text == STUB

    def test_show_my_records_tool_route(self, nutrition_off):
        reply = execute_personal_tool("show_my_records", {"section": "diary"}, bot_user=Mock())
        assert reply is not None
        assert reply.text == STUB

    def test_positive_control_diary_reaches_the_consent_gate_when_on(
        self, nutrition_on, monkeypatch
    ):
        """При включённом флаге дневник идёт ДАЛЬШЕ — до ворот согласия.

        Согласие закрыто нарочно: ответ CONSENT_CLOSED_TEXT доказывает, что
        первые ворота (флаг) пройдены и сработали вторые. Ни один из двух
        текстов не равен заглушке.
        """
        monkeypatch.setattr(personal_surface, "personal_records_consent_open", lambda _u: False)
        reply = render_diary(Mock())
        assert reply.text == CONSENT_CLOSED_TEXT
        assert reply.text != STUB


class TestWaterIsADiaryEntryAndAnswersTheStub:
    """W1 — вход, которого не было ни в DRF-1994, ни в первом замере: вода ПИШЕТ в дневник."""

    def test_water_claims_the_turn_and_answers_the_stub(self, nutrition_off):
        skill = WaterSkill()
        context = _ctx("стакан воды")

        assert skill.matches(context) is True
        result = skill.handle(context)
        assert result.reply_text == STUB
        assert result.meta["reply_kind"] == "water_nutrition_off"

    def test_positive_control_water_reaches_its_consent_gate_when_on(
        self, nutrition_on, monkeypatch
    ):
        from apps.skills.water import skill as water_module

        monkeypatch.setattr(water_module, "_consent_open", lambda _u: False)
        result = WaterSkill().handle(_ctx("стакан воды"))
        assert result.reply_text  # что-то сказано
        assert result.reply_text != STUB


# ---------------------------------------------------------------------------
# 3. Инструменты консьержа — не предлагаются, а если позваны — заглушка, не None
# ---------------------------------------------------------------------------


class TestNutritionToolsAreWithheldAndStubbed:
    def test_the_three_tools_leave_the_offer_and_screening_stays(self, nutrition_off):
        offered = {
            spec["name"]
            for spec in _tools_offered(
                "хочу заполнить анкету по питанию", SimpleNamespace(skill_state={})
            )
        }

        # Присутствие — впереди отсутствия: без этой строки «не предлагается»
        # зеленело бы и на пустом списке инструментов.
        assert "show_masters" in offered
        assert "show_my_records" in offered
        assert not (offered & NUTRITION_ONLY_TOOL_NAMES)

    def test_positive_control_the_three_tools_are_offered_when_on(self, nutrition_on):
        offered = {
            spec["name"]
            for spec in _tools_offered(
                "хочу заполнить анкету по питанию", SimpleNamespace(skill_state={})
            )
        }
        assert NUTRITION_ONLY_TOOL_NAMES <= offered

    @pytest.mark.parametrize("tool", sorted(NUTRITION_ONLY_TOOL_NAMES))
    def test_a_called_tool_answers_the_stub_not_none(self, nutrition_off, tool):
        """``None`` отдал бы ход модели — и модель могла бы заговорить о еде сама."""
        lookup = Mock(
            side_effect=AssertionError("навык не должен быть найден: ворота стоят раньше")
        )
        with patch.object(nutrition_global, "_skill_by_name", lookup):
            result = execute_nutrition_tool(
                tool,
                {"drink_text": "стакан воды", "food_text": "борщ 300г"},
                bot_user=Mock(),
                conversation=SimpleNamespace(skill_state={}),
                trace_id="t-1994",
                message_text="борщ 300г",
            )
        assert isinstance(result, SkillResult)
        assert result.reply_text == STUB
        assert lookup.call_count == 0

    def test_positive_control_a_called_tool_reaches_the_skill_when_on(self, nutrition_on):
        fake = Mock()
        fake.name = "water"
        fake.matches.return_value = True
        fake.handle.return_value = SkillResult(reply_text="записала, 250 мл")
        with (
            patch.object(nutrition_global, "_skill_by_name", return_value=fake),
            patch.object(nutrition_global, "_run_skill", side_effect=lambda s, c: s.handle(c)),
        ):
            result = execute_nutrition_tool(
                "log_water",
                {"drink_text": "стакан воды"},
                bot_user=Mock(),
                conversation=SimpleNamespace(skill_state={}),
                trace_id="t-1994",
                message_text="стакан воды",
            )
        assert result is not None
        assert result.reply_text == "записала, 250 мл"
        assert fake.handle.call_count == 1


# ---------------------------------------------------------------------------
# 4. Промпт — реклама инструментов уходит вместе с инструментами, скрининг остаётся
# ---------------------------------------------------------------------------


class TestPromptDoesNotAdvertiseWithheldTools:
    def test_block_keeps_screening_and_drops_the_three_lines(self, nutrition_off):
        block = _nutrition_tools_prompt_block()
        assert _SCREENING_PROMPT_LINE in block
        assert "show_my_records" in block
        assert _NUTRITION_TOOLS_PROMPT_LINES not in block
        for name in NUTRITION_ONLY_TOOL_NAMES:
            assert name not in block

    def test_positive_control_block_advertises_the_three_when_on(self, nutrition_on):
        block = _nutrition_tools_prompt_block()
        assert _SCREENING_PROMPT_LINE in block
        assert _NUTRITION_TOOLS_PROMPT_LINES in block

    def test_the_whole_system_prompt_agrees(self, nutrition_off):
        prompt = build_concierge_system_prompt(today=date(2026, 9, 17))
        assert "health_screening ПЕРВЫМ" in prompt
        assert "start_nutrition_anketa" not in prompt


# ---------------------------------------------------------------------------
# 5. Coarse guard ЖИВ при выключенном контуре — положительные контроли,
#    названные по причине: владелец — «safety coarse guard remains mandatory
#    even in Core Pilot»; эксперт блокирует то, что ЗА грубой защитой.
# ---------------------------------------------------------------------------


class TestCoarseGuardStaysOnWhenTheNutritionContourIsOff:
    def test_pre_check_gate_still_short_circuits_a_crisis(self, nutrition_off):
        """``pre_check`` не знает о питании: ``SAFETY_PATTERNS`` только расширяет
        умолчания (``_verdict_patterns``), ``ENABLED``-флага у него нет."""
        outcome = evaluate_inbound("я думаю о суициде")
        assert outcome.allowed is False
        assert outcome.reply_text == CRISIS_REPLY_TEXT
        assert outcome.reply_text != STUB

    def test_health_screening_skill_still_answers_a_red_flag_not_the_stub(self, nutrition_off):
        """Скрининг на RED_FLAG отвечает «сначала к врачу» ДО чтения памятки
        (§35 п.5 владельца). Погасить его флагом ПИТАНИЯ значило бы снять
        защитную реплику ради выключения еды — поэтому он НЕ в
        ``NUTRITION_ONLY_TOOL_NAMES``, и этот узел стережёт, чтобы кто-нибудь
        «не довёл» выключатель за компанию."""
        skill = HealthScreeningSkill()
        context = _ctx("онемела рука")

        assert skill.matches(context) is True
        result = skill.handle(context)
        assert result.reply_text == RED_FLAG_REPLY
        assert result.reply_text != STUB

    def test_health_screening_tool_still_runs_through_execute_nutrition_tool(self, nutrition_off):
        assert "health_screening" not in NUTRITION_ONLY_TOOL_NAMES
        with patch.object(nutrition_global, "_run_skill", side_effect=lambda s, c: s.handle(c)):
            result = execute_nutrition_tool(
                "health_screening",
                {"symptom_text": "онемела рука"},
                bot_user=Mock(),
                conversation=SimpleNamespace(id=1, skill_state={}),
                trace_id="t-1994",
                message_text="онемела рука",
            )
        assert result is not None
        assert result.reply_text == RED_FLAG_REPLY

    def test_health_screening_is_still_offered_to_the_model_on_a_red_flag(self, nutrition_off):
        offered = {
            spec["name"] for spec in _tools_offered("онемела рука", SimpleNamespace(skill_state={}))
        }
        assert "health_screening" in offered
        assert not (offered & NUTRITION_ONLY_TOOL_NAMES)


# ---------------------------------------------------------------------------
# 6. Сквозной узел: настоящий глобальный ход, модель не вызывалась
# ---------------------------------------------------------------------------

pytestmark_e2e = pytest.mark.django_db


@pytest.mark.django_db
class TestTheModelIsNeverCalledForTheAnketaWhenOff:
    """«/anketa» набранный и чип «📋 Пройти анкету» (тот же callback) идут
    настоящей лестницей ``handle_global_max_event``: структурный слой забирает
    ход, навык отвечает заглушкой, консьерж (модель) не вызывается ни разу."""

    @pytest.fixture(autouse=True)
    def _onboarding_on(self, settings):
        settings.GLOBAL_BOT_ONBOARDING = True

    @pytest.fixture(autouse=True)
    def _no_chat_actions(self, monkeypatch):
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
        )

    @pytest.fixture
    def sent(self, monkeypatch):
        from apps.channels.max import handler as max_handler

        calls: list[dict] = []

        def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
            calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
            return {"ok": True}

        monkeypatch.setattr(max_handler, "send_message", fake_send)
        return calls

    @pytest.fixture
    def fake_redis(self, monkeypatch):
        from apps.orchestrator.memory import short_term
        from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

        fake = _FakeRedis()
        monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
        return fake

    @pytest.fixture
    def model(self, monkeypatch):
        """Шпион на месте модели. Счётчик вызовов — предмет теста."""
        from apps.orchestrator.discovery import DiscoveryReply

        spy = MagicMock(return_value=DiscoveryReply(text="модель ответила", persisted=False))
        monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
        return spy

    @staticmethod
    def _welcomed_user(user_id: int):
        from django.utils import timezone

        from apps.consent.services import record_global_consent
        from apps.identity.services.resolver import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=str(user_id), chat_id="8899"
        )
        bot_user.welcomed_at = timezone.now()
        bot_user.save(update_fields=["welcomed_at"])
        record_global_consent(
            bot_user,
            consent_type="personal_data",
            source="test:nutrition_switch_1994",
            document_version="welcome-s2-v1",
        )
        return bot_user

    @staticmethod
    def _msg(*, text: str, user_id: int, mid: str = "m-1") -> dict:
        return {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Ирина"},
                "recipient": {"chat_id": 8899, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        }

    def test_typed_anketa_gets_the_stub_and_the_model_is_not_called(
        self, nutrition_off, sent, fake_redis, model
    ):
        from apps.channels.max import handler as max_handler

        self._welcomed_user(70001)
        max_handler.handle_global_max_event(self._msg(text="/anketa", user_id=70001))

        assert sent, "ответ ушёл в канал"
        assert sent[-1]["text"] == STUB
        assert model.call_count == 0

    def test_the_chip_callback_is_the_same_message_and_gets_the_same_stub(
        self, nutrition_off, sent, fake_redis, model
    ):
        """Чип несёт буквальный «/anketa» (``personal_surface.CHIP_ANKETA``):
        тап == набранный текст, и заглушка та же."""
        from apps.channels.max import handler as max_handler
        from apps.orchestrator.personal_surface import CHIP_ANKETA

        assert CHIP_ANKETA["callback"] == "/anketa"
        self._welcomed_user(70002)
        max_handler.handle_global_max_event(
            self._msg(text=CHIP_ANKETA["callback"], user_id=70002, mid="chip-1")
        )

        assert sent[-1]["text"] == STUB
        assert model.call_count == 0

    def test_positive_control_when_on_the_anketa_answers_something_else(
        self, nutrition_on, sent, fake_redis, model
    ):
        """Тот же ход при включённом флаге — не заглушка, и модель по-прежнему не
        нужна: анкета детерминирована. Пара к двум узлам выше."""
        from apps.channels.max import handler as max_handler

        self._welcomed_user(70003)
        max_handler.handle_global_max_event(self._msg(text="/anketa", user_id=70003))

        assert sent
        assert sent[-1]["text"] != STUB
        assert model.call_count == 0
