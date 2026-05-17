# Runbook: Disaster recovery — Postgres backups + PITR

> Status: **draft**
> Last exercised: _never_ — first quarterly drill scheduled after deploy (DRF-852)
> Target completion sprint: _Phase 1 / PI2_
> Owner: Lead

## Purpose

Take a complete, restorable copy of production Postgres off the prod VM every
day, plus a continuous WAL stream, so that a disk-loss / corruption / "DROP
TABLE in prod" event recovers to **≤ 24 h RPO** (daily base backup) and
**≤ 1 h RTO** (operator-driven PITR from a fresh base + WAL replay).

The bot now persists money-and-reputation data (YooKassa orders per B7,
real `Order` rows per B3/B5, audit log per PI1) — losing the database is no
longer a development inconvenience, it's a refund-and-apology event.
This runbook is the only thing standing between that event and a recovery.

## Trigger / when to run

- **Deploy procedure** — first-time setup of backup infrastructure (sections
  §Deploy procedure + §Verification). Run **once** at PI2 cutover.
- **Restore procedure (PITR)** — production data loss / corruption / accidental
  destructive SQL. Sev1 per [`incident-response.md`](incident-response.md).
  Run §Restore procedure (PITR).
- **Quarterly drill** — every 90 days, exercise the restore on a staging /
  scratch VM. Run §Quarterly drill checklist.
- **Alert: backup freshness** — `check_backup_freshness.sh` reported stale
  (> 25 h since last base backup). Run §Monitoring → troubleshooting branch.
- **Alert: WAL archive lag** — `pg_stat_archiver` query exceeded threshold.
  Same troubleshooting branch.

---

## Scope

This runbook covers:

- Daily **base backup** via `pg_basebackup` to an S3-compatible bucket.
- Continuous **WAL archiving** via Postgres `archive_command` to the same
  bucket — enables point-in-time recovery between base backups.
- **Restore (PITR)** procedure for a fresh data directory from any moment in
  the retention window.
- **Monitoring** (backup freshness + WAL archive lag) and how it wires into
  the existing alerting channel (Telegram per `incident-response.md`).
- **Retention policy** (30 daily, 12 monthly) and pruning behaviour.

Out of scope (deferred or owned elsewhere):

- Cross-region replication — Phase 2 (single-region is acceptable for Phase 1,
  spec opt-out).
- Application-level dumps (`pg_dump`) — `pg_basebackup` + WAL gives us better
  RPO at lower operational cost. If a row-level export is needed for a
  one-off migration, run `pg_dump` ad-hoc from the restored copy.
- Redis / ChromaDB backup — Redis is cache-tier (regenerable from PG);
  ChromaDB has its own runbook ([`chromadb-auth.md`](chromadb-auth.md)) and
  KB sources are re-indexable from `KnowledgeBase` rows in PG.
- Terraform / IaC for the bucket — operator creates the bucket manually
  via the provider console (out of scope for Phase 1).

---

## Architecture

```
                          prod VM (app.penza.taxi)
        ┌──────────────────────────────────────────────────────┐
        │  Postgres 14+                                        │
        │  ┌────────────────────────────────────────┐          │
        │  │ archive_command =                      │          │
        │  │   pg_archive_wal.sh %p %f              │ ─── WAL ─┼──┐
        │  └────────────────────────────────────────┘          │  │
        │                                                       │  │
        │  cron 03:00 UTC daily:                                │  │
        │    pg_base_backup.sh ─── tar.gz ── pg_basebackup ─────┼──┤
        │                                                       │  │
        │  cron :15 hourly:                                     │  │
        │    check_backup_freshness.sh ─── healthcheck.io ping  │  │
        └──────────────────────────────────────────────────────┘  │
                                                                  │
                                          ┌───────────────────────▼─────────────┐
                                          │  S3-compatible bucket               │
                                          │   <bucket>/base/<YYYY-MM-DD>.tar.gz │
                                          │   <bucket>/wal/<WAL-segments>       │
                                          │                                     │
                                          │  Server-side encryption: SSE-S3     │
                                          │  Retention: 30 daily + 12 monthly   │
                                          └─────────────────────────────────────┘

Restore path (reverse):
  1. Pull latest base backup → restore tar to PGDATA
  2. recovery.signal + restore_command pulls WAL from bucket
  3. Postgres replays WAL to target time, then promotes
```

