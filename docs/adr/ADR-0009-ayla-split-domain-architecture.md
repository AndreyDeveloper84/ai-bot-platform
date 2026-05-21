# ADR-0009: Ayla split-domain architecture — bot-platform = AI backbone, Ayla djangoproject = booking/payments/catalog REST

**Status:** Accepted — 2026-05-20

## Context

ADR-0002 (2026-05-07) established a three-repo split (`mysite/`, `ayla-ai-core/`, `ai-bot-platform/`) for the Formula tela salon and the early multi-tenant bot platform. Since then:

1. **Ayla-first strategic pivot (2026-05-19, memory `project_ayla_first_strategic_pivot`)** repositioned Ayla as one product for the user (Ayla + Ayla Pro mobile), with salon as provider, not customer. AI belongs to the user, not the salon.
2. A separate `Ayla` repository emerged (PyCharm/Ayla) containing `frontAyla/` (Expo monorepo: `apps/client`, `apps/pro`, `packages/shared`) and `djangoproject/` (Django 5.2 + DRF backend with 12 apps including a DDD booking engine, YooKassa, multi-tenant `tenants` app, and Expo-facing REST API at `api.ayla.app`).
3. A May 20 audit of all three repos surfaced significant duplication: booking domain (Ayla djangoproject `appointments/` + bot-platform `apps/booking/`), payments (Ayla `payments/` + bot-platform `apps/orders/` + `apps/integrations/yookassa/`), and catalog (Ayla `services/` + bot-platform `apps/catalog/`). Both Ayla djangoproject and bot-platform separately mirror YClients.
4. The Notion architecture v2.0 (31.03) describes Ayla as a single Django backend serving two mobile apps via a single REST API with `X-App-Type` header. Reality: bot-platform has matured into a far more capable backend (26 apps, multi-tenant, ChromaDB RAG, two-bus events, MAX Mini App, observability), while Ayla djangoproject holds the booking engine and YooKassa integration.

We need to decide where each domain canonically lives, otherwise:
- Dual writes diverge → user sees inconsistent state across mobile and bot channels.
- YClients webhook duplication causes race conditions.
- The longer we wait, the more code gets layered on top of duplicated foundations.

ADR-0002 named only three repos. The Ayla repo is the implicit fourth and was never given a role; this ADR fills that gap and supersedes ADR-0002's scope for the Ayla product. `mysite/` continues to serve Formula tela (now just one tenant of bot-platform's multi-tenant world).

## Decision

**Split-domain architecture. Each domain has one canonical home. Cross-repo state flows only through a published event contract.**

### Repo roles

| Repo | Role |
|---|---|
| **`ayla-ai-core`** (existing, v0.8.1 → v1.0 freeze) | Pure Python AI library. Unchanged. `AIConcierge`, `BrandVoiceConfig`, provider adapters, tool schemas, anti-hallucination. Pinned via `git+ssh@vX.Y.Z` in both consumers. |
| **`ai-bot-platform`** (existing) | AI backbone. Owns **channel identity** (BotUser per-channel), **AI memory profile** (UserPersonalContext, see §Memory model), conversations, skills, tools, KB/RAG, channels (MAX/Telegram/future WhatsApp), MAX Mini App, tenancy runtime, audit, events, eventbus, replay, observability, persona, prompt registry, experiments, handoff, consent. **Does NOT own canonical User identity or PII** — calls Ayla for those. |
| **`Ayla`** (existing — djangoproject + frontAyla) | Transaction backend + mobile front. Owns: **canonical User identity + PII** (auth, OTP, JWT issuance, profile, avatar), booking domain (Appointment DDD), payments (YooKassa lifecycle), canonical catalog (services + masters + prices + durations + schedules), provider-specific history (visits, reviews per tenant), Expo mobile apps for client and master. |
| `mysite/` (existing — out of Ayla scope) | Continues to serve Formula tela salon. Treated as one tenant in bot-platform's multi-tenant world. No Ayla-product code added here. |

