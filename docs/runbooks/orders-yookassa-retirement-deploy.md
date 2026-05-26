# Runbook — Orders + YooKassa Retirement Deploy

> Status: **draft**
> Last exercised: _never (pre-pilot, target window post-#739 merge 2026-05-26)_
> Target completion sprint: _Phase 0 / Bucket 6_
> Owner: _Gamma stream_

## Purpose

Applies migration `apps/orders/migrations/0002_drop_orders_tables.py`
(PR #739, merged 2026-05-26) which permanently retires the
`orders_order` + `orders_paymentevent` tables. Payment lifecycle has
moved to Ayla djangoproject per ADR-0009 §Domain ownership.

Also covers the recovery procedure if the migration fails mid-flight
— PRE_PILOT issue #731 (Round-1 adversarial finding on #739, agent
`a0e15c3aba9de55c6`).

## Trigger / when to run

- **Planned activity:** T4 deploy of #739 merge (post-T3 production
  data audit + post-T0-T2 cabinet flip soak window).
- **Recovery:** if `manage.py migrate` exits non-zero on
  `orders.0002_drop_orders_tables` and `showmigrations orders` shows
  partial state (`[X] 0001_initial [ ] 0002_drop_orders_tables`).

## Prerequisites — T0→T4 ordered timeline

The 4 preconditions are NOT an unordered checklist. They form a
strict temporal sequence: out-of-order execution causes silent
webhook loss or false-negative monitoring. Each timestamp MUST be
later than the previous. Full rationale lives in the migration
docstring at `apps/orders/migrations/0002_drop_orders_tables.py`
§«Pre-merge gates — ordered timeline».

| Gate | Sign-off | Description |
|---|---|---|
| **T0** | Alpha tech lead | Ayla `/api/v1/payments/webhook` production-ready: HMAC verified, idempotency wired, CI green. |
| **T1** | Ops (with Cabinet screenshot) | YooKassa Personal Cabinet webhook URL flipped from bot-platform to Ayla. **`T1 > T0`.** |
| **T2** | W2 monitoring | 24-hour zero-traffic soak on bot-platform `/api/v1/yookassa/webhook/` measured AFTER T1. **`T2_start > T1`** — if measured between T0 and T1, zero traffic is structurally guaranteed (Cabinet still points here) and the check has zero detection value. |
| **T3** | Tech lead | Production data audit (see §«T3 audit query» below). **`T3 > T2`.** |
| **T4** | This runbook | Deploy #739 merge → `manage.py migrate` runs DROP TABLE. **`T4 > T3`.** |

If any timestamp inversion is observed, extend the soak window so
that 24 contiguous hours follow T1. There is no rationalisation that
makes inverted ordering safe.

### T3 audit query

Run on **bot-platform's** prod replica (these are bot-platform
tables being dropped — running on Ayla's replica either errors with
`relation "orders_order" does not exist` and gets misread as «zero
rows», or hits an unrelated Ayla table of coincidentally similar
name). Use **raw SQL**, NOT Django shell — the shell would route
through `TenantScopedManager` and silently return `0` outside
`tenant_scope(...)`.

```bash
psql "$BOT_PLATFORM_REPLICA_DSN" -c \
  "SELECT count(*) FROM orders_order;"
psql "$BOT_PLATFORM_REPLICA_DSN" -c \
  "SELECT count(*) FROM orders_paymentevent \
   WHERE created_at > NOW() - INTERVAL '30 days';"
```

Decision:

- **Both zero OR only `paymentevent` non-zero** → proceed to T4.
- **`orders_order > 0`** → STOP, escalate. Data not migrated to
  Ayla yet; running 0002 would silently delete in-flight intents.

## Step-by-step T4 deploy

1. Confirm T0-T3 all signed off (see prerequisites). Do not proceed
   without all four.
