#!/usr/bin/env bash
#
# test_scripts.sh — hand-rolled shell tests for the backup scripts.
#
# Pure static testing — does NOT touch a real Postgres or a real S3 bucket.
# Mocks awscli + pg_basebackup via PATH-prepended shims.
#
# Run from anywhere:
#   bash scripts/backup/tests/test_scripts.sh

set -uo pipefail
IFS=$'\n\t'

# Resolve paths relative to this script's location.
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$(cd "$TEST_DIR/.." && pwd)"

PASS=0
FAIL=0
FAILED_TESTS=()

# --- test harness ---

run_test() {
    local name="$1"; shift
    local stdout_file stderr_file rc
    stdout_file="$(mktemp)"
    stderr_file="$(mktemp)"

    if "$@" >"$stdout_file" 2>"$stderr_file"; then
        rc=0
    else
        rc=$?
    fi
    printf 'TEST_RC=%d\n' "$rc"
    printf 'TEST_STDOUT_FILE=%s\n' "$stdout_file"
    printf 'TEST_STDERR_FILE=%s\n' "$stderr_file"
}

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        printf '  ok   %s\n' "$label"
        return 0
    else
        printf '  FAIL %s: expected=%q actual=%q\n' "$label" "$expected" "$actual"
        return 1
    fi
}

assert_contains() {
    local label="$1" needle="$2" haystack_file="$3"
    if grep -q -- "$needle" "$haystack_file"; then
        printf '  ok   %s\n' "$label"
        return 0
    else
        printf '  FAIL %s: needle %q not found in %s\n' "$label" "$needle" "$haystack_file"
        printf '       contents:\n'
        sed 's/^/         /' "$haystack_file"
        return 1
    fi
}

