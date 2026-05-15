# Runbook: STRICT_TENANT_SCOPE=strict production flip

> Status: **complete**
> Last exercised: _staging dry-run pending Sprint 10 / F-dry (DRF-868)_
> Owner: Lead
> Sprint 10 / F-flip (DRF-869) — Sprint 8 / F1 (DRF-730) was canceled and
> respawned here under the new Sprint 10 scope.

## Purpose

Flip the production `STRICT_TENANT_SCOPE` from `audit` (default) to
`strict`. Once `strict`, any unscoped ORM query (missing
`tenant_scope` ContextVar or mismatched tenant header) raises
`CrossTenantError` instead of returning silent / wrong rows.

This is **the last load-bearing move before the X-track canary**
(DRF-874..877). Doing it wrong means production starts 500-ing on any
code path that still relies on the audit-mode escape hatch.

## Two env vars — both must move together

| Env var | Before flip | After flip | Why |
|---|---|---|---|
| `STRICT_TENANT_SCOPE` | `audit` | `strict` | Switches manager behaviour: silent log → raise `CrossTenantError` |
| `STRICT_SCOPE_FLIP_AT` | _(unset)_ | `<ISO 8601 UTC>` | Arms the F2 monitor (`monitor_post_flip_violations`) for the 24h post-flip window |

Setting **only** `STRICT_TENANT_SCOPE=strict` enforces the rule but
leaves the F2 monitor idle — you'd get strict-mode protection without
the post-flip retro telemetry. Always set both.

## Trigger / when to run

- **End of Sprint 10 F-track (planned)** — once **all** the pre-flight
  checks below have been green for the previous 7 consecutive days
  AND the staging dry-run (F-dry / DRF-868) has executed cleanly.
- **Never** before then — the audit window exists precisely so silent
  bugs surface without breaking traffic.

## Pre-flight checks (ALL must pass)

| Check | Target | How to verify |
|---|---|---|
| **7-day clean delta** | 7 consecutive `ShadowDeltaSnapshot` rows with `intent_agreement ≥ 0.95` | Open `/admin/observability/shadow-delta/` — last 7 rows ≥ 95% |
| **Zero scope violations** | last 7 days | `AuditLog.all_tenants.filter(action__in=["tenant_scope_violation", "tenancy.scope.cross_tenant_attempt"], created_at__gte=now - timedelta(days=7)).count() == 0` (F2 monitor checks both action names) |
| **`/readyz/` green** | last 7 days | Watch the readyz log — no `unhealthy` window > 15 min |
| **Sentry P0 = 0** | last 7 days | Sentry dashboard filter `level:fatal environment:production` |
| **Rollback runbook exercised on staging** | once this Sprint | `docs/runbooks/rollback-procedure.md` changelog has a recent date (Sprint 10 / X-rollback DRF-872) |
| **Alerting smoke green** | once this Sprint | `python manage.py smoke_alert --severity=critical` from prod → Telegram + Sentry both receive within 60s (per `on-call.md` § Smoke test) |
| **Staging F-dry drill completed** | once this Sprint | DRF-868 ticket Done; staging audit log shows `observability.strict_scope.violation_detected` from the seeded test |

If ANY check fails: **DO NOT FLIP.** Extend shadow another 24h and
re-evaluate.

## Step-by-step procedure

### Step 0 — Staging F-dry drill (DRF-868, one-time, before first prod flip)

Practice the entire procedure on staging FIRST, **including a
deliberately-seeded violation** so we confirm the F2 monitor + Telegram
alert pipeline both fire end-to-end. Same env file shape, same restart
pattern, same verification queries.

