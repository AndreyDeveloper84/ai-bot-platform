#!/usr/bin/env bash
#
# check_backup_freshness.sh — verify the most recent base backup is < 25 h old.
#
# Designed to be wrapped by a cron + healthcheck.io ping. Exit 0 = fresh,
# non-zero = stale (caller should fail the healthcheck).
#
# Testability: --mock-now sets the reference "now" timestamp, and the
# AWS_LIST_FN environment variable can override the bucket-listing command
# for unit tests (see scripts/backup/tests/test_scripts.sh).
#
# See docs/runbooks/disaster-recovery.md.

set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_NAME="$(basename "$0")"
readonly CONFIG_FILE="${BACKUP_ENV_FILE:-/etc/formula_tela/backup.env}"
readonly MAX_AGE_SECONDS="${MAX_AGE_SECONDS:-90000}"  # 25 h default

MOCK_NOW=""

log() {
    printf '%s [%s] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$SCRIPT_NAME" "$*"
}

err() {
    log "ERROR: $*" >&2
}

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [--mock-now <ISO8601>] [--help]

Check that the most recent Postgres base backup in the S3-compatible bucket
is younger than \$MAX_AGE_SECONDS seconds (default: 90000 = 25 h).

Exits 0 if fresh, 1 if stale or empty bucket, 2 on config error.

Options:
  --mock-now <ISO8601>   Use this timestamp as "now" instead of system clock.
                         Useful for testing. Example: 2026-05-17T03:00:00Z
  --help                 Show this help and exit.

Environment:
  MAX_AGE_SECONDS        Override the freshness threshold.
  AWS_LIST_FN            Function name to replace 'aws s3 ls' for tests.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mock-now)
                MOCK_NOW="${2:-}"
                if [[ -z "$MOCK_NOW" ]]; then
                    err "--mock-now requires a value"
                    exit 2
                fi
                shift 2
                ;;
            --help|-h) usage; exit 0 ;;
            *) err "unknown argument: $1"; usage >&2; exit 2 ;;
        esac
    done
}

load_config() {
    if [[ ! -r "$CONFIG_FILE" ]]; then
        err "config file not readable: $CONFIG_FILE"
        exit 2
    fi
    # shellcheck disable=SC1090
    set -a; . "$CONFIG_FILE"; set +a

    : "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL not set in $CONFIG_FILE}"
    : "${S3_BUCKET:?S3_BUCKET not set in $CONFIG_FILE}"
}

# list_base_backups: outputs one "<ISO8601-date> <ISO8601-time> <size> <key>"
# line per base backup. Pluggable via AWS_LIST_FN for tests.
list_base_backups() {
    if [[ -n "${AWS_LIST_FN:-}" ]]; then
        "$AWS_LIST_FN"
    else
        aws --endpoint-url "$S3_ENDPOINT_URL" s3 ls \
            "s3://$S3_BUCKET/base/" 2>/dev/null \
            | grep -E 'base-' || true
    fi
}

epoch_of() {
    # POSIX-ish portable: try GNU date, fall back to BSD date.
    local input="$1"
    date -u -d "$input" +%s 2>/dev/null \
        || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$input" +%s 2>/dev/null \
        || date -u -j -f "%Y-%m-%d %H:%M:%S" "$input" +%s 2>/dev/null \
        || return 1
}

main() {
    parse_args "$@"

    # In test mode (AWS_LIST_FN set), skip config load — tests provide what they need.
    if [[ -z "${AWS_LIST_FN:-}" ]]; then
        load_config
    fi

    local now_epoch
    if [[ -n "$MOCK_NOW" ]]; then
        now_epoch="$(epoch_of "$MOCK_NOW")" || {
            err "could not parse --mock-now: $MOCK_NOW"
            exit 2
        }
    else
        now_epoch="$(date -u +%s)"
    fi

    local listing latest_line latest_date latest_time latest_epoch age_seconds
    listing="$(list_base_backups)"

    if [[ -z "$listing" ]]; then
        err "no base backups found in bucket — possibly empty or unreachable"
        exit 1
    fi

    # awscli ls output: "YYYY-MM-DD HH:MM:SS <size> <key>"
    # Sort lexicographically by date+time (column 1 + 2) and take the last.
    latest_line="$(echo "$listing" | sort -k1,2 | tail -1)"
    latest_date="$(echo "$latest_line" | awk '{print $1}')"
    latest_time="$(echo "$latest_line" | awk '{print $2}')"

    if [[ -z "$latest_date" || -z "$latest_time" ]]; then
        err "could not parse listing: $latest_line"
        exit 1
    fi

    latest_epoch="$(epoch_of "${latest_date} ${latest_time}")" || {
        err "could not parse timestamp: ${latest_date} ${latest_time}"
        exit 1
    }

    age_seconds=$(( now_epoch - latest_epoch ))

    if (( age_seconds < 0 )); then
        err "latest backup is in the future (clock skew?): age=${age_seconds}s"
        exit 1
    fi

    if (( age_seconds > MAX_AGE_SECONDS )); then
        err "STALE: latest backup ${latest_date} ${latest_time} is ${age_seconds}s old (> ${MAX_AGE_SECONDS}s)"
        exit 1
    fi

    log "fresh: latest backup ${latest_date} ${latest_time} is ${age_seconds}s old (< ${MAX_AGE_SECONDS}s)"
    exit 0
}

main "$@"
