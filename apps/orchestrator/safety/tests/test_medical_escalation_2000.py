"""S-2 · Медицинская эскалация 103/112 отдельно от кризисного текста (DRF-2000).

Замер на нетронутом ``dev 6b5c1023`` (20.09):

* ``pre_check`` группа «acute medical emergency» (``скорая|emergency|умираю|
  сердечный приступ|heart attack``) лежит в списке ``HANDOFF`` → ``gate``
  отвечает ``CRISIS_REPLY_TEXT``: первой строкой телефон доверия
  8-800-2000-122, «103» в коде гейта нет; ``\\bскорая\\b`` не ловит «вызовите
  скорую» (склонение) → ALLOW;
* «не могу дышать / теряю сознание / давит в груди / задыхаюсь» — гейт ALLOW,
  классификатор ``health_screening`` даёт RED_FLAG: на per-tenant пути навык
  отвечает ``MEDICAL_EMERGENCY_TEXT_V2`` детерминированно, на global-пути —
  только если модель сама выберет инструмент (не детерминировано);
* текст эскалации есть — ``medical_emergency.MEDICAL_EMERGENCY_TEXT_V2``
  ([OD-BOT §163], дословно, 18.09; v2 сменил текст VQ3 от 16.09) — и уже
  используется навыком и Mini App; гейт его не знает.

Сторожа:

* p1 — гейт: группа «неотложка» → verdict ``medical``, ответ =
  ``MEDICAL_EMERGENCY_TEXT_V2`` байт-в-байт, «103» и «112» в ПЕРВОЙ строке,
  «телефон доверия» и 8-800 — нет; safety-sensitive flow прекращён
  (``allowed=False``);
* p2 — кризисный текст не тронут: «хочу умереть» / «я думаю о суициде» →
  ``handoff`` и прежний ``CRISIS_REPLY_TEXT`` (#1084) — положительная пара;
* p3 — приоритет: самоповреждение + сердце в одной реплике → кризис (в нём
  и так есть 112); гипербола «умираю как хочу на массаж» → ALLOW как была;
* p4 — global-путь: red flag G1–G7 из классификатора (не из группы гейта) →
  детерминированный ответ v2 БЕЗ вызова модели (провайдер, который бросает
  при обращении); per-tenant навык по-прежнему отвечает v2;
* p5 — DRE/Mini App: ``assess`` знает вердикт ``medical`` (STOP, handoff
  REQUIRED), ``health_gate._hard_stop`` отдаёт ``health_red_flag``, не ``block``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from apps.orchestrator.safety.gate import CRISIS_HOTLINE, CRISIS_REPLY_TEXT, evaluate_inbound
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.orchestrator.safety.pre_check import SafetyVerdict, pre_check

EMERGENCY_PHRASES = (
    "у меня сердечный приступ",
    "кажется, сердечный приступ",
    "heart attack",
    "вызовите скорую",
    "нужна скорая помощь",
    "умираю от боли в груди",
    "не могу дышать",
    "задыхаюсь",
    "теряю сознание",
    "потеряла сознание",
    "давит в груди",
)

CRISIS_PHRASES = ("хочу умереть", "я думаю о суициде", "хочу убить себя")


# ── p1 — гейт: неотложка → 103/112, не телефон доверия ───────────────


class TestP1MedicalVerdict:
    def test_verdict_exists(self) -> None:
        assert SafetyVerdict.MEDICAL.value == "medical"

    @pytest.mark.parametrize("text", EMERGENCY_PHRASES)
    def test_emergency_phrase_gets_the_medical_text(self, text: str) -> None:
        outcome = evaluate_inbound(text)
        assert outcome.allowed is False  # safety-sensitive flow прекращён
        assert outcome.verdict == "medical"
        assert outcome.reply_text == MEDICAL_EMERGENCY_TEXT_V2
        first_line = outcome.reply_text.splitlines()[0]
        assert "103" in first_line and "112" in first_line
        assert CRISIS_HOTLINE not in outcome.reply_text
        assert "телефон доверия" not in outcome.reply_text.lower()  # empty-assert-ok: 103/112 выше

    def test_the_text_is_the_owner_ruling_verbatim(self) -> None:
        assert MEDICAL_EMERGENCY_TEXT_V2.startswith("По описанию это может требовать")
        assert "103 или 112" in MEDICAL_EMERGENCY_TEXT_V2


# ── p2 — кризисный текст не тронут ───────────────────────────────────


class TestP2CrisisTextUntouched:
    @pytest.mark.parametrize("text", CRISIS_PHRASES)
    def test_crisis_phrase_keeps_the_hotline_reply(self, text: str) -> None:
        outcome = evaluate_inbound(text)
        assert outcome.allowed is False and outcome.verdict == "handoff"
        assert outcome.reply_text == CRISIS_REPLY_TEXT
        assert CRISIS_HOTLINE in outcome.reply_text and "112" in outcome.reply_text

    def test_crisis_text_is_the_1084_signoff(self) -> None:
        assert CRISIS_REPLY_TEXT.startswith("Спасибо, что написал(а) мне это.")
        assert "телефон доверия" in CRISIS_REPLY_TEXT


# ── p3 — приоритет и гипербола ───────────────────────────────────────


class TestP3PrecedenceAndHyperbole:
    def test_self_harm_plus_cardiac_is_crisis(self) -> None:
        outcome = evaluate_inbound("хочу убить себя, сердце разрывается, сердечный приступ")
        assert outcome.verdict == "handoff" and outcome.reply_text == CRISIS_REPLY_TEXT

    @pytest.mark.parametrize(
        "text", ["умираю как хочу на массаж", "умираю хочу кофе", "скорее бы на маникюр"]
    )
    def test_hyperbole_is_allowed(self, text: str) -> None:
        assert evaluate_inbound(text).allowed is True

    def test_pre_check_priority_order(self) -> None:
        assert pre_check("хочу умереть, вызовите скорую").verdict == SafetyVerdict.HANDOFF
        assert pre_check("вызовите скорую").verdict == SafetyVerdict.MEDICAL


# ── p4 — global-путь: red flag без модели ────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestP4GlobalPathIsDeterministic:
    @pytest.fixture(autouse=True)
    def _nutrition_on(self, settings):
        settings.NUTRITION_ENABLED = True

    @staticmethod
    def _bot_user_and_conversation(suffix: str):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max",
            channel_user_id=f"drf2000-{suffix}-uid",
            chat_id=f"drf2000-{suffix}-chat",
        )
        return bot_user, resolve_active_global_conversation(bot_user)

    @pytest.mark.parametrize("text", ["онемела половина лица", "болит шея, отдаёт в руку"])
    def test_red_flag_answers_without_the_model(self, monkeypatch, text: str) -> None:
        from apps.orchestrator import concierge
        from apps.skills.health_screening.classifier import PainSignal, classify

        assert classify(text) == PainSignal.RED_FLAG  # положительно: это red flag классификатора
        assert evaluate_inbound(text).allowed is True  # и НЕ группа гейта — путь модели

        provider = AsyncMock()
        provider.complete.side_effect = AssertionError("модель не должна вызываться на red flag")
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)

        bot_user, conversation = self._bot_user_and_conversation(text[:6].encode().hex()[:8])
        reply = concierge.generate_concierge_reply(
            text, bot_user=bot_user, conversation=conversation
        )
        assert reply.text == MEDICAL_EMERGENCY_TEXT_V2
        assert provider.complete.call_count == 0  # empty-assert-ok: текст v2 строкой выше

    def test_a_plain_beauty_turn_still_reaches_the_model(self, monkeypatch) -> None:
        """Положительная пара: без red flag модель зовётся."""
        from apps.llm.protocol import CompletionResult
        from apps.orchestrator import concierge

        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="Подберу массаж — какой район?",
            tool_calls=[],
            prompt_tokens=1,
            completion_tokens=1,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="stop",
        )
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        bot_user, conversation = self._bot_user_and_conversation("plain")
        # SOFT-сигнал (не red flag): модель зовётся, как в test_screening_loop_1542.
        reply = concierge.generate_concierge_reply(
            "Что-то тянет поясницу", bot_user=bot_user, conversation=conversation
        )
        assert provider.complete.call_count >= 1
        assert reply.text


# ── p5 — DRE и Mini App знают новый вердикт ──────────────────────────


class TestP5ConsumersKnowMedical:
    def test_assessment_maps_medical_to_stop_required(self) -> None:
        from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyState
        from apps.orchestrator.safety.assessment import assess

        result = pre_check("давит в груди")
        assert result.verdict == SafetyVerdict.MEDICAL  # положительно
        assessment = assess(result, state_revision=1, source="pre_check")
        assert assessment.state == SafetyState.STOP
        assert assessment.handoff == Handoff.REQUIRED

    def test_miniapp_health_gate_names_it_a_red_flag(self) -> None:
        from apps.miniapp_api import health_gate

        stop = health_gate._hard_stop("сердечный приступ")
        assert stop is not None
        assert stop.kind == health_gate.KIND_RED_FLAG
        assert stop.text == MEDICAL_EMERGENCY_TEXT_V2