### Domain ownership matrix

| Domain | Canonical home | Notes |
|---|---|---|
| AI chat, intent, NLU, conversations, messages | ai-bot-platform | `apps/conversations`, `apps/orchestrator`, `apps/llm` |
| Skills (FAQ, booking, food, water, health, privacy, cancel/reschedule) | ai-bot-platform | `apps/skills` |
| Tools (slot lookup, booking create, KB retrieval, reminder send) | ai-bot-platform | `apps/tools` |
| KB / RAG / ChromaDB per-tenant + global_kb | ai-bot-platform | `apps/kb` |
| Provider knowledge (FAQ, contraindications, aftercare scripts) | ai-bot-platform | `apps/kb` |
| MAX, Telegram, future WhatsApp channels | ai-bot-platform | `apps/channels` |
| MAX Mini App backend + frontend | ai-bot-platform | `apps/miniapp_api`, `apps/miniapp` |
| Tenancy, multi-tenant context, STRICT_TENANT_SCOPE | ai-bot-platform | `apps/tenancy` |
| Audit, events bus, eventbus (Postgres outbox), replay | ai-bot-platform | `apps/audit`, `apps/events`, `apps/eventbus`, `apps/replay` |
| Observability, shadow-mode, OpenTelemetry | ai-bot-platform | `apps/observability` |
| **Core user memory (`UserPersonalContext`)** | **ai-bot-platform** | `apps/identity` — cross-channel, **user-owned (not cross-tenant)**, reusable across providers only within consent boundary. See §Memory model for the reuse rule. |
| **Canonical User identity + PII** (auth: OTP + JWT + Anonymous JWT + social, profile, avatar, name, basic settings) | **Ayla djangoproject** | `users/` — source of user truth. Bot-platform NEVER stores canonical PII. |
| **Channel identity** (per-channel BotUser linked to canonical User via FK, RFM/LTV projection) | **ai-bot-platform** | `apps/identity/BotUser`, `ClientProfile` — channel-scoped wrapper around canonical User |
| **Booking domain (Appointment DDD, state machine, snapshot, idempotency)** | **Ayla djangoproject** | `appointments/` — keeps existing engine |
| Master schedule (WorkingHours, ScheduleException, slots) | Ayla djangoproject | canonical; bot-platform `apps/scheduling` becomes read-only cache for slot resolution |
| **Payments (YooKassa hold→capture, refund, webhook, commission)** | **Ayla djangoproject** | `payments/` (after refactor — see Phase 0). bot-platform `apps/orders` reduced to display-only |
| **Catalog: services, masters, prices, durations, categories** | **Ayla djangoproject** | canonical |
| Catalog read-only mirror for AI/RAG | ai-bot-platform | `apps/catalog` — kept as 15-min sync mirror for bot performance |
| Reviews | Ayla djangoproject | `reviews/` |
| Provider-specific history (visits to salon X, master Y) | Ayla djangoproject | per-tenant, never leaves tenant boundary |
| Booking reminders + escalation state machine | ai-bot-platform | `apps/booking` reduced to `RemoteBookingProxy` + reminder/escalation FSM; canonical record lives in Ayla |
| Food domain (canonical food log, daily summary, water tracker) | Ayla djangoproject | `nutrition/` (existing scaffold) — mobile-primary |
| Food skill (chat-side food logging via `Сок 0,5л`, photo recognition) | ai-bot-platform | `apps/skills/food_logging` — calls Ayla nutrition API |
| AI-avatar | (deferred Phase 2, per Linear DRF-235) | — |
| Voice STT+TTS | (deferred Phase 2+) | `apps/voice` scaffold only |

### Mobile API split (gateway-routed)

Expo apps talk to two backends via a single API gateway (`api.ayla.app` → Nginx routing):

