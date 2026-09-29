# Unified System Stabilization Roadmap

## Status

Living roadmap. Initial version: 2026-05-28.

This roadmap turns the open architecture audits into executable stabilization work across:

| Repository | Role |
| --- | --- |
| `ai-bot-platform-codex` | Bot runtime, mini app backend, event consumers, Ayla clients |
| `Ayla/djangoproject-codex` | Canonical backend for booking, payments, identity, catalog, nutrition |
| `ayla-ai-core` | Shared AI orchestration library |

## Goal

Make the unified Ayla system stable enough that booking, payment, identity, and AI conversation flows do not drift between services.

The target shape is simple:

1. Ayla owns transactional domains: booking, payment, catalog, identity, nutrition.
2. bot-platform owns channel UX, AI conversations, skills, reminders, and event consumers.
3. Cross-service communication happens through explicit REST contracts and explicit events.
4. Every enabled integration has contract tests, auth checks, and operational visibility.

## Non-Goals

- Do not redesign the product.
- Do not rewrite all booking/payment code at once.
- Do not remove legacy tables before migration and reconciliation are proven.
- Do not change public mobile API behavior unless required for correctness.

## Priority Rules

| Priority | Meaning |
| --- | --- |
| P0 | Blocks stable production integration or can corrupt booking/payment state |
| P1 | Important for MVP stability, but can follow P0 foundation work |
| P2 | Documentation, cleanup, or long-term maintainability |

## Phase Summary

| Phase | Theme | Main outcome | Priority |
| --- | --- | --- | --- |
| 0 | Contract freeze | Stop adding new drift while fixes are being planned | P0 |
| 1 | URL and auth foundation | bot-platform can reliably call Ayla internal APIs | P0 |
| 2 | Payment contract | Payment create/retry/events are one coherent flow | P0 |
| 3 | Event delivery | Ayla booking/payment events reach bot-platform ingest | P0 |
| 4 | Booking ownership migration | bot-platform stops owning current booking state | P0 |
| 5 | API spec cleanup | PDFs/docs match canonical implementation | P1 |
| 6 | AI core and memory boundary | AI behavior and memory ownership stop drifting | P1 |
| 7 | Hardening | Observability, replay, readiness, regression tests | P1 |

## Phase 0. Contract Freeze

### Goal

Prevent new integration drift while P0 contracts are being repaired.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 0.1 | Create one cross-system contract matrix for REST, events, auth, env vars | docs | P0 | S |
| 0.2 | Mark payment, booking, eventbus, Ayla clients as contract-frozen areas | docs + both repos | P0 | XS |
| 0.3 | Add checklist to PR template: endpoint path, auth header, response shape, owner service | both repos | P1 | S |

### Acceptance Criteria

- [ ] Every cross-service endpoint has owner, caller, path, auth, request, response, and tests named.
- [ ] New changes to booking/payment/event clients reference the contract matrix.
- [ ] No new bot-platform Ayla client is added without a route/auth test.

### Risks

- Without a freeze, parallel fixes can keep moving the target.

## Phase 1. URL And Auth Foundation

### Goal

Make bot-platform reliably call existing Ayla endpoints.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 1.1 | Define `AYLA_BASE_URL` as host-only and add shared Ayla URL builder | bot-platform | P0 | S |
| 1.2 | Add `AYLA_INTERNAL_API_TOKEN` to bot-platform settings | bot-platform | P0 | XS |
| 1.3 | Fix recommendations path to `/api/v1/internal/me/catalog/recommendations/` | bot-platform | P0 | XS |
| 1.4 | Change recommendations auth to `AYLA_INTERNAL_API_TOKEN` | bot-platform | P0 | XS |
| 1.5 | Decide nutrition token naming: keep `NUTRITION_SERVICE_TOKEN` or migrate to bearer | both | P1 | M |
| 1.6 | Add production fail-fast/readiness checks for enabled Ayla integrations | both | P0 | M |
| 1.7 | Add contract tests for URL building and headers | bot-platform | P0 | M |

### Acceptance Criteria

- [ ] `AYLA_BASE_URL=https://dev.gobeauty.site` produces correct URLs for all Ayla clients.
- [ ] No client can accidentally build `.../api/v1/api/v1/...`.
- [ ] Recommendations call uses `/api/v1/internal/me/catalog/recommendations/`.
- [ ] Missing enabled Ayla secrets fail at boot or readiness, not in user flow.

### Tests

- [ ] Unit tests for shared URL builder.
- [ ] Header tests for recommendations, nutrition, payments.
- [ ] Readiness check for Ayla base URL and auth configuration.

### Dependencies

- None. This is the first implementation phase.

## Phase 2. Payment Contract

### Goal

