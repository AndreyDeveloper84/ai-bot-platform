# Q-ATT-IMPL1 — Port legacy bot tools to shared booking service

**Status:** Required before cutover (Phase 2 of `mysite → ai-bot-platform`).
**Owner:** B3 / B5 parallel-agent track (DRF-839 booking skill, DRF-841 cancel/reschedule).
**Coordination:** This doc is the contract — B3 PR diff specified inline.

---

## Why

After cutover, the dev MAX bot's MAX_BOT_TOKEN points at the new
`ai-bot-platform`. Two writers create `BookingRequest` rows:

1. **Bot LLM tools** (`apps/skills/booking/tools.py::execute_confirm`,
   `execute_reschedule`) — written by the B3 agent. Currently they
   probably duplicate booking-creation logic.
2. **Mini App HTTP endpoint** (`POST /api/v1/customer/bookings`) — uses
   the hardened `apps.booking.services.create.create_customer_booking`.

Without coordination, the two writers can drift on:

* `billable` rule (locked: `ai_direct + status=confirmed`; reschedule
  exception per Q12-α)
* `attribution_metadata.actor_type` validator (rejects missing field)
* `attribution_metadata.created_by` semantics (`execute_confirm` vs
  `execute_reschedule`)
* Race protection — `select_for_update` + partial unique index
* YC push (async task firing after commit)
* Structured error envelope (`BookingCreateError` slug → HTTP status)

**Fix:** bot LLM tools become thin wrappers that delegate to the
shared service. One create path, one attribution path, one race-safety
contract.

---

## Concrete diff for B3 agent

### `apps/skills/booking/tools.py::execute_confirm`

```python
# BEFORE (current B3 implementation — assumed shape):
def execute_confirm(ctx, *, master_id, service_id, visit_at_iso) -> dict:
    # ... ~150 lines of YC API calls, BookingRequest.objects.create,
    #     attribution metadata assembly, etc.

# AFTER:
from apps.booking.services.create import (
    BookingCreateError,
    CreateBookingInput,
    create_customer_booking,
)
from datetime import datetime

def execute_confirm(ctx, *, master_id, service_id, visit_at_iso) -> dict:
    """LLM tool: confirm a customer booking from bot DM context.

    Thin wrapper — delegates the full create logic (lock, race-check,
    attribution, YC push) to the shared service. The LLM only sees a
    flat tool result (ok/error + minimal fields); the service handles
    invariants.
    """
    visit_at = datetime.fromisoformat(visit_at_iso.replace("Z", "+00:00"))
    try:
        booking = create_customer_booking(
            inp=CreateBookingInput(
                tenant=ctx.tenant,
                bot_user=ctx.bot_user,
                service_id=str(service_id),
                master_id=str(master_id),
                visit_at=visit_at,
                created_by="execute_confirm",
            ),
            correlation_id=ctx.trace_id or None,
        )
    except BookingCreateError as exc:
        return {"ok": False, "error": exc.slug, "detail": exc.detail}

    return {
        "ok": True,
        "booking_id": str(booking.id),
        "visit_at": booking.visit_at.isoformat(),
        "service_name": booking.service_name,
        "master_name": booking.master_name,
    }
```

### `apps/skills/booking/tools.py::execute_reschedule` (B5 / DRF-841)

```python
# AFTER:
from apps.booking.services.reschedule import reschedule_customer_booking

def execute_reschedule(ctx, *, old_booking_id, new_visit_at_iso) -> dict:
    visit_at = datetime.fromisoformat(new_visit_at_iso.replace("Z", "+00:00"))
    try:
        new_booking = reschedule_customer_booking(
            tenant=ctx.tenant,
            bot_user=ctx.bot_user,
            old_booking_id=str(old_booking_id),
            new_visit_at=visit_at,
            correlation_id=ctx.trace_id or None,
        )
    except BookingCreateError as exc:
        return {"ok": False, "error": exc.slug, "detail": exc.detail}
    return {"ok": True, "booking_id": str(new_booking.id)}
```

### What this gives you for free

* Atomic `select_for_update` on master row inside `transaction.atomic`
* Re-run resolver under lock → no double-book
* DB-level partial unique constraint as second-line defense
* Attribution metadata: `actor_type=customer`, `created_by=execute_confirm`
  (or `execute_reschedule`), `started_by=customer`, `test_mode=false`,
  `booking_created_at=<iso>` — all populated, passes validator
* `compute_billable()` runs once, deterministic — Q12-α reschedule
  exception honored
* Async YC push enqueued post-commit
* `booking.created` event emitted with `correlation_id`
* Structured `BookingCreateError` slug ready for either LLM tool result
  format (`{"ok": false, "error": "slot_unavailable", ...}`) or HTTP
  status mapping

### What you can DELETE from current `execute_confirm`

* Inline `BookingRequest.objects.create(...)` — service handles
* Inline attribution metadata dict assembly — service handles
* Inline YC `create_record` call (if it exists in the tool) — async
  task fires automatically
* Manual `billable` / `billing_reason` computation — service handles
* Manual race-check (re-fetch slots, compare) — service does it under
  lock

### What you KEEP in `execute_confirm`

* LLM-facing tool parameter parsing
* `ctx` extraction (tenant, bot_user, trace_id)
* Pre-call validation if you want LLM to retry on bad input (e.g.,
  master_id type check)
* Post-call success message formatting for the LLM ("Записал вас к
  Анне на 22 мая 15:30")

---

## Testing contract

The B3 PR must:

* Keep all existing `apps/skills/booking/tests/test_tools.py` tests
  passing — service does the work, but the tool's contract (inputs →
  outputs) shouldn't change
* Add an integration test that exercises both surfaces:
  - LLM tool `execute_confirm` creates BookingRequest
  - Mini App `POST /api/v1/customer/bookings` for the same slot
    afterwards → 409 slot_unavailable (proves shared race protection)
* Verify `BookingRequest.attribution_metadata.created_by` correctly
  reflects which surface created it

---

## Migration / compat

No DB migration needed — service uses existing schema. The diff is
code-only.

If B3 already merged before this port: their `execute_confirm` rows
will continue to work (validator passes if `actor_type=customer` is
set). The race-safety + YC push features will be missing until the
port lands.

---

## Coordination notes

* This port is **blocker for cutover Phase 2** (per
  `docs/plans/dev-cutover-playbook.md` — pending).
* Schedule: target completion in the 3-day window after PR #144
  merges to `dev`.
* If B3 agent diverges from the diff above (alternative architecture
  proposal), reopen this thread with comparison — but **the unified
  service contract is non-negotiable** without explicit founder review.

---

## Cross-references

* Hardened service: `apps/booking/services/create.py`
* Reschedule service: `apps/booking/services/reschedule.py`
* Attribution policy: `docs/design/policies/attribution-policy.md`
* Locked billable rule: founder Q3 delta (post-4a review, 2026-05-18)
* YC push task: `apps/integrations/yclients/tasks.py`
