# WAVE1-T02 Booking Lifecycle Correctness — PR-T02-2 Design

> Status: design approved by owner after contract-gap closure.
> Scope: BOT-side code only (`ai-bot-platform`). No Backend changes, no topic enablement, no deploy.
> Base branch: `dev`.
> Working branch: `fix/wave1-t02-booking-lifecycle-correctness`.

---

## 1. Background

`scratchpad/WAVE1_T02_CONTRACT_GAPS_DECISION.md` identified four contract gaps between Ayla's booking events and the BOT consumer:

1. Backend emits `awaiting_payment`; BOT's `RemoteBookingProxy.Status` knows `pending_payment`.
2. `booking.created` with unpaid status was scheduling reminders.
3. `booking.confirmed` did not create reminders for previously pending bookings.
4. `booking.confirmed` and `booking.cancelled` silently no-op'd when the `RemoteBookingProxy` was missing, permanently losing out-of-order events.

Owner decisions OD-T02-4 through OD-T02-7 require a narrow BOT PR that fixes these lifecycle semantics without touching Backend, topics, or deploy.

---

## 2. Goals and non-goals

### Goals

- Normalize producer status vocabulary at ingest (`awaiting_payment → pending_payment`).
- Fail-closed on unknown `booking.created` status values.
- Create reminders only for `confirmed` bookings.
- Create reminders idempotently on `booking.confirmed`.
- Turn missing-proxy `booking.confirmed` / `booking.cancelled` into retryable handler failures.
- Preserve cross-tenant isolation and idempotency.
- Update contract fixtures, DLQ redaction allowlist, and the miniapp status alias.

### Non-goals

- No Backend code changes.
- No topic enablement / rollout configuration changes.
- No deploy.
- No changes to the legacy `booking.rescheduled` handler beyond keeping it registered.
- No new state-machine framework or service-layer extraction; helpers stay in the consumer module per Option A.

---

## 3. Design approach

**Option A: in-handler helpers in `apps/eventbus/consumers/booking.py`.**

The consumer module already contains the handler code, helper functions (`_schedule_reminders`, `_cancel_reminders`, etc.), and the `RemoteBookingProxy` model. We add a small set of pure helpers and typed exceptions, then call them from the three lifecycle handlers.

This keeps the PR narrow, keeps lifecycle rules visible next to the handlers, and avoids premature abstraction for a four-topic pilot.

---

## 4. New helpers and exceptions

All additions live in `apps/eventbus/consumers/booking.py`.

### 4.1 Status normalization

```python
def normalize_booking_created_status(raw_status: object) -> str:
    """Map producer booking.created status values to the BOT enum.

    Raises UnknownBookingStatusError for any value that is not a string
    or not one of the four contracted strings.
    """
    if not isinstance(raw_status, str):
        raise UnknownBookingStatusError(
            f"Unknown booking.created status type: {type(raw_status).__name__}"
        )

    mapping = {
        "awaiting_payment": RemoteBookingProxy.Status.PENDING_PAYMENT,
        "pending_payment": RemoteBookingProxy.Status.PENDING_PAYMENT,
        "confirmed": RemoteBookingProxy.Status.CONFIRMED,
        "tentative": RemoteBookingProxy.Status.TENTATIVE,
    }
    try:
        return mapping[raw_status]
    except KeyError as exc:
        raise UnknownBookingStatusError(
            f"Unknown booking.created status: {raw_status!r}"
        ) from exc
```

Rules:

- Exact-match closed enum only. No `.strip()`, `.lower()`, substring guessing, or `.replace()`.
- Non-string inputs fail-closed with `UnknownBookingStatusError`.
- Returns `RemoteBookingProxy.Status.*` string values so the proxy always stores a valid enum.

### 4.2 Reminder eligibility predicate

```python
def _is_reminder_eligible(status: str) -> bool:
    """Reminders are only created for confirmed bookings."""
    return status == RemoteBookingProxy.Status.CONFIRMED
```

### 4.3 Exceptions

