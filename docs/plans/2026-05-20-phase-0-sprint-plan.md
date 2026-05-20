# Phase 0 Sprint Plan — Ayla Architecture Stabilization

> **Status:** Active — 2026-05-20
> **Owner:** Andrey Tikhonov + parallel Claude/Codex agents
> **Duration:** 3–4 недели (target end ~2026-06-10)
> **Foundation milestone:** GH `Sprint 1 — Foundation backbone` (milestone #1) — already 51 open + 17 closed pre-deploy locks
> **Two tracks run in parallel:** A) Sprint 1 product foundation (5 EPICs Ayla-first pivot), B) Phase 0 infra/rebrand/contracts (new issues, ~28 to create)
> **References:** ADR-0009 (architecture decision), `2026-05-20-ayla-consolidated-architecture.md` (analysis), memory `freeze-mvp-until-boundaries-locked`
> **GH issues created:** #412 — #439 (28 issues in `Sprint 1 — Foundation backbone` milestone, 2026-05-20). Tech-lead review 2026-05-20 added 7 more issues (#441 — #446 — event-contract doc + 5 split consumers + idempotency contract test). See §GH issue mapping at end.

## Tech-lead review corrections applied (2026-05-20)

Following user's review of ADR-0009 + this plan, 10 corrections applied:

1. **User ownership clarified** in ADR §Repo roles + §Domain ownership matrix: Ayla djangoproject owns canonical User identity + PII; bot-platform owns channel identity (BotUser) + AI memory profile only.
2. **Memory boundary** spelled out in ADR §Memory model with reuse-rule + examples (✅ reusable green-zone facts vs ❌ never-reusable provider-specific facts).
3. **Transactional tools = REST wrappers** added as ADR Hard rule #5: bot-platform skills/tools that touch booking/payment/catalog MUST call Ayla REST API, no direct DB writes.
4. **`tenant_id` claim = `active_tenant_id`** added as ADR Hard rule #6: a user can have N tenant relationships; JWT carries currently-active context; backends MUST verify `TenantUserRelationship` on every tenant-scoped request.
5. **Event versioning + idempotency** added in ADR §Mandatory event contract: explicit envelope schema with `event_version: int`, dedupe-by-`event_id` rule. New Phase 0 issue for `docs/architecture/event-contract.md` BEFORE implementation tickets land.
6. **Event contract doc as prerequisite** — new issue #441 (event-contract.md taxonomy + versioning rules) blocks issues #429–#433 (outbox/dispatcher/consumers).
7. **#433 split into 5 separate handler issues** (per event family): booking, payment, service, master+review, user.profile. Issue #433 becomes umbrella; concrete work tracked in #442 – #446.
8. **#435 JWT semantics** refined: `active_tenant_id` claim + `TenantUserRelationship` verification step; aligns with #246 (User.tenant_id removal in Sprint 1).
9. **#439 booking_source placement** refined: `ProviderProfile.booking_source` only for MVP, NOT duplicated across Master/Salon/Tenant. If multi-location appears, move to `ProviderLocation` in Phase 1.5.
10. **ADR-0011 (#437) gates UserPersonalContext model merge** (#228-230 in Sprint 1 Track A). Privacy fields (`sensitivity_level`, `source`, `last_inferred_at`, `delete_requested_at`, `consent_at`) MUST be in the initial migration — adding later is expensive. New issue #447 captures contract test.

Two additional corrections also applied to ADR rather than plan:
- ADR §Booking SoR rule now explicitly says salons with multiple locations use per-location booking_source (Phase 1.5 escape hatch).
- ADR §Hard rules expanded from 4 to 7 (additions: transactional tools as REST wrappers, active_tenant_id semantics, event versioning + idempotency).

## Goal

Close service boundaries (ADR-0009 §Domain ownership matrix) and tech foundations before Phase 1 MVP work begins. Sprint 1 foundation + Phase 0 infra **must both finish** before pilot in Penza (Sprint B, 2026-07-15) can start onboarding real users.

**Management rule (per user):** «Если задача не помогает закрыть Phase 0, она не делается сейчас». Sprint 2/3/4 are frozen.

## Two parallel tracks

### Track A — Sprint 1 product foundation (already in GH, 5 EPICs)

These belong to ai-bot-platform per ADR-0009 §Hybrid memory and §Mobile API split. **Continue at current pace.**

| EPIC | GH # | Scope |
|---|---|---|
| Ayla brand + identity + persona scaffolding | #219 | TenantPersonaOverride (#224), persona-override API (#225), brand asset folder (#226), `assistant_persona` event (#227), LLM voice modulator (#280), forbidden-phrase filter (#281), «вы бот?» templates (#282), classifier message→emergency_tier (#283), tier-specific framing (#284), anonymous memory write guard (#285) |
| Cross-tenant memory + 3-zone sensitivity | #220 | UserPersonalContext model (#228), MemoryEntry (#229), RedZoneAccessLog (#230), GET memory (#231), DELETE entry (#232), POST forget-all (#233), nightly TTL sweep (#234), yellow-zone counter rollup (#235), UI memory section (#236), `memory` event domain (#237) |
| 4-tier emergency fallback | #221 | EmergencyEscalation (#238), EmergencyEventLog (#239), POST /emergencies (#240), GET queue (#241), claim (#242), resolve (#243), SLA breach watcher (#244), `emergency` event domain (#245) |
| Tenant-as-provider + Ayla Pro split | #222 | ProviderProfile (#247), RBAC TenantOwner vs Master (#248), migration: remove tenant_id from User + add TenantUserRelationship (#246), provider directory API (#249, #250, #251), customer provider switcher UI (#252), Ayla Pro Mini App shell (#253), `provider` event domain (#254) |
| Anonymous-to-registered + MAX OAuth | #223 | AnonymousSession (#255), SoftPersonalContext (#256), POST /anonymous/session (#257), MAX OAuth callback + merge (#258), cross-device merge (#259), OAuth gate modal UI (#260), anonymous welcome card (#261), `anonymous` event domain (#262) |

**All 17 pre-deploy-locks** (Q-AYL11/12/13/20, Q-AML6/8/17, Q-AEF11/15/16/17/18, Q-TP2/11/14, Q-AN1/9) — already closed. Policy decisions resolved.

### Track B — Phase 0 infra / rebrand / contracts (new GH issues, 28 to create)

These are NOT in Sprint 1 milestone yet. They become new GH issues with labels `P0`, `ayla-foundation`, plus one of `epic:infra` | `epic:rebrand` | `epic:contracts`. Created in milestone `Sprint 1 — Foundation backbone` so they share the foundation deadline.

#### Bucket 1 — ADR-0009 docs (3 issues)
- [docs] Merge ADR-0009 into ai-bot-platform `docs/adr/` + reference from `docs/architecture.md` + `CLAUDE.md`
- [docs] Copy ADR-0009 to Ayla djangoproject `docs/architecture/`
- [docs] Copy ADR-0009 to ayla-ai-core `docs/`

#### Bucket 2 — Technical rebrand (4 issues, #226 brand assets already exists)
*Scope per user: package namespace, Bundle IDs, env URLs, README/CLAUDE.md/backend service names, API doc title. NOT in scope: visual redesign, App Store marketing assets, full brand book.*
- [rebrand][frontAyla] `@beautygo/*` → `@ayla/*` yarn workspaces rename + build smoke
- [rebrand][frontAyla] Bundle IDs `ru.beautygo.client/pro` → `ru.ayla.client/pro` (Expo `app.config.ts` + provisioning profiles + Android signing). Note: Apple provisioning review may take 3-7 days — DOES NOT block other Phase 0 work
- [rebrand][infra] Env URL: `dev.gobeauty.site` → `dev.ayla.app` (DNS, LE certs, reverse proxy, 30-day 301 redirect from old domain)
- [rebrand][docs] README + CLAUDE.md + backend service names + API doc titles BeautyGo → Ayla across all three repos

#### Bucket 3 — Secrets hygiene (2 issues)
- [infra][secrets] Remove `.mcp.json` plaintext tokens (Figma + Notion) → 1Password Connect + `.mcp.json.example`. Pre-commit `detect-secrets` already covers it
- [infra][secrets] Remove `db.sqlite3` from HEAD on Ayla djangoproject + add to `.gitignore` + CI guard against re-add. **No git history rewrite** unless sensitive data confirmed in SQLite (per user — history rewrite is risk-asymmetric)

#### Bucket 4 — Ayla djangoproject infra (5 issues)
- [infra][migration] Ayla djangoproject docker-compose with Postgres 16 + Redis 7 + MinIO + Django dev service
- [infra] Ayla djangoproject `settings/dev.py` + `settings/test.py` — Postgres `DATABASES`, Redis `CACHES`, Celery `CELERY_BROKER_URL`
- [infra] Ayla djangoproject Celery worker + beat in docker-compose; `make worker` / `make beat` shortcuts
- [infra][migration] One-off SQLite → Postgres migration script + verification (`dumpdata` → `loaddata` + all fixtures still pass)
- [infra] Outbox worker enabled in compose (`appointments/infrastructure/outbox_worker.py` was implemented but never run); integration test that OutboxEvent rows get consumed

#### Bucket 5 — Payment refactor (1 issue)
- [refactor][payment] Ayla djangoproject: move `Payment` from `appointments/models.py:318` → `payments/models.py`. New `Payment` in payments app; `Appointment` FK → Payment; data migration + reverse migration tested. Run full `appointments/tests/` suite + manual smoke (create→pay→cancel→refund) before merge

#### Bucket 6 — YooKassa consolidation (2 issues)
- [refactor][payment] ai-bot-platform `apps/orders` reduced to display-only. **Keep:** `OrderView`, `PaymentStatusView`, `PaymentLinkMessage`. **Remove:** payment creation, YooKassa SDK calls, capture/refund logic, payment truth storage. Skills that triggered checkout now call Ayla `POST /api/v1/payments/create` and render returned link
- [refactor][payment] ai-bot-platform: remove `/api/v1/yookassa/webhook` URL from `urls.py`; switch YooKassa Personal Cabinet webhook URL to Ayla djangoproject

#### Bucket 7 — Event contract Ayla → bot-platform (12 issues; #441 is BLOCKER for the rest)

*Mandatory per ADR-0009 — Variant A only works if Ayla publishes domain events to bot-platform on every state change.*

**Prerequisite (must land before any other Bucket 7 ticket):**
- **#441** [docs][contracts] **`docs/architecture/event-contract.md`** — full event taxonomy (12 events listed below + envelope schema), `event_version` rules, idempotency requirements, dedup-by-`event_id`, deprecation policy (≥30 days for breaking changes), failure modes, retry budget, lag SLA (>5 min → on-call alert)

**Ayla djangoproject — outbox publishers (3 issues):**
- **#429** [events][contracts] Ayla djangoproject: outbox + dispatcher for `booking.{created,cancelled,rescheduled,completed}`. Idempotent, retries, exponential backoff, on-call alert on >5 min lag
- **#430** [events][contracts] Ayla djangoproject: outbox + dispatcher for `payment.{authorized,captured,failed,refunded}`
- **#431** [events][contracts] Ayla djangoproject: outbox + dispatcher for `service.updated`, `master.schedule.updated`, `review.created`, `user.profile.updated`

**ai-bot-platform — ingestion endpoint (1 issue):**
- **#432** [events][contracts] ai-bot-platform: new endpoint `POST /api/v1/internal/events/ingest` with HMAC-SHA256 signature verification (shared secret in Vault) + per-`(event_name, event_version)` handler routing + dedup-by-`event_id`

**ai-bot-platform — consumers, split into 5 separate handler tickets per tech-lead review:**
- **#433** [events][contracts] **umbrella issue** — consumers index + cross-handler concerns (handler registration, dead-letter queue, observability)
- **#442** [events][contracts] Consumer: `booking.*` — upsert `RemoteBookingProxy` + create/cancel/reschedule `BookingReminder`, trigger post-visit review skill on `booking.completed`
- **#443** [events][contracts] Consumer: `payment.*` — update `Conversation` context, on `payment.failed` trigger payment-failed skill + optional handoff
- **#444** [events][contracts] Consumer: `service.updated` — invalidate `apps/catalog` mirror cache for service + dependent slots
- **#445** [events][contracts] Consumer: `master.schedule.updated` + `review.created` — invalidate slot cache for master, update `ClientProfile` RFM/sentiment
- **#446** [events][contracts] Consumer: `user.profile.updated` — sync PII subset (name, avatar) in bot-platform `apps/identity` channel-identity layer

**Idempotency contract test (1 issue):**
- **#447** [test][contracts] Idempotency contract test: send same event 3 times → assert exactly one side-effect per consumer (RemoteBookingProxy upsert, cache invalidation, etc.)

#### Bucket 8 — API Gateway (1 issue)
- [gateway][infra] Nginx routing at `api.ayla.app`. Path-based: `/auth/*`, `/users/me/*`, `/specialists/*`, `/services/*`, `/categories/*`, `/appointments/*`, `/payments/*`, `/reviews/*`, `/schedule/*`, `/search` → Ayla djangoproject. `/ai/*`, `/customer/chat/*`, `/customer/memory/*`, `/customer/conversations/*`, `/customer/auth/verify`, `/customer/slots`, `/internal/events/ingest` → ai-bot-platform. Health probes per backend. Tested with curl matrix in `docs/qa/phase-0-gateway-routing.md`

#### Bucket 9 — Unified JWT contract (1 issue)
- [jwt][contracts] Unified JWT: Ayla djangoproject auth issues JWT with `tenant_id` claim per ADR-0009. ai-bot-platform middleware verifies with same signing key (or shared issuer). Aligns with #246 (User.tenant_id removal → TenantUserRelationship). Anonymous-to-user merge via #258 works through gateway. Phase A.7 DRF-242.8 work serves as starting point on bot-platform side

#### Bucket 10 — ADRs (2 issues)
- [docs][llm] ADR-0010: LLM Provider Choice for Russian. Evaluate Claude Sonnet 4 / GPT-4o / GigaChat / YaLM / Gemini by 7 criteria (per user): (1) Russian language quality, (2) wellness/beauty domain understanding, (3) tool-calling reliability, (4) cost per 1K tokens, (5) p95 latency, (6) API reliability + rate limits, (7) safety (no medical promises). Choose primary + fallback. Consume existing «LLM Benchmark — Выводы и рекомендация» Notion doc as input. Close before Phase 1 pilot
- [docs][privacy] ADR-0011: UserPersonalContext Privacy & Retention Policy. Lock down fields, retention, encryption-at-rest, access logs, 152-ФЗ alignment. Even if legal audit later overrides, engineering needs known boundaries. Pair with #228-230 UserPersonalContext models — those must include `sensitivity_level`, `source`, `last_inferred_at`, `delete_requested_at` from day 1 (per user — adding them retroactively is expensive)

#### Bucket 11 — Tests (1 issue)
- [test][contracts] E2E + automated integration test (not just manual smoke). Test: mobile creates booking via Ayla API → Ayla persists → outbox publishes `booking.created` → bot-platform `/internal/events/ingest` receives → consumer updates RemoteBookingProxy + memory → user asks via Telegram "когда у меня запись" → bot replies with correct datetime. Automated portion: API-level (POST appointment → assert event sent → assert proxy created). Manual portion: Telegram round-trip. Document in `docs/qa/phase-0-roundtrip-smoke.md`

#### Bucket 12 — Booking_source dual-mode (1 issue)
*Per user: practical recognition that not all masters use YClients.*
- [model] Ayla djangoproject: add `booking_source: 'yclients' | 'ayla_local'` field to Master / Salon / Tenant model. If `yclients`: Ayla shows slots from YClients (existing), creates in YClients, mirrors locally. If `ayla_local`: Ayla owns schedule + booking + slots directly. Document in `docs/architecture/booking-source-dual-mode.md`

**Total new issues to create: 28 (original) + 7 (added per tech-lead review) = 35.**

Order of work in Bucket 7 (gated by #441):
1. #441 `event-contract.md` doc — must land first (blocker).
2. #429, #430, #431 — outbox publishers (can run in parallel after #441).
3. #432 — ingest endpoint (parallel to publishers).
4. #442–#446 — consumers (after #432).
5. #433 — umbrella (closes when #442–#446 close).
6. #446 — idempotency contract test (after at least one consumer ships).

---

## Sprint week-by-week (combined)

### Week 1 (2026-05-20 → 2026-05-27) — Documents + Rebrand kickoff + Secrets

- Bucket 1 (ADR docs): all 3 issues.
- Bucket 2 (rebrand): kick off all 4 issues — package rename, Bundle IDs (start Apple provisioning early), env URL DNS prep, README/CLAUDE.md.
- Bucket 3 (secrets): both issues done.
- Bucket 12 (booking_source field): start.
- Track A: continue Sprint 1 EPICs at current pace.

### Week 2 (2026-05-27 → 2026-06-03) — Ayla infra + Payment refactor

- Bucket 4 (Ayla djangoproject infra): all 5 issues — Postgres, Celery, Redis, outbox, settings/test.
- Bucket 5 (Payment refactor): the one big move.
- Bucket 2 (rebrand): finish what's pending. Apple provisioning may still be in review — does not block.
- Bucket 10 (ADRs): start ADR-0010 (LLM evaluation) + ADR-0011 (privacy).
- Track A: continue Sprint 1 EPICs.

### Week 3 (2026-06-03 → 2026-06-10) — YooKassa consolidation + Event contract + Gateway + JWT

- Bucket 6 (YooKassa): both issues — orders→display-only, webhook URL switch.
- Bucket 7 (event contract): all 5 issues — Ayla outbox/dispatcher for booking/payment/other, bot-platform ingest endpoint, consumers.
- Bucket 8 (API Gateway): Nginx routing.
- Bucket 9 (JWT contract): aligned across both backends.
- Bucket 11 (round-trip test): automated integration test + manual smoke.
- Bucket 10 (ADRs): finalize.

### Week 4 (2026-06-10 → 2026-06-17, buffer) — Catch-up + Phase 0 close

- Catch any spillover (especially Apple provisioning if Bundle IDs not approved).
- Final sign-off: all 9 close criteria validated via checklist.
- Phase 0 close PR: tech lead merges closing doc that unblocks Phase 1.
- Track A Sprint 1 EPICs should be approaching done or done.

---

## What's NOT in Phase 0 (explicit non-goals)

Per user:
- **No visual redesign / brand book / App Store marketing visuals.** Technical rebrand only.
- **No AI-avatar work** — deferred Phase 2 (Linear DRF-235 already deferred).
- **No voice STT+TTS** — deferred Phase 2+.
- **No Sprint 2/3/4 product features** — frozen until Phase 0 closes.
- **No Settings Hub UI / Conversation Dashboard / Schedule Editor UX** — Phase 1.5.
- **No KZ localization** — Phase 5.
- **No git history rewrite for db.sqlite3** unless sensitive data confirmed.

If a ticket can't tell which side it falls on, default to "not Phase 0".

---

## Risk register

| ID | Risk | Mitigation in Phase 0 |
|---|---|---|
| **T1** (PRD) | 152-ФЗ for UserPersonalContext (red-zone fields) | ADR-0011 written in Phase 0; legal audit kicked off Week 1 (does not gate Phase 0 close but DOES gate Phase 1 pilot) |
| **T2** (PRD) | LLM quality for Russian | ADR-0010 in Phase 0; benchmark consumed; primary + fallback chosen |
| **E1** (PRD) | B2B→Consumer expertise gap | Per user — not blocking Phase 0; separate "Consumer Retention Track" in Phase 1+ |
| **Booking SoR drift** | YClients vs ayla_local | Bucket 12 `booking_source` field; documented in architecture |
| **Apple Bundle ID provisioning slow** | Could take 3-7 days | Per user — does not block other Phase 0 work; mobile builds can stay on dev Bundle IDs until provisioning approved |
| **Event delivery lag** | Stale memory if events lost | Outbox pattern (Postgres outbox already proven in bot-platform); retries; on-call alert on >5 min lag |
| **db.sqlite3 history rewrite risk** | Could orphan branches | Per user — skip rewrite unless sensitive data confirmed |

---

## Linear ↔ GH alignment

- Linear remains the day-to-day tracker for individual engineers (DRF-XXX issue numbers).
- GH milestone `Sprint 1 — Foundation backbone` is the **single source of truth** for what counts as foundation work.
- New 28 issues created via `gh issue create` in this milestone with labels:
  - `P0` (priority)
  - `ayla-foundation` (matches Sprint 1 family)
  - one of: `epic:infra`, `epic:rebrand`, `epic:contracts`
  - plus type tags: `docs`, `migration`, `events`, `secrets`, `gateway`, `jwt`, `payment`, `refactor`, `test`, `model`

---

## Definition of Phase 0 close (Andrey signs off when all 9 are true)

1. ADR-0009 merged across all three repos (`docs/adr/` in bot-platform, `docs/architecture/` in Ayla djangoproject, `docs/` in ayla-ai-core). Linked from `CLAUDE.md` in bot-platform.
2. Technical rebrand done: `@beautygo/*` → `@ayla/*`, Bundle IDs flipped (Apple may still be in review — backend rebrand still counts), env URLs migrated, README/CLAUDE.md/backend service names updated.
3. Secrets hygiene: `git ls-files | xargs grep -l 'sk-' 2>/dev/null` returns nothing. `db.sqlite3` not in HEAD. Pre-commit `detect-secrets` active in all three repos.
4. Ayla djangoproject `manage.py runserver` boots against Postgres. `select_for_update()` in `appointments/infrastructure/availability/` actually locks rows (verified by concurrency test). Outbox worker consumes events.
5. `Payment` lives in `payments/`. `appointments/models.py:318` no longer contains Payment.
6. bot-platform: `grep -r 'YooKassa\|yookassa' apps/` returns only display-only Order paths.
7. Event contract: round-trip test passes (mobile booking → bot memory update). Both automated and manual portions documented in `docs/qa/`.
8. `curl api.ayla.app/auth/health` → Ayla. `curl api.ayla.app/ai/health` → bot-platform. Gateway routing complete.
9. JWT issued by Ayla, verified by both backends with `tenant_id` claim. Anonymous-to-user merge round-trip works.

When all 9 green: Andrey opens a PR titled "Phase 0 close — unblock Phase 1 MVP work" that updates `feedback_freeze_mvp_until_boundaries_locked` memory to "RESOLVED — Phase 0 closed YYYY-MM-DD" and lifts the Sprint 2/3/4 freeze.

---

## Phase 1 preview (starts when Phase 0 closes)

For context only — not in Phase 0 scope.

**Phase 1 MVP-0** (~4-6 weeks, target pilot 2026-07-15 per Sprint B DRF-300):
- Close DRF-116 (BottomNav design for 5/4 tabs).
- Close DRF-241 (REST `/api/v1/ai/chat/` over AIConcierge) — in-flight.
- Food Scanner v1: nutrition domain in Ayla djangoproject + `apps/skills/food_logging` in bot-platform + LogMeal/Passio recognition. **No medical claims, no diagnoses.**
- Pre-launch QA + 50 masters onboarded in Penza.
- Sprint B validation (DRF-231/232/233/234) + Decision Day.

**Phase 1.5** (~2-3 weeks, late July):
- UserPersonalContext extended: 8 anti-spam rules in prod, 3 source types, daily Celery inference.
- Settings Hub UI (NotificationPreference in `apps/adminconsole`).
- Conversation Dashboard for salon admins.
- Schedule Management UX.

**Phase 2** (Aug–Sep):
- AI-avatar (DRF-147 — recheck whether to unfreeze).
- Voice STT+TTS.
- 152-ФЗ legal audit closure.

**Phase 5** (Oct–Nov):
- KZ localization.
- Kaspi Pay.
- 50 masters in Almaty.

---

## Notes on user's 5 corrections (acknowledged)

1. **db.sqlite3 — no history rewrite by default.** Reflected in Bucket 3, risk register, close criterion #3.
2. **App Store provisioning doesn't block other work.** Reflected in Bucket 2 and risk register.
3. **UserPersonalContext fields from day 1 (sensitivity_level, source, last_inferred_at, delete_requested_at).** Reflected in Bucket 10 (ADR-0011), and will be added to #228 model issue's body.
4. **Automated integration test, not just manual smoke.** Reflected in Bucket 11.
5. **Food Scanner split: nutrition domain in Ayla djangoproject, AI interpretation/skill in bot-platform.** Reflected in Phase 1 preview + matches ADR-0009 §Domain ownership matrix.

Management rule reinforced throughout: «Если задача не помогает закрыть Phase 0, она не делается сейчас».

---

## GH issue mapping (created 2026-05-20)

All in milestone `Sprint 1 — Foundation backbone`. Labels: `P0`, `ayla-foundation`, plus epic tag (`epic:infra` | `epic:rebrand` | `epic:contracts`) + type tags.

### Bucket 1 — ADR-0009 docs
- [#412] [docs] Merge ADR-0009 into ai-bot-platform docs/adr/ + link from architecture.md + CLAUDE.md
- [#413] [docs] Copy ADR-0009 to Ayla djangoproject docs/architecture/
- [#414] [docs] Copy ADR-0009 to ayla-ai-core docs/

### Bucket 2 — Technical rebrand
- [#415] [rebrand] frontAyla: @beautygo/* → @ayla/* yarn workspaces rename + build smoke
- [#416] [rebrand] frontAyla: Bundle IDs ru.beautygo.* → ru.ayla.* (Expo app.config + provisioning)
- [#417] [rebrand] Env URL dev.gobeauty.site → dev.ayla.app (DNS + certs + reverse proxy + 30-day 301)
- [#418] [rebrand] README + CLAUDE.md + backend service names + API doc titles: BeautyGo → Ayla across 3 repos
- (#226 already exists for brand asset folder + design tokens — NOT recreated)

### Bucket 3 — Secrets hygiene
- [#419] [infra][secrets] Remove .mcp.json plaintext tokens (Figma, Notion) → 1Password Connect + .mcp.json.example
- [#420] [infra][secrets] Remove db.sqlite3 from HEAD in Ayla djangoproject + .gitignore + CI guard (no history rewrite)

### Bucket 4 — Ayla djangoproject infra
- [#421] [infra][migration] Ayla djangoproject docker-compose: Postgres 16 + Redis 7 + MinIO + Django dev
- [#422] [infra] Ayla djangoproject settings/dev.py + settings/test.py — Postgres/Redis/Celery config
- [#423] [infra] Ayla djangoproject Celery worker + beat in docker-compose + make worker/beat shortcuts
- [#424] [infra][migration] Ayla djangoproject SQLite → Postgres dev data migration script + verification
- [#425] [infra] Ayla djangoproject outbox worker enabled in docker-compose + integration test

### Bucket 5 — Payment refactor
- [#426] [refactor][payment] Move Payment from appointments/models.py:318 to payments/models.py (Ayla djangoproject)

### Bucket 6 — YooKassa consolidation
- [#427] [refactor][payment] ai-bot-platform apps/orders → display-only (remove YooKassa lifecycle)
- [#428] [refactor][payment] ai-bot-platform: remove /api/v1/yookassa/webhook + switch YooKassa Personal webhook URL to Ayla

### Bucket 7 — Event contract Ayla → bot-platform (expanded 2026-05-20 per review)
- [#441] **PREREQUISITE** [docs][contracts] event-contract.md taxonomy + envelope + versioning rules (blocks #429-#433, #441-#446)
- [#429] [events][contracts] Ayla djangoproject outbox + dispatcher for booking.{created,cancelled,rescheduled,completed}
- [#430] [events][contracts] Ayla djangoproject outbox + dispatcher for payment.{authorized,captured,failed,refunded}
- [#431] [events][contracts] Ayla djangoproject outbox + dispatcher for service.updated, master.schedule.updated, review.created, user.profile.updated
- [#432] [events][contracts] ai-bot-platform: /api/v1/internal/events/ingest endpoint with HMAC verification + per-`(event_name, event_version)` handler routing + dedup-by-event_id
- [#433] [events][contracts] **umbrella** — consumers index + cross-handler concerns (DLQ, observability)
- [#442] [events][contracts] Consumer: booking.* family
- [#443] [events][contracts] Consumer: payment.* family
- [#444] [events][contracts] Consumer: service.updated (catalog cache invalidation)
- [#445] [events][contracts] Consumer: master.schedule.updated + review.created
- [#446] [events][contracts] Consumer: user.profile.updated
- [#447] [test][contracts] Idempotency contract test (same event 3x → exactly one side-effect per consumer)

### Bucket 8 — API Gateway
- [#434] [gateway][infra] Nginx API Gateway: api.ayla.app/* routing to Ayla + bot-platform per ADR-0009

### Bucket 9 — Unified JWT contract
- [#435] [jwt][contracts] Unified JWT with tenant_id claim — Ayla issues, both backends verify

### Bucket 10 — ADRs (close before Phase 1)
- [#436] [docs][llm] ADR-0010: LLM Provider Choice for Russian — 7-criteria evaluation + primary/fallback
- [#437] [docs][privacy] ADR-0011: UserPersonalContext Privacy & Retention Policy (152-ФЗ engineering boundaries)

### Bucket 11 — Tests
- [#438] [test][contracts] E2E + automated integration test: mobile booking → event → memory update → Telegram answer

### Bucket 12 — Booking_source dual-mode
- [#439] [model] Ayla djangoproject: booking_source dual-mode field (yclients | ayla_local) on tenant/salon/master
