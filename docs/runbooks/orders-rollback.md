# Runbook — Orders + YooKassa Retirement Rollback

> Status: **complete**
> Last exercised: _never (planned tabletop before 2026-07-15 pilot)_
> Target completion sprint: _Phase 0 / Bucket 6_
> Owner: _Gamma stream_

## Purpose

Reverses a problematic post-T4 deploy of PR #739 (`apps/orders` +
`apps/integrations/yookassa` retirement). The forward deploy is
documented in
[`orders-yookassa-retirement-deploy.md`](orders-yookassa-retirement-deploy.md);
this runbook covers the «something went catastrophically wrong»
path.

Two distinct rollback strategies are needed because the DROP TABLE
in `0002_drop_orders_tables.py` is destructive AND payment lifecycle
also routed traffic to Ayla (T1 cabinet flip). The strategy depends
on how soon after T4 the regression was caught.

| Path | Use when | Reversal time |
|---|---|---|
| **A: Within-24h restore** | < 24 hours since T4 deploy + pre-T4 `pg_dump` snapshot still on disk + YooKassa Cabinet operator reachable | ≤ 30 min |
| **B: Accept-and-replay** (post-24h OR snapshot lost) | > 24 hours since T4 OR snapshot unrecoverable OR Cabinet operator unreachable | ≤ 4 hours |

Path A restores the tables AND re-points YooKassa back at
bot-platform — full revert of T1+T4 in one motion. Path B
accepts the divergence (Ayla remains the canonical payment SoR)
and only repairs side-effects via event replay.

Path A is strictly preferable when available because it minimises
data-loss surface. Path B exists because Path A's preconditions
expire quickly.

## Trigger / when to run

- **Hard fail post-T4**: Ayla `/api/v1/payments/webhook` returns 5xx
  for > 5 minutes after T4 deploy, OR ingest dispatcher P95 > 10s
  for `payment.*` events, OR `payment.captured` consumer error rate
  > 5% for > 10 minutes.
- **Sentry P0**: any non-recoverable error class tagged
  `pipeline_step=payment.*` post-T4.
- **Founder / tech-lead manual**: pre-emptive revert before
  customer-impact reports arrive.
