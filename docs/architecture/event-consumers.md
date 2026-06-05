# Event consumers — how-to guide

> **Status**: living document. Last verified 2026-05-25 after #433 umbrella close.
>
> This document is the **how-to** for adding a new cross-service event
> consumer in bot-platform. The full envelope + event-name + idempotency
> **contract** lives in [`event-contract.md`](event-contract.md). This
> file is the implementation guide: where to put code, which patterns to
> reuse, which tests to write.

## 1. Architecture at a glance

```
Ayla djangoproject  ──(HMAC-signed POST)──►  /api/v1/internal/events/ingest
                                                       │
                                                       ▼
                                              view → envelope parse
                                                       │
                                                       ▼
                                              dispatch_envelope()
                                                       │
                              ┌────────────────────────┼────────────────────────┐
                              ▼                        ▼                        ▼
                       UNKNOWN_EVENT_NAME       (event_name, version)     HANDLER_EXCEPTION
                         → IngestDLQ          → registered handler       → retry counter +
                                                       │                  threshold-DLQ
                                                       ▼
                                              transaction.atomic:
                                                IngestDedupe write
                                                handler side-effects
                                                COMMIT (together)
```

Two-bus reminder: this guide is about the **cross-service** event bus
(`apps/eventbus`, dot.notation event names like `booking.created`).
The in-process analytics bus (`apps/events`, snake_case `booking_created`)
is unrelated — different pattern, different module.

## 2. Adding a new consumer — 6-step recipe

### Step 1 — Confirm the event is in the §3 catalog

The dispatcher rejects any `event_name` not in
[`event-contract.md`](event-contract.md) §3 closed set
(`_KNOWN_NAMES` constant in `apps/eventbus/ingest_dispatcher.py`). If
your event isn't there, you need a contract amendment FIRST (cross-
stream work with Ayla / Alpha).

### Step 2 — Create the handler module

Place it under `apps/eventbus/consumers/`. One module per logical
family — examples:

* `consumers/booking.py` — `booking.created / .cancelled / .rescheduled / .completed`
* `consumers/payment.py` — `payment.authorized / .captured / .failed / .refunded`
* `consumers/catalog.py` — `service.updated`
* `consumers/identity.py` — `user.profile.updated`
* `consumers/schedule.py` — `master.schedule.updated`
* `consumers/reviews.py` — `review.created`

Handler signature (canonical shape):

```python
def handle_my_event(envelope: IngestEnvelope) -> None:
    # 1. Tenant guard FIRST (A3 mandate from PR #524).
    assert_envelope_tenant_authorized(envelope)

    # 2. Parse + validate data shape defensively.
    try:
        my_id = UUID(envelope.data["my_id"])
    except (KeyError, ValueError, TypeError):
        logger.warning("...bad_id...", envelope.event_id)
        return

    # 3. Resolve tenant + look up mirror rows.
    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        return

    # 4. Side-effects.
    # The outer dispatcher already wraps this call in
    # transaction.atomic — your writes are part of the same
    # transaction as the IngestDedupe row.

    # 5. Optional: emit internal analytics events.
    emit_internal_event("my_event_happened", properties={...})
```

### Step 3 — Register the handler

Add a `register_my_handlers()` function in your consumer module:

```python
def register_my_handlers() -> None:
    try:
        register(
            event_name="my.event",
            event_version=1,
            handler=handle_my_event,
        )
    except ValueError:
        # Duplicate registration — silently OK on autoreload.
        pass
```

Then call it from `apps/eventbus/apps.py::EventBusConfig.ready`:

```python
from apps.eventbus.consumers.my_module import register_my_handlers
register_my_handlers()
```

### Step 4 — Choose the right idempotency layer

The dispatcher already gives you:

* **`IngestDedupe`** — one row per `event_id`, written in the SAME
  transaction as your handler's side-effects. Same-event_id replay
  short-circuits at the dispatcher; your handler doesn't run twice.

That's enough for most consumers. Add a **second** layer when:

* The event has a payload-level identity (`payment_id`, `review_id`,
  `appointment_id`) and the operator might re-fire the event with a
  fresh ULID (= bypass IngestDedupe). Then you need a per-payload
  dedupe table. Examples:
  * `PaymentTerminalDedupe(tenant_id, payment_id, terminal_state)` — #443
  * `ReviewProcessedDedupe(tenant_id, review_id)` — #445
* The handler's side-effects compound (counter increment, ledger
  accrual, fan-out emit). Double-firing those is a billing-class
  bug.

For pure cache invalidation (`service.updated` cache_version bump),
the dispatcher's IngestDedupe is sufficient — additive replays are
harmless.

### Step 5 — Write the tests

Three layers:

1. **Per-handler tests** in `apps/eventbus/tests/test_my_consumer.py`:
   * Happy path
   * Idempotency replay-3× (call handler directly 3 times, assert 1
     side-effect)
   * Cross-tenant isolation (spoofer tenant doesn't mutate victim's
     row)
   * Tenant-verify mandate (`assert_envelope_tenant_authorized` raises
     on null tenant_id where appropriate)
   * Malformed payload defence
