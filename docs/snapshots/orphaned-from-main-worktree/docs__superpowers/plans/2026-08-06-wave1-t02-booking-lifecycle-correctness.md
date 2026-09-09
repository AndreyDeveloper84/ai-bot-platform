# WAVE1-T02 Booking Lifecycle Correctness — PR-T02-2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement BOT-side booking lifecycle correctness: status normalization (`awaiting_payment → pending_payment`), confirmed-only reminders, fail-loud missing-proxy handling for `booking.confirmed`/`booking.cancelled`, terminal-state guard, and supporting tests/fixtures/frontend alias.

**Architecture:** In-handler helpers in `apps/eventbus/consumers/booking.py` (Option A). Pure normalization function, typed `ValueError` subclasses for retryable failures, row-locking proxy fetches, idempotent reminder scheduler. Supporting one-line changes in redaction allowlist and miniapp alias, plus two new contract fixtures with manifest regeneration.

**Tech Stack:** Python 3.12, Django 5.x, pytest, ruff, mypy, TypeScript (miniapp).

## Global Constraints

- BOT-side code only (`ai-bot-platform`). No Backend changes.
- No topic enablement / rollout configuration changes.
- No deploy.
- No changes to legacy `booking.rescheduled` handler beyond keeping it registered.
- Base branch: `dev`.
- Working branch: `fix/wave1-t02-booking-lifecycle-correctness`.
- All status values stored in `RemoteBookingProxy.status` must be members of `RemoteBookingProxy.Status`.
- Unknown `booking.created` status must raise `UnknownBookingStatusError` (subclass `ValueError`) → retryable 500, dedupe rollback.
- Missing proxy for `booking.confirmed`/`booking.cancelled` must raise typed `ValueError` → retryable 500, dedupe rollback.
- `cancelled` is terminal; late `booking.confirmed` is a no-op.
- `last_synced_event_id` only updated on real state mutation.
- No check-then-create for reminders; rely on `update_or_create` uniqueness.
- `_schedule_reminders` must not clobber `sent_at`/`replied_at` on update.
- Cross-tenant proxy lookup uses `appointment_id` only, then `_assert_proxy_tenant` guard.

---

## File structure

| File | Responsibility |
|------|----------------|
| `apps/eventbus/consumers/booking.py` | Core consumer handlers + new helpers/exceptions. |
| `apps/eventbus/ingest_redaction.py` | DLQ enum allowlist. |
| `apps/miniapp/src/lib/booking-status.ts` | Frontend status alias. |
| `tests/fixtures/contracts/booking.cancelled.v1.json` | New canonical fixture. |
| `tests/fixtures/contracts/appointment.rescheduled.v1.json` | New canonical fixture. |
| `tests/fixtures/contracts/__init__.py` | Fixture registry. |
| `tests/fixtures/contracts/MANIFEST.sha256` | Fixture drift guard. |
| `apps/eventbus/tests/test_contract_fixtures.py` | Fixture key/shape guards. |
| `apps/eventbus/tests/test_booking_consumer.py` | Consumer unit tests. |

---

## Task 0: Prepare branch and record baseline

**Files:**
- Shell: repository root.

**Interfaces:**
- Produces: branch `fix/wave1-t02-booking-lifecycle-correctness` based on latest `origin/dev`.

- [ ] **Step 1: Fetch latest dev and record pre-change state**

```bash
git fetch origin dev
git rev-parse --abbrev-ref HEAD > /tmp/pr2-start-branch.txt
git rev-parse HEAD > /tmp/pr2-start-head.txt
git rev-parse origin/dev > /tmp/pr2-origin-dev.txt
git status --short > /tmp/pr2-start-status.txt
echo "start_branch=$(cat /tmp/pr2-start-branch.txt)"
echo "start_head=$(cat /tmp/pr2-start-head.txt)"
echo "origin_dev=$(cat /tmp/pr2-origin-dev.txt)"
echo "status=$(cat /tmp/pr2-start-status.txt)"
```

Expected: `origin/dev` is the latest remote SHA; working tree is clean.

- [ ] **Step 2: Create feature branch**

```bash
git checkout -b fix/wave1-t02-booking-lifecycle-correctness origin/dev
```

Expected: branch created and checked out.

---

## Task 1: Add status normalization helpers and exceptions

**Files:**
- Modify: `apps/eventbus/consumers/booking.py` (top-level helpers near `_REMINDER_OFFSETS`).

