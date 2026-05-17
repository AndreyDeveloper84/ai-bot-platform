#!/usr/bin/env bash
#
# pg_base_backup.sh — daily full Postgres base backup to S3-compatible bucket.
#
# Runs as root via cron (see scripts/backup/examples/crontab.example).
# Reads config from /etc/formula_tela/backup.env.
#
# See docs/runbooks/disaster-recovery.md for full context.

set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_NAME="$(basename "$0")"
readonly CONFIG_FILE="${BACKUP_ENV_FILE:-/etc/formula_tela/backup.env}"

DRY_RUN=0

log() {
    printf '%s [%s] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$SCRIPT_NAME" "$*"
}

err() {
    log "ERROR: $*" >&2
}

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [--dry-run] [--help]

Take a Postgres base backup with pg_basebackup, compress, and upload to
the S3-compatible bucket configured in $CONFIG_FILE. Prune old backups
per RETENTION_DAILY_DAYS / RETENTION_MONTHLY_MONTHS.

Options:
  --dry-run   Print what would be done; do not run pg_basebackup or upload.
  --help      Show this help and exit.

Environment (in $CONFIG_FILE):
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
  S3_ENDPOINT_URL          S3-compatible endpoint URL
  S3_BUCKET                target bucket name
  PG_DATA_DIR              path for pg_basebackup -D (typically a tmp dir)
  PG_USER                  Postgres superuser (default: postgres)
  RETENTION_DAILY_DAYS     keep daily backups newer than N days (default 30)
  RETENTION_MONTHLY_MONTHS keep monthly backups for N months (default 12)
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run) DRY_RUN=1; shift ;;
            --help|-h) usage; exit 0 ;;
            *) err "unknown argument: $1"; usage >&2; exit 2 ;;
        esac
    done
}

load_config() {
    if [[ ! -r "$CONFIG_FILE" ]]; then
        err "config file not readable: $CONFIG_FILE"
        exit 1
    fi
    # shellcheck disable=SC1090
    set -a; . "$CONFIG_FILE"; set +a

    : "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL not set in $CONFIG_FILE}"
    : "${S3_BUCKET:?S3_BUCKET not set in $CONFIG_FILE}"
    : "${PG_USER:=postgres}"
    : "${RETENTION_DAILY_DAYS:=30}"
    : "${RETENTION_MONTHLY_MONTHS:=12}"
}

check_prereqs() {
    if ! command -v aws >/dev/null 2>&1; then
        err "awscli not installed"
        exit 1
    fi
    if (( DRY_RUN == 0 )) && ! command -v pg_basebackup >/dev/null 2>&1; then
        err "pg_basebackup not installed"
        exit 1
    fi
}

run_backup() {
    local timestamp tmp_dir tar_path s3_key
    timestamp="$(date -u +"%Y-%m-%dT%H-%M-%SZ")"
    tmp_dir="$(mktemp -d -t pg_basebackup.XXXXXX)"
    tar_path="$tmp_dir/base-${timestamp}.tar.gz"
    s3_key="base/base-${timestamp}.tar.gz"

    log "starting base backup: timestamp=$timestamp tmp_dir=$tmp_dir"

    if (( DRY_RUN )); then
        log "DRY RUN: would run pg_basebackup -U $PG_USER -F tar -z -X stream -D $tmp_dir"
        log "DRY RUN: would upload to s3://$S3_BUCKET/$s3_key via $S3_ENDPOINT_URL"
        log "DRY RUN: would prune daily > ${RETENTION_DAILY_DAYS}d and monthly > ${RETENTION_MONTHLY_MONTHS}mo"
        rmdir "$tmp_dir"
        return 0
    fi

    # pg_basebackup writes base.tar.gz + pg_wal.tar.gz into -D when -F tar.
    # We re-tar the whole dir to a single artifact for simpler restore.
    log "running pg_basebackup"
    sudo -u "$PG_USER" pg_basebackup \
        -D "$tmp_dir/raw" \
        -F tar \
        -z \
        -X stream \
        --checkpoint=fast \
        --progress \
        --verbose 2>&1 | sed "s/^/$(date -u +%H:%M:%SZ) pg_basebackup: /"

    log "bundling tarball"
    tar -czf "$tar_path" -C "$tmp_dir/raw" .

    local tar_size_bytes
    tar_size_bytes="$(stat -c '%s' "$tar_path")"
    log "tarball ready: $tar_path size=${tar_size_bytes}B"

    log "uploading to s3://$S3_BUCKET/$s3_key"
    aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp \
        "$tar_path" "s3://$S3_BUCKET/$s3_key" \
        --only-show-errors

    log "upload complete; cleaning local tmp"
    rm -rf "$tmp_dir"

    prune_old_backups
}

