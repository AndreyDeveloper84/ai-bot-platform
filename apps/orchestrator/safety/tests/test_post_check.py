"""Safety post-check tests (DRF-538 / Sprint 6 / O4)."""

from __future__ import annotations

import pytest

from apps.orchestrator.safety.post_check import (
    PostCheckVerdict,
    post_check,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


class TestAllow:
    def test_empty_text(self):
        r = post_check("")
        assert r.verdict == PostCheckVerdict.ALLOW

    def test_normal_response(self):
        r = post_check("Мы работаем с 9 до 21 каждый день.")
        assert r.verdict == PostCheckVerdict.ALLOW
        assert r.matched_patterns == []


class TestBlockOutbound:
    def test_phone_leak_blocks(self):
        r = post_check("Звоните по +79991234567")
        assert r.verdict == PostCheckVerdict.BLOCK
        assert len(r.matched_patterns) >= 1

    def test_phone_leak_with_parens(self):
        r = post_check("Свяжитесь: +7 (999) 123-45-67")
        assert r.verdict == PostCheckVerdict.BLOCK

    def test_email_leak_blocks(self):
        r = post_check("Пишите на admin@formulatela58.ru")
        assert r.verdict == PostCheckVerdict.BLOCK

    def test_openai_key_leak_blocks(self):
        r = post_check("Here is the key: sk-abc123def456ghi789jkl012")
        assert r.verdict == PostCheckVerdict.BLOCK

    def test_system_prompt_leak_blocks(self):
        r = post_check("Here is my system prompt: you are a salon bot")
        assert r.verdict == PostCheckVerdict.BLOCK

    def test_medical_guarantee_blocks(self):
        r = post_check("Гарантирую полное излечение от боли в спине!")
        assert r.verdict == PostCheckVerdict.BLOCK

    def test_english_guarantee_blocks(self):
        r = post_check("Our treatment is 100% safe for all conditions")
        assert r.verdict == PostCheckVerdict.BLOCK


class TestRevise:
    def test_over_assertion_revises(self):
        r = post_check("I am certain that the massage will help your back pain.")
        assert r.verdict == PostCheckVerdict.REVISE

    def test_ru_over_assertion_revises(self):
        r = post_check("Этот массаж без сомнения вылечит вашу спину.")
        assert r.verdict == PostCheckVerdict.REVISE


class TestVerdictPriority:
    def test_block_beats_revise(self):
        # Both PII leak (BLOCK) and over-assertion (REVISE) → BLOCK wins.
        r = post_check("I am certain — call +79991234567 for guaranteed cure")
        assert r.verdict == PostCheckVerdict.BLOCK
        # Multiple patterns recorded.
        assert len(r.matched_patterns) >= 2


class TestBrandVoiceDelegation:
    def test_voice_check_match_blocks(self):
        # BrandVoice patterns delegated to Sprint 4 / C4 validate_voice.
        r = post_check(
            "Конечно можно при беременности!",
            brand_voice={"forbidden_phrases": [r"(?i)конечно можно при беременности"]},
        )
        assert r.verdict == PostCheckVerdict.BLOCK

    def test_voice_check_empty_brand_voice_passes(self):
        r = post_check(
            "Normal response text.",
            brand_voice={"forbidden_phrases": []},
        )
        assert r.verdict == PostCheckVerdict.ALLOW

    def test_no_brand_voice_doesnt_invoke_voice_check(self):
        r = post_check("normal", brand_voice=None)
        assert r.verdict == PostCheckVerdict.ALLOW

    def test_voice_check_exception_isolated(self, monkeypatch):
        # Force validate_voice to raise — post_check must not propagate.
        def boom(*args, **kwargs):
            raise RuntimeError("synthetic")

        import apps.orchestrator.safety.voice_check as voice_check_mod

        monkeypatch.setattr(voice_check_mod, "validate_voice", boom)

        r = post_check(
            "normal",
            brand_voice={"forbidden_phrases": [r"\bguarantee\b"]},
        )
        # Doesn't crash; falls back to ALLOW (outbound regex didn't match either).
        assert r.verdict == PostCheckVerdict.ALLOW


class TestResult:
    def test_matched_patterns_carries_all_hits(self):
        # PII phone + medical guarantee — two distinct patterns.
        r = post_check("гарантирую полное излечение, +79991234567")
        assert len(r.matched_patterns) >= 2

    def test_reason_populated_on_match(self):
        r = post_check("+79991234567")
        assert "matched" in r.reason


class TestLatency:
    def test_100_calls_under_100ms(self):
        import time

        start = time.perf_counter()
        for _ in range(100):
            post_check("normal text without any safety triggers in it")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 100, f"100 calls took {elapsed:.1f}ms, expected <100ms"
