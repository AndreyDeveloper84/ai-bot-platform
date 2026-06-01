# Cross-System Contract Matrix

> **Status:** DRAFT v1 — pending W3 security columns + tech-lead review
> **Date:** 2026-06-01
> **Gap-registry item:** M-P0-1 (Block A deliverable)
> **Owner:** tech-lead. The `🔒 Security review (W3)` column in every table is owned by **W3** and is intentionally left `«TBD-W3»` — do not fill it here.

## Purpose

This is the single authoritative registry of every contract/seam between the three systems:

- **`ai-bot-platform`** — the AI backbone (this repo). Owns channel identity, AI memory, conversations, skills, tools, KB/RAG, channels, eventbus consumers, tenancy runtime, observability.
- **`beautygo_backend`** = the **"Ayla djangoproject"** — canonical identity / booking / payments / catalog / reviews / nutrition. Source of truth per ADR-0009.
- **`ayla-ai-core`** — the shared, pinned Python AI library used by both consumers.

It is the stabilizing backbone that stops the three systems drifting apart. Every row is grounded in repo evidence (file + section quoted). Where a contract is undefined, undiscoverable in code, or flagged as drift by the source docs, the row is explicitly marked **DRIFT / MISSING / TBD** rather than papered over.

### Scope

3 systems, MVP surface. REST seams (Section A), the 12 MVP domain events (Section B), auth & service-to-service (Section C), env/config (Section D), and open-P0 status (Section E).

### How to read

- **Direction** is from the perspective of the caller → callee.
- **Owner service** is the canonical home per ADR-0009 §Domain ownership matrix.
- **Status** = `stable` (contract locked + code/tests exist), `DRIFT` (a real divergence the source flags), `MISSING` (no code/contract found), `TBD` (under-specified, needs a follow-up).
- **`🔒 Security review (W3)`** is empty (`«TBD-W3»`) on purpose — W3 populates threat-model / authz notes per row.

### Authoritative sources (read 2026-06-01, all on `dev` @ `5f925b56`)

- `docs/architecture/event-contract.md` — 12 MVP events, envelope, delivery, HMAC, PII rules.
- `docs/architecture/jwt-contract.md` (v1.3) — token taxonomy, claims, RS256/JWKS, `tenant_id` = active-tenant semantics, s2s consent-binding.
- `docs/adr/ADR-0009-ayla-split-domain-architecture.md` — domain ownership matrix, hard rules, mandatory event contract.
- Code: `apps/integrations/ayla/` (`profile_client.py`, `recommendations_client.py`, `nutrition_client.py`, `user_proxy.py`), `apps/integrations/ayla_payments/client.py`, `apps/eventbus/` (`ingest_security.py`, `consumers/*.py`).

> **Doc-gap note:** The two audit docs named in the M-P0-1 brief — `docs/architecture/api-spec-contract-drift-audit.md` and `docs/architecture/unified-system-architecture-audit.md` — **do not exist on `dev`** (only `event-contract.md`, `jwt-contract.md`, `event-consumers.md` are present under `docs/architecture/`). P0 statuses in Section E are therefore reconstructed from ADR-0009 + the contract docs + this session's knowledge, NOT from those audits. **The audits are themselves a gap (see Section E / follow-ups).**

---

## Section A — REST contracts (bot-platform ↔ beautygo / Ayla)

All endpoints below are grounded in actual client code in `apps/integrations/`. No endpoint is invented; where the brief asked for a contract that has no client, it is listed as **MISSING**.

