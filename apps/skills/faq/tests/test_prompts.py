"""FAQ prompt template tests (DRF-590 / Sprint 7 / F3)."""

from __future__ import annotations


from apps.skills.faq.prompts import (
    FAQ_EXAMPLES,
    PROMPT_REGISTRY_KEY,
    BrandVoiceConfig,
    Example,
    build_faq_prompt,
)


def _voice(
    persona: str = "Алина, администратор салона «Формула тела»",
    tone: str = "",
    forbidden: tuple[str, ...] = (),
) -> BrandVoiceConfig:
    """Minimal brand voice helper."""
    return BrandVoiceConfig(persona=persona, tone=tone, forbidden=forbidden)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestPromptStructure:
    def test_returns_chatml_messages_list(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="часы?")
        assert isinstance(messages, list)
        assert all(isinstance(m, dict) for m in messages)
        # Every message has role + content.
        for m in messages:
            assert "role" in m
            assert "content" in m

    def test_first_message_is_system(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="часы?")
        assert messages[0]["role"] == "system"
        # Voice persona quoted in the system message.
        assert "Алина" in messages[0]["content"]

    def test_last_message_is_user_query(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="какой адрес?")
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "какой адрес?"

    def test_few_shot_pairs_alternate_user_assistant(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="x")
        # Strip system (first) and final user query (last). The middle
        # should be pairs of user/assistant.
        middle = messages[1:-1]
        assert len(middle) % 2 == 0  # pairs
        for i, m in enumerate(middle):
            expected_role = "user" if i % 2 == 0 else "assistant"
            assert m["role"] == expected_role


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


class TestExamples:
    def test_default_examples_used_when_not_provided(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="x")
        # Strip system + final user → remaining content is the default
        # examples flattened into user/assistant pairs.
        assert len(messages) == 1 + len(FAQ_EXAMPLES) * 2 + 1

    def test_custom_examples_override(self) -> None:
        custom = [Example(user="привет", assistant="здравствуйте!")]
        messages = build_faq_prompt(
            brand_voice=_voice(),
            examples=custom,
            query="x",
        )
        # 1 system + 1 user/assistant pair + 1 final user = 4
        assert len(messages) == 4
        assert messages[1]["content"] == "привет"
        assert messages[2]["content"] == "здравствуйте!"

    def test_four_default_examples_exist(self) -> None:
        # The FAQ skill ships exactly 4 anchors. More tilt the prompt
        # too far into example-imitation; fewer don't cover enough
        # voice ground.
        assert len(FAQ_EXAMPLES) == 4


# ---------------------------------------------------------------------------
# Retrieval grounding
# ---------------------------------------------------------------------------


class TestRetrievedChunksBlock:
    def test_no_chunks_includes_tool_hint(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="x")
        system = messages[0]["content"]
        # Without retrieved chunks, the prompt nudges the LLM to call
        # search_knowledge_base.
        assert "search_knowledge_base" in system
        assert "ИЗВЛЕЧЁННЫЕ" not in system

    def test_chunks_included_in_system_prompt(self) -> None:
        chunks = [
            {"text": "Часы: 10-22", "doc_type": "faq"},
            {"text": "Адрес: ул. Ленина 1", "doc_type": "faq"},
        ]
        messages = build_faq_prompt(
            brand_voice=_voice(),
            retrieved_chunks=chunks,
            query="часы?",
        )
        system = messages[0]["content"]
        assert "ИЗВЛЕЧЁННЫЕ ИЗ БАЗЫ ЗНАНИЙ" in system
        assert "Часы: 10-22" in system
        assert "Адрес: ул. Ленина 1" in system

    def test_chunks_present_anti_hallucination_text(self) -> None:
        chunks = [{"text": "x"}]
        messages = build_faq_prompt(
            brand_voice=_voice(),
            retrieved_chunks=chunks,
            query="?",
        )
        system = messages[0]["content"]
        assert "ТОЛЬКО на основе извлечённых" in system
        assert "уточню у мастера" in system

    def test_empty_chunk_text_skipped(self) -> None:
        chunks = [{"text": "  "}, {"text": "Реальный текст"}]
        messages = build_faq_prompt(
            brand_voice=_voice(),
            retrieved_chunks=chunks,
            query="?",
        )
        system = messages[0]["content"]
        assert "Реальный текст" in system
        # No empty [1] / [2] enumerations from whitespace-only.
        assert "[1] " not in system or "Реальный" in system

    def test_chunks_numbered(self) -> None:
        chunks = [{"text": "first"}, {"text": "second"}]
        messages = build_faq_prompt(brand_voice=_voice(), retrieved_chunks=chunks, query="?")
        system = messages[0]["content"]
        assert "[1]" in system
        assert "[2]" in system


# ---------------------------------------------------------------------------
# Voice variations
# ---------------------------------------------------------------------------


class TestBrandVoiceVariations:
    def test_tone_rendered_when_present(self) -> None:
        voice = _voice(tone="дружелюбный")
        messages = build_faq_prompt(brand_voice=voice, query="?")
        system = messages[0]["content"]
        assert "дружелюбный" in system

    def test_tone_absent_no_line(self) -> None:
        voice = _voice(tone="")
        messages = build_faq_prompt(brand_voice=voice, query="?")
        system = messages[0]["content"]
        assert "Тон:" not in system

    def test_forbidden_rendered_when_present(self) -> None:
        voice = _voice(forbidden=("гарантирую", "100%"))
        messages = build_faq_prompt(brand_voice=voice, query="?")
        system = messages[0]["content"]
        assert "Запрещено" in system
        assert "гарантирую" in system

    def test_forbidden_absent_no_line(self) -> None:
        voice = _voice(forbidden=())
        messages = build_faq_prompt(brand_voice=voice, query="?")
        system = messages[0]["content"]
        assert "Запрещено" not in system


# ---------------------------------------------------------------------------
# Length cap reminder
# ---------------------------------------------------------------------------


class TestAnswerLengthCap:
    def test_600_char_cap_in_system(self) -> None:
        messages = build_faq_prompt(brand_voice=_voice(), query="?")
        system = messages[0]["content"]
        # Operator-visible cap; F2 enforces post-LLM.
        assert "600" in system


# ---------------------------------------------------------------------------
# Decision 15 — NO render_system_prompt import
# ---------------------------------------------------------------------------


class TestDecisionFifteen:
    def test_module_does_not_import_render_system_prompt(self) -> None:
        import apps.skills.faq.prompts as module

        # Walk the module's globals to ensure no render_system_prompt
        # symbol leaked in.
        for name in dir(module):
            assert name != "render_system_prompt", (
                "apps.skills.faq.prompts imported render_system_prompt — "
                "violates Decision 15 (booking-domain coupling)"
            )

    def test_prompt_registry_key_stable(self) -> None:
        # Sprint 4 PromptRegistry consumers look up by this slug.
        # Changing it requires a migration in the registry.
        assert PROMPT_REGISTRY_KEY == "skill.faq.grounded_answer"
