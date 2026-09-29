# WAVE1_T02_CONTRACT_GAPS_DECISION

> Owner verdict: **ACK AFTER FIX**
> Scope: BOT-side contract gaps only. No Backend topics, no deploy, no PR-T02-2 start until this document is signed off.

---

## 1. Backend Release Revision

| Item | Required baseline | Local fact |
|------|-------------------|------------|
| Backend `origin/dev` | `566fe19b` | **Not present** in this repository or in `.codex-worktrees/admin-surface-spec`. Cannot verify Backend emit code locally. |
| BOT `origin/dev` | `a7aa237fc50ad7dc6826021a2289bfe41ff425b2` | Current checkout is `87429d4d2` (ahead of the BOT baseline). The four pilot handlers exist and are registered. |

Required Backend revision **must contain**:

- `booking.created`
- `booking.cancelled`
- `appointment.rescheduled`
- dual-emission legacy `booking.rescheduled`

**Decision:** before any PR-T02-2 code is written, the Backend team must confirm the exact target revision and the emitted status vocabulary. If the target Backend revision is not `566fe19b` or another revision that emits the same shape, the contract matrix must be rebuilt.

---

## 2. Status Vocabulary Decision

### Gap

- Backend `booking.created` emits `status = "awaiting_payment"` (observed in `apps/miniapp/src/lib/booking-status.ts`, `apps/miniapp_api/views.py`, drift-audit docs).
- BOT contract `event-contract.md §3.1` and `RemoteBookingProxy.Status` know only: `confirmed`, `pending_payment`, `tentative`.
- Current consumer (`apps/eventbus/consumers/booking.py:299`) writes `data["status"]` verbatim, so an `awaiting_payment` event would land as an invalid enum value and would be redacted in the DLQ allowlist (`apps/eventbus/ingest_redaction.py:82-153`).

### Options evaluated

| Option | Effort / risk | Verdict |
|--------|---------------|---------|
| A. Backend renames `awaiting_payment → pending_payment` | Requires Ayla code change + deploy. Delays pilot. | Rejected for pilot. |
| B. BOT extends public enum with `awaiting_payment` | Permanently accepts a non-contract value; splits the cross-repo vocabulary. | Rejected. |
| C. **BOT normalizes producer vocabulary at ingest** | One-file consumer change; keeps the accepted contract intact; matches owner recommendation. | **Accepted.** |

### Decision: Option C — BOT normalizes at ingest

Mapping (single source of truth inside `handle_booking_created`):

| Producer raw value | Normalized BOT value | UI mapping note |
|--------------------|----------------------|-----------------|
| `awaiting_payment` | `pending_payment` | `apps/miniapp/src/lib/booking-status.ts` must also alias `pending_payment → awaiting_payment` (currently aliases `payment_pending` only). |
| `pending_payment` | `pending_payment` | pass-through |
| `confirmed` | `confirmed` | pass-through |
| `tentative` | `tentative` | pass-through |
| anything else | **fail-closed** | raise a controlled validation error → `HANDLER_EXCEPTION` → 500 → retry budget → DLQ. |

Requirements:

1. Mapping is explicit and unit-tested for every allowed value.
2. Unknown status is fail-closed (no silent acceptance of arbitrary strings).
3. Raw producer value is preserved in structured logs and in the event audit trail before normalization.
4. `awaiting_payment` is added to `ingest_redaction.py ALLOWED_ENUM_VALUES` so a failed event can still be forensically inspected in `IngestDLQ.raw_body`.
5. `RemoteBookingProxy` and all downstream reads use the normalized value.
6. The contract fixture `booking.created.v1.json` stays on `confirmed`; new tests exercise the normalization path with `awaiting_payment`.

---

## 3. Booking Confirmation Lifecycle

### Actual lifecycle (as observed from BOT code + contract)

```text
booking.created(status=awaiting_payment)
  → booking.confirmed        (payment hold succeeded / pre-pay confirmation)
  → booking.cancelled        (user cancels before payment, or payment hold expired)
  → booking.no_show          (terminal state after visit)
```

