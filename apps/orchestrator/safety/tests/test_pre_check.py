"""Safety pre-check tests (DRF-537 / Sprint 6 / O3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator.safety.pre_check import (
    SafetyVerdict,
    pre_check,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


def _decision(risk_level: str = "low"):
    return SimpleNamespace(risk_level=risk_level)


class TestAllow:
    def test_empty_text(self):
        r = pre_check("")
        assert r.verdict == SafetyVerdict.ALLOW

    def test_neutral_text(self):
        r = pre_check("Сколько стоит классический массаж?")
        assert r.verdict == SafetyVerdict.ALLOW
        assert r.matched_patterns == []

    def test_no_decision_no_risk_elevation(self):
        r = pre_check("hello", intent_decision=None)
        assert r.verdict == SafetyVerdict.ALLOW


class TestHandoff:
    def test_suicidal_ideation_ru(self):
        r = pre_check("я думаю о самоубийстве, помогите")
        assert r.verdict == SafetyVerdict.HANDOFF

    def test_suicidal_ideation_en(self):
        r = pre_check("I want to kill myself")
        assert r.verdict == SafetyVerdict.HANDOFF

    def test_acute_emergency(self):
        r = pre_check("у меня сердечный приступ, что делать")
        assert r.verdict == SafetyVerdict.HANDOFF

    def test_abuse_signal(self):
        r = pre_check("меня избивает муж")
        assert r.verdict == SafetyVerdict.HANDOFF


class TestBlock:
    def test_drug_name(self):
        r = pre_check("посоветуйте ибупрофен перед массажем")
        assert r.verdict == SafetyVerdict.BLOCK

    def test_diagnosis_request(self):
        r = pre_check("поставьте диагноз по моим симптомам")
        assert r.verdict == SafetyVerdict.BLOCK

    def test_legal_advice_request(self):
        r = pre_check("хочу подать в суд")
        assert r.verdict == SafetyVerdict.BLOCK


class TestDrugMentionIsNotStop:
    """Owner 11.09 §3: a bare mention is not STOP; asking to pick/dose/schedule is.

    Both sides of the same phrase, on purpose: a rule that only had the STOP
    side would pass with the old bare-word pattern, and a rule that only had
    the NORMAL side would pass with no pattern at all.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "вчера выпила ибупрофен, можно сегодня на массаж?",
            "принимаю парацетамол, это помешает процедуре?",
            "у меня сейчас курс антибиотиков, записаться можно?",
            "после кеторола голова прошла, спасибо",
            "I took a painkiller this morning, is a facial ok?",
        ],
    )
    def test_a_bare_mention_passes(self, text):
        r = pre_check(text)
        assert r.verdict == SafetyVerdict.ALLOW, (text, r.matched_patterns)
        assert r.matched_patterns == []

    @pytest.mark.parametrize(
        "text",
        [
            "посоветуйте ибупрофен перед массажем",
            "подберите мне обезболивающее",
            "сколько таблеток парацетамола можно выпить?",
            "какую дозу кеторола принимать?",
            "какая схема приёма антибиотика?",
            "дайте анальгин",
            "what painkiller should I take before waxing?",
            "recommend a dosage of ibuprofen — tramadol?",
        ],
    )
    def test_asking_to_pick_or_dose_is_stop(self, text):
        r = pre_check(text)
        assert r.verdict == SafetyVerdict.BLOCK, (text, r.matched_patterns)
        assert len(r.matched_patterns) == 1

    @pytest.mark.parametrize(
        "text",
        [
            "посоветуйте хорошего мастера по маникюру",
            "дайте адрес салона",
            "подберите мне время на массаж",
            "сколько стоит стрижка?",
            "recommend a good stylist for a bob",
        ],
    )
    def test_an_ask_without_a_drug_is_the_bots_ordinary_work(self, text):
        """The ask-half alone must not fire: «посоветуйте мастера» is what
        this bot is for. Found by substitution — with the drug-lookahead
        loosened to any word, nothing here went red until this test."""
        r = pre_check(text)
        assert r.verdict == SafetyVerdict.ALLOW, (text, r.matched_patterns)
        assert r.matched_patterns == []

    def test_the_two_sides_differ_by_the_ask_alone(self):
        """Same drug, same words otherwise — only the asking changes the verdict."""
        assert pre_check("ибупрофен перед массажем — нормально?").verdict == SafetyVerdict.ALLOW
        assert pre_check("посоветуйте ибупрофен перед массажем").verdict == SafetyVerdict.BLOCK