- **Direct user actions** → Ayla djangoproject:
  - Auth (OTP, social, anonymous → merge, refresh, logout)
  - Profile (GET/PATCH `/users/me`, avatar, settings)
  - Catalog (specialists, services, categories, search, slots)
  - Booking (create/list/get/cancel/reschedule)
  - Payments (create/webhook/status)
  - Reviews (create/reply)
  - Schedule (master self-service)

- **AI actions** → ai-bot-platform:
  - Chat (`POST /api/v1/customer/chat/`)
  - Recommendations
  - Memory (`GET/PATCH/DELETE /api/v1/customer/memory/*`)
  - Food scanner photo intent (calls Ayla nutrition after recognition)
  - Water tracker chat reactions
  - Conversations history

- **AI-initiated booking (canonical flow):**
  ```
  Mobile → bot-platform: "запиши на завтра вечером на массаж"
  bot-platform: parse intent + lookup slots via Ayla REST
  bot-platform → Mobile: 3 candidate slots
  Mobile → bot-platform: confirm slot N
  bot-platform → Ayla: POST /api/v1/appointments (create)
  Ayla: persists Appointment, charges YooKassa
  Ayla: publishes booking.created on eventbus
  bot-platform: consumes booking.created → updates core memory, adds reminder
  bot-platform → Mobile: "Записала. До встречи 🤍"
  ```

### Mandatory event contract (Variant A only works with this)

Ayla djangoproject MUST publish domain events to bot-platform on every state change. Without this, AI gives wrong answers about user state.

**Event envelope (required for every event):**
```json
{
  "event_id": "01HXXXXX",        // ULID, unique
  "event_name": "booking.created", // dot.notation
  "event_version": 1,             // integer; bump on breaking change
  "occurred_at": "ISO8601",
  "tenant_id": "uuid-or-null",
  "user_id": "uuid",              // canonical Ayla User
  "actor": "system|user|admin",
  "correlation_id": "uuid",       // for tracing related events
  "causation_id": "uuid|null",    // what caused this event
  "data": { ... }                 // domain payload (versioned)
}
```

**Versioning rule:** breaking changes to `data` schema require a new `event_version`. Consumers register handlers per `(event_name, event_version)` pair. Old versions stay supported for at least one deprecation cycle (~30 days).

**Idempotency rule:** consumers MUST be idempotent. Dedupe by `event_id`. Same event delivered N times → same observable side-effect.

Minimum events for MVP (all at `event_version: 1`):
- `booking.created`
- `booking.cancelled`
- `booking.rescheduled`
- `booking.completed`
- `payment.authorized`
- `payment.captured`
- `payment.failed`
- `payment.refunded`
- `review.created`
- `service.updated`
- `master.schedule.updated`
- `user.profile.updated`

bot-platform consumers update memory, conversation context, reminders, audit, analytics. Event delivery uses bot-platform's existing two-bus pattern (`apps/eventbus/`, dot.notation, ULID, Postgres outbox on Ayla side → HTTP push or pull to bot-platform).

**Event taxonomy doc** lives at `docs/architecture/event-contract.md` (created as separate Phase 0 issue before implementation tickets land).

### Booking SoR rule

- **YClients-using salons:** YClients is the SoR. Ayla djangoproject mirrors via existing webhook. bot-platform reads via Ayla REST. Both Ayla and bot-platform never write directly to YClients without going through Ayla's sync layer.
- **Solo masters / salons without YClients:** Ayla djangoproject local DB is the SoR. bot-platform reads via Ayla REST.
- **bot-platform never owns booking SoR.** Its `BookingRequest` becomes `RemoteBookingProxy` — a cache for reminder + escalation FSM only.

### Memory model — hybrid (with explicit reuse boundary)

