## Status update — 2026-06-01 (tech-lead reconciliation)

This section overlays current status onto the original audit below. The body of this document is unchanged; treat the statuses here as authoritative where they differ from the original "Status: Open" lines.

### P0 status table

| ID | Finding | Current status | Notes / PRs |
| --- | --- | --- | --- |
| P0-1 | Event-name drift | PARTIAL | Booking events flow Ayla→bot. **Payment vocabulary NOT yet aligned** (Block B). Do not enable per-topic external delivery for payment events until Block B aligns names, otherwise 422/DLQ. |
| P0-2 | Booking ownership split | DEFERRED | Deferred post-pilot per verdict A2 (Block D). Dual-source accepted LATENT with divergence monitoring + G9 import-linter contract. |
| P0-3 | Payment create contract | RESOLVED (decision) | Canonical create = `POST /payments/create {appointment_id, return_url}` (per API Spec v2.0). Bot's `amount_rub`/`kind=certificate` shape is incompatible; verdict B = bot does NOT create payments (retry-only). Vocab alignment tracked in Block B. |
| P0-4 | Outbox not delivered to bot | CLOSED (Ayla side) | Block C shipped — Ayla PRs #170 (OutboxEvent dual-delivery), #177 (HTTP publisher + retry/backoff), #181 (HMAC + timestamp), #178 (replay command), #182 (E2E smoke Ayla half). Gate C unlocked. Bot-side joint smoke half PENDING (Gamma). Per-topic `external_delivery_enabled` opt-in PENDING. |
| P0-5 | ai-core version drift | CLOSED | A9 — bot-platform PR #935 + Ayla PR #176, both pin ayla-ai-core @ `e73a1b4784c150493c300b316d7a62cd423c8377`. |
| P0-6 | Catalog/schedule source-of-truth split | UNCHANGED | Context unchanged; mirror strategy per ADR-0009; catalog ownership in Ayla. |
| P0-7 | YClients ownership | UPDATED | Per E0.4 audit: bot-side YClients webhook is FULL+HARDENED (tenant scoping, audit wrapper, event emit); the `BookingRequest`→`RemoteBookingProxy` shrink is in-flight (latent, tied to A2 / Block D). |

### New artifacts since this audit

