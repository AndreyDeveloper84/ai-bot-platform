# `scripts/backup/` — Postgres backup + PITR scripts

Operator-facing scripts for daily Postgres base backups, continuous WAL
archiving, freshness monitoring, and point-in-time restore. Intended to run
on the prod VM via cron + Postgres `archive_command`.

**The runbook is the source of truth**: see
[`docs/runbooks/disaster-recovery.md`](../../docs/runbooks/disaster-recovery.md)
for context, deploy procedure, restore procedure, and the quarterly drill.
This README is a one-page orientation; the runbook is the document you
follow when something is on fire.

## What's here

| File | Purpose |
|---|---|
| `pg_base_backup.sh` | Daily full backup. Cron-driven. Uploads `.tar.gz` to bucket, prunes old. `--dry-run` for verification. |
| `pg_archive_wal.sh` | Postgres `archive_command` target. Called per WAL segment. Quiet on success, loud + non-zero on failure (Postgres retries). |
| `check_backup_freshness.sh` | Hourly cron. Alerts via healthcheck.io if newest base backup > 25 h old. |
| `restore_pitr.sh` | Interactive guided restore. Pulls base + WAL, configures `recovery.signal` + `restore_command`, hands off to operator. |
| `examples/backup.env.example` | Template for `/etc/formula_tela/backup.env`. Placeholders only — no real creds. |
| `examples/crontab.example` | Cron lines to drop in `/etc/cron.d/pg-backup`. |
| `examples/postgresql.conf.example` | Required `postgresql.conf` settings (`wal_level`, `archive_mode`, `archive_command`). |
| `tests/test_scripts.sh` | Hand-rolled shell tests. Static-only; no real Postgres or S3. |

## Quick start (operator)

1. Read [`docs/runbooks/disaster-recovery.md`](../../docs/runbooks/disaster-recovery.md). Don't skip this.
2. Create the S3-compatible bucket + IAM creds (runbook §1).
3. Fill in `examples/backup.env.example`, install to `/etc/formula_tela/backup.env` (mode 600).
4. `scp` the four `.sh` files to the prod VM, `install` to `/usr/local/bin/`.
5. Add `examples/crontab.example` to `/etc/cron.d/pg-backup`.
6. Merge `examples/postgresql.conf.example` into `/etc/postgresql/14/main/postgresql.conf`.
7. Restart Postgres, run the verification step (runbook §Deploy §7).
8. Sign up at healthcheck.io, wire the UUID into the cron line.

You're done when:

- The bucket's `base/` prefix has a fresh `.tar.gz`.
- The bucket's `wal/` prefix has recent WAL segments.
- `pg_stat_archiver.failed_count` is 0.
- healthcheck.io shows a green tick for the freshness check.

## Running the tests

```sh
bash scripts/backup/tests/test_scripts.sh
```

The test runner uses pure shell — no external dependencies (no bats). It
mocks `awscli` and the bucket listing so it can run anywhere bash + the
GNU coreutils run. It does **not** spin up Postgres.

## CI integration

The current `.github/workflows/ci.yml` is Python-only (`pytest`, `ruff`,
`mypy`). The bash tests here aren't wired into CI yet — TODO for a future
follow-up if shell drift becomes a real problem. For now: run
`bash scripts/backup/tests/test_scripts.sh` before sending changes here for
review.

## Design notes

- **Storage-provider-agnostic**: all bucket access goes through
  `aws --endpoint-url $S3_ENDPOINT_URL`. Works with AWS S3, Yandex Object
  Storage, MinIO, anything S3-API-compatible.
- **No real credentials in this repo**: `examples/backup.env.example` is
  placeholders only. Real creds live in `/etc/formula_tela/backup.env` on
  prod, mode 600, root-owned.
- **`pg_basebackup` not `pg_dump`**: PITR requires a physical backup +
  WAL stream. `pg_dump` is logical and can't be used for point-in-time
  replay. (`pg_dump` is fine for one-off SQL exports — run it from a
  restored copy.)
- **Postgres-level cron, not Celery**: the backup orchestration lives in
  `/etc/cron.d/pg-backup`, not in the Django/Celery stack. Celery depends
  on Postgres being up; the thing that backs up Postgres must not.

## What this PR does NOT include

- Terraform / IaC for the bucket.
- Cross-region replication.
- Automated restore tests.
- Prometheus metrics for `pg_stat_archiver`.
- Celery beat for backup scheduling (deliberately omitted — see above).
- A `pg_dump` fallback (the `formula_tela` repo may already have one; this
  is complementary, not a replacement).

See the runbook's "Known limits / open questions" section for the full list.
