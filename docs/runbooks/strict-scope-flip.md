# Runbook: STRICT_TENANT_SCOPE=strict production flip

> Status: **draft**
> Last exercised: _staging dry-run pending Sprint 8 / R3_
> Target completion sprint: **Sprint 8 / F1 (DRF-730)** — the actual flip is
> the last move of Sprint 8 once shadow has been clean for 7 days.
> Owner: Platform Lead

## Purpose

Flip the production `STRICT_TENANT_SCOPE` from `audit` (default) to
`strict`, the IM-2 milestone from PHASE0_DESIGN §2.3 Sprint 8. Once
`strict`, any unscoped ORM query (missing `tenant_scope` ContextVar or
mismatched tenant header) raises `CrossTenantError` instead of returning
silent / wrong rows.

This is **the last load-bearing move of Sprint 8** before the Sprint 9
canary cutover. Doing it wrong means production starts 500-ing on any
code path that still relies on the audit-mode escape hatch.

## Trigger / when to run

- **End of Sprint 8 (planned)** — once **all** the pre-flight checks
  below have been green for the previous 7 consecutive days.
- **Never** before then — the audit window exists precisely so silent
  bugs surface without breaking traffic.

## Pre-flight checks (ALL must pass)

| Check | Target | How to verify |
|---|---|---|
| **7-day clean delta** | 7 consecutive `ShadowDeltaSnapshot` rows with `intent_agreement ≥ 0.95` | Open `/admin/observability/shadow/` — last 7 rows ≥ 95% |
| **Zero `tenant_scope_violation` audit rows** | last 7 days | `AuditLog.objects.filter(action="tenant_scope_violation", created_at__gte=now - timedelta(days=7)).count() == 0` |
| **`/readyz/` green** | last 7 days | Watch the readyz log / Prometheus uptime panel — no `unhealthy` window > 15 min |
| **Sentry P0 = 0** | last 7 days | Sentry dashboard filter `level:fatal environment:production` |
| **Sprint 8 / R1 rollback runbook exercised on staging** | once this sprint | docs/runbooks/rollback-procedure.md changelog has a Sprint 8 date |
| **Staging strict-scope flip drill completed** | once | See Step 0 below |

If ANY check fails: **DO NOT FLIP.** Extend shadow another 24h and
re-evaluate.

## Step-by-step procedure

### Step 0 — Staging dry-run (one-time, before first prod flip)

Practice the entire procedure on staging FIRST. Same env file shape,
same restart pattern, same verification queries.

```sh
ssh ops@stg-platform.penza.taxi
sudo nano /etc/ai-bot-platform/.env
# Change: STRICT_TENANT_SCOPE=audit  →  STRICT_TENANT_SCOPE=strict
sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Watch:
```sh
sudo docker compose logs -f --tail=200 web worker | grep -i "tenant_scope\|CrossTenantError"
```

Expected: zero violations in the next 60 minutes of staging traffic.
If staging surfaces a violation → fix the offending code path, re-test,
do NOT proceed to prod.

### Step 1 — Schedule window (24h ahead)

Post in `#ai-bot-ops`:
```
📅 STRICT_TENANT_SCOPE prod flip planned for <YYYY-MM-DD> 03:00 МСК
Pre-flight: all green (7-day delta / 0 violations / readyz green)
Rollback path: docs/runbooks/strict-scope-flip.md
```

Operator on call confirms availability.

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

### Step 3 — Flip the env var + roll services (90 sec)

```sh
sudo cp /etc/ai-bot-platform/.env /etc/ai-bot-platform/.env.bak.$(date +%Y%m%d-%H%M)
sudo nano /etc/ai-bot-platform/.env
# Change: STRICT_TENANT_SCOPE=audit  →  STRICT_TENANT_SCOPE=strict

sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Watch boot logs until both services are healthy:
```sh
sudo docker compose logs -f --tail=50 web worker
# Expect: '/readyz/ status: healthy' within 30s for each service.
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
- Telegram admin chat — F2 monitor alerts.
- `/readyz/` — must stay green.
- MAX webhook latency — should be unchanged (strict mode is in-process check, no I/O).

### Step 6 — Confirm + close

After 60 clean minutes:
```
✅ STRICT_TENANT_SCOPE=strict LIVE in prod
Pre-flip violations (24h): 0
Post-flip violations (60 min): 0
F2 24h monitor: running until <timestamp + 24h>
```

After 24 clean hours the F2 monitor auto-stops; runbook status flips to
**complete**.

## Rollback procedure (≤ 60 sec)

If ANY post-flip violation lands OR Sentry surfaces an unexpected error
class:

```sh
ssh ops@app-platform.penza.taxi
sudo cp /etc/ai-bot-platform/.env.bak.<latest> /etc/ai-bot-platform/.env
sudo docker compose --env-file /etc/ai-bot-platform/.env \
  up -d --force-recreate --no-deps web worker
```

Verify:
```sh
sudo docker compose exec web env | grep STRICT_TENANT_SCOPE
# Expect: STRICT_TENANT_SCOPE=audit
```

Post in `#ai-bot-ops`:
```
🔄 STRICT_TENANT_SCOPE rolled back to audit
Reason: <one-line>
Next: root-cause owner = <name>; re-attempt after fix
```

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Service boot loop with `CrossTenantError` on startup | A management command / migration runs outside `tenant_scope` | Find the call site; wrap in `with tenant_scope(tenant):` or use `Model.all_tenants` manager |
| Sentry: `CrossTenantError` from a Celery worker | Worker task missing `current_tenant` ContextVar setup | Audit the offending task; restore `tenant_scope` in the task body |
| F2 monitor pages within minutes of flip | Pipeline path uses default manager without tenant context | Roll back via the procedure above; fix the code path; retry next maintenance window |

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (full outage / violation storm) | Platform Lead | PagerDuty + MAX admin chat |
| P1 (single endpoint failing) | Platform team | Slack `#ai-bot-ops` |

## Changelog

- 2026-05-13 — Platform Lead — Sprint 8 / R3 (DRF-729) initial draft.
  Status: draft. Graduates to **complete** after the production flip
  completes one clean 24h window in Sprint 8 / F1 (DRF-730).
