"""FAQ skill (KB-driven) tests (DRF-589 / Sprint 7 / F2).

Replaces Sprint 6 stub tests. The skill now runs a two-call
tool-using flow through the L5 router; tests inject mocked
:meth:`OpenAIProvider.complete` / ``.embedding`` to stand in for
real LLM calls.
"""

from __future__ import annotations

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
from apps.skills.base import SkillContext, SkillResult
from apps.skills.faq.skill import (
    EVENT_FAQ_ANSWERED,
    EVENT_FAQ_HANDOFF,
    FAQSkill,
)
from apps.skills.faq.tools import SEARCH_KB_TOOL_SPEC
from apps.tenancy.models import Tenant


# The FAQ skill bridges sync → async → back-to-sync (search tool writes
# audit rows from a worker thread via sync_to_async). SQLite tests need
# transaction-mode db so the worker thread can see the test's data;
# CI Postgres works either way but transaction=True is the portable
# choice.
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
    return Tenant.objects.create(slug="faq-skill", name="FAQ Skill")


@pytest.fixture
def context(tenant: Tenant) -> SkillContext:
    bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="u1")
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
    return SkillContext(
        conversation=conv,
        bot_user=bu,
        message_text="сколько стоит массаж?",
        trace_id="t-faq",
    )


def _seed_chunks(tenant: Tenant) -> None:
    chroma = ChromaClient()
    chroma.upsert(
        tenant,
        [
            KbItem(
                id="c1",
                text="Массаж спины от 1500 рублей за 60 минут.",
                embedding=[0.9] * 8,
                metadata={
                    "doc_id": "doc-1",
                    "doc_type": "faq",
                    "source_uri": "mysite://faq/1",
                },
            ),
        ],
    )


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="gpt-4o-mini-mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


def _patch_provider_complete(side_effects: list[Any]) -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider

    return patch.object(OpenAIProvider, "complete", side_effect=side_effects)


def _patch_provider_embedding(vec: list[float] | None = None) -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider

    return patch.object(OpenAIProvider, "embedding", return_value=vec or [0.9] * 8)


# ---------------------------------------------------------------------------
# matches()
# ---------------------------------------------------------------------------


class TestMatches:
    def test_intent_faq_matches(self, context: SkillContext) -> None:
        from apps.orchestrator.intent_router import IntentDecision

        ctx_with_intent = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="x",
            intent=IntentDecision(intent="faq", skill="faq", confidence=0.9, risk_level="low"),
        )
        assert FAQSkill().matches(ctx_with_intent) is True

    def test_intent_not_faq_does_not_match(self, context: SkillContext) -> None:
        from apps.orchestrator.intent_router import IntentDecision

        ctx_with_intent = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="x",
            intent=IntentDecision(
                intent="booking", skill="booking", confidence=0.9, risk_level="low"
            ),
        )
        assert FAQSkill().matches(ctx_with_intent) is False

    def test_no_intent_falls_back_to_keyword_match(self, context: SkillContext) -> None:
        # context fixture text contains "?" + "сколько" — keyword match.
        assert FAQSkill().matches(context) is True

    def test_no_intent_no_keyword_no_match(self, tenant: Tenant) -> None:
        bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="u2")
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
        ctx = SkillContext(conversation=conv, bot_user=bu, message_text="привет")
        assert FAQSkill().matches(ctx) is False

    @pytest.mark.parametrize(
        "text",
        [
            "Расскажи про RF-лифтинг",
            "Опиши процедуру VelaShape",
            "Подскажи цены на массаж",
            "Меня интересует обёртывание",
            "Поведай про лазер",
            "Что входит в комплекс",
            "Есть ли у вас прессотерапия",
            "Можно ли записаться сегодня",
        ],
    )
    def test_imperative_phrasings_match(self, tenant: Tenant, text: str) -> None:
        """Regression for cutover smoke 2026-05-19: imperatives must hit FAQ."""
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=f"imp-{hash(text) & 0xFFFF:x}"
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
        ctx = SkillContext(conversation=conv, bot_user=bu, message_text=text)
        assert FAQSkill().matches(ctx) is True


# ---------------------------------------------------------------------------
# handle() — happy path with retrieval
# ---------------------------------------------------------------------------


