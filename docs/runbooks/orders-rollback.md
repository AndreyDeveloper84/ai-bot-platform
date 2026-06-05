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
   **after step 5** (code rollback restores the live handler).

   **Mute** the 410 rate-limit / sample dashboards / Sentry alerts
   before this step if practical — every YooKassa retry during
   steps 2-5 will hit the 410 path and contribute to the
   sampler's audit / log volume. Don't be alarmed at elevated
   warning-level signal during the restore window.
2. **Identify the snapshot file.**

   ```bash
   ssh prod
   ls -lh /backups/bot-platform/orders-pre-T4-*.dump
   # Pick the most recent one (suffix is YYYYMMDD-HHMMSS).
   ```

3. **Restore the dropped tables** (as DB superuser — `pg_restore`
   needs CREATE TABLE permission). Sanity-check the dump FIRST,
   then restore atomically:

   ```bash
   # On the DB host — list the dump's contents to confirm both
   # tables are present + the format matches what pg_dump -F c
   # produced in the deploy runbook step 2:
   sudo -u postgres pg_restore -l \
     /backups/bot-platform/orders-pre-T4-<TIMESTAMP>.dump \
     | grep -E 'orders_(order|paymentevent)'

   # Atomic restore — --single-transaction ensures partial failure
   # rolls back cleanly instead of leaving tables present but
   # missing indexes / FK constraints. --format=c is explicit
   # fail-fast guard against accidentally pointing at a plain-SQL
   # dump someone left in /backups/.
   sudo -u postgres pg_restore \
     --dbname=ai_bot_platform \
     --format=c \
     --single-transaction \
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
   but the tables now exist again. **Use `--fake`** to mark `0002`
   as unapplied WITHOUT running the schema operations — running the
   actual reverse SQL would crash with `relation "orders_paymentevent"
   already exists` because Django's `DeleteModel.database_backwards`
   issues a plain `CREATE TABLE` (no `IF NOT EXISTS`), and
   `pg_restore` in step 3 already recreated both tables with their
   indexes + FK constraints. Mirrors the symmetric `--fake` idiom
   used in the forward runbook §«Failure mode recovery»:

   ```bash
   sudo -u ai-bot-platform uv run python manage.py migrate \
     --fake orders 0001_initial
   ```

   After this, `showmigrations orders` reports
   `[X] 0001_initial`, `[ ] 0002_drop_orders_tables` — Django's
   history aligned with the restored DB.
5. **Revert the code deploy.** The pre-#739 image tag is the `dev`
   build immediately preceding the #739 merge
   (`cb030c17fd66caae65a6d315caa20c418347c450`). Identify it via:

   ```bash
   gh pr list --state merged --base dev \
     --search "merged:<DATE_BEFORE_T4>..<T4_DATE>" \
     --json number,mergedAt,headRefOid --limit 5
   ```

   Pick the youngest entry with `mergedAt < T4 timestamp`. Use
   that `headRefOid` (or its derived image tag) as the rollback
   target. Then follow `docs/runbooks/rollback-procedure.md`
   Path B (full image rollback) for the deploy mechanics.

   Pinning to an image tag (NOT «the commit immediately before
   cb030c1») avoids ambiguity on a non-linear `dev` graph with
   parallel-agent merges.
6. **Verify YooKassa Cabinet URL** is still set to bot-platform's
   `/api/v1/yookassa/webhook/` (you flipped it there in step 1;
   this is a defence-in-depth recheck — confirm no one else
   flipped it during steps 2-5). The URL string hasn't changed,
   but as of step 5 it now resolves to the **LIVE** webhook
   handler instead of the 410 Gone retired endpoint. Verify with
   the smoke test in §«Path A verification» step 3.
7. **Replay missed YooKassa events.** The 410 mini-app's audit
   log is **sampled** (per-IP +60s windows — see
   `apps/integrations/yookassa_retired/views.py:140-160`) so it
   UNDER-counts retries by an unknown factor, and YooKassa
   traffic typically arrives from a narrow source-IP range that
   compresses heavily under the sampler. **DO NOT** treat the
   audit query as the canonical source of missed events.

   **Canonical source: YooKassa Personal Cabinet's own event
   log.** Cabinet's per-payment view lists every webhook
   delivery attempt + its 4xx/5xx/2xx classification. For each
   payment with a 410 / 5xx in the rollback window, click the
   per-payment «resend webhook» button.

   The audit query below is a best-effort cross-check for the
   YooKassa-side audit — DO NOT rely on it as the only source:

   ```sql
   -- Best-effort sample (per-IP sampling under-counts):
   SELECT created_at, payload->>'remote_ip' AS remote_ip,
          payload->>'body_bytes' AS body_bytes
   FROM audit_auditlog
   WHERE action = 'yookassa.webhook_received_after_retirement'
     AND created_at >= '<T4 timestamp>'
   ORDER BY created_at;
   ```

   The payload deliberately omits the YooKassa event UUID (no
   body parsing per PII rule §7 — see views.py:35-50). Use
   `remote_ip` only to confirm the rows came from YooKassa's IP
   ranges, not from scanners.

   If Cabinet's resend window has closed (>24h since the original
   payment event), small per-payment data loss is unavoidable.

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

**Time-to-stable budget — 30-60 min realistic, escalate at 45 min
if not on track to converge.** Per-step honest estimate:

| Step | Realistic time |
|---|---|
| 1. Cabinet flip + propagation + alert muting | 3-10 min |
| 2. Find snapshot | 1 min |
| 3. `pg_restore` (atomic, single-transaction; thousands of rows + indexes + FKs) | 1-5 min |
| 4. `migrate --fake` | 30 sec |
| 5. Image rollback (per `rollback-procedure.md` Path B — ≤ 5 min target) | 5-10 min |
| 6. Cabinet URL recheck | 1 min |
| 7. Replay via Cabinet «resend» per-payment (variable — depends on payment count in window) | 5-30 min |
| Verification (4 substeps) | 2-5 min |
| **Total P50** | **~45 min** |
| **Total P95** | **~65 min** |

The 30-minute floor is only achievable on a drill with a small
payment-count window + Cabinet operator standing by. Escalate per
§Escalation contacts at 45 min if step 7 (resend loop) is dragging.

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
3. **Pull the Ayla outbox replay tooling.** Ayla djangoproject is
   expected to ship a management command to replay events to
   bot-platform. The command lives in Ayla repo (not bot-platform);
   ask Alpha tech lead to invoke:

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
   `/api/v1/internal/events/ingest`.

   **Phase-0 dependency:** `replay_outbox_to_consumer` is referenced
   here as a planned Alpha deliverable. If it is NOT shipped by the
   2026-07-15 pilot, Path B degrades to manual SQL replay against
   Ayla's `eventbus_outbox` rows + an ad-hoc HMAC-signed POST loop —
   escalate as a pre-pilot blocker to Alpha tech lead. Cross-track
   in Ayla's Phase-0 sprint plan.

   **Why replay is safe — three layers of idempotency** on the
   bot-platform consumer side:

   1. **Dispatcher-level (primary)** — `IngestDedupe` PK on
      `event_id` (`apps/eventbus/ingest_dispatcher.py:217-228`).
      Duplicate `event_id` short-circuits with outcome=DUPLICATE
      BEFORE the handler runs. No side effects, no Conversation
      read.
   2. **Handler-level (defence in depth)** —
      `Conversation.last_payment_event_id` check inside the
      `select_for_update` lock
      (`apps/eventbus/consumers/payment.py:209`). Protects against
      the category-2 risk that the dispatcher dedupe is disabled
      or bypassed.
   3. **Terminal-state dedupe** — `PaymentTerminalDedupe` on
      `(tenant_id, payment_id, terminal_state)` (`apps/eventbus/
      models.py`). Blocks double loyalty fan-out for
      `payment.captured` / `payment.refunded` even when Ayla
      regenerates the `event_id` for the same payment.

   So replay behaviour:

   - **Previously landed events** trigger layer 1 → DUPLICATE → no
     side effects (operator inspecting `IngestDedupe` for the
     event_id will see the row present; the dispatcher
     short-circuits BEFORE the handler invocation).
   - **Previously failed events** (handler exception → rolled back
     → no IngestDedupe row) execute cleanly on replay — this is
     the first successful handler run.
   - **Previously landed under DIFFERENT event_id** (Ayla outbox
     re-mint, edge case) trigger layer 3 for captured/refunded
     only. `payment.authorized` + `payment.failed` rely on layers
     1+2 (intentional — see consumer docstring at
     `apps/eventbus/consumers/payment.py:30-37`).
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
- _2026-05-26_ — Gamma stream — Round-1 friendly review (#806,
  agent `a91aabacbd8ae4f3f`) closeout: M1 corrected Path A step 4
  to `migrate --fake orders 0001_initial` (running the actual
  reverse SQL would CRASH with `relation already exists` because
  pg_restore already recreated tables in step 3 + Django's
  DeleteModel reverse issues plain CREATE TABLE); M2 corrected
  Path A step 7 to use YooKassa Cabinet's per-payment resend as
  canonical source of missed events (the 410 mini-app's audit
  log is sampled per-IP and under-counts); M3 added Phase-0
  dependency callout for Ayla's `replay_outbox_to_consumer`
  command + rewrote Path B step 3 with the actual 3-layer
  idempotency model (IngestDedupe primary, last_payment_event_id
  secondary, PaymentTerminalDedupe terminal-state); M4 corrected
  Path A step 5 to pin via `gh pr list` + image tag instead of
  ambiguous «commit immediately before cb030c1»; S1 added
  pg_restore `-l` sanity check + `--format=c` + `--single-
  transaction`; S2 replaced unrealistic 30-min target with
  honest 30-60 min per-step budget; S3 corrected off-by-one
  («after step 5» not «after step 4») + added alert-muting
  guidance; S4 clarified step 6 as verify-not-flip.