All subclass `ValueError` so the existing dispatcher `HANDLER_EXCEPTION` path returns HTTP 500, rolls back the handler transaction (including the `IngestDedupe` insert), and lets Ayla retry.

```python
class UnknownBookingStatusError(ValueError):
    ""``booking.created`` carried a status outside the closed enum."""

class BookingConfirmedPendingProxyError(ValueError):
    ""``booking.confirmed`` arrived before the proxy exists."""

class BookingCancelledPendingProxyError(ValueError):
    ""``booking.cancelled`` arrived before the proxy exists."""
```

Handlers will not catch these exceptions internally.

---

## 5. Handler changes

### 5.1 `handle_booking_created`

Current flow:

1. `assert_envelope_tenant_authorized(envelope)`
2. Resolve tenant; return if unknown.
3. Parse timestamps, resolve `BotUser`.
4. Build `create_defaults` including `status = data["status"]`.
5. `get_or_create` proxy.
6. If linked `BotUser`, schedule reminders unconditionally.
7. Emit internal event.

New flow:

1. `assert_envelope_tenant_authorized(envelope)`
2. Resolve tenant; return if unknown.
3. Parse timestamps, resolve `BotUser`.
4. **Normalize status:**
   ```python
   normalized_status = normalize_booking_created_status(data.get("status"))
   ```
5. **Forensic log** when normalization changes the value:
   ```python
   if data.get("status") != normalized_status:
       logger.info(
           "eventbus.consumer.booking.created.status_normalized "
           "event_id=%s appointment_id=%s tenant_id=%s raw_status=%s normalized_status=%s",
           envelope.event_id,
           data.get("appointment_id"),
           envelope.tenant_id,
           data.get("status"),
           normalized_status,
       )
   ```
6. Build `create_defaults` with `status = normalized_status`.
7. `get_or_create` proxy.
8. If linked `BotUser` **and** `_is_reminder_eligible(normalized_status)`, schedule reminders; otherwise skip.
9. Emit internal `booking_created` event with `status = normalized_status`.

Behaviour matrix:

| Producer status | Normalized proxy status | Reminders |
|-----------------|------------------------|-----------|
| `awaiting_payment` | `pending_payment` | no |
| `pending_payment` | `pending_payment` | no |
| `confirmed` | `confirmed` | yes |
| `tentative` | `tentative` | no |
| anything else | exception → 500 | no side effects |

### 5.2 `handle_booking_confirmed`

Current flow uses a blind `RemoteBookingProxy.all_tenants.filter(appointment_id=...).update(...)`, which silently succeeds when no proxy exists and cannot enforce ordering against a later `booking.cancelled`.

New flow:

1. `assert_envelope_tenant_authorized(envelope)`
2. Resolve tenant; return if unknown.
3. **Fetch the proxy with row locking by `appointment_id` only, then enforce tenant auth:**
   ```python
   proxy = (
       RemoteBookingProxy.all_tenants
       .select_for_update()
       .filter(appointment_id=appointment_id)
       .first()
   )
   ```
   The lookup is intentionally not scoped by tenant here so that the existing `_assert_proxy_tenant` guard can still detect and block a cross-tenant spoof (a proxy for the same `appointment_id` owned by a different tenant).
4. `_assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)`.
5. If `proxy is None`, raise `BookingConfirmedPendingProxyError`.
6. Idempotency short-circuit on `last_synced_event_id == event_id`; return.
7. **Terminal-state guard:** if `proxy.status == RemoteBookingProxy.Status.CANCELLED`, log and return without changing state or `last_synced_event_id`. A late `booking.confirmed` must not resurrect a cancelled booking.
8. Update proxy:
   ```python
   proxy.status = RemoteBookingProxy.Status.CONFIRMED
   proxy.last_synced_event_id = envelope.event_id
   proxy.save(update_fields=["status", "last_synced_event_id", "synced_at"])
   ```
