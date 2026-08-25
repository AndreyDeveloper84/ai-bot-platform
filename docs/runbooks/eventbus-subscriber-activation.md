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

**Scope carve-out — the allowlist gates TENANT-SCOPED events only.** Four
contract events carry `tenant_id: null` by design (`user.profile.updated`,
`subscription.activated`, `subscription.past_due`, `billing.fee_charged`,
per `event-contract.md` §2 + AMD-015). They have no tenant dimension to check,
so `assert_envelope_tenant_authorized` admits them under the null-tenant rule
**before** the allowlist is consulted — configuring a narrow pilot event set
does not stop them. This is pre-existing contract behaviour that T-02
deliberately did not change (OD-T02-1 scope: "envelope без tenant_id —
сохранить существующее контрактное поведение"). Do not read "allowlist active"
in the boot log as "this is the complete ingest surface"; the log line names
the four exempt events.

Two properties bound that carve-out:

* **It is observable.** Every admitted tenant-null envelope emits
  `eventbus.ingest.tenant_verify_accepted verification_mode=tenant_null_carveout`
  with `event_id`, `event_name`, `user_id`, `correlation_id`. That line is the
  only way to enumerate which subjects were asserted with no tenant binding —
  alert on an anomalous `user_id` spread from this key.
* **It is temporary, not permanent.** The check is no longer short-circuited
  ahead of the canonical probe: once `TenantUserRelationship` ships (Sprint 1
  #246), a tenant-null envelope is verified against the subject — `user_id`
  must hold at least one active relationship with some tenant, else
  `no_active_relationship_user_scope`. No operator action is needed at that
  point; expect the reject reason to start appearing for unknown users.

**No wildcards.** `*`, `all`, `any`, `booking.*` are rejected by the parser.
There is no spelling that means "allow everything"; enumerate every value.

**Malformed configuration fails the process.** A bad UUID, an empty CSV
element, a stray trailing comma or a wildcard raises `ImproperlyConfigured`
at settings load — the app refuses to boot rather than start with a
half-parsed allowlist. Nothing about a parse error ever widens access.

**Owner decision OD-T02-5.** The controlled pilot ingests four events,
not three. `booking.confirmed` is included because PR-T02-2 moved
reminder scheduling from `booking.created` to `booking.confirmed`; without
it, online bookings that start as `awaiting_payment` would never receive
reminders, and every `booking.confirmed` would be rejected, retried, and
dead-lettered. The owner explicitly accepted the wider ingest surface
(4 of 18 contract event names) for the pilot.

**Example configuration** (one pilot tenant, the OD-T02-5 event set):

```bash
EVENT_INGEST_ALLOWED_TENANTS=9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c
EVENT_INGEST_ALLOWED_EVENTS=booking.created,booking.confirmed,booking.cancelled,appointment.rescheduled
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

The ingest returns to fail-closed. Bot-platform is then safe — but this is
**not a silent rollback**. A denied envelope raises `TenantAuthorizationError`,
which the dispatcher surfaces as `HANDLER_EXCEPTION` → HTTP 500. Per
`event-contract.md` §6.3/§6.4 Ayla treats 500 as retryable: 5 attempts with
backoff, then `dead=true` **and a PagerDuty alert on the `ayla-events`
rotation**. Rolling back therefore pages the Ayla on-call for every subsequent
tenant event.

Consequences to plan for:

- **Coordinate rollback with the Ayla on-call.** Silence or expect the
  `ayla-events` alerts for the duration.
- **The same applies to any event outside the pilot event set.** The
  OD-T02-5 set (`booking.created`, `booking.confirmed`, `booking.cancelled`,
  `appointment.rescheduled`) is 4 of the 18 contract event names. If Ayla
  emits `payment.*`, `review.created`, `service.updated`,
  `master.schedule.updated`, `booking.completed` or `booking.no_show` for an
  allowlisted pilot tenant, each one burns the retry budget and
  dead-letters. Confirm with the owner that the pilot event set matches
  what Ayla actually publishes **before** rollout.

`event_not_allowed` / `tenant_not_allowed` are configuration-permanent — a
retry can never succeed — so §6.3 arguably wants a 4xx here rather than 500.
Changing the dispatcher's outcome mapping is a cross-repo contract change and
is deliberately **out of scope for PR-T02-1**; it is a tracked follow-up.

**Public MVP limitation.** The allowlist is a *scope limiter, not a
relationship proof*. It bounds which tenants and events may be ingested; it
does **not** establish that `envelope.user_id` genuinely belongs to
`envelope.tenant_id`. Residual risks accepted for the Controlled Pilot:

- the ingest HMAC secret is installation-wide, so any holder can sign for
  any allowlisted tenant — the allowlist caps the blast radius, it does not
  eliminate it;
- an allowlisted tenant can assert events for arbitrary `user_id` values. The
  only detective control is the `user_id` field on every
  `tenant_verify_accepted` log line — alert on a tenant asserting an
  anomalous spread of user ids;
- the four tenant-null events above bypass the allowlist entirely (see the
  scope carve-out).

Public MVP MUST replace this with the full `TenantUserRelationship`
contract. Until then, keep the tenant allowlist to the handful of tenants
actually onboarded.

### F. Triage: Ayla events that never reach the mirror (DRF-1291)

Written after the 23.08 mirror-discrepancy analysis: Ayla's event journal
held 26 distinct bookings, the bot mirror 24, and both losses were
*delivery failures* nobody saw. The three signatures below cover every
"the mirror is short" case found there; each has a distinct cause and a
distinct recovery. **Do not batch-triage them** — a `pending` row that was
never attempted and a `dead` row are different defects.

**Reading Ayla's journal.** Delivery state lives on Ayla's
`OutboxEvent` rows: `bot_delivery_status` (`pending` / `failed` / `sent` /
`dead`), `bot_attempt_count`, `bot_last_error`, `bot_response_status`.

| Signature in Ayla's journal | Cause | Recovery |
|---|---|---|
| `pending` + `bot_attempt_count = 0` for days/weeks | **Never picked up.** Ayla sets `external_delivery_enabled` once, at emit time, from `OUTBOX_EXTERNAL_DELIVERY_TOPICS`; rows emitted while the topic was not allowlisted are permanently invisible to the publisher's eligibility filter. This is not a backlog that drains — nothing revisits the gate. | Ayla side: flip the flag for the affected rows (`UPDATE appointments_outboxevent SET external_delivery_enabled = true WHERE …`) so the publisher ships them. Bot side needs no fix: a late `booking.created` backfills missing mirror references (DRF-1110), and an out-of-order `booking.cancelled` fails retryable and lands in `HandlerFailureTracker`/DLQ on threshold instead of vanishing. |
| `dead` + `bot_last_error` ≈ `HTTP 401 {"reason": "hmac_mismatch"}` | **Secret drift.** Ayla's `AYLA_OUTBOUND_HMAC_SECRET` and the bot's `EVENT_INGEST_HMAC_SECRET` disagree (rotation skew, unset on one side). Ayla dead-letters on the *first* 4xx — no retry budget is burned. | Since DRF-1291 this is a **visible** failure on the bot side: ERROR log `eventbus.ingest.signature_failed reason=hmac_mismatch` plus a sampled `system.module.health.degraded` event (`module_name=eventbus.ingest`, `severity=error`, `metric=ingest_signature_failed:<reason>`) — the same alert surface as the dispatcher's DLQ quarantine. Re-sync both secrets, restart, then replay the dead rows from Ayla (`manage.py replay_dead_outbox_events`). |
| `booking.rescheduled` rows `pending` + `attempt_count = 0` while the paired `appointment.rescheduled` delivered `200` | **Intentional non-delivery (decision DRF-1291).** `booking.rescheduled` is the repo-local legacy alias; `appointment.rescheduled` is the canonical cross-repo DER contract (AYLA-DEC-0022/0036) and alone updates the mirror. | Do **not** add `booking.rescheduled` to `OUTBOX_EXTERNAL_DELIVERY_TOPICS` or `EVENT_INGEST_ALLOWED_EVENTS` — dual-shipping both contracts only risks double-applying (the legacy one carries no version to arbitrate with). The bot handler stays registered, so enabling it later is a config-only flip. Ayla-side follow-up (cross-repo): stop emitting the legacy topic into the external queue at all, so leftover rows cannot masquerade as a real backlog. |

If none of the three signatures match, the loss is in a layer this runbook
doesn't cover — escalate with the `event_id`s to the backend on-call; the
periodic mirror↔canon reconciliation sweep is tracked separately (DRF-1111,
DRF-1161).

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
- 2026-08-25 — DRF-1291 — added §F: triage for Ayla→mirror delivery failures (emit-time gate `pending`/`attempt_count=0`, `hmac_mismatch` now escalates to ERROR + sampled `system.module.health.degraded`, `booking.rescheduled` confirmed intentionally not shipped cross-service).