- `booking.confirmed` flips `RemoteBookingProxy.status` to `confirmed` (`apps/eventbus/consumers/booking.py:1008`).
- `booking.confirmed` carrying `payment_id` creates an `authorized` `PaymentMirror` row (`apps/eventbus/consumers/booking.py:1017-1027`).
- A record can remain in `pending_payment` state until payment succeeds or the hold expires.
- Cancellation before payment is handled by `booking.cancelled` with `reason_code` from the contract set (`user_changed_plans`, `payment_hold_expired`, etc.).
- The exact payment-hold timeout is owned by Backend; BOT only reacts to events.

### Owner Decision OD-T02-5

- **Include `booking.confirmed` in the controlled pilot.**
- **Do not treat `booking.created` as final** when the normalized status is `pending_payment`.
- **Do not build an "awaiting-payment" reminder flow**; rely on `booking.confirmed` to transition the proxy and trigger reminder creation (see §4).
- `booking.no_show` is intentionally **out of scope** for this pilot topic set.

---

## 4. Reminder Eligibility

### Current paths

- `handle_booking_created` schedules T-24h + T-2h reminders unconditionally if a linked `BotUser` exists (`apps/eventbus/consumers/booking.py:166-209`).
- `handle_booking_confirmed` does **not** create reminders; it assumes `booking.created` already did (`apps/eventbus/consumers/booking.py:978-979`).
- `handle_booking_cancelled` cancels pending reminders (`apps/eventbus/consumers/booking.py:366-426`).
- `handle_appointment_rescheduled_canonical` re-pegs pending reminders (`apps/eventbus/consumers/booking.py:891`).

### Problem

If `booking.created` arrives with `awaiting_payment`/`pending_payment`, reminders are created for an unpaid booking and could fire before payment is captured.

### Owner Decision OD-T02-6

- **Reminders are created only for confirmed bookings.**
- In `handle_booking_created`:
  - If normalized `status == confirmed`: schedule T-24h + T-2h reminders as today.
  - If normalized `status` is `pending_payment` or `tentative`: **skip reminder creation** (do not create disabled rows; simpler and fewer states to reason about).
- In `handle_booking_confirmed`:
  - If reminders for the appointment do not yet exist, create them idempotently for the linked `BotUser`.
  - If they already exist, leave them unchanged.
- `handle_booking_cancelled` and `handle_appointment_rescheduled_canonical` keep their existing reminder behaviour.

---

## 5. Tenant ID Preflight

### Scope

Backend DB must satisfy the following before the pilot flip:

```text
appointments_with_null_tenant = 0
outbox_rows_for_enabled_topics_with_null_tenant = 0
```

Enabled topics for this check:

- `booking.created`
- `booking.confirmed`
- `booking.cancelled`
- `appointment.rescheduled`

### Canonical pilot tenant UUID

The UUID `9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c` used in BOT tests and fixtures is **not** the production pilot tenant. Operations must publish the real `PILOT_TENANT_ID` separately.

### Queries to run on Backend DB

```sql
-- 1. Appointment rows without tenant
SELECT COUNT(*) FROM appointments WHERE tenant_id IS NULL;

-- 2. Outbox rows for enabled pilot topics without tenant
SELECT COUNT(*)
FROM outbox
WHERE tenant_id IS NULL
  AND event_name IN (
      'booking.created',
      'booking.confirmed',
      'booking.cancelled',
      'appointment.rescheduled'
  );

-- 3. Breakdown by topic (for triage)
SELECT event_name, COUNT(*)
FROM outbox
WHERE tenant_id IS NULL
  AND event_name IN (...)
GROUP BY event_name;
```

### Safe backfill plan

1. Identify source: legacy rows predating `tenant_id` backfill, solo-master edge cases, or migration gaps.
2. Produce a deterministic mapping script (e.g. map by `salon_id` → `tenant_id`) and run it in a transaction.
3. Re-run the three queries above and confirm zero.
4. Add a `CHECK (tenant_id IS NOT NULL)` or `NOT NULL` constraint on `appointments.tenant_id` and the outbox table **after** counts are zero, to prevent re-introduction.
5. Keep the rollback script (inverse UPDATE) until 24 h after the flip.

