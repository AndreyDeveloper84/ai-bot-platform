# ADR-0005: Multi-LLM provider routing from Sprint 6

**Status:** Accepted — 2026-05-07 (deferred parts noted)

## Context

OpenAI is excellent but a single point of failure. Some prospect tenants have explicit preferences (Anthropic) or geographic constraints (Russian / EU residency). Today every skill in `mysite/maxbot/` calls `openai.chat.completions.create` directly — adding a second provider later would touch every call site. EPIC-P in the Linear backlog already calls for multi-provider; we wire the abstraction in Sprint 6 even though only one provider is implemented at first.

## Decision

Define an `LLMProvider` Protocol in Sprint 6:

```python
class LLMProvider(Protocol):
    async def complete(
        self, messages, *, tools=None, model=None, temperature=None, ...,
    ) -> LLMResponse: ...
```

The `OpenAIProvider` implementation lands in Sprint 6. `AnthropicProvider` lands in Sprint 6 only if time permits (otherwise Phase 1).

Routing is per-skill, declared in `apps.promptreg`:

```python
{ "skill": "faq", "primary": "openai/gpt-4o-mini",
  "fallback": "openai/gpt-4o", "cost_routing": False }
```

`apps.orchestrator.llm.router` resolves provider+model, applies the circuit breaker (CR-3 — open after 5 failures in 60s), and falls through to `fallback` on outage.

## Consequences

- **Easier:** switching a tenant to a different provider = config change in `apps.promptreg`.
- **Easier:** automatic fallback on provider outage prevents a hard bot down.
- **Easier:** replay fixtures are provider-agnostic — same `LLMResponse` schema, regardless of who answered.
- **Cost:** 1–2 dev-days per additional provider integration (request/response shape + tool-calling differences).
- **Risk:** structured outputs and tool-calling differ across providers (Anthropic uses XML, OpenAI uses JSON, Google uses both). Hidden inside the adapter; tested via per-provider conformance tests in Sprint 6.

## Alternatives considered

- **LiteLLM** as the abstraction layer. Considered — gives N providers free. Rejected for Phase 0; we want fewer abstractions to debug while the platform is young. Reconsider in Phase 1 when adding provider 3+.
- **Single OpenAI provider forever.** Rejected — known prospects already require Anthropic, and EU residency tenants can't legally use OpenAI's US endpoints.
- **Route at edge proxy (Envoy / nginx Lua).** Rejected. Loses observability — we want every call recorded by `apps.replay`, which means the routing has to happen *inside* the Python process.
