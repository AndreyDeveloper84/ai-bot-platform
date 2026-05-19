"""Concrete LLM providers (Sprint 7 / L-track + Sprint 8 / EPIC-P).

* :mod:`apps.llm.providers.openai_provider` — OpenAI (L2 / DRF-581)
* :mod:`apps.llm.providers.anthropic_provider` — Anthropic (L4 / DRF-583)
* :mod:`apps.llm.providers.deepseek_provider` — DeepSeek (DRF-280 T2;
  OpenAI-compatible API, primary humanizer per DRF-279 spike)

Pick providers via :class:`apps.llm.router.LLMRouter` (L5 / DRF-587),
not by importing these directly in skill code.
"""