9. **Always call the idempotent reminder scheduler** (no `exists()` check):
   ```python
   if bot_user is not None:
       _schedule_reminders(tenant=tenant, bot_user=bot_user, appointment_id=appointment_id, start_at=proxy.start_at)
   ```
   `update_or_create` on `(ayla_appointment_id, tenant, kind)` prevents duplicates. To avoid clobbering already-sent reminders on replay/late events, `_schedule_reminders` must not include `sent_at`/`replied_at` in its `defaults`; the model defaults already set them to `NULL` on create, and existing non-NULL values must be preserved on update.
10. Upsert `PaymentMirror` if `payment_id` present (existing behaviour).
11. Emit internal `booking_confirmed` event.

### 5.3 `handle_booking_cancelled`

Current flow returns silently when `proxy is None` (out-of-order cancellation). This loses the event if `booking.created` never arrived.

New flow:

1. `assert_envelope_tenant_authorized(envelope)`
2. Resolve tenant; return if unknown.
3. **Fetch the proxy with row locking by `appointment_id` only, then enforce tenant auth:**
   ```python
   proxy = (
       RemoteBookingProxy.all_tenants
       .select_for_update()
       .filter(appointment_id=appointment_id)
       .first()
   )
   ```
   Same pattern as `handle_booking_confirmed`: no tenant filter in the SQL lookup so `_assert_proxy_tenant` can still block cross-tenant spoof attempts.
4. `_assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)`.
5. If `proxy is None`, raise `BookingCancelledPendingProxyError`.
6. Idempotency short-circuit on `last_synced_event_id == event_id`; return.
7. Update proxy:
   ```python
   proxy.status = RemoteBookingProxy.Status.CANCELLED
   proxy.last_synced_event_id = envelope.event_id
   proxy.save(update_fields=["status", "last_synced_event_id", "synced_at"])
   ```
8. Cancel PENDING reminders: `_cancel_reminders(appointment_id=appointment_id)`.
9. Emit internal `booking_cancelled` event.

### 5.4 Terminal-state rule

`CANCELLED` is treated as a terminal state for the pilot:

- A late `booking.confirmed` for a `CANCELLED` proxy is a no-op (info log, no state change, no reminder creation).
- This prevents a confirmed event that arrives out of order from resurrecting an appointment the user already cancelled.
- If Backend later introduces a genuine "rebook same appointment" flow, that will be a new event or a new version; the pilot does not support it.

---

## 6. DLQ redaction

Add `"awaiting_payment"` to `ALLOWED_ENUM_VALUES` in `apps/eventbus/ingest_redaction.py`. This is the only producer value that is not already in the BOT public enum but may legitimately appear in a failed `booking.created` event; keeping it visible in `IngestDLQ.raw_body` is required for forensic triage.

No other redaction changes.

---

## 7. Frontend alias

In `apps/miniapp/src/lib/booking-status.ts`, add to `BACKEND_ALIAS_MAP`:

```ts
pending_payment: "awaiting_payment",
```

This maps the normalized BOT status back to the existing customer-visible bucket. The map already handles `awaiting_payment` and `payment_pending`; adding `pending_payment` covers the normalized value that will now be stored in the proxy.

---

## 8. Contract fixtures

Add two canonical event fixtures:

- `tests/fixtures/contracts/booking.cancelled.v1.json`
- `tests/fixtures/contracts/appointment.rescheduled.v1.json`

Update:

- `EVENT_FIXTURES` in `tests/fixtures/contracts/__init__.py`
- `EVENT_DATA_KEYS` in `apps/eventbus/tests/test_contract_fixtures.py`
- Regenerate `MANIFEST.sha256` via `python -m tests.fixtures.contracts --write-manifest`

These fixtures close the contract-gap action items and provide regression anchors for the pilot topic set.

---

## 9. Test plan

All tests go into `apps/eventbus/tests/test_booking_consumer.py` unless noted.

### 9.1 Status normalization

- `awaiting_payment` → `pending_payment` stored in proxy.
- `pending_payment` → `pending_payment` identity.
- `confirmed` → `confirmed` identity.
- `tentative` → `tentative` identity.
- Unknown string → `UnknownBookingStatusError`, no proxy created, no reminders.
- Empty string → `UnknownBookingStatusError`.
- `None`/non-string → `UnknownBookingStatusError` (type check).
- Forensic log emitted on normalization.