2. Snapshot prod DB:

   ```bash
   pg_dump "$BOT_PLATFORM_PRIMARY_DSN" \
     -t orders_order -t orders_paymentevent \
     -F c -f /backups/bot-platform/orders-pre-T4-$(date +%Y%m%d-%H%M%S).dump
   ```

   Retain at least 24 hours. F4 rollback runbook (issue #736)
   references this dump for the within-24h rollback path.
3. Deploy #739 merge via standard rolling restart (`docs/runbooks/
   server-deployment.md`).
4. Inspect the migration plan **before** applying:

   ```bash
   ssh prod
   cd /opt/ai-bot-platform
   sudo -u ai-bot-platform uv run python manage.py migrate --plan | grep orders
   ```

   Expected output:

   ```
     orders.0002_drop_orders_tables
   ```

   (Single PENDING line. If you see anything else, escalate — the
   #739 deploy may not have landed.)
5. Apply migration:

   ```bash
   sudo -u ai-bot-platform uv run python manage.py migrate orders 0002
   ```

   Expected output:

   ```
   Operations to perform:
     Target specific migration: 0002_drop_orders_tables, from orders
   Running migrations:
     Applying orders.0002_drop_orders_tables... OK
   ```

   On exit code 0 → proceed to verification (§«Verification»).
   On non-zero exit → §«Failure mode».

## Verification

After successful migrate:

1. Confirm migration recorded as applied:

   ```bash
   sudo -u ai-bot-platform uv run python manage.py showmigrations orders
   ```

   Expected:

   ```
   orders
    [X] 0001_initial
    [X] 0002_drop_orders_tables
   ```

2. Confirm tables actually dropped:

   ```bash
   psql "$BOT_PLATFORM_PRIMARY_DSN" -c "\d orders_order"
   psql "$BOT_PLATFORM_PRIMARY_DSN" -c "\d orders_paymentevent"
   ```

   Both MUST report «Did not find any relation named ...».
3. Confirm the `/api/v1/yookassa/webhook/` URL still resolves to
   410 Gone (PR #768 `yookassa_retired` mini-app):

   ```bash
   curl -X POST -i https://<bot-platform-prod>/api/v1/yookassa/webhook/ \
     -H "Content-Type: application/json" -d '{}'
   ```

   Expected: `HTTP/1.1 410 Gone` with body
   `{"status":"gone","reason":"yookassa_webhook_retired",...}`. A
   404 here means routing is broken — escalate.
4. 24h post-T4 audit check — no new `yookassa.*` audit rows beyond
   the sampled `webhook_received_after_retirement` rows (any
   pre-flip Cabinet leftovers; should trend to zero):

   ```sql
   SELECT action, count(*)
   FROM audit_log
   WHERE created_at > NOW() - INTERVAL '24 hours'
     AND action LIKE 'yookassa%'
   GROUP BY action;
   ```
5. Ayla-side payment event consumers still incrementing
   (`apps.eventbus.consumers.payment` — `payment.authorized`,
   `payment.captured`, `payment.failed`, `payment.refunded`).
   Check `/admin/observability/` Prometheus dashboard.

Time-to-stable target: all five indicators green within 30 minutes
of T4 deploy completion.

## Failure mode: 0002 mid-migration crash

`0002_drop_orders_tables.py` uses Django's default `atomic = True`
so BOTH `DeleteModel` operations run in a single transaction. On
PostgreSQL, `DROP TABLE` requires `ACCESS EXCLUSIVE` lock — a
long-lived transaction holding even a read lock could hang the
migration past the `lock_timeout`. The T2 24-hour zero-traffic soak
provides natural lock-safety because no application code writes to
these tables after T1, but operational failure modes remain:

- Network blip mid-statement
- OOM / OOM-killer reaping the Django process
- Database connection drop
- Deploy-time concurrent ALTER from another schema migration
  (shouldn't happen — no overlapping migrations in #739, but defence
  in depth)

Symptom: `manage.py migrate` exits non-zero. `showmigrations
orders` shows partial state. `\d orders_order` still reports the
table exists. Tenants holding the `PROTECT` FK to `tenancy_tenant`
can NOT be deleted via Django ORM until this is resolved.

### Recovery procedure

1. Connect as DB superuser:

   ```bash
   psql "$BOT_PLATFORM_PRIMARY_DSN" -U postgres
   ```

2. Confirm partial state:

   ```sql
   \d orders_order            -- should exist
   \d orders_paymentevent     -- may or may not (depending on crash timing)
   ```

3. Manual cascade drop (matches what 0002 would have done; CASCADE
   handles any cross-table FK that didn't already drop):

   ```sql
   BEGIN;
   DROP TABLE IF EXISTS orders_paymentevent CASCADE;
   DROP TABLE IF EXISTS orders_order CASCADE;
   COMMIT;
   ```

   `IF EXISTS` is required — `orders_paymentevent` may already be
   gone if the crash happened between the two DeleteModel
   operations.
4. Mark the migration as applied in Django's history (the tables
   no longer exist, so Django would error trying to drop them
   again):

   ```bash
   sudo -u ai-bot-platform uv run python manage.py migrate --fake orders 0002
   ```

5. Verify final state — re-run §«Verification» steps 1-3.

### Post-mortem trigger

Any invocation of this recovery procedure → write a post-mortem
(template below). Mid-migration crash on a destructive migration is
a non-trivial event even when recovered cleanly.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (data corruption / lost rows) | Tech lead + DB admin | Slack `#ops-p0` + page rotation |
| P1 (deploy stuck > 30 min) | SRE on-call | PagerDuty rotation |
| Pre-T4 query failed | Tech lead | Slack `#phase0-gamma` |

## Post-mortem template

Used after any non-trivial run — specifically after any recovery
procedure invocation.

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

## Related runbooks

- [`docs/runbooks/disaster-recovery.md`](disaster-recovery.md) — full DR procedure for catastrophic loss.
- [`docs/runbooks/rollback-procedure.md`](rollback-procedure.md) — general rollback procedure.
- [`docs/runbooks/server-deployment.md`](server-deployment.md) — standard deploy mechanics referenced in step 3.
- **F4 follow-up** [`docs/runbooks/orders-rollback.md`](orders-rollback.md) (issue #736, not yet written) — post-T4 rollback path: within-24h pgRestore + cabinet flip-back, >24h accept-divergence-and-replay.
- **F5 follow-up** (issue #737) — cleanup PR removing `apps.orders` stub + #768 `yookassa_retired` mini-app after 30-day soak.

## Changelog

- _2026-05-26_ — Gamma stream — initial draft (issue #731 closeout, PRE_PILOT for 2026-07-15 pilot).