record_result() {
    local name="$1" status="$2"
    if [[ "$status" -eq 0 ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILED_TESTS+=("$name")
    fi
}

start_test() {
    printf '\n=== %s\n' "$1"
}

# --- test fixture: minimal backup.env for tests ---

make_fixture_env() {
    local f
    f="$(mktemp)"
    cat >"$f" <<'EOF'
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=test-region
S3_ENDPOINT_URL=http://mock.test
S3_BUCKET=test-bucket
PG_DATA_DIR=/tmp/test-pgdata
PG_USER=postgres
RETENTION_DAILY_DAYS=30
RETENTION_MONTHLY_MONTHS=12
EOF
    printf '%s' "$f"
}

# --- tests ---

test_help_flags() {
    start_test "all four scripts: --help exits 0"
    local script status=0
    for script in pg_base_backup.sh pg_archive_wal.sh check_backup_freshness.sh restore_pitr.sh; do
        local out err rc
        out="$(mktemp)"; err="$(mktemp)"
        if bash "$SCRIPTS_DIR/$script" --help >"$out" 2>"$err"; then
            rc=0
        else
            rc=$?
        fi
        assert_eq "$script --help rc" "0" "$rc" || status=1
        assert_contains "$script --help mentions Usage" "Usage" "$out" || status=1
        rm -f "$out" "$err"
    done
    record_result "test_help_flags" "$status"
}

test_pg_base_backup_dry_run() {
    start_test "pg_base_backup.sh --dry-run exits 0 without doing anything"
    local env_file out err rc status=0
    env_file="$(make_fixture_env)"
    out="$(mktemp)"; err="$(mktemp)"

    # Shim PATH so 'aws' is a noop that the dry-run won't actually call.
    # The dry-run path returns BEFORE invoking any command, so this is belt-and-braces.
    local shim_dir
    shim_dir="$(mktemp -d)"
    cat >"$shim_dir/aws" <<'EOF'
#!/usr/bin/env bash
echo "SHIM aws called with: $*" >&2
exit 0
EOF
    chmod +x "$shim_dir/aws"

    if BACKUP_ENV_FILE="$env_file" PATH="$shim_dir:$PATH" \
        bash "$SCRIPTS_DIR/pg_base_backup.sh" --dry-run >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi

    assert_eq "dry-run rc" "0" "$rc" || status=1
    assert_contains "dry-run announces DRY RUN" "DRY RUN" "$out" || status=1
    # Should NOT have called aws (shim writes to stderr if invoked).
    if grep -q "SHIM aws called" "$err"; then
        printf '  FAIL dry-run unexpectedly invoked aws\n'
        printf '       stderr:\n'
        sed 's/^/         /' "$err"
        status=1
    else
        printf '  ok   dry-run did not invoke aws\n'
    fi

    rm -rf "$env_file" "$out" "$err" "$shim_dir"
    record_result "test_pg_base_backup_dry_run" "$status"
}

test_pg_archive_wal_rejects_few_args() {
    start_test "pg_archive_wal.sh rejects < 2 args with non-zero exit"
    local out err rc status=0
    out="$(mktemp)"; err="$(mktemp)"

    # No args.
    if bash "$SCRIPTS_DIR/pg_archive_wal.sh" >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi
    if (( rc == 0 )); then
        printf '  FAIL pg_archive_wal.sh with 0 args returned 0\n'
        status=1
    else
        printf '  ok   pg_archive_wal.sh with 0 args returned %d\n' "$rc"
    fi
    assert_contains "0-arg error mentions expected" "expected 2 args" "$err" || status=1

    # One arg.
    : >"$out"; : >"$err"
    if bash "$SCRIPTS_DIR/pg_archive_wal.sh" /tmp/fake-wal >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi
    if (( rc == 0 )); then
        printf '  FAIL pg_archive_wal.sh with 1 arg returned 0\n'
        status=1
    else
        printf '  ok   pg_archive_wal.sh with 1 arg returned %d\n' "$rc"
    fi

    rm -f "$out" "$err"
    record_result "test_pg_archive_wal_rejects_few_args" "$status"
}

test_check_backup_freshness_fresh() {
    start_test "check_backup_freshness.sh: fresh backup returns 0"
    local out err rc status=0
    out="$(mktemp)"; err="$(mktemp)"

    # Mock awscli listing function. Return one base backup dated 1 hour before mock-now.
    mock_list_fresh() {
        echo "2026-05-17 02:00:00     1234567 base-2026-05-17T02-00-00Z.tar.gz"
    }
    export -f mock_list_fresh

    if AWS_LIST_FN=mock_list_fresh \
        bash "$SCRIPTS_DIR/check_backup_freshness.sh" \
            --mock-now "2026-05-17T03:00:00Z" >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi

    assert_eq "fresh rc" "0" "$rc" || status=1
    assert_contains "fresh log" "fresh:" "$out" || status=1

    rm -f "$out" "$err"
    record_result "test_check_backup_freshness_fresh" "$status"
}

test_check_backup_freshness_stale() {
    start_test "check_backup_freshness.sh: stale backup returns non-zero"
    local out err rc status=0
    out="$(mktemp)"; err="$(mktemp)"

    # Mock listing: backup is 48 h old → > 25 h threshold.
    mock_list_stale() {
        echo "2026-05-15 02:00:00     1234567 base-2026-05-15T02-00-00Z.tar.gz"
    }
    export -f mock_list_stale

    if AWS_LIST_FN=mock_list_stale \
        bash "$SCRIPTS_DIR/check_backup_freshness.sh" \
            --mock-now "2026-05-17T03:00:00Z" >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi

    if (( rc == 0 )); then
        printf '  FAIL stale backup returned 0\n'
        status=1
    else
        printf '  ok   stale backup returned %d\n' "$rc"
    fi
    assert_contains "stale error" "STALE" "$err" || status=1

    rm -f "$out" "$err"
    record_result "test_check_backup_freshness_stale" "$status"
}

test_check_backup_freshness_uses_filename_utc_not_ls_localtime() {
    # Regression: `aws s3 ls` prints LastModified in the host's local
    # timezone. Earlier versions of this script parsed columns 1+2 as if
    # they were UTC, producing a -3h skew on MSK hosts and rejecting
    # legitimate-fresh backups as "in the future". The fix parses the
    # unambiguous UTC timestamp embedded in the key (column 4).
    start_test "check_backup_freshness.sh: parses key UTC, not ls local-time"
    local out err rc status=0
    out="$(mktemp)"; err="$(mktemp)"

    # Simulate an MSK host: ls timestamp is 3h ahead of the filename's
    # UTC stamp. With the buggy parser, age would be -3600s and the
    # script would log "in the future". With the fix, it derives age
    # from the filename and reports fresh.
    mock_list_local_tz() {
        echo "2026-05-17 05:00:00     1234567 base-2026-05-17T02-00-00Z.tar.gz"
    }
    export -f mock_list_local_tz

    if AWS_LIST_FN=mock_list_local_tz \
        bash "$SCRIPTS_DIR/check_backup_freshness.sh" \
            --mock-now "2026-05-17T03:00:00Z" >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi

    assert_eq "tz-skew rc" "0" "$rc" || status=1
    assert_contains "tz-skew log" "fresh:" "$out" || status=1
    # The "in the future" diagnostic must NOT appear — that's the bug
    # this test is guarding against.
    if grep -q "in the future" "$err"; then
        printf '  FAIL "in the future" emitted — fix regressed\n'
        status=1
    else
        printf '  ok   no "in the future" diagnostic\n'
    fi

    rm -f "$out" "$err"
    record_result "test_check_backup_freshness_uses_filename_utc_not_ls_localtime" "$status"
}

test_check_backup_freshness_empty_bucket() {
    start_test "check_backup_freshness.sh: empty bucket returns non-zero"
    local out err rc status=0
    out="$(mktemp)"; err="$(mktemp)"

    mock_list_empty() {
        :  # output nothing
    }
    export -f mock_list_empty

    if AWS_LIST_FN=mock_list_empty \
        bash "$SCRIPTS_DIR/check_backup_freshness.sh" \
            --mock-now "2026-05-17T03:00:00Z" >"$out" 2>"$err"; then
        rc=0
    else
        rc=$?
    fi

    if (( rc == 0 )); then
        printf '  FAIL empty bucket returned 0\n'
        status=1
    else
        printf '  ok   empty bucket returned %d\n' "$rc"
    fi
    assert_contains "empty error" "no base backups" "$err" || status=1

    rm -f "$out" "$err"
    record_result "test_check_backup_freshness_empty_bucket" "$status"
}

# --- run all tests ---

main() {
    printf 'running backup script tests from %s\n' "$SCRIPTS_DIR"

    test_help_flags
    test_pg_base_backup_dry_run
    test_pg_archive_wal_rejects_few_args
    test_check_backup_freshness_fresh
    test_check_backup_freshness_stale
    test_check_backup_freshness_uses_filename_utc_not_ls_localtime
    test_check_backup_freshness_empty_bucket

    printf '\n========================================\n'
    printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
    if (( FAIL > 0 )); then
        printf 'Failed tests:\n'
        for t in "${FAILED_TESTS[@]}"; do
            printf '  - %s\n' "$t"
        done
        exit 1
    fi
    printf 'All tests passed.\n'
    exit 0
}

main "$@"