### Current state

This preflight **cannot be completed from the BOT repository**. A Backend PR/runbook is required. Do **not** perform the backfill in this phase without a separate plan + rollback.

---

## 6. Final Pilot Topic Set

| # | Topic | Version | Producer | Required payload | BOT handler | Normalized status behaviour | Ordering dependency |
|---|-------|---------|----------|------------------|-------------|-----------------------------|---------------------|
| 1 | `booking.cancelled` | 1 | Ayla | `appointment_id`, `cancelled_by`, `reason_code`, `cancelled_at` | `handle_booking_cancelled` | n/a | Safe to enable before `booking.created`; out-of-order drops are handled. |
| 2 | `booking.created` | 1 | Ayla | `appointment_id`, `specialist_id`, `service_id`, `start_at`, `end_at`, `status`, `price_total`, `source` | `handle_booking_created` | `awaiting_payment → pending_payment`; unknown values fail-closed. | None. |
| 3 | `booking.confirmed` | 1 | Ayla | `appointment_id`, `payment_id` (optional `amount`) | `handle_booking_confirmed` | Sets proxy to `confirmed`; creates reminders if missing. | Requires `booking.created` (proxy must exist; handler is no-op if missing). |
| 4 | `appointment.rescheduled` | 1 | Ayla | `appointment_id`, `version`, `previous_version`, `revision_id`, `changed_fields`, `actor`, optional `starts_at`/`previous_starts_at` | `handle_appointment_rescheduled_canonical` | n/a | Requires proxy from `booking.created`; raises `CanonicalReschedulePendingProxyError` if missing, retrying until proxy exists. |

**Excluded from controlled pilot:** `booking.rescheduled` — legacy handler remains registered because Backend dual-emits it, but it is **not** a pilot topic and will be removed when Backend stops dual emission.

---

## 7. Enable Order

### Sequence

1. `booking.cancelled`
2. `booking.created`
3. `booking.confirmed`
4. `appointment.rescheduled`

### Rationale

- `booking.cancelled` first: handler drops out-of-order cancellations cleanly, so enabling it before `booking.created` is safe.
- `booking.created` second: establishes the `RemoteBookingProxy` and normalized status.
- `booking.confirmed` third: depends on the proxy created by `booking.created`; also triggers reminder creation for previously pending bookings.
- `appointment.rescheduled` last: strict dependency on an existing proxy; handler raises if proxy is missing, relying on Ayla retry to converge.

### Replay of old outbox rows

After the flip, Ayla will drain any pending outbox rows for the enabled topics. BOT's `IngestDedupe` table guards against duplicate processing. If a row was previously DLQ'd, manual ops replay is required.

---

## 8. Retry/DLQ Reality

### BOT ingest endpoint — what it actually returns

| Condition | HTTP status | Retry by Ayla? | DLQ on BOT side? | Notes |
|-----------|-------------|----------------|------------------|-------|
| OK / duplicate | 200 | No | No | Duplicate returns `{"duplicate": true}`. |
| Rate limited | 429 | Back off | No | Retry-After header not currently set on 429. |
| HMAC/timestamp fail | 401 | No | No | Ayla must not retry 4xx. |
| Malformed JSON / missing envelope field | 400 | No | No | Publisher bug. |
| Unknown `event_name` | 422 | No | **Yes** (`reason=unknown_event_name`) | Permanent contract drift. |
| Unknown `event_version` | 422 | No | **Yes** (`reason=unknown_event_version`) | Permanent. |
| `event_id` too long | 422 | No | **Yes** (`reason=event_id_too_long`) | Fail-fast. |
| Handler exception | 500 | **Yes** | **After threshold** (`reason=handler_exception`) | Threshold driven by `EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD` (default 3). |
| Saturated executor | 503 | **Yes** | No | Retry-After: 5s. |

### Handler failure tracker (BOT code)