**Interfaces:**
- Produces:
  - `normalize_booking_created_status(raw_status: object) -> str`
  - `_is_reminder_eligible(status: str) -> bool`
  - `UnknownBookingStatusError(ValueError)`
  - `BookingConfirmedPendingProxyError(ValueError)`
  - `BookingCancelledPendingProxyError(ValueError)`

- [ ] **Step 1: Write the failing import/type tests first (optional smoke)**

Add a temporary test in `apps/eventbus/tests/test_booking_consumer.py` inside a new class:

```python
class TestStatusNormalizer:
    def test_normalizer_exists(self):
        from apps.eventbus.consumers.booking import (
            BookingCancelledPendingProxyError,
            BookingConfirmedPendingProxyError,
            UnknownBookingStatusError,
            normalize_booking_created_status,
        )

        assert normalize_booking_created_status("confirmed") == "confirmed"
```

Run:

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestStatusNormalizer -v
```

Expected: FAIL (import error / function not defined).

- [ ] **Step 2: Add helpers and exceptions to `booking.py`**

Insert after `_REMINDER_OFFSETS` definition (around line 77):

```python
class UnknownBookingStatusError(ValueError):
    ""``booking.created`` carried a status outside the closed enum."""


class BookingConfirmedPendingProxyError(ValueError):
    ""``booking.confirmed`` arrived before the proxy exists."""