| Contract / endpoint | Direction | Owner service (ADR-0009) | Request summary | Response summary | Auth model | Env vars | Covering tests/fixtures | Status | P0 ref | 🔒 Security review (W3) |
|---|---|---|---|---|---|---|---|---|---|---|
| `POST /api/v1/payments/create` | bot-platform → Ayla | Ayla (payments) | `{amount_rub:"X.XX", description, kind, recipient_name, buyer_email}` + header `Idempotence-Key: <uuid>`; only `kind="certificate"` exercised today | `{payment_id, checkout_url, status}` (YooKassa-hosted checkout URL) | `Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}` | `AYLA_BASE_URL`, `AYLA_INTERNAL_API_TOKEN`, `AYLA_PAYMENTS_TEST_MODE` | `apps/integrations/ayla_payments/tests/` (test-mode short-circuit + mocked HTTP) | stable | — | «TBD-W3» |
| Payment **retry** | bot-platform → Ayla | Ayla (payments) | No dedicated retry endpoint. Retry = re-POST `/payments/create` with the SAME `Idempotence-Key` (returns same Payment row). urllib3 `Retry(total=3, status_forcelist=[502,503,504])` on the client. | same as create | as above | as above | partial (idempotence-key asserted in client tests) | DRIFT | — | «TBD-W3» |
| `POST /internal/me/catalog/recommendations/` | bot-platform → Ayla | Ayla (catalog) | body forwarded as-is (`lat`/`lon`/`goal`/`tenant_history`); pass-through, no shape gate on bot side | parsed JSON object, pass-through | `Authorization: Bearer {AYLA_SERVICE_TOKEN}` **+** `X-External-User-ID: bot:{channel}:{id}` (no client JWT forwarded) | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | DRIFT | — | «TBD-W3» |
| `GET /api/v1/users/{user_id}` (profile fetch) | bot-platform → Ayla | Ayla (identity/PII) | path `user_id` (UUID) | closed-shape parse: **only** `display_name`, `avatar_url` retained (PII §7) | `Authorization: Bearer {AYLA_SERVICE_TOKEN}` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | stable | — | «TBD-W3» |
| `POST /api/v1/nutrition/internal/scan/` | bot-platform → Ayla | Ayla (nutrition) | multipart `image` + optional `portion_multiplier` | `data.{scan_id, dish_name, confidence, portion_g, nutrition, provider}` | `X-Service-Token: {AYLA_SERVICE_TOKEN}` **+** `X-External-User-ID` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | DRIFT | — | «TBD-W3» |
| `POST /api/v1/nutrition/internal/food-log/` | bot-platform → Ayla | Ayla (nutrition) | `{meal_type, portion_multiplier, scan_id?, dish_name?}` + optional `X-Idempotency-Key` | `data.{id, dish_name, meal_type, calories}` | `X-Service-Token` + `X-External-User-ID` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | DRIFT | — | «TBD-W3» |
| `GET /api/v1/nutrition/internal/summary/` | bot-platform → Ayla | Ayla (nutrition) | query `date?`, `with_comment?` | daily totals + goals + entries (+ optional `ai_comment`) | `X-Service-Token` + `X-External-User-ID` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | stable | — | «TBD-W3» |
| `GET/POST /api/v1/nutrition/internal/profile/` | bot-platform → Ayla | Ayla (nutrition) | GET none / POST partial-or-full profile dict; norms computed server-side under `data.norms.*` | profile + BMR/norms + `goal_overridden_by`; 200 `exists=false` or 404 = no profile | `X-Service-Token` + `X-External-User-ID` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | stable | — | «TBD-W3» |
| `POST/DELETE/GET /api/v1/nutrition/internal/water/*` | bot-platform → Ayla | Ayla (nutrition) | add `{ml, beverage_slug?, ts?}` (+`X-Idempotency-Key`), undo `…/{entry_id}/`, today `…/today/` | water entry / today totals; coefficient applied server-side | `X-Service-Token` + `X-External-User-ID` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | stable | — | «TBD-W3» |
| `GET /api/v1/nutrition/internal/deficits/` + `…/insights/cross_domain/*` | bot-platform → Ayla | Ayla (nutrition) | deficits `?days=N`; insight GET/seen/dismiss/convert (`convert` carries `appointment_id`) | hint (prompt-injection sanitized client-side) / insight object | `X-Service-Token` + `X-External-User-ID` | `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN` | `apps/integrations/ayla/tests/` | stable | — | «TBD-W3» |
| Slots lookup (`GET /api/v1/specialists/{id}/slots`) | bot-platform → Ayla | Ayla (catalog/schedule) | ADR-0009 §Hard rule #5 + §AI-initiated booking flow name this path, but **no client exists in `apps/integrations/`**. | — | TBD (presumably s2s bearer) | TBD | none found | MISSING | — | «TBD-W3» |
| Booking **create** (`POST /api/v1/appointments`) | bot-platform → Ayla | Ayla (booking) | named in ADR-0009 §AI-initiated booking flow + §Hard rule #5; **no REST client found in code.** Booking state reaches bot-platform only via the `booking.*` events (Section B). | — | TBD | TBD | none found | MISSING | — | «TBD-W3» |
| Booking **cancel** / **reschedule** | bot-platform → Ayla | Ayla (booking) | bot-platform must call Ayla REST to mutate (ADR-0009 §The contract is one-way; bot never writes Ayla state directly). **No client found.** Cancel/reschedule are observed inbound only via `booking.cancelled` / `booking.rescheduled` events. | — | TBD | TBD | none found | MISSING | — | «TBD-W3» |
| Internal **events ingest** `POST /api/v1/internal/events/ingest` | Ayla → bot-platform | bot-platform (eventbus) | single event envelope per POST (no batching MVP); see Section B + C | HTTP 200 / 401 / 422 / 400 / 500 per `event-contract.md` §8 | HMAC-SHA256 over raw body (`X-Ayla-Event-Signature`) + `X-Ayla-Event-Timestamp` ±300s | `EVENT_INGEST_HMAC_SECRET` | `apps/eventbus/tests/` (`ingest_security`) | stable | P0-4 (delivery) | «TBD-W3» |

