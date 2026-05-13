"""LLM provider abstraction (Sprint 7 / L-track).

A provider-agnostic interface for chat completion + embeddings. Sits
above the concrete Sprint 1 ``apps.orchestrator.llm.openai_provider``
so Sprint 7+ can swap in Anthropic without touching the call sites.

This package is intentionally NOT a Django app — it holds no models.
The protocol + dataclasses live here so callers (skills, orchestrator,
KB) can ``from apps.llm import LLMProvider, CompletionResult, ToolCall``
without dragging in any provider's HTTP client.

* :mod:`apps.llm.protocol` — Protocol + DTOs + exceptions (L1 / DRF-580)
* ``apps.llm.providers`` — concrete implementations (L2 OpenAI / L4 Anthropic)
* ``apps.llm.router`` — three-tier resolution (L5 / DRF-587)
"""