The same bucket holds both base backups (`base/` prefix) and WAL segments
(`wal/` prefix). One bucket, one credential, two prefixes — fewer things to
get wrong at 03:00 UTC.

**Note for operators familiar with the `mysite/formula_tela` repo**: that
repo likely has a `mysite/scripts/backup.sh` (or `mysite/scripts/db_backup.sh`)
which runs `pg_dump`-style nightly. This runbook uses `pg_basebackup` + WAL
instead — strictly better RPO/RTO. The two are not mutually exclusive; if
you want belt-and-braces, keep the formula_tela `pg_dump` script running for
human-readable SQL snapshots alongside this one.

---

## Pre-requisites

| Item | Why | How to verify |
|---|---|---|
| Postgres ≥ 14 | `pg_basebackup -X stream` + per-segment archiving | `psql -c "SHOW server_version;"` |
| `wal_level=replica` (or `logical`) | Required for WAL streaming | `psql -c "SHOW wal_level;"` |
| `archive_mode=on` | Enables `archive_command` to run | `psql -c "SHOW archive_mode;"` |
| `archive_command` set | Where WAL segments go | `psql -c "SHOW archive_command;"` |
| `max_wal_senders` ≥ 3 | `pg_basebackup -X stream` opens a replication slot | `psql -c "SHOW max_wal_senders;"` |
| S3-compatible bucket created | Backup destination | provider console |
| IAM access key + secret with `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`, `s3:DeleteObject` on the bucket | Scripts upload / list / prune | provider console |
| `awscli` v2 installed on prod | `pg_base_backup.sh` + `pg_archive_wal.sh` shell out to `aws s3 cp` | `aws --version` |
| Network egress to bucket endpoint from prod VM | Otherwise uploads silently retry forever | `curl -I $S3_ENDPOINT_URL` |
| `/etc/formula_tela/backup.env` (mode 600, root-owned) | Holds the IAM creds + bucket name | `stat -c "%a %U" /etc/formula_tela/backup.env` |

Required `postgresql.conf` lines (see `scripts/backup/examples/postgresql.conf.example`):

```
wal_level = replica
archive_mode = on
archive_command = '/usr/local/bin/pg_archive_wal.sh %p %f'
max_wal_senders = 5
```

A Postgres restart is required after changing `wal_level` or `archive_mode`.
`archive_command` itself can be changed with a reload (no restart).

---

## Deploy procedure

Run once on `app.penza.taxi` as `root` (or a sudoer who can edit
`/etc/postgresql/`). Each step ends with a verification — don't proceed if
the previous check failed.

### 1. Create the bucket + IAM user

Provider console actions (Yandex Object Storage / AWS S3 / MinIO — all work):

1. Create bucket `ai-bot-platform-prod-backups` (or whatever name you choose;
   put it in `S3_BUCKET` later). Region: closest to prod VM.
2. Enable **server-side encryption** — SSE-S3 / AES-256 default is sufficient
   for Phase 1. See §Encryption below for SSE-KMS upgrade.
3. Disable public access on the bucket.
4. Create a dedicated IAM user (e.g. `ai-bot-platform-backup`) with **only**
   `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`, `s3:DeleteObject` on
   that bucket. No console login. Generate an access key + secret.
5. Save the access key / secret in 1Password under `ops vault → backup-s3`.

### 2. Drop credentials onto the VM

```sh
sudo install -d -m 700 -o root -g root /etc/formula_tela
sudo install -m 600 -o root -g root /dev/stdin /etc/formula_tela/backup.env <<'EOF'
AWS_ACCESS_KEY_ID=<paste from 1Password>
AWS_SECRET_ACCESS_KEY=<paste from 1Password>
AWS_DEFAULT_REGION=ru-central1
S3_ENDPOINT_URL=https://storage.yandexcloud.net
S3_BUCKET=ai-bot-platform-prod-backups
PG_DATA_DIR=/var/lib/postgresql/14/main
PG_USER=postgres
RETENTION_DAILY_DAYS=30
RETENTION_MONTHLY_MONTHS=12
EOF

sudo stat -c "%a %U:%G %n" /etc/formula_tela/backup.env
# Expected: 600 root:root /etc/formula_tela/backup.env
```