**Section A drift / gap callouts:**
- **DRIFT (auth inconsistency, HIGH):** three different s2s auth conventions coexist across Ayla clients — `Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}` (payments), `Authorization: Bearer {AYLA_SERVICE_TOKEN}` (recommendations, profile), and `X-Service-Token: {AYLA_SERVICE_TOKEN}` (nutrition, all endpoints). Two distinct secrets (`AYLA_INTERNAL_API_TOKEN` vs `AYLA_SERVICE_TOKEN`) AND two header styles. `jwt-contract.md` §5.4 mandates a single RS256 s2s token w/ consent binding — none of these clients implement that yet. This is the biggest REST seam to converge.
- **DRIFT:** recommendations endpoint sits under `/internal/...` while everything else is `/api/v1/...` — inconsistent base path.
- **MISSING:** slots lookup, booking create, booking cancel, booking reschedule have **no REST client in this repo** despite being mandated by ADR-0009. They are the canonical AI-initiated-booking write path and must exist before that flow ships.

---

## Section B — Events (Ayla → bot-platform), the 12 MVP events

Producer is always **Ayla djangoproject** (outbox → HMAC-signed HTTP POST). Consumer handlers all live in `apps/eventbus/consumers/`. All events are `event_version: 1`. Envelope-level dedupe is `IngestDedupe(event_id)` at the dispatcher (`event-contract.md` §5); additional per-event idempotency keys noted below.