- **NOT a trigger**: `webhook_received_after_retirement` audit rows
  in the soak window — that's expected forensic capture per the
  yookassa_retired mini-app (#732 / #768). Sampled volume is
  bounded.

## Prerequisites

| Variable / access | Where | Used by |
|---|---|---|
| `BOT_PLATFORM_PRIMARY_DSN` | Vault → `/etc/ai-bot-platform/.env`. Application user. | `pg_restore`, verification queries. |
| DB superuser access | `sudo -u postgres psql` on DB host OR explicit superuser DSN from DB admin. | Pre-restore: ensure clean slate. Path A only. |
| YooKassa Personal Cabinet credentials | Ops / founder | Path A only — flip URL back to bot-platform. |
| Pre-T4 snapshot file | `/backups/bot-platform/orders-pre-T4-YYYYMMDD-HHMMSS.dump` | Path A only — produced by step 2 of the deploy runbook. |
| Ayla djangoproject outbox replay tooling | Ayla ops | Path B only — replays missed `payment.*` events. |

## Path A — Within-24h restore

**Preconditions:** all four:

1. T4 deploy completed less than 24 hours ago.
2. `pg_dump` snapshot file from deploy step 2 is on disk + readable.
3. YooKassa Cabinet operator reachable and willing to flip URL back.
4. No new YooKassa traffic has been processed by Ayla (or any
   processed events are recoverable from Ayla's outbox).

If ANY precondition fails → switch to Path B.

### Step-by-step Path A

1. **Halt incoming YooKassa traffic at source.** Flip the YooKassa
   Personal Cabinet webhook URL FROM Ayla BACK TO bot-platform
   (`https://<bot-platform-prod>/api/v1/yookassa/webhook/`). This
   stops Ayla from receiving new events while we restore.

   Note: the bot-platform URL currently returns 410 Gone via the
   `apps.integrations.yookassa_retired` mini-app. **That's
   intentional during the flip transition** — incoming retries get
   forensic capture but don't process. Real processing resumes
   after step 4.
2. **Identify the snapshot file.**

   ```bash
   ssh prod
   ls -lh /backups/bot-platform/orders-pre-T4-*.dump
   # Pick the most recent one (suffix is YYYYMMDD-HHMMSS).
   ```

3. **Restore the dropped tables** (as DB superuser — `pg_restore`
   needs CREATE TABLE permission):

   ```bash
   # On the DB host:
   sudo -u postgres pg_restore \
     --dbname=ai_bot_platform \
     --table=orders_order \
     --table=orders_paymentevent \
     /backups/bot-platform/orders-pre-T4-<TIMESTAMP>.dump
   ```

   `pg_restore` does not drop existing tables. Both `orders_order`
   + `orders_paymentevent` are gone post-T4, so CREATE proceeds
   cleanly. If `pg_restore` errors with «relation already exists»,
   someone partially restored — investigate before retrying.
4. **Rebuild Django migration history.** The
   `0002_drop_orders_tables` migration is still recorded as applied,
   but the tables now exist again. Reverse `0002` to align reality:

   ```bash
   sudo -u ai-bot-platform uv run python manage.py migrate \
     orders 0001_initial
   ```

   This walks the migration history backward to `0001_initial` —
   Django will note `0002_drop_orders_tables` as unapplied. Tables
   stay as restored.
5. **Revert the code deploy.** Ship the pre-#739 image (the merge
   commit immediately before `cb030c17fd66caae65a6d315caa20c418347c450`).
   Use `docs/runbooks/rollback-procedure.md` Path B (full image
   rollback) for the mechanics.
6. **Switch YooKassa Cabinet URL** to point AT bot-platform (NOT
   the 410 Gone yookassa_retired path — point at the ACTUAL revived
   `/api/v1/yookassa/webhook/` route now that the code rollback
   restored the live handler).
7. **Replay 410-captured retries.** Query the audit log for any
   retries that hit the 410 endpoint during the rollback window:

   ```sql
   SELECT created_at, payload
   FROM audit_auditlog
   WHERE action = 'yookassa.webhook_received_after_retirement'
     AND created_at >= '<T4 timestamp>'
   ORDER BY created_at;
   ```

   Each row represents a retry attempt that didn't process. Ask
   ops to manually trigger YooKassa to resend (Cabinet has a
   per-payment «resend webhook» button) OR accept the small data
   loss (sample-bounded by the 30-day retention window of YooKassa
   retries).

### Path A verification

After all 7 steps:

1. `manage.py showmigrations orders` → `[X] 0001_initial`,
   `[ ] 0002_drop_orders_tables`.
2. `\d orders_order` + `\d orders_paymentevent` → both report
   present.
3. `curl -X POST /api/v1/yookassa/webhook/` → returns the LIVE
   webhook handler's expected response (NOT 410). Test with a
   YooKassa-shaped sample payload.
4. Audit log shows the LIVE webhook handler's actions
   (`yookassa.cert.payment_captured` etc.) appearing again for
   new YooKassa events.

Time-to-stable target: 30 min from operator decision to full
verification.

## Path B — Accept-and-replay (post-24h or snapshot lost)

**Preconditions:** Path A's preconditions failed (snapshot gone /
> 24h elapsed / Cabinet operator unreachable). Path B accepts
that the canonical payment SoR has migrated to Ayla and only
repairs bot-platform-side observability.

### Step-by-step Path B

1. **Confirm Ayla is processing payment events correctly.** Check
   Ayla's payment dashboard / Sentry for the post-T1 window. If
   Ayla itself is broken, this is a much larger incident — escalate
   to founder + Alpha tech lead.
2. **Identify the divergence window.** The window starts at T4
   deploy time and ends at incident detection. During this window:
   - bot-platform's `payment.*` consumers may have failed to update
     Conversation rows.
   - Loyalty fan-out events may have been missed.
   - Customer DM for `payment.failed` N=3 threshold may have been
     missed.