- **Core user memory** (cross-channel, **user-owned**): bot-platform `apps/identity/UserPersonalContext`. Schema per Notion AI-Personalization doc. Layers: locations, schedule, finance, preferences, life events, sensitive (red zone), inferred patterns.
- **Provider-specific history** (per-tenant, provider-owned): Ayla djangoproject. Visits to salon X, reviews left at provider, payments + refunds, subscriptions, salon-specific preferences (e.g. "preferred master at salon X").
- **Reuse boundary (HARD RULE — corrects "cross-tenant memory" wording):**
  - Core memory is **user-owned, not cross-tenant**. It can be **reused across providers** only when: (a) the fact is not provider-confidential and (b) user consent permits. Default: green-zone facts (district, preferred time, diet, allergies) are reusable; yellow-zone facts (children, partner sensitivities) require implicit consent from being stored in core; red-zone facts (pregnancy, conditions) are USE-only (filter contraindications), never reasons exposed to provider.
  - Provider-specific facts (booking history at salon X, reviews left at provider Y, sale price paid to tenant Z) **NEVER leak across tenant boundary**. They live in the tenant's Ayla djangoproject scope.
  - When AI in MAX chat answers "когда у меня запись?" — it queries Ayla djangoproject for the user's bookings across all tenants the user has relationships with (via JWT-claimed active relationships, see §Mobile API split + JWT). It does NOT carry over provider-confidential context to a chat in a different provider's scope.
- **No standalone `ayla-memory` service for MVP.** Acceptable later (Phase 2+) when multi-region + multi-channel + retention data justify operational overhead.
- Examples (reusable vs not):
  - ✅ Reusable across providers: "user prefers evening time", "user is vegetarian", "user follows water tracker", "user dislikes strong pressure massage".
  - ❌ Never crosses providers without explicit consent: "user had procedure X at salon Y", "user complained about master Z", "user paid 5000₽ at salon W".
- See memory: `project_ayla_memory_hybrid_model.md`.

## Consequences

### Easier
- **No code migration of mature apps.** bot-platform's 20+ canonical apps stay. Ayla's booking DDD stays. Phase A.7 multi-tenant scoping (DRF-242.x) stays.
- **Pilot deadline (Sprint B, 2026-07-15) doesn't slide.** No 4–6 week rewrite gauntlet.
- **Each repo specializes in its strength.** bot-platform = AI/observability/multi-tenant runtime; Ayla = transactional booking + mobile.
- **YooKassa unified.** One repo holds the money lifecycle.
- **Catalog has one source of truth** (Ayla djangoproject), accessed via REST and mirrored read-only in bot-platform.

### Acceptable
- **Mobile talks to two backends.** Mitigated by API Gateway with path-based routing; mobile sees one `api.ayla.app` host.
- **Two Django services to operate.** Each repo already has its own CI, deploy, Sentry project.
- **Event contract becomes critical infrastructure.** If events fail, AI memory becomes stale. Mitigated by Postgres outbox pattern (already proven in bot-platform), retry budget, idempotent consumers, observability alerts on lag.

### Harder
- **Cross-service consistency requires discipline.** Every new feature must decide: which repo owns this? Mistakes are expensive to undo.
- **JWT contract must be unified.** Both backends validate the same JWT signed by Ayla auth (or shared issuer). `tenant_id` claim required.
- **Catalog mirror in bot-platform must be invalidated on `service.updated` events.** Add cache-bust logic in `apps/catalog` consumer.
- **Bot-platform's `apps/orders` and `apps/integrations/yookassa` must be removed or reduced.** Tightly woven into skills today — careful refactor (see Phase 0 Sprint Plan).
- **Bot-platform's `apps/booking` must shrink** from full BookingRequest model to `RemoteBookingProxy + reminder FSM`. Migration data path needed.

