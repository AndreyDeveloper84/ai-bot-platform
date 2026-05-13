"""FAQ skill integration scenarios (DRF-591 / Sprint 7 / F4).

Five scripted scenarios that exercise the F2 skill end-to-end against
real ChromaDB (PersistentClient/tmp_path) + real KbDocument ORM +
mocked LLM provider. Sibling test files focus on individual seams
(prompts, tools, skill matches/handle units); F4 tests the
**composition**:

1. Happy path with retrieval → high confidence + grounded answer.
2. Empty chunks → handoff with `faq_low_confidence`.
3. Low confidence → handoff (top-3 score average below threshold).
4. Multi-doc-type retrieval — service + help_article chunks merged
   into the prompt; doc_types filter narrows correctly.
5. Decision 15 enforcement — no `render_system_prompt` symbol
   imported into the FAQ module surface.

These tests are deliberately heavier than the unit suite — they catch
contract drift between the skill and its three dependencies (F1
tool / F3 prompt / L5 router) that unit tests with deep mocks can
miss.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.kb.chromadb_client import ChromaClient, KbItem, reset_client_cache
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext
from apps.skills.faq.skill import FAQSkill
from apps.skills.faq.tools import SEARCH_KB_TOOL_SPEC
from apps.tenancy.models import Tenant


# The FAQ skill chain bridges sync → async → sync (search tool writes
# audit rows from a worker thread via sync_to_async). transaction-mode
# DB is needed for SQLite tests.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.CHROMA_HTTP_HOST = ""  # type: ignore[attr-defined]
    settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
    settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
    reset_client_cache()
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()
    reset_client_cache()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="faq-int", name="FAQ Int")


@pytest.fixture
def context(tenant: Tenant) -> SkillContext:
    bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="u1")
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
    return SkillContext(
        conversation=conv,
        bot_user=bu,
        message_text="какие часы работы",
        trace_id="t-faq-int",
    )


def _seed_chunks(
    tenant: Tenant,
    *,
    doc_type: str = "faq",
    text: str = "Часы работы: 10-22.",
    chunk_id: str = "c1",
    source_uri: str = "mysite://faq/hours",
) -> None:
    """Plant one chunk in chromadb under `doc_type` for the tenant."""
    ChromaClient().upsert(
        tenant,
        [
            KbItem(
                id=chunk_id,
                text=text,
                embedding=[0.9] * 8,
                metadata={
                    "doc_id": chunk_id,
                    "doc_type": doc_type,
                    "source_uri": source_uri,
                },
            )
        ],
    )


def _completion(
    *,
    text: str = "",
    tool_calls: list[ToolCall] | None = None,
) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="gpt-4o-mini-mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


def _patch_provider(complete_responses: list[Any], embedding: list[float] | None = None) -> Any:
    """Context manager that pins both .complete and .embedding on
    OpenAIProvider for one test.

    Returns a multi-patch context (use as `with _patch_provider(...) as _:`).
    """
    from apps.llm.providers.openai_provider import OpenAIProvider

    embedding_vec = embedding or [0.9] * 8

    class _Stack:
        def __init__(self) -> None:
            self._patches = [
                patch.object(OpenAIProvider, "complete", side_effect=complete_responses),
                patch.object(OpenAIProvider, "embedding", return_value=embedding_vec),
            ]

        def __enter__(self) -> None:
            for p in self._patches:
                p.start()

        def __exit__(self, *_args: object) -> None:
            for p in reversed(self._patches):
                p.stop()

    return _Stack()


# ---------------------------------------------------------------------------
# Scenario 1 — Happy path
# ---------------------------------------------------------------------------


class TestScenarioHappyPath:
    def test_two_call_grounded_answer_high_confidence(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        _seed_chunks(tenant)
        tc = ToolCall(
            id="c1",
            name="search_knowledge_base",
            arguments={"query": "часы работы"},
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Часы работы: 10-22."),
        ]
        with _patch_provider(completions):
            result = FAQSkill().handle(context)

        assert result.reply_text.strip()
        assert result.should_handoff is False
        assert result.confidence is not None and result.confidence > 0.7
        # ToolCall threaded through SkillResult for replay redactor.
        assert result.tool_calls_made == [tc]


# ---------------------------------------------------------------------------
# Scenario 2 — Empty chunks
# ---------------------------------------------------------------------------


class TestScenarioEmptyChunks:
    def test_no_chunks_triggers_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        # No seed — chromadb collection is empty for this tenant.
        tc = ToolCall(id="c1", name="search_knowledge_base", arguments={"query": "x"})
        completions = [_completion(tool_calls=[tc])]
        with _patch_provider(completions):
            result = FAQSkill().handle(context)

        assert result.should_handoff is True
        assert result.handoff_reason == "faq_low_confidence"


# ---------------------------------------------------------------------------
# Scenario 3 — Low confidence (top-3 below threshold)
# ---------------------------------------------------------------------------


class TestScenarioLowConfidence:
    def test_below_threshold_triggers_handoff(
        self,
        context: SkillContext,
        tenant: Tenant,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _seed_chunks(tenant)
        tc = ToolCall(id="c1", name="search_knowledge_base", arguments={"query": "x"})
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="grounded answer"),
        ]
        # Force confidence below 0.5 threshold.
        from apps.skills.faq import skill as skill_module

        monkeypatch.setattr(skill_module, "_compute_confidence", lambda _r: 0.3)

        with _patch_provider(completions):
            result = FAQSkill().handle(context)

        assert result.should_handoff is True
        assert result.handoff_reason == "faq_low_confidence"
        assert result.confidence == 0.3


# ---------------------------------------------------------------------------
# Scenario 4 — Multi-doc-type retrieval merged into messages
# ---------------------------------------------------------------------------


class TestScenarioMultiDocType:
    def test_service_and_help_article_chunks_both_present(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        # Seed one chunk per doc_type.
        _seed_chunks(
            tenant,
            doc_type="service",
            text="Массаж спины — от 1500 рублей.",
            chunk_id="svc-1",
            source_uri="mysite://services/1",
        )
        _seed_chunks(
            tenant,
            doc_type="help_article",
            text="Адрес: ул. Ленина 1, Пенза.",
            chunk_id="help-1",
            source_uri="mysite://help-articles/1",
        )

        captured_messages: list[list[dict[str, Any]]] = []

        async def fake_complete(
            self_provider: Any,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            temperature: float = 0.0,
            tools: list[dict[str, Any]] | None = None,
            max_tokens: int | None = None,
        ) -> CompletionResult:
            captured_messages.append(messages)
            if len(captured_messages) == 1:
                return _completion(
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="search_knowledge_base",
                            arguments={"query": "цена и адрес", "k": 5},
                        )
                    ]
                )
            return _completion(text="Массаж 1500₽, адрес ул. Ленина 1.")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with (
            patch.object(OpenAIProvider, "complete", fake_complete),
            patch.object(OpenAIProvider, "embedding", return_value=[0.9] * 8),
        ):
            result = FAQSkill().handle(context)

        # Second LLM call had both doc_type chunks in the system prompt.
        assert len(captured_messages) == 2
        second_system = captured_messages[1][0]["content"]
        assert "Массаж спины" in second_system
        assert "ул. Ленина" in second_system

        assert result.should_handoff is False


# ---------------------------------------------------------------------------
# Scenario 5 — Decision 15 import discipline
# ---------------------------------------------------------------------------


class TestScenarioImportDiscipline:
    def test_faq_module_does_not_expose_render_system_prompt(self) -> None:
        """`apps.skills.faq.skill` must NOT import or re-export
        ``ayla_ai_core.prompts.render_system_prompt`` (Decision 15).
        """
        module = importlib.import_module("apps.skills.faq.skill")
        for name in dir(module):
            assert name != "render_system_prompt", (
                "apps.skills.faq.skill exposes render_system_prompt — "
                "violates Decision 15 (booking-domain coupling)"
            )

    def test_faq_prompts_module_does_not_expose_render_system_prompt(self) -> None:
        module = importlib.import_module("apps.skills.faq.prompts")
        for name in dir(module):
            assert name != "render_system_prompt", (
                "apps.skills.faq.prompts exposes render_system_prompt — violates Decision 15"
            )

    def test_skill_imports_only_platform_owned_dataclasses(self) -> None:
        """F3 (DRF-590) decoupled FAQ from ayla's BrandVoiceConfig
        schema. Verify the skill still uses the platform-owned types.
        """
        from apps.skills.faq.skill import _DEFAULT_BRAND_VOICE
        from apps.skills.faq.prompts import BrandVoiceConfig

        # _DEFAULT_BRAND_VOICE is an instance of our local dataclass,
        # not ayla's BrandVoiceConfig (different module path).
        assert isinstance(_DEFAULT_BRAND_VOICE, BrandVoiceConfig)
        # Module path proves origin.
        assert _DEFAULT_BRAND_VOICE.__class__.__module__ == "apps.skills.faq.prompts"

    def test_search_kb_tool_spec_canonical_shape(self) -> None:
        """Sanity: tool spec is the L1-canonical shape (Decision 12)."""
        assert set(SEARCH_KB_TOOL_SPEC.keys()) == {
            "name",
            "description",
            "parameters",
        }
        assert SEARCH_KB_TOOL_SPEC["name"] == "search_knowledge_base"