class TestClarify:
    def test_vague_medical_question(self):
        r = pre_check("почему болит спина?")
        assert r.verdict == SafetyVerdict.CLARIFY

    def test_clarify_vague_eng(self):
        r = pre_check("what's wrong with me")
        assert r.verdict == SafetyVerdict.CLARIFY


class TestVerdictPriority:
    def test_handoff_beats_block(self):
        # Both suicidal pattern (handoff) and drug name (block) match.
        r = pre_check("я хочу убить себя, дайте ибупрофен")
        assert r.verdict == SafetyVerdict.HANDOFF
        assert len(r.matched_patterns) >= 2

    def test_block_beats_clarify(self):
        # Drug name (block) + vague medical (clarify) → block wins.
        r = pre_check("почему болит, дайте парацетамол")
        assert r.verdict == SafetyVerdict.BLOCK


class TestRiskElevation:
    def test_high_risk_decision_elevates_to_handoff(self):
        r = pre_check("обычный вопрос", intent_decision=_decision(risk_level="high"))
        assert r.verdict == SafetyVerdict.HANDOFF

    def test_medium_risk_decision_elevates_to_block(self):
        r = pre_check("обычный вопрос", intent_decision=_decision(risk_level="medium"))
        assert r.verdict == SafetyVerdict.BLOCK

    def test_low_risk_decision_no_elevation(self):
        r = pre_check("обычный вопрос", intent_decision=_decision(risk_level="low"))
        assert r.verdict == SafetyVerdict.ALLOW


class TestBrandVoice:
    def test_brand_voice_forbidden_phrase_blocks(self):
        r = pre_check(
            "обсудим интим за доплату",
            brand_voice={"forbidden_phrases": [r"(?i)интим"]},
        )
        assert r.verdict == SafetyVerdict.BLOCK
        assert any("интим" in p for p in r.matched_patterns)

    def test_bad_brand_voice_regex_ignored(self):
        # Bad regex shouldn't crash; just gets skipped.
        r = pre_check(
            "normal text",
            brand_voice={"forbidden_phrases": ["[unclosed"]},
        )
        assert r.verdict == SafetyVerdict.ALLOW


class TestSettingsOverride:
    def test_settings_patterns_append_to_defaults(self, settings):
        settings.SAFETY_PATTERNS = {
            SafetyVerdict.BLOCK.value: [r"(?i)secret_keyword"],
        }
        # Default patterns still active (drug names)
        r1 = pre_check("посоветуйте ибупрофен")
        assert r1.verdict == SafetyVerdict.BLOCK
        # Plus the new pattern
        r2 = pre_check("contains secret_keyword here")
        assert r2.verdict == SafetyVerdict.BLOCK

    def test_invalid_settings_value_skipped(self, settings):
        # Non-list value for a verdict — silently ignored.
        settings.SAFETY_PATTERNS = {SafetyVerdict.BLOCK.value: "not a list"}
        r = pre_check("normal text")
        assert r.verdict == SafetyVerdict.ALLOW


class TestResult:
    def test_matched_patterns_carries_all_hits(self):
        # Two distinct patterns: drug-name regex (block) + clarify regex.
        r = pre_check("дайте ибупрофен почему болит")
        # Drug → block, vague-medical → clarify. Both patterns recorded.
        assert len(r.matched_patterns) >= 2

    def test_reason_populated_on_match(self):
        r = pre_check("я хочу подать в суд")
        assert "matched" in r.reason


class TestLatency:
    def test_100_calls_under_50ms(self):
        import time

        texts = ["normal text"] * 100
        start = time.perf_counter()
        for t in texts:
            pre_check(t)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        assert elapsed < 50, f"100 calls took {elapsed:.1f}ms, expected <50ms"
