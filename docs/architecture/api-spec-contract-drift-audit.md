## Status update — 2026-06-01 (tech-lead reconciliation)

This section overlays current status onto the original audit below. API Spec v2.0 was fully read on 2026-06-01. The body of this document is unchanged.

### Confirmations

- **Canonical source priority confirmed:** code wins over spec ("spec follows reality").
- **Confirmed deviations:**
  - cancel/complete/reschedule are separate POST endpoints (spec showed `PATCH /status`);
  - refresh URL is `/auth/token/refresh/`;
  - social-auth uses provider-in-path;
  - `AppointmentStatus` enum in spec lacks `awaiting_payment` (code has it);
  - payment-create canonical body = `{appointment_id, return_url}`.

### Auth fragmentation (from contract-matrix code read)

Three service-to-service conventions exist in bot's Ayla clients:

- `Bearer {AYLA_INTERNAL_API_TOKEN}` — payments;
- `Bearer {AYLA_SERVICE_TOKEN}` — recommendations / profile;
- `X-Service-Token {AYLA_SERVICE_TOKEN}` — nutrition.

None match the RS256 s2s token in `jwt-contract.md`. → needs a dedicated service-to-service auth ADR.

### Pre-ADR-0009 legacy in spec

API Spec v2.0 (dated 2026-03-31, before ADR-0009) exposes Ayla-side `/ai/chat`, `/ai/conversations`, and `/users/me/personal-context` (7 fields shipped). These AI/memory surfaces are reassigned to bot-platform by ADR-0009; flag as deprecate/reconcile (memory-ownership ADR).

### Nutrition

Nutrition internal endpoints (scan/profile/summary/water) confirmed LIVE on Ayla dev (verified 2026-06-01).

---

# API Spec Contract Drift Audit

## Status

Living document. Initial version: 2026-05-28.

This report compares local API specification PDFs with the current inspected code in:

| Source | Role |
| --- | --- |
| `D:\Мои документы\BeautyGo\Api\_API_Specification_v2.0__ayla.pdf` | Main API spec, dated 2026-03-31 |
| `D:\Мои документы\BeautyGo\Api\_API_Changelog__BeautyGO_Backend.pdf` | Backend changelog with later implementation decisions |
| `D:\Мои документы\BeautyGo\Api\__API_Contract_Audit__2026-04-13.pdf` | Earlier three-way contract audit |
| `D:\Мои документы\BeautyGo\Api\__Implementation_Status__Deviations__2026-04-27.pdf` | Documented implementation deviations |
| `D:\Мои документы\BeautyGo\Api\Ayla_Backend__Phase_3_Nutrition_Endpoints_Spec.pdf` | Bot-facing nutrition internal API spec |
| `Ayla/djangoproject-codex` | Current Ayla backend implementation |
| `ai-bot-platform-codex` | Current bot-platform clients and consumers |

## Executive Summary

The PDF documents are useful, but they are not one synchronized source of truth.

The main conflict is between:

- the older `API Specification v2.0`;
- later backend decisions documented in `API Changelog` and `Implementation Status & Deviations`;
- current bot-platform clients that were implemented against mixed assumptions.

Important principle: do not blindly change backend code to match the old v2.0 spec. Several deviations are intentional and already documented. The safer approach is to make the current Ayla backend plus documented deviations the canonical contract, then update the spec and bot clients around that.

## Priority Scale

| Priority | Meaning |
| --- | --- |
| P0 | Can break live booking/payment/profile integration |
| P1 | High drift risk; can cause 404/403/DLQ/runtime failures |
| P2 | Documentation, maintainability, or onboarding issue |

## Findings

### P0-1. `AYLA_BASE_URL` Meaning Is Not Stable

**Status:** Open

**What the PDF says:** `API Specification v2.0` defines base URL as `https://api.ayla.app/api/v1/`.

**What code does:**

- Some bot-platform clients treat `AYLA_BASE_URL` as host-only and append `/api/v1/...`.
- `recommendations_client` treats it closer to API-root and appends `/internal/me/catalog/recommendations/`.

**Impact:** If ops sets `AYLA_BASE_URL` to include `/api/v1`, nutrition/payment clients can become `.../api/v1/api/v1/...`. If ops sets it host-only, recommendations becomes `.../internal/...` and misses `/api/v1`.

**Recommendation:** Define `AYLA_BASE_URL` as host-only, for example `https://dev.gobeauty.site`, and introduce a shared bot-platform Ayla URL builder that always inserts `/api/v1`.

### P0-2. Recommendations Path Is Wrong In Bot-Platform

**Status:** Open

**Ayla route:** `/api/v1/internal/me/catalog/recommendations/`

**bot-platform route:** `/internal/me/catalog/recommendations/`

**Evidence:** Ayla mounts `users.catalog_recommendations_urls` under `/api/v1/internal/me/catalog/recommendations/`. bot-platform `recommendations_client` builds `{AYLA_BASE_URL}/internal/me/catalog/recommendations/`.

**Impact:** Recommendations can return 404 even when auth is correct.

**Recommendation:** Fix bot client path through the shared URL builder and update tests that currently assert the wrong URL.

### P0-3. Payment Create Contract Is Compatible With Ayla Spec, But Not With Bot-Platform

**Status:** Open

**Spec/Ayla contract:**