2. **Contract test** in `tests/contracts/test_event_idempotency.py`:
   * Add a `TestMyConsumerIdempotency` class with one test using
     `_dispatch_3x_and_assert`. This pins the dispatcher-level
     `IngestDedupe` contract.
3. **Negative tests** if your handler enforces an allowlist (e.g.
   PII §7 in #446) — assert blocked fields produce ZERO side-effects.

### Step 6 — Handle exceptions

The dispatcher catches any exception your handler raises and:

* Logs via `logger.exception`.
* Returns `DispatchOutcome.HANDLER_EXCEPTION` → view returns HTTP 500
  → Ayla retries per §6.3.
* Increments `HandlerFailureTracker(event_id, handler_name, outcome)`
  in a **separate transaction** (so the counter survives the
  rollback).
* When `attempt_count >= EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD`
  (default 3, env-driven) — upserts an `IngestDLQ` row with
  `reason="handler_exception"` for operator triage.

You do **not** need to handle the exception yourself unless you want
to convert it into a graceful no-op (which is rare — most failures
should propagate so Ayla retries).

## 3. Dead-letter queue (DLQ)

A single `IngestDLQ` row exists per `(event_id, reason)` pair —
`UniqueConstraint` enforced at the schema level. Reasons the
dispatcher writes today:

| reason | When |
|---|---|
| `unknown_event_name` | event_name not in §3 catalog |
| `unknown_event_version` | event_name OK, but no handler registered for that version |
| `handler_exception` | handler raised, retry counter crossed threshold |

DLQ writes are **upserts** — re-firing the same failure scenario
refreshes the row's `raw_body` and updates `dead_lettered_at` (when
using `update_or_create` semantics). Operator manual replay sets
`replayed_at`.

`raw_body.data` is **always redacted** via
`apps.eventbus.ingest_redaction.redact_data_for_dlq` — PII §7 enforced
at the DLQ boundary regardless of consumer behaviour.

## 4. Observability

Today: structured logs (logger.info / .warning / .exception) +
Sentry breadcrumbs via Django integration.

**Not today**: Prometheus / OTel metrics. Filed as FOLLOW_UP from
the #433 umbrella — separate observability project will add per-event
counters + handler latency histogram + DLQ-write counter. Until then,
log aggregator + Sentry are the operator surfaces.

Key log lines to know:

* `eventbus.ingest.handler_registered name=... version=...` — boot-time registration
* `eventbus.ingest.handler_exception event_id=... name=... version=...` — handler raise
* `eventbus.ingest.handler_exception_threshold event_id=... handler=... attempts=N threshold=M` — DLQ write triggered
* `eventbus.ingest.dlq_write_failed event_id=... reason=...` — DLQ write itself failed

## 5. Migration ordering across teams

Cross-stream model changes (e.g. a new field on `BotUser` /
`Conversation` / `ClientProfile`) typically need a handoff to the
team owning that app (per `project_ayla_active_streams` memory).
Pattern from #442/#443/#446:

1. Consumer team writes a HANDOFF text describing the field spec.
2. Owner team ships a mini-PR with one migration (15-30 min work).
3. Consumer ff-merges dev once the field lands.
4. Consumer's handler can rely on the field.

If the consumer must ship BEFORE the field migration lands, use the
**graceful degrade** pattern: `hasattr(row, "new_field")` check
+ warning log + skip the field-specific write. Examples:

* `apps/eventbus/consumers/identity.py::handle_user_profile_updated`
  — `BotUser.avatar_url` graceful skip pre-W4 migration.
* `apps/eventbus/consumers/reviews.py::handle_review_created`
  — `ClientProfile.last_review_*` graceful skip pre-W4.

(Cite by function symbol rather than line number — line numbers drift
on every refactor; symbols are stable.)

## 6. References

* [`event-contract.md`](event-contract.md) — full envelope + event
  taxonomy + versioning + PII rules.
* [`ADR-0009`](../adr/ADR-0009-ayla-split-domain-architecture.md) —
  why bot-platform mirrors and never owns canonical state.
* Existing consumer examples:
  * `apps/eventbus/consumers/booking.py` (PR #623)
  * `apps/eventbus/consumers/payment.py` (PR #643)
  * `apps/eventbus/consumers/catalog.py` (PR #669)
  * `apps/eventbus/consumers/identity.py` (PR #680)
  * `apps/eventbus/consumers/schedule.py` (PR #713)
  * `apps/eventbus/consumers/reviews.py` (PR #713)
* Idempotency dedupe tables:
  * `apps/eventbus/models.py::IngestDedupe` — per-event_id
  * `apps/eventbus/models.py::PaymentTerminalDedupe` — #443
  * `apps/eventbus/models.py::ReviewProcessedDedupe` — #445
* Failure tracking:
  * `apps/eventbus/models.py::HandlerFailureTracker` — #433
  * `apps/eventbus/models.py::IngestDLQ` — #432 + #433 upsert