### 3. Install the scripts

From your laptop, copy the four scripts and the example configs to the VM:

```sh
scp scripts/backup/pg_base_backup.sh \
    scripts/backup/pg_archive_wal.sh \
    scripts/backup/check_backup_freshness.sh \
    scripts/backup/restore_pitr.sh \
    ops@app.penza.taxi:/tmp/

ssh ops@app.penza.taxi sudo install -m 755 -o root -g root \
    /tmp/pg_base_backup.sh /tmp/pg_archive_wal.sh \
    /tmp/check_backup_freshness.sh /tmp/restore_pitr.sh \
    /usr/local/bin/

ssh ops@app.penza.taxi ls -l /usr/local/bin/pg_*.sh /usr/local/bin/check_backup_freshness.sh /usr/local/bin/restore_pitr.sh
# Expected: -rwxr-xr-x root root ... for all four
```

### 4. Add cron entries

Add to `/etc/cron.d/pg-backup` (root-owned, mode 644):

```cron
# /etc/cron.d/pg-backup — daily base backup + hourly freshness check
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=ops@formula-tela.example   # set to your real ops email

# Daily full backup at 03:00 UTC. 03:00 UTC = 06:00 МСК, matches the
# existing audit-cleanup window (PI1) — both run when traffic is lowest.
0 3 * * * root /usr/local/bin/pg_base_backup.sh >> /var/log/pg_base_backup.log 2>&1

# Hourly freshness check. On failure (stale > 25 h), curl the healthcheck.io
# fail endpoint so the on-call gets paged.
15 * * * * root /usr/local/bin/check_backup_freshness.sh >> /var/log/pg_backup_check.log 2>&1 || curl --silent --max-time 10 https://hc-ping.com/<your-uuid>/fail
```

Replace `<your-uuid>` with the healthcheck.io check UUID you create per
§Monitoring below.

### 5. Edit `postgresql.conf`

```sh
sudo -u postgres editor /etc/postgresql/14/main/postgresql.conf
```

Set (or add):

```
wal_level = replica
archive_mode = on
archive_command = '/usr/local/bin/pg_archive_wal.sh %p %f'
max_wal_senders = 5
```

See `scripts/backup/examples/postgresql.conf.example` for the full snippet
with comments.

### 6. Restart Postgres

```sh
sudo systemctl restart postgresql
sudo -u postgres psql -c "SHOW archive_mode;"  # Expected: on
sudo -u postgres psql -c "SHOW archive_command;"  # Expected: /usr/local/bin/pg_archive_wal.sh %p %f
sudo -u postgres psql -c "SHOW wal_level;"  # Expected: replica
```

If `archive_mode` reports off after restart, check `systemctl status
postgresql` and the Postgres log at `/var/log/postgresql/postgresql-14-main.log`
for a syntax error in the config.

### 7. Verification

Force-rotate a WAL segment + verify the archiver wrote it to the bucket:

```sh
sudo -u postgres psql -c "SELECT pg_switch_wal();"
# Returns: the LSN of the rotation. Wait ~5 sec.

# Look for the archive line in the Postgres log:
sudo tail -50 /var/log/postgresql/postgresql-14-main.log | grep -i archive
# Expected: "archived write-ahead log file <segment-name>"

# Confirm the segment landed in the bucket:
sudo --preserve-env bash -c 'set -a; . /etc/formula_tela/backup.env; \
  aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls "s3://$S3_BUCKET/wal/" | tail -5'
# Expected: 1+ files with recent timestamp
```

Smoke-test the base backup script without uploading (dry-run mode):

```sh
sudo /usr/local/bin/pg_base_backup.sh --dry-run
# Expected: exits 0, prints what it WOULD do, doesn't write anything
```

Then run the real thing once, out-of-cron, to seed the bucket:

```sh
sudo /usr/local/bin/pg_base_backup.sh
# Expected: completes in 1-10 min depending on DB size, exits 0

sudo --preserve-env bash -c 'set -a; . /etc/formula_tela/backup.env; \
  aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls "s3://$S3_BUCKET/base/"'
# Expected: 1 .tar.gz with today's date
```