- `POST /api/v1/payments/create/`
- body: `appointment_id`, optional `return_url`
- response: `payment_id`, `confirmation_url`, `amount`

**bot-platform behavior:**

- sends `amount_rub`, `description`, `kind`, `recipient_name`, `buyer_email`
- expects `checkout_url`
- uses the endpoint for certificate purchase

**Impact:** This is not a small field mismatch. It is a different product flow using an appointment-payment endpoint.

**Recommendation:** Either add a separate Ayla-owned certificate/order payment endpoint and model, or disable bot certificate purchase in live mode. Also align response field naming: prefer Ayla's `confirmation_url`.

### P0-4. Payment Event Vocabulary Is Not Covered By REST Spec

**Status:** Open

**Spec public payment status:** `pending`, `succeeded`, `failed`, `refunded`.

**Ayla internal payment state:** `pending`, `authorized`, `paid`, `failed`, `refunded`, `partially_refunded`.

**bot-platform event consumers expect:** `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`.

**Impact:** REST API status, internal payment state, and cross-service event names are different layers, but documentation currently blurs them. This has already produced `payment.confirmed` vs `payment.captured` drift.

**Recommendation:** Maintain a separate event contract table. Do not rely on REST API spec to define event lifecycle.

### P1-1. Appointments Spec v2.0 Is Stale

**Status:** Open

**Spec v2.0 says:** `PATCH /appointments/{id}/status` and `PATCH /appointments/{id}/reschedule`.

**Changelog / deviations / code say:**

- `POST /api/v1/appointments/{id}/cancel/`
- `POST /api/v1/appointments/{id}/complete/`
- `POST /api/v1/appointments/{id}/reschedule/`

**Impact:** A frontend or bot developer reading only v2.0 can implement wrong endpoints.

**Recommendation:** Update `API Specification v2.0` to follow reality. The deviation is intentional and documented, so do not refactor backend back to generic PATCH just for spec purity.

### P1-2. Appointment Status Enum Is Incomplete In Main Spec

**Status:** Open

**Current Ayla code uses:**

- `pending`
- `awaiting_payment`
- `confirmed`
- `in_progress`
- `completed`
- `cancelled`
- `no_show`

**Spec/changelog coverage:** v2.0 is older; changelog adds `awaiting_payment`; current code also has `in_progress`.

**Impact:** UI and bot may treat valid states as unknown.

**Recommendation:** Update API enum and screen docs to include the full current lifecycle.

### P1-3. Profile Fetch Route Exists In Event Contract, But Not In Inspected Ayla Routes

**Status:** Open

**bot-platform expects:** `GET /api/v1/users/{user_id}`

**Inspected Ayla routes expose:** `/api/v1/users/me/` and related `me/*` endpoints.

**Impact:** `user.profile.updated` consumer can fail every time it tries to fetch profile fields.

**Recommendation:** Add an explicit internal profile endpoint for bot-platform, or change the consumer to call a real route. The endpoint must expose only the approved PII subset.

### P1-4. Nutrition Internal API Mostly Matches Phase 3 Spec, But Secret Naming Drifts

**Status:** Open

**Nutrition spec says:** `/api/v1/nutrition/internal/...` with `X-Service-Token: <NUTRITION_SERVICE_TOKEN>` and `X-External-User-ID`.

**Ayla code:** matches this pattern.

**bot-platform code:** sends `X-Service-Token`, but the local setting is named `AYLA_SERVICE_TOKEN`.

**Impact:** Works only if ops knows to keep differently named secrets equal across services.

**Recommendation:** Either use `NUTRITION_SERVICE_TOKEN` on both sides or migrate nutrition to the same `AYLA_INTERNAL_API_TOKEN` bearer pattern as other internal user-actor endpoints.

### P1-5. Home Endpoint Exists In Deviations, Not Main Spec

**Status:** Open

**Implementation deviations document:** `GET /api/v1/home/` exists and should be added to the main spec.

**Ayla code:** mounts `/api/v1/home/`.

**Impact:** New developers reading only v2.0 will miss a real endpoint.

**Recommendation:** Add a Home Screen section to the main API spec.

## Recommended Canonical Source Policy

Use this order until specs are cleaned up:

1. Current Ayla backend code for real route availability and response wrappers.
2. `API Changelog` and `Implementation Status & Deviations` for intentional deviations.
3. `API Specification v2.0` for broad product contract, but not as the final truth where deviations exist.
4. bot-platform clients only after they are proven against Ayla route table/OpenAPI.

## Immediate Fix Plan

1. Define `AYLA_BASE_URL` as host-only and add shared URL builder in bot-platform.
2. Fix recommendations URL and tests.
3. Fix payment response field parsing: `confirmation_url`, not `checkout_url`.
4. Decide certificate payment ownership: add Ayla endpoint/model or disable the bot skill.
5. Add internal user profile endpoint or disable profile fetch consumer.
6. Update main API spec with documented deviations.
7. Add contract tests that compare bot client paths against Ayla routes/OpenAPI.

## Do Not Mark This Fixed Until

- every bot-platform Ayla client uses one URL builder;
- `AYLA_BASE_URL` meaning is documented and tested;
- Spec v2.0 contains the documented appointment deviations;
- payment create contract is one shape end to end;
- profile sync route exists or the consumer is disabled;
- nutrition secret naming is intentional and documented;
- cross-service event contracts live outside the REST API spec.
