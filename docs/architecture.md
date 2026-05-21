# ai-bot-platform — Architecture

> Condensed reference. Each section ≤ 500 words and links to the full source — [`mysite/docs/arch/PHASE0_DESIGN.md` v2](../../mysite/docs/arch/PHASE0_DESIGN.md) — for deep context. Read this on a coffee break, dive into the source for design decisions.

---

## 1. Goal & non-goals

**Goal.** Build a multi-tenant AI bot platform that consolidates the AI logic currently spread across `mysite/maxbot/` (production MAX bot for Формула тела) and the Ayla nutrition tracker, behind a single tenanted API. First tenant: `formula-tela`. First channel: MAX Messenger. Phase 0 ships an architecture-ready single-tenant runtime in 22 weeks (10 sprints).

**Non-goals (explicit).**

- Multi-tenancy *implementation* in Phase 0 — only multi-tenancy *readiness*. Tenant code paths exist (`TenantContext`, `STRICT_TENANT_SCOPE`, scoped managers), but only one tenant runs in prod until Phase 1.
- New end-user features. Phase 0 carries over `mysite/maxbot/` behaviour 1:1; users see no change. Real product evolution starts Phase 1.
- Vendor lock-in. The platform abstracts LLM providers (`apps/orchestrator/llm`), embedding providers (`apps/kb`), payments (`apps/channels`), so a tenant can switch backends without touching domain code.

**Success criteria for Phase 0 cutover (Sprint 10).** 100% MAX traffic flows through `ai-bot-platform`; `mysite/maxbot/` is archived; no regression in BookingRequest, conversation outcome rate, reminder delivery, or food-scan latency. Replay fixtures lock the contract so future refactors can be validated against historical traffic.