| event_name | version | Producer | Consumer handler (`apps/eventbus/consumers/...`) | Idempotency key | Side-effects | Fixture | Status | P0 ref | 🔒 Security review (W3) |
|---|---|---|---|---|---|---|---|---|---|
| `booking.created` | 1 | Ayla | `booking.py::handle_booking_created` (#442) | `event_id` (IngestDedupe) + `RemoteBookingProxy.appointment_id` upsert + `BookingReminder(ayla_appointment_id,tenant,kind)` | upsert `RemoteBookingProxy`; schedule T-24h/T-2h reminders; set `Conversation.last_booking_at`; emit internal `booking_created` | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `booking.cancelled` | 1 | Ayla | `booking.py::handle_booking_cancelled` (#442) | `event_id` + `proxy.last_synced_event_id` | proxy → CANCELLED; cancel PENDING reminders; emit `booking_cancelled`. Out-of-order (cancel-before-create) is **dropped**, not stubbed (DoS defense) | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `booking.rescheduled` | 1 | Ayla | `booking.py::handle_booking_rescheduled` (#442) | `event_id` + `proxy.last_synced_event_id` | move `start_at`/`end_at` (preserve duration); re-peg reminders; refresh `Conversation.last_booking_at`; emit `booking_rescheduled`. Refuses non-positive-duration proxy | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `booking.completed` | 1 | Ayla | `booking.py::handle_booking_completed` (#442) | `event_id` + `proxy.last_synced_event_id` | proxy → COMPLETED; emit `booking_completed`. **Contract steps 2 (post-visit review skill) + 3 (RFM/sentiment) deferred to follow-up tickets** | `apps/eventbus/tests/` | DRIFT | — | «TBD-W3» |
| `payment.authorized` | 1 | Ayla | `payment.py::handle_payment_authorized` (#443) | `event_id` + `Conversation.last_payment_event_id` | set `Conversation.pending_payment_id`; no DM | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `payment.captured` | 1 | Ayla | `payment.py::handle_payment_captured` (#443) | `event_id` + `PaymentTerminalDedupe(tenant_id,payment_id,CAPTURED)` | clear pending slot (if matches); set captured ts; reset failure counter; emit `loyalty_bonus_eligible`; no DM | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `payment.failed` | 1 | Ayla | `payment.py::handle_payment_failed` (#443) | `event_id` + `Conversation.last_payment_event_id` (under row lock) | map reason → closed enum; increment `consecutive_payment_failures`; clear pending; dispatch `payment_failed` skill **exactly at threshold** (`PAYMENT_FAILED_HANDOFF_THRESHOLD`) via `on_commit` | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `payment.refunded` | 1 | Ayla | `payment.py::handle_payment_refunded` (#443) | `event_id` + `PaymentTerminalDedupe(tenant_id,payment_id,REFUNDED)` | set refunded ts; emit `loyalty_refund_reverse`; customer DM owned by Ayla, not here | `apps/eventbus/tests/` | stable | — | «TBD-W3» |
| `review.created` | 1 | Ayla | `reviews.py::handle_review_created` (#445) | `event_id` + `ReviewProcessedDedupe(tenant_id,review_id)` | derive sentiment from rating; set `low_rating_flag` (sticky) on `ClientProfile`; never fetch review text (PII §7). Degrades pre-W4 ClientProfile migration via `hasattr` | `apps/eventbus/tests/` | DRIFT | — | «TBD-W3» |
| `service.updated` | 1 | Ayla | `catalog.py::handle_service_updated` (#444) | `event_id` + idempotent `cache_version` bump | bump `CatalogService.cache_version` (tenant-scoped); flip `is_active` via `previous_values` negation. **`duration`-change slot-cache invalidation is a no-op / FOLLOW_UP — no scheduling cache exists** | `apps/eventbus/tests/` | DRIFT | — | «TBD-W3» |
| `master.schedule.updated` | 1 | Ayla | `schedule.py::handle_master_schedule_updated` (#445) | `event_id` + additive `cache_version` bump | bump `CatalogMaster.cache_version`; per-date granularity NOT used (global counter per tech-lead Q3). **No active slot cache reader yet — forward-compat signal only** | `apps/eventbus/tests/` | DRIFT | — | «TBD-W3» |
| `user.profile.updated` | 1 | Ayla | `identity.py::handle_user_profile_updated` (#446) | `event_id` + value-comparison (no-op if unchanged) | sync `display_name`/`avatar_url` only into `BotUser`; phone/email/birthday/language cause zero traffic (PII §7). `tenant_id` may be **null** (only such event). `avatar_url` write gated on W4 migration | `apps/eventbus/tests/` | DRIFT | — | «TBD-W3» |

**Section B drift / gap callouts:**
- **DRIFT (deferred side-effects):** `booking.completed` (review-skill + RFM), `review.created` (ClientProfile fields behind W4 migration), `service.updated` & `master.schedule.updated` (slot-cache invalidation is a no-op because `apps/scheduling` has no cache layer), and `user.profile.updated` (`avatar_url` field behind W4 migration). These handlers exist and are registered, but parts of their contract-defined side-effects are stubbed/deferred — flagged so they are not mistaken for fully closed.
- All 12 events are registered and have a handler — no event is MISSING.

---

## Section C — Auth & service-to-service

| Mechanism | Type / claims | Issuer → Verifier | Spec | Code | Status | 🔒 Security review (W3) |
|---|---|---|---|---|---|---|
| `access` JWT | 15-min; `sub`, `aud:["ai-bot-platform"]`, `ayla.{token_type,tenant_id,relationships,scope,user_role,contract_version}` | Ayla issues (RS256) → bot-platform verifies via JWKS | `jwt-contract.md` §2–§3, §8 | bot-platform verifier middleware **not yet in `apps/identity`** (ticket filed post-merge per doc §11) | TBD | «TBD-W3» |
| `refresh` JWT | 90-day; rotates on use, blacklist on rotation; never reaches bot-platform | Ayla only | `jwt-contract.md` §2, §7 | n/a (Ayla side) | TBD | «TBD-W3» |
| `anonymous` JWT | 30-day; narrow scope (`provider:directory:read`, `memory:write:green_zone_only`, `chat:anonymous`); `tenant_id:null` | Ayla → bot-platform | `jwt-contract.md` §2, §6 | verifier TBD | TBD | «TBD-W3» |
| `service_to_service` JWT (spec) | 5-min RS256; MUST embed nested user-token + consent binding (`user_on_behalf_of`, scope-subset, jti single-use dedup) | each side own keypair | `jwt-contract.md` §4.4, §5.4 | **NOT implemented** — current s2s is plain bearer secret(s), see below | DRIFT | «TBD-W3» |
| s2s bearer (actual) | static long-lived shared secret(s); two distinct secrets + two header styles | bot-platform → Ayla | clients in `apps/integrations/*` | `AYLA_INTERNAL_API_TOKEN` (payments, `Authorization: Bearer`); `AYLA_SERVICE_TOKEN` (recommendations/profile `Authorization: Bearer`; nutrition `X-Service-Token`) | DRIFT | «TBD-W3» |
| Event HMAC signing | HMAC-SHA256 over raw body; `X-Ayla-Event-Signature: sha256=<hex>` + `X-Ayla-Event-Timestamp` (±300s, constant-time compare) | Ayla → bot-platform ingest | `event-contract.md` §6.2 | `apps/eventbus/ingest_security.py` (`verify_signature`) | stable | «TBD-W3» |
| `tenant_id` claim semantics | = **active** tenant, NOT ownership; null = global user scope; verifier must re-check `TenantUserRelationship` live | — | `jwt-contract.md` §5, ADR-0009 §Hard rule #6 | mirror table consulted in eventbus consumers (`assert_envelope_tenant_authorized`) | stable (event side); JWT verifier TBD | «TBD-W3» |

**Section C callouts:** the JWT contract is documented (v1.3, heavily reviewed) but the **bot-platform verifier middleware is not yet in the repo**, and the **real s2s auth is a static bearer secret diverging from the §5.4 consent-bound RS256 spec**. Both are weak spots W3 should prioritize.

---

## Section D — Env vars / config registry

| Name | Owner | Meaning | Where set / read |
|---|---|---|---|
| `AYLA_BASE_URL` | bot-platform infra | Base URL of Ayla djangoproject (currently `gobeauty.site` infra; `api.ayla.app` deferred per ADR-0009 domain update) | read by all `apps/integrations/ayla*` clients via `settings` |
| `AYLA_INTERNAL_API_TOKEN` | bot-platform infra / shared | s2s bearer secret used by **payments** client (`Authorization: Bearer`) | `apps/integrations/ayla_payments/client.py` |
| `AYLA_SERVICE_TOKEN` | bot-platform infra / shared | s2s secret used by **recommendations** + **profile** (`Authorization: Bearer`) and **nutrition** (`X-Service-Token`) | `apps/integrations/ayla/{recommendations,profile,nutrition}_client.py` |
| `AYLA_PAYMENTS_TEST_MODE` | bot-platform | when True (default) `create_payment` returns a stub URL, no HTTP | `apps/integrations/ayla_payments/client.py` |
| `EVENT_INGEST_HMAC_SECRET` | shared (Vault, quarterly rotation) | HMAC secret for inbound event signature verification | `apps/eventbus/ingest_security.py` |
| `PAYMENT_FAILED_HANDOFF_THRESHOLD` | bot-platform | consecutive-failure count at which `payment.failed` triggers the customer handoff skill | `apps/eventbus/consumers/payment.py` |

**Section D callout — DRIFT:** `AYLA_INTERNAL_API_TOKEN` and `AYLA_SERVICE_TOKEN` are two separate secrets serving the same s2s role with no documented reason; consolidating (or migrating to the §5.4 RS256 s2s token) is a follow-up.

---

## Section E — Open P0 status

> Reconstructed from ADR-0009 + the contract docs + this session's knowledge. The originally-cited audit docs (`api-spec-contract-drift-audit.md`, `unified-system-architecture-audit.md`) are **absent from the repo**, so individual P0-1..P0-7 wordings could not be quoted verbatim. Treated as best-effort mapping — **verify against the real audits once they land.**

| P0 | Topic | Status (this session) | Evidence / note |
|---|---|---|---|
| P0-1 | Cross-system contract registry (this matrix, M-P0-1) | IN PROGRESS → addressed by this DRAFT v1 | this document |
| P0-4 | Outbox / event delivery reliability | IN PROGRESS (Block C) | ingest dispatcher, HMAC, dedupe, DLQ present (`apps/eventbus/`); delivery-side hardening ongoing |
| P0-5 | ayla-ai-core version drift | **CLOSED** (per A9 / version-drift; PR #176 + #935) | this session knows A9 / version-drift is CLOSED — `ayla-ai-core` pinned per ADR-0009 repo-roles |
| P0-2 | (topic TBD — audit absent) | UNKNOWN | could not pin — audit doc missing |
| P0-3 | (topic TBD — audit absent) | UNKNOWN | could not pin — audit doc missing |
| P0-6 | (topic TBD — audit absent) | UNKNOWN | could not pin — audit doc missing |
| P0-7 | (topic TBD — audit absent) | UNKNOWN | could not pin — audit doc missing |

---

## Follow-up gaps for tech-lead

1. **Missing audit docs** — recreate / locate `api-spec-contract-drift-audit.md` and `unified-system-architecture-audit.md`; Section E P0-2/3/6/7 cannot be pinned without them.
2. **Booking write path MISSING** — no REST client for slots lookup, booking create, cancel, reschedule despite ADR-0009 mandating them. Required for the AI-initiated booking flow.
3. **s2s auth convergence** — unify `AYLA_INTERNAL_API_TOKEN` / `AYLA_SERVICE_TOKEN` and the `Authorization: Bearer` vs `X-Service-Token` split; migrate toward the §5.4 RS256 consent-bound s2s token.
4. **JWT verifier middleware** — not yet present in `apps/identity`; whole of Section C user-token verification is TBD in code.
5. **Deferred event side-effects** — close the stubbed parts of `booking.completed`, `review.created`, `service.updated`, `master.schedule.updated`, `user.profile.updated` (slot-cache layer + W4 migrations).
6. **W3 security columns** — every `🔒 Security review (W3)` cell is `«TBD-W3»`, pending W3.
