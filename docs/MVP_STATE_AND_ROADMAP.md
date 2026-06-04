# Ayla — State of the Platform & Roadmap to MVP

> **Status: authoritative, 2026-06-04.** This is the single source of truth for *what exists, what does not, what blocks us, and the path to MVP.* It supersedes scattered/aspirational status tables elsewhere. Where a `CLAUDE.md` "spec alignment" table or an older plan disagrees with this document, **this document wins** (and the older doc should be corrected — see §6).
>
> Grounded in seven read-only code audits of `origin/dev` across `ai-bot-platform`, `beautygo_backend` (Ayla canonical SoR), `ayla-ai-core`, and the tech lead's in-production `formula_tela` monolith (2026-06-04 session).

---

## 1. Product vision (confirmed)

**One nationwide "Ayla" bot.** A client messages a single bot in MAX; the AI understands the request, **matches the best master across ALL salons**, books, **remembers the client**, and cares for them daily (beauty + food/wellness). Beauty booking is the entry + monetisation; long-term personal memory is the differentiator ("AI, который помнит. Всегда."). Two-sided: clients + providers (salons/masters).

**Not** one bot per salon. **Not** an external aggregator. The cross-salon marketplace is built *into* this backend pair.

## 2. Architecture trunk (decided)

- **Ayla (`beautygo_backend`) is the single source of truth** for schedule, catalog, bookings, payments, reviews, profiles (ADR-0009).
- **The bot (`ai-bot-platform`) is a read/write mirror via REST** — it reads slots+catalog and writes bookings through Ayla REST; it never owns canonical transactional state.
- **Tenant isolation is the safety invariant** (`STRICT_TENANT_SCOPE`, `TenantScopedManager` → `CrossTenantError`), hardened this session (PRs #995/#998/#1000/#1009) and enforced by the import-boundary linter (#1011).
- **Lock to a salon only at booking.** Discovery runs tenant-less (`current_tenant()=None`, blessed for global scope). Cross-tenant discovery is a **public, read-only carve-out** (`all_tenants`, public fields only); all commercial reads/writes stay tenant-scoped.
- **AI conversation engine = `ayla-ai-core` v0.8.1** (frozen): `AIConcierge` orchestrator, provider-agnostic UUID-ready tool schemas, anti-hallucination dispatch, built-in Claude adapter, `AYLA_MARKETPLACE_VOICE`. Use as-is; do not modify during freeze.
- **Memory, recommendations & personalization are CHANNEL-INDEPENDENT PLATFORM SERVICES — not bot features.** They are owned by the AI runtime (`ai-bot-platform` + `ayla-ai-core`, per ADR-0009) and exposed via API, consumed **identically by every channel**: the MAX bot, the Mini App, and the **mobile app** (the mobile app reaches them through `beautygo_backend` proxying to the personalization/recommendation API). The bot is *one* channel into Ayla's memory — memory must never live only in the bot. North Star «AI, который помнит. Всегда.» = the **same** memory + recommendations on every surface (bot chat, mobile home screen, Mini App, booking assistant). A request like the mobile `GET /api/v1/customer/home` and a bot turn must draw on the same context (preferences, history, favourite masters, constraints, wellness signals).

```
beautygo_backend  = source of truth (schedule, catalog, bookings, payments, profiles)
ayla-ai-core      = AI logic: memory, recommendations, reasoning, anti-hallucination
MAX bot           = conversational channel  ─┐
Mobile app / Mini App = visual channel (DRF) ─┴─► both consume the SAME memory + AI via API
```

## Decisions — confirmed 2026-06-04 (tech lead)

1. **One Ayla for all salons** — not a bot-per-salon, not an aggregator. Locked.
2. **Ayla backend is the single source of truth** for bookings + schedule; the bot is a REST mirror, never a CRM/booking store.
3. **Pilot is staged:** **① Technical Go-Live = M0 + FOUNDATION + P0** (iron the booking chain on Ayla first); **② Product Go-Live = + MEM-lite + ENGAGE-lite** as a fast-follow. Do **not** block the first live test on the full memory/nudge build.
4. **Provider walk-in / manual booking is IN P0** (minimal: name · phone optional · service · time · master — no CRM/payment), to prevent double-booking.
5. **Cross-tenant marketplace is NOT in the first Penza pilot**, but the foundation is built forward-compatible.
6. **Memory & recommendations are platform capabilities, not bot-only** — bot and mobile app consume the same Ayla memory/recommendation/personalization via backend APIs (see the trunk bullet above). The mobile app must never become a "dumb витрина" while the magic lives only in MAX.
7. **Status-honesty rule** — never write "done" when only the spec is done (see §6).

---

## 3. What we HAVE (grounded)

The platform is **far more built than "greenfield."** A mature two-sided system exists.

### 3.1 Client bot (`ai-bot-platform`)
- **14 conversational skills** (`apps/skills/*`): `welcome`, `booking`, `faq` (KB/RAG via ChromaDB + confidence-gate→handoff), `payment_failed`, `privacy_consent` (152-ФЗ), `human_handoff`, `food_scanner` + `food_clarify` + `food_correction`, `water`, `nutrition_anketa`, `health_screening`, `cross_domain` (stub), `echo`.
- **Booking conversation — 8 LLM tools** (`apps/skills/booking/tools.py`): `show_masters`, `show_slots`, `confirm_booking` (2-button card → create + schedules T-24h/T-2h reminders), `cancel_booking`, `reschedule_booking`, `show_my_bookings`, `calc_price` (+promo), `buy_certificate`.
- **MAX channel UX** (`apps/channels/max/`): text, inline keyboards (2-D, ≤29 rows), button types `callback`/`link`/`open_app`(Mini App)/`contact`, photo, typing indicators. **No native carousels/cards** — lists render as text + button rows.
- **Mini App** (`apps/miniapp_api/` + React `apps/miniapp/`): catalog, full booking flow, profile, 152-ФЗ delete, wellness dashboard, recommendations proxy.
- **Lifecycle notifications**: confirmation, T-24h/T-2h reminders, cancel/reschedule notices, day-after review nudge (B11), payment-failure cascade.
- **Live per-turn memory**: short-term Redis window + `ClientProfile` RFM/loyalty/lifecycle snapshot in the prompt.

### 3.2 Provider side (salon/master) — **two surfaces**
- **Ayla Pro mobile** (React Native, separate repo; backed by `beautygo_backend` DRF):
  - **Schedule CRUD** (working hours + time-off), tested — `users/schedule_api.py`.
  - **Services CRUD** (price/duration/category) — `services/views.py`.
  - **Bookings**: see / **complete** / **no-show** / cancel / reschedule — `appointments/views.py` (race-safe, tenant-checked).
  - **Reviews reply** — `reviews/views.py`.
  - **Commission + split**: 8% + YooKassa split to specialist sub-account, hold→capture, refunds — `payments/services.py`.
  - Push(FCM)+SMS notifications: new-booking / cancel / reschedule / no-show.
- **Master Mini App in the bot** (`ai-bot-platform/apps/master_api`):
  - Onboarding (invite-token claim/accept), daily **dashboard** ("now / next / needs attention"), schedule **view** + availability-**request** (owner approves), **customer roster** (masked), notification prefs, catalog view.
  - **Master-AI**: the master reads/sends client DMs with **AI drafts** — generate / send-as-me / release-to-AI. (AI concierge on the provider side too.)

### 3.3 Infrastructure (built, some not wired — see §4)
- **Tenant isolation** — hardened, linter-enforced.
- **Memory models** — `UserPersonalContext`, `MemoryEntry` (zones), `memory_writer` (consent/minor-protection), `red_zone_reader` (RLS). **Models exist; not wired into chat (§4).**
- **`ayla-ai-core` v0.8.1** — the canonical, production-hardened concierge (extracted from `formula_tela`, 30+ days in prod), Claude-ready, frozen.
- **Ayla booking engine** — slot math (30-min grid, time-off/break/booked/min-notice/timezone), atomic `select_for_update` create, cancel/reschedule services. **Complete + tested.**
- **Worker/consumer** — drains `ingress:max`; compose service added (#1010/#1012).

---

## 4. What we DON'T have / what is MIS-WIRED (the real gaps & blockers)

Ordered by impact on the vision.

| # | Gap / blocker | Detail | Impact |
|---|---|---|---|
| **G1** | **Bot booking runs on YClients + single-tenant, not Ayla, not cross-salon** | `apps/skills/booking` resolves slots from YClients (dormant until `YCLIENTS_*`) and writes a local `BookingRequest`; bound to one tenant via `MAX_BOT_TENANT_SLUG`. Three divergent availability stores (Ayla native / bot-YClients / bot-own-scheduling), none reads Ayla `/slots`. | **Blocks the trunk + marketplace.** This is P0. |
| **G2** | **Long-term memory NOT wired into runtime AI — across ANY channel** | `UserPersonalContext`/`MemoryEntry` have **zero runtime references** in skills/orchestrator. The infrastructure exists but is not wired into runtime AI experiences on **any** surface: bot chat, mobile app, Mini App, recommendation/home screens, the booking assistant. Memory is a **channel-independent platform capability**, not bot-only; today it feeds none (only short-term + RFM snapshot is live). | **Kills the headline differentiator on every channel.** |
| **G3** | **No cross-tenant marketplace index** | `CatalogMaster`/`CatalogService` are per-tenant mirrors (`TenantScopedManager`). Discovery is within-tenant only. No nationwide search a master "joins." | **Blocks "finds a master across all salons."** |
| **G4** | **No in-chat `recommend_services`; no proactive nudge engine** | `recommend_services` + a rich nudge engine (repeat-offer, win-back, re-engagement, care-by-health-signal, cross-promo — 11 classes) exist in `formula_tela`, **absent in platform**. Platform has only fixed reminders + one day-after nudge. `cross_domain` (mixed-intent) is a stub. | Weakens matching ("paralysis of choice") + retention. |
| **G5** | **No provider manual/walk-in booking** | `appointments.create()` forbids non-clients. A salon can't enter a phone/walk-in booking into its own diary. If the salon books outside Ayla, the bot's slots go stale → **double-booking risk**. | Table-stakes for real salons; pilot risk. |
| **G6** | **No self-serve provider onboarding; no multi-master salon management** | Onboarding is ops-runbook (`solo_onboarding.py`, SQL-verified) or invite-token. No "register my salon" flow. All provider surfaces are single-master-scoped; no owner/manager surface for multiple staff. | **Blocks nationwide scale.** |
| **G7** | **No provider analytics / payout ledger** | `analytics` app is ingest-only; RFM is client-side/admin-only; commission split happens at transaction time with **no earnings/settlement surface**. | Provider retention; post-pilot. |
| **G8** | **Bot ↔ Ayla REST read/write client does not exist** | Nothing in the bot calls Ayla's internal REST for slots/catalog/booking. This is the **shared foundation** G1 and G3 both need. | Foundation for P0 + P1. |
| **G9** | **Ops: MAX not provisioned** | `MAX_BOT_TOKEN`/`_WEBHOOK_SECRET`/`MAX_BOT_TENANT_SLUG`/`_MINIAPP_URL` are empty placeholders; webhook unregistered; prod worker launch unverified (out-of-repo deploy). | Bot is dark until set. Config/ops. |
| **G10** | **Rebrand leftovers** | Deep links `beautygo-pro://` (→ `ayla-pro://`), NPM `@beautygo/shared`, repo names. | Cosmetic; pre-launch sweep. |

---

## 5. MVP definition & cut-line

**MVP = a launchable Penza pilot that proves the model and is forward-compatible to the nationwide marketplace.** Released in stages (tech-lead decision 2026-06-04):

- **① Technical Go-Live = M0 + FOUNDATION + P0** — the iron booking chain: the bot books *one* Penza salon **through Ayla** (not YClients), correct slots, **no double-booking** (incl. provider walk-in), client can reschedule/cancel, salon sees the booking. Validates the riskiest unknown (booking on Ayla) first, on real users.
- **② Product Go-Live = + MEM-lite + ENGAGE-lite** (fast-follow) — adds light **cross-channel** memory + light recommendations/nudges so Ayla feels like Ayla, not a plain booking bot. Does **not** block ①.
- **③ MVP-Marketplace = + P1 + P2 + P3** — cross-tenant discovery + tenant-less bot + handoff. The actual vision; built in parallel, flipped on when ready. **Not** in the first Penza pilot.

Everything in §4 not in a milestone below is **post-MVP**.

---

## 6. Documents that caused the confusion (to correct)

The "planned-as-done" pattern (a roadmap target cited as implemented) created real confusion this session. Corrections:

1. **`.importlinter.baseline` confabulation** — cited as enforcement across 3 docs; the file never existed (the real enforcement is `ruff` TID251 + the new AST linter #1011). Tracked: **#1001** (enforcement) + **#1002** (doc sweep).
2. **"AI Chat / booking — not implemented"** (`beautygo_backend/CLAUDE.md` spec-alignment) — misleading: the **bot** (this repo) has a full 8-tool booking conversation, and `ayla-ai-core` ships the concierge. Correct to: *"AI booking conversation lives in `ai-bot-platform` + `ayla-ai-core`; what is missing is its **regrounding onto Ayla REST** (G1)."*
3. **"UserPersonalContext — not implemented"** — half-true: the **models/writer exist** but are **not wired into chat** (G2). Correct to: *"infrastructure exists; not yet read/written during conversation."*
4. **Tenant/marketplace model undocumented** — the "tenant = salon → N bots" implication and the cross-tenant marketplace plan were nowhere written, causing the core misunderstanding. Fixed by this document + the marketplace EPIC **#1014** (promote to an ADR).
5. **Three availability stores** — no doc states which of Ayla-native / bot-YClients / bot-own-scheduling is canonical. This document fixes it: **Ayla is canonical; the others are divergences to retire** (G1).

**Action:** add a pointer to this document at the top of both `CLAUDE.md` files; apply corrections 2–3 to the `beautygo_backend` spec-alignment table; promote #1014 to an ADR. (Done where safe in this PR; CLAUDE.md behavioural edits proposed for tech-lead sign-off.)

### Status-honesty rule (adopt going forward)

Docs must **never** write "done"/"implemented" when only the spec or a model exists. Every capability carries an explicit status:

**`designed` → `implemented` → `wired` → `tested` → `production-ready`**

"Implemented" ≠ "wired" (cf. G2: memory is *implemented* but not *wired*). This single rule prevents the planned-as-done disease that caused this session's confusion.

---

## 7. Roadmap to MVP (detailed, phased)

Sizes: **S** ≈ days, **M** ≈ 1–2 weeks, **L** ≈ 3+ weeks (one stream). Streams run in parallel where noted.

### M0 — Ops & single-salon enablement · S · (config/ops)
Make the *existing* bot live for one Penza salon.
- Provision MAX env/secrets (`MAX_BOT_TOKEN`/`_WEBHOOK_SECRET`/`MAX_BOT_TENANT_SLUG`/`_MINIAPP_URL`); register webhook; confirm prod worker runs (G9).
- Onboard the Penza salon + masters via `solo_onboarding` (G6 ops path).
- **Acceptance:** the current bot answers + books in MAX for the Penza tenant.
- Owner: ops + 1 stream. **No app code.**

### FOUNDATION — Bot ↔ Ayla REST client · M · (G8)
The shared spine for P0 + P1.
- `apps/integrations/ayla/` read/write client: `get_slots`, `get_specialists/get_services` (catalog), booking `create/cancel/reschedule` (HMAC internal API).
- **Ayla (S2):** expose/confirm the internal REST endpoints (the engine exists; this is the contract surface) + `TenantUserRelationship` grant-on-first-booking on create.
- **Acceptance:** bot can read Ayla slots/catalog and create/cancel/reschedule a booking via REST in a test.

### P0 — Reground booking on Ayla REST · M · (G1, depends on FOUNDATION)
- Repoint `apps/skills/booking` resolvers (slots/masters/services) YClients → Ayla REST.
- `confirm_booking` create: local `BookingRequest` → **Ayla REST** (resolves the ADR-0009 #1/#5 conflict; confirm the #427 carve-out intent).
- Wire user cancel/reschedule → Ayla REST.
- **Provider walk-in/manual booking** (G5): add a provider-initiated create path in Ayla so the salon's diary is the true SoR (else slots go stale).
- **Acceptance:** end-to-end chat booking in the Penza salon, slots + writes through Ayla; no double-booking with walk-ins.
- Owner: 1 bot stream + S2 (Ayla).

### MEM — Wire long-term memory as a CHANNEL-INDEPENDENT service · M · (G2)
- Expose memory + recommendation/personalization as a **platform API** (in the AI runtime), not a bot-internal hook. Read `UserPersonalContext`/relevant `MemoryEntry` into the context-builder; write via `memory_writer` (anti-spam: 1 field/session, cooldown, zones).
- **Consume the same service from every channel:** the MAX bot turn, the Mini App, and the **mobile app** (mobile reaches it via `beautygo_backend` proxy, e.g. `GET /api/v1/customer/home` returns personalised blocks: next booking, daily info, Ayla recommendation, suitable services, reminder).
- Surface memory in `recommend`/`show_masters` ("как обычно к Анне", "рядом с офисом").
- **Acceptance:** the bot **and** the mobile/Mini-App home demonstrably draw on the **same** remembered preference across sessions; 152-ФЗ "забудь X" works on all surfaces. *(MEM-lite for ② = a thin first cut: read top preferences + favourite master into context; full memory schema is the fast-follow.)*
- Owner: 1 stream. (Headline differentiator; can run parallel to P0.)

### ENGAGE — Port matching + nudges · M · (G4)
- Port `recommend_services` (goal-based) as an in-chat tool.
- Port the `formula_tela` nudge engine (repeat-offer, win-back, re-engagement, care-by-health-signal, cross-promo) onto Ayla events.
- (Optional) adopt `ayla-ai-core` `AIConcierge` spine + Claude adapter as a consolidation.
- **Acceptance:** AI recommends from "paralysis of choice"; proactive repeat/win-back nudges fire on real lifecycle events.

> **MVP-Pilot cut-line = M0 + FOUNDATION + P0 + MEM + ENGAGE.** Single Penza salon, booking on Ayla, memory live, engagement. Go-live candidate.

### P1 — Cross-tenant catalog discovery · M · (G3)
- `apps/marketplace/` — sole sanctioned `all_tenants` discovery carve-out; public-field DTO; `Tenant.city`/geo; linter #1011 contract (cross-tenant catalog reads only from `apps/marketplace/*`).
- **Freeze:** new capability — needs an explicit Phase-0 freeze exception.

### P2 — Tenant-less bot + global identity · M
- Global-bot ingress (no `tenant_scope` at entry); sentinel-tenant global `BotUser` (no migration); defer `tenant_scope` out of the miniapp decorator into booking endpoints.

### P3 — Discovery → booking handoff · M · (depends P1+P2)
- `show_masters` → marketplace index (cross-tenant); `confirm_booking` enters `tenant_scope(master.tenant)`.

> **MVP-Marketplace = + P1 + P2 + P3.** One bot finds a master across salons and books. The vision, live.

### POST-MVP (parallelisable, not on the critical path)
- **PROV**: self-serve provider onboarding + multi-master salon management (G6); provider analytics + payout ledger (G7).
- **P4**: geo/ranking, index scale, abuse/rate-limit, 152-ФЗ cross-tenant grant review, Redis slot cache, multi-channel.
- **Rebrand sweep** (G10); MAX carousels/cards UX; FAQ warmup cache.

---

## 8. Sequencing

```
M0 (ops) ─┐
          ├─► FOUNDATION (Ayla REST client) ─┬─► P0 (reground booking) ──┐
          │                                  └─► P1 (catalog discovery)  │
MEM (memory) ──────────────────────────────────────────────────────────┤► MVP-Pilot go-live
ENGAGE (recommend + nudges) ────────────────────────────────────────────┘
                              P0+P1 ─► P2 (tenant-less) ─► P3 (handoff) ─► MVP-Marketplace
POST-MVP: PROV, P4, rebrand — parallel, off critical path
```

## 9. Decisions

**Resolved 2026-06-04 (see the Decisions block in §2):**
- ✅ One Ayla for all salons. ✅ Ayla backend = SoR for bookings/schedule. ✅ Pilot staged: ① Technical Go-Live `M0+FOUNDATION+P0`, ② Product Go-Live `+MEM-lite+ENGAGE-lite` fast-follow. ✅ Provider walk-in IN P0 (minimal). ✅ Marketplace not in first Penza pilot, foundation forward-compatible. ✅ Memory/recommendations = channel-independent platform service (bot + mobile + Mini App). ✅ This doc = top source of truth. ✅ Status-honesty rule (§6).

**Still open (do not block ①):**
1. **Freeze exception** for the marketplace track (P1+). P0/MEM/ENGAGE are conformance/MVP and can start now.
2. **`BookingRequest` local write** — sanctioned carve-out (#427) or latent ADR-0009 violation? P0 redirects to Ayla REST regardless (orchestrator to verify during P0 scoping).

---

_Source audits (this session): client-bot capability, provider capability, schedule/availability, MAX readiness, marketplace architecture, formula_tela bot, ayla-ai-core. EPIC: #1014._
