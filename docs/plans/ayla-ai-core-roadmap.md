# ayla-ai-core Roadmap (v0.7 → v1.0)

> **Status**: 2026-05-13. v0.7.0 just released (PR #6 pending merge). This doc covers the post-v0.7.0 release plan based on the six-agent architectural review (Backend Architect / AI Engineer / Security Engineer / Code Reviewer / Performance Benchmarker / Codebase Onboarding) and consumer roadmap (ai-bot-platform Sprint 7 → Phase 3).
>
> **Audience**: ayla-ai-core maintainer (1.0 FTE) + ai-bot-platform consumer team. Linear is operational tracker; this doc explains **why each release exists, what it changes, and who it affects** — context that Linear issues can't carry.

## Library overview

`ayla-ai-core` is a **shared AI orchestration library** providing prompt rendering, anti-hallucination primitives, tool dispatch, and brand voice config for LLM-driven products. Pure-Python, no Django coupling at the call site (though Django is a transitive dep — see v0.8.0 plans).

**Architecture rule** ([`ai-bot-platform/docs/architecture.md:158`](../architecture.md#L158)): ayla-ai-core is the **only** allowed AI library carry-over for ai-bot-platform. Direct calls to `openai`/`anthropic`/`google-genai` go through `apps.orchestrator.llm` for circuit-breaker + replay coverage.

## Consumers + version pins

| Consumer | Version | Lifecycle |
|---|---|---|
| `mysite/maxbot/` (production MAX bot, Формула тела) | v0.6.0 | **FROZEN per Sprint 0** (2026-05-09). Stays on 0.6.0 forever — replaced by ai-bot-platform Sprint 10 cutover. |
| `ai-bot-platform` (Django multi-tenant SaaS) | v0.7.0 (pending) → v0.7.x → v0.8.0 → ... | Active development. Pins by **SHA, not tag** (security: tags are force-pushable). |
| Ayla marketplace (future) | v0.8.0+ greenfield | Phase 2+. Builds on the post-refactor library. |

## Current state (v0.7.0, shipped 2026-05-13)

- 6 modules, 30 exports, 139 tests passing
- 4 P0 bug fixes (B1/B2/B3/B4 — see CHANGELOG)
- BREAKING: `SpecialistContext.tenant_id` mandatory + resolver kwarg `tenant_id`
- Tag `v0.7.0` pushed; SHA `2675836240fe...` captured for ai-bot-platform pin
- PR #6 in ayla repo open, awaiting merge

## Architectural findings to address (six-agent review summary)

### Strengths to preserve and double down on

1. **`SpecialistContext[ID_T]` anti-hallucination IP** — `candidate_ids: frozenset` O(1) ID validation, cross-validation in handlers. Production-tested 30+ days. **Generalize** to `CandidateContext[ID_T, ItemT]` in v0.8.0 so FAQ chunks, services, foods, etc all get the same anti-hallucination guarantee.
2. **`BrandVoiceConfig` + `Example` frozen dataclasses** — pure data, no behavior, no Django, no template coupling. Survives any breaking change.
3. **`Protocol` + duck-typed `tool_call`** — OpenAI SDK upgrades don't break dispatch; consumers back `ConversationStore` with anything (Django models / dicts / etc).
4. **Zero direct DB / Redis / file I/O in `src/`** — clean library boundary.
5. **CPU cost <10ms/turn** — not a bottleneck; OpenAI HTTP-call is the only real latency surface.

### Issues sorted by severity

| Severity | Issue | First addressed in |
|---|---|---|
| P0 | B1/B2/B3/B4 (4 fixes) | **v0.7.0 — DONE** |
| P1 | `_compose_messages` empty-content assistant turns | v0.7.0 (B1) |
| P1 | `handle_show_masters` score/reason misalignment | v0.7.0 (B2) |
| P1 | `tenant_id` not enforced | v0.7.0 (B3) |
| P1 | Prompt-injection via `extra_hint` / `client_name` | v0.7.0 (B4) |
| P1 | `django>=5.0,<6.0` hard runtime dep | v0.7.0 tightened to `>=5.2` → **v0.8.0 drops Django entirely** |
| P1 | Parallel `tool_calls` silently dropped | **v0.7.2** |
| P1 | Cross-tenant guard relies on caller's `context_builder` | v0.7.0 partial → **v0.7.2 stricter** |
| P2 | O(N) scans in `handle_show_slots` / `handle_confirm_booking` | **v0.7.2** |
| P2 | `TOOL_DEFINITIONS` module-level mutable list | **v0.7.2** (freeze via `MappingProxyType`) |
| P2 | `today=date.today()` hidden non-determinism for replay | **v0.7.3** |
| P2 | No latency/tokens in `ChatResponseDTO` (only logged) | **v0.7.3** |
| P2 | Logger names not tenant-aware | **v0.7.3** |
| P2 | Anthropic compatibility — orchestrator hard-expects OpenAI shape | **v0.8.0** built-in adapter |
| P2 | `SYSTEM_PROMPT_TEMPLATE` monolithic, booking-coupled | **v0.8.0** pluggable composer |
| P2 | `AIConcierge` class booking-coupled (unusable for non-booking) | **v0.8.0** + **v0.9.0** domain split |
| P3 | No CHANGELOG.md (pre-v0.7.0) | v0.7.0 done |
| P3 | `_safe_int` / `_safe_uuid` in `__all__` with leading underscore | **v0.8.0** rename to public names |
| P3 | Index misalignment in `confirm_booking` if `id_parser` and `tool_definitions` schema disagree | **v0.7.1** opportunistic |

---

# Release roadmap

## v0.7.1 — Adoption patch

**Target**: ~1 week after v0.7.0 PR merges. Driven by ai-bot-platform Sprint 7 adoption findings.

**Rationale**: When ai-bot-platform pins v0.7.0 SHA in Track A2 and Phase 1 starts using `dispatch_tool_call` in booking flow, real prod traffic will surface issues that no test caught. v0.7.1 is the **rapid-response slot** — purely bug fixes, no scope creep.

**Scope**:
- Whatever bugs Sprint 7 surfaces (placeholder — fill in as found)
- One known opportunistic fix: **`id_parser` / `tool_definitions` consistency check** in `AIConcierge.__init__` (Code Reviewer B7). Currently if a consumer constructs `AIConcierge` with `tool_definitions=build_tool_definitions("string")` but forgets `id_parser=_safe_uuid`, every tool call silently falls back to `ASK_CLARIFICATION`. Add an assertion that fails loud at init.

**Breaking**: No. Patch-level, backwards-compatible by definition.

**Consumer impact**:
- ai-bot-platform: bump SHA pin, no code changes
- mysite/maxbot: skip (frozen)

**Necessity**: P1. Without a rapid-response slot, real bugs from Sprint 7 adoption block Phase 1 booking port. With the slot, fixes ship within days.

**Impact**: Stability. ai-bot-platform team trusts ayla updates enough to keep upgrading.

**Effort**: 1-3 days depending on bug count.

**Gate**: PR opened within 48h of first reported issue + all reported issues closed before tag.

---

## v0.7.2 — Performance + parallel tool_calls patch

**Target**: ~2 weeks. Sprint 7 closing time.

**Rationale**: Two issues from the Performance Benchmarker + Code Reviewer reviews are **latent risks** — invisible at Phase 0 traffic (50–100 msgs/day single-tenant) but break at Phase 2 SaaS scale (10K msgs/day). Fix while small.

### Scope

#### Task 1: O(1) lookups in tool handlers (Performance P2)

**Where**: `src/ayla_ai_core/tool_handlers.py:221, 274`

**Problem**: `handle_show_slots` and `handle_confirm_booking` do `next((c for c in context.candidates if c.id == master_id), None)` — linear scan on every tool call. Plus `master_service_ids = {sid for sid, _ in master.services}` rebuilt per call. At N=20 specialists: 1–2 ms total, invisible. At N=1000 (future tenant with large catalog): 50–100 ms — breaches the per-turn budget.

**Solution**: Precompute on `SpecialistContext` build:
```python
@dataclass(frozen=True)
class SpecialistContext[ID_T]:
    candidates: list[SpecialistCandidate[ID_T]]
    candidate_ids: frozenset[ID_T]
    candidate_service_ids: frozenset[ID_T]
    summary_text: str
    tenant_id: str
    # NEW v0.7.2:
    by_id: dict[ID_T, SpecialistCandidate[ID_T]]  # O(1) candidate lookup
```

And `SpecialistCandidate.service_id_set: frozenset[ID_T]` for cross-validation. Handlers become O(1).

**Necessity**: P2 now, P0 at scale. The cost of fixing later (after consumers depend on the lookup pattern) is higher than fixing now.

**Impact**: Per-turn latency stays flat as catalog grows. Anti-hallucination layer no longer degrades at scale.

#### Task 2: Parallel `tool_calls` support (Code Reviewer P1)

**Where**: `src/ayla_ai_core/orchestrator.py:213`

**Problem**: `_parse_completion` reads `completion.choices[0].message.tool_calls[0]` — silently drops calls 1+. OpenAI's gpt-4o defaults `parallel_tool_calls=True` and routinely emits 2-3 parallel calls. Currently they vanish.

**Solution**: Either (a) pass `parallel_tool_calls=False` to disable (simple), or (b) iterate all tool_calls in `_parse_completion` and run dispatcher on each. **Recommended (b)** — multiple tool calls in one turn is a real LLM pattern (e.g., "show masters AND show slots simultaneously"), disabling cripples future skills.

```python
# v0.7.2 _parse_completion
tool_calls = msg.tool_calls or []
results = [
    dispatch_tool_call(tc, context, ...)
    for tc in tool_calls
]
return _merge_results(results) or _parse_text(msg)
```

`_merge_results` strategy TBD — likely first non-clarification wins, or compose action_data list.

**Necessity**: P1. Silent data loss in production right now (gpt-4o defaults).

**Impact**: Bot can answer compound questions ("show me masters for back massage AND tell me how to get there") in one turn instead of two.

#### Task 3: `TOOL_DEFINITIONS` immutability (Code Reviewer P2)

**Where**: `src/ayla_ai_core/tools.py:210`

**Problem**: `TOOL_DEFINITIONS` is a module-level **mutable list** of dicts. One consumer mutating it (e.g., monkey-patching to add a custom tool) breaks every other consumer in-process.

**Solution**: Wrap in `MappingProxyType` (for dicts) and tuple (for list) so the constant is truly read-only:
```python
TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = tuple(
    MappingProxyType(d) for d in build_tool_definitions("integer")
)
```

Consumers needing mutation use `build_tool_definitions(...)` directly (always returns a fresh list).

**Necessity**: P2 — defensive immutability. Trivial fix.

**Impact**: One less footgun for consumers.

#### Task 4: Stricter cross-tenant guard

**Where**: `src/ayla_ai_core/tool_handlers.py` — `_assert_tenant_id_set` (v0.7.0 added) currently only checks NOT EMPTY.

**Problem**: B3 in v0.7.0 fixed "tenant_id missing" but doesn't check that `tenant_id` matches the resolver's row. Currently if resolver returns row from another tenant, code proceeds — only the `master_tenant_mismatch` fallback (also v0.7.0) catches it, and ONLY if the resolver bothers to return `tenant_id` in the dict.

**Solution**: Mandate that resolvers return `tenant_id` in the result dict OR explicitly opt out via `__resolver_skips_tenant_check__ = True` attribute. Default = strict.

**Necessity**: P1. Real cross-tenant leak window if consumer's resolver doesn't include tenant_id (currently optional).

**Impact**: Multi-tenant correctness guaranteed even for sloppy resolver implementations.

### Tests

- 5 new perf tests asserting O(1) behavior up to N=1000 candidates
- 4 new parallel-tool_calls tests (single, double, triple, with error in middle)
- 3 immutability tests (`TOOL_DEFINITIONS` cannot be mutated, fresh build returns new list)
- 4 cross-tenant strict-guard tests

### Breaking

- `SpecialistContext` gains `by_id` field — **backwards compatible** if callers use the builder. Direct constructor users (rare) need to add it.
- `SpecialistCandidate` gains `service_id_set` field — same.
- Resolvers that don't return `tenant_id`: get **warning** in v0.7.2, **error** in v0.8.0. Documented in CHANGELOG.

**Consumer impact**:
- ai-bot-platform: ~1 day to bump pin + verify
- Future Ayla marketplace: greenfield, builds against v0.7.2 directly

**Necessity**: P1. Without this, ai-bot-platform Phase 2 SaaS (10K msgs/day target) hits latency cliff.

**Impact**: Latency stays sub-3s at scale. Anti-hallucination resolver layer becomes safe-by-default.

**Effort**: ~1 week.

**Gate**: Latency benchmark passes at N=1000 candidates (p95 ≤ 50ms per handler call); parallel tool_calls test green.

---

## v0.7.3 — Observability + replay determinism patch

**Target**: ~2 weeks. Aligned with ai-bot-platform Sprint 8 (Observability stack).

**Rationale**: Sprint 8 ships OpenTelemetry + Sentry + structured logs. Ayla currently emits logs to `ayla_ai_core.tool_handlers` / `ayla_ai_core.orchestrator` loggers with no tenant_id — incident response can't filter by tenant. Plus replay-mode determinism needs frozen-clock hooks beyond what A3 (already shipped in ai-bot-platform DRF-617) covers at the adapter layer.

### Scope

#### Task 1: Tenant-aware logging

**Where**: `src/ayla_ai_core/orchestrator.py:82`, `tool_handlers.py:63`

**Problem**: Logger names are global strings. Log line `ai_concierge: conv=<uuid> action=confirm_booking ...` (orchestrator.py:409) has no `tenant_id` — operators can't filter incident logs to "show me only formula-tela errors".

**Solution**: Two changes:
1. Add `tenant_id` to every log line via `extra=` dict:
   ```python
   logger.info(
       "ai_concierge: conv=%s action=%s tokens=%d/%d latency=%dms",
       conv_id, action_type, tin, tout, latency,
       extra={"tenant_id": context.tenant_id},
   )
   ```
2. Document a logging Filter pattern for consumers (`ai-bot-platform` will install it):
   ```python
   class TenantContextFilter(logging.Filter):
       def filter(self, record):
           record.tenant_id = getattr(record, "tenant_id", "")
           return True
   ```

**Necessity**: P1 for multi-tenant SaaS. Without this, an incident affecting one tenant pollutes logs for all.

**Impact**: 152-ФЗ data-subject request can scope to one user/tenant. Sprint 8 observability dashboards can pivot by tenant.

#### Task 2: Telemetry in `ChatResponseDTO`

**Where**: `src/ayla_ai_core/orchestrator.py:97-110, 391-393`

**Problem**: Latency, tokens, model used — all measured but only **logged**, not in the return value. Consumers wanting to record metrics must parse log strings or wrap the openai_client themselves.

**Solution**: Extend `ChatResponseDTO`:
```python
@dataclass(frozen=True)
class ChatResponseDTO:
    conversation_id: UUID
    content: str
    action_type: str | None
    action_data: dict | None
    # NEW v0.7.3:
    latency_ms: int
    tokens_in: int
    tokens_out: int
    model: str
    provider: str  # "openai" | "anthropic" | ...
```

Consumers can emit Prometheus/StatsD metrics directly from the DTO without parsing.

**Necessity**: P2 → P0 at SaaS scale. Without telemetry-as-data, ops fly blind on cost per tenant per day.

**Impact**: K13 telemetry on ai-bot-platform side (was deferred to Sprint 8 dashboards) gets cleaner data source.

#### Task 3: Replay-determinism — frozen clock in ayla

**Where**: `src/ayla_ai_core/prompts.py:207` (caller-supplied `today`), `src/ayla_ai_core/orchestrator.py:84` (history_limit not token-bounded)

**Problem**: `today=date.today()` defaults to wall-clock — replay-mode runs need pinning. Currently the caller (ai-bot-platform adapter A1) handles it via `frozen_now` kwarg passed in. But other consumers might not — and history loading from `ConversationStore` has no replay-clock hook.

**Solution**: Optional `frozen_now: datetime | None` parameter on `AIConcierge.send_message`. When set, propagates to:
1. `today=frozen_now.date()` in prompt rendering (overrides any default)
2. `history_filter_max_age` based on frozen_now (consumer can scope history)
3. New `ReplayDeterminismError` raised if any sub-call would introduce non-determinism (e.g., random.* in a custom dispatcher)

**Necessity**: P2 for ayla as a library; P0 for any consumer doing replay testing. ai-bot-platform A3 already handled this at the adapter layer (DRF-617) but **other consumers** (future Ayla marketplace) need it at ayla layer.

**Impact**: Replay infrastructure works for all ayla consumers, not just ai-bot-platform.

#### Task 4: Token-budget guard for history

**Where**: `src/ayla_ai_core/orchestrator.py:84` — `history_limit=10` is count-based, not token-based

**Problem**: 10 messages × 5KB/message = 50KB context. gpt-4o-mini limit is 128K but cost scales linearly. A chatty user could blow $1/turn.

**Solution**: Add `history_token_budget: int = 4000` parameter. Walk history backwards from newest, include messages until token budget exhausted. Use tiktoken (already a runtime dep candidate — pin it).

**Necessity**: P2. Cost ceiling per turn. Currently the bot can rack up unbounded cost on a long conversation.

**Impact**: Per-turn cost has a hard upper bound. Cost dashboard gets clean baseline.

### Tests

- 5 logging tests (extra=tenant_id propagates correctly, Filter integration)
- 4 telemetry tests (DTO fields populated, deterministic across runs)
- 5 replay-determinism tests (frozen_now propagates everywhere, raises on non-deterministic dispatcher)
- 3 history-token-budget tests (budget respected, falls back to message count if no tiktoken)

### Breaking

`ChatResponseDTO` adds fields — backwards-compat (consumers reading the existing fields don't break; constructor with positional args might). Document migration.

**Consumer impact**:
- ai-bot-platform Sprint 8: adopts immediately, simplifies observability instrumentation
- Future consumers: get observability for free

**Necessity**: P1 for multi-tenant prod readiness.

**Impact**: Library becomes operationally legible. Cost/latency dashboards have first-class data sources.

**Effort**: ~1-2 weeks.

**Gate**: ai-bot-platform Sprint 8 dashboards use `ChatResponseDTO` fields without parsing logs.

---

## v0.8.0 — Architectural refactor (BREAKING)

**Target**: ~4-6 weeks. Aligned with ai-bot-platform Phase 1 booking port (~Sep 2026) and Phase 2 prep.

**Rationale**: Three top Backend Architect findings + AI Engineer + Code Reviewer all converge on the same root cause: **the library is booking-domain-coupled at the architectural seams**, not just in the booking handlers. Specifically:

1. `SYSTEM_PROMPT_TEMPLATE` is monolithic and 70% booking-funnel-specific (mentions ДОСТУПНЫЕ МАСТЕРА, show_slots, confirm_booking, no-phone-request).
2. `AIConcierge` requires a `SpecialistContext` (not a generic `CandidateContext`) — unusable for FAQ chunks, services-only flows, nutrition foods.
3. `django>=5.0,<6.0` as **runtime** dep of a "core" library blocks any consumer's Django upgrade.

v0.8.0 unwinds all three. It's the **biggest breaking change** in the library's lifecycle and unlocks Phase 2 (booking) + Phase 3 (nutrition) as clean plug-ins instead of forks.

### Scope

#### Task 1: Generalize `SpecialistContext` → `CandidateContext[ID_T, ItemT]`

**Where**: New `src/ayla_ai_core/context.py` design

**Problem**: `SpecialistContext` works only for "specialist with services" shape. FAQ chunks need `(chunk_id, content, score, source_uri)`. Nutrition needs `(food_id, name, calories_per_100g)`. Each consumer is forced to either fake a `SpecialistContext` (anti-pattern) or skip anti-hallucination entirely.

**Solution**: Two-axis generic:
```python
@dataclass(frozen=True)
class CandidateContext[ID_T: (int, UUID, str), ItemT]:
    candidates: list[ItemT]
    candidate_ids: frozenset[ID_T]
    summary_text: str
    tenant_id: str
    # Hook for cross-validation — domain-specific predicate
    cross_validator: Callable[[ItemT, dict[str, Any]], bool] | None = None
```

Each domain registers its own `ItemT` shape:
- Booking: `SpecialistCandidate` (existing) → `CandidateContext[int, SpecialistCandidate[int]]`
- FAQ: `KbChunk(id, text, score, doc_type, source_uri)` → `CandidateContext[str, KbChunk]`
- Nutrition: `FoodItem(id, name, kcal, ...)` → `CandidateContext[int, FoodItem]`

`SpecialistContext` is **kept as a deprecated alias** with `__deprecated__` warning. Removed in v0.9.0.

**Necessity**: P1. Without this, Phase 3 nutrition (ai-bot-platform Phase 3) is blocked from using ayla's anti-hallucination IP.

**Impact**: Anti-hallucination becomes the **default contract** across all skills, not just booking. The 30-day-tested IP gets reused everywhere.

#### Task 2: Pluggable `PromptComposer` (sections)

**Where**: New `src/ayla_ai_core/prompts/composer.py`

**Problem**: `SYSTEM_PROMPT_TEMPLATE` (`prompts.py:87-173`) is a 173-line monolith with booking-funnel-specific rules baked in. Consumers wanting non-booking flows must either:
- Reuse with `extra_hint` (docstring says "soft hint, wrong layer")
- Fork the template (loses upstream IP)
- Reimplement from scratch (current FAQ approach in ai-bot-platform DRF-590 — works but duplicates the anti-hallucination instructions)

**Solution**: Replace monolith with composable sections:
```python
class PromptSection(Protocol):
    def render(self, ctx: dict[str, Any]) -> str: ...

@dataclass(frozen=True)
class PromptComposer:
    sections: list[PromptSection]

    def render(self, ctx: dict[str, Any]) -> str:
        return "\n\n".join(s.render(ctx) for s in self.sections)


# Pre-built section library
class VoiceSection: ...        # brand voice + persona
class DateContextSection: ...   # today, client_name, bookings_count
class CandidateSection: ...     # render top-N candidates with IDs
class RulesSection: ...         # behavior rules (parameterizable)
class ExamplesSection: ...      # few-shot examples
class AntiHallucinationSection: ...  # "use only these IDs"
class HandoffSection: ...       # when to bounce to manager


# Booking skill composer (mimics v0.7.x SYSTEM_PROMPT_TEMPLATE)
BOOKING_COMPOSER = PromptComposer(sections=[
    VoiceSection(),
    DateContextSection(),
    CandidateSection(label="ДОСТУПНЫЕ МАСТЕРА"),
    RulesSection(rule_set="booking_funnel"),
    ExamplesSection(),
    AntiHallucinationSection(scope="masters_and_services"),
    HandoffSection(triggers=["менеджер", "оператор"]),
])


# FAQ composer (what ai-bot-platform DRF-590 reimplements)
FAQ_COMPOSER = PromptComposer(sections=[
    VoiceSection(),
    DateContextSection(),
    CandidateSection(label="НАЙДЕННЫЕ ДОКУМЕНТЫ"),  # KB chunks
    RulesSection(rule_set="grounded_answer"),
    AntiHallucinationSection(scope="cite_only_retrieved"),
])
```

`render_system_prompt(...)` becomes a thin wrapper that picks a default composer. Consumers can build their own composers from sections without reimplementing.

**Necessity**: P1. Phase 2 native booking + Phase 3 nutrition both need their own prompt shapes. Without composer, each phase re-forks the template.

**Impact**: Adding a new skill domain becomes a 50-LOC composer instead of a 200-LOC template fork.

#### Task 3: Drop Django runtime dependency

**Where**: `pyproject.toml:26` + `src/ayla_ai_core/orchestrator.py:50` (only `asgiref` usage)

**Problem**: `django>=5.2,<6.0` is a **runtime** dep of a "core" library. The only actual usage is `from asgiref.sync import sync_to_async` (which doesn't need Django itself — `asgiref` is independent). Any consumer wanting Django 6.0 must wait for ayla to bump.

**Solution**:
1. Move `django` to `[project.optional-dependencies.django]` extra
2. Direct dep on `asgiref>=3.7` (already there)
3. Document in README: "ayla-ai-core works without Django; consumers using it inside Django apps install via `pip install ayla-ai-core[django]`"

**Necessity**: P1 architectural hygiene. Backend Architect flagged this as worst issue.

**Impact**: Django upgrade in ai-bot-platform no longer blocked by ayla cadence. Future non-Django consumers (e.g., FastAPI service) install lighter.

#### Task 4: Drop deprecated `MasterContext` / `MasterCandidate` aliases

**Where**: `src/ayla_ai_core/context.py:146-160`

**Problem**: Backward-compat aliases from the DRF-237 → DRF-238 transition. v0.7.0 still re-exports them. After 6+ months they're dead weight.

**Solution**: Remove. mysite/maxbot (the only legit user) is frozen on v0.6.0 anyway.

**Necessity**: P2 cleanup. Reduces `__all__` surface by 3 symbols.

**Impact**: Smaller API surface = easier v1.0 freeze later.

#### Task 5: Anthropic response-shape adapter built-in

**Where**: New `src/ayla_ai_core/providers/anthropic_adapter.py`

**Problem**: AI Engineer flagged — `orchestrator.py:204` hard-expects OpenAI shape (`completion.choices[0].message.tool_calls`). Anthropic returns `content` blocks. ai-bot-platform handles this in its router (DRF-587), but every consumer must reimplement.

**Solution**: Ship a `def to_openai_shape(anthropic_response) -> openai_shaped` adapter in ayla. Consumers pass any provider's raw response through it before `_parse_completion`.

**Necessity**: P2 for ayla as standalone library. P0 for any non-ai-bot-platform consumer.

**Impact**: Anthropic / future providers (Yandex / DeepSeek) integrate cleanly.

#### Task 6: Rename `_safe_int` / `_safe_uuid` to public names

**Where**: `src/ayla_ai_core/tool_handlers.py:88, 103`

**Problem**: Code Reviewer P3 — names have leading underscore (Python convention: private) but are in `__all__` (public surface). Inconsistent.

**Solution**: Rename to `parse_int` / `parse_uuid` (public). Keep `_safe_int` / `_safe_uuid` as deprecated aliases until v0.9.0.

**Necessity**: P3 hygiene.

**Impact**: Consumer code reads more naturally; static analyzers stop complaining.

### Tests

- ~25 new tests covering generic `CandidateContext`, `PromptComposer` sections, Django-free import path, Anthropic adapter
- Migration tests proving v0.7.x SpecialistContext-using code can run unchanged (except for the deprecation warnings)

### Breaking changes summary

| Change | Migration path |
|---|---|
| `CandidateContext[ID_T, ItemT]` introduced | `SpecialistContext` aliased to `CandidateContext[int, SpecialistCandidate[int]]` — still works |
| `PromptComposer` introduced | `render_system_prompt(...)` keeps current signature, just builds composer internally |
| Django moved to extras | Pip install `ayla-ai-core[django]` instead of `ayla-ai-core` |
| `MasterContext` / `MasterCandidate` removed | Use `SpecialistContext[int]` / `SpecialistCandidate[int]` |
| `parse_int` / `parse_uuid` public | `_safe_int` / `_safe_uuid` deprecated aliases (removed in v0.9.0) |

**Consumer impact**:
- ai-bot-platform: Phase 1 booking port can use generic `CandidateContext`. Phase 3 nutrition gets anti-hallucination. ~1-2 weeks integration work.
- Future Ayla marketplace: builds on v0.8.0 directly, no migration

**Necessity**: P0 long-term. Without this, every new domain forks ayla.

**Impact**: Library is finally domain-agnostic. Anti-hallucination IP is reusable everywhere. Plugin ecosystem possible.

**Effort**: ~4-6 weeks (1 dev).

**Gate**: ai-bot-platform Phase 1 booking port uses v0.8.0 generic types without forking.

---

## v0.9.0 — Domain split

**Target**: ~4 weeks after v0.8.0. ~Q1 2027 alongside ai-bot-platform Phase 3 nutrition.

**Rationale**: v0.8.0 made the **core** library domain-agnostic. v0.9.0 finishes the job by extracting the **booking-domain handlers** into a separate package. After v0.9.0:
- `ayla-ai-core` = pure core (PromptComposer, CandidateContext, dispatch_tool_call framework, voice, anti-hallucination)
- `ayla-ai-core-booking` = booking handlers (`SHOW_MASTERS`, `SHOW_SLOTS`, `CONFIRM_BOOKING`, etc) + their tools
- `ayla-ai-core-faq` = (new) FAQ-skill handlers
- `ayla-ai-core-nutrition` = (new) nutrition-domain handlers

### Scope

#### Task 1: Extract booking handlers → `ayla-ai-core-booking`

**Where**: New package, separate PyPI / git+ install

**Problem**: `tool_handlers.handle_*` (5 functions), `tools.build_tool_definitions`, `tools.ActionType` constants — all booking-specific. Currently bundled in core. Forces non-booking consumers to either ignore (memory bloat) or fork.

**Solution**: Move all 5 handlers + tool definitions + booking-specific examples into `ayla-ai-core-booking` package. Core library has zero booking references. ai-bot-platform Phase 2.3 imports both: `pip install ayla-ai-core[booking]`.

**Necessity**: P1 for clean ecosystem. P2 for ai-bot-platform (already done well via composer).

**Impact**: Library install size shrinks. Memory footprint smaller. Booking domain can iterate independently.

#### Task 2: Plugin architecture for `dispatch_tool_call`

**Where**: `src/ayla_ai_core/tool_handlers/dispatcher.py`

**Problem**: Currently `dispatch_tool_call` hard-codes routing to the 5 booking handlers. Adding a new handler requires either modifying ayla or providing a custom `tool_dispatcher` (which loses the built-in fallback to clarification).

**Solution**: Plugin registry:
```python
# ayla-ai-core core
class DispatcherRegistry:
    handlers: dict[str, Callable[..., ToolResult]] = {}

    @classmethod
    def register(cls, action_name: str):
        def decorator(fn):
            cls.handlers[action_name] = fn
            return fn
        return decorator

# ayla-ai-core-booking package
@DispatcherRegistry.register(ActionType.SHOW_MASTERS)
def handle_show_masters(...): ...

# ai-bot-platform Phase 3 nutrition
@DispatcherRegistry.register("recognize_food")
def handle_recognize_food(...): ...
```

`dispatch_tool_call` dispatches via registry; unknown actions → clarification fallback.

**Necessity**: P1 for true plugin ecosystem.

**Impact**: New skills land as packages without modifying core.

#### Task 3: Refactor existing consumers

**Where**: ai-bot-platform Phase 2.3 + Phase 3 prep work

**Problem**: After v0.9.0, ai-bot-platform must explicitly install booking handlers via extra. F2 FAQ skill already uses its own handlers; just need to formalize.

**Solution**: Migration PR in ai-bot-platform:
- `pyproject.toml`: `"ayla-ai-core[booking] @ git+...@<v0.9.0-SHA>"`
- Phase 3 nutrition: own `ayla-ai-core-nutrition` package (greenfield)

**Necessity**: P2 — coordination.

**Impact**: ai-bot-platform install statement explicitly lists domain plugins it uses.

#### Task 4: Drop deprecated aliases from v0.7.x

**Where**: `_safe_int` / `_safe_uuid` → removed (now public as `parse_int` / `parse_uuid`)

**Necessity**: P3 cleanup.

**Impact**: API surface shrinks more.

### Tests

- Plugin registry tests
- Cross-package integration tests (core + booking + faq)
- Migration tests for ai-bot-platform consumers

### Breaking changes

| Change | Migration |
|---|---|
| Booking handlers in separate package | `pip install ayla-ai-core[booking]` or direct git+ |
| `dispatch_tool_call` uses registry | Consumers must import their domain package to register handlers |
| `_safe_int` / `_safe_uuid` removed | Use `parse_int` / `parse_uuid` |

**Consumer impact**:
- ai-bot-platform: Phase 2.3 booking port uses `ayla-ai-core-booking`. Phase 3 nutrition uses `ayla-ai-core-nutrition` (own package).
- Future Ayla marketplace: cherry-picks domain packages

**Necessity**: P1 long-term. Without this, every new skill domain forks ayla.

**Impact**: True plugin ecosystem. Library scales to N domains without core changes.

**Effort**: ~4 weeks.

**Gate**: ai-bot-platform Phase 3 nutrition ships its own handler package without modifying core.

---

## v1.0.0 — Stabilization

**Target**: ~Q2 2027. After ai-bot-platform reaches Phase 2 native booking in prod.

**Rationale**: After v0.9.0 the architecture is right. v1.0.0 is the **trust-signal release**: API freeze, semver becomes strict (no breaking changes in minor bumps), comprehensive docs.

### Scope

#### Task 1: Public API freeze

**Where**: All public modules

**Solution**:
- Run `darglint`/`pydoctor` on `__all__` of every module
- Pin every public signature
- Anything not in `__all__` becomes officially private — consumers using it ignore at own risk

**Necessity**: P0 for v1.0.

**Impact**: Consumers can adopt with confidence; minor bumps are now safe.

#### Task 2: Comprehensive Sphinx docs

**Where**: New `docs/` directory in ayla-ai-core repo

**Solution**:
- API reference (auto-generated)
- Migration guides (v0.6 → v0.7 → v0.8 → v0.9 → v1.0)
- "How to write a domain plugin" tutorial
- "Anti-hallucination patterns" deep-dive
- Hosted at github-pages / readthedocs

**Necessity**: P0 for external adoption.

**Impact**: New consumers onboard in hours, not days. External contributions become realistic.

#### Task 3: Migration tooling

**Where**: New `src/ayla_ai_core/migrate.py` + CLI

**Solution**:
- `python -m ayla_ai_core.migrate v0.7 v0.8 --path apps/skills/` — auto-rewrites import paths, adds `tenant_id` kwarg, etc
- AST-based, runs from any tag → any newer tag

**Necessity**: P1. Without migration tooling, every breaking release requires manual sed scripts in every consumer.

**Impact**: Future breaking changes (v1.1 → v2.0?) become low-friction.

#### Task 4: Long-term support commitment

**Where**: `RELEASING.md` + GitHub release templates

**Solution**:
- Statement: "v1.x stable. Minor bumps are backwards-compatible. Major bumps ship migration tooling."
- Security backports for 6 months per minor
- Public release schedule

**Necessity**: P0 for external trust.

**Impact**: Library becomes safe to depend on long-term.

### Tests

- Migration tool tests (v0.7 → v0.8, v0.8 → v0.9, v0.9 → v1.0)
- API stability tests (signatures locked, public symbols frozen)
- Docs build CI

### Breaking changes

None — that's the point. v1.0 is the freeze.

**Consumer impact**: zero break, lots of new docs.

**Necessity**: P0 strategic.

**Impact**: Library matures from "internal tool" to "production-grade open-source-quality".

**Effort**: ~3 weeks.

**Gate**: Docs live, migration tool ships, semver-checker CI green.

---

# Per-version consumer phase mapping

| ayla version | ai-bot-platform phase | What unlocks for consumer |
|---|---|---|
| v0.7.0 | Sprint 7 day 0 (current) | Adapter pin (Track A2) |
| v0.7.1 | Sprint 7 → 8 transition | Hot-fix any production bugs |
| v0.7.2 | Sprint 8 | O(1) perf at scale; parallel tool_calls |
| v0.7.3 | Sprint 8 (observability) | Tenant-aware logs + telemetry-as-data |
| v0.8.0 | Phase 1 (booking port) ~Sep 2026 | Generic CandidateContext for booking specialists; pluggable composer |
| v0.9.0 | Phase 2 (native YClients booking) ~Dec 2026 + Phase 3 (nutrition) prep ~Q1 2027 | Plugin architecture; own nutrition handler package |
| v1.0.0 | Phase 2+ in prod | External adoption possible (other tenants, OSS) |

# Risk register

| Risk | Mitigation |
|---|---|
| v0.7.1 bugs surface but no maintainer time | Reserve 20% of Sprint 8 capacity for ayla maintenance |
| v0.8.0 breaking changes overwhelm ai-bot-platform | Migration tooling deferred to v1.0 — but ship a `MIGRATION_v0.7_to_v0.8.md` guide |
| Plugin architecture (v0.9.0) over-engineers | Ship plugin registry as a thin wrapper first; complex routing only if needed |
| Performance perf claims unverified | v0.7.2 ships with benchmark harness; CI fails if perf regresses |
| GH_DEPLOY_TOKEN rotation breaks CI on token expiry | Add token-expiry alert (14 days warning) — separate ticket |

# Cross-references

- v0.7.0 CHANGELOG: `~/PycharmProjects/ayla-ai-core/CHANGELOG.md`
- Six-agent review summary: chat history 2026-05-12 / 13
- ai-bot-platform Sprint 7 plan: `docs/plans/sprint-7-kb-catalog.md`
- Architecture rule: `docs/architecture.md:158`

---

**Maintainer note**: this is a strategic plan. Each version's actual scope solidifies as the prior version ships and consumer feedback arrives. Treat dates as targets, not commitments. Linear is the operational tracker — sync this doc to Linear milestones after each version finalizes.