class BookingCancelledPendingProxyError(ValueError):
    ""``booking.cancelled`` arrived before the proxy exists."""


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


def _is_reminder_eligible(status: str) -> bool:
    """Reminders are only created for confirmed bookings."""
    return status == RemoteBookingProxy.Status.CONFIRMED
```

- [ ] **Step 3: Run the import smoke test**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestStatusNormalizer -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/eventbus/consumers/booking.py apps/eventbus/tests/test_booking_consumer.py
git commit -m "feat(eventbus): add booking status normalizer and lifecycle exceptions"
```

---

## Task 2: Make `_schedule_reminders` preserve already-sent state on update

**Files:**
- Modify: `apps/eventbus/consumers/booking.py` (`_schedule_reminders`).

**Interfaces:**
- Consumes: existing `_schedule_reminders` contract.
- Produces: same contract, but `sent_at`/`replied_at` are no longer in `defaults`.

- [ ] **Step 1: Update `_schedule_reminders`**

Change the `defaults` dict in `_schedule_reminders` from:

```python
            defaults={
                "bot_user": bot_user,
                "yclients_record_id": None,
                "chat_id": chat_id,
                "visit_at": start_at,
                "status": BookingReminder.Status.PENDING,
                "scheduled_at": start_at - offset,
                "master_name": "",
                "service_name": "",
                "sent_at": None,
                "replied_at": None,
            },
```

to:

```python
            defaults={
                "bot_user": bot_user,
                "yclients_record_id": None,
                "chat_id": chat_id,
                "visit_at": start_at,
                "status": BookingReminder.Status.PENDING,
                "scheduled_at": start_at - offset,
                "master_name": "",
                "service_name": "",
            },
```

Rationale: the model already defaults `sent_at`/`replied_at` to `NULL` on create; removing them from `defaults` preserves non-NULL values when `update_or_create` performs an update (e.g. walk-in replay or late `booking.confirmed`).

- [ ] **Step 2: Run existing reminder tests to ensure no regression**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py -k reminder -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/eventbus/consumers/booking.py
git commit -m "fix(eventbus): preserve sent_at/replied_at when upserting reminders"
```

---

## Task 3: Update `handle_booking_created`

**Files:**
- Modify: `apps/eventbus/consumers/booking.py` (`handle_booking_created`).

**Interfaces:**
- Consumes: `normalize_booking_created_status`, `_is_reminder_eligible`.
- Produces: `RemoteBookingProxy.status` always stores a valid enum value; reminders only for `confirmed`.

- [ ] **Step 1: Write failing tests for normalization + reminder gating**

Add to `apps/eventbus/tests/test_booking_consumer.py` inside `TestBookingCreated`:

```python
    def test_status_awaiting_payment_normalized_to_pending_payment(self, tenant, bot_user_linked):
        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(status="awaiting_payment"),
        )
        handle_booking_created(env)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.PENDING_PAYMENT

    def test_pending_payment_creates_no_reminders(self, tenant, bot_user_linked):
        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(status="pending_payment"),
        )
        handle_booking_created(env)
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 0
        )

    def test_tentative_creates_no_reminders(self, tenant, bot_user_linked):
        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(status="tentative"),
        )
        handle_booking_created(env)
        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 0
        )

    def test_unknown_status_raises(self, tenant, bot_user_linked):
        from apps.eventbus.consumers.booking import UnknownBookingStatusError

        env = _envelope(
            event_name="booking.created",
            data=_booking_created_data(status="future_unknown"),
        )
        with pytest.raises(UnknownBookingStatusError):
            handle_booking_created(env)

    def test_non_string_status_raises(self, tenant, bot_user_linked):
        from apps.eventbus.consumers.booking import UnknownBookingStatusError

        data = _booking_created_data()
        data["status"] = {"value": "confirmed"}
        env = _envelope(event_name="booking.created", data=data)
        with pytest.raises(UnknownBookingStatusError):
            handle_booking_created(env)
```

Run:

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingCreated -v
```

Expected: new tests FAIL.

- [ ] **Step 2: Modify `handle_booking_created`**

Inside `handle_booking_created`, after parsing timestamps and resolving `bot_user`, insert normalization and log:

```python
    raw_status = data.get("status")
    normalized_status = normalize_booking_created_status(raw_status)

    if raw_status != normalized_status:
        logger.info(
            "eventbus.consumer.booking.created.status_normalized "
            "event_id=%s appointment_id=%s tenant_id=%s raw_status=%s normalized_status=%s",
            envelope.event_id,
            data.get("appointment_id"),
            envelope.tenant_id,
            raw_status,
            normalized_status,
        )
```

Then change `create_defaults`:

```python
    create_defaults = {
        "tenant": tenant,
        "bot_user": bot_user,
        "start_at": start_at,
        "end_at": end_at,
        "status": normalized_status,
        "source": data.get("source", ""),
        "service_id": UUID(data["service_id"]) if data.get("service_id") else None,
        "specialist_id": (UUID(data["specialist_id"]) if data.get("specialist_id") else None),
        "last_synced_event_id": envelope.event_id,
    }
```

Then gate reminders:

```python
    if bot_user is not None:
        if _is_reminder_eligible(normalized_status):
            _schedule_reminders(
                tenant=tenant,
                bot_user=bot_user,
                appointment_id=appointment_id,
                start_at=start_at,
            )
        _touch_conversation_last_booking(
            bot_user=bot_user,
            tenant=tenant,
            last_booking_at=start_at,
        )
    else:
        logger.info(
            "eventbus.consumer.booking.created.orphan_proxy "
            "appointment_id=%s status=%s",
            appointment_id,
            normalized_status,
        )
```

And update internal event emission to use normalized status:

```python
    emit_internal_event(
        "booking_created",
        properties={
            "appointment_id": str(appointment_id),
            "status": normalized_status,
            "source": data.get("source", ""),
            "start_at": data["start_at"],
        },
    )
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingCreated -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/eventbus/consumers/booking.py apps/eventbus/tests/test_booking_consumer.py
git commit -m "feat(eventbus): normalize booking.created status and gate reminders on confirmed"
```

---

## Task 4: Update `handle_booking_confirmed`

**Files:**
- Modify: `apps/eventbus/consumers/booking.py` (`handle_booking_confirmed`).

**Interfaces:**
- Consumes: `BookingConfirmedPendingProxyError`, `_schedule_reminders`.
- Produces: proxy fetched with row lock, missing proxy raises, terminal cancelled guard, reminders scheduled idempotently.

- [ ] **Step 1: Write failing tests for missing proxy + terminal guard + reminder creation**

Add to `TestBookingConfirmed`:

```python
    def test_confirmed_without_proxy_raises(self, tenant):
        from apps.eventbus.consumers.booking import BookingConfirmedPendingProxyError

        env = _envelope(
            event_name="booking.confirmed",
            data={"appointment_id": APPOINTMENT_ID, "payment_id": PAYMENT_ID},
        )
        with pytest.raises(BookingConfirmedPendingProxyError):
            handle_booking_confirmed(env)

    def test_confirmed_creates_reminders_for_pending_proxy(self, tenant, bot_user_linked):
        RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status=RemoteBookingProxy.Status.PENDING_PAYMENT,
        )
        env = _envelope(event_name="booking.confirmed", data=self._confirmed_data())
        handle_booking_confirmed(env)

        assert (
            BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 2
        )

    def test_confirmed_after_cancelled_is_no_op(self, tenant, bot_user_linked):
        proxy = RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=bot_user_linked,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status=RemoteBookingProxy.Status.CANCELLED,
            last_synced_event_id="01J9CANCEL0000000000000001",
        )
        env = _envelope(
            event_id="01J9CONFIRMAFTERCANCEL0001",
            event_name="booking.confirmed",
            data=self._confirmed_data(),
        )
        handle_booking_confirmed(env)

        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED
        assert proxy.last_synced_event_id == "01J9CANCEL0000000000000001"
```

Run:

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingConfirmed -v
```

Expected: new tests FAIL.

- [ ] **Step 2: Remove the obsolete missing-proxy no-op test**

Delete `TestBookingConfirmed.test_unknown_appointment_no_error` from `apps/eventbus/tests/test_booking_consumer.py`. It asserted the old silent no-op behaviour.

- [ ] **Step 3: Rewrite `handle_booking_confirmed`**

Replace the body of `handle_booking_confirmed` with:

```python
def handle_booking_confirmed(envelope: IngestEnvelope) -> None:
    """``booking.confirmed`` — appointment moved to ``confirmed`` (B1).

    Emitted by Ayla when an appointment is confirmed (typically once
    payment is captured). Idempotently flips the proxy to ``confirmed``
    and ensures reminders exist. Missing proxy is a retryable failure;
    a late confirm after cancellation is a no-op because ``cancelled``
    is a terminal state for the pilot.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.booking.confirmed.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    proxy = (
        RemoteBookingProxy.all_tenants.select_for_update()
        .filter(appointment_id=appointment_id)
        .first()
    )

    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    if proxy is None:
        logger.warning(
            "eventbus.consumer.booking.confirmed.pending_proxy "
            "appointment_id=%s event_id=%s tenant_id=%s",
            appointment_id,
            envelope.event_id,
            tenant.id,
        )
        raise BookingConfirmedPendingProxyError(
            f"appointment {appointment_id}: no RemoteBookingProxy yet for booking.confirmed "
            f"(event_id={envelope.event_id})"
        )

    if proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.confirmed.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    if proxy.status == RemoteBookingProxy.Status.CANCELLED:
        logger.info(
            "eventbus.consumer.booking.confirmed.after_cancelled_noop "
            "appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    proxy.status = RemoteBookingProxy.Status.CONFIRMED
    proxy.last_synced_event_id = envelope.event_id
    proxy.save(update_fields=["status", "last_synced_event_id", "synced_at"])

    bot_user = _resolve_bot_user(user_id=UUID(envelope.user_id), tenant=tenant)
    if bot_user is not None:
        _schedule_reminders(
            tenant=tenant,
            bot_user=bot_user,
            appointment_id=appointment_id,
            start_at=proxy.start_at,
        )

    if data.get("payment_id"):
        from apps.eventbus.consumers.payment import upsert_payment_mirror

        upsert_payment_mirror(
            tenant=tenant,
            appointment_id=appointment_id,
            payment_id=data.get("payment_id"),
            capture_state="authorized",
            amount=data.get("amount"),
            event_id=envelope.event_id,
        )

    emit_internal_event(
        "booking_confirmed",
        properties={
            "appointment_id": str(appointment_id),
            "payment_id": data.get("payment_id", ""),
        },
    )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingConfirmed -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/eventbus/consumers/booking.py apps/eventbus/tests/test_booking_consumer.py
git commit -m "feat(eventbus): fail-loud confirmed, terminal cancelled guard, confirmed reminders"
```

---

## Task 5: Update `handle_booking_cancelled`

**Files:**
- Modify: `apps/eventbus/consumers/booking.py` (`handle_booking_cancelled`).

**Interfaces:**
- Consumes: `BookingCancelledPendingProxyError`.
- Produces: missing proxy raises; existing behaviour otherwise preserved.

- [ ] **Step 1: Write failing tests for missing proxy and ordering**

Add to `TestBookingCancelled`:

```python
    def test_cancelled_without_proxy_raises(self, tenant):
        from apps.eventbus.consumers.booking import BookingCancelledPendingProxyError

        env = _envelope(
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        with pytest.raises(BookingCancelledPendingProxyError):
            handle_booking_cancelled(env)

    def test_cancel_before_created_then_create_then_replay_cancels(self, tenant):
        from apps.eventbus.consumers.booking import BookingCancelledPendingProxyError

        env_cancel = _envelope(
            event_id="01J9CANCELBEFORECREATE001",
            event_name="booking.cancelled",
            data={
                "appointment_id": APPOINTMENT_ID,
                "cancelled_by": "user",
                "reason_code": "user_changed_plans",
                "cancelled_at": "2026-05-21T16:08:45.119Z",
            },
        )
        with pytest.raises(BookingCancelledPendingProxyError):
            handle_booking_cancelled(env_cancel)

        env_create = _envelope(
            event_id="01J9CREATEAFTERCANCEL001",
            event_name="booking.created",
            data=_booking_created_data(),
        )
        handle_booking_created(env_create)

        handle_booking_cancelled(env_cancel)
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED
        assert proxy.last_synced_event_id == env_cancel.event_id
```

Run:

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingCancelled -v
```

Expected: new tests FAIL (current code silently returns on missing proxy).

- [ ] **Step 2: Remove the obsolete out-of-order cancellation test**

Delete `TestBookingCancelled.test_out_of_order_cancel_dropped_no_proxy_written` from `apps/eventbus/tests/test_booking_consumer.py`. It asserted the old silent drop behaviour.

- [ ] **Step 3: Rewrite the missing-proxy branch in `handle_booking_cancelled`**

Replace the `if proxy is None:` branch in `handle_booking_cancelled` with:

```python
    if proxy is None:
        logger.warning(
            "eventbus.consumer.booking.cancelled.pending_proxy "
            "appointment_id=%s event_id=%s tenant_id=%s",
            appointment_id,
            envelope.event_id,
            tenant.id,
        )
        raise BookingCancelledPendingProxyError(
            f"appointment {appointment_id}: no RemoteBookingProxy yet for booking.cancelled "
            f"(event_id={envelope.event_id})"
        )
```

Also add row locking to the proxy fetch. Change:

```python
    proxy = RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).first()
```

to:

```python
    proxy = (
        RemoteBookingProxy.all_tenants.select_for_update()
        .filter(appointment_id=appointment_id)
        .first()
    )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingCancelled -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/eventbus/consumers/booking.py apps/eventbus/tests/test_booking_consumer.py
git commit -m "feat(eventbus): fail-loud cancelled on missing proxy"
```

---

## Task 6: Update DLQ redaction allowlist

**Files:**
- Modify: `apps/eventbus/ingest_redaction.py`.

**Interfaces:**
- Produces: `"awaiting_payment"` in `ALLOWED_ENUM_VALUES`.

- [ ] **Step 1: Add `awaiting_payment` to allowlist**

In `ALLOWED_ENUM_VALUES`, add `"awaiting_payment"` next to the existing booking status values:

```python
        # booking.created.status
        "confirmed",
        "pending_payment",
        "awaiting_payment",
        "tentative",
```

- [ ] **Step 2: Run redaction-related tests**

```bash
python -m pytest apps/eventbus/tests/test_round3_new_surfaces.py apps/eventbus/tests/test_handler_exception_dlq.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add apps/eventbus/ingest_redaction.py
git commit -m "feat(eventbus): allow awaiting_payment in DLQ redaction for forensic visibility"
```

---

## Task 7: Update miniapp status alias

**Files:**
- Modify: `apps/miniapp/src/lib/booking-status.ts`.

**Interfaces:**
- Produces: `pending_payment: "awaiting_payment"` in `BACKEND_ALIAS_MAP`.

- [ ] **Step 1: Add alias**

In `BACKEND_ALIAS_MAP`, add after the existing payment entries:

```ts
  // C7 online path (payment created, not yet captured)
  awaiting_payment: "awaiting_payment",
  payment_pending: "awaiting_payment",
  pending_payment: "awaiting_payment",
```

- [ ] **Step 2: Run TypeScript check if available**

```bash
cd apps/miniapp && npm run typecheck || true
```

If no typecheck script, skip with note.

- [ ] **Step 3: Commit**

```bash
git add apps/miniapp/src/lib/booking-status.ts
git commit -m "feat(miniapp): map normalized pending_payment to awaiting_payment UI bucket"
```

---

## Task 8: Add contract fixtures and regenerate manifest

**Files:**
- Create: `tests/fixtures/contracts/booking.cancelled.v1.json`
- Create: `tests/fixtures/contracts/appointment.rescheduled.v1.json`
- Modify: `tests/fixtures/contracts/__init__.py`
- Modify: `apps/eventbus/tests/test_contract_fixtures.py`
- Modify: `tests/fixtures/contracts/MANIFEST.sha256` (via command).

**Interfaces:**
- Produces: canonical fixtures registered and hash-guarded.

- [ ] **Step 1: Create `booking.cancelled.v1.json`**

```json
{
  "event_id": "01J9J5ABKDQ7T2V8R4Q1P9D5F3D",
  "event_name": "booking.cancelled",
  "event_version": 1,
  "occurred_at": "2026-05-22T16:35:00.000Z",
  "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
  "user_id": "f1a2b3c4-d5e6-4789-9abc-def012345678",
  "actor": "user",
  "correlation_id": "f7a8b9c0-d1e2-3456-7890-123456789abd",
  "causation_id": "01J9J5ABKDQ7T2V8R4Q1P9D5F3C",
  "data": {
    "appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8",
    "cancelled_by": "user",
    "reason_code": "user_changed_plans",
    "cancelled_at": "2026-05-22T16:35:00.000Z"
  }
}
```

- [ ] **Step 2: Create `appointment.rescheduled.v1.json`**

```json
{
  "event_id": "01J9J5ABKDQ7T2V8R4Q1P9D5F3E",
  "event_name": "appointment.rescheduled",
  "event_version": 1,
  "occurred_at": "2026-05-22T17:00:00.000Z",
  "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
  "user_id": "f1a2b3c4-d5e6-4789-9abc-def012345678",
  "actor": "system",
  "correlation_id": "f7a8b9c0-d1e2-3456-7890-123456789abe",
  "causation_id": "01J9J5ABKDQ7T2V8R4Q1P9D5F3C",
  "data": {
    "appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8",
    "version": 2,
    "previous_version": 1,
    "revision_id": "rev-2026-05-22-0001",
    "changed_fields": ["starts_at"],
    "actor": {"type": "system"},
    "starts_at": "2026-05-23T15:00:00+03:00",
    "previous_starts_at": "2026-05-22T15:00:00+03:00"
  }
}
```

- [ ] **Step 3: Register fixtures**

Update `EVENT_FIXTURES` in `tests/fixtures/contracts/__init__.py`:

```python
EVENT_FIXTURES = (
    "booking.created.v1.json",
    "booking.confirmed.v1.json",
    "booking.cancelled.v1.json",
    "appointment.rescheduled.v1.json",
    "payment.captured.v1.json",
    "payment.failed.v1.json",
)
```

- [ ] **Step 4: Update `EVENT_DATA_KEYS` in `test_contract_fixtures.py`**

Add:

```python
    "booking.cancelled.v1.json": {
        "appointment_id",
        "cancelled_by",
        "reason_code",
        "cancelled_at",
    },
    "appointment.rescheduled.v1.json": {
        "appointment_id",
        "version",
        "previous_version",
        "revision_id",
        "changed_fields",
        "actor",
        "starts_at",
        "previous_starts_at",
    },
```

- [ ] **Step 5: Regenerate manifest**

```bash
python -m tests.fixtures.contracts --write-manifest
```

- [ ] **Step 6: Run contract fixture tests**

```bash
python -m pytest apps/eventbus/tests/test_contract_fixtures.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/contracts/
git add apps/eventbus/tests/test_contract_fixtures.py
git commit -m "chore(contracts): add booking.cancelled and appointment.rescheduled pilot fixtures"
```

---

## Task 9: Add ordering and adversarial tests

**Files:**
- Modify: `apps/eventbus/tests/test_booking_consumer.py`.

**Interfaces:**
- Consumes: all handler changes from Tasks 3–5.
- Produces: tests covering O1–O8 and invariants.

- [ ] **Step 1: Add ordering scenario tests**

Add a new class `TestBookingLifecycleOrdering`:

```python
class TestBookingLifecycleOrdering:
    def _pending_proxy(self, tenant: Tenant) -> RemoteBookingProxy:
        return RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=None,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status=RemoteBookingProxy.Status.PENDING_PAYMENT,
        )

    def test_normal_paid_booking_flow(self, tenant, bot_user_linked):
        env_created = _envelope(
            event_name="booking.created",
            data=_booking_created_data(status="awaiting_payment"),
        )
        handle_booking_created(env_created)

        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID))
        assert proxy.status == RemoteBookingProxy.Status.PENDING_PAYMENT
        assert BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 0

        env_confirmed = _envelope(
            event_id="01J9CONFIRM001",
            event_name="booking.confirmed",
            data={"appointment_id": APPOINTMENT_ID, "payment_id": PAYMENT_ID},
        )
        handle_booking_confirmed(env_confirmed)

        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 2

    def test_walk_in_no_duplicate_reminders(self, tenant, bot_user_linked):
        env_created = _envelope(
            event_name="booking.created",
            data=_booking_created_data(status="confirmed"),
        )
        handle_booking_created(env_created)
        assert BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 2

        env_confirmed = _envelope(
            event_id="01J9CONFIRM002",
            event_name="booking.confirmed",
            data={"appointment_id": APPOINTMENT_ID, "payment_id": PAYMENT_ID},
        )
        handle_booking_confirmed(env_confirmed)
        assert BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(APPOINTMENT_ID)).count() == 2

    def test_confirmed_then_cancelled_cancels_reminders(self, tenant, bot_user_linked):
        self._pending_proxy(tenant)
        handle_booking_confirmed(
            _envelope(
                event_id="01J9CONFIRM003",
                event_name="booking.confirmed",
                data={"appointment_id": APPOINTMENT_ID, "payment_id": PAYMENT_ID},
            )
        )
        assert BookingReminder.all_tenants.filter(
            ayla_appointment_id=UUID(APPOINTMENT_ID),
            status=BookingReminder.Status.PENDING,
        ).count() == 2

        handle_booking_cancelled(
            _envelope(
                event_id="01J9CANCEL003",
                event_name="booking.cancelled",
                data={
                    "appointment_id": APPOINTMENT_ID,
                    "cancelled_by": "user",
                    "reason_code": "user_changed_plans",
                    "cancelled_at": "2026-05-22T16:35:00.000Z",
                },
            )
        )
        assert BookingReminder.all_tenants.filter(
            ayla_appointment_id=UUID(APPOINTMENT_ID),
            status=BookingReminder.Status.CANCELLED,
        ).count() == 2

    def test_late_confirmed_after_cancelled_stays_cancelled(self, tenant, bot_user_linked):
        proxy = RemoteBookingProxy.all_tenants.create(
            appointment_id=UUID(APPOINTMENT_ID),
            tenant=tenant,
            bot_user=bot_user_linked,
            start_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
            end_at=dt.datetime(2026, 5, 22, 16, 0, tzinfo=dt.timezone.utc),
            status=RemoteBookingProxy.Status.CANCELLED,
            last_synced_event_id="01J9CANCEL004",
        )
        handle_booking_confirmed(
            _envelope(
                event_id="01J9CONFIRMAFTERCANCEL004",
                event_name="booking.confirmed",
                data={"appointment_id": APPOINTMENT_ID, "payment_id": PAYMENT_ID},
            )
        )
        proxy.refresh_from_db()
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED
        assert proxy.last_synced_event_id == "01J9CANCEL004"
```

- [ ] **Step 2: Add dispatcher-level dedupe rollback test**

Add to `apps/eventbus/tests/test_ingest_dispatcher.py` (or create a focused test in `test_booking_consumer.py` that calls `dispatch_envelope` directly):

```python
@pytest.mark.django_db
def test_confirmed_without_proxy_does_not_commit_dedupe(settings):
    from apps.eventbus.consumers.booking import BookingConfirmedPendingProxyError
    from apps.eventbus.ingest_dispatcher import DispatchOutcome, dispatch_envelope
    from apps.eventbus.ingest_envelope import IngestEnvelope
    from apps.eventbus.models import IngestDedupe
    from apps.tenancy.models import Tenant

    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True
    Tenant.objects.create(
        id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
        slug="t-dedupe",
        name="Dedupe test tenant",
    )
    env = IngestEnvelope(
        event_id="01J9DEDUPECONFIRM000000001",
        event_name="booking.confirmed",
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 22, 15, 0, tzinfo=dt.timezone.utc),
        tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
        user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
        actor="system",
        correlation_id=None,
        causation_id=None,
        data={"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8", "payment_id": PAYMENT_ID},
    )
    result = dispatch_envelope(env)
    assert result.outcome == DispatchOutcome.HANDLER_EXCEPTION
    assert isinstance(result.exception, BookingConfirmedPendingProxyError)
    assert not IngestDedupe.objects.filter(event_id=env.event_id).exists()
```

- [ ] **Step 3: Run all new ordering + dispatcher tests**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py::TestBookingLifecycleOrdering -v
python -m pytest apps/eventbus/tests/test_booking_consumer.py::test_confirmed_without_proxy_does_not_commit_dedupe -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/eventbus/tests/test_booking_consumer.py
git commit -m "test(eventbus): add lifecycle ordering and dedupe rollback tests"
```

---

## Task 10: Full test suite + lint + type checks

**Files:**
- Shell.

- [ ] **Step 1: Run eventbus and booking tests**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py apps/eventbus/tests/test_contract_fixtures.py apps/eventbus/tests/test_ingest_dispatcher.py -v
```

Expected: PASS.

- [ ] **Step 2: Run ruff**

```bash
ruff check apps/eventbus/consumers/booking.py apps/eventbus/ingest_redaction.py apps/eventbus/tests/test_booking_consumer.py tests/fixtures/contracts/__init__.py apps/eventbus/tests/test_contract_fixtures.py
```

Expected: no errors.

- [ ] **Step 3: Run mypy on changed Python files**

```bash
mypy apps/eventbus/consumers/booking.py apps/eventbus/ingest_redaction.py
```

Expected: no new errors.

- [ ] **Step 4: Regenerate fixtures manifest if any fixture changed**

```bash
python -m tests.fixtures.contracts --write-manifest
```

- [ ] **Step 5: Commit any autofixes**

```bash
git diff --quiet || git commit -am "chore: ruff/mypy/fixture drift fixes"
```

---

## Task 11: Final review and PR preparation

**Files:**
- Shell.

- [ ] **Step 1: Verify diff scope**

```bash
git diff --stat origin/dev
```

Expected: only files from Section 11 plus test changes.

- [ ] **Step 2: Run the full relevant CI subset one more time**

```bash
python -m pytest apps/eventbus/tests/test_booking_consumer.py apps/eventbus/tests/test_contract_fixtures.py apps/eventbus/tests/test_ingest_dispatcher.py -q
ruff check apps/eventbus/ tests/fixtures/contracts/__init__.py
mypy apps/eventbus/consumers/booking.py apps/eventbus/ingest_redaction.py
```

Expected: all green.

- [ ] **Step 3: Push branch**

```bash
git push -u origin fix/wave1-t02-booking-lifecycle-correctness
```

- [ ] **Step 4: Prepare PR description notes**

Create a draft message in a temp file (do not open a PR automatically):

```bash
cat > /tmp/pr2-notes.md <<'EOF'
# fix(eventbus): enforce booking lifecycle ordering and confirmed reminders

Closes T-02 lifecycle correctness gaps per OD-T02-4/5/6/7.

## What changed
- Status normalization for `booking.created`: `awaiting_payment → pending_payment`.
- Fail-closed `UnknownBookingStatusError` for unknown/non-string statuses.
- Reminders created only when status is `confirmed`.
- `booking.confirmed` now idempotently creates reminders and raises `BookingConfirmedPendingProxyError` if proxy is missing.
- `booking.cancelled` now raises `BookingCancelledPendingProxyError` if proxy is missing.
- Terminal-state guard: late `booking.confirmed` after cancellation is a no-op.
- Proxy fetches use `select_for_update()`; tenant guard preserved.
- DLQ redaction allowlist includes `awaiting_payment` for forensic visibility.
- Miniapp alias maps `pending_payment` to `awaiting_payment` UI bucket.
- Added contract fixtures for `booking.cancelled` and `appointment.rescheduled`.

## What is intentionally not changed
- No Backend topics / deploy / rollout config.
- Legacy `booking.rescheduled` handler unchanged.
- Backend tenant-null preflight remains a separate runbook.

## Tests
- Extended `apps/eventbus/tests/test_booking_consumer.py` with normalization, eligibility, ordering, missing-proxy, dedupe-rollback, and terminal-state tests.
- Contract fixture drift guard updated.

## Checklist
- [ ] pytest green
- [ ] ruff green
- [ ] mypy green
- [ ] Round 1 state-machine review
- [ ] Round 2 adversarial review
EOF
cat /tmp/pr2-notes.md
```

- [ ] **Step 5: Final commit if notes were generated (optional)**

No commit needed for the temp file.

---

## Self-review checklist

- [ ] **Spec coverage:** Every acceptance criterion from the design doc maps to at least one task/test.
- [ ] **No placeholders:** No "TBD", "TODO", or vague steps remain.
- [ ] **Type consistency:** `normalize_booking_created_status` returns `str`; `_is_reminder_eligible` takes `str`; exceptions subclass `ValueError`.
- [ ] **No Backend changes:** No Backend file paths appear in the plan.
- [ ] **No topic enablement:** No runtime configuration / env files are modified.