Backup infrastructure is now live.

---

## Restore procedure (PITR)

**Stop. Read this whole section before you start.** Restoring a database is
a one-way operation against the data directory you target — picking the
wrong PGDATA path destroys live data.

### 0. Declare incident

Per [`incident-response.md`](incident-response.md). Data loss is Sev1.
Open the war-room, appoint an IC, then come back here.

### 1. Stop Postgres on the target host

```sh
sudo systemctl stop postgresql
sudo systemctl status postgresql  # Expected: inactive (dead)
```

If you're restoring onto a fresh VM, skip this — Postgres isn't running yet.

### 2. Run the guided restore script

```sh
sudo /usr/local/bin/restore_pitr.sh
```

The script will prompt for:

- **Target time** — either `latest` (replay everything we have) or an ISO-8601
  timestamp like `2026-05-17T03:30:00Z`. Resolve in UTC; that's what Postgres
  understands.
- **Bucket prefix** — leave default unless you're restoring from a non-default
  bucket layout.
- **Target data directory** — e.g. `/var/lib/postgresql/14/main`. The script
  will **refuse to proceed** if the directory is non-empty unless you confirm
  with `WIPE` typed exactly — this is a deliberate footgun guard.

The script does, in order:

1. Pulls the latest base backup (`s3://<bucket>/base/<latest>.tar.gz`) to a
   temp dir.
2. Wipes the target PGDATA (after `WIPE` confirmation).
3. Extracts the tarball into PGDATA.
4. Creates `recovery.signal`.
5. Appends to `postgresql.auto.conf`:
   - `restore_command = 'aws --endpoint-url <ep> s3 cp s3://<bucket>/wal/%f %p'`
   - If you specified a target time: `recovery_target_time = '<time>'` plus
     `recovery_target_action = 'promote'`.
6. Sets ownership: `chown -R postgres:postgres <PGDATA>`.

### 3. Start Postgres, watch replay

```sh
sudo systemctl start postgresql
sudo tail -f /var/log/postgresql/postgresql-14-main.log
```

Look for these markers in order:

```
starting point-in-time recovery to <timestamp>
restored log file "<segment>" from archive
...
consistent recovery state reached at <LSN>
redo done at <LSN>
selected new timeline ID: <N>
archive recovery complete
database system is ready to accept connections
```

If you set `recovery_target_time` and Postgres replays past it, the log will
show:

```
recovery stopping before commit of transaction <xid>, time <timestamp>
```

### 4. Verify

```sh
# Most recent committed row in the audit log should be near (but ≤) target time:
sudo -u postgres psql -d ai_bot_platform -c \
  "SELECT MAX(created_at) FROM audit_auditrecord;"

# Spot-check the most damaged table (the one that prompted the restore):
sudo -u postgres psql -d ai_bot_platform -c "SELECT COUNT(*) FROM <table>;"
```

### 5. Promote (only if not auto-promoted)

If you set `recovery_target_action = 'promote'` (the script default), Postgres
already promoted. To check:

```sh
sudo -u postgres psql -c "SELECT pg_is_in_recovery();"
# Expected: f (false — promoted)
```

If it's still `t`, manually promote:

```sh
sudo -u postgres pg_ctlcluster 14 main promote
```

### 6. Re-enable archiving toward a fresh bucket prefix

**Critical**: the restored database is on a **new timeline**. Continuing to
archive WAL into the same `wal/` prefix risks confusing future restores.
Update `S3_BUCKET` in `/etc/formula_tela/backup.env` to a new bucket OR
move the old base/wal files to a `historical/` prefix before resuming
archiving:

```sh
sudo --preserve-env bash -c 'set -a; . /etc/formula_tela/backup.env; \
  aws --endpoint-url "$S3_ENDPOINT_URL" s3 mv "s3://$S3_BUCKET/wal/" \
    "s3://$S3_BUCKET/wal-pre-restore-$(date +%Y%m%d)/" --recursive'
```

Then take a fresh base backup immediately so PITR has a new anchor:

```sh
sudo /usr/local/bin/pg_base_backup.sh
```

### 7. Smoke-test the application

Open the bot, send `/start`, verify state. Per [`incident-response.md`](incident-response.md) §Resolve.

