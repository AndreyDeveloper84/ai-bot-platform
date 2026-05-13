"""Concrete LLM providers (Sprint 7 / L-track).

* :mod:`apps.llm.providers.openai_provider` — OpenAI (L2 / DRF-581)
* ``apps.llm.providers.anthropic_provider`` — Anthropic (L4 / DRF-583)

Pick providers via :class:`apps.llm.router.LLMRouter` (L5 / DRF-587),
not by importing these directly in skill code.
"""
