#!/usr/bin/env bash
#
# restore_pitr.sh — guided point-in-time restore from S3-compatible bucket.
#
# Interactive. Walks the operator through pulling a base backup + configuring
# WAL replay. NOT meant to run unattended.
#
# See docs/runbooks/disaster-recovery.md § Restore procedure (PITR).

set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_NAME="$(basename "$0")"
readonly CONFIG_FILE="${BACKUP_ENV_FILE:-/etc/formula_tela/backup.env}"

log() {
    printf '%s [%s] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$SCRIPT_NAME" "$*"
}

err() {
    log "ERROR: $*" >&2
}

prompt() {
    local var_name="$1"
    local message="$2"
    local default="${3:-}"
    local reply
    if [[ -n "$default" ]]; then
        read -r -p "$message [$default]: " reply
        reply="${reply:-$default}"
    else
        read -r -p "$message: " reply
    fi
    printf -v "$var_name" '%s' "$reply"
}

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [--help]

Guided interactive Postgres point-in-time recovery from the S3-compatible
bucket configured in $CONFIG_FILE.

Will prompt for:
  - Target time: 'latest' or ISO-8601 UTC (e.g. 2026-05-17T03:30:00Z)
  - Bucket prefix (default: base/)
  - Target data directory (e.g. /var/lib/postgresql/14/main)

REFUSES to wipe a non-empty data directory unless you type 'WIPE' exactly.
Make sure Postgres is stopped on the target host before running this.

Options:
  --help    Show this help and exit.
EOF
}

main() {
    if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
        usage
        exit 0
    fi

    if [[ $# -gt 0 ]]; then
        err "unknown argument: $1"
        usage >&2
        exit 2
    fi

    if [[ ! -r "$CONFIG_FILE" ]]; then
        err "config file not readable: $CONFIG_FILE"
        exit 1
    fi
    # shellcheck disable=SC1090
    set -a; . "$CONFIG_FILE"; set +a

    : "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL not set in $CONFIG_FILE}"
    : "${S3_BUCKET:?S3_BUCKET not set in $CONFIG_FILE}"
    : "${PG_DATA_DIR:=/var/lib/postgresql/14/main}"

    if ! command -v aws >/dev/null 2>&1; then
        err "awscli not installed"
        exit 1
    fi

    log "PITR restore — interactive mode"
    log "bucket: s3://$S3_BUCKET via $S3_ENDPOINT_URL"

    local target_time bucket_prefix target_pgdata
    prompt target_time \
        "Target time (UTC ISO-8601, or 'latest' to replay everything)" \
        "latest"
    prompt bucket_prefix "Bucket prefix for base backups" "base/"
    prompt target_pgdata "Target data directory" "$PG_DATA_DIR"

    log "configuration:"
    log "  target_time   = $target_time"
    log "  bucket_prefix = $bucket_prefix"
    log "  target_pgdata = $target_pgdata"

    # Verify Postgres is stopped.
    if pgrep -x postgres >/dev/null 2>&1; then
        err "postgres processes are still running on this host. Stop Postgres first:"
        err "  sudo systemctl stop postgresql"
        exit 1
    fi

    # Find the latest base backup.
    local latest_base
    latest_base="$(aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls \
        "s3://$S3_BUCKET/${bucket_prefix}" 2>/dev/null \
        | awk '{print $4}' | grep -E '^base-' | sort | tail -1)"

    if [[ -z "$latest_base" ]]; then
        err "no base backups found at s3://$S3_BUCKET/${bucket_prefix}"
        exit 1
    fi

    log "latest base backup: $latest_base"

    # Wipe confirmation.
    if [[ -e "$target_pgdata" ]] && [[ -n "$(ls -A "$target_pgdata" 2>/dev/null || true)" ]]; then
        log "target data directory is non-empty: $target_pgdata"
        local confirm
        prompt confirm \
            "Type WIPE to confirm deletion of $target_pgdata contents (anything else aborts)" \
            ""
        if [[ "$confirm" != "WIPE" ]]; then
            err "wipe not confirmed, aborting"
            exit 1
        fi
        log "wiping $target_pgdata"
        rm -rf "${target_pgdata:?}"/*
    fi

    mkdir -p "$target_pgdata"

    # Pull + extract.
    local tmp_dir
    tmp_dir="$(mktemp -d -t pitr_restore.XXXXXX)"
    log "downloading $latest_base → $tmp_dir"
    aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp \
        "s3://$S3_BUCKET/${bucket_prefix}${latest_base}" \
        "$tmp_dir/$latest_base"

    log "extracting tarball into $target_pgdata"
    tar -xzf "$tmp_dir/$latest_base" -C "$target_pgdata"
    rm -rf "$tmp_dir"

    # The pg_basebackup -F tar output contains base.tar.gz + pg_wal.tar.gz when
    # extracted from the wrapper. Operator may need to extract those individually
    # depending on layout; document but don't force.
    if [[ -f "$target_pgdata/base.tar.gz" ]]; then
        log "found nested base.tar.gz — extracting"
        tar -xzf "$target_pgdata/base.tar.gz" -C "$target_pgdata"
        rm -f "$target_pgdata/base.tar.gz"
    fi
    if [[ -f "$target_pgdata/pg_wal.tar.gz" ]]; then
        log "found nested pg_wal.tar.gz — extracting into pg_wal/"
        mkdir -p "$target_pgdata/pg_wal"
        tar -xzf "$target_pgdata/pg_wal.tar.gz" -C "$target_pgdata/pg_wal"
        rm -f "$target_pgdata/pg_wal.tar.gz"
    fi

    # recovery.signal
    log "creating recovery.signal"
    touch "$target_pgdata/recovery.signal"

    # Append restore config.
    log "appending restore config to postgresql.auto.conf"
    local restore_cmd
    restore_cmd="aws --endpoint-url '$S3_ENDPOINT_URL' s3 cp 's3://$S3_BUCKET/wal/%f' '%p'"

    {
        echo ""
        echo "# --- PITR restore appended by $SCRIPT_NAME at $(date -u +%FT%TZ) ---"
        echo "restore_command = '${restore_cmd}'"
        if [[ "$target_time" != "latest" ]]; then
            echo "recovery_target_time = '${target_time}'"
            echo "recovery_target_action = 'promote'"
        fi
        echo "# --- end PITR restore ---"
    } >> "$target_pgdata/postgresql.auto.conf"

    # Ownership.
    : "${PG_USER:=postgres}"
    log "setting ownership to ${PG_USER}:${PG_USER}"
    chown -R "${PG_USER}:${PG_USER}" "$target_pgdata"
    chmod 700 "$target_pgdata"

    cat <<EOF

================================================================
  PITR setup complete. Next steps (manual):

  1. Verify the layout looks right:
       ls $target_pgdata
       grep -A2 'PITR restore' $target_pgdata/postgresql.auto.conf

  2. Start Postgres:
       sudo systemctl start postgresql

  3. Watch the log:
       sudo tail -f /var/log/postgresql/postgresql-*-main.log
       # Expect: 'starting point-in-time recovery'
       # Expect: 'archive recovery complete'
       # Expect: 'database system is ready to accept connections'

  4. Verify promotion:
       sudo -u postgres psql -c "SELECT pg_is_in_recovery();"
       # Expect: f

  5. Smoke-test data + rotate the bucket WAL prefix per runbook §6.
================================================================

EOF
    log "done"
}

main "$@"