---

## Monitoring

Two complementary alerts, both wired into the existing Telegram alert
channel via existing infrastructure (no new services).

### Alert A: Backup freshness (primary)

**What**: `check_backup_freshness.sh` runs hourly via cron. It lists the
bucket's `base/` prefix and checks the most recent file is < 25 h old (24 h
nominal + 1 h grace for the daily cron + upload time).

**Threshold**: stale = no base backup newer than 25 h.

**Integration**: cron line wraps the script and pings healthcheck.io on
failure. healthcheck.io is the lightweight watchdog (free tier, no service
to run, supports Telegram webhook for the notification).

Setup:

1. Sign up at https://healthchecks.io (free tier, supports Telegram).
2. Create a check named `ai-bot-platform-backup-freshness`. Schedule: hourly,
   grace 30 min.
3. Add the bot's Telegram chat as a notification integration.
4. Copy the ping UUID into the cron line in `/etc/cron.d/pg-backup`.

When `check_backup_freshness.sh` exits non-zero, the cron `||` clause hits
`https://hc-ping.com/<uuid>/fail`. healthcheck.io alerts Telegram within
1 minute. If the cron itself doesn't run at all (e.g. the VM is down),
healthcheck.io fires after the grace period because the success ping never
arrived.

### Alert B: WAL archive lag (secondary)

**What**: Postgres exposes `pg_stat_archiver`. If `archive_command` starts
failing (network, credentials, bucket full), `failed_count` increments and
`last_archived_time` stops advancing. Continuous WAL archiving is what makes
PITR possible between daily base backups — silent archive failure is the
worst failure mode (you find out at restore time).

**Query** (run from your existing Sentry cron / Prometheus exporter, OR add
a Postgres-side cron that pages):

```sql
SELECT
  archived_count,
  failed_count,
  EXTRACT(EPOCH FROM (NOW() - last_archived_time)) AS seconds_since_last_archive,
  last_failed_time,
  last_failed_wal
FROM pg_stat_archiver;
```

**Threshold**: alert if `seconds_since_last_archive > 900` (15 min) OR
`failed_count` increased since the previous poll.

15 min is conservative — a busy DB rotates WAL every couple of minutes;
an idle DB might legitimately go 30 min between archives. Tune after a
month of baseline observation. The goal is "catch credential drift in
the same business day", not "catch every WAL segment".

**Where to wire it**: Phase 1 has Sentry + Telegram, no Prometheus yet.
Simplest path: add a Postgres-side cron that runs the query and emits a
Sentry capture on threshold breach. If/when PI4 ships Prometheus
(if that ticket exists), migrate to a proper `pg_stat_archiver` exporter
metric + Grafana alert.

---

## Quarterly drill checklist

Run every 90 days. On-call schedules the drill, posts in Telegram admin
chat the day before, and runs it on a **scratch VM** — not on prod.

1. Spin up a fresh VM (or staging VM) with the same Postgres major version
   as prod.
2. Install `awscli`, install the four backup scripts to `/usr/local/bin/`,
   drop a copy of `/etc/formula_tela/backup.env` (read-only IAM creds — see
   note below).
3. Run `restore_pitr.sh` against `latest` (replay everything available).
4. Verify the restored DB has:
   - Row counts that look reasonable (compare to prod).
   - Most recent `audit_auditrecord.created_at` is within 1 h of `NOW()`.
   - At least one `Order` row from the last week (B3/B5/B7 smoke).
5. Time the whole drill end-to-end. Target: ≤ 1 h RTO. Record in the
   changelog of this runbook.
6. **Tear down** the scratch VM. Don't leave a stale clone running — it'll
   accumulate stale WAL replays from the same `wal/` prefix.
7. Update `Last exercised` at the top of this file with the drill date.

**Drill credential hygiene**: the drill needs read-only access to the bucket.
Either (a) create a separate IAM user with only `s3:GetObject` + `s3:ListBucket`
and use those creds in the drill VM, or (b) use the prod creds but ensure the
drill VM never gets the `s3:PutObject` / `s3:DeleteObject` use-case (the
restore script only reads). Option (a) is safer.

---

## Retention policy