class TestRetrievalFlow:
    def test_two_call_grounded_answer(self, context: SkillContext, tenant: Tenant) -> None:
        _seed_chunks(tenant)
        tc = ToolCall(
            id="call_1",
            name="search_knowledge_base",
            arguments={"query": "сколько стоит массаж?"},
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Массаж спины — от 1500 рублей."),
        ]
        with (
            _patch_provider_complete(completions),
            _patch_provider_embedding([0.9] * 8),
        ):
            result = FAQSkill().handle(context)
        assert isinstance(result, SkillResult)
        assert "1500" in result.reply_text
        assert result.should_handoff is False
        assert result.tool_calls_made == [tc]
        assert result.confidence is not None
        assert result.confidence >= 0.5

    def test_direct_answer_when_no_tool_call(self, context: SkillContext, tenant: Tenant) -> None:
        with _patch_provider_complete([_completion(text="Привет!")]):
            result = FAQSkill().handle(context)
        assert result.should_handoff is False
        assert result.reply_text == "Привет!"
        assert result.tool_calls_made == []


# ---------------------------------------------------------------------------
# handle() — handoff paths
# ---------------------------------------------------------------------------


class TestHandoffPaths:
    def test_empty_retrieval_triggers_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        # No chunks seeded → tool returns empty hits.
        tc = ToolCall(id="c1", name="search_knowledge_base", arguments={"query": "x"})
        with (
            _patch_provider_complete([_completion(tool_calls=[tc])]),
            _patch_provider_embedding([0.5] * 8),
        ):
            result = FAQSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "faq_low_confidence"

    def test_low_confidence_triggers_handoff(
        self, context: SkillContext, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_chunks(tenant)
        tc = ToolCall(id="c1", name="search_knowledge_base", arguments={"query": "x"})
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="some answer"),
        ]
        from apps.skills.faq import skill as skill_module

        monkeypatch.setattr(skill_module, "_compute_confidence", lambda _r: 0.3)
        with (
            _patch_provider_complete(completions),
            _patch_provider_embedding([0.9] * 8),
        ):
            result = FAQSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "faq_low_confidence"
        assert result.confidence == 0.3

    def test_unknown_tool_name_triggers_handoff(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        tc = ToolCall(id="x", name="some_other_tool", arguments={})
        with _patch_provider_complete([_completion(tool_calls=[tc])]):
            result = FAQSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "faq_unknown_tool"


# ---------------------------------------------------------------------------
# Decision 15 — NO render_system_prompt import
# ---------------------------------------------------------------------------


class TestDecisionFifteen:
    def test_skill_does_not_import_ayla_render(self) -> None:
        import apps.skills.faq.skill as module

        for name in dir(module):
            assert name != "render_system_prompt", (
                "apps.skills.faq.skill imported render_system_prompt — violates Decision 15"
            )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_answered_audit_written_on_success(self, context: SkillContext, tenant: Tenant) -> None:
        from apps.audit.models import AuditLog

        _seed_chunks(tenant)
        tc = ToolCall(id="c", name="search_knowledge_base", arguments={"query": "x"})
        completions = [_completion(tool_calls=[tc]), _completion(text="answer")]
        with (
            _patch_provider_complete(completions),
            _patch_provider_embedding([0.9] * 8),
        ):
            FAQSkill().handle(context)
        rows = AuditLog.all_tenants.filter(action=EVENT_FAQ_ANSWERED)
        assert rows.exists()

    def test_handoff_audit_written_on_low_confidence(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        from apps.audit.models import AuditLog

        tc = ToolCall(id="c", name="search_knowledge_base", arguments={"query": "x"})
        with (
            _patch_provider_complete([_completion(tool_calls=[tc])]),
            _patch_provider_embedding([0.5] * 8),
        ):
            FAQSkill().handle(context)
        rows = AuditLog.all_tenants.filter(action=EVENT_FAQ_HANDOFF)
        assert rows.exists()


# ---------------------------------------------------------------------------
# Tool spec wiring
# ---------------------------------------------------------------------------


class TestToolSpecPassedToLLM:
    def test_first_call_includes_search_kb_tool(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        captured_tools: list[Any] = []

        async def fake_complete(
            self_provider: Any,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            temperature: float = 0.0,
            tools: list[dict[str, Any]] | None = None,
            max_tokens: int | None = None,
        ) -> CompletionResult:
            captured_tools.append(tools)
            return _completion(text="ok")

        from apps.llm.providers.openai_provider import OpenAIProvider

        with patch.object(OpenAIProvider, "complete", fake_complete):
            FAQSkill().handle(context)

        assert captured_tools
        first_tools = captured_tools[0]
        assert first_tools is not None
        names = [spec["name"] for spec in first_tools]
        assert SEARCH_KB_TOOL_SPEC["name"] in names


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_faq_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "faq" in names
