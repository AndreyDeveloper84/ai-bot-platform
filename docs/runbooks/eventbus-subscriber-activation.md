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

### E. Pilot-scoped tenant + event allowlists for cross-service ingest (T-02)

Applies to **inbound** envelopes from Ayla (`POST /api/v1/internal/events/ingest`),
not to the in-repo subscriber registry above.

**Why it exists.** `assert_envelope_tenant_authorized()` verifies
`(user_id, tenant_id)` against the canonical `TenantUserRelationship`
(ADR-0009 §Hard rule #6). That model lives in Ayla, so bot-platform cannot
import it, and the helper fails **closed** on every tenant-scoped envelope:
`TenantAuthorizationError → 500 → Ayla retry → DLQ`. Per owner decision
**OD-T02-1** the pilot does not implement the full relationship in bot and
does not use a global fail-open. Instead, two enumerated allowlists bound
what may be ingested.

**The two settings.**

| Setting | Meaning |
|---|---|
| `EVENT_INGEST_ALLOWED_TENANTS` | CSV of canonical hyphenated tenant UUIDs. |
| `EVENT_INGEST_ALLOWED_EVENTS` | CSV of event names. |

An envelope is admitted only when **all** of the following hold:

1. its `tenant_id` is in `EVENT_INGEST_ALLOWED_TENANTS`;
2. its `event_name` is in `EVENT_INGEST_ALLOWED_EVENTS`;
3. the tenant exists and is active in the bot DB;
4. it already passed HMAC verification and schema validation.

**Empty means DENY ALL.** Both default to empty, and an unset value is never
read as "no restriction". A half-configured pair (tenants but no events, or
vice versa) is also deny-all — the boot log flags it as `ERROR`.

**No wildcards.** `*`, `all`, `any`, `booking.*` are rejected by the parser.
There is no spelling that means "allow everything"; enumerate every value.

**Malformed configuration fails the process.** A bad UUID, an empty CSV
element, a stray trailing comma or a wildcard raises `ImproperlyConfigured`
at settings load — the app refuses to boot rather than start with a
half-parsed allowlist. Nothing about a parse error ever widens access.

**Example configuration** (one pilot tenant, the OD-T02-2 event set):

```bash
EVENT_INGEST_ALLOWED_TENANTS=9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c
EVENT_INGEST_ALLOWED_EVENTS=booking.created,booking.cancelled,appointment.rescheduled
```

Note `booking.rescheduled` is **not** in the pilot set — it is the
repo-local legacy alias; `appointment.rescheduled` is the canonical
cross-repo name.

**Boot-time signals** (`apps.eventbus.startup_checks`):

| Log key | Level | Meaning |
|---|---|---|
| `allowlist_empty` | WARNING | Both empty → fail-closed. Safe; the correct default. |
| `allowlist_half_configured` | ERROR | One side empty → deny-all that *looks* configured. |
| `allowlist_active` | INFO | Pilot scope in effect; lists the event names. |
| `allowlist_malformed` | ERROR | Unparseable → deny-all. |
| `allowlist_unknown_event` | ERROR | Name absent from the contract vocabulary — probably a typo. |
| `tenant_verify_fail_open_enabled` | WARNING `security=critical` | Global fail-open is ON. See below. |

**Per-event audit** (`apps.eventbus.ingest_tenancy`): `tenant_verify_accepted`
(INFO, `verification_mode=pilot_allowlist`) and `tenant_verify_rejected`
(WARNING) with `reason` ∈ {`tenant_not_allowed`, `event_not_allowed`,
`tenant_not_found`, `tenant_lookup_error`, `relationship_unavailable`,
`malformed_configuration`}. Identifiers only — payloads are never logged.

**Do NOT use `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN`.** It disables tenant
verification for *every* tenant and *every* event. It defaults to `False`,
staging no longer sets it, and it exists solely as an emergency escape
hatch. If you ever turn it on, the boot log and every admitted event carry
`security=critical`. This runbook does not recommend it under any
circumstance — widen the allowlists instead.

**Rollback.** Clear both variables and restart:

```bash
EVENT_INGEST_ALLOWED_TENANTS=
EVENT_INGEST_ALLOWED_EVENTS=
```

The ingest returns to fail-closed. Inbound tenant events will 500 and
dead-letter — which is the intended, safe state, not an outage to page on.

**Public MVP limitation.** The allowlist is a *scope limiter, not a
relationship proof*. It bounds which tenants and events may be ingested; it
does **not** establish that `envelope.user_id` genuinely belongs to
`envelope.tenant_id`. Residual risks accepted for the Controlled Pilot:

- the ingest HMAC secret is installation-wide, so any holder can sign for
  any allowlisted tenant — the allowlist caps the blast radius, it does not
  eliminate it;
- an allowlisted tenant can assert events for arbitrary `user_id` values.

Public MVP MUST replace this with the full `TenantUserRelationship`
contract. Until then, keep the tenant allowlist to the handful of tenants
actually onboarded.

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
- 2026-08-06 — T-02 PR-T02-1 — added §E: pilot-scoped `EVENT_INGEST_ALLOWED_TENANTS` / `EVENT_INGEST_ALLOWED_EVENTS` allowlists for cross-service ingest. Staging's unconditional `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True` removed.