### 9.2 Reminder eligibility

- `confirmed` create → exactly 2 reminders.
- `pending_payment` create → 0 reminders.
- `tentative` create → 0 reminders.
- `booking.confirmed` on pending proxy → creates reminders.
- Walk-in (`created(confirmed)` then `confirmed`) → no duplicate reminders.
- `booking.cancelled` → PENDING reminders cancelled.

### 9.3 Missing-proxy fail-loud

- `booking.confirmed` without proxy → `BookingConfirmedPendingProxyError`.
- `booking.cancelled` without proxy → `BookingCancelledPendingProxyError`.
- After the exception, no `IngestDedupe` row exists (dispatcher-level test in `test_ingest_dispatcher.py` or direct DB assertion).
- After `booking.created` lands, replay of the same `booking.confirmed` event succeeds.
- Cross-tenant existing proxy is rejected by `_assert_proxy_tenant` before any state mutation; the SQL lookup uses `appointment_id` only to preserve spoof detection.

### 9.4 Ordering scenarios

- **O1:** `created(awaiting_payment)` → proxy `pending_payment`, 0 reminders; `confirmed` → proxy `confirmed`, 2 reminders.
- **O2:** `created(confirmed)` → reminders; `confirmed` → no duplicate reminders.
- **O3:** `confirmed` before `created` → retryable failure; after `created`, replay succeeds.
- **O4:** `cancelled` before `created` → retryable failure; after `created`, replay cancels.
- **O5:** `created(awaiting_payment)` → 0 reminders; `cancelled` → proxy `cancelled`, no reminders.
- **O6:** Duplicate `created`, `confirmed`, `cancelled` → idempotent, stable final state.
- **O7:** Unknown status → retryable failure, no dedupe, no proxy.
- **O8:** `created(tentative)` → proxy `tentative`, 0 reminders until confirmed.
- **Late confirmed after cancelled:** `created(pending_payment)` → `cancelled` → `confirmed` (different event_id) → final status `cancelled`, reminders cancelled/absent.
- **Confirmed then cancelled:** `created(pending_payment)` → `confirmed` (reminders created) → `cancelled` → final status `cancelled`, reminders cancelled.

### 9.5 Regression

- Existing happy-path tests for `booking.created`, `booking.confirmed`, `booking.cancelled` remain green.
- Cross-tenant spoof tests remain green.
- `appointment.rescheduled` canonical handler tests remain green.
- Contract fixture drift guard passes.
- `test_consumer_tenant_verification_mandate` lint remains green.

---

## 10. Transaction and dedupe semantics

Handlers run inside `dispatch_envelope`'s `transaction.atomic()`:

- On success: side-effects and `IngestDedupe` row commit together.
- On exception: the whole transaction rolls back. No proxy write, no reminder write, no dedupe row, no internal event emission.
- The dispatcher then writes a `HandlerFailureTracker` row in a **separate** transaction. After `EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD` attempts (default 3), an `IngestDLQ` row is upserted.
- Ayla sees HTTP 500 and retries per its publisher policy. Because no dedupe row was committed, the retry of the same `event_id` is not treated as a duplicate.

This satisfies the required retryable failure + dedupe rollback semantics.

---

## 11. Files changed

| File | Change |
|------|--------|
| `apps/eventbus/consumers/booking.py` | Status normalization, reminder gating, fail-loud missing proxy, terminal-state guard, row locking. |
| `apps/eventbus/ingest_redaction.py` | Add `awaiting_payment` to `ALLOWED_ENUM_VALUES`. |
| `apps/eventbus/tests/test_booking_consumer.py` | New tests for normalization, eligibility, ordering, missing proxy. |
| `apps/eventbus/tests/test_contract_fixtures.py` | Update `EVENT_DATA_KEYS` for new fixtures. |
| `tests/fixtures/contracts/booking.cancelled.v1.json` | New fixture. |
| `tests/fixtures/contracts/appointment.rescheduled.v1.json` | New fixture. |
| `tests/fixtures/contracts/__init__.py` | Add fixtures to `EVENT_FIXTURES`. |
| `tests/fixtures/contracts/MANIFEST.sha256` | Regenerate. |
| `apps/miniapp/src/lib/booking-status.ts` | Add `pending_payment → awaiting_payment` alias. |