3. **Pull the Ayla outbox replay tooling.** Ayla djangoproject has
   a management command to replay events to bot-platform. The
   command lives in Ayla repo (not bot-platform); ask Alpha tech
   lead to invoke:

   ```bash
   # On Ayla side:
   manage.py replay_outbox_to_consumer \
     --consumer=bot-platform \
     --event-prefix=payment. \
     --since='<T4 timestamp>' \
     --until='<incident detection timestamp>'
   ```

   This re-sends every `payment.*` event in the window through the
   normal HMAC-signed POST to bot-platform's
   `/api/v1/internal/events/ingest`. The consumer's idempotency
   short-circuit (`Conversation.last_payment_event_id`) is what
   makes replay safe — events that DID land update no further;
   events that missed update Conversation rows for the first time.
4. **Verify bot-platform's catch-up.** Query the
   `eventbus_ingestdedupe` table for the divergence window:

   ```sql
   SELECT event_name, count(*)
   FROM eventbus_ingestdedupe
   WHERE first_seen_at >= '<T4 timestamp>'
     AND event_name LIKE 'payment.%'
   GROUP BY event_name;
   ```

   Counts should match Ayla's outbox count for the same window.
5. **Audit the customer-facing side.** For each affected customer,
   manually review whether the N=3 threshold DM (#738) should have
   fired and didn't. If yes, decide per-case whether to send a
   make-good DM. This is the only piece that can't be replayed
   atomically — DM is one-shot and the customer may have already
   self-resolved (paid via alternate channel, abandoned booking).

### Path B verification

1. Ayla outbox count + bot-platform `eventbus_ingestdedupe` count
   match for the divergence window.
2. No `HANDLER_EXCEPTION` audit rows for the replayed events.
3. Sample 5 affected Conversation rows manually — `last_payment_*`
   fields populated as expected for the replayed events.

Time-to-stable target: 4 hours from operator decision to
verification (longer than Path A because replay touches many
events and requires Alpha coordination).

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (Ayla payment processing broken) | Founder + Alpha tech lead | Slack `#ops-p0` + page rotation |
| P1 (bot-platform divergence detected) | SRE on-call + Gamma stream | PagerDuty + Slack `#phase0-gamma` |
| YooKassa Cabinet flip (Path A) | Ops + founder | Slack `#ops` + DM |
| Replay tooling (Path B) | Alpha tech lead | Slack `#phase0-alpha` |

## Post-mortem template

A rollback invocation is ALWAYS a P0/P1 event — write up regardless
of outcome.

- **What happened.** When was the regression detected? What were the
  symptoms?
- **What was the trigger.** Specific monitor / customer report /
  Sentry alert.
- **What did we expect — what actually happened.** Compared to T4
  deploy expectations.
- **Which path was taken — and why** (or why both, if Path A failed
  and we fell to Path B).
- **How long did it take to detect / mitigate / resolve.**
- **What we learned about #739, the deploy runbook, or this
  rollback runbook.**
- **Action items** (owner + deadline). Specifically: was the
  preconditions table accurate? Was the time-to-stable target met?

## Related runbooks

- [`docs/runbooks/orders-yookassa-retirement-deploy.md`](orders-yookassa-retirement-deploy.md) — the forward deploy this runbook reverses.
- [`docs/runbooks/rollback-procedure.md`](rollback-procedure.md) — generic Sprint-10 canary + image rollback procedure. Path A step 5 references it.
- [`docs/runbooks/disaster-recovery.md`](disaster-recovery.md) — full DR (use only if both Path A AND Path B fail).
- **PR #768** `apps.integrations.yookassa_retired` mini-app — the 410 Gone endpoint that captures retries during the flip window. Path A step 7 queries its audit rows.
- **PR #790** (#730) `relabel_legacy_orders_targets` management command — relabels historic audit rows pre-T4. Path A step 4 (migration history rebuild) does NOT need to reverse this relabel; the legacy_orders.tasks_archived label is harmless after the actual tables come back. A separate (manual) `UPDATE audit_auditlog SET target='orders.tasks' WHERE target='legacy_orders.tasks_archived'` is optional cleanup post-Path-A.

## Changelog

- _2026-05-26_ — Gamma stream — initial complete version (issue
  #736 closeout, F4 follow-up from Round-1 adversarial on #739,
  agent `a0e15c3aba9de55c6`). PRE_PILOT delivery for 2026-07-15
  pilot. Tabletop dry-run planned pre-pilot.