Make payment create, retry, provider webhook, and bot-platform payment consumers speak one contract.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 2.1 | Decide certificate payment ownership: Ayla model/endpoint or disable bot skill | product + Ayla + bot | P0 | M |
| 2.2 | Fix bot payment client response parsing: `confirmation_url`, not `checkout_url` | bot-platform | P0 | XS |
| 2.3 | Fix payment create trailing slash and idempotency header naming | bot-platform + Ayla | P0 | S |
| 2.4 | Add explicit Ayla internal appointment payment create endpoint if bot needs it | Ayla | P0 | M |
| 2.5 | Emit ADR event names from Ayla: `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded` | Ayla | P0 | M |
| 2.6 | Add `payment.failed` emit for YooKassa `payment.canceled` | Ayla | P0 | S |
| 2.7 | Implement bot `AylaPaymentsClient.retry_payment()` against `/api/v1/payments/internal/{id}/retry/` | bot-platform | P1/P0 | M |
| 2.8 | Add cross-repo payment fixture tests | both | P0 | M |

### Acceptance Criteria

- [ ] A live-mode bot payment call either succeeds against Ayla or is disabled by feature flag.
- [ ] Certificate flow has a canonical Ayla owner or is not exposed.
- [ ] Ayla payment events are accepted by bot-platform eventbus.
- [ ] Failed payment triggers `handle_payment_failed()` and the payment-failed skill in a smoke test.
- [ ] Retry callback returns a fresh `confirmation_url`.

### Tests

- [ ] Ayla webhook tests for authorized/captured/failed/refunded emits.
- [ ] bot-platform event consumer tests with real Ayla event fixtures.
- [ ] Live-mode payment client contract test with mocked Ayla response shape.

### Dependencies

- Phase 1 URL/auth foundation.

## Phase 3. Event Delivery

### Goal

Ensure Ayla outbox delivers booking/payment events to bot-platform ingest, not only local handlers.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 3.1 | Decide whether current `OutboxEvent` table is local, cross-service, or both | Ayla | P0 | S |
| 3.2 | Add Ayla HTTP publisher to bot-platform `/api/v1/internal/events/ingest` | Ayla | P0 | L |
| 3.3 | Sign payloads with `X-Ayla-Event-Signature` and timestamp | Ayla | P0 | M |
| 3.4 | Split local notification processing from external delivery state | Ayla | P0 | L |
| 3.5 | Add retry/backoff/dead-letter fields or table | Ayla | P0 | M |
| 3.6 | Add replay command for stuck/dead events | Ayla | P1 | M |
| 3.7 | Add E2E event delivery smoke test | both | P0 | M |

### Acceptance Criteria

- [ ] Creating/changing an Ayla appointment results in bot-platform `IngestDedupe`.
- [ ] Local notification success cannot mark external delivery complete.
- [ ] Failed HTTP delivery stays retryable and visible to ops.
- [ ] Dead-letter events can be replayed.

### Tests

- [ ] Ayla publisher unit tests for HMAC over exact JSON bytes.
- [ ] bot-platform ingest tests using Ayla fixtures.
- [ ] E2E smoke: Ayla booking created -> bot-platform consumer handled.

### Dependencies

- Phase 1 URL/auth foundation.
- Phase 2 event naming decisions for payments.

## Phase 4. Booking Ownership Migration

### Goal

Make Ayla `Appointment` the only current booking source of truth.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 4.1 | Freeze new user-facing writes to bot-platform `BookingRequest` | bot-platform | P0 | S |
| 4.2 | Build `AylaBookingClient` for create/cancel/reschedule/list/detail | bot-platform | P0 | L |
| 4.3 | Change booking skill confirm/cancel/reschedule to call Ayla | bot-platform | P0 | L |
| 4.4 | Keep `PendingBookingAction` as UX draft only | bot-platform | P0 | M |
| 4.5 | Disable direct bot-platform YClients booking writes for Ayla-owned tenants | bot-platform | P0 | M |
| 4.6 | Use `RemoteBookingProxy` as current booking mirror | bot-platform | P0 | L |
| 4.7 | Add reconciliation job: Ayla active appointments vs bot mirror | both | P1 | M |

### Acceptance Criteria

- [ ] No customer-facing bot flow creates/cancels/reschedules without Ayla.
- [ ] A bot-created booking appears in Ayla mobile history immediately.
- [ ] An Ayla-created booking appears in bot-platform through event delivery.
- [ ] Direct YClients booking writes have one owner.

### Tests

- [ ] Booking skill tests assert Ayla API calls, not YClients calls.
- [ ] Contract tests for Ayla appointment create/cancel/reschedule.
- [ ] Reconciliation report test for known fixture drift.

### Dependencies

- Phase 1 URL/auth foundation.
- Phase 3 event delivery.

## Phase 5. API Spec Cleanup

### Goal

Make API documents match canonical implementation and documented deviations.

### Tasks

| ID | Task | Repo/Location | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 5.1 | Update main API spec with appointment action deviations | API docs | P1 | S |
| 5.2 | Add full appointment status enum including `awaiting_payment` and `in_progress` | API docs | P1 | XS |
| 5.3 | Add `/api/v1/home/` to main spec | API docs | P1 | XS |
| 5.4 | Separate REST payment statuses from internal payment states and event names | API docs | P1 | M |
| 5.5 | Add bot-facing internal API section: auth, paths, response wrappers | API docs | P1 | M |
| 5.6 | Add internal profile endpoint contract or remove it from event docs | API docs + Ayla/bot | P1/P0 | M |