- `HandlerFailureTracker` counts attempts per `(event_id, handler_name, outcome)`.
- Default threshold is `3` (`apps/eventbus/ingest_dispatcher.py:405`).
- Crossing the threshold writes/updates an `IngestDLQ` row with redacted `raw_body`.
- **There is no automatic replay** for inbound DLQ rows; replay is manual ops (re-publish from Ayla or clear `IngestDedupe`/`HandlerFailureTracker`).

### Ayla publisher retry policy

- The BOT repository **does not implement** Ayla's outbox dispatcher.
- `event-contract.md §6.3/§6.4` states: max 5 attempts, exponential backoff `1s, 5s, 30s, 120s, 600s`, no retry on 4xx, PagerDuty `ayla-events` after dead-letter.
- These values are **not enforced by BOT code** and must be treated as Ayla-side configuration to be confirmed from the Backend repo.

### Alerting

- No PagerDuty call exists in BOT code.
- BOT emits `system.module.health.degraded` when its **outbound** dispatcher dead-letters rows (`apps/eventbus/dispatcher.py:218-239`).
- Inbound DLQ alerting relies on the observability stack consuming `IngestDLQ` rows and `HandlerFailureTracker` entries.

---

## 9. Required PRs

### PR-T02-2 (BOT)

1. Status normalization in `handle_booking_created`.
2. Add `awaiting_payment` to `ingest_redaction.py ALLOWED_ENUM_VALUES` for forensic DLQ visibility.
3. Gate reminder creation on `status == confirmed` in `handle_booking_created`.
4. Create reminders idempotently in `handle_booking_confirmed` when missing.
5. Add/extend unit tests covering all normalized statuses, unknown-status fail-closed behaviour, and reminder eligibility.
6. Update `apps/miniapp/src/lib/booking-status.ts` `BACKEND_ALIAS_MAP` to map `pending_payment → awaiting_payment`.
7. Add contract fixtures and manifest entries for `booking.cancelled.v1.json` and `appointment.rescheduled.v1.json` (currently missing from `tests/fixtures/contracts/`).

### Backend preflight PR / runbook (not a pilot topic PR)

1. Tenant-null audit queries and report.
2. Safe backfill script + rollback script.
3. `NOT NULL` constraint on `appointments.tenant_id` (and outbox `tenant_id` for salon-scoped events) after backfill.

---

## 10. Owner Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| **OD-T02-5** | Include `booking.confirmed` in pilot; normalize `awaiting_payment → pending_payment`; do not treat `booking.created` as final for unpaid bookings. | Without `booking.confirmed` the proxy would stay in a non-confirmed state and reminders would be wrong. |
| **OD-T02-6** | Reminders only for `confirmed` bookings; `booking.confirmed` creates reminders if missing. | Prevents reminders firing for unpaid bookings. |
| **OD-T02-7** | Keep the legacy `booking.rescheduled` handler registered during pilot as a compatibility sink, but do **not** list it as a pilot topic. Remove only after Backend stops dual emission. | Avoids DLQ spam from current Backend dual-emission behaviour. |
| **OD-T02-8** | Backend tenant-null preflight must show `appointments_with_null_tenant = 0` and `outbox_rows_for_enabled_topics_with_null_tenant = 0` before flip. | Prevents cross-tenant data leakage and dispatch failures. |

---

## 11. Final ACK Conditions

Before status moves to **READY FOR PR-T02-2**, the following must be true:

- [ ] Owner signs off OD-T02-5, OD-T02-6, OD-T02-7, OD-T02-8.
- [ ] Backend team confirms the target Backend revision emits exactly the four pilot topics and the `awaiting_payment` status.
- [ ] Backend tenant-null preflight returns zero for both counts.
- [ ] PR-T02-2 implementation plan covers status normalization, reminder gating, DLQ redaction, tests, miniapp alias, and new contract fixtures.
- [ ] No deploy and no Backend topic changes are started.

## Final Status

**READY AFTER OWNER DECISION**

> Once the owner decisions above are confirmed and the Backend preflight is green, this becomes **READY FOR PR-T02-2**.