- **30 daily** base backups (rolling window).
- **12 monthly** base backups (first-of-month snapshot, kept 12 months).
- WAL segments retained as long as the oldest base backup that might need
  them — practically, ~35 days of WAL.

`pg_base_backup.sh` is responsible for pruning on each run:

1. After uploading the new daily, list `base/` in the bucket.
2. Identify the daily that's now > 30 days old. If it's first-of-month, move
   to `base/monthly/`; otherwise delete.
3. Identify monthlies > 12 months old. Delete.
4. For WAL: identify segments older than the oldest retained base backup
   minus a 1-day safety margin. Delete.

The pruning logic is conservative — if the script can't determine which WAL
is safe to delete, it leaves it alone. Storage cost > restore impossibility.

Spec reference: 30/12 mirrors the policy in DRF-852.

---

## Encryption

- **At rest**: bucket-level SSE-S3 (AES-256) is the Phase 1 default. Enable
  in the provider console at bucket creation.
- **In transit**: `awscli` enforces TLS by default when `S3_ENDPOINT_URL`
  uses `https://`. Don't use `http://` for prod.

**Opt-in upgrade**: SSE-KMS with a customer-managed key (CMK). Adds rotation
+ audit trail at the cost of per-request KMS calls (negligible at our
volume). To enable: create a KMS key, grant the backup IAM user
`kms:Encrypt` + `kms:Decrypt` + `kms:GenerateDataKey`, set the bucket
default encryption to SSE-KMS with the key. No script changes needed —
`awscli` honours the bucket default.

Phase 1 ships with SSE-S3 because (a) no key-rotation policy yet, (b)
the threat model is "operator loses the disk", not "cloud provider goes
rogue" — and SSE-S3 covers (a) just as well as SSE-KMS for that threat.
If we add a compliance requirement for CMKs (152-ФЗ specific clause?
investigate), revisit.

---

## Known limits / open questions

- **Single-region storage**. Per spec opt-out. If the bucket's region goes
  dark, the daily backups are unreachable until the region recovers.
  Mitigation if needed: bucket-level cross-region replication (provider
  feature, no script change). Phase 2 ticket if it materializes.
- **No automated restore test**. The quarterly drill is human-driven.
  Automating it requires a parallel staging Postgres that we don't have
  budgeted yet (DRF-???, not opened).
- **WAL pile-up risk**. If `archive_command` fails repeatedly, Postgres
  retains WAL on the data disk until archived — disk can fill. The
  `pg_stat_archiver` alert (Alert B) is the early warning; if it fires,
  fix the archive path **before the disk fills**.
- **Healthcheck.io as critical-path dependency**. We trust a third-party
  watchdog for backup-freshness alerts. Acknowledged risk; the cost of
  rolling our own watchdog is more than the cost of the dependency.

---

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| Sev1 (data loss, restore needed) | Lead | per [`on-call.md`](on-call.md) — Telegram + phone |
| Sev2 (stale backup, WAL lag) | Lead | Telegram admin chat |
| Vendor: bucket provider | provider support | provider console |
| Vendor: healthcheck.io | https://healthchecks.io/support | email only |

---

## Related runbooks

- [`incident-response.md`](incident-response.md) — Sev1 declaration + war-room
  procedure that opens before this runbook's restore section runs.
- [`rollback-procedure.md`](rollback-procedure.md) — image-level rollback,
  the cheaper option when the cause is "bad code" not "bad data". Try
  rollback first; restore is the heavy hammer.
- [`on-call.md`](on-call.md) — who picks up the page that triggers a restore.

---

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md).
Specific things to capture for restore events:

- **What was lost** — table, rough row count, time window.
- **How we found out** — alert? user report? "I noticed"?
- **RPO actual vs target** — minutes of data lost vs the 24 h budget.
- **RTO actual vs target** — minutes from declaration to bot back up vs
  the 1 h budget.
- **Was the quarterly drill exercised on time?** If no, that's an action
  item — every restore SHOULD be preceded by a recent successful drill.

---

## Changelog

- 2026-05-17 — Lead — initial draft (Phase 1 / PI2 / DRF-852). Status:
  **draft** — ships as the deployable artifact for PI2. Flips to
  **complete** after the first successful quarterly drill.