### Acceptance Criteria

- [ ] A new developer can implement clients from docs without hitting known wrong routes.
- [ ] Docs state that current Ayla code plus deviations are canonical until OpenAPI is generated.
- [ ] Event contracts are not hidden inside REST API docs.

### Dependencies

- Phase 1 and Phase 2 decisions.

## Phase 6. AI Core And Memory Boundary

### Goal

Stop AI behavior and user memory from diverging between Ayla backend, bot-platform, and `ayla-ai-core`.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 6.1 | Align `ayla-ai-core` version or SHA across Ayla and bot-platform | both | P0/P1 | S |
| 6.2 | Add startup log/readiness check for loaded AI core version | both | P1 | S |
| 6.3 | Define profile vs AI memory ownership | docs + both | P1 | M |
| 6.4 | Decide whether Ayla `ai.Conversation` is active product surface or legacy | Ayla | P1 | M |
| 6.5 | Add deletion/export semantics for cross-service memory | both | P1 | L |

### Acceptance Criteria

- [ ] Both services report the same approved `ayla-ai-core` version.
- [ ] PII/profile lives in Ayla; AI conversation memory boundary is documented.
- [ ] No AI output can directly mutate booking/payment without deterministic validation.

### Dependencies

- None for version alignment.
- Identity/memory ownership decisions may depend on product scope.

## Phase 7. Hardening And Operations

### Goal

Make failures visible and recoverable.

### Tasks

| ID | Task | Repo | Priority | Estimate |
| --- | --- | --- | --- | --- |
| 7.1 | Add dashboard metrics: outbox pending age, retry count, dead count | Ayla | P1 | M |
| 7.2 | Add bot-platform ingest metrics: accepted, rejected, DLQ, handler failures | bot-platform | P1 | M |
| 7.3 | Add readiness checks for Ayla clients and event ingest secret | bot-platform | P1 | M |
| 7.4 | Add runbook for replaying Ayla events to bot-platform | docs + Ayla | P1 | S |
| 7.5 | Add contract test CI job using Ayla route/OpenAPI fixture | both | P1 | L |
| 7.6 | Add smoke checklist for staging before production deploy | docs | P1 | S |

### Acceptance Criteria

- [ ] On-call can tell whether integration is healthy without reading code.
- [ ] Stuck events have a documented replay path.
- [ ] CI catches URL/auth/response drift before deploy.

## Recommended Execution Order

1. Phase 0: freeze contracts.
2. Phase 1: URL/auth foundation.
3. Phase 2: payment contract.
4. Phase 3: event delivery.
5. Phase 4: booking ownership migration.
6. Phase 5: API spec cleanup in parallel after decisions are stable.
7. Phase 6 and 7 in parallel where they do not block P0 fixes.

## First Sprint Candidate

| Task | Why first |
| --- | --- |
| 1.1 Shared Ayla URL builder | Reduces repeated path drift immediately |
| 1.2 Add `AYLA_INTERNAL_API_TOKEN` setting | Unblocks internal clients |
| 1.3 Fix recommendations path | Small, high-confidence P0 |
| 1.4 Fix recommendations auth | Same code area as path |
| 2.2 Parse `confirmation_url` | Small, high-confidence payment fix |
| 2.3 Payment trailing slash/header decision | Prevents POST redirect/idempotency drift |
| 0.1 Contract matrix | Gives every follow-up a stable reference |

## Release Gates

### Gate A: Integration Foundation

- [ ] Ayla URL builder used by all bot-platform Ayla clients.
- [ ] Recommendations client passes path/auth contract tests.
- [ ] Enabled Ayla auth secrets fail fast or readiness fails clearly.

### Gate B: Payment Stability

- [ ] Payment create is either disabled for bot certificate flow or has a canonical Ayla endpoint.
- [ ] Ayla emits payment events bot-platform accepts.
- [ ] Failed payment triggers bot recovery smoke test.

### Gate C: Event Stability

- [ ] Ayla booking/payment event observed in bot-platform ingest.
- [ ] Failed delivery is retryable and visible.
- [ ] Replay runbook tested once in staging.

### Gate D: Booking Ownership

- [ ] Bot booking mutations call Ayla.
- [ ] Direct YClients writes from bot booking flow disabled for Ayla-owned tenants.
- [ ] Reconciliation job reports no active drift for pilot tenant.

## Open Decisions

1. Should certificate purchase exist in MVP, and if yes, which Ayla domain owns it?
2. Should nutrition migrate from `X-Service-Token` to `AYLA_INTERNAL_API_TOKEN` bearer?
3. Should `user.profile.updated` fetch use a new internal user endpoint or carry safe fields in the event?
4. Is `booking.confirmed` a local Ayla notification event only, or a cross-service event?
5. Which service owns YClients writes after booking migration: Ayla only, or tenant-specific configurable owner?

## References

- `docs/architecture/unified-system-architecture-audit.md`
- `docs/architecture/api-spec-contract-drift-audit.md`
- `docs/architecture/event-contract.md`
- `docs/architecture/jwt-contract.md`