```sh
ssh ops@stg-platform.penza.taxi
sudo nano /etc/ai-bot-platform/.env
# Change BOTH:
#   STRICT_TENANT_SCOPE=audit  →  STRICT_TENANT_SCOPE=strict
#   add: STRICT_SCOPE_FLIP_AT=<now-as-iso8601-utc>
# Example: STRICT_SCOPE_FLIP_AT=2026-05-15T03:00:00+00:00

sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Watch boot logs for clean start (no `CrossTenantError` storm):
```sh
sudo docker compose logs -f --tail=200 web worker | grep -i "tenant_scope\|CrossTenantError"
```

Within 15 min, verify the F2 monitor armed itself by checking for the
heartbeat audit row:
```sh
sudo docker compose exec web uv run python manage.py shell -c "
from apps.audit.models import AuditLog
print(AuditLog.all_tenants.filter(action='observability.post_flip.checked').order_by('-created_at').first())
"
```

**Seed a synthetic violation** to validate the alert path:
```sh
sudo docker compose exec web uv run python manage.py shell -c "
from apps.audit.models import AuditLog
AuditLog.all_tenants.create(action='tenant_scope_violation', target='Conversation', payload={'note': 'F-dry drill'})
print('seeded')
"
```

Within the next 15-minute F2 tick, expect:
- Telegram alert in the alerts channel (per `on-call.md`)
- Sentry event with `alert.severity=critical` tag
- `observability.strict_scope.violation_detected` audit row written

After confirming all three fired: clear `STRICT_SCOPE_FLIP_AT` from
staging env, restart staging worker, mark DRF-868 Done.

If staging surfaces a real (non-seeded) violation → fix the offending
code path, re-test, do NOT proceed to prod.

### Step 1 — Schedule window (24h ahead)

Post in the Telegram admin chat (NOT the alerts channel):
```
📅 STRICT_TENANT_SCOPE prod flip planned for <YYYY-MM-DD HH:MM> МСК
Pre-flight: all green (7-day delta / 0 violations / readyz green / smoke green)
Rollback path: docs/runbooks/strict-scope-flip.md § Rollback procedure
```

On-call confirms availability per `docs/runbooks/on-call.md`. Phase 0
on-call is single-human (Lead) — pick a window where Lead is awake +
reachable for the full 24h F2 monitor window, not just the 60-min soak.

### Step 2 — Pre-flip snapshot (5 min before flip)

Record current state for the post-mortem if anything goes wrong:
```sh
ssh ops@app-platform.penza.taxi
sudo docker compose exec web uv run python manage.py shell -c "
from apps.audit.models import AuditLog
from datetime import datetime, timedelta, timezone
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
print('24h audit count:', AuditLog.all_tenants.filter(created_at__gte=cutoff).count())
print('24h violations:', AuditLog.all_tenants.filter(action='tenant_scope_violation', created_at__gte=cutoff).count())
"
```

Capture the output. The "24h violations" number MUST be zero.

### Step 3 — Flip the env vars + roll services (90 sec)

```sh
sudo cp /etc/ai-bot-platform/.env /etc/ai-bot-platform/.env.bak.$(date +%Y%m%d-%H%M)
sudo nano /etc/ai-bot-platform/.env
# Change BOTH:
#   STRICT_TENANT_SCOPE=audit  →  STRICT_TENANT_SCOPE=strict
#   add (or replace): STRICT_SCOPE_FLIP_AT=<now-as-iso8601-utc>
# Example value: STRICT_SCOPE_FLIP_AT=2026-05-22T03:00:00+00:00

sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Watch boot logs until both services are healthy:
```sh
sudo docker compose logs -f --tail=50 web worker
# Expect: '/readyz/ status: healthy' within 30s for each service.
# Expect: NO 'CrossTenantError' lines. Any → rollback immediately (§ Rollback).
```

### Step 4 — F2 verification monitor (Celery beat starts automatically)

F2 / DRF-731 wired the `monitor_post_flip_violations` Celery task. It
runs every 15 minutes for 24 hours after F1 and pages on any non-zero
`tenant_scope_violation` count. Confirm it's scheduled:

```sh
sudo docker compose exec worker celery -A config inspect scheduled \
  | grep monitor_post_flip_violations
```

### Step 5 — 60-minute soak (active watch)

For the first hour post-flip the on-call operator actively watches:
- Sentry — any new `CrossTenantError` events.
- Telegram alerts channel — F2 monitor pages on any violation.
- `/readyz/` — must stay green.
- MAX webhook latency — should be unchanged (strict mode is in-process check, no I/O).

### Step 6 — Confirm + close

After 60 clean minutes, post in Telegram admin chat:
```
✅ STRICT_TENANT_SCOPE=strict LIVE in prod
Pre-flip violations (24h): 0
Post-flip violations (60 min): 0
F2 24h monitor: running until <timestamp + 24h>
```

### Step 7 — F-post (DRF-870): 24h retro + clear STRICT_SCOPE_FLIP_AT

24 hours after Step 3, the F2 monitor self-stops (the
`monitor_post_flip_violations` task checks the 24h window and no-ops
when expired). Operator runs the post-flip retro:

```sh
sudo docker compose exec web uv run python manage.py shell -c "
from apps.audit.models import AuditLog
from datetime import timedelta
from django.utils import timezone
since = timezone.now() - timedelta(hours=24)
heartbeats = AuditLog.all_tenants.filter(action='observability.post_flip.checked', created_at__gte=since).count()
violations = AuditLog.all_tenants.filter(action__in=['tenant_scope_violation', 'tenancy.scope.cross_tenant_attempt'], created_at__gte=since).count()
print(f'heartbeats: {heartbeats} (expect ~96)')
print(f'violations: {violations} (expect 0)')
"
```

If heartbeats ≈ 96 (±2) AND violations = 0:
```sh
sudo nano /etc/ai-bot-platform/.env
# REMOVE the STRICT_SCOPE_FLIP_AT line entirely.
sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps worker
```

F2 monitor enters idle state (no-op on every tick). The flip is now
permanent; this runbook stays load-bearing only for the next rollback
emergency or quarterly drill.

If violations > 0 OR heartbeats < 90: investigate from the audit log
+ Sentry events first; rollback per § Rollback if any are unexplained.

## Rollback procedure (≤ 60 sec)

If ANY post-flip violation lands OR Sentry surfaces an unexpected error
class, rollback **immediately** — do not investigate from the live
stack. Per `on-call.md` § Acknowledge SLA, critical-severity breach
=> rollback first, post-mortem after.

```sh
ssh ops@app-platform.penza.taxi
sudo cp /etc/ai-bot-platform/.env.bak.<latest> /etc/ai-bot-platform/.env
sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Verify both env vars reverted:
```sh
sudo docker compose exec web env | grep -E 'STRICT_TENANT_SCOPE|STRICT_SCOPE_FLIP_AT'
# Expect: STRICT_TENANT_SCOPE=audit
# Expect: STRICT_SCOPE_FLIP_AT either unset OR an old value (which the
# F2 monitor will see as a closed window and idle on).
```

Post in Telegram admin chat:
```
🔄 STRICT_TENANT_SCOPE rolled back to audit
Reason: <one-line>
Next: root-cause owner = <name>; re-attempt after fix
```

See also: [`rollback-procedure.md`](rollback-procedure.md) — the
general rollback playbook this scope-flip rollback is a special case of.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Service boot loop with `CrossTenantError` on startup | A management command / migration runs outside `tenant_scope` | Find the call site; wrap in `with tenant_scope(tenant):` or use `Model.all_tenants` manager |
| Sentry: `CrossTenantError` from a Celery worker | Worker task missing `current_tenant` ContextVar setup | Audit the offending task; restore `tenant_scope` in the task body |
| F2 monitor pages within minutes of flip | Pipeline path uses default manager without tenant context | Roll back via the procedure above; fix the code path; retry next maintenance window |

## Escalation contacts

Phase 0 has a single-human on-call (Lead) — there is no second-line to
escalate to. See `docs/runbooks/on-call.md` § Escalation for the
acknowledged-risk rationale + mitigations.

| Severity | Action |
|---|---|
| P0 (violation storm / boot loop) | Rollback first (above), debug from audit log + Sentry events. No second human to escalate to. |
| P1 (single endpoint failing) | Open Linear ticket; stay flipped if the path is non-critical AND violations contained to one tenant. |

When Phase 1 adds a 2nd human to on-call, this table gets a real
escalation timer.

## Related runbooks

* [`on-call.md`](on-call.md) — alerting routing + AI backup boundary
* [`canary-ramp.md`](canary-ramp.md) — depends on this flip being clean
  (criterion 5: zero post-flip violations)
* [`rollback-procedure.md`](rollback-procedure.md) — general rollback
  playbook this is a special case of
* [`shadow-mode-launch.md`](shadow-mode-launch.md) — shadow mode is the
  pre-condition for the 7-day clean delta gate

## Changelog

- 2026-05-13 — Lead — Sprint 8 / R3 (DRF-729) initial draft.
- 2026-05-15 — Lead — Sprint 10 polish (DRF-868 + DRF-869 prep):
  added STRICT_SCOPE_FLIP_AT env var semantics, Step 0 violation-seed
  drill (F-dry), Step 7 F-post 24h retro, Telegram-only alerting
  references (PD removed per DRF-862 Won't Do), cross-links updated.
  Status: **complete** — runbook is now operator-ready for F-dry + F-flip
  execution. Will get a "Last exercised: <date>" stamp post-F-dry.