prune_old_backups() {
    log "pruning: retention=${RETENTION_DAILY_DAYS}d daily, ${RETENTION_MONTHLY_MONTHS}mo monthly"

    local cutoff_daily cutoff_monthly listing
    cutoff_daily="$(date -u -d "${RETENTION_DAILY_DAYS} days ago" +%Y-%m-%d 2>/dev/null \
        || date -u -v-"${RETENTION_DAILY_DAYS}"d +%Y-%m-%d)"
    cutoff_monthly="$(date -u -d "${RETENTION_MONTHLY_MONTHS} months ago" +%Y-%m-%d 2>/dev/null \
        || date -u -v-"${RETENTION_MONTHLY_MONTHS}"m +%Y-%m-%d)"

    log "cutoff_daily=$cutoff_daily cutoff_monthly=$cutoff_monthly"

    # List existing daily backups; promote first-of-month past the daily cutoff
    # to monthly/, delete the rest past cutoff.
    listing="$(aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls \
        "s3://$S3_BUCKET/base/" 2>/dev/null | awk '{print $4}' | grep -E '^base-' || true)"

    local key date_part
    while IFS= read -r key; do
        [[ -z "$key" ]] && continue
        # base-YYYY-MM-DDTHH-MM-SSZ.tar.gz → date_part = YYYY-MM-DD
        date_part="$(echo "$key" | sed -E 's/^base-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/')"
        if [[ "$date_part" < "$cutoff_daily" ]]; then
            local day_of_month
            day_of_month="$(echo "$date_part" | cut -d- -f3)"
            if [[ "$day_of_month" == "01" ]]; then
                log "promoting to monthly: $key"
                aws --endpoint-url "$S3_ENDPOINT_URL" s3 mv \
                    "s3://$S3_BUCKET/base/$key" \
                    "s3://$S3_BUCKET/base/monthly/$key" \
                    --only-show-errors
            else
                log "deleting expired daily: $key"
                aws --endpoint-url "$S3_ENDPOINT_URL" s3 rm \
                    "s3://$S3_BUCKET/base/$key" \
                    --only-show-errors
            fi
        fi
    done <<< "$listing"

    # Prune monthly/ past cutoff_monthly.
    listing="$(aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls \
        "s3://$S3_BUCKET/base/monthly/" 2>/dev/null | awk '{print $4}' | grep -E '^base-' || true)"

    while IFS= read -r key; do
        [[ -z "$key" ]] && continue
        date_part="$(echo "$key" | sed -E 's/^base-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/')"
        if [[ "$date_part" < "$cutoff_monthly" ]]; then
            log "deleting expired monthly: $key"
            aws --endpoint-url "$S3_ENDPOINT_URL" s3 rm \
                "s3://$S3_BUCKET/base/monthly/$key" \
                --only-show-errors
        fi
    done <<< "$listing"

    # WAL pruning: delete WAL segments older than (cutoff_daily - 1 day safety margin).
    # We do this conservatively — only segments older than the oldest base backup
    # are eligible.
    log "WAL pruning: bounded by oldest base backup (conservative)"
    local wal_listing oldest_base
    oldest_base="$(aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls \
        "s3://$S3_BUCKET/base/" 2>/dev/null | awk '{print $4}' | grep -E '^base-' | sort | head -1 \
        | sed -E 's/^base-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/' || true)"

    if [[ -z "$oldest_base" ]]; then
        log "no base backups found, skipping WAL prune"
        return 0
    fi

    log "oldest retained base backup date: $oldest_base — WAL older than this minus 1d is eligible"
    # WAL segment timestamps are not in the filename; rely on LastModified.
    # awscli doesn't expose --older-than, so iterate.
    wal_listing="$(aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls \
        "s3://$S3_BUCKET/wal/" 2>/dev/null || true)"
    local wal_cutoff
    wal_cutoff="$(date -u -d "$oldest_base - 1 day" +%Y-%m-%d 2>/dev/null \
        || date -u -j -f "%Y-%m-%d" -v-1d "$oldest_base" +%Y-%m-%d 2>/dev/null \
        || echo "")"

    if [[ -z "$wal_cutoff" ]]; then
        log "could not compute WAL cutoff (date arithmetic unsupported); skipping WAL prune"
        return 0
    fi

    local wal_date wal_key
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        wal_date="$(echo "$line" | awk '{print $1}')"
        wal_key="$(echo "$line" | awk '{print $4}')"
        [[ -z "$wal_key" || -z "$wal_date" ]] && continue
        if [[ "$wal_date" < "$wal_cutoff" ]]; then
            log "deleting expired WAL: $wal_key (date=$wal_date)"
            aws --endpoint-url "$S3_ENDPOINT_URL" s3 rm \
                "s3://$S3_BUCKET/wal/$wal_key" \
                --only-show-errors
        fi
    done <<< "$wal_listing"
}

main() {
    parse_args "$@"
    if (( DRY_RUN )); then
        log "starting (DRY RUN) — no changes will be made"
    else
        log "starting"
    fi
    load_config
    check_prereqs
    run_backup
    log "done"
}

main "$@"
