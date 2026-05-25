# Runbook: Q12-α partial-failure reschedule triage

> Status: **draft**
> Last exercised: _never (created 2026-05-25)_
> Target completion sprint: _Phase 0 close / pilot prep_
> Owner: _on-call ops / W3 stream_

## Purpose

Find and resolve customer-facing reschedule operations that left the
system in a split-brain state — the YClients booking was cancelled but
the new BookingRequest was never written to our DB. Customer thinks
they have an appointment (or doesn't, depending on UI feedback); we
need to fix this manually.

These tickets are tagged with `q12a_chain_terminator: True` in the
audit row payload per founder ACK 2026-05-22 decision #4 (issue #478
close-out).

## Trigger / when to run

- Customer support ticket: «бот меня перенёс, но в YClients ничего нет»
  / «куда делась моя запись»
- Daily ops review (T+24h post-flip / once-per-day during pilot)
- Spike in `booking.reschedule.partial` event rate (eventbus dashboard)

## Prerequisites

- Django Admin access (`/admin/audit/auditlog/`)
- OR direct Postgres access (production read-replica is sufficient for
  triage; mutation requires primary)
- Slack #ops channel handle for status updates

## Procedure

### Option A — admin UI (recommended for routine triage)

1. Open `/admin/audit/auditlog/` in Django Admin.
2. In the right-hand «Filter» sidebar locate **Q12-α chain terminator**.
3. Select **«Chain terminators (partial-failure tickets)»**.
4. The list now shows ONLY audit rows where the partial-failure path
   fired. Default ordering is `-created_at` (newest first).
5. For each row needing follow-up, click into the detail view:
   - `action` should be `booking.reschedule.partial` (or related —
     widen the prose filter if you see other actions)
   - `payload` JSON contains `q12a_chain_terminator_reason: "partial_failure"`
     and the original `record_id` / `new_datetime` / `master_id` /
     `service_id` needed for manual rebook
6. Manual rebook via YClients admin → confirm with customer over chat.
7. Document resolution in incident log if rate is non-zero — feed back
   into #561 (Prometheus alerting) for proactive future detection.

### Option B — raw SQL (use when admin UI is down OR for batch export)

```sql
-- Postgres @> containment (efficient with GIN index on payload)
SELECT
    id,
    created_at,
    tenant_id,
    action,
    payload->>'old_record_id'           AS old_yc_record_id,
    payload->>'new_datetime'            AS attempted_new_datetime,
    payload->>'master_id'               AS master_id,
    payload->>'service_id'              AS service_id,
    payload->>'q12a_chain_terminator_reason' AS terminator_reason
FROM audit_auditlog
WHERE payload @> '{"q12a_chain_terminator": true}'
  AND created_at >= NOW() - INTERVAL '7 days'
ORDER BY created_at DESC
LIMIT 100;
```

Alternative if you don't need the partial-failure subset narrowing
(also works on SQLite for local replication of an issue):

```sql
SELECT id, created_at, action, payload
FROM audit_auditlog
WHERE payload ? 'q12a_chain_terminator'    -- key-presence form
ORDER BY created_at DESC
LIMIT 50;
```

The `?` (key-presence) form is what the Django admin filter
generates — it's backend-portable. The `@>` (containment) form is
Postgres-specific but safer if writers ever start storing
`q12a_chain_terminator: False` (today they don't — contract pinned
in `apps/audit/tests/test_admin_filters.py`).

## Decision branches

- **Zero terminator rows in window** → healthy state, no action.
- **1-5 rows / week** → expected pilot baseline; manual rebook each,
  log for trend analysis.
- **>5 rows / day OR sudden spike** → escalate to W3 / tech-lead:
  likely a YClients API change or local DB write regression.
  Cross-reference with `apps.events.services` event-emit error logs
  for the same window.

## Out of scope

- The Q12-α billing semantics — closed in PR #526; the audit row's
  `q12a_chain_terminator: True` already signals the chain is broken
  and any subsequent booking by this customer is billable as a fresh
  sale.
- Proactive alerting — tracked in #561 (Prometheus
  `billing_q12a_missing_signal_total` counter + alert rule).

## Related

- Issue #530 (this runbook + admin filter)
- Issue #478 (Q12-α founder ACK)
- PR #526 (Q12-α core implementation)
- `apps/skills/booking/tools.py::execute_reschedule` (partial-failure
  emit site, ~line 1818)
- `apps/audit/admin.py::Q12aChainTerminatorFilter` (Django Admin
  filter)
