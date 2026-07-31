"""Shared test double for the intent classifier's production LLM boundary (W0-B1D-01).

### Why this exists

Since #975 the runtime resolves the intent provider through::

    apps.orchestrator.intent_router.classify()
    → apps.llm.router.get_router().get_provider(tenant, skill="intent", op="complete")
    → provider.complete(...)

``get_router`` is imported function-locally inside
``intent_router._classify_production_path``, so the live lookup target is
the ``apps.llm.router`` module attribute — patching a module-level symbol on
``apps.orchestrator.intent_router`` would be inert.

Deterministic pipeline tests previously patched the legacy Sprint-1 seam
``apps.orchestrator.intent_router.OpenAIProvider``. After the #975 migration
that patch stopped applying: tests issued real HTTPS calls to
``api.openai.com`` with a fake key, intent degraded to ``unknown`` and the
pipeline fell into fallback/welcome paths.

This module patches the CURRENT boundary — ``apps.llm.router.get_router`` —
so it returns a :class:`FakeLLMRouter` that:

* serves a deterministic :class:`FakeProvider` for ``skill="intent"``
  (pinned IntentDecision JSON on the success path, or a configurable
  exception for the failure path);
* serves a benign no-network plain-text :class:`FakeProvider` for every
  other skill, so a skill dispatched downstream of the pinned intent
  (e.g. FAQ) never builds a real vendor client;
* optionally delegates named skills to a real :class:`LLMRouter` instance
  (``delegate_skills=("faq",)``) for tests that mock that skill's provider
  seam themselves via ``patch.object`` on the concrete provider class;
* records every ``get_provider`` / ``complete`` call so tests can assert
  the tenant/skill context the runtime actually passed.

No sockets are banned and no production module is modified — the patch is
applied with :func:`unittest.mock.patch` inside the test process only. The
real router singleton is dropped before and after each patched block via
:func:`apps.llm.router.reset_router_cache` so no cached provider (or its
SDK client) leaks across tests.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from apps.llm.protocol import CompletionResult
from apps.llm.router import LLMRouter, reset_router_cache

# Default pinned IntentDecision payload — same shape the legacy
# ``_fake_llm`` fixtures returned pre-#975.
DEFAULT_INTENT_PAYLOAD: dict[str, Any] = {
    "intent": "faq",
    "skill": "faq",
    "confidence": 0.9,
    "risk_level": "low",
    "missing_slots": [],
    "reply_mode": "text",
    "needs_rag": False,
    "needs_tool": False,
}

#: Plain-text answer returned by the generic (non-intent) fake provider.
GENERIC_REPLY_TEXT = "Тестовый ответ ассистента."


class FakeProvider:
    """Deterministic ``LLMProvider`` stand-in — never touches the network.

    Two modes:

    * ``payload`` set → :meth:`complete` returns the JSON-serialised payload
      as :attr:`CompletionResult.text` (intent-classifier shape).
    * ``payload`` is ``None`` → :meth:`complete` returns
      :data:`GENERIC_REPLY_TEXT` with no tool calls (a skill downstream of
      the pinned intent treats it as a direct, retrieval-free answer).

    Failure scenario: pass ``error=`` and :meth:`complete` raises it —
    the intent router converts known ``LLMError`` subclasses into the safe
    fallback IntentDecision, exercising the same path a vendor outage would.
    """

    name = "fake"
    default_completion_model = "fake-completion-model"
    default_embedding_model = "fake-embedding-model"

    def __init__(
        self, payload: dict[str, Any] | None = None, *, error: BaseException | None = None
    ) -> None:
        self._payload = payload
        self._error = error
        self.complete_calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        self.complete_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        if self._error is not None:
            raise self._error
        text = json.dumps(self._payload) if self._payload is not None else GENERIC_REPLY_TEXT
        return CompletionResult(
            text=text,
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=20,
            model=model or self.default_completion_model,
            provider=self.name,
            finish_reason="stop",
        )

    async def embedding(self, text: str, *, model: str | None = None) -> list[float]:
        return [0.5] * 8


class FakeLLMRouter:
    """Router stand-in returned by the patched ``get_router``.

    * ``skill="intent"`` → the deterministic intent provider; asserts the
      production contract that the intent path always carries a tenant.
    * ``skill`` in ``delegate_skills`` → forwarded to a real
      :class:`LLMRouter` instance so tests that patch a concrete provider
      class keep their seam. The delegate is a private instance — the
      process-wide singleton (and its provider cache) is never touched.
    * anything else → the generic plain-text fake.
    """

    def __init__(
        self,
        *,
        intent_payload: dict[str, Any] | None = None,
        intent_error: BaseException | None = None,
        delegate_skills: tuple[str, ...] = (),
    ) -> None:
        self.intent_provider = FakeProvider(
            payload=dict(DEFAULT_INTENT_PAYLOAD if intent_payload is None else intent_payload),
            error=intent_error,
        )
        self.generic_provider = FakeProvider()
        self.delegate = LLMRouter()
        self.delegate_skills = frozenset(delegate_skills)
        self.get_provider_calls: list[dict[str, Any]] = []

    def get_provider(
        self,
        tenant: Any = None,
        *,
        skill: str = "",
        op: str = "complete",
        prefer_fallback_from: str | None = None,
    ) -> Any:
        self.get_provider_calls.append(
            {
                "tenant": tenant,
                "skill": skill,
                "op": op,
                "prefer_fallback_from": prefer_fallback_from,
            }
        )
        if skill == "intent":
            if tenant is None:
                raise AssertionError(
                    "intent provider requested without tenant — production "
                    "pipeline always supplies one (legacy path bypasses the router)"
                )
            return self.intent_provider
        if skill in self.delegate_skills:
            return self.delegate.get_provider(
                tenant, skill=skill, op=op, prefer_fallback_from=prefer_fallback_from
            )
        return self.generic_provider


@contextmanager
def patch_intent_llm(
    *,
    intent_payload: dict[str, Any] | None = None,
    intent_error: BaseException | None = None,
    delegate_skills: tuple[str, ...] = (),
) -> Iterator[FakeLLMRouter]:
    """Patch ``apps.llm.router.get_router`` to return a :class:`FakeLLMRouter`.

    Yields the fake router so tests can inspect ``get_provider_calls`` /
    ``intent_provider.complete_calls``. The real router singleton cache is
    reset on entry and exit so no state leaks across tests.
    """
    fake = FakeLLMRouter(
        intent_payload=intent_payload,
        intent_error=intent_error,
        delegate_skills=delegate_skills,
    )
    reset_router_cache()
    try:
        with patch("apps.llm.router.get_router", return_value=fake):
            yield fake
    finally:
        reset_router_cache()
