#!/usr/bin/env bash
#
# pg_archive_wal.sh — Postgres archive_command target.
#
# Called by Postgres for every WAL segment. Args:
#   $1 = %p (path to the WAL file to archive)
#   $2 = %f (the WAL filename only)
#
# Contract with Postgres:
#   - Exit 0 on success. Postgres marks the segment archived.
#   - Exit non-zero on failure. Postgres retries the same segment in a loop.
#
# Keep output minimal — Postgres logs every invocation. Errors only on stderr.
# See docs/runbooks/disaster-recovery.md.

set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_NAME="$(basename "$0")"
readonly CONFIG_FILE="${BACKUP_ENV_FILE:-/etc/formula_tela/backup.env}"

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME <wal-path> <wal-filename>
       $SCRIPT_NAME --help

Postgres archive_command target. Uploads the given WAL segment to the
S3-compatible bucket configured in $CONFIG_FILE.

Configure in postgresql.conf:
  archive_mode = on
  archive_command = '/usr/local/bin/pg_archive_wal.sh %p %f'
EOF
}

err() {
    printf '%s [%s] ERROR: %s\n' \
        "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$SCRIPT_NAME" "$*" >&2
}

main() {
    if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
        usage
        exit 0
    fi

    if [[ $# -lt 2 ]]; then
        err "expected 2 args (wal-path, wal-filename), got $#"
        usage >&2
        exit 2
    fi

    local wal_path="$1"
    local wal_name="$2"

    if [[ ! -r "$wal_path" ]]; then
        err "WAL file not readable: $wal_path"
        exit 1
    fi

    if [[ ! -r "$CONFIG_FILE" ]]; then
        err "config file not readable: $CONFIG_FILE"
        exit 1
    fi

    # shellcheck disable=SC1090
    set -a; . "$CONFIG_FILE"; set +a

    : "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL not set}"
    : "${S3_BUCKET:?S3_BUCKET not set}"

    if ! command -v aws >/dev/null 2>&1; then
        err "awscli not installed"
        exit 1
    fi

    # --only-show-errors keeps Postgres logs clean. Failures surface on stderr,
    # awscli exit code is preserved. Postgres retries on non-zero.
    if ! aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp \
        "$wal_path" "s3://$S3_BUCKET/wal/$wal_name" \
        --only-show-errors; then
        err "upload failed: $wal_name"
        exit 1
    fi

    exit 0
}

main "$@"
