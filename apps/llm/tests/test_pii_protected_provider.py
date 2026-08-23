"""PIITokenizingProvider decorator tests (Phase D / 152-ФЗ Tier-A).

Pins the decorator contract — tokenize messages going к vendor,
detokenize the response coming back — and exercises the Phase G
adversarial concerns surfaced by tech-lead:

  * pii_conversation_id=None legitimate flow → pass-through, WARN log
  * pii_conversation_id=None user-facing flow → policy-detectable via
    WARNING log signal (not hard block — see module docstring)
  * Token collision attack (user types literal `<PHONE_5>`) → escape
    sentinel applied, NOT reverse-substituted
  * Hallucinated tokens in LLM response — already covered в
    test_pii_tokenizer; double-check via decorator boundary
  * Concurrent calls к same conversation — Lua atomic, also tested
    в Phase C

Uses the same `_FakeRedis` HASH stub plus a `_FakeProvider` async
double — no real Redis, no real OpenAI/Anthropic SDK.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest

from apps.llm import pii_tokenizer
from apps.llm.pii_protected_provider import PIITokenizingProvider
from apps.llm.protocol import CompletionResult
from apps.llm.tests.test_pii_tokenizer import (
    _FakeRedis,
    _extract_token,
    _has_token,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeProvider:
    """Async LLMProvider double recording every call для assertion."""

    name = "fake-openai"

    def __init__(self, response_text: str = "ok") -> None:
        self.response_text = response_text
        self.last_messages: list[dict[str, Any]] | None = None
        self.last_text: str | None = None
        self.complete_calls = 0
        self.embedding_calls = 0
        self.last_tool_choice: str | None = None

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        # DRF-1286 — the Protocol grew a forced-tool-use parameter;
        # doubles that spell the signature out have to carry it.
        tool_choice: str | None = None,
    ) -> CompletionResult:
        self.last_tool_choice = tool_choice
        self.last_messages = [dict(m) for m in messages]
        self.complete_calls += 1
        return CompletionResult(
            text=self.response_text,
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=5,
            model=model or "fake-model",
            provider=self.name,
            finish_reason="stop",
        )

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        self.last_text = text
        self.embedding_calls += 1
        return [0.1, 0.2, 0.3]


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRedis]:
    fake = _FakeRedis()
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    pii_tokenizer._invalidate_script_cache()
    yield fake
    pii_tokenizer._invalidate_script_cache()


@pytest.fixture
def conv_id() -> str:
    return str(uuid4())


@pytest.fixture
def fake_provider() -> _FakeProvider:
    return _FakeProvider()


# ---------------------------------------------------------------------------
# Pass-through (no active PII context)
# ---------------------------------------------------------------------------


class TestNoActiveScope:
    @pytest.mark.asyncio
    async def test_complete_passes_through_when_no_context(self, fake_provider, fake_redis, caplog):
        wrapped = PIITokenizingProvider(fake_provider)
        messages = [{"role": "user", "content": "Звоните +7 999 123 45 67"}]
        with caplog.at_level(logging.WARNING, logger="apps.llm.pii_protected_provider"):
            result = await wrapped.complete(messages, model="gpt-4")
        # No tokenization applied — vendor saw raw PII (this is the bug
        # signal we want operator to see in logs).
        assert fake_provider.last_messages[0]["content"] == "Звоните +7 999 123 45 67"
        assert result.text == "ok"
        # WARNING fired — policy signal для operator surface.
        assert any("no_active_scope" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_embedding_passes_through_when_no_context(self, fake_provider, fake_redis):
        wrapped = PIITokenizingProvider(fake_provider)
        await wrapped.embedding("anna@example.ru", model="text-embedding-3-small")
        assert fake_provider.last_text == "anna@example.ru"


# ---------------------------------------------------------------------------
# Active scope — tokenize + detokenize round-trip
# ---------------------------------------------------------------------------


class TestActiveScope:
    @pytest.mark.asyncio
    async def test_tool_choice_forwarded_when_asked_for(self, fake_provider, fake_redis, conv_id):
        """DRF-1286 — the PII wrapper must not swallow forced tool use.

        The concierge's forced retry reaches the vendor THROUGH this
        wrapper. If it dropped `tool_choice`, the retry would silently
        become an ordinary second call — the same silent-degradation
        failure the retry exists to fix.
        """
        wrapped = PIITokenizingProvider(fake_provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(
                [{"role": "user", "content": "покажи мастеров"}],
                model="gpt-4",
                tools=[{"name": "t", "description": "d", "parameters": {}}],
                tool_choice="required",
            )
        assert fake_provider.last_tool_choice == "required"

    @pytest.mark.asyncio
    async def test_provider_that_predates_tool_choice_still_works(self, fake_redis, conv_id):
        """DRF-1286 — an ordinary call must not carry the new parameter.

        The wrapped object is duck-typed, and not every implementation
        accepts every parameter (the ai-core Anthropic adapter was already
        found poorer than expected once — no ``role="tool"``). This double
        deliberately has the PRE-DRF-1286 signature: if the wrapper ever
        goes back to forwarding `tool_choice` unconditionally, an ordinary
        call to such a provider raises TypeError, and this test says so
        instead of a skill breaking in production.
        """

        class _PreToolChoiceProvider:
            name = "legacy"

            async def complete(
                self,
                messages: list[dict[str, Any]],
                *,
                model: str | None = None,
                temperature: float = 0.0,
                tools: list[dict[str, Any]] | None = None,
                max_tokens: int | None = None,
            ) -> CompletionResult:
                return CompletionResult(text="ok", model=model or "m", provider=self.name)

            async def embedding(self, text: str, *, model: str) -> list[float]:
                return [0.1]

        wrapped = PIITokenizingProvider(_PreToolChoiceProvider())
        with pii_tokenizer.pii_context(conv_id):
            result = await wrapped.complete([{"role": "user", "content": "привет"}], model="gpt-4")
        assert result.text == "ok"

    @pytest.mark.asyncio
    async def test_complete_tokenizes_user_message(self, fake_provider, fake_redis, conv_id):
        wrapped = PIITokenizingProvider(fake_provider)
        messages = [{"role": "user", "content": "Звоните +7 999 123 45 67"}]
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(messages, model="gpt-4")
        # Vendor saw tokenized content — raw phone NEVER hit the wire.
        sent = fake_provider.last_messages[0]["content"]
        assert "+7" not in sent
        assert _has_token(sent, "PHONE", 1)

    @pytest.mark.asyncio
    async def test_complete_tokenizes_all_messages_including_system(
        self, fake_provider, fake_redis, conv_id
    ):
        """System prompts may carry dynamic user context inserted by
        skill rendering — tokenize them too (idempotent on PII-free)."""
        wrapped = PIITokenizingProvider(fake_provider)
        messages = [
            {"role": "system", "content": "User context: phone +7 999 000 00 00"},
            {"role": "user", "content": "Запиши меня"},
            {"role": "assistant", "content": "Email подтверждения test@example.ru"},
        ]
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(messages, model="gpt-4")
        for sent_msg in fake_provider.last_messages:
            assert "+7" not in sent_msg["content"]
            assert "test@example.ru" not in sent_msg["content"]

    @pytest.mark.asyncio
    async def test_complete_detokenizes_response(self, fake_redis, conv_id):
        """LLM echoes the token in response → caller sees original PII back."""
        # Pre-mint a token by tokenizing first.
        tokenized = pii_tokenizer.tokenize("+7 999 123 45 67", conv_id)
        token = _extract_token(tokenized, "PHONE", 1)
        # Build a fake provider whose response echoes the token.
        provider = _FakeProvider(response_text=f"Перезвоню на {token} завтра")
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            result = await wrapped.complete(
                [{"role": "user", "content": "+7 999 123 45 67"}],
                model="gpt-4",
            )
        # Caller-visible text has the ORIGINAL phone string back.
        assert "+7 999 123 45 67" in result.text
        assert token not in result.text

    @pytest.mark.asyncio
    async def test_embedding_tokenizes_input(self, fake_provider, fake_redis, conv_id):
        wrapped = PIITokenizingProvider(fake_provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.embedding("anna@example.ru", model="text-embedding-3-small")
        assert "anna@example.ru" not in fake_provider.last_text
        assert _has_token(fake_provider.last_text, "EMAIL", 1)

    @pytest.mark.asyncio
    async def test_name_attribute_proxies_to_wrapped(self, fake_provider):
        """Audit + cost tracker must see real vendor name."""
        wrapped = PIITokenizingProvider(fake_provider)
        assert wrapped.name == "fake-openai"

    @pytest.mark.asyncio
    async def test_completion_metadata_preserved(self, fake_redis, conv_id):
        """token / model / provider / finish_reason survive the round-trip."""
        provider = _FakeProvider(response_text="hello")
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            result = await wrapped.complete(
                [{"role": "user", "content": "hi"}],
                model="gpt-4",
            )
        assert result.prompt_tokens == 10
        assert result.completion_tokens == 5
        assert result.provider == "fake-openai"
        assert result.finish_reason == "stop"


# ---------------------------------------------------------------------------
# Phase G adversarial coverage
# ---------------------------------------------------------------------------


class TestAdversarial:
    """Adversarial Phase G concerns (tech-lead handoff):

    1. Caller passes None legitimately → pass-through + warn (above).
    2. Caller passes None for user-facing flow → bug, WARN log catches it.
    3. Token collision attack — user types `<PHONE_5>` literally.
    4. Prefix collision PHONE_1 vs PHONE_10 — already в Phase C.
    5. Detokenize race condition — Lua atomicity + append-only rev map.
    6. Lua ABI changes — script body integrity guard в Phase C.
    """

    @pytest.mark.asyncio
    async def test_user_supplied_token_shape_does_not_reverse_substitute(self, fake_redis, conv_id):
        """ADVERSARIAL (Phase G): user types literal `<PHONE_5>` to fish
        for another customer's number in the conversation's rev-map.
        Per-conversation NONCE defence makes this impossible — user
        cannot guess NONCE → cannot construct token shape matching
        rev-map → no leak."""
        # Pre-populate 5 real phones in the rev map.
        for i in range(1, 6):
            pii_tokenizer.tokenize(f"+7 999 000 00 {i:02d}", conv_id)
        # User types literal `<PHONE_5>` (no nonce) plus their own message.
        # LLM echoes it back verbatim in the response (worst-case faithful echo).
        provider = _FakeProvider(response_text="Ваш запрос: <PHONE_5> понял.")
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            result = await wrapped.complete(
                [{"role": "user", "content": "Что значит <PHONE_5>?"}],
                model="gpt-4",
            )
        # The 5th PHONE's actual digits MUST NOT appear — nonce defence
        # ensures user-typed `<PHONE_5>` never matches the rev-map regex.
        assert "+7 999 000 00 05" not in result.text
        # User's literal `<PHONE_5>` text survives the round-trip intact.
        assert "<PHONE_5>" in result.text

    @pytest.mark.asyncio
    async def test_hallucinated_token_in_response_logged(self, fake_redis, conv_id, caplog):
        """LLM emits `<PHONE_{nonce}_99>` that was never в the tokenmap.
        Hallucination — log WARNING, leave string intact."""
        # Seed the nonce by tokenizing one real PII item first.
        pii_tokenizer.tokenize("+7 999 000 00 01", conv_id)
        nonce = fake_redis.hashes[pii_tokenizer._key(conv_id)]["nonce"]
        hallucinated = f"<PHONE_{nonce}_99>"
        provider = _FakeProvider(response_text=f"Звоните на {hallucinated}")
        wrapped = PIITokenizingProvider(provider)
        with caplog.at_level(logging.WARNING, logger="apps.llm.pii_tokenizer"):
            with pii_tokenizer.pii_context(conv_id):
                result = await wrapped.complete(
                    [{"role": "user", "content": "hi"}],
                    model="gpt-4",
                )
        assert hallucinated in result.text
        assert any("hallucinated_token" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_nested_pii_context_inner_overrides_outer(self, fake_provider, fake_redis):
        """Stack discipline — inner scope wins, outer restored on exit."""
        conv_a = str(uuid4())
        conv_b = str(uuid4())
        wrapped = PIITokenizingProvider(fake_provider)
        with pii_tokenizer.pii_context(conv_a):
            assert pii_tokenizer.current_conversation_id() == conv_a
            with pii_tokenizer.pii_context(conv_b):
                assert pii_tokenizer.current_conversation_id() == conv_b
                await wrapped.complete(
                    [{"role": "user", "content": "anna@example.ru"}],
                    model="gpt-4",
                )
            # Outer scope restored.
            assert pii_tokenizer.current_conversation_id() == conv_a
        assert pii_tokenizer.current_conversation_id() is None

    @pytest.mark.asyncio
    async def test_pii_context_none_explicit_opt_out(self, fake_provider, fake_redis):
        """Explicit `pii_context(None)` deactivates tokenization для a
        block — e.g. internal background pipeline где caller asserts no
        user PII present."""
        wrapped = PIITokenizingProvider(fake_provider)
        with pii_tokenizer.pii_context(None):
            assert pii_tokenizer.current_conversation_id() is None
            await wrapped.complete(
                [{"role": "user", "content": "Звоните +7 999 123 45 67"}],
                model="gpt-4",
            )
        # Raw PII reaches vendor (the caller explicitly opted out).
        assert "+7 999 123 45 67" in fake_provider.last_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_messages_with_extra_keys_preserved(self, fake_provider, fake_redis, conv_id):
        """Some skill flows pass `name`, `tool_call_id`, etc. на message
        dicts. Decorator must preserve them — only ``content`` field
        gets tokenized."""
        wrapped = PIITokenizingProvider(fake_provider)
        messages = [
            {
                "role": "tool",
                "content": "phone is +7 999 123 45 67",
                "tool_call_id": "call_abc",
                "name": "lookup_user",
            }
        ]
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(messages, model="gpt-4")
        sent = fake_provider.last_messages[0]
        assert sent["tool_call_id"] == "call_abc"
        assert sent["name"] == "lookup_user"
        assert sent["role"] == "tool"
        assert "+7" not in sent["content"]

    @pytest.mark.asyncio
    async def test_empty_content_handled(self, fake_provider, fake_redis, conv_id):
        """Some assistant messages have empty content (tool-call-only
        turns) — must not crash."""
        wrapped = PIITokenizingProvider(fake_provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(
                [{"role": "assistant", "content": ""}],
                model="gpt-4",
            )
        assert fake_provider.last_messages[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_message_without_content_key_handled(self, fake_provider, fake_redis, conv_id):
        """Defensive: malformed message dict без `content` key passes
        through с empty string default — no KeyError."""
        wrapped = PIITokenizingProvider(fake_provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(
                [{"role": "user"}],  # type: ignore[list-item]
                model="gpt-4",
            )
        # No crash — content defaulted to empty string.
        assert fake_provider.last_messages[0]["content"] == ""


# ---------------------------------------------------------------------------
# Router integration — confirm wrapper is auto-applied
# ---------------------------------------------------------------------------


class TestAuditEmit:
    """Phase F: audit row contract.

    Single audit emit point in the decorator — payload carries tokenized
    counts + structured metadata, NEVER raw user PII. Tests guard the
    contract from regression.

    Uses ``django_db(transaction=True)`` because the audit row is
    inserted from a worker thread (sync_to_async с
    ``thread_sensitive=False``) which can't share the test's in-memory
    transaction — pattern mirrors :mod:`apps.llm.tests.test_cost_tracker`.
    """

    pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_complete_emits_llm_call_completed_audit(self, fake_redis, conv_id):
        from asgiref.sync import sync_to_async

        from apps.audit.models import AuditLog

        provider = _FakeProvider(response_text="Ok")
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(
                [{"role": "user", "content": "Звоните +7 999 123 45 67"}],
                model="gpt-4",
            )
        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action="llm.call_completed")),
            thread_sensitive=False,
        )()
        assert rows
        row = rows[0]
        assert row.payload["op"] == "complete"
        assert row.payload["provider"] == "fake-openai"
        assert row.payload["messages_count"] == 1
        assert row.payload["pii_category_counts"] == {"PHONE": 1}
        assert row.payload["conversation_id"] == conv_id

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_audit_payload_never_contains_raw_pii(self, fake_redis, conv_id):
        """ADR-0011 §11.4 contract: NO raw user PII reaches audit log."""
        import json as _json

        from asgiref.sync import sync_to_async

        from apps.audit.models import AuditLog

        raw_phone = "+7 999 123 45 67"
        raw_email = "anna@example.ru"
        raw_cc = "4111 1111 1111 1111"

        provider = _FakeProvider(response_text="ok")
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(
                [
                    {
                        "role": "user",
                        "content": f"Звоню {raw_phone}, email {raw_email}, карта {raw_cc}",
                    }
                ],
                model="gpt-4",
            )
        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action="llm.call_completed")),
            thread_sensitive=False,
        )()
        assert rows
        for row in rows:
            payload_blob = _json.dumps(row.payload, ensure_ascii=False)
            assert "9991234567" not in payload_blob
            assert "+7 999 123 45 67" not in payload_blob
            assert "anna@example.ru" not in payload_blob
            assert "4111 1111 1111 1111" not in payload_blob

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_response_pii_counts_audited_when_present(self, fake_redis, conv_id):
        from asgiref.sync import sync_to_async

        from apps.audit.models import AuditLog

        tokenized = pii_tokenizer.tokenize("+7 999 123 45 67", conv_id)
        token = _extract_token(tokenized, "PHONE", 1)
        provider = _FakeProvider(response_text=f"Перезвоню {token}")
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.complete(
                [{"role": "user", "content": "+7 999 123 45 67"}],
                model="gpt-4",
            )
        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action="llm.call_completed")),
            thread_sensitive=False,
        )()
        assert rows
        assert rows[0].payload.get("response_pii_category_counts") == {"PHONE": 1}

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_embedding_also_emits_audit(self, fake_redis, conv_id):
        from asgiref.sync import sync_to_async

        from apps.audit.models import AuditLog

        provider = _FakeProvider()
        wrapped = PIITokenizingProvider(provider)
        with pii_tokenizer.pii_context(conv_id):
            await wrapped.embedding("anna@example.ru", model="emb-3")
        rows = await sync_to_async(
            lambda: list(
                AuditLog.all_tenants.filter(action="llm.call_completed", payload__op="embedding")
            ),
            thread_sensitive=False,
        )()
        assert rows
        assert rows[0].payload["pii_category_counts"] == {"EMAIL": 1}

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.asyncio
    async def test_no_audit_emitted_when_no_active_scope(self, fake_provider, fake_redis):
        """Scope inactive → pass-through, no PII-specific audit row."""
        from asgiref.sync import sync_to_async

        from apps.audit.models import AuditLog

        wrapped = PIITokenizingProvider(fake_provider)
        await wrapped.complete([{"role": "user", "content": "hi"}], model="gpt-4")
        rows = await sync_to_async(
            lambda: list(AuditLog.all_tenants.filter(action="llm.call_completed")),
            thread_sensitive=False,
        )()
        assert not rows


class TestRouterIntegration:
    """Smoke test that LLMRouter._load_provider returns wrapped instance.

    Detailed router behaviour (tier resolution, audit rows) lives в
    test_router.py — we only confirm here that the wrap was applied
    (so future provider additions don't accidentally skip protection)."""

    def test_router_wraps_with_pii_protection(self, settings: pytest.FixtureRequest, db):
        from apps.llm.pii_protected_provider import PIITokenizingProvider
        from apps.llm.router import LLMRouter, reset_router_cache

        settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
        settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
        reset_router_cache()

        router = LLMRouter()
        provider = router.get_provider(tenant=None)
        assert isinstance(provider, PIITokenizingProvider)
        # Wrapped provider's name still reflects real vendor.
        assert provider.name == "openai"
        reset_router_cache()