### Hard rules (non-negotiable)
1. **No duplicate canonical state.** If the matrix says Ayla owns it, bot-platform may cache or mirror, never own. Reverse also true.
2. **No direct cross-repo DB access.** Both backends only talk REST + events. No shared DB tables, no `psycopg2` from one repo to the other repo's DB.
3. **No new MVP features merge until Phase 0 close criteria are met** (memory `freeze-mvp-until-boundaries-locked`). Allowed during freeze: bug fixes, infra migration, rebrand, event contract code, ADR/sprint docs, Sprint 1 EPICs (Track A).
4. **bot-platform does not grow new transactional domains.** It's the AI/observability runtime, not a generic Django shop.
5. **Transactional tools in bot-platform are REST wrappers, not direct DB writes.** Any tool/skill in `apps/skills/` or `apps/tools/` that touches booking, payment, appointment, service, master, schedule MUST call Ayla djangoproject REST API. NO direct DB writes to booking/payment/catalog tables from bot-platform. Tools that need slot data CALL `/api/v1/specialists/{id}/slots`; tools that create bookings CALL `POST /api/v1/appointments`; etc. The reverse-engineered "let's just write to the same DB" anti-pattern is forbidden.
6. **JWT `tenant_id` claim is `active_tenant_id`, not permanent ownership.** A user can have relationships with N tenants. The JWT carries the currently-active tenant context for the request. Backends MUST verify `TenantUserRelationship(user_id, tenant_id)` exists and is active on every tenant-scoped request. For global AI memory requests (e.g. memory layer queries in bot-platform), `tenant_id` MAY be null — means "global user scope, not tenant-scoped." See §Mobile API split.
7. **Every event MUST include `event_version`.** Consumers MUST be idempotent (dedupe by `event_id`). See §Mandatory event contract.

## Alternatives considered

### Variant B — everything in bot-platform, Ayla djangoproject deprecated
Rejected. Migrating the booking DDD, YooKassa flow, REST contract (60+ endpoints in API Spec v2.0), user/auth, services/categories, reviews from Ayla djangoproject into bot-platform = 4–6 weeks of regression risk and Sprint B (pilot 2026-07-15) slides. Phase A.7 multi-tenant scoping (already done in Ayla) gets re-done. Mobile API contract changes. Net: ~3000 LOC migration with no product win.

### Variant C — everything in Ayla djangoproject per Notion, bot-platform shrunk to thin channel
Rejected. The opposite migration: 15+ mature apps from bot-platform (conversations, orchestrator, skills, tools, kb, audit, events, eventbus, replay, observability, tenancy, persona, promptreg, miniapp_api/miniapp, channels). ~10–20K LOC. Sprint 9 STRICT_TENANT_SCOPE flip stops. Sprint 10 canary ramp stops. ChromaDB per-tenant + global_kb infra rebuilt in Ayla. 6–12 weeks total. Notion-purity is not worth this.

### Standalone `ayla-memory` service
Deferred. Cleaner separation but operationally premature. Re-evaluate Phase 2 when multi-region + multi-channel + retention SLAs make hybrid expensive.

## References
- Memory: [`ayla-split-domain-architecture`](../../C:/Users/user/.claude/projects/.../memory/project_ayla_split_domain_architecture.md)
- Memory: [`ayla-memory-hybrid-model`](../../C:/Users/user/.claude/projects/.../memory/project_ayla_memory_hybrid_model.md)
- Memory: [`freeze-mvp-until-boundaries-locked`](../../C:/Users/user/.claude/projects/.../memory/feedback_freeze_mvp_until_boundaries_locked.md)
- Doc: `docs/plans/2026-05-20-ayla-consolidated-architecture.md` (full analysis with all three variants)
- Doc: `docs/plans/2026-05-20-phase-0-sprint-plan.md` (concrete execution plan)
- Supersedes: scope of ADR-0002 for Ayla product (mysite remains for Formula tela)
- Linear: DRF-230 (UserPersonalContext, In Progress), DRF-242.x (multi-tenant scoping, Done), DRF-300 (Sprint B pilot checklist, due 2026-07-15)