---

## 12. Remaining known gaps (out of scope)

- **Reminder duplicate race across create-path and confirm-path:** `_schedule_reminders` uses `update_or_create` keyed by `(ayla_appointment_id, tenant, kind)`, which should prevent duplicates. This PR will add tests to prove it for the pilot paths. If any residual duplicate gap remains, it will be documented as input to PR-T02-4.
- **Legacy `booking.rescheduled` handler:** stays registered as a compatibility sink but is not modified.
- **Backend tenant-null preflight:** belongs to a separate Backend runbook.
- **Backend retry/DLQ policy values:** still owned by Ayla configuration, not BOT code.

---

## 13. Acceptance criteria

1. `awaiting_payment` is never stored in `RemoteBookingProxy.status`.
2. Unknown status fails closed with `UnknownBookingStatusError`.
3. `pending_payment` and `tentative` do not create reminders.
4. `confirmed` creates reminders, both on `booking.created(confirmed)` and on `booking.confirmed`.
5. Walk-in dual emission does not duplicate reminders.
6. `booking.confirmed` without proxy raises `BookingConfirmedPendingProxyError` (retryable).
7. `booking.cancelled` without proxy raises `BookingCancelledPendingProxyError` (retryable).
8. Retry of the same `event_id` after the proxy appears succeeds.
9. Dedupe rollback is preserved (no dedupe row on handler exception).
10. Cross-tenant isolation is preserved.
11. Late `booking.confirmed` after `booking.cancelled` is a no-op (terminal cancelled state).
12. CI green: pytest eventbus/booking tests, contract tests, ruff, mypy.
13. Two review rounds (state-machine + adversarial) without open P0/P1/P2.

---

## 14. Additional design clarifications (review amendments)

These amendments are approved by the owner and become part of the implementation contract.

### 14.1 Cross-tenant lookup invariant

The proxy lookup intentionally uses `appointment_id` only:

```python
proxy = (
    RemoteBookingProxy.all_tenants
    .select_for_update()
    .filter(appointment_id=appointment_id)
    .first()
)
```

Tenant validation **must** be performed immediately afterwards via `_assert_proxy_tenant(...)`.

Rationale:

- Allows explicit detection of cross-tenant spoof attempts.
- Preserves security auditability.
- Avoids silently treating spoof attempts as missing proxies.

Any wording or code implying that the SQL lookup itself filters by tenant must be removed.

### 14.2 Terminal lifecycle rule

For Pilot MVP the lifecycle is:

```text
pending_payment
      │
      ▼
  confirmed
      │
      ▼
  cancelled (terminal)
```

The transition `cancelled → confirmed` is explicitly forbidden. A late `booking.confirmed` after a successful cancellation must:

- Leave the proxy unchanged.
- Leave reminders unchanged.
- Not overwrite `last_synced_event_id`.
- Emit an informational audit log only.

### 14.3 `last_synced_event_id` invariant

`last_synced_event_id` may only be updated when a real state mutation has been committed. It must **not** be updated for:

- Terminal-state no-op.
- Unknown producer status.
- Missing proxy.
- Tenant authorization failure.
- Duplicate short-circuit.

### 14.4 Reminder scheduler contract

This PR assumes the existing scheduler is idempotent. Required invariant:

> `_schedule_reminders(...)` may be invoked repeatedly for the same appointment without creating duplicate reminders or resetting already-processed reminder state.

The implementation must not introduce a check-then-create race.

### 14.5 Global lifecycle invariants

After this PR the following invariants must hold:

1. `RemoteBookingProxy.status` always belongs to the BOT canonical enum.
2. Unknown producer status never mutates the proxy.
3. At most one active reminder set exists for one appointment.
4. A cancelled booking cannot transition back to confirmed.
5. Handler exceptions never commit `IngestDedupe`.