- `docs/architecture/contract-matrix.md` — cross-system contract registry, draft v1 (PR #940).
- E0.1 / E0.1-followup / E0.4 / E0.5 / E0.6 legacy-migration coverage audits + maintainability docs committed (PR #940).

---

# Unified System Architecture Audit

## Status

Living document. Initial version: 2026-05-28.

This report is updated as new architecture risks are found across the unified Ayla system.

## Scope

This audit treats the following repositories as one product system:

| Repository | Role in target architecture |
| --- | --- |
| `ai-bot-platform-codex` | AI/channel runtime, MAX bot, mini app backend, conversations, memory, skills, event consumers, observability |
| `Ayla/djangoproject-codex` | Canonical backend for user identity, PII, booking, payments, catalog, reviews, nutrition, notifications |
| `ayla-ai-core` | Shared AI orchestration library: `AIConcierge`, prompts, tool schemas, provider adapters, anti-hallucination helpers |

## Priority Scale

| Priority | Meaning |
| --- | --- |
| P0 | Blocks stable integration or can break booking/payment/user-visible flows |
| P1 | High architecture risk; may cause drift, duplicated logic, or hard-to-debug behavior |
| P2 | Maintainability, SOLID, DRY, testability, or documentation issue |

## Executive Summary

The main risk is not formatting or code style. The main risk is contract drift between services.

The system documentation says that Ayla backend owns transactional domains such as booking, payments, catalog, and user identity. In code, bot-platform still owns or mutates parts of booking, YClients, payment-facing flows, conversation state, and catalog/schedule mirrors.

Because of this, the system can become unstable even when each repository works in isolation.

## P0 Findings

### P0-1. Event Contract Drift Between Ayla And Bot-Platform

**Status:** Open

**Why it matters:** Ayla can emit events that bot-platform does not accept or does not handle. This breaks cross-service side effects: reminders, memory updates, payment follow-ups, cache invalidation, analytics, and conversation context.

**Evidence:**

- Ayla `OutboxEvent.Topic` includes `booking.confirmed`, `booking.no_show`, `payment.confirmed`, `cache.invalidate_slots`, and `tenant.relationship.revoked`.
- bot-platform cross-service ingest allows only `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.completed`, `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`, `review.created`, `service.updated`, `master.schedule.updated`, and `user.profile.updated`.
- bot-platform registers handlers for `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`, not `payment.confirmed`.

**Impact:** Some Ayla events can become `422 unknown_event_name`, land in DLQ, or never trigger expected behavior.

**Recommended fix:**

1. Create one shared event matrix: `event_name`, `owner`, `publisher`, `consumer`, `version`, `payload schema`, `handler`, `test`.
2. Decide whether Ayla should emit `payment.captured` or bot-platform should accept `payment.confirmed`.
3. Remove or map non-contract events before production delivery.
4. Add contract tests on both sides.

#### Detailed Audit: P0 Event Contract

**Date:** 2026-05-28

**Simple conclusion:** services use similar words, but they do not speak the same contract. Some events have the same lifecycle meaning, but different names. Some events have the same name, but different payload fields. This is worse than having no integration, because it can fail only after real user actions.

| Flow | Ayla currently emits | bot-platform accepts | bot-platform handler | Payload compatible | Priority |
| --- | --- | --- | --- | --- | --- |
| Booking created | `booking.created` | Yes | Yes | No: Ayla sends `booking_id`; bot expects `appointment_id` and uses `status` | P0 |
| Booking cancelled | `booking.cancelled` | Yes | Yes | No: Ayla sends `booking_id`; bot expects `appointment_id`; actor/user semantics are unclear | P0 |
| Booking rescheduled | `booking.rescheduled` | Yes | Yes | No: Ayla sends `booking_id` and `start_at`; bot expects `appointment_id` and `new_start_at` | P0 |
| Booking completed | `booking.completed` | Yes | Yes | No: Ayla sends `booking_id`; bot expects `appointment_id` | P0 |
| Booking confirmed | `booking.confirmed` | No | No | Not applicable | P0 |
| Booking no-show | `booking.no_show` | No | No | Not applicable | P1 |
| Payment authorized/hold | Ayla emits `booking.confirmed` on `waiting_for_capture` | bot expects `payment.authorized` | Yes | No: lifecycle vocabulary is different | P0 |
| Payment captured/succeeded | Ayla emits `payment.confirmed` | bot expects `payment.captured` | Yes | No: event name is different | P0 |
| Payment failed | No matching Ayla emitter found | `payment.failed` | Yes | Not applicable | P0 |
| Payment refunded | `payment.refunded` | Yes | Yes | Mostly yes: `payment_id`, `appointment_id`, `amount` match; `currency` defaults in bot | P0 because delivery is still unclear |
| Review created | No matching Ayla emitter found | `review.created` | Yes | Not applicable | P1 |
| Service updated | No matching Ayla emitter found | `service.updated` | Yes | Not applicable | P1 |
| Master schedule updated | No matching Ayla emitter found | `master.schedule.updated` | Yes | Not applicable | P1 |
| User profile updated | No ADR-style Ayla emitter found | `user.profile.updated` | Yes | Nutrition has separate `profile_updated`, but not the same envelope | P1 |
| Nutrition events | `profile_updated`, `water_logged`, `milestone_reached`, `pattern_detected`, `recognition_completed` | Not in eventbus contract | No eventbus handler found | Separate webhook shape: `event_type`, `external_user_id`, `payload` | P1 |

**Evidence from Ayla backend:**

- `appointments.models.OutboxEvent.Topic` contains local topics that bot-platform does not allow: `booking.confirmed`, `booking.no_show`, `payment.confirmed`, `cache.invalidate_slots`, `tenant.relationship.revoked`.
- `create_booking_service.py` emits `booking.created` with `booking_id`, not `appointment_id`.
- `cancel_reschedule_service.py` emits `booking.cancelled` and `booking.rescheduled` with `booking_id`.
- `appointments.views.py` emits `booking.completed` and `booking.no_show` with `booking_id`.
- `payments.views.py` maps `payment.waiting_for_capture` to `booking.confirmed`.
- `payments.views.py` maps `payment.succeeded` to `payment.confirmed`.
- `payments.views.py` emits `payment.refunded` with the closest shape to bot-platform.
- Search in Ayla code found ADR mentions for `payment.authorized`, `payment.captured`, `payment.failed`, `review.created`, `service.updated`, `master.schedule.updated`, `user.profile.updated`, but no matching production emitters in the inspected paths.

**Evidence from bot-platform:**

- `apps.eventbus.ingest_envelope.ALLOWED_EVENT_NAMES` accepts the ADR vocabulary: `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.completed`, `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`, `review.created`, `service.updated`, `master.schedule.updated`, `user.profile.updated`.
- `apps.eventbus.consumers.booking` registers only `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.completed`.
- `apps.eventbus.consumers.payment` registers only `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`.
- Booking consumers read `data["appointment_id"]`; current Ayla booking events provide `data["booking_id"]`.
- `booking.created` consumer also uses `data["status"]`; current Ayla `booking.created` payload does not provide it.

**Why this blocks stable work:**

1. If Ayla sends `booking.created` to bot-platform today, ingest can accept the event name, but the handler can fail on missing `appointment_id`.
2. If Ayla sends `payment.confirmed`, bot-platform rejects it as unknown, because bot-platform waits for `payment.captured`.
3. If payment fails in Ayla, bot-platform has a handler for `payment.failed`, but no matching Ayla event was found in the inspected production code.
4. Notification, reminders, memory, cache invalidation, and conversation follow-ups can silently diverge from the real booking/payment state.
5. Local Ayla outbox dispatch can mark events as processed locally without proving that bot-platform received and handled them.

**Recommended fix order:**

1. Freeze one event vocabulary before changing business logic.
2. Prefer ADR vocabulary for cross-service events: `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`.
3. Decide whether `booking.confirmed` is a local Ayla notification event only, or add it explicitly to the cross-service contract.
4. Normalize booking payloads to use one canonical id field. Recommended: `appointment_id`, because bot-platform `RemoteBookingProxy` already uses this language.
5. Add `status` and `source` to `booking.created`, or make bot-platform defaults explicit and tested.
6. Add a delivery adapter from Ayla outbox to bot-platform ingest. `processed_at` should mean "delivered to intended external consumer" for cross-service events.
7. Add contract tests with real Ayla payload fixtures executed against bot-platform ingest/consumers.

**Do not mark this fixed until:**

- every P0 event has one name, one version, one schema, and one owner;
- bot-platform can ingest actual Ayla payload fixtures without handler exceptions;
- Ayla has tests proving it emits ADR-compatible payment lifecycle events;
- a failed delivery to bot-platform cannot be confused with a successfully processed local outbox event.

### P0-2. Booking Ownership Is Split

**Status:** Open

**Why it matters:** Booking must have one source of truth. Currently Ayla has canonical `Appointment`, while bot-platform still creates and mutates `BookingRequest` and can push to YClients.

**Evidence:**

- Ayla backend has `Appointment`, booking state transitions, idempotency keys, and outbox events.
- bot-platform has `BookingRequest` and `BookingReminder`.
- bot-platform `apps/booking/services/create.py` creates `BookingRequest`.
- bot-platform `apps/skills/booking/tools.py` calls YClients directly for create/cancel/reschedule flows.

**Impact:** A booking can exist or change in one service while the other service has stale or conflicting state. This is dangerous for customer trust, reminders, payments, and provider schedules.

**Recommended fix:**

1. Declare Ayla `Appointment` as the only canonical booking object.
2. Reduce bot-platform booking models to read-only mirror, reminders, or conversation context.
3. Replace direct YClients writes in bot-platform with Ayla REST calls.
4. Introduce `RemoteBookingProxy` or equivalent adapter in bot-platform.

#### Detailed Audit: P0 Booking Ownership

**Date:** 2026-05-28

**Simple conclusion:** the codebase currently has two booking systems. Ayla has `Appointment` as a transactional booking engine. bot-platform has `BookingRequest` as a full local booking lifecycle and `RemoteBookingProxy` as a newer Ayla mirror. The target architecture says bot-platform should mirror Ayla, but active bot-platform code can still create, cancel, reschedule, reassign, complete, and push bookings to YClients without going through Ayla.

| Area | Ayla backend | bot-platform | Risk |
| --- | --- | --- | --- |
| Canonical model | `appointments.Appointment` | `booking.BookingRequest` and `booking.RemoteBookingProxy` | Two local truths for one user booking |
| Create booking | `CreateBookingService` creates `Appointment` + `Payment` | `booking.services.create.create_customer_booking` creates `BookingRequest`; `skills.booking.execute_confirm` calls YClients then creates `BookingRequest` | Double-booking or missing payment state |
| Cancel booking | `CancelBookingService` changes `Appointment.status` | `skills.booking.execute_cancel` cancels YClients and updates `BookingRequest`; `booking.services.transitions.commit_cancel` updates `BookingRequest` | Ayla can still show active booking after bot cancel |
| Reschedule booking | `RescheduleBookingService` mutates same `Appointment` time window | `skills.booking.execute_reschedule` cancels old YClients record, creates new YClients record, writes new `BookingRequest`; `booking.services.reschedule` creates new local row | Different reschedule semantics |
| Completion | Ayla specialist endpoint marks `Appointment.completed` | bot-platform has `completed_at` and periodic completion logic for `BookingRequest` | Review/aftercare/reminders can diverge |
| Provider/admin cascade | Ayla has specialist departure cascade over `Appointment` | bot-platform admin deactivation mutates/cancels `BookingRequest` rows | Master deactivation can affect only one backend |
| YClients ownership | Target docs say Ayla should be sync layer | bot-platform has YClients webhook, direct client calls, and async push task | Integration-specific logic leaks into AI runtime |
| Mirror model | Ayla outbox should feed bot-platform | `RemoteBookingProxy` exists and is designed as Ayla mirror | Good target shape, but not yet the only path |
| Bot-to-Ayla mutation API | Ayla exposes `/api/v1/appointments/` create/cancel/reschedule | No bot-platform booking mutation client to Ayla was found in inspected paths | Bot cannot rely on Ayla as SoR yet |

**Evidence from Ayla backend:**

- `appointments.models.Appointment` stores the canonical booking state: `pending`, `awaiting_payment`, `confirmed`, `in_progress`, `completed`, `cancelled`, `no_show`.
- `CreateBookingService` creates `Appointment` and `Payment` in one transaction, checks slot conflicts, stamps tenant, writes idempotency key, and emits `booking.created`.
- `CancelBookingService` locks the `Appointment`, transitions to `cancelled`, records `cancelled_by`, and emits `booking.cancelled`.
- `RescheduleBookingService` locks the `Appointment`, checks conflicts, updates the same row's `start_datetime`/`end_datetime`, and emits `booking.rescheduled`.
- `AppointmentViewSet` exposes create, cancel, complete, no-show, and reschedule endpoints around those services.

**Evidence from bot-platform:**

- `booking.models.BookingRequest` is not only an audit mirror. It has lifecycle states: `confirmed`, `cancel_requested`, `reschedule_requested`, `cancelled`, `rescheduled`.
- `booking.models.RemoteBookingProxy` correctly describes the intended target: local mirror of Ayla `Appointment`, no PII, used for AI grounding and reminders.
- `booking.services.create.create_customer_booking` creates a local `BookingRequest`, emits `booking.created`, and then enqueues `push_booking_to_yclients`.
- `booking.services.create.create_customer_booking` explicitly says "Platform = source of truth; YC is eventual mirror", which conflicts with the unified Ayla architecture where Ayla backend owns booking.
- `skills.booking.execute_confirm` directly calls `client.create_record(...)` against YClients and then writes `BookingRequest`.
- `skills.booking.execute_cancel` directly calls `client.cancel_record(...)` and then updates `BookingRequest.status = cancelled`.
- `skills.booking.execute_reschedule` implements reschedule as YClients cancel + YClients create + local `BookingRequest` updates.
- `integrations.yclients.webhooks` receives YClients admin events and creates `BookingRequest` + reminders directly.
- `integrations.yclients.tasks.push_booking_to_yclients` pushes bot-platform `BookingRequest` rows to YClients asynchronously.
- `admin_api.services.master_deactivation.execute_deactivation` reassigns or cancels local `BookingRequest` rows during master deactivation.
- Search in bot-platform did not find a booking mutation client that calls Ayla `/api/v1/appointments/` create/cancel/reschedule endpoints.

**Why this blocks stable work:**

1. A customer can book through Ayla mobile and bot-platform may not know if events do not arrive.
2. A customer can book through bot-platform and Ayla may not know at all, so payment, mobile app history, specialist app, and Ayla notifications can be wrong.
3. A bot cancel can cancel YClients and local `BookingRequest`, while Ayla `Appointment` remains active.
4. A mobile cancel can cancel Ayla `Appointment`, while bot-platform `BookingRequest` or YClients-derived rows remain active.
5. Reschedule semantics differ: Ayla updates one appointment; bot-platform often cancels old + creates new. Analytics and billing chains can disagree.
6. YClients webhook arriving in bot-platform can create rows outside Ayla, bypassing Ayla payment/tenant/state-machine rules.
7. `RemoteBookingProxy` is the correct destination model, but it coexists with active write paths instead of replacing them.

**Recommended target shape:**

| Concern | Owner | Allowed bot-platform behavior |
| --- | --- | --- |
| Booking creation | Ayla `Appointment` service | Call Ayla API; never write canonical booking locally |
| Booking cancellation | Ayla `CancelBookingService` | Call Ayla API; mirror result via events |
| Booking reschedule | Ayla `RescheduleBookingService` | Call Ayla API; mirror result via events |
| Booking payment coupling | Ayla | Read-only display or retry link through explicit payment API |
| YClients sync | Ayla sync layer | No direct bot-platform YClients booking writes |
| AI memory/reminders | bot-platform | Use `RemoteBookingProxy` and Ayla events |
| Conversation preview / confirmation cards | bot-platform | Keep `PendingBookingAction`, but execution calls Ayla |

**Recommended fix order:**

1. Freeze new writes to `BookingRequest` for user-facing booking creation, cancel, and reschedule.
2. Build a bot-platform `AylaBookingClient` with explicit methods: `create_appointment`, `cancel_appointment`, `reschedule_appointment`, `get_my_bookings`.
3. Change `execute_confirm`, `execute_cancel`, and `execute_reschedule` to call Ayla instead of YClients/local `BookingRequest`.
4. Keep `PendingBookingAction` for UX confirmation, but make it store Ayla request payloads, not YClients record operations.
5. Move YClients webhook and push ownership to Ayla, or disable bot-platform booking mutations for tenants whose booking SoR is Ayla.
6. Convert `BookingRequest` to one of two clear roles: legacy analytics table or deprecated read-only history. Do not use it as current booking state.
7. Make `RemoteBookingProxy` the only bot-platform current-booking model.
8. Add reconciliation job: Ayla active `Appointment` count vs bot-platform `RemoteBookingProxy` count per tenant/user/date.

**Do not mark this fixed until:**

- no customer-facing bot flow creates, cancels, or reschedules a booking without Ayla;
- no bot-platform code writes current booking state to `BookingRequest` as the source of truth;
- YClients booking writes have one owner;
- a bot-created booking appears in Ayla mobile history and payment flow immediately;
- an Ayla-created booking appears in bot-platform `RemoteBookingProxy` via event delivery;
- tests cover bot confirm/cancel/reschedule as Ayla API calls, not direct YClients writes.

### P0-3. Payment Create Contract Appears Incompatible

**Status:** Open

**Why it matters:** Payment create can fail in live mode because bot-platform and Ayla expect different authentication for the same endpoint.

**Evidence:**

- bot-platform `AylaPaymentsClient.create_payment()` calls `POST /api/v1/payments/create` with `Authorization: Bearer AYLA_INTERNAL_API_TOKEN`.
- Ayla `PaymentCreateView` requires normal authenticated client permissions: `IsAuthenticated` and `IsClient`.
- Ayla internal bearer endpoint exists for retry, not for create.

**Impact:** New payments from bot-platform can work in test mode but fail in live mode with `401` or `403`.

**Recommended fix:**

1. Decide whether bot-platform is allowed to create payments.
2. If yes, add an explicit Ayla internal payment-create endpoint with bearer + external user verification.
3. If no, remove bot-platform payment-create calls and route users through Ayla-owned client flow.
4. Add live-mode contract test.

#### Detailed Audit: P0 Payment Flow

**Date:** 2026-05-28

**Simple conclusion:** Ayla is the right owner for YooKassa and canonical `Payment`, but the actual integration is not stable yet. Ayla implements appointment payments. bot-platform has a client that calls the same endpoint as if it were a generic/certificate payment API. Event names also do not match: Ayla emits `payment.confirmed`, while bot-platform waits for `payment.captured`; Ayla marks failed payments, while bot-platform waits for `payment.failed` to trigger recovery logic.

| Flow | Ayla backend behavior | bot-platform expectation | Risk |
| --- | --- | --- | --- |
| Appointment payment create | `POST /api/v1/payments/create/` requires authenticated client and body `appointment_id`, `return_url` | `AylaPaymentsClient.create_payment()` sends internal bearer token and body `amount_rub`, `description`, `kind`, `recipient_name`, `buyer_email` | P0: live bot call can fail with `401/403/400` |
| Certificate payment create | `Payment` requires FK to `appointments.Appointment`; no inspected certificate/order payment model was found | `buy_certificate` calls Ayla payments with `kind="certificate"` | P0: product flow has no compatible canonical object |
| Payment hold / authorization | YooKassa `payment.waiting_for_capture` sets `Payment.AUTHORIZED`, confirms appointment, emits `booking.confirmed` | Payment consumer handles `payment.authorized` and stores `Conversation.pending_payment_id` | P0: bot never records pending authorized payment |
| Payment capture / success | YooKassa `payment.succeeded` sets `Payment.PAID`, emits `payment.confirmed` | Payment consumer handles `payment.captured`, clears pending payment, resets failure counter, emits loyalty event | P0: successful payment can be invisible to bot-platform |
| Payment failed / canceled | YooKassa `payment.canceled` sets `Payment.FAILED` and may cancel appointment; no matching outbox emit found in inspected branch | Payment consumer handles `payment.failed`, increments failure counter, calls `payment_failed` skill on threshold | P0: failed-payment recovery and customer DM may never start |
| Refund | Client refund endpoint and webhook emit `payment.refunded` | Payment consumer handles `payment.refunded` | Mostly aligned, but still blocked by outbox delivery gap |
| Retry | Ayla has mobile retry and internal retry endpoint `POST /api/v1/payments/internal/{id}/retry/` | `payment_failed` skill still has a TODO stub for `retry_payment()` client method | P1/P0: retry UX is designed, but not fully wired from bot callback |
| Legacy YooKassa in bot-platform | `/api/v1/yookassa/` is mounted to retired `410 Gone` app | New payments should go through Ayla | Good direction; keep monitoring for accidental legacy traffic |

**Evidence from Ayla backend:**

- `payments.models.Payment` explicitly says payments are Ayla-canonical and the `payments` app owns the YooKassa lifecycle.
- `Payment` is tied to `appointments.Appointment` through a required FK, so the inspected model cannot represent a standalone certificate payment.
- `PaymentCreateView` requires `IsAuthenticated` and `IsClient`.
- `PaymentCreateSerializer` accepts only `appointment_id` and optional `return_url`.
- `PaymentCreateView` fetches an appointment by `id=appointment_id` and `client=request.user`, then creates a YooKassa payment for `appointment.price`.
- `PaymentCreateView` reads idempotency from `X-Idempotency-Key`, while bot-platform sends `Idempotence-Key`.
- `PaymentWebhookView` maps `payment.waiting_for_capture` to `Payment.AUTHORIZED`, changes appointment to `confirmed`, and emits `booking.confirmed`.
- `PaymentWebhookView` maps `payment.succeeded` to `Payment.PAID` and emits `payment.confirmed`.
- `PaymentWebhookView` maps `payment.canceled` to `Payment.FAILED`, but no `payment.failed` outbox emit was found in that branch.
- `InternalPaymentRetryView` is correctly shaped for bot service auth: bearer token, `X-External-User-ID`, and body `client_id` cross-check.
- `PaymentRefundView` emits `payment.refunded` both for direct refund and refund webhook path.

**Evidence from bot-platform:**

- `apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment()` calls `/api/v1/payments/create` with `Authorization: Bearer <AYLA_INTERNAL_API_TOKEN>`.
- The same client sends `amount_rub`, `description`, `kind`, `recipient_name`, and `buyer_email`, not `appointment_id`.
- The client expects response field `checkout_url`, while Ayla returns `confirmation_url`.
- `apps.skills.booking.tools.buy_certificate()` uses this client for `kind="certificate"`.
- `apps.eventbus.consumers.payment` registers `payment.authorized`, `payment.captured`, `payment.failed`, and `payment.refunded`.
- `handle_payment_failed()` is the path that increments payment failure counters and invokes `apps.skills.payment_failed.skill.on_payment_failed_event`.
- `apps.skills.payment_failed.skill` still documents retry client integration as pending TODO: `retry_payment(payment_id, ayla_user_id, idempotency_key)`.
- bot-platform retired the old YooKassa webhook path into a `410 Gone` app, which supports the target decision that Ayla owns provider webhooks.

**Why this blocks stable work:**

1. Bot certificate checkout can pass tests in `AYLA_PAYMENTS_TEST_MODE=True`, then fail in production because the live Ayla endpoint expects a different auth model and body.
2. A successful Ayla hold can confirm an appointment, but bot-platform will not receive `payment.authorized`, so conversation state and pending payment context drift.
3. A successful payment can emit `payment.confirmed`, which bot-platform does not handle; loyalty and failure-counter reset logic stay stale.
4. A failed YooKassa payment can update Ayla DB, but bot-platform may never receive `payment.failed`; the payment recovery skill and customer DM do not run.
5. Certificate payments currently look designed in bot-platform, but the inspected Ayla payment model is appointment-bound. That is a product-level gap, not just a serializer typo.
6. Refund events are closest to compatible, but outbox delivery is still not proven, so bot-platform may not observe them.

**Recommended target shape:**

| Concern | Owner | Target contract |
| --- | --- | --- |
| Appointment payment create | Ayla | Client JWT endpoint with `appointment_id`, or explicit internal-on-behalf endpoint with `appointment_id` + resolved user |
| Certificate/package payment create | Ayla product/order domain, if the feature is real | Separate model and endpoint, not appointment `PaymentCreateView` |
| YooKassa webhook | Ayla | Ayla updates `Payment`, emits ADR-compatible cross-service events |
| Payment retry | Ayla | bot-platform calls internal retry endpoint through a real `retry_payment()` client |
| Payment conversation recovery | bot-platform | consume `payment.failed`, update conversation, send recovery DM |
| Loyalty and bot memory | bot-platform | consume `payment.captured` and `payment.refunded` only from Ayla events |

**Recommended fix order:**

1. Stop treating `/api/v1/payments/create/` as both appointment checkout and certificate checkout.
2. Decide whether certificate purchase is in scope now. If not, disable/hide `buy_certificate` in live mode. If yes, create an Ayla-owned certificate/order payment model and endpoint.
3. Add an explicit internal appointment-payment create endpoint only if bot-platform really needs to create appointment payments on behalf of a user.
4. Align response names: either Ayla returns `checkout_url`, or bot-platform reads `confirmation_url`. Use one public contract.
5. Align idempotency header naming: use one of `X-Idempotency-Key` or `Idempotence-Key` and test it cross-service.
6. Change Ayla payment webhook emits to ADR vocabulary: `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`.
7. Add a `payment.failed` emit for `payment.canceled`, with closed enum `reason` values so bot-platform does not store provider free text.
8. Implement bot-platform `AylaPaymentsClient.retry_payment()` for `/api/v1/payments/internal/{id}/retry/`.
9. Add cross-repo contract fixtures for create, authorized, captured, failed, refunded, and retry.

**Do not mark this fixed until:**

- bot-platform live-mode payment create either succeeds against Ayla with the exact auth/body contract, or is explicitly disabled;
- certificate payments have a canonical Ayla owner/model, or the bot skill is removed from production paths;
- Ayla emits `payment.authorized`, `payment.captured`, `payment.failed`, and `payment.refunded` with schemas bot-platform can ingest;
- failed payment in Ayla triggers bot-platform `handle_payment_failed()` and the `payment_failed` skill in a smoke test;
- retry callback in bot-platform calls the real Ayla internal retry endpoint;
- no bot-platform code creates or mutates YooKassa lifecycle state directly.

### P0-4. Ayla Booking/Payment Outbox Is Not Clearly Delivered To Bot-Platform

**Status:** Open

**Why it matters:** The architecture depends on Ayla publishing events to bot-platform. Current code shows local outbox dispatch and handlers, but booking/payment HTTP delivery to bot-platform ingest is not clearly wired.

**Evidence:**

- Ayla `CELERY_BEAT_SCHEDULE` runs `appointments.tasks.dispatch_outbox_events`.
- `appointments.tasks` uses local handler registry with log stubs and notification handlers.
- A separate `appointments/infrastructure/outbox_worker.py` states that its worker is not scheduled for MVP.
- Nutrition has a separate webhook delivery mechanism; booking/payment delivery to bot-platform needs confirmation.

**Impact:** Events may be marked processed locally without ever reaching bot-platform, or may sit in outbox with no cross-service effect.

**Recommended fix:**

1. Confirm desired delivery pattern: HTTP push from Ayla to bot-platform, pull by bot-platform, or broker.
2. Implement one production path with HMAC, retry, DLQ, and lag metrics.
3. Ensure `processed_at` means "delivered/handled by intended consumer", not merely "logged locally".
4. Add E2E test: create appointment in Ayla -> bot-platform receives and handles event.

#### Detailed Audit: P0 Outbox Delivery Path

**Date:** 2026-05-28

**Simple conclusion:** bot-platform has an HTTP ingest endpoint, but Ayla's scheduled booking/payment outbox dispatcher does not call it. In the current inspected path, Ayla marks `OutboxEvent.processed_at` after local notification/log handlers run. That means the row can look successfully processed in Ayla while bot-platform never received the event.

| Step | Expected by architecture | Current code behavior | Risk |
| --- | --- | --- | --- |
| Event write | Domain change writes `OutboxEvent` in the same DB transaction | Ayla emit helpers create `OutboxEvent` rows with ADR-style envelope | Good foundation |
| Scheduler | Outbox dispatcher runs periodically | `CELERY_BEAT_SCHEDULE["dispatch-outbox-events"]` runs `appointments.tasks.dispatch_outbox_events` every 10 seconds | Active |
| Delivery target | Dispatcher ships event to bot-platform `/api/v1/internal/events/ingest` | Scheduled dispatcher routes to in-process `EVENT_HANDLERS` | P0 gap |
| Handler registry | Cross-service events should go to external consumer | Defaults are log stubs; notifications app replaces many with local notification handlers | P0: local side effects can hide missing external delivery |
| Success marker | `processed_at` should mean external consumer accepted/handled the event | `processed_at` is set after local handler returns | P0 semantic mismatch |
| Retry policy | Retry HTTP 5xx, network errors, and timeouts with backoff | Ayla increments `error_count` only when local handler raises; no HTTP retry path found | P0 gap |
| Dead-letter | After max attempts, mark event dead and alert | No `dead` field exists; rows with `error_count >= 5` are skipped and remain pending | P1/P0 operational gap |
| Cross-service HMAC | Ayla signs raw JSON body with `X-Ayla-Event-Signature` and timestamp | No Ayla booking/payment publisher using `X-Ayla-Event-Signature` found | P0 gap |
| Ordering | Contract says FIFO per `correlation_id` partition | Current dispatcher orders by `created_at`; no per-correlation partitioning | P1 unless strict ordering is required |
| Replay | Runbook references replay tooling | Search found only docs/runbook references, no Ayla `replay_outbox_to_consumer` command in inspected code | P1 |

**Evidence from Ayla backend:**

- `appointments.models.OutboxEvent` has `processed_at`, `error_count`, and `last_error`, but no explicit `dead`, `next_retry_at`, `delivered_at`, `consumer`, or `destination`.
- `djangoProject.settings.base.CELERY_BEAT_SCHEDULE` schedules `appointments.tasks.dispatch_outbox_events` every 10 seconds.
- `appointments.tasks.dispatch_outbox_events` loads rows where `processed_at IS NULL`, calls a local handler from `EVENT_HANDLERS`, then sets `processed_at`.
- `appointments.tasks.EVENT_HANDLERS` starts with log-only handlers for all appointment/payment topics.
- `appointments.tasks._register_notification_handlers()` imports `notifications.outbox_handlers.BOOKING_HANDLERS` and replaces several handlers with local notification handlers.
- `notifications.outbox_handlers` explicitly catches missing templates and missing appointments so the row can still be marked processed.
- `appointments.infrastructure.outbox_worker.process_outbox_events` is a second outbox processor, but its header says it is not scheduled for MVP; it also uses local stub handlers.
- Search in Ayla did not find a booking/payment HTTP publisher using `X-Ayla-Event-Signature`, `EVENT_INGEST_HMAC_SECRET`, or `/api/v1/internal/events/ingest`.

**Evidence from bot-platform:**

- `config.urls` mounts `/api/v1/internal/events/`.
- `apps.eventbus.urls` exposes `/api/v1/internal/events/ingest`.
- `apps.eventbus.views.InternalEventsIngestView` verifies HMAC and timestamp, parses the envelope, dispatches to registered handlers, and maps outcomes to HTTP status codes.
- `apps.eventbus.ingest_security` expects `X-Ayla-Event-Signature: sha256=<hex>` and `X-Ayla-Event-Timestamp: <unix_ms>`.
- `apps.eventbus.ingest_dispatcher` writes `IngestDedupe` and runs the handler in one DB transaction; duplicate delivery returns success without rerunning the handler.
- `apps.eventbus.ingest_dispatcher` writes `IngestDLQ` for unknown event names/versions and tracks handler failures.

**Why this blocks stable work:**

1. Ayla can show a green outbox while bot-platform state is stale.
2. Reminders, AI memory, payment follow-ups, and conversation context can miss real booking/payment changes.
3. A local notification success can permanently hide cross-service delivery failure.
4. Operators cannot distinguish "sent to bot-platform" from "handled locally" using `processed_at`.
5. Contract tests can pass inside each repo while the integration is broken between repos.

**Recommended target shape:**

Separate local side effects from cross-service event delivery.

Recommended model:

| Concern | Owner | State marker |
| --- | --- | --- |
| Local Ayla notifications | Ayla notification dispatcher | `notification` rows / notification delivery state |
| Cross-service delivery to bot-platform | Ayla event publisher | `published_at`, `attempt_count`, `next_retry_at`, `dead_at`, `last_error` |
| bot-platform ingestion | bot-platform eventbus | `IngestDedupe`, `IngestDLQ`, audit rows |

**Recommended fix order:**

1. Decide whether the existing `appointments_outboxevent` table is for local events, cross-service events, or both. Right now it is doing both conceptually but only local processing in code.
2. Add an explicit Ayla publisher for cross-service topics that posts the full envelope to bot-platform ingest.
3. Sign the exact JSON bytes with `EVENT_INGEST_HMAC_SECRET` and send `X-Ayla-Event-Signature` plus `X-Ayla-Event-Timestamp`.
4. Treat bot-platform HTTP `200` as successful delivery; retry on network errors, timeout, `429`, and `5xx`; do not retry permanent `4xx` after writing an operator-visible error.
5. Stop using the same `processed_at` flag for both local notification handling and external event delivery, or rename/split the table fields so the meaning is not ambiguous.
6. Add a real dead-letter marker (`dead_at` or `status=dead`) and a replay command.
7. Add an E2E test with an actual Ayla envelope fixture posted to bot-platform ingest.

**Do not mark this fixed until:**

- an Ayla booking/payment event can be observed in bot-platform `IngestDedupe`;
- a failed bot-platform HTTP response leaves the Ayla event retryable and visible to ops;
- a local notification handler cannot mark cross-service delivery as complete;
- there is a documented replay procedure for stuck/dead events;
- monitoring reports pending depth, oldest pending age, retry count, and dead-letter count.

### P0-5. Shared AI Core Version Drift

**Status:** Open

**Why it matters:** Both backends depend on `ayla-ai-core`, but they use different versions. AI behavior can diverge between mobile/backend and bot-platform.

**Evidence:**

- Ayla backend pins `ayla-ai-core` to `v0.6.0`.
- bot-platform pins `ayla-ai-core[django]` to a SHA aligned with later `v0.8.1` work.
- local `ayla-ai-core` reports package version `0.8.1`, while its README still references `0.1.0`.

**Impact:** Prompt rendering, tool dispatching, provider behavior, history truncation, or safety behavior can differ per channel.

**Recommended fix:**

1. Choose one approved `ayla-ai-core` version or SHA for both consumers.
2. Update both dependency pins in one coordinated change.
3. Add startup/version smoke checks in both services.
4. Update stale `ayla-ai-core` README.

### P0-6. Catalog / Schedule / Slot Source Of Truth Is Split

**Status:** Open

**Why it matters:** Catalog, master schedule, and slot availability are booking-critical. If the user sees a stale service, stale duration, or stale free slot, the system can create failed bookings, double-booking conflicts, wrong prices, or broken payment amounts.

**Expected ownership:** Ayla backend should own the canonical catalog, specialists, working hours, time-off, slot calculation, and appointment occupancy. bot-platform should keep only a read-only mirror/cache for AI, RAG, and conversational UX.

**Current behavior found:**

- bot-platform still has full catalog mirror models in `apps.catalog.models`, including `CatalogService`, `CatalogMaster`, and `MasterService`.
- bot-platform catalog sync still pulls from `mysite` via `MYSITE_CATALOG_BASE_URL` and `MYSITE_CATALOG_SERVICE_TOKEN`, not from Ayla.
- bot-platform already has consumers for Ayla `service.updated` and `master.schedule.updated`, but these consumers only bump local `cache_version`; they do not fetch/create canonical rows.
- Ayla `OutboxEvent.Topic` and `EVENT_VERSIONS` do not include `service.updated` or `master.schedule.updated`.
- Ayla service CRUD (`services.views.ServiceViewSet`) updates canonical services but no inspected emit path publishes `service.updated`.
- Ayla schedule APIs (`users.schedule_api`) update `SpecialistWorkingHours` and `SpecialistTimeOff`, then invalidate Ayla local slot cache, but no inspected emit path publishes `master.schedule.updated`.
- bot-platform mini app computes public slots locally from `apps.scheduling` models, `MasterService`, and `BookingRequest`, not by asking Ayla.
- bot-platform booking creation re-runs the same local slot resolver and writes local `BookingRequest`, then pushes to YClients best-effort.

**Concrete code evidence:**

- `apps/catalog/services/http_client.py:162` reads `MYSITE_CATALOG_BASE_URL`; `apps/catalog/services/http_client.py:164` reads `MYSITE_CATALOG_SERVICE_TOKEN`.
- `apps/catalog/models.py:64` defines legacy integer `external_id`; `apps/catalog/models.py:125` adds nullable `ayla_service_id`.
- `apps/catalog/models.py:216` and `apps/catalog/models.py:234` define `CatalogMaster.ayla_user_id` twice.
- `apps/catalog/models.py:210` stores `yclients_staff_id` as integer, while Ayla `SpecialistProfile.yclients_staff_id` is a string field.
- `apps/catalog/models.py:344` defines `MasterService`, a platform-side master-service mapping that can diverge from Ayla service ownership.
- `apps/miniapp_api/views.py:388` exposes local `/slots`; `apps/miniapp_api/views.py:487` computes slots locally with `compute_free_slots`.
- `apps/booking/services/create.py:224` reads local `CatalogService`; `apps/booking/services/create.py:255` checks local `MasterService`; `apps/booking/services/create.py:274` computes local free slots; `apps/booking/services/create.py:368` pushes to YClients best-effort after local booking creation.
- `appointments/infrastructure/outbox/envelope.py:59` lists registered event versions; catalog/schedule events are absent.
- `appointments/models.py:391` defines outbox topics; catalog/schedule events are absent.
- `users/schedule_api.py:257`, `users/schedule_api.py:306`, `users/schedule_api.py:404`, and `users/schedule_api.py:465` mutate schedule/time-off and invalidate local cache only.
- `services/views.py:61` exposes Ayla service CRUD, but no inspected outbox emit for catalog changes was found.

**Design problems in simple terms:**

1. There are multiple answers to "what services does this master offer?": Ayla `Service`, bot-platform `CatalogService`, bot-platform `MasterService`, and old `mysite` sync.
2. There are multiple answers to "when is this master available?": Ayla `SpecialistWorkingHours`/`SpecialistTimeOff`, bot-platform `WorkingHours`/`ScheduleException`/`TimeBlock`, and YClients-related data.
3. There are multiple answers to "is this slot free?": Ayla availability service and bot-platform local slot resolver.
4. bot-platform has event consumers for invalidation, but Ayla currently does not appear to emit the needed events.
5. The code says the mirror is read-only, but the bot-platform models and APIs still behave like an operational source of truth.

**Impact:**

- A master can update service duration or price in Ayla, while bot-platform still books using stale duration or stale price.
- A master can change working hours in Ayla, while bot-platform still shows old slots.
- A slot can be free in bot-platform but blocked in Ayla/YClients, causing booking failure after the user already chose it.
- A slot can be blocked in bot-platform but free in Ayla, causing lost bookings.
- Payment amount and booking duration can be calculated from stale catalog data.
- Cache version fields can look architecturally ready while no active cache reader or publisher makes them effective.

**Recommended target shape:**

| Concern | Source of truth | bot-platform role |
| --- | --- | --- |
| Service category and service details | Ayla | read-only mirror or live API reader |
| Master profile and service ownership | Ayla | read-only mirror for AI/search |
| Working hours and time off | Ayla | read-only cache/invalidation signal only |
| Slot calculation | Ayla availability API | call Ayla for customer-facing slot selection |
| Appointment occupancy | Ayla `Appointment` | mirror only if needed for conversation context |
| YClients sync | one chosen owner | no direct competing writes from both services |

**Recommended audit/fix order later:**

1. Decide whether bot-platform `/slots` is allowed to remain customer-facing. If not, route it to Ayla availability.
2. Add Ayla emitters for `service.updated` and `master.schedule.updated`, or remove bot consumers until the publisher exists.
3. Replace `mysite` catalog sync with Ayla snapshot/event sync, or explicitly document `mysite` as still canonical during a migration window.
4. Make bot-platform catalog mirrors use Ayla UUIDs as primary lookup keys; legacy integer `external_id` should not block Ayla-fed rows.
5. Move master-service assignment changes to Ayla only; bot-platform `MasterService` should be read-only or deprecated.
6. Add contract tests for service duration, price, master-service ownership, schedule change, time-off change, and slot occupancy.

**Do not mark this fixed until:**

- a service duration/price change in Ayla invalidates or updates bot-platform before any new booking uses stale data;
- a schedule/time-off change in Ayla invalidates bot-platform slot reads;
- customer-facing slot APIs have one canonical computation path;
- bot-platform cannot create a booking from a master-service mapping that does not exist in Ayla;
- the migration away from `mysite` catalog sync is either complete or explicitly bounded.

### P0-7. YClients Integration Ownership Is Reversed

**Status:** Open

**Why it matters:** YClients is an external booking system. If two internal services can read, write, mirror, and react to YClients independently, the product can lose the real booking state. This affects booking correctness, reminders, payments, analytics, and customer trust.

**Expected ownership from ADR/docs:** Ayla should be the booking/schedule sync layer. bot-platform should call Ayla REST or consume Ayla events. bot-platform should not write directly to YClients for Ayla-owned tenants.

**Current behavior found:**

- Ayla has the declarative field `SpecialistProfile.booking_source = ayla_local | yclients`, plus `yclients_company_id` and `yclients_staff_id`.
- Ayla docs say the YClients adapter is not built yet.
- Ayla code comments explicitly say this repo has no YClients integration yet.
- bot-platform has the active YClients HTTP client, webhook receiver, async push task, and booking skill code that calls YClients directly.
- bot-platform root URLs expose `/api/v1/yclients/webhook/`.
- bot-platform YClients webhook creates local `BookingRequest` and reminder rows directly.
- bot-platform booking skill reads YClients staff/slots and executes create/cancel/reschedule through YClients.
- bot-platform local booking service creates `BookingRequest` first, then enqueues a best-effort push to YClients.

**Concrete code evidence:**

- `users/models.py:169` says Ayla will fetch slots via YClients and create bookings in YClients when `booking_source='yclients'`.
- `users/models.py:175` says the Ayla repo has no YClients integration yet.
- `docs/architecture/booking-source-dual-mode.md:45` says the YClients adapter is not built in Phase 0.
- `payments/exceptions.py:16` says Ayla does not integrate with YClients.
- `users/masters_internal_api.py:63` exposes an internal batch lookup by YClients staff ids, but it only resolves staff ids to Ayla specialists.
- `apps/integrations/yclients/client.py:247` defines the bot-platform `YClientsAPI`.
- `apps/integrations/yclients/client.py:421`, `:450`, `:480`, and `:515` read services, staff, available dates, and available times directly from YClients.
- `apps/integrations/yclients/client.py:551` creates YClients records directly.
- `apps/integrations/yclients/client.py:641` cancels YClients records directly.
- `apps/integrations/yclients/webhooks.py:124` receives YClients webhooks in bot-platform.
- `apps/integrations/yclients/webhooks.py:214` resolves every webhook through one configured `YCLIENTS_WEBHOOK_TENANT_SLUG`.
- `apps/integrations/yclients/webhooks.py:472` creates `BookingRequest` from inbound YClients `record.create`.
- `apps/integrations/yclients/webhooks.py:578` and `:659` update/cancel `BookingReminder` rows from YClients updates/deletes.
- `apps/integrations/yclients/tasks.py:93` pushes bot-platform bookings to YClients.
- `apps/integrations/yclients/tasks.py:132` gates push by tenant feature `yclients_integration`, not by Ayla ownership.
- `apps/integrations/yclients/tasks.py:146` calls `client.create_record`.
- `apps/skills/booking/tools.py:586` and `:677` use YClients for masters and slots.
- `apps/skills/booking/tools.py:924`, `:1301`, and `:1590` execute confirm/cancel/reschedule directly against YClients.

**Design problems in simple terms:**

1. The target says "Ayla owns YClients sync", but the working implementation lives in bot-platform.
2. Ayla has a `booking_source='yclients'` switch, but no inspected runtime branch that actually uses a YClients adapter.
3. bot-platform can create/cancel/reschedule YClients records without Ayla `Appointment`, payment, tenant, or state-machine rules.
4. YClients webhooks can create bot-platform booking/reminder state without creating or updating Ayla `Appointment`.
5. The webhook tenant resolution is single-tenant by settings slug, while the rest of the architecture is moving toward multi-tenant routing.
6. The bot-platform push task treats YClients as an eventual mirror of local `BookingRequest`, while Ayla docs describe YClients as the source of truth for YClients-mode specialists. Those are opposite models.

**Impact:**

- A bot booking can exist in YClients and bot-platform, but not in Ayla.
- A YClients admin booking can create bot reminders, but not Ayla appointment/payment state.
- A mobile/Ayla booking and a bot/YClients booking can race for the same real slot.
- A bot cancel can cancel YClients while Ayla still believes the appointment is active.
- A YClients webhook can be acknowledged with HTTP 200 even if tenant resolution or local processing failed, leaving only audit logs as the recovery path.
- Payments and booking billing can be wrong because YClients booking state bypasses Ayla payment ownership.

**Recommended target shape:**

| Concern | Owner | Rule |
| --- | --- | --- |
| YClients credentials | Ayla | tenant/provider-scoped, encrypted |
| YClients slot reads | Ayla adapter | bot-platform asks Ayla for slots |
| YClients booking create/cancel/reschedule | Ayla adapter | bot-platform sends booking intent to Ayla |
| YClients webhooks | Ayla | Ayla updates `Appointment`, emits events to bot-platform |
| bot-platform reminders/conversations | bot-platform | consume Ayla booking events only |
| legacy bot YClients tools | bot-platform | disabled for Ayla-owned tenants or wrapped behind Ayla client |

**Recommended audit/fix order later:**

1. Decide if YClients is still a supported production source of truth, or only a legacy Formula Tela dependency to sunset.
2. If supported, build the Ayla YClients adapter behind `booking_source='yclients'` before enabling YClients-mode specialists.
3. Move the YClients webhook URL from bot-platform to Ayla, or make bot-platform forward raw webhooks to Ayla without local booking writes.
4. Disable direct bot-platform YClients create/cancel/reschedule for Ayla-owned tenants.
5. Replace bot booking skill YClients calls with Ayla REST calls for staff, slots, create, cancel, and reschedule.
6. Add reconciliation reports: YClients record without Ayla `Appointment`, Ayla appointment without YClients record, bot `BookingRequest` without either.

**Do not mark this fixed until:**

- one and only one service owns YClients writes per tenant;
- a YClients webhook creates/updates canonical Ayla appointment state before bot-platform reminders;
- bot-platform destructive booking tools no longer call YClients directly for Ayla-owned tenants;
- slot reads for YClients-mode specialists go through the same owner as booking writes;
- failed YClients sync has an operator-visible retry/reconciliation path.

### P0-8. Notification / Reminder Ownership Is Split Across Channels And Booking Sources

**Status:** Open

**Why it matters:** Reminders are user-facing and trust-sensitive. A wrong reminder is often worse than no reminder: the user can arrive for a cancelled visit, miss a real visit, receive duplicate prompts, or get a review/aftercare message for a service that never happened.

**Expected ownership:** Ayla should own canonical appointment/payment facts. bot-platform may own conversational delivery through MAX/Telegram, but it should schedule/send reminders from Ayla events or Ayla APIs, not from its own booking truth. Ayla mobile push notifications and bot chat reminders need one shared notification policy.

**Current behavior found:**

- Ayla has its own `notifications` app with persisted `Notification` rows and async delivery.
- Ayla sends mobile push notifications from appointment/payment outbox handlers.
- Ayla also has its own appointment reminder beat: `notifications.dispatch_appointment_reminders`.
- Ayla sends post-visit aftercare, water reminders, and beauty insights from its notification tasks.
- bot-platform has a separate `BookingReminder` model and separate Celery beats for T-24h, T-2h, escalation, and post-visit follow-up.
- bot-platform reminder logic was originally designed around confirmed YClients bookings.
- bot-platform now also schedules reminders from Ayla `booking.created` events using `ayla_appointment_id`.
- bot-platform master notification preferences and quiet hours exist, but the model says consumer-side gating is out of scope.

**Concrete code evidence:**

- `notifications/models.py:24` defines Ayla `Notification`.
- `notifications/services/dispatcher.py:29` defines Ayla `NotificationService`; `notifications/services/dispatcher.py:62` queues `deliver_notification`.
- `notifications/tasks.py:70` defines `dispatch_appointment_reminders`.
- `notifications/tasks.py:128` defines `dispatch_post_visit_aftercare`.
- `notifications/tasks.py:251` defines `dispatch_water_reminders`.
- `notifications/tasks.py:350` defines `dispatch_beauty_insights`.
- `notifications/outbox_handlers.py:153`, `:193`, `:238`, and `:362` send notifications for booking/payment events.
- `djangoProject/settings/base.py:604`, `:614`, `:623`, `:627`, and `:634` schedule Ayla reminder/retention beats.
- `apps/bookings/reminders_factory.py:62` schedules bot-platform T-24h and T-2h reminders.
- `apps/bookings/tasks.py:241` sends due bot-platform reminders through `apps.channels.max.outbound.send_message`.
- `apps/bookings/escalation.py:171` escalates stale T-24h reminders to salon manager.
- `apps/bookings/followups.py:341` sends bot-platform post-visit followups.
- `apps/eventbus/consumers/booking.py:161` schedules bot reminders keyed by Ayla `ayla_appointment_id`.
- `apps/booking/models.py:562` adds `BookingReminder.ayla_appointment_id`.
- `apps/booking/models.py:601` still has legacy uniqueness on `(yclients_record_id, kind)`.
- `apps/bookings/tasks.py:177` re-checks local `BookingRequest` state before sending, but `apps/bookings/tasks.py:195` documents that Ayla-path reminders with `booking_request IS NULL` are a known gap and return `send`.
- `apps/notifications/models.py:37` says consumer-side gating for master notification preferences ships later.

**Design problems in simple terms:**

1. Ayla and bot-platform both have notification engines.
2. Ayla sends by mobile push/device token; bot-platform sends by chat `chat_id`.
3. Ayla reminder logic is based on canonical `Appointment`.
4. bot-platform reminder logic is based on local `BookingReminder`, sometimes from YClients, sometimes from Ayla events.
5. The same real appointment can produce Ayla push notifications and bot chat reminders without a shared policy.
6. bot-platform has quiet-hour/preference data for masters, but dispatchers are not guaranteed to read it.
7. Some bot reminders cannot fully re-check Ayla appointment state at send time because they do not have a local `BookingRequest` FK.

**Impact:**

- Duplicate user messages: Ayla 1h push plus bot T-2h reminder plus bot T-24h reminder may all fire independently.
- Stale bot reminders can still send if Ayla cancellation/reschedule events fail to reach bot-platform.
- Ayla mobile users who never opened the bot receive only Ayla pushes; bot users may receive extra chat reminders. The product behavior differs by channel, not by explicit user preference.
- A follow-up/review/aftercare nudge can be sent from one service while the other service has payment/refund/cancel context that should suppress it.
- Operators cannot answer one simple question: "why did this user receive this message?" without checking two notification systems and two booking mirrors.

**Recommended target shape:**

| Concern | Owner | Rule |
| --- | --- | --- |
| Canonical appointment/payment state | Ayla | only Ayla decides whether a visit is active, completed, cancelled, refunded, or no-show |
| Mobile push notification center | Ayla | persisted `Notification` rows for app push/in-app feed |
| Chat reminders and callbacks | bot-platform | driven by Ayla events/API, not local booking truth |
| Reminder policy | shared contract | one table/spec for T-24h, T-2h, T-1h, escalation, aftercare, review |
| Quiet hours / opt-out | shared contract | all dispatchers must read the same policy or receive a policy decision from owner |
| Suppression rules | Ayla-owned facts | refund, cancel, no-show, dispute, opt-out, payment failure must be available before send |

**Recommended audit/fix order later:**

1. Create a notification ownership matrix by message type: booking created, payment paid, T-24h, T-2h, T-1h, cancel, reschedule, aftercare, review, water, beauty insight, manager escalation.
2. Decide which messages are mobile-only, chat-only, or both.
3. Add a shared message key/idempotency key per real appointment and message type.
4. Make bot-platform chat reminders re-check Ayla appointment state before send, or consume a fresh Ayla snapshot/event that is guaranteed current.
5. Apply quiet hours, opt-out, and master notification preferences at dispatch time.
6. Add an operator-visible notification ledger that can answer: message type, source event, source appointment, channel, recipient, status, and suppression reason.

**Do not mark this fixed until:**

- every reminder/notification type has one owner and one idempotency key;
- bot-platform cannot send a reminder for an Ayla appointment that Ayla now considers cancelled/rescheduled/completed/no-show;
- mobile push and chat reminders are coordinated by explicit policy, not by whichever beat happens to run;
- quiet hours and opt-out are enforced by all active dispatchers;
- notification delivery can be audited across Ayla and bot-platform for one appointment.

### P0-9. Analytics / Audit / Observability Ownership Is Fragmented

**Status:** Open

**Why it matters:** when booking, payment, reminder, AI, or integration flows fail, operators need one reliable way to answer: what happened, who caused it, did the event deliver, what side effects ran, and what should be replayed. Today the pieces exist, but they are split across services and tables with different meanings.

**Expected ownership:** each service may keep its own local metrics, but P0 product flows need a shared observability contract: common correlation ids, common event delivery status, common DLQ/replay rules, and one operator-facing incident view.

**Current behavior found:**

- bot-platform has several mature observability surfaces: `AuditLog`, `Event`, `DomainEvent`, `IngestDedupe`, `IngestDLQ`, `HandlerFailureTracker`, `ReplayTrace`, `AIRequestMetric`, `AIDailyMetricSummary`, `AdminTask`, OTel spans, Sentry capture, and dashboards.
- Ayla has separate surfaces: `AnalyticsEvent`, `OutboxEvent`, `Notification`, `NutritionOutboxEvent`, health/readiness endpoints, Sentry setup, and task logs.
- `ayla-ai-core` exposes tenant-aware library logs and telemetry fields, but it does not own durable product audit rows by itself.
- The same real user flow can create rows in Ayla analytics, Ayla outbox, Ayla notifications, bot-platform eventbus, bot-platform audit, bot replay, and AI metrics without one shared incident id.
- Different tables use different status semantics: Ayla `OutboxEvent.processed_at`, bot-platform `IngestDedupe.processed_at`, nutrition `DELIVERED/DLQ`, notification `SENT/FAILED/SKIPPED`, bot `AdminTask`, and bot `IngestDLQ` do not mean the same operational thing.

**Concrete code evidence:**

- `apps/audit/models.py` defines bot-platform `AuditLog`.
- `apps/audit/services.py` defines `write_audit`; audit failures are swallowed and logged.
- `apps/events/models.py` defines bot-platform `Event` for structured telemetry / analytics-style events.
- `apps/events/services.py` defines `emit`; unknown event names are logged but still persisted.
- `apps/eventbus/models.py` defines `DomainEvent`, `IngestDedupe`, `IngestDLQ`, `HandlerFailureTracker`, and terminal dedupe ledgers.
- `apps/eventbus/ingest_dispatcher.py` writes inbound cross-service DLQ rows and handler failure counters.
- `apps/observability/models.py` defines AI quality and request metric tables.
- `apps/replay/models.py` defines `ReplayTrace` for AI pipeline reconstruction.
- `apps/orchestrator/pipeline.py` writes audit rows, replay traces, Sentry events, OTel span events, and `AdminTask` rows for outbound failures.
- `analytics/models.py` defines Ayla `AnalyticsEvent`, a mobile telemetry table with idempotency by `client_event_id`.
- `appointments/models.py` defines Ayla `OutboxEvent` with `processed_at`, `error_count`, and `last_error`, but no explicit destination, delivered status, or dead-letter timestamp.
- `appointments/tasks.py` marks Ayla `OutboxEvent.processed_at` after local handler execution; many handlers are log stubs or notification handlers, not proof of bot-platform ingest.
- `notifications/models.py` defines Ayla `Notification` with `PENDING`, `SENT`, `FAILED`, and `SKIPPED`.
- `nutrition/models.py` defines `NutritionOutboxEvent` with `PENDING`, `DELIVERED`, and `DLQ`, separate from appointment outbox.
- `nutrition/webhook_delivery.py` implements retry/backoff/DLQ for nutrition webhooks, using another event shape.
- `djangoProject/health.py` checks only DB/cache/migrations, not cross-service event delivery, Celery backlog, DLQ size, or integration auth readiness.
- `src/ayla_ai_core/observability.py` adds tenant-aware logging context, but only the host application decides whether those logs become durable audit/metrics.

**Design problems in simple terms:**

1. There are too many meanings for "event": product analytics event, audit event, domain event, outbox event, notification event, AI replay event, and webhook event.
2. There is no single trace that follows one appointment from Ayla create -> payment -> outbox -> bot ingest -> reminder -> notification -> AI memory update.
3. Ayla `OutboxEvent.processed_at` can mean "local handler ran", while bot-platform `IngestDedupe.processed_at` means "external event consumer side effect committed". These are not equivalent.
4. DLQ ownership is inconsistent: bot eventbus has `IngestDLQ`, nutrition has `NutritionOutboxEvent.Status.DLQ`, appointments outbox has retry count and skipped rows, notifications have `FAILED`, and outbound bot delivery becomes `AdminTask`.
5. Analytics and audit are mixed conceptually. Mobile `AnalyticsEvent` is useful for product behavior, but it is not a compliance-grade audit trail for booking/payment decisions.
6. Sentry/logging can show symptoms, but the product still lacks a single durable operator ledger for P0 workflows.
7. Health endpoints prove process dependencies are alive, but not that Ayla and bot-platform can actually exchange events, auth, webhooks, reminders, or notifications.
8. `ayla-ai-core` logs `tenant_id`, but without a host-level `request_id` / `correlation_id` contract it cannot join back to Ayla booking/payment/user records reliably.

**Impact:**

- During an incident, support may not be able to prove whether a booking event was emitted, delivered, consumed, ignored as duplicate, failed, or skipped.
- A user can receive a reminder or notification, but operators may need to inspect several tables to explain why.
- Payment and booking reconciliation can miss failures because each service sees only its local success.
- Event delivery can look healthy in Ayla because local handlers processed rows while bot-platform never received the cross-service event.
- AI quality metrics can be correct for bot responses, while business conversion metrics are wrong because booking/payment events did not correlate.
- DLQ rows can exist without a shared replay policy, so recovery depends on developer knowledge instead of an operational runbook.

**Recommended target shape:**

| Concern | Owner | Rule |
| --- | --- | --- |
| Product analytics | emitting client/service | never used as compliance proof; schema/catalog versioned separately |
| Domain audit trail | owner of the state change | records who/what changed booking, payment, identity, memory, notification policy |
| Cross-service delivery ledger | publisher + consumer | stores event id, source, destination, status, attempts, last error, replay status |
| P0 correlation id | request entry / workflow starter | propagated through REST, events, Celery, notifications, AI calls, and logs |
| DLQ and replay | owning integration adapter | every DLQ has owner, severity, replay command, retention, and escalation rule |
| Operator incident view | platform/ops | one dashboard for booking/payment/reminder/event health across both services |
| Technical metrics | each runtime | exported consistently; dashboards join by service, tenant, flow, correlation id |

**Recommended audit/fix order later:**

1. Define a shared observability taxonomy: `analytics`, `audit`, `domain_event`, `integration_delivery`, `notification_delivery`, `ai_metric`, `replay_trace`.
2. Define mandatory ids for P0 flows: `correlation_id`, `causation_id`, `tenant_id`, `user_id`, `appointment_id`, `payment_id`, `event_id`, `notification_id`.
3. Decide whether Ayla `OutboxEvent` is local-only, cross-service-only, or split into two tables. Do not let one `processed_at` represent both meanings.
4. Add a cross-service delivery ledger or dashboard that joins Ayla outbox rows to bot-platform `IngestDedupe` / `IngestDLQ`.
5. Normalize DLQ statuses and replay procedures across appointment outbox, nutrition outbox, notification delivery, bot eventbus, and outbound bot messages.
6. Add P0 operational dashboards: oldest outbox age, pending count, DLQ count, handler failure count, notification failure count, stale reminder count, payment-event mismatch count.
7. Extend readiness/smoke checks beyond DB/cache: event ingest auth, enabled webhook destination, Celery beat freshness, DLQ thresholds, Sentry config, and required integration tokens.
8. Create an incident runbook: "booking created but bot did not know", "payment captured but reminder suppressed", "notification sent incorrectly", "AI answered stale booking".

**Do not mark this fixed until:**

- one appointment/payment flow can be traced end-to-end by one correlation id;
- Ayla event delivery to bot-platform has an operator-visible success/failure status;
- every DLQ has an owner, replay path, retention rule, and alert threshold;
- analytics events are clearly separated from audit/compliance events;
- support can answer "why did this user receive this message?" from one documented query/view;
- readiness or smoke monitoring detects broken cross-service auth/event delivery before users notice.

### P0-10. Provider / Master / Tenant Boundary Is Not A Single Contract

**Status:** Open

**Why it matters:** tenant/provider/master boundaries are the main security boundary for salons, masters, bookings, payments, reminders, analytics, and AI context. If one service thinks "master" means `User.role=specialist`, another thinks it means `CatalogMaster.linked_bot_user`, and a third only receives `tenant_id`, the system can show, mutate, or message data in the wrong provider scope.

**Expected ownership:** Ayla should be the canonical owner of provider tenant, user relationship, specialist profile, booking tenant, and payment tenant. bot-platform may keep local projections for bot/mini app UX, but those projections must be traceable to Ayla ids and revocation events. `ayla-ai-core` should remain a tenant-aware library, not the authority that decides which tenant/master data a user may access.

**Current behavior found:**

- Ayla has at least three role/tenant concepts at once: `User.role` + nullable `User.tenant`, `SpecialistProfile.tenant`, and `TenantUserRelationship`.
- Ayla `IsSpecialist` checks only `request.user.role == "specialist"`; it does not require an active staff/admin `TenantUserRelationship` in `request.tenant`.
- Ayla `IsTenantMember` returns `True` when `request.tenant` is absent. This is intentional for global customer endpoints, but it means protected views must be very explicit about whether they are global or tenant-scoped.
- Ayla appointment creation accepts a `specialist_id`, loads `SpecialistProfile` by id, and then writes the appointment under `specialist.tenant`. If `request_tenant_id` differs, the service can grant a customer relationship to the specialist's tenant. This may be intended marketplace behavior, but it is not a simple "request tenant owns booking" rule.
- Ayla `SpecialistProfile.tenant` and `Service.tenant` are nullable for legacy rollout. Active provider-facing rows can therefore exist in a weak tenant state unless every create/update path preserves the invariant.
- Ayla service management (`ServiceViewSet`) uses `IsSpecialist` and `request.user.specialist_profile`, not active tenant membership. A user marked as specialist is treated as a provider actor even if the tenant relationship is missing or revoked.
- Ayla public service/category reads are not clearly tenant-scoped in the inspected views. That may be valid marketplace behavior, but the boundary is not named as "global marketplace mode".
- bot-platform has a stronger local tenant guard: `TenantScopedManager`, `tenant_scope`, and strict middleware. But it uses its own `Tenant`, `TenantStaff`, `CatalogMaster`, `BotUser`, and role resolver.
- bot-platform master identity is `CatalogMaster.linked_bot_user`; admin identity is `TenantStaff`; customer identity is `BotUser`. These are local projections and are not the same tables as Ayla `TenantUserRelationship` or `SpecialistProfile`.
- bot-platform customer/admin/master Mini App auth currently binds many flows to `MAX_BOT_TENANT_SLUG`. That works for single-bot/single-tenant mode, but it is not a general multi-provider ingress contract.
- bot-platform event tenant verification helper is designed to validate `(user_id, tenant_id)` through a `TenantUserRelationship`, but in the inspected bot-platform code this canonical model is not actually present in `apps.tenancy.models`. The helper therefore has a fail-closed/fail-open bridge instead of a real cross-system relationship check.
- `ayla-ai-core` validates that tool calls carry tenant-aware candidate context, but it cannot prove the host application supplied the right tenant-scoped candidate set. The boundary must be enforced before AI context is built.

**Concrete code evidence:**

- `users.models.User` contains legacy `role` and nullable `tenant`.
- `users.models.SpecialistProfile` contains nullable `tenant`, `booking_source`, `yclients_company_id`, and `yclients_staff_id`.
- `users.models.TenantUserRelationship` is the newer relationship table with active/inactive history and per-tenant roles.
- `users.permissions.IsSpecialist` checks only the global user role.
- `users.permissions.IsTenantMember` passes when `request.tenant` is `None`.
- `users.middleware.TenantContextMiddleware` can resolve tenant from `X-Tenant` or JWT claim, but strict behavior depends on settings and route exceptions.
- `appointments.views.AppointmentViewSet.get_queryset()` correctly filters by `request.tenant` when present, but global list mode without tenant can show appointments across all active relationships.
- `appointments.application.services.create_booking_service.CreateBookingService` loads specialist/service by ids and sets booking tenant from the specialist, not from `request.tenant`.
- `services.views.ServiceViewSet` gates provider CRUD through `IsSpecialist` and `request.user.specialist_profile`.
- `services.views.ServicePublicViewSet` returns active services without an explicit tenant filter in the inspected queryset.
- `users.masters_internal_api.MastersByYclientsStaffIdsView` is a good local example: it requires explicit `tenant_id` and filters masters by `(tenant_id, yclients_staff_id)`, which avoids cross-tenant staff-id collisions.
- `apps.tenancy.models.Tenant` in bot-platform enforces slug immutability and uses active-tenant managers.
- `apps.tenancy.managers.TenantScopedManager` enforces tenant scoping for bot-platform ORM reads/writes.
- `apps.catalog.models.CatalogMaster` stores `ayla_user_id`, `yclients_staff_id`, invite state, and `linked_bot_user`.
- `apps.tenancy.models.TenantStaff` stores bot-local owner/admin/receptionist roles.
- `apps.identity.services.role_resolver.resolve_role()` computes bot roles from `CatalogMaster` and `TenantStaff`, not from Ayla relationships.
- `apps.miniapp_api.views.require_init_data()` and `apps.admin_api.auth.require_admin_role()` resolve tenant from configured bot tenant / `BotUser`.
- `apps.master_api.auth.require_master_init_data()` resolves the master from `CatalogMaster.linked_bot_user`.
- `apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized()` expects a canonical relationship check, but also documents the transition/fail-open risk when that model is unavailable.

**Design problems in simple terms:**

1. The word "tenant" does not yet have one cross-system meaning. In some places it means salon/provider, in some places bot tenant, in some places active request scope, and in some places marketplace filter.
2. The word "master" has at least two identities: Ayla `SpecialistProfile` and bot `CatalogMaster`. They can drift.
3. The word "role" has at least three sources: Ayla `User.role`, Ayla `TenantUserRelationship.role`, and bot `TenantStaff`/`CatalogMaster`.
4. Revocation is not guaranteed to propagate. A user can lose an Ayla tenant relationship while bot-platform still has `TenantStaff`, `CatalogMaster.linked_bot_user`, reminders, or cached role context unless events and projections are guaranteed.
5. Booking creation can cross from requested tenant to specialist tenant. That may be the correct marketplace rule, but it must be explicit and tested because it affects ownership, notifications, payment attribution, and audit.
6. Nullable tenant fields are useful for migration, but dangerous as a long-term invariant for active masters/services/appointments.
7. Local tenant safety inside bot-platform does not prove cross-system safety if bot tenant ids are not guaranteed to be the same canonical ids as Ayla tenant ids.
8. AI context can become wrong even if `ayla-ai-core` is careful, because the core library receives already-selected candidates from the host.

**Impact:**

- A specialist can pass provider permissions because of a global role even if their active relationship to the current tenant is missing.
- A service, master, schedule, reminder, or booking can be attributed to the wrong provider if projections drift.
- A revoked staff/admin/master can remain active in bot-platform surfaces if the revocation event is not delivered and applied.
- Cross-tenant YClients staff ids can collide unless every lookup follows the `tenant_id + staff_id` pattern.
- Marketplace flows and tenant-scoped flows can be confused, producing bookings under a provider the caller did not think they selected.
- Support cannot reliably answer "which tenant owns this master/user/booking" from one documented rule.
- AI recommendations can include wrong-provider masters/services if the host builds context from stale or unscoped mirrors.

**Recommended target shape:**

| Concern | Owner | Rule |
| --- | --- | --- |
| Tenant/provider canonical id | Ayla | one immutable UUID per provider/salon; bot stores the same id or an explicit mapping |
| Tenant slug | Ayla, projected to bot | immutable after creation; never used as sole security boundary |
| User relationship to tenant | Ayla `TenantUserRelationship` | active relationship is the source of truth for customer/staff/admin access |
| Master/specialist identity | Ayla `SpecialistProfile` | bot `CatalogMaster` is a projection with `ayla_specialist_id`/`ayla_user_id` and sync status |
| Bot master login | bot-platform | allowed only when linked to an active Ayla specialist projection |
| Bot admin/receptionist roles | Decide explicitly | either projected from Ayla TUR or declared bot-local with its own sync/revocation contract |
| Booking tenant | Ayla appointment service | derived by a documented rule: selected specialist tenant or explicit tenant-scoped create, never implicit ambiguity |
| Public marketplace reads | Ayla | explicitly named global mode with safe filters; not accidental `request.tenant=None` behavior |
| AI candidate context | host app | host must filter by canonical tenant before calling `ayla-ai-core`; core validates consistency only |

**Recommended audit/fix order later:**

1. Write an ADR: `Provider / Master / Tenant Boundary`.
2. Decide if tenant ids are shared UUIDs across Ayla and bot-platform or mapped through a table. Do not rely on slug coincidence alone.
3. Mark `User.role` as legacy or define exactly where it remains authoritative. Recommended: tenant-role decisions should use `TenantUserRelationship`.
4. Require active tenant relationship for provider/staff actions, not just `User.role=specialist`.
5. Make active `SpecialistProfile.tenant` and active `Service.tenant` non-null in target state; keep nullable only as migration debt with a cleanup plan.
6. Add invariant tests: specialist tenant, service tenant, appointment tenant, payment tenant, reminder tenant, and event tenant must agree.
7. Define marketplace booking semantics: when `request.tenant` is missing or different from specialist tenant, what is allowed and what relationship is created.
8. Add projection contract for bot `CatalogMaster`: source Ayla ids, sync status, tombstone/revoke behavior, and conflict handling.
9. Decide whether bot `TenantStaff` roles are local-only or projections of Ayla tenant roles. If local-only, document why they may differ.
10. Add contract tests for revocation: revoke relationship in Ayla -> bot loses relevant master/admin/customer capabilities or receives a visible stale-projection failure.

**Do not mark this fixed until:**

- there is one accepted cross-system definition of tenant/provider/master/user role;
- every provider/admin/master action checks active tenant membership or an explicitly documented bot-local role;
- bot-platform projections have canonical Ayla ids or a documented mapping table;
- active master/service/booking/payment rows cannot have ambiguous tenant ownership;
- marketplace mode and tenant-scoped mode are separate contracts, not side effects of missing `request.tenant`;
- revocation from Ayla reliably removes or disables bot-platform permissions;
- AI candidate context is proven tenant-filtered before reaching `ayla-ai-core`.

### P0-11. User Data Lifecycle / Privacy Boundary Is Split

**Status:** Open

**Why it matters:** the product stores phone numbers, profiles, booking history, payments, food photos, nutrition/water logs, AI conversations, AI memory, notification/device tokens, analytics events, and bot/channel identity. A user-facing request like "delete my data", "export my data", or "forget what Ayla knows about me" must have one clear meaning. Today those actions are implemented as separate local operations in different services.

**Expected ownership:** Ayla owns canonical account, auth, phone, mobile profile, bookings, payments, nutrition, reviews, and mobile notifications. bot-platform owns channel identity, bot conversations, AI memory, bot consent, bot audit/replay, and channel preferences. A privacy request must be a cross-service operation with scope, status, idempotency, retry, and audit.

**Current behavior found:**

- Ayla account delete anonymizes local `User` PII, deactivates the account, blacklists refresh tokens, deletes device tokens, deletes social accounts, and later hard-deletes the `User` row after a 30-day grace period.
- Ayla account delete does not appear to call bot-platform or emit a durable privacy/delete event to bot-platform.
- Ayla account delete does not explicitly handle all Ayla-owned data categories in one place: appointments, payments, reviews, analytics events, notifications, nutrition profile, food logs, food scans, water entries, AI conversations, personal context, OTP codes, and tenant relationship history are handled by model FK behavior or not addressed in the delete service.
- Ayla `UserPersonalContext` has its own wipe endpoint that hard-deletes the context row; bot-platform memory spec prefers soft-delete tombstones and deletion reasons.
- Ayla AI conversation delete only soft-deletes one conversation. It is not the same operation as account delete or AI memory delete.
- Ayla water entry delete is per-entry soft delete with restore window and later purge. Food logs and food scans have different retention behavior.
- Food scan images are documented as S3 objects with a bucket lifecycle TTL, while the database row can remain as audit/replay/cost evidence.
- Ayla analytics events use `actor=SET_NULL`, so hard-deleting a user preserves product telemetry but breaks direct subject export unless the relationship is captured before deletion.
- Ayla notifications cascade with user delete, but their rendered body/data may contain appointment context until then.
- bot-platform `PrivacyConsentSkill.data_export()` exports `BotUser`, `ConsentRecord`, conversations, and messages for one `BotUser`, but not `MemoryEntry`, `UserPersonalContext`, `UserPreferences`, `ClientProfile`, booking mirrors, reminders, audit logs, replay traces, or Ayla-owned data.
- bot-platform `PrivacyConsentSkill.data_delete()` calls `delete_bot_user_data()`, which soft-marks conversations and then hard-deletes conversations/messages and the `BotUser`; this is channel data deletion, not global Ayla account deletion.
- bot-platform `ConsentRecord` is append-only by design, but current `BotUser` hard-delete cascades consent rows, so the legal consent history can disappear while only `AuditLog` rows remain.
- bot-platform `MemoryEntry` and `UserPersonalContext` implement a stronger privacy model in the schema, but the inspected self-service export/delete tool does not include them.
- `ayla-ai-core` is a library and has no durable delete/export responsibility; it relies on host apps to decide what memory/context is stored and removed.

**Concrete code evidence:**

- `users.services.AuthService.delete_account()` anonymizes local user PII, clears profile/avatar, deactivates specialist profile, blacklists refresh tokens, deletes `DeviceToken` rows, and deletes social accounts.
- `users.views.UserMeView.delete()` exposes Ayla account deletion and optionally consumes OTP.
- `users.management.commands.cleanup_deleted_users` hard-deletes soft-deleted Ayla users after `GRACE_PERIOD_DAYS = 30`.
- `users.personal_context_views.UserPersonalContextView.delete()` hard-deletes the Ayla `UserPersonalContext` row.
- `users.personal_context_views.UserPersonalContextFieldDeleteView.delete()` resets one field to its default value.
- `ai.views.ConversationDetailView.delete()` sets `is_active=False` and `deleted_at` for one Ayla AI conversation.
- `nutrition.models.FoodScan` stores provider audit data and only references the image storage key; the model doc says image TTL is handled by S3 lifecycle.
- `nutrition.models.NutritionProfile`, `FoodLog`, `FoodScan`, `WaterEntry`, and `CrossDomainShownRule` store wellness/food/water/health-adjacent data with different deletion/retention behavior.
- `nutrition.services.water_entry_service.WaterEntryService.soft_delete()` soft-deletes a water entry and hard-deletes the mirrored `FoodLog`.
- `nutrition.services.water_entry_service.purge_deleted_water_entries()` physically purges soft-deleted water entries after a configurable age.
- `notifications.models.Notification` stores rendered title/body/data and cascades with the user.
- `analytics.models.AnalyticsEvent.actor` uses `on_delete=SET_NULL`; anonymous events use `anonymous_session_id`.
- `apps.skills.privacy_consent.tools.data_export()` exports bot user, consents, conversations, and messages only.
- `apps.skills.privacy_consent.tools.data_delete()` hard-deletes the `BotUser` through `delete_bot_user_data()`.
- `apps.identity.services.resolver.delete_bot_user_data()` marks conversations deleted, then deletes conversations and the `BotUser`.
- `apps.identity.models.UserPersonalContext`, `MemoryEntry`, and `RedZoneAccessLog` define a stronger soft-delete/audit model for AI memory.
- `apps.consent.models.ConsentRecord` says consent history is append-only, but `ConsentRecord.bot_user` is `CASCADE`.
- `apps.audit.models.AuditLog` is retained/archived separately and intentionally does not store raw PII.

**Design problems in simple terms:**

1. "Delete account", "delete bot data", "delete AI memory", "delete chat history", and "delete wellness data" are not the same operation, but the system does not expose one cross-service matrix that explains the difference.
2. Ayla can delete/anonymize a user while bot-platform still keeps channel identity, conversations, AI memory, preferences, reminders, or projections.
3. bot-platform can delete a `BotUser` while Ayla still keeps account/profile, appointments, payments, nutrition, personal context, and mobile AI conversations.
4. Export is incomplete from both directions: bot export is channel-local, and an Ayla aggregate export endpoint was not found in the inspected paths.
5. Deletion semantics differ by table: hard delete, soft delete, field reset, anonymization, `SET_NULL`, cascade, S3 TTL, audit retention, and DLQ retention all coexist without one policy.
6. Sensitive data is not grouped by retention class. Payment/legal audit, product analytics, AI memory, notification body, and food photos have different reasons to retain or delete.
7. Consent history is conceptually append-only, but can be removed by bot user hard-delete unless a separate audit record is considered sufficient.
8. Account deletion is not a saga. If bot-platform is down when Ayla account deletion happens, there is no inspected durable retryable request to finish the privacy operation later.
9. Some delete actions erase evidence before export can run. For example, hard-deleting a user after 30 days can break subject access unless an export/status operation is completed first.

**Data lifecycle matrix:**

| Data category | Current owner | Current delete/export behavior | Risk |
| --- | --- | --- | --- |
| Ayla auth user / phone / profile | Ayla | account delete anonymizes and later hard-deletes user | no cross-service propagation |
| Ayla device tokens | Ayla | deleted during account delete | good local behavior, not coordinated with bot channels |
| Ayla social accounts | Ayla | deleted during account delete | good local behavior |
| Ayla tenant relationships | Ayla | revoke exists separately; account delete does not explicitly orchestrate relationship history | unclear privacy/audit retention |
| Ayla appointments | Ayla | not explicitly handled in account delete; FK behavior determines outcome | booking/payment legal retention not documented |
| Ayla payments | Ayla | not explicitly handled in account delete | payment audit retention vs user erasure not documented |
| Ayla notifications | Ayla | cascade on user hard-delete; rendered text/data retained until then | body/data can contain sensitive context |
| Ayla analytics | Ayla | `actor` set null on user delete | useful BI retention, but export linkage disappears |
| Ayla personal context | Ayla | separate hard-delete or per-field reset | not aligned with bot memory tombstones |
| Ayla mobile AI conversations | Ayla | per-conversation soft-delete only | not part of account delete/export boundary |
| Ayla nutrition profile/logs/scans | Ayla | mixed cascade, soft-delete, S3 TTL, SET_NULL | health/wellness lifecycle not centralized |
| bot `BotUser` | bot-platform | hard-delete through privacy skill | not the same as Ayla account delete |
| bot conversations/messages | bot-platform | marked then hard-deleted in delete flow | deletes channel history but not AI memory entries |
| bot consents | bot-platform | exported, then cascade-deleted with BotUser | conflicts with append-only consent story |
| bot AI memory | bot-platform | schema supports soft-delete/forget-all; inspected privacy tool does not include it | user can think memory is gone when it is not |
| bot audit/replay/metrics | bot-platform | retained separately | export/deletion inclusion policy not documented |
| `ayla-ai-core` runtime context | host apps | no storage responsibility | fine, but host delete/export rules must be explicit |

**Recommended target shape:**

| User-facing request | Recommended meaning | Owner / orchestrator |
| --- | --- | --- |
| Delete AI memory | Remove live AI memory entries and Ayla personal-context prompt hints; keep required audit tombstones | bot-platform owns memory deletion; Ayla participates for local context until deprecated |
| Delete chat history | Remove or anonymize conversations/messages in Ayla mobile AI and bot-platform channel conversations according to retention rules | owning chat service, coordinated by privacy operation |
| Delete channel data | Delete one bot/channel identity and channel-local conversations/preferences | bot-platform |
| Delete account | Anonymize/delete Ayla account and trigger all dependent service deletions; retain legal/payment/audit records as policy allows | Ayla orchestrates cross-service saga |
| Export my data | Aggregate Ayla account/profile/booking/payment/nutrition + bot channel/conversation/memory data into one operation status | Ayla public entrypoint, bot-platform contributor |
| Revoke provider relationship | Remove tenant/provider access without deleting global account or global AI memory | Ayla, with bot projection update |

**Recommended audit/fix order later:**

1. Write an ADR: `User Data Lifecycle And Privacy Boundary`.
2. Define privacy scopes in product copy and API names: `account`, `ai_memory`, `chat_history`, `channel_identity`, `wellness_data`, `provider_relationship`.
3. Add a durable privacy operation model: `request_id`, user id, scope, requested_at, status per service, retries, completed_at, failed_at, error.
4. Make Ayla account delete emit or call a bot-platform privacy operation with idempotency. Do not rely on best-effort synchronous calls only.
5. Build an aggregate export contract before hard-delete: Ayla contributes account/profile/transactional/wellness data; bot-platform contributes channel/conversation/memory data.
6. Update bot-platform export to include live `MemoryEntry` data according to `docs/specs/memory-entry-schema.md`, or rename it to "channel conversation export".
7. Update bot-platform delete to either call memory forget-all or rename it to "delete channel data".
8. Decide whether bot `ConsentRecord` must survive deletion as append-only legal audit, be anonymized, or be represented only by `AuditLog`.
9. Define retention classes for every sensitive table: immediate delete, anonymize, soft-delete tombstone, legal retention, product analytics, physical media TTL.
10. Add a reconciliation report: users deleted in Ayla but still present in bot-platform, and bot users deleted while Ayla user remains active.

**Do not mark this fixed until:**

- user-facing delete/export language maps to explicit data scopes;
- Ayla account delete creates a durable cross-service deletion request for bot-platform;
- bot-platform channel delete does not falsely claim to delete all Ayla/account/wellness data;
- AI memory export/delete includes `MemoryEntry` or is clearly excluded in product/API copy;
- every sensitive data category has a retention/anonymization rule;
- hard-delete jobs cannot run before required privacy operation statuses are completed or intentionally waived;
- tests cover one user with Ayla account, appointments, payment, nutrition data, Ayla AI chat, bot conversation, bot consent, and bot memory.

## P1 Findings

### P1-1. Identity And Service-To-Service Auth Are Fragmented

**Status:** Open

**Why it matters:** There are several service auth mechanisms: `AYLA_SERVICE_TOKEN`, `AYLA_INTERNAL_API_TOKEN`, `NUTRITION_SERVICE_TOKEN`, event HMAC, and future JWT contract. This increases integration mistakes.

**Evidence:**

- Nutrition internal endpoints use `X-Service-Token`.
- Some Ayla internal endpoints use `Authorization: Bearer AYLA_INTERNAL_API_TOKEN`.
- bot-platform recommendation and profile clients use `AYLA_SERVICE_TOKEN`.
- Event ingest uses `EVENT_INGEST_HMAC_SECRET`.

**Impact:** Tokens can be misconfigured independently. Some flows fail closed, but production may boot with missing integration env and fail only at runtime.

**Recommended fix:**

1. Create a single service-to-service auth matrix.
2. Mark production-required env vars for every enabled integration.
3. Standardize naming and headers where possible.
4. Add readiness checks for enabled integrations.

#### Detailed Audit: Identity / Service-To-Service Auth

**Date:** 2026-05-28

**Simple conclusion:** the system has the right security ideas, but the names and contracts are not aligned. Ayla has moved several internal endpoints to `Authorization: Bearer AYLA_INTERNAL_API_TOKEN` plus `X-External-User-ID`. bot-platform still uses `AYLA_SERVICE_TOKEN` for some of those calls. For payments, bot-platform reads `AYLA_INTERNAL_API_TOKEN`, but the setting is not declared in `config/settings/base.py`, so the client can see an empty token even if the environment variable exists.

| Flow | Ayla expects | bot-platform sends / configures | Risk |
| --- | --- | --- | --- |
| Nutrition internal API | `X-Service-Token: NUTRITION_SERVICE_TOKEN` + `X-External-User-ID` | `apps.integrations.ayla.nutrition_client` sends `X-Service-Token` from `AYLA_SERVICE_TOKEN` | P1/P0: works only if ops manually keeps differently named vars equal |
| Catalog recommendations | `Authorization: Bearer AYLA_INTERNAL_API_TOKEN` + `X-External-User-ID` | `recommendations_client` sends bearer from `AYLA_SERVICE_TOKEN` | P0: likely `403` against current Ayla |
| Internal booking records | `Authorization: Bearer AYLA_INTERNAL_API_TOKEN` + `X-External-User-ID` | No inspected bot client in this audit; Ayla contract is clear | P1 until caller is verified |
| Payment internal retry | `Authorization: Bearer AYLA_INTERNAL_API_TOKEN` + `X-External-User-ID` + body `client_id` | bot payment retry client is still TODO | P1/P0: endpoint exists, callback not wired |
| Payment create from bot | Ayla public create expects client JWT and `IsClient`; no internal create endpoint | bot payment client sends bearer `AYLA_INTERNAL_API_TOKEN` | P0: wrong auth model for same path |
| Payment client config | bot code reads `settings.AYLA_INTERNAL_API_TOKEN` | `config/settings/base.py` defines `AYLA_BASE_URL` and `AYLA_SERVICE_TOKEN`, not `AYLA_INTERNAL_API_TOKEN` | P0: live payment client can always see empty token |
| Masters internal lookup | `Authorization: Bearer AYLA_INTERNAL_API_TOKEN`, no external user | Need verify bot caller separately | P1: correct Ayla pattern, but same missing bot setting risk if caller uses this token |
| Ayla event ingest into bot-platform | `X-Ayla-Event-Signature` HMAC + timestamp using `EVENT_INGEST_HMAC_SECRET` | bot-platform receiver exists; Ayla booking/payment publisher not found | P0 already covered by outbox audit |
| Nutrition outbound webhook | HMAC with `NUTRITION_WEBHOOK_SECRET` | Separate from ADR eventbus HMAC | P1: another auth family to document and rotate |
| Profile fetch after `user.profile.updated` | Bot client calls `/api/v1/users/{user_id}` with `AYLA_SERVICE_TOKEN` | Ayla inspected route list exposes `/api/v1/users/me/`, not `/api/v1/users/{id}` | P1/P0: likely auth and path drift together |

**Evidence from Ayla backend:**

- `users.permissions.IsServiceAccount` checks `X-Service-Token` against `settings.NUTRITION_SERVICE_TOKEN` and fails closed when empty.
- `users.permissions.IsBotServiceWithVerifiedClient` checks `Authorization: Bearer <token>` against `settings.AYLA_INTERNAL_API_TOKEN`, requires `X-External-User-ID`, resolves an Ayla `User`, and assigns `request.user`.
- `users.permissions.IsInternalBearer` checks `Authorization: Bearer <token>` against `settings.AYLA_INTERNAL_API_TOKEN` without user resolution.
- `payments.InternalPaymentRetryView` disables default JWT auth and uses `IsBotServiceWithVerifiedClient`.
- `appointments.records_api.MeBookingsListView` disables default JWT auth and uses `IsBotServiceWithVerifiedClient`.
- `users.catalog_recommendations_api.CatalogRecommendationsView` disables default JWT auth and uses `IsBotServiceWithVerifiedClient`.
- `users.masters_internal_api.MastersByYclientsStaffIdsView` disables default JWT auth and uses `IsInternalBearer`.
- Ayla `djangoProject.settings.base` defines `NUTRITION_SERVICE_TOKEN` and `AYLA_INTERNAL_API_TOKEN`.
- Ayla `djangoProject.settings.prod` requires `GOOGLE_CLIENT_ID`, `APPLE_CLIENT_ID`, and `YOOKASSA_WEBHOOK_ALLOWED_IPS`, but does not include `NUTRITION_SERVICE_TOKEN` or `AYLA_INTERNAL_API_TOKEN` in `_REQUIRED_PROD_ENV`.

**Evidence from bot-platform:**

- `config.settings.base` defines `AYLA_BASE_URL` and `AYLA_SERVICE_TOKEN`.
- Search in `config/settings` did not find a declared `AYLA_INTERNAL_API_TOKEN` setting, while `AylaPaymentsClient` reads `settings.AYLA_INTERNAL_API_TOKEN`.
- `apps.integrations.ayla.nutrition_client` documents that it renamed `NUTRITION_SERVICE_TOKEN` to `AYLA_SERVICE_TOKEN`; it sends `X-Service-Token` from `AYLA_SERVICE_TOKEN`.
- `apps.integrations.ayla.recommendations_client` sends `Authorization: Bearer {AYLA_SERVICE_TOKEN}` and `X-External-User-ID`.
- `apps.integrations.ayla.profile_client` sends `Authorization: Bearer {AYLA_SERVICE_TOKEN}` to `/api/v1/users/{user_id}`.
- `apps.integrations.ayla_payments.client` sends `Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}`, but the setting is not declared in the inspected bot settings.
- `config.settings.production` fail-fast checks include `MYSITE_CATALOG_SERVICE_TOKEN`, `SENTRY_DSN`, `CHROMA_AUTH_TOKEN`, and `MYSITE_WEBHOOK_HMAC_SECRET`, but not `AYLA_BASE_URL`, `AYLA_SERVICE_TOKEN`, `AYLA_INTERNAL_API_TOKEN`, or `EVENT_INGEST_HMAC_SECRET`.
- bot-platform event ingest HMAC verification fails closed when `EVENT_INGEST_HMAC_SECRET` is empty, but production boot does not fail fast on that missing secret in the inspected settings.

**Why this blocks stable work:**

1. Recommendations can fail even when both services are "configured", because bot-platform sends `AYLA_SERVICE_TOKEN` while Ayla validates against `AYLA_INTERNAL_API_TOKEN`.
2. Nutrition can work only by convention: the same secret must be called `AYLA_SERVICE_TOKEN` in bot-platform and `NUTRITION_SERVICE_TOKEN` in Ayla.
3. Payment live mode can fail before any HTTP call because `settings.AYLA_INTERNAL_API_TOKEN` is missing from bot-platform settings.
4. Event ingest can be deployed with an empty HMAC secret and then reject every Ayla event at runtime instead of failing at boot.
5. Service-on-behalf-of-user and service-only calls use similar bearer syntax but different identity guarantees; without a matrix, developers can send a bearer token without `X-External-User-ID` to an endpoint that requires a resolved user.
6. Rotation is hard: one logical integration secret may have different names in each repo, so ops can rotate one side and silently break the other.

**Recommended target shape:**

Use three explicit auth families and stop mixing names:

| Auth family | Use for | Header contract | Recommended setting names |
| --- | --- | --- | --- |
| `internal_user_actor` | bot acts on behalf of a user | `Authorization: Bearer <token>` + `X-External-User-ID` | `AYLA_INTERNAL_API_TOKEN` on both sides |
| `internal_service` | bot/service does catalog/admin lookup without user actor | `Authorization: Bearer <token>` | `AYLA_INTERNAL_API_TOKEN` or separate `AYLA_INTERNAL_SERVICE_TOKEN` if scopes split |
| `nutrition_legacy` | existing nutrition internal endpoints until migrated | `X-Service-Token` + `X-External-User-ID` | Either keep `NUTRITION_SERVICE_TOKEN` on both sides, or migrate nutrition to `AYLA_INTERNAL_API_TOKEN` |
| `event_transport` | Ayla outbox to bot-platform ingest | `X-Ayla-Event-Signature` + `X-Ayla-Event-Timestamp` | `EVENT_INGEST_HMAC_SECRET` on both sides |
| `webhook_transport` | product-specific webhook push | `X-Signature`/domain-specific HMAC | domain-specific only when truly separate |

**Recommended fix order:**

1. Add `AYLA_INTERNAL_API_TOKEN = os.environ.get("AYLA_INTERNAL_API_TOKEN", "")` to bot-platform settings if any bot client uses it.
2. Change `recommendations_client` to use `AYLA_INTERNAL_API_TOKEN`, not `AYLA_SERVICE_TOKEN`, because Ayla uses `IsBotServiceWithVerifiedClient`.
3. Decide nutrition naming: either set bot `NUTRITION_SERVICE_TOKEN` and use it directly, or migrate Ayla nutrition endpoints to `IsBotServiceWithVerifiedClient`.
4. Do not use `AYLA_SERVICE_TOKEN` as a generic bucket for unrelated Ayla auth. Rename or deprecate it.
5. Add production fail-fast checks for enabled Ayla integrations: `AYLA_BASE_URL`, `AYLA_INTERNAL_API_TOKEN`, nutrition token if nutrition is enabled, and `EVENT_INGEST_HMAC_SECRET` if event ingest is enabled.
6. Add a single `docs/architecture/service-auth-contract.md` table: endpoint, repo, path, auth family, required headers, setting name on caller, setting name on callee, fail-fast behavior, rotation owner.
7. Add contract tests that instantiate bot clients with settings and assert exact headers against Ayla permission expectations.
8. Add readiness checks that call cheap Ayla internal endpoints with configured auth and report `ok/misconfigured/forbidden`.

**Do not mark this fixed until:**

- every bot-platform Ayla client uses the token name that Ayla actually validates;
- `AYLA_INTERNAL_API_TOKEN` is declared in bot-platform settings if used by any client;
- production boot fails fast when enabled cross-service auth secrets are missing;
- recommendation, booking-records, payment-retry, and nutrition smoke tests prove `200/403` behavior with valid and invalid headers;
- token rotation docs name both sides of each secret explicitly;
- no endpoint relies on "same secret, different setting name" without that being documented and tested.

### P1-2. AI Conversation And Memory Ownership Is Unclear

**Status:** Open

**Why it matters:** Ayla backend and bot-platform both store AI conversations or personal context. Without a boundary, personalization can fork.

**Evidence:**

- Ayla backend has `ai.Conversation` and `ai.Message`.
- bot-platform has `apps.conversations.Conversation` and `Message`.
- Ayla backend has `users.UserPersonalContext`.
- bot-platform has `identity.UserPersonalContext`, `MemoryEntry`, and red-zone memory infrastructure.

**Impact:** The same user can have different AI memory depending on entry point. Privacy deletion/export semantics can also diverge.

**Recommended fix:**

1. Define which data is "profile" and which is "AI memory".
2. Keep PII/profile in Ayla.
3. Keep AI memory and cross-channel conversation context in bot-platform, unless explicitly scoped otherwise.
4. Exchange only via documented events/API.

#### Detailed Audit: AI Conversation / Memory Ownership

**Date:** 2026-05-28

**Simple conclusion:** the target architecture says bot-platform is the AI backbone and owns long-term AI memory. The current implementation does not fully follow that boundary. Ayla backend still has an active mobile AI chat, its own `Conversation`/`Message` tables, and its own `users.UserPersonalContext` that is injected into the LLM prompt. bot-platform also has conversations and a richer memory system. This means one real user can have two different "Ayla remembers me" states.

| Concern | Target owner from ADR-0009 | Current Ayla backend | Current bot-platform | Risk |
| --- | --- | --- | --- | --- |
| Canonical user identity and PII | Ayla backend | `users.User`, `Profile`, phone, avatar, auth/JWT | `BotUser` has channel identity and `ayla_user_id`; also stores phone mirror | PII boundary needs strict mirroring rules |
| Mobile AI chat history | Should route to AI owner or be explicitly scoped | Active `/api/v1/ai/chat/`, `ai.Conversation`, `ai.Message` | `apps.conversations.Conversation`, `Message` for bot/channel chats | Two histories for one assistant |
| Long-term AI memory | bot-platform | `users.UserPersonalContext` with explicit preferences | `identity.UserPersonalContext` + `MemoryEntry` + red-zone audit | Memory fork and deletion/export drift |
| AI prompt context | bot-platform memory should be source | Ayla `ChatService` loads `actor.personal_context` and formats prompt hint | bot-platform has memory schema and red-zone reader/writer but no single cross-service memory API found | Same user can get different answers by channel |
| Shared AI library | `ayla-ai-core`, same approved version/SHA | `requirements.txt` pins `ayla-ai-core @ ...@v0.6.0` | `pyproject.toml` pins `ayla-ai-core[django]` to later SHA aligned with `0.8.1` work | Different prompt/orchestrator behavior |
| Sensitive memory | bot-platform red/yellow/green model | Ayla context stores `skin_sensitivities` and diet preferences in plain model fields | `MemoryEntry` has zones, consent, encryption, TTL, red-zone access log | Privacy controls differ by entry point |
| Forget/delete semantics | One user-visible memory policy | Ayla deletes `UserPersonalContext` row and soft-deletes conversations | bot-platform has `forget_all_requested_at`, soft delete, deletion reasons, audit logs | User may think memory is gone while another service keeps it |
| AI core persistence | Consumers provide store, but ownership must be fixed | `DjangoConversationStore` writes Ayla `ai.*` tables | bot-platform has its own conversation services | Flexible interface makes duplicate ownership easy |

**Evidence from target docs:**

- `docs/adr/ADR-0009-ayla-split-domain-architecture.md` states that bot-platform owns AI chat, conversations, skills, tools, KB/RAG, and core user memory.
- The same ADR states that Ayla backend owns canonical user identity, PII, booking, payments, catalog, provider-specific history, reviews, and nutrition.
- `ayla-ai-core/docs/ADR-0009-split-domain-context.md` repeats that `ayla-ai-core` is a pure Python library and does not own persistence.
- `docs/architecture/event-contract.md` says medical details belong only to bot-platform `UserPersonalContext` and should not appear in events.

**Evidence from Ayla backend:**

- `ai.views.AIChatView` exposes active mobile chat at `/api/v1/ai/chat/`.
- `ai.application.services.chat_service.ChatService` calls `get_concierge_for(actor)` and sends messages through `ayla-ai-core`.
- `ai.stores.DjangoConversationStore` persists conversations and messages into Ayla `ai.Conversation` and `ai.Message`.
- `ai.application.services.chat_service.ChatService._build_personal_context_hint()` reads `actor.personal_context`.
- `ai.personal_context_hint.format_personal_context_hint()` renders Ayla `users.UserPersonalContext` fields into the LLM prompt.
- `users.personal_context_views` exposes `/api/v1/users/me/personal-context/` with GET/PATCH/DELETE, field reset, and skip tracking.
- `users.UserPersonalContext` is a OneToOne model on Ayla `User`, with districts, time slots, budget, diet, skin sensitivities, home/work districts, favorite masters, busy days, and provenance fields.
- Ayla `requirements.txt` pins `ayla-ai-core @ git+https://github.com/AndreyDeveloper84/ayla-ai-core.git@v0.6.0`.

**Evidence from bot-platform:**

- `apps.conversations.models.Conversation` and `Message` store bot/channel conversation history with tenant scoping, trace id, rendered text, skill state, payment grounding, and ownership tier.
- `apps.conversations.services.record_message()` is the sanctioned write path for bot messages and emits `conversations.message.stored`.
- `apps.identity.models.BotUser` links channel identity to canonical Ayla user via `ayla_user_id`, without shared DB FK.
- `apps.identity.models.UserPersonalContext` is keyed by canonical Ayla `user_id` and is intentionally not tenant-scoped.
- `apps.identity.models.MemoryEntry` stores zone-tagged memory facts with sensitivity zone, source, consent, TTL, deletion reason, encrypted content, and source tenant metadata.
- `apps.identity.models.RedZoneAccessLog`, `apps.identity.services.red_zone_reader`, and `apps.identity.services.memory_writer` implement stricter controls for red-zone reads/writes.
- `apps.identity.services.memory_writer` currently fails closed for yellow/red writes because the Ayla DOB lookup endpoint is not implemented yet.
- bot-platform `pyproject.toml` pins `ayla-ai-core[django]` to a later SHA, while local `ayla-ai-core` reports version `0.8.1`.

**Why this blocks stable product behavior:**

1. A user can talk to Ayla in mobile and build one context, then talk in MAX/mini app and get a different context.
2. Deleting memory in Ayla mobile can delete Ayla `users.UserPersonalContext`, but not bot-platform `MemoryEntry` rows.
3. Deleting/forgetting memory in bot-platform can leave Ayla `users.UserPersonalContext` and Ayla AI chat history intact.
4. Sensitive preferences such as allergies or skin sensitivities are handled with different protection levels in the two services.
5. Different `ayla-ai-core` versions can make the same prompt, tool call, or history window behave differently.
6. Support and audit cannot answer a simple user question: "What exactly does Ayla remember about me?"
7. The platform can accidentally violate DRY and SOLID at the architecture level: each service has a separate memory model, separate prompt formatter, separate history store, and separate deletion semantics.

**Important nuance:**

Not every user-related fact should move to bot-platform. Ayla backend should still own canonical profile and structured transactional records:

- phone, email, auth, avatar, legal profile data;
- appointments, payments, reviews, provider-specific visit history;
- structured nutrition logs, food photos, daily water totals, and mobile-facing wellness records.

The risk is specifically long-term AI memory and AI conversation context. Structured health/nutrition records can live in Ayla, but AI-derived long-term memory about the user should have one owner and one privacy model.

**Recommended target shape:**

| Data type | Owner | Rule |
| --- | --- | --- |
| Auth identity, phone, profile, avatar | Ayla backend | Canonical source; bot-platform may mirror only safe fields needed for UX |
| Provider-specific booking/payment/review history | Ayla backend | Never becomes cross-tenant AI memory without explicit user-safe transformation |
| Structured nutrition/water/food logs | Ayla backend | Mobile/product records; bot-platform may call APIs or receive safe summaries |
| Channel identity | bot-platform | `BotUser` maps channel users to Ayla canonical user id |
| AI conversation history for MAX/mini app | bot-platform | Canonical for channel AI runtime |
| Mobile AI conversation history | Decide explicitly | Recommended: route mobile AI chat to bot-platform or mark Ayla AI chat as legacy/separate |
| Long-term AI memory | bot-platform | `UserPersonalContext` + `MemoryEntry` + red-zone controls |
| Prompt memory hints | bot-platform | Ayla should not format a competing long-term AI memory prompt unless mobile AI remains separate by decision |
| Shared orchestration library | `ayla-ai-core` | Same approved version/SHA in both consumers |

**Recommended fix order:**

1. Make an explicit product decision: is Ayla `/api/v1/ai/chat/` still a production chat surface, or is it legacy until mobile routes AI chat to bot-platform?
2. Write one ADR or contract doc named `ai-memory-ownership` with the table above: owner, allowed reader, allowed writer, deletion/export behavior, events/API.
3. If mobile AI should share the same memory as MAX/mini app, route mobile AI chat to bot-platform through gateway/API instead of maintaining Ayla `ai.Conversation` as a second canonical history.
4. If Ayla mobile AI must stay temporarily, mark Ayla `ai.Conversation` and `users.UserPersonalContext` as local/legacy and forbid them from being called "core memory".
5. Align `ayla-ai-core` version/SHA across Ayla and bot-platform before comparing AI behavior.
6. Define one memory delete/export flow across both services. User-facing wording must say which stores are affected.
7. Add contract tests proving that memory deletion in one public surface triggers the required action in the other service or clearly does not claim to.
8. Add tests or lint rules preventing red/yellow AI memory from being emitted through ordinary events or plain profile sync.
9. Add a service endpoint or event for safe profile mirror fields only; do not use raw `UserPersonalContext` as a cross-service profile API.

**Do not mark this fixed until:**

- one service is declared owner of long-term AI memory;
- mobile AI chat either uses that owner or is explicitly documented as separate/legacy;
- users can delete/export "what Ayla remembers" with one understandable behavior;
- Ayla and bot-platform use the same approved `ayla-ai-core` version/SHA;
- sensitive memory has one privacy model, not one per entry point;
- support can trace a user answer back to the exact conversation store and memory store that produced it.

### P1-3. REST Path Drift In Bot-Platform Ayla Clients

**Status:** Open

**Why it matters:** Bot-platform clients can call paths that do not match Ayla URL mounting.

**Evidence:**

- Ayla recommendations route is mounted under `/api/v1/internal/me/catalog/recommendations/`.
- bot-platform recommendations client builds `/internal/me/catalog/recommendations/` relative to `AYLA_BASE_URL`.

**Impact:** Depending on `AYLA_BASE_URL`, recommendations can fail with 404 even though both sides have tests.

**Recommended fix:**

1. Decide if `AYLA_BASE_URL` includes `/api/v1`.
2. Encode that convention in one config name and docs.
3. Add contract tests against Ayla route list or OpenAPI schema.

#### Detailed Audit: Ayla API Client Path

**Date:** 2026-05-28

**Simple conclusion:** bot-platform has several Ayla clients, but they do not share one URL-building convention. Some clients assume `AYLA_BASE_URL` is only the host and append `/api/v1/...`. One client assumes `AYLA_BASE_URL` already points at an API root and appends `/internal/...`. Another client calls an endpoint that does not exist in the inspected Ayla route list. This creates integration bugs that look like auth or availability problems, but are actually path drift.

| Client / flow | bot-platform builds | Ayla actual route | Status | Risk |
| --- | --- | --- | --- | --- |
| Nutrition client | `{AYLA_BASE_URL}/api/v1/nutrition/internal/...` | `/api/v1/nutrition/internal/...` | Path aligned if `AYLA_BASE_URL` is host-only | P1: naming/auth drift remains, path mostly OK |
| Recommendations client | `{AYLA_BASE_URL}/internal/me/catalog/recommendations/` | `/api/v1/internal/me/catalog/recommendations/` | Missing `/api/v1` | P0: likely 404 unless `AYLA_BASE_URL` secretly includes `/api/v1` |
| Recommendations tests | Assert `https://ayla.test/internal/me/catalog/recommendations/` | Ayla route list says `/api/v1/internal/...` | Test pins wrong path | P0: tests protect drift |
| Profile sync client | `{AYLA_BASE_URL}/api/v1/users/{user_id}` | Inspected `users_urls.py` exposes `/api/v1/users/me/`, not `/api/v1/users/{id}` | Endpoint not found in route list | P1/P0: `user.profile.updated` consumer can fail on every event |
| Payment create client | `{AYLA_BASE_URL}/api/v1/payments/create` | `/api/v1/payments/create/` | Missing trailing slash | P1/P0: POST redirect risk; also body/auth/response mismatch |
| Payment create response | Expects `checkout_url` | Ayla returns `confirmation_url` | Response shape mismatch | P0: even a successful HTTP call can parse as failure |
| Payment retry callback | TODO client method | `/api/v1/payments/internal/{payment_id}/retry/` exists | Not wired | P1/P0: recovery UX incomplete |
| Booking records client | No bot client found in inspected code | `/api/v1/internal/me/bookings/` exists | Ayla side ready, caller missing or elsewhere | P1: customer booking history/repeat intent may not use Ayla |
| Booking mutation client | No bot mutation client found | `/api/v1/appointments/` exists | Missing target adapter | P0 already covered by booking ownership audit |

**Evidence from Ayla backend:**

- `djangoProject.urls` mounts `users.catalog_recommendations_urls` at `/api/v1/internal/me/catalog/recommendations/`.
- `users.catalog_recommendations_urls` mounts `CatalogRecommendationsView` at the empty suffix, so the full route is exactly `/api/v1/internal/me/catalog/recommendations/`.
- `djangoProject.urls` mounts `appointments.records_urls` at `/api/v1/internal/me/bookings/`.
- `appointments.records_urls` exposes list, detail, and repeat-intent routes under `/api/v1/internal/me/bookings/`.
- `djangoProject.urls` mounts `nutrition.urls` at `/api/v1/nutrition/`.
- `nutrition.urls` exposes internal routes under `internal/...`, so bot URLs like `/api/v1/nutrition/internal/scan/` match.
- `djangoProject.urls` mounts `payments.urls` at `/api/v1/payments/`.
- `payments.urls` exposes `create/`, `internal/<uuid:pk>/retry/`, `<uuid:pk>/retry/`, and `<uuid:pk>/refund/` with trailing slashes.
- `payments.views.PaymentCreateView` returns `confirmation_url`, not `checkout_url`.
- `payments.views._execute_payment_retry()` also returns `confirmation_url`.
- `users.users_urls` exposes `/api/v1/users/me/`, `/api/v1/users/me/client-profile/`, `/api/v1/users/me/personal-context/`, and `/api/v1/users/me/tenant-relationships/`; no `/api/v1/users/{user_id}` route was found in the inspected user URL files.

**Evidence from bot-platform:**

- `apps.integrations.ayla.nutrition_client` consistently builds URLs under `{base}/api/v1/nutrition/internal/...`.
- `apps.integrations.ayla.recommendations_client` builds `{base}/internal/me/catalog/recommendations/`, missing `/api/v1`.
- `apps.miniapp_api.tests.test_recommendations` explicitly asserts `https://ayla.test/internal/me/catalog/recommendations/`, so the current test suite locks in the wrong path against current Ayla.
- `apps.integrations.ayla.profile_client` builds `{base}/api/v1/users/{user_id}`.
- `apps.integrations.ayla_payments.client` builds `{base}/api/v1/payments/create` without the trailing slash.
- `apps.integrations.ayla_payments.client` parses response field `checkout_url`; Ayla returns `confirmation_url`.
- Search did not find a bot-platform client for `/api/v1/internal/me/bookings/`.
- Search did not find a bot-platform booking mutation client calling Ayla `/api/v1/appointments/`.

**Why this blocks stable work:**

1. A 404 from recommendations can be misdiagnosed as a token problem because auth is also drifting in the same client.
2. Profile sync can put `user.profile.updated` events into retry/DLQ forever if bot-platform fetches a route Ayla does not expose.
3. Payment create can fail in several layers: missing slash redirect, wrong auth, wrong request body, and wrong response field.
4. Tests currently verify bot-platform's local assumptions, not Ayla's actual route table.
5. `AYLA_BASE_URL` has no single meaning. If ops sets it to `https://ayla/api/v1` to fix recommendations, nutrition and payment become `.../api/v1/api/v1/...`.
6. Missing bot clients for records and booking mutations means new Ayla endpoints can exist unused, while older local/YClients paths keep running.

**Recommended target shape:**

Use one base URL convention:

| Setting | Meaning | Example |
| --- | --- | --- |
| `AYLA_BASE_URL` | scheme + host only, no API prefix | `https://dev.gobeauty.site` |
| `AYLA_API_PREFIX` | API prefix, default `/api/v1` | `/api/v1` |
| `AylaApiClient.path()` | joins base + prefix + endpoint safely | `/internal/me/catalog/recommendations/` -> full URL |

Recommended endpoint constants:

| Constant | Value |
| --- | --- |
| `AYLA_RECOMMENDATIONS_PATH` | `/internal/me/catalog/recommendations/` |
| `AYLA_NUTRITION_SCAN_PATH` | `/nutrition/internal/scan/` |
| `AYLA_PAYMENT_CREATE_PATH` | `/payments/create/` |
| `AYLA_PAYMENT_INTERNAL_RETRY_PATH` | `/payments/internal/{payment_id}/retry/` |
| `AYLA_ME_BOOKINGS_PATH` | `/internal/me/bookings/` |
| `AYLA_APPOINTMENTS_PATH` | `/appointments/` |

**Recommended fix order:**

1. Decide and document that `AYLA_BASE_URL` is host-only. Do not allow callers to sneak `/api/v1` into it.
2. Add a small shared URL builder in bot-platform for Ayla clients; every client should use it.
3. Fix recommendations path to include `/api/v1` through the shared builder and update tests so they assert the real Ayla route.
4. Either add an Ayla internal user profile endpoint for bot-platform, or change `profile_client` to a route that exists. Do not keep `/api/v1/users/{user_id}` as an assumed contract without an Ayla URL.
5. Fix payment create path to include trailing slash and align response field names (`confirmation_url` vs `checkout_url`).
6. Add or implement missing bot clients for `/api/v1/internal/me/bookings/` and `/api/v1/appointments/` if bot-platform should use Ayla as the booking SoR.
7. Add contract tests that compare bot client paths against Ayla URL names or OpenAPI schema, not hand-written expected strings.
8. Add a lightweight startup/readiness check for each enabled Ayla client path: build URL, call a cheap endpoint, report exact status.

**Do not mark this fixed until:**

- all bot-platform Ayla clients use one shared URL builder;
- `AYLA_BASE_URL` meaning is documented and enforced in tests;
- recommendations live call hits `/api/v1/internal/me/catalog/recommendations/`;
- profile sync either calls a real Ayla route or the consumer is disabled until the route exists;
- payment create/retry paths use trailing slashes and parse Ayla's actual response fields;
- tests fail if a bot client URL is missing `/api/v1` or points at a route not present in Ayla.

### P1-4. AI Memory Delete / Export Boundary Is Not Contracted

**Status:** Open

**Why it matters:** A user-facing phrase like "delete what Ayla remembers about me" must have one exact meaning. Today Ayla backend and bot-platform have separate memory, conversation, delete, and export paths. The code and docs do not yet define one cross-service operation.

**Evidence:**

- bot-platform has a detailed `MemoryEntry` / `UserPersonalContext` privacy spec with `forget_all_requested_at`, deletion reasons, soft-delete semantics, export rules, and red-zone audit.
- bot-platform `PrivacyConsentSkill` exports and deletes `BotUser`, consents, conversations, and messages, but inspected code did not show it exporting or deleting `MemoryEntry`.
- Ayla backend exposes `DELETE /api/v1/users/me/personal-context/` and hard-deletes the `users.UserPersonalContext` row.
- Ayla backend account delete anonymizes the `User`, clears device tokens/social accounts, but inspected code did not show it coordinating with bot-platform memory deletion.
- Ayla backend AI chat delete soft-deletes a single `ai.Conversation`, but this is separate from user memory deletion.

**Impact:** The product can tell a user that their data or memory was deleted while another service still retains AI memory, chat history, or profile-derived prompt context. Export can also be incomplete, which is risky for trust and compliance.

**Recommended fix:**

1. Define one ADR-level boundary for "profile", "transactional data", "conversation history", and "AI memory".
2. Define one user-facing delete/export matrix: which data stores are included, excluded, soft-deleted, hard-deleted, retained for audit, and why.
3. Make one service own AI memory deletion/export orchestration. Recommended: bot-platform owns AI memory operations; Ayla owns account/profile deletion and calls or emits to bot-platform.
4. Do not expose "delete Ayla memory" unless it reaches both Ayla-local memory remnants and bot-platform `MemoryEntry`, or clearly says what it deletes.

#### Detailed Audit: AI Memory Ownership ADR / Delete-Export Boundary

**Date:** 2026-05-28

**Simple conclusion:** the system already has strong pieces, but they do not form one user-rights flow. bot-platform has the stronger AI memory privacy model. Ayla backend has the canonical account/profile model and a simpler personal-context API. The missing part is a cross-service ADR that says what happens when the user asks to see, delete, export, or stop future AI memory.

| User action | Ayla backend current behavior | bot-platform current behavior | Gap |
| --- | --- | --- | --- |
| View personal context | `GET /api/v1/users/me/personal-context/` returns Ayla `users.UserPersonalContext` | Memory schema docs mention `GET /api/v1/users/me/memory`, but inspected production code did not show that endpoint | Two possible "what Ayla knows" surfaces |
| Update personal context | Ayla `PATCH /personal-context/` writes fields directly on `users.UserPersonalContext` | `MemoryEntry` has `memory_writer.write_entry()` and zone rules | No single writer contract |
| Delete one memory field | Ayla resets a named `UserPersonalContext` field to default | `MemoryEntry` spec defines per-entry delete with `deletion_reason='user_delete'` | Field reset vs per-entry delete are not the same model |
| Delete all personal context | Ayla hard-deletes `UserPersonalContext` row | `UserPersonalContext.forget_all_requested_at` exists; spec says async sweep soft-deletes entries | Different deletion semantics |
| Delete channel data | No direct Ayla-to-bot coordination found | `PrivacyConsentSkill.data_delete()` calls `delete_bot_user_data()` for one `BotUser` | Deletes channel identity, not necessarily global AI memory |
| Export data | No Ayla account-wide export path found in inspected code | `data_export()` returns BotUser, consents, conversations, messages | Export omits `MemoryEntry` despite memory spec export rules |
| Delete account | Ayla `AuthService.delete_account()` anonymizes account and clears tokens/social/device state | No inspected cross-service call/event from Ayla account delete to bot-platform | User account deletion can leave bot memory unless separate flow runs |
| Delete AI chat history | Ayla `DELETE /api/v1/ai/conversations/{id}/` soft-deletes one Ayla AI conversation | bot `delete_bot_user_data()` hard-deletes conversations after marking them | Separate chat stores with different retention |
| Sensitive/red memory audit | Ayla `UserPersonalContext` has no red-zone access log | bot-platform `RedZoneAccessLog` is append-only, 7-year retention | Sensitive facts get different audit treatment by entry point |

**Evidence from bot-platform docs and code:**

- `docs/adr/ADR-0011-user-personal-context-privacy.md` maps subject rights to memory endpoints such as `GET /api/v1/users/me/memory`, `DELETE /api/v1/users/me/memory/{entry_id}`, and `POST /api/v1/users/me/memory/forget-all`.
- `docs/specs/memory-entry-schema.md` defines export semantics: export includes live memory fields like content, source, consent timestamps, and excludes soft-deleted rows and internal `deletion_reason`.
- `apps.identity.models.UserPersonalContext` has `soft_deleted_at` and `forget_all_requested_at`.
- `apps.identity.models.MemoryEntry` has `delete_requested_at`, `soft_deleted_at`, and `deletion_reason`.
- `apps.identity.models.RedZoneAccessLog` is intended to survive memory entry purge.
- `apps.skills.privacy_consent.tools.data_export()` exports `bot_user`, `consents`, and `conversations`, but inspected code does not include `MemoryEntry` in the export payload.
- `apps.skills.privacy_consent.tools.data_delete()` calls `delete_bot_user_data(bot_user)`.
- `apps.identity.services.resolver.delete_bot_user_data()` soft-marks conversations, then hard-deletes conversations/messages through cascade and deletes the `BotUser`; it does not touch `UserPersonalContext` or `MemoryEntry`.
- Search did not find production endpoints implementing `/api/v1/users/me/memory/forget-all` or per-entry memory delete in the inspected bot-platform code.

**Evidence from Ayla backend:**

- `users.personal_context_views.UserPersonalContextView.delete()` deletes the whole `UserPersonalContext` row with `UserPersonalContext.objects.filter(user=request.user).delete()`.
- `users.personal_context_views.UserPersonalContextFieldDeleteView.delete()` resets one field to a default value.
- `users.personal_context_views.UserPersonalContextView.patch()` stores explicit context fields and stamps `data_sources[field] = "explicit"`.
- `ai.views.ConversationDetailView.delete()` soft-deletes a single Ayla AI conversation by setting `is_active=False` and `deleted_at`.
- `users.services.AuthService.delete_account()` anonymizes account PII, clears avatar/profile names, deactivates specialist profile, blacklists tokens, deletes device tokens, and deletes social accounts.
- Search in inspected Ayla paths did not find account delete calling bot-platform, emitting an AI memory deletion event, exporting user data, or deleting Ayla AI conversations as part of account deletion.

**Why this is an architecture risk:**

1. The same phrase can mean different things:
   - "personal context" in Ayla backend;
   - "memory" in bot-platform docs;
   - "my data" in bot-platform privacy skill;
   - "conversation history" in both services.
2. A user can delete Ayla personal context but still have bot-platform `MemoryEntry` rows.
3. A user can invoke bot-platform data delete and remove channel conversations, while Ayla personal context and Ayla AI conversations remain.
4. A user export can miss the most sensitive part of the product promise: "what Ayla remembers".
5. Ayla hard-deletes personal context while bot-platform spec prefers soft-delete tombstones and deletion reasons. This creates audit inconsistency.
6. Red/yellow memory controls exist in bot-platform, but Ayla context fields such as diet and skin sensitivities can still feed prompts without the same zone/consent model.
7. Account deletion in Ayla does not appear to be a cross-service saga. If bot-platform is down or unreachable, there is no visible retry/idempotency mechanism for memory deletion.

**Recommended target ADR:**

Create an ADR named `ADR: AI Memory Ownership And Data Subject Rights Boundary`.

Minimum decisions:

| Decision | Recommended answer |
| --- | --- |
| Long-term AI memory owner | bot-platform |
| Canonical account/profile owner | Ayla backend |
| Conversation owner for MAX/mini app | bot-platform |
| Conversation owner for mobile AI | Decide: preferably bot-platform through gateway; otherwise Ayla mobile chat is explicitly legacy/separate |
| Structured wellness/nutrition records owner | Ayla backend |
| Cross-service delete orchestrator | Ayla owns account delete; bot-platform owns AI memory delete; Ayla account delete must call/emit to bot-platform |
| Cross-service export orchestrator | One public export endpoint should aggregate Ayla profile/transactional data + bot-platform AI memory/conversations |
| Audit retention | Audit logs survive deletion; user export excludes internal deletion reasons and soft-deleted memory entries |
| Failure semantics | Delete/export request gets a request id, status, retry policy, and idempotency key |

**Recommended delete/export boundary:**

| Data category | Delete on "forget AI memory" | Delete on "delete account" | Include in "export my data" | Owner |
| --- | --- | --- | --- | --- |
| Ayla account auth/profile/phone/avatar | No | Yes/anonymize according to legal retention | Yes, before deletion | Ayla backend |
| Ayla transactional records: appointments/payments/reviews | No | Usually retain/anonymize per legal/business retention | Yes where legally allowed | Ayla backend |
| Ayla structured nutrition/water/food logs | No, unless user says delete wellness data | Yes or separately governed | Yes | Ayla backend |
| Ayla `users.UserPersonalContext` | Yes, if still used for prompt memory | Yes | Yes if live | Ayla backend until deprecated |
| Ayla `ai.Conversation` / `Message` | Optional: only if product says chat history is memory | Yes or separate "delete chat history" | Yes if retained | Ayla backend until deprecated |
| bot `BotUser` channel identity | No, unless deleting channel data/account | Yes for bot account delete | Yes | bot-platform |
| bot conversations/messages | Optional: separate "delete chat history" | Yes/anonymize according to retention | Yes if retained | bot-platform |
| bot `UserPersonalContext` and `MemoryEntry` | Yes | Yes | Yes for live entries, per spec | bot-platform |
| `RedZoneAccessLog`, audit logs, payment audit | No | No, retain according to audit policy | Maybe summary only; not internal operational detail | Owning service |

**Recommended implementation sequence, still as audit scope:**

1. Stop using the same UI copy for different operations. Use three names:
   - "delete AI memory";
   - "delete chat history";
   - "delete account".
2. Promote bot-platform memory spec from local docs to cross-system ADR status.
3. Decide whether Ayla `users.UserPersonalContext` is deprecated, a profile-preference cache, or a temporary mobile-only prompt hint.
4. Define a cross-service `user.privacy.delete_requested` / `user.privacy.export_requested` event or internal API with idempotency key.
5. Add an operation status model: `request_id`, user id, requested scope, service statuses, completed/failed/retryable.
6. Update bot-platform `data_export()` contract so it includes live `MemoryEntry` data according to `memory-entry-schema.md` export rules, or explicitly label it "channel data export", not "all data export".
7. Update bot-platform `data_delete()` contract so it either triggers `forget_all` for `MemoryEntry` or explicitly labels itself "delete channel data".
8. Update Ayla account delete to publish/call a bot-platform memory/account deletion operation, with retry and DLQ.
9. Add contract tests with one user who has Ayla context, Ayla AI conversation, bot conversation, and bot `MemoryEntry`; verify delete/export outcomes for each scope.

**Do not mark this fixed until:**

- there is one accepted ADR for AI memory ownership and data-subject rights;
- product copy distinguishes memory deletion, chat history deletion, and account deletion;
- Ayla account delete either reaches bot-platform or produces a durable retryable request;
- bot-platform export includes `MemoryEntry` or is renamed to channel-only export;
- bot-platform delete either touches `MemoryEntry` or is renamed to channel-only delete;
- Ayla `UserPersonalContext` is either deprecated as AI memory or included in the same delete/export boundary;
- a cross-service test proves that a user with data in both systems gets a complete export and a complete scoped deletion.

### P0-12. Contract Tests / E2E Smoke Coverage Does Not Prove The Unified System

**Status:** Open

**Why this is P0:** The three codebases have many useful tests, but most of them prove local behavior inside one repository. They do not yet prove that Ayla backend, bot-platform, and `ayla-ai-core` work together as one release unit. This means all CI pipelines can be green while the real dev/prod flow is broken by a field rename, event-name mismatch, auth-header mismatch, or version drift.

**Expected design:**

- One shared contract source describes event names, event versions, REST paths, auth headers, required response fields, and owner service.
- Producer tests generate or validate the same fixtures that consumer tests read.
- Cross-repo smoke tests run the most important user journeys end to end.
- CI blocks merges/deploys when a P0 cross-service contract breaks.
- Contract changes are explicit product/architecture decisions, not accidental local refactors.

**Current behavior found:**

- bot-platform has strong local eventbus tests: dispatcher idempotency, booking/payment consumers, tenant verification mandates, replay fixtures, and Ayla client unit tests.
- Ayla has strong local outbox tests: envelope wrapper, booking state emission coverage, outbox end-to-end dispatch, payment webhook behavior, and internal payment retry endpoint authorization.
- `ayla-ai-core` has its own package CI on Python 3.12/3.13, and bot-platform has an import/version smoke for `ayla-ai-core`.
- The CI workflows are still repo-local. bot-platform CI checks bot-platform, Ayla CI checks Ayla, and ai-core CI checks ai-core. There is no observed compatibility job that checks selected commits of all three repositories together.
- The bot-platform E2E Ayla suite is skipped by default unless `AYLA_BASE_URL` and `AYLA_SERVICE_TOKEN` are present. That is useful for operator-triggered smoke, but it is not a mandatory merge gate.
- Ayla `smoke-on-dev` exists, but it focuses on the deployed Ayla container and nutrition smoke. It does not prove the booking/payment event path into bot-platform.
- Many tests use synthetic fixtures written inside the same repo. For example, bot-platform consumer tests construct `booking.created` data with `appointment_id`, while Ayla outbox tests show payload examples with `booking_id`. Each side can pass locally while disagreeing in production.
- Ayla event registry includes `booking.confirmed` and `payment.confirmed`, while bot-platform contract tests expect `payment.authorized` and `payment.captured`. The tests currently preserve each side's vocabulary rather than forcing one shared vocabulary.
- bot-platform Ayla payment client tests expect a generic create-payment response with `checkout_url`, while inspected Ayla internal payment retry returns `confirmation_url` and is appointment-bound.
- `ayla-ai-core` version checks exist, but the dev smoke workflow in Ayla still contains comments about runtime installation of `v0.6.0`, while current package metadata and bot smoke expect `0.8.1`. That is a release-process smell even if current requirements are newer.

**Why this is an architectural problem, in simple words:**

Unit tests answer: "does this module behave the way this repo expects?"

Contract tests must answer: "does the other repo actually send or accept exactly this?"

E2E smoke must answer: "can a real user journey cross service boundaries today?"

Right now those are mixed together. The system has many good local safety nets, but it lacks the one safety net that matters most for a distributed product: proving that the seams between services still fit.

**Concrete gaps to cover:**

1. **Ayla outbox -> bot-platform ingest.**
   Ayla should produce canonical JSON fixtures for every emitted outbox event. bot-platform consumer tests should read those exact fixtures, not hand-built equivalents.

2. **bot-platform REST clients -> Ayla provider endpoints.**
   bot-platform should generate request fixtures for recommendations, nutrition, profile fetch, payment create/retry, and privacy operations. Ayla provider tests should validate that the real views accept those requests and return the expected response shape.

3. **Event taxonomy gate.**
   CI should fail when Ayla declares or emits `payment.confirmed` while bot-platform only handles `payment.captured`, or when Ayla emits `booking_id` while bot-platform consumes `appointment_id`, unless a deliberate versioned migration exists.

4. **Auth contract gate.**
   The exact service-to-service auth model must be tested across repos: header name, bearer format, token setting name, empty-token behavior, user impersonation header, and tenant scope.

5. **Payment flow smoke.**
   A minimal dev smoke should prove: failed payment in Ayla -> bot-platform receives failure event -> bot payment retry skill calls Ayla internal retry -> Ayla creates retry payment -> bot receives usable checkout/confirmation URL.

6. **Booking flow smoke.**
   A minimal dev smoke should prove: Ayla appointment created/rescheduled/cancelled/completed -> outbox dispatch -> bot ingest -> local proxy/reminders update exactly once.

7. **Tenant boundary smoke.**
   Cross-tenant attempts must be tested across the real HTTP/event boundary, not only inside local model tests.

8. **Privacy lifecycle smoke.**
   Delete/export requests should prove cross-service behavior: Ayla account/privacy request reaches bot-platform AI memory/channel data or produces a durable retry/DLQ item.

9. **AI core compatibility smoke.**
   Both host apps should prove they install and use the same intended `ayla-ai-core` version for the same release train.

10. **API spec drift gate.**
    The API specification in the external docs folder should be checked against implemented Ayla routes and bot-platform clients. Right now the spec review found mismatches, but it is not automated.

**Impact:**

- A field rename can break production without breaking CI.
- Event consumers can be perfectly idempotent for the wrong payload.
- API clients can be well tested against mocked responses that no real Ayla endpoint returns.
- A deployment can contain incompatible commits from the three repos.
- Dev smoke can prove Ayla alone is healthy while the unified Ayla + bot + AI product is broken.
- High-risk flows like booking, payment, privacy, and tenant boundaries depend on manual awareness instead of a repeatable release gate.

**Recommended fix:**

1. Create a shared `contracts/` package or folder with:
   - `events/*.json` canonical event fixtures;
   - `rest/*.yaml` or OpenAPI snippets for internal endpoints;
   - `auth/*.md` for service-token rules;
   - `versions/ai-core.json` for allowed package versions per release train.
2. Make Ayla provider tests produce/validate the event fixtures.
3. Make bot-platform consumer tests consume the Ayla fixtures unchanged.
4. Make bot-platform client tests produce request fixtures and Ayla provider tests accept them unchanged.
5. Add a cross-repo GitHub Actions workflow or release script that checks:
   - Ayla `dev` commit;
   - bot-platform `dev` commit;
   - `ayla-ai-core` pinned tag/SHA;
   - shared contracts.
6. Add a small mandatory E2E smoke suite for P0 flows:
   - booking created -> bot ingest;
   - payment failed/retry -> Ayla internal retry;
   - tenant mismatch -> denied;
   - privacy delete/export -> cross-service operation recorded;
   - ai-core version -> identical expected version in both hosts.
7. Keep external providers mocked at the boundary for CI, but keep the Ayla/bot boundary real. For example, mock YooKassa/YClients/MAX, but do not mock Ayla views when testing bot-platform's Ayla client contract.
8. Record smoke output as deploy evidence: commit SHAs, contract fixture version, test command, and result.

**Do not mark this fixed until:**

- there is one shared contract fixture set used by both Ayla and bot-platform tests;
- a bot-platform consumer test reads at least one real Ayla-produced booking fixture;
- an Ayla provider test accepts at least one real bot-platform client request fixture;
- CI or a release gate checks compatible commits across all three repos;
- dev smoke proves at least booking-created and payment-retry flows across service boundaries;
- contract changes require fixture updates in the same PR or release bundle;
- the API specification drift check is automated or explicitly run as part of release sign-off.

## P2 Findings

### P2-1. Fat Modules Reduce Maintainability

**Status:** Open

**Why it matters:** Large files mix validation, orchestration, integration calls, persistence, and response formatting. This makes small changes risky.

**Evidence:**

- Ayla `nutrition/views.py` is over 1200 lines.
- Ayla `payments/views.py` is over 700 lines.
- bot-platform `apps/skills/booking/tools.py` is over 2500 lines.
- bot-platform `apps/miniapp_api/views.py` is over 1000 lines.

**Impact:** Violates single responsibility. Harder to test, review, and safely refactor.

**Recommended fix:**

1. Extract application services around booking, payment, nutrition, and mini app flows.
2. Keep views/controllers thin.
3. Move external integration logic into adapters.
4. Add focused tests around extracted services.

## Cross-System Contract Matrix

| Contract | Publisher/caller | Consumer/callee | Current risk | Priority |
| --- | --- | --- | --- | --- |
| Booking lifecycle events | Ayla | bot-platform | Event names do not fully match | P0 |
| Payment lifecycle events | Ayla | bot-platform | `payment.confirmed` vs `payment.captured` drift | P0 |
| Booking mutation | bot-platform | Ayla/YClients | bot-platform still writes local booking and YClients | P0 |
| Payment create | bot-platform | Ayla | Auth contract mismatch | P0 |
| Payment retry | bot-platform | Ayla | Ayla internal endpoint exists; bot client callback still TODO | P1/P0 |
| Certificate payment | bot-platform | Ayla | bot expects generic payment; Ayla inspected model is appointment-bound | P0 |
| Internal auth: recommendations | bot-platform | Ayla | bot sends `AYLA_SERVICE_TOKEN`; Ayla expects `AYLA_INTERNAL_API_TOKEN` | P0 |
| Internal auth: nutrition | bot-platform | Ayla | same shared secret has different setting names | P1 |
| Internal auth: payment retry | bot-platform | Ayla | bot setting for `AYLA_INTERNAL_API_TOKEN` not declared in inspected settings | P0 |
| Nutrition internal API | bot-platform | Ayla | Separate token/header model | P1 |
| Catalog recommendations | bot-platform | Ayla | Possible `/api/v1` path drift | P1 |
| Ayla URL building | bot-platform | Ayla | clients use different assumptions about whether `AYLA_BASE_URL` includes `/api/v1` | P1/P0 |
| Profile fetch route | bot-platform | Ayla | bot calls `/api/v1/users/{user_id}`; inspected Ayla routes expose `/api/v1/users/me/` only | P1/P0 |
| Payment response shape | bot-platform | Ayla | bot expects `checkout_url`; Ayla returns `confirmation_url` | P0 |
| AI core library | Ayla + bot-platform | ayla-ai-core | Version drift | P0 |
| AI memory/profile | Ayla + bot-platform | both | Ownership unclear | P1 |
| Mobile AI chat history | Ayla mobile/API | bot-platform target AI owner | Ayla still exposes active `/api/v1/ai/chat/` and stores `ai.Conversation` | P1 |
| Memory delete/export | user-facing surfaces | Ayla + bot-platform | Ayla context delete and bot `forget_all` are separate semantics | P1 |
| Sensitive AI memory | Ayla + bot-platform | user/privacy layer | Ayla personal context has simpler fields; bot has red/yellow/green controls | P1 |
| Account delete -> AI memory delete | Ayla | bot-platform | no inspected durable cross-service deletion request | P1 |
| Data export aggregation | user-facing surfaces | Ayla + bot-platform | bot export omits `MemoryEntry`; Ayla export endpoint not found in inspected code | P1 |
| Catalog source of truth | Ayla target / mysite legacy | bot-platform | bot mirror still syncs from `mysite` while Ayla has canonical `Service` | P0 |
| Schedule source of truth | Ayla target | bot-platform | bot-platform still owns writable schedule models and local slot resolver | P0 |
| `service.updated` | Ayla | bot-platform | bot consumer exists; Ayla publisher/topic not found | P0 |
| `master.schedule.updated` | Ayla | bot-platform | bot consumer exists; Ayla publisher/topic not found | P0 |
| Slot availability | Ayla target | mini app / bot booking | bot computes customer-facing slots locally from mirror data | P0 |
| YClients booking writes | Ayla target | YClients | bot-platform currently creates/cancels/reschedules directly | P0 |
| YClients webhooks | YClients | Ayla target / bot-platform current | webhook terminates in bot-platform and writes local booking/reminder state | P0 |
| YClients mode switch | Ayla | Ayla booking engine | `booking_source='yclients'` exists, but adapter/runtime branch not built | P0 |
| Appointment reminders | Ayla + bot-platform | users | Ayla and bot-platform both schedule/send reminders from different state | P0 |
| Chat reminder state check | Ayla | bot-platform | Ayla-path reminders can send without local `BookingRequest` state re-check | P0 |
| Notification preferences / quiet hours | user/master settings | dispatchers | bot prefs exist but consumer-side gating is not wired everywhere | P1/P0 |
| Post-visit followups / aftercare | Ayla + bot-platform | users | both services have post-visit messaging with different suppression context | P1/P0 |
| Domain audit trail | Ayla + bot-platform | operators/compliance | bot has `AuditLog`; Ayla has domain-specific ledgers but no unified audit surface | P1/P0 |
| Cross-service event delivery status | Ayla | bot-platform/operators | Ayla local `processed_at` and bot `IngestDedupe` are separate truths | P0 |
| Correlation ids | all services | logs/audit/dashboard | no proven end-to-end id across booking, payment, reminders, notifications, and AI | P0 |
| DLQ / replay | Ayla + bot-platform | operators | bot eventbus, nutrition outbox, appointment outbox, notifications, and AdminTask use different failure models | P0 |
| Product analytics events | mobile/Ayla/bot-platform | product/BI | analytics tables/events are separate from audit and delivery ledgers | P1 |
| AI runtime telemetry | ayla-ai-core consumers | host apps/operators | library logs include tenant context, but durable trace ownership belongs to host apps | P1 |
| Tenant/provider canonical identity | Ayla | bot-platform / AI / mobile | Ayla and bot-platform have separate `Tenant` models; shared UUID vs mapping table is not documented | P0 |
| Master/specialist canonical identity | Ayla | bot-platform / AI | Ayla `SpecialistProfile` and bot `CatalogMaster` can drift without a single projection contract | P0 |
| Active tenant context | mobile / mini app / bot / events | Ayla + bot-platform | `X-Tenant`, JWT tenant claim, MAX initData, bot tenant slug, and event `tenant_id` are different entry contracts | P0 |
| Provider/staff roles | Ayla + bot-platform | provider/admin/master surfaces | Ayla `User.role`, Ayla `TenantUserRelationship`, bot `TenantStaff`, and bot `CatalogMaster` are separate role sources | P0 |
| Tenant relationship revocation | Ayla | bot-platform | Ayla revoke emits an event, but bot role/master projections need explicit disable/sync contract | P0 |
| Marketplace vs tenant-scoped booking | Ayla | mobile / bot / AI | booking may follow specialist tenant rather than request tenant; intended behavior needs an explicit contract | P0 |
| AI candidate tenant boundary | host apps | ayla-ai-core | core can validate tenant-aware inputs, but cannot prove host selected the correct tenant-scoped candidates | P1/P0 |
| Account deletion | Ayla | bot-platform / AI / notifications | Ayla local delete does not create an inspected durable bot-platform deletion operation | P0 |
| Data export aggregation | Ayla + bot-platform | user-facing privacy surface | bot export is channel-local; aggregate Ayla+bot export endpoint not found | P0 |
| AI memory deletion/export | bot-platform target / Ayla temporary context | user privacy surface | MemoryEntry schema exists, but inspected privacy tool exports/deletes only BotUser conversations/consents | P0 |
| Chat history deletion | Ayla + bot-platform | user privacy surface | Ayla conversation soft-delete and bot conversation hard-delete have different semantics | P1/P0 |
| Nutrition / wellness deletion | Ayla | user privacy surface | food scans, logs, profiles, water entries, images, and cross-domain insights have mixed retention rules | P0 |
| Consent retention | bot-platform | legal/privacy audit | ConsentRecord is append-only by intent but cascades on BotUser hard-delete | P1/P0 |
| Analytics after user delete | Ayla | BI/privacy export | `AnalyticsEvent.actor` becomes null, preserving BI but losing export linkage | P1 |
| Event fixture compatibility | Ayla | bot-platform | local tests use synthetic payloads; no shared fixture proves producer and consumer agree | P0 |
| REST client/provider contract | bot-platform | Ayla | clients and endpoints are tested separately, mostly with mocks | P0 |
| Unified CI compatibility | Ayla + bot-platform + ai-core | release process | CI is repo-local; no observed cross-repo compatibility gate | P0 |
| Dev E2E smoke | deployed dev stack | operators/release | existing smokes do not prove booking/payment bot boundary | P0 |
| API spec drift | API docs | Ayla + bot clients | spec review is manual; no automated drift gate | P1/P0 |
| ai-core release train | host repos | ayla-ai-core | version checks exist, but release workflow still shows historical/runtime install drift risk | P1/P0 |
| Privacy saga smoke | Ayla | bot-platform | no cross-service delete/export smoke found | P0 |

## Immediate Stabilization Plan

### Step 1. Freeze Contract Drift

Create and enforce a checked contract table for:

- event names and versions
- REST paths
- auth headers
- required env vars
- owner service
- consumer handlers

### Step 2. Fix P0 Integration Breaks

Recommended order:

1. Event taxonomy alignment.
2. Payment create auth/path decision.
3. Booking mutation ownership.
4. Outbox delivery to bot-platform.
5. Catalog/schedule/slot source-of-truth decision.
6. YClients ownership decision and kill-switch for direct bot writes.
7. Notification/reminder ownership matrix and duplicate-send suppression.
8. Provider/master/tenant boundary contract and projection mapping.
9. User data lifecycle, privacy scopes, and cross-service delete/export saga.
10. Shared contract fixtures and cross-repo E2E smoke gate.
11. Unified observability/audit ledger for P0 flows.
12. `ayla-ai-core` version alignment.

### Step 3. Add E2E Smoke Tests

Minimum smoke coverage:

- Ayla creates appointment -> bot-platform receives `booking.created`.
- Ayla captures/confirms payment -> bot-platform payment consumer handles it.
- bot-platform calls Ayla recommendations endpoint successfully.
- bot-platform payment create/retry live-mode contract works or is explicitly disabled.
- both services import the same `ayla-ai-core` version.

## Open Questions

1. Should bot-platform be allowed to create new payments, or only retry/display payments created by Ayla?
2. Should `booking.confirmed` remain a cross-service event, or should confirmation be represented by payment lifecycle events?
3. Should bot-platform keep `BookingRequest` only as a mirror/reminder context, or should it be fully deprecated?
4. Which token model is the target: long-lived bearer, `X-Service-Token`, HMAC, or short-lived service JWT?
5. Which service owns user-facing AI conversation history for mobile Ayla chat?
6. Should Ayla `/api/v1/ai/chat/` be retired, proxied to bot-platform, or kept as a separate product surface with no shared memory promise?
7. What should "delete my Ayla memory" delete across Ayla backend and bot-platform?
8. Should "export my data" be one aggregated operation, or separate "Ayla account export" and "AI/channel export" operations?
9. Should account deletion automatically delete AI memory, or should audit/business retention keep some scoped records with anonymization?
10. Should customer-facing slot reads always call Ayla, or may bot-platform serve cached slots with strict invalidation guarantees?
11. When does `mysite` stop being a catalog source for bot-platform?
12. Should `MasterService` remain editable in bot-platform, or become a read-only projection of Ayla `Service` ownership?
13. Which service owns YClients slot/schedule integration while Ayla `booking_source=yclients` is present but the inspected code still says the repo has no YClients integration?
14. Is YClients a long-term supported booking source, or a legacy dependency to migrate away from?
15. Should the existing bot-platform YClients webhook be disabled, forwarded to Ayla, or kept only for non-Ayla tenants?
16. Where should YClients credentials live: bot-platform env vars, Ayla tenant/provider settings, or a separate secrets service?
17. Which reminder set is product-approved: Ayla T-1h push, bot T-24h/T-2h chat reminders, or both?
18. Should bot-platform ask Ayla for a fresh appointment snapshot immediately before sending any appointment reminder?
19. Where is the canonical opt-out/quiet-hours decision made for cross-channel notifications?
20. Should post-visit review/aftercare be owned by Ayla only, bot-platform only, or split by channel with shared suppression rules?
21. Which system owns the operator incident dashboard for booking/payment/notification failures?
22. What is the canonical `correlation_id` propagation rule across REST, outbox, eventbus, Celery, notification delivery, and AI calls?
23. Which event types are compliance audit, which are product analytics, and which are integration delivery telemetry?
24. Which failure states require alerting, manual review, replay, or silent retention: `FAILED`, `DLQ`, skipped outbox, duplicate ingest, stale reminder, and outbound `AdminTask`?
25. Is the tenant/provider id physically the same UUID in Ayla and bot-platform, or should bot-platform store an explicit Ayla-to-bot tenant mapping?
26. Is Ayla `User.role` legacy, or can it still grant provider permissions without `TenantUserRelationship`?
27. Can marketplace booking intentionally cross the active request tenant, and what should `request_tenant_id` mean in that case?
28. Are bot-platform `TenantStaff` roles projections of Ayla tenant roles, or intentionally bot-local operational roles?
29. What should happen in bot-platform when Ayla revokes a customer/staff/master tenant relationship?
30. Should active `SpecialistProfile.tenant` and `Service.tenant` become non-null before production multi-provider rollout?
31. What exact user-facing operations should exist: delete account, delete AI memory, delete chat history, delete channel data, delete wellness data, export all data?
32. Which service owns the public privacy request status page/API?
33. Should Ayla account deletion block until bot-platform confirms deletion/export contribution, or proceed asynchronously with retry/DLQ?
34. Which records are retained for legal/payment/audit reasons after account deletion, and how are they anonymized?
35. Should bot-platform `ConsentRecord` survive `BotUser` deletion as anonymized legal history, or is `AuditLog` enough?
36. Should Ayla nutrition/food photo data be deleted as part of account deletion, separately controlled as wellness deletion, or retained with anonymization?
37. Where should shared contract fixtures live: Ayla repo, bot-platform repo, ai-core repo, separate contracts repo, or generated artifact?
38. Which CI pipeline owns the cross-repo compatibility gate?
39. Should a PR to one repo be blocked if it breaks compatibility with the current `dev` branch of another repo?
40. Which smoke tests block deployment, and which are warning-only diagnostics?
41. How should CI mock external providers while keeping the Ayla/bot boundary real?
42. Who approves intentional breaking contract changes and fixture updates?

## Evidence Log

Add future findings here with:

- date
- repository
- file path
- finding
- impact
- recommended fix
