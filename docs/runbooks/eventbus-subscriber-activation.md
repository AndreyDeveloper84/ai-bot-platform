# Runbook: eventbus subscriber activation

> Status: **draft**
> Last exercised: _never_
> Target completion sprint: _Phase 2.2 PR-G (this PR)_
> Owner: _Backend / on-call_

## Purpose

Activate, deactivate, or change which `apps.eventbus` subscribers consume dispatched `DomainEvent` rows. The dispatcher (`apps.eventbus.dispatch_pending_events` Celery task) calls each registered subscriber in order; a subscriber failure on one event marks that row failed but does NOT block other subscribers on the same envelope.

## Trigger / when to run

- Production deploy of Phase 2.2 — first-time **AuditSubscriber activation**
- New real subscriber lands (loyalty / billing / retention) — add to registry
- Subscriber misbehaving on prod traffic — rollback to Noop
- Periodic review: confirm `DOMAIN_EVENT_SUBSCRIBERS` env matches the production manifest

## Prerequisites

- SSH or platform-console access to set environment variables on the production Celery workers
- Ability to restart Celery workers (rolling restart preferred)
- Access to Sentry + admin console for verification

## Step-by-step procedure

### A. Activate AuditSubscriber (one-time, first prod deploy after Phase 2.2)

1. Verify migration `apps/eventbus/0002_domainevent_dead_lettered_at` is applied.
   ```bash
   python manage.py showmigrations eventbus
   ```
   Expected: `[X] 0002_domainevent_dead_lettered_at`.

2. **No env change needed.** Production default (`config/settings/production.py`) sets
   `DOMAIN_EVENT_SUBSCRIBERS = ["apps.eventbus.subscribers.AuditSubscriber"]`
   when the env var is empty. Deploy of Phase 2.2 PR-G automatically activates.

3. If operator wants to **defer activation** (keep Noop in prod temporarily), set in deploy environment:
   ```
   DOMAIN_EVENT_SUBSCRIBERS=apps.eventbus.dispatcher.NoopSubscriber
   ```

4. Restart Celery workers (rolling).

### B. Rollback (deactivate AuditSubscriber)

1. Set in deploy env:
   ```
   DOMAIN_EVENT_SUBSCRIBERS=apps.eventbus.dispatcher.NoopSubscriber
   ```

2. Restart Celery workers.

3. New events flow into outbox but no AuditLog rows are written from the bus. Existing AuditLog rows are NOT removed.

### C. Add a new real subscriber

1. Land the subscriber class on `main` (e.g. `apps.loyalty.subscribers.LoyaltySubscriber`). It must implement `Subscriber.handle(envelope) -> None` (see `apps/eventbus/dispatcher.py::Subscriber` Protocol).

2. Update prod env (order matters — put high-throughput real subscribers BEFORE AuditSubscriber so AuditLog captures the post-processing outcome):
   ```
   DOMAIN_EVENT_SUBSCRIBERS=apps.loyalty.subscribers.LoyaltySubscriber,apps.eventbus.subscribers.AuditSubscriber
   ```

3. Restart Celery workers.

### D. Verify after activation

1. Trigger a known domain event flow on prod (e.g. a test booking via the bot).

2. Admin console → check `apps_eventbus_domainevent` table:
   ```sql
   SELECT event_name, is_dispatched, dispatched_at
   FROM apps_eventbus_domainevent
   ORDER BY event_id DESC LIMIT 5;
   ```
   Expected: `is_dispatched=true`, `dispatched_at` populated within ~60s of insert.

3. Admin console → check `apps_audit_auditlog`:
   ```sql
   SELECT action, created_at
   FROM apps_audit_auditlog
   WHERE action LIKE 'booking.%' OR action LIKE 'customer.%'
   ORDER BY created_at DESC LIMIT 5;
   ```
   Expected: one AuditLog row per recent DomainEvent row.

4. Check for DLQ entries (should be empty in a healthy run):
   ```sql
   SELECT COUNT(*) FROM apps_eventbus_domainevent WHERE dead_lettered_at IS NOT NULL;
   ```

## Verification

- DomainEvent row count and AuditLog row count grow in lockstep after activation (with ~60s lag for the Celery beat tick).
- No surge in Sentry errors tagged `eventbus.dispatch.subscriber_failed`.
- DLQ count stays at 0 (or grows much slower than the dispatch rate).

Time-to-stable: within 1 Celery beat tick after worker restart (~60s).

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 — outbox stuck, DLQ count exploding | Backend on-call | Telegram + PagerDuty |
| P1 — AuditLog growth abnormal | Backend lead | Telegram |
| Vendor | n/a (in-house) | — |

## Post-mortem template

Used after every non-trivial run.

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

## Changelog

- 2026-05-19 — Phase 2.2 PR-G — initial draft. AuditSubscriber default-active in production via settings/production.py override.
