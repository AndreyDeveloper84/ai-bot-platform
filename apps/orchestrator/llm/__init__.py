"""LLM provider layer.

Sprint 1 ships a single OpenAI provider wrapped in a DIY circuit
breaker (per ADR-0005 — CR-3 mitigation). Multi-provider routing lands
in Sprint 6/7; the firm ``with_circuit_breaker(name, ...)`` API in
``apps.orchestrator.llm.breaker`` is designed for extract into
``ayla-ai-core`` once a second provider arrives (review revision 3C).
"""