Reference: [PHASE0_DESIGN.md §0](../../mysite/docs/arch/PHASE0_DESIGN.md#0-executive-summary).

---

## 2. Repo layout

Three-repo split — originally ADR-0002, refined by **[ADR-0009](adr/ADR-0009-ayla-split-domain-architecture.md)** (Ayla split-domain architecture, Variant A — locked 2026-05-20). Under ADR-0009 the three repos take on sharper roles:

| Repo | Role under ADR-0009 |
|---|---|
| `mysite/` (formula_tela) | Static site, SEO landing pages, AI marketing agents, YooKassa, payments, FROZEN `maxbot/` carry-over source. Stays alive forever. |
| `Ayla djangoproject` | **Canonical SoR** for booking lifecycle (Appointment DDD + state machine), master schedule, services catalog, reviews, user profile, **payments (YooKassa hold→capture→refund)**. Publishes domain events to `ai-bot-platform` per [`docs/architecture/event-contract.md`](architecture/event-contract.md). |
| `ai-bot-platform/` | This repo. AI / observability / multi-tenant runtime. **Consumes** Ayla domain events (memory, reminders, RFM, catalog cache). Booking + payment are read-only here; mutations route through Ayla REST. |
| `ayla-ai-core/` | Shared AI library — LLM clients, voice config, anti-hallucination helpers, replay primitives. **v0.8.1 → v1.0 freeze** per ADR-0009; pinned via `git+ssh@vX.Y.Z` in both consumers. Unchanged. |

**Architectural constraints from ADR-0009 (non-negotiable, see §Hard rules in the ADR):**

1. No duplicate canonical state — if Ayla owns it, bot-platform may cache or mirror, never own. Reverse also true.
2. No direct cross-repo DB access — both backends talk REST + events only. No shared tables, no cross-repo `psycopg2`.
3. Phase 0 freeze on new MVP features — only refactor and decoupling work merges.
4. bot-platform does NOT grow new transactional domains. Any new transactional state goes to Ayla.
5. Transactional tools in bot-platform skills are REST wrappers — bot-platform never DB-writes booking / payment / catalog.
6. JWT `tenant_id` claim = `active_tenant_id`; verify via `TenantUserRelationship`.
7. Every cross-service event has `event_version`; consumers idempotent. See [`docs/architecture/event-contract.md`](architecture/event-contract.md).

Inside `ai-bot-platform/`:

```
config/                     Django project package (settings, urls, wsgi/asgi, celery)
apps/                       20 Django apps — empty in Sprint 0, filled sprint-by-sprint
  tenancy           identity        conversations    orchestrator
  skills            tools           kb               channels
  ingress           workers         consent          audit
  events            experiments     voice            catalog
  replay            promptreg       adminconsole     handoff
legacy_maxbot/              AS-IS snapshot of mysite/maxbot/ at freeze a52e4e6 (75 files)
legacy_formulatela_mcp/     AS-IS snapshot of mysite MCP server (12 files)
legacy_notifications/       AS-IS snapshot of mysite/notifications/ (2 files)
docs/architecture.md        ← you are here
docs/adr/                   Architecture Decision Records 0001–0006 (DRF-413)
docs/runbooks/              Operational runbooks (DRF-414 skeletons)
docs/source-materials/      Pointers to the canonical source docs in mysite/docs/arch/
tests/smoke/                "Django boots" smoke gate
```

Twenty apps were carved up so each has a single responsibility and ships in a known sprint. The map of which app fills in which sprint is in [README.md](../README.md).

`legacy_*/` is import-only (`ruff TID251` blocks accidental imports from `apps/**`). Drained sprint-by-sprint and deleted in Sprint 10's cleanup PR.

Reference: [PHASE0_DESIGN.md §1](../../mysite/docs/arch/PHASE0_DESIGN.md#1-repo-structure).

---

## 3. Sprint plan (10 sprints, 22 weeks)

| Sprint | Weeks | Theme | Lands |
|---|---|---|---|
| **0** | 1–2 | Bootstrap | Scaffold, docker, lockfile, CI, CODEOWNERS, pre-commit, freeze, legacy carry-over, ADRs, runbook skeletons |
| 1 | 3–4 | Orchestrator skeleton | Conversations + Orchestrator + Events + LLM circuit breaker (CR-3) |
| 2 | 5–6 | Tenancy + identity | TenantContext, scope managers, audit log, consent registry, encryption (ADR-0006) |
| 3 | 7–8 | Skills + tools | Skill base class, 5 baseline skills (FAQ, booking, masters, services, contacts), tool layer |
| 4 | 9–10 | Channels + ingress | MAX webhook, Telegram skeleton, ingress queue, workers, handoff to humans |
| 5 | 11–12 | Replay infrastructure | Recorder, redactor, golden + adversarial + voice fixtures, replay workflow |
| 6 | 13–14 | Promptreg + experiments + voice | Live prompt reload, sticky bucketing, brand voice validator |
| 7 | 15–16 | KB + catalog sync (F0.17) | chromadb migration from `legacy_formulatela_mcp/`, 15-min mirror from `mysite/services_app/` |
| 8 | 17–18 | Shadow mode | Platform receives MAX traffic in shadow. No outbound. Replay diff vs production. |
| 9 | 19–20 | Canary | 10% → 50% MAX traffic on platform. Outbound enabled. Branch protection live. |
| **10** | 21–22 | Cutover | 100%. `mysite/maxbot/` archived. Cleanup PR. |

Reference: [PHASE0_DESIGN.md §2](../../mysite/docs/arch/PHASE0_DESIGN.md#2-migration-path-mysitemaxbot--ai-bot-platform).

---

## 4. Request lifecycle pipeline

A user message takes ~6 hops from MAX webhook to outbound reply. Sync ingress / async execution split: ingress acks within 300ms; orchestration + LLM + tool calls run on workers via Redis Streams.

```
   MAX webhook (FastAPI in apps/channels)
       │ verify X-Max-Bot-Api-Secret, hydrate tenant_id from URL/header
       ▼
   apps.ingress.queue.enqueue(event)      ← Redis Streams, idempotent on event_id
       │ ack 200 in <300ms
       ▼
   apps.workers.consumer    (Celery worker pool)
       │ pop from stream, hydrate TenantContext, hydrate Conversation
       ▼
   apps.orchestrator.run(conversation, message)
       │ resolve skill via apps.skills (intent classifier or explicit callback)
       │ resolve prompt via apps.promptreg (live-reload via Redis pub/sub)
       │ optional voice rewrite via apps.voice.rewriter
       │ call apps.orchestrator.llm.route(provider) — with circuit breaker (CR-3)
       │ optional tool calls via apps.tools (YClients, Catalog, KB, Reminders…)
       │ persist Message + ReplayTrace via apps.replay.recorder + apps.audit
       ▼
   apps.channels.send(reply)              ← outbound to MAX/TG/Web
       │ apps.events.publish() for analytics + outbox
```

Three contracts hold the pipeline together:

- **Tenant**: every step runs inside a `TenantContext` set by ingress; `STRICT_TENANT_SCOPE` aborts on any cross-tenant query.
- **Idempotency**: `event_id` from MAX is the dedupe key in Redis Streams; ingress and worker are both idempotent.
- **Observability**: every step emits a `trace_id` + structured event so `apps.replay` can reconstruct the run later.

Reference: [PHASE0_DESIGN.md §5](../../mysite/docs/arch/PHASE0_DESIGN.md#5-orchestrator-design--full-pipeline).

---

## 5. Multi-tenant patterns

Tenant-readiness without tenant-implementation. Three primitives.

**TenantContext (ADR-0003).** A `ContextVar[Tenant]` set by `apps.tenancy.middleware` on every request and by `apps.workers.consumer` on every Celery task. Async-safe: `ContextVar` propagates correctly across `await` and `sync_to_async` boundaries — no thread-locals.

**Scoped managers.** Every domain model that holds tenant data exposes both `objects` (tenanted, default) and `all_tenants` (escape hatch, audited). The default manager filters `tenant=current_tenant()`. Any code that needs cross-tenant access uses `Model.all_tenants` and is grep-able for review.

**STRICT_TENANT_SCOPE setting (IM-2).** A boolean. In `audit` mode it logs cross-tenant queries to `apps.audit`; in `strict` mode it aborts. Sprint 0–8 run `audit`. Sprint 9 (canary) flips to `strict` after 2 weeks of zero audit hits soak. Without this safeguard a missing `tenant=` filter would silently leak data; with it, the failure is loud.

**Test fixture isolation.** `tests/conftest.py` wraps every test in a fresh tenant; cross-tenant tests must `pytest.mark.cross_tenant` to be collected. Default test runs against tenant `fixture-A`; data created in test_X cannot leak to test_Y.

Phase 0 ships everything *correctly tenanted* but only one tenant exists. Phase 1 onboards tenant #2 by setting `tenant_id=2` and pointing them at a different MAX webhook URL — no schema migrations, no code branches.

Reference: [PHASE0_DESIGN.md §6](../../mysite/docs/arch/PHASE0_DESIGN.md#6-multi-tenant-patterns).

---

## 6. Dependency graph

Strict layering enforced by `ruff TID251` and (eventually) `pytest-archon`. An app may only import from layers below it.

```
       L4   apps/adminconsole, apps/handoff
              │
       L3   apps/orchestrator, apps/skills, apps/tools, apps/voice
              │
       L2   apps/conversations, apps/promptreg, apps/experiments,
            apps/replay, apps/kb, apps/catalog, apps/channels, apps/ingress, apps/workers
              │
       L1   apps/identity, apps/consent, apps/audit, apps/events
              │
       L0   apps/tenancy        ← every app reads TenantContext from here
```

Rules:

1. **No upward imports.** L1 cannot import L2.
2. **No sideways imports across L2/L3.** Skills don't import KB directly — they go through Tools.
3. **No `from legacy_* import` in apps/**.** Already enforced (DRF-410).
4. **External libs are pinned in `pyproject.toml` and resolved by `uv.lock`.** No floating deps.
5. **`ayla-ai-core` is the only allowed AI library carry-over.** Direct calls to `openai`, `anthropic`, `google-genai` go through `apps.orchestrator.llm` for circuit-breaker + replay coverage.

A violation is a CI failure, not a code review nudge. The graph is the contract.

Reference: [PHASE0_DESIGN.md §8](../../mysite/docs/arch/PHASE0_DESIGN.md#8-f0-dependencies-graph).

---

## 7. Top 5 risks

1. **Cross-tenant data leak** before strict mode flips. *Mitigation:* `STRICT_TENANT_SCOPE=audit` from Sprint 2; flip to `strict` after 14 days of zero audit hits in Sprint 9.
2. **LLM provider outage cascading into bot downtime.** *Mitigation:* circuit breaker (CR-3) — opens after 5 failures in 60s, fallback to template response, half-open after 30s. Multi-provider routing lands Sprint 6.
3. **Replay fixtures drift** from production behaviour, hiding regressions. *Mitigation:* 100% sampling in Sprint 5 (IM-3) plus weekly replay-vs-prod diff alert. Sample down only after baseline parity is proven.
4. **Catalog sync (F0.17) lag** between `mysite/services_app/` and `apps/catalog/`. *Mitigation:* 15-min cron + version stamp on every record + Telegram alert if sync falls >30 min behind.
5. **Migration drift** in `legacy_maxbot/` causing the cutover to be wrong. *Mitigation:* freeze policy (DRF-411) + cherry-pick discipline + replay fixtures captured before the freeze; Sprint 8 shadow mode runs both stacks against the same traffic.

Full register (14 risks, 6 ADRs): [PHASE0_DESIGN.md §9](../../mysite/docs/arch/PHASE0_DESIGN.md#9-risk-register), [§10](../../mysite/docs/arch/PHASE0_DESIGN.md#10-adr-library).
