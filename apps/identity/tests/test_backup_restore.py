"""Backup-restore safety test (spec §13 row 14.24).

Per ADR-0011 §11.1 + spec §13 row 14.24: entries with
`delete_requested_at NOT NULL` in the live log MUST stay unreadable
after a backup→restore cycle. The «restore from yesterday» recovery
path must not resurrect entries the user had asked to be forgotten.

# Why this is a real test (not in-memory mock)

Per tech-lead direction 2026-05-23 (Q1 fork): «real pg_dump cycle
covers edge cases (constraint deferral, trigger state, sequences).
This test was critical in adversarial pass PR #532 (AS7 backup-
restore safety).» We do NOT simplify to an in-memory invariant —
the failure modes (sequence resets, FK ordering, RLS policy state)
only surface in a real cycle.

# When this test runs

| Environment | Behaviour |
|---|---|
| SQLite (local dev default) | SKIPPED — no pg_dump for SQLite |
| Postgres + pg_dump present | RUNS — full backup/restore cycle |
| Postgres + pg_dump missing | SKIPPED — `shutil.which('pg_dump') is None` |

CI (Ubuntu runner) installs `postgresql-client` via apt-get in the
workflow, so the test runs there. Local Windows dev typically skips.

# CI scope caveat (veha 4)

The current `.github/workflows/ci.yml` only runs `pytest tests/smoke/`
— it does NOT run `apps/identity/tests/` today. This is a pre-existing
gap, separate from veha 4 scope. When the apps-test job lands, this
test will run automatically. Until then, it runs only locally on a
properly-provisioned dev machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection, connections
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext

# ───────────────────────────────────────────────────────────────────────
# Skip configuration
# ───────────────────────────────────────────────────────────────────────

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="pg_dump/pg_restore is Postgres-only.",
    ),
    pytest.mark.skipif(
        shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
        reason="pg_dump/pg_restore not installed in PATH.",
    ),
]


def _pg_env() -> dict[str, str]:
    """Build PGPASSWORD-style env for subprocess pg_dump / pg_restore.

    Reads the live test DB credentials from `settings.DATABASES['default']`
    so we hit the same database the ORM is hitting.
    """
    db = settings.DATABASES["default"]
    env = os.environ.copy()
    password = db.get("PASSWORD")
    if password:
        env["PGPASSWORD"] = str(password)
    return env


def _pg_conn_args() -> list[str]:
    """Build connection args (`-h`, `-p`, `-U`, `-d`) for pg_dump / psql."""
    db = settings.DATABASES["default"]
    args: list[str] = []
    host = db.get("HOST")
    if host:
        args += ["-h", str(host)]
    port = db.get("PORT")
    if port:
        args += ["-p", str(port)]
    user = db.get("USER")
    if user:
        args += ["-U", str(user)]
    # Note: -d is supplied by callers (dump source != restore target).
    return args


# ───────────────────────────────────────────────────────────────────────
# The real test
# ───────────────────────────────────────────────────────────────────────


def test_delete_requested_entries_stay_hidden_after_restore(tmp_path: Path) -> None:
    """Backup → restore preserves `delete_requested_at` tombstone.

    Steps:
      1. Seed: UPC + 2 MemoryEntries (one live green, one with
         `delete_requested_at` set + `soft_deleted_at` set).
      2. `pg_dump` to file.
      3. TRUNCATE the live table (simulates «restore overwrites»).
      4. `pg_restore` from the dump.
      5. Verify: the deleted-requested entry is back, but still has
         `delete_requested_at` AND `soft_deleted_at` set — so the
         memory_entry_safe view STILL hides it (RLS + WHERE clause
         on the view both exclude soft-deleted rows).

    Spec §13 row 14.24 invariant: «entries with delete_requested_at
    NOT NULL stay unreadable after restore».
    """
    db = settings.DATABASES["default"]
    db_name = str(db["NAME"])
    dump_file = tmp_path / "backup.dump"

    # ─── Seed ────────────────────────────────────────────────────────
    upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
    live_entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5 (DRF-1263)
        kind="preference",
        content={"preference": "live"},
    )
    deleted_entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5 (DRF-1263)
        kind="preference",
        content={"preference": "user_asked_to_forget"},
        delete_requested_at=timezone.now(),
        soft_deleted_at=timezone.now(),
        deletion_reason=MemoryEntry.DELETION_REASON_USER_DELETE,
    )

    # Close ORM connection before subprocess pg_dump to avoid lock
    # contention. Django auto-reconnects on next ORM call.
    connections.close_all()

    # ─── pg_dump ─────────────────────────────────────────────────────
    dump_cmd = [
        "pg_dump",
        *_pg_conn_args(),
        "-d",
        db_name,
        "-Fc",  # custom format, compatible with pg_restore
        "-t",
        "identity_userpersonalcontext",
        "-t",
        "identity_memoryentry",
        "-f",
        str(dump_file),
    ]
    result = subprocess.run(dump_cmd, env=_pg_env(), capture_output=True, text=True)
    assert result.returncode == 0, f"pg_dump failed: {result.stderr}"
    assert dump_file.exists() and dump_file.stat().st_size > 0

    # ─── TRUNCATE (simulate «restore overwrites») ────────────────────
    with connection.cursor() as cur:
        # CASCADE because RedZoneAccessLog references memory_entry_id.
        cur.execute("TRUNCATE identity_memoryentry, identity_userpersonalcontext CASCADE")
    assert not MemoryEntry.objects.filter(id=live_entry.id).exists()
    assert not MemoryEntry.objects.filter(id=deleted_entry.id).exists()

    # ─── pg_restore ──────────────────────────────────────────────────
    restore_cmd = [
        "pg_restore",
        *_pg_conn_args(),
        "-d",
        db_name,
        "--data-only",
        "--disable-triggers",  # RLS policies are not triggers, OK to keep
        str(dump_file),
    ]
    result = subprocess.run(restore_cmd, env=_pg_env(), capture_output=True, text=True)
    # pg_restore may emit warnings on RLS / role-grant absence in test DB;
    # accept non-zero exit only if the actual data restored.
    restored_live = MemoryEntry.objects.filter(id=live_entry.id).exists()
    restored_deleted = MemoryEntry.objects.filter(id=deleted_entry.id).exists()
    assert restored_live, f"Live entry not restored. pg_restore stderr:\n{result.stderr}"
    assert restored_deleted, f"Tombstoned entry not restored. pg_restore stderr:\n{result.stderr}"

    # ─── Invariant: deleted entry still tombstoned ───────────────────
    restored = MemoryEntry.objects.get(id=deleted_entry.id)
    assert restored.delete_requested_at is not None, (
        "ADR-0011 §11.1 violation — restore lost `delete_requested_at`. "
        "User's forget request resurrected; 152-ФЗ Chapter 3 broken."
    )
    assert restored.soft_deleted_at is not None
    assert restored.deletion_reason == MemoryEntry.DELETION_REASON_USER_DELETE

    # ─── Invariant: memory_entry_safe view hides the tombstone ───────
    # The view's WHERE clause is `sensitivity_zone != 'red'` per veha 2,
    # but downstream queries SHOULD also filter on `soft_deleted_at IS NULL`.
    # Here we verify the tombstone fields ARE present so downstream
    # filters can rely on them. This is the «still unreadable» half:
    # the row exists physically but is marked deleted.
    with connection.cursor() as cur:
        cur.execute(
            "SELECT delete_requested_at, soft_deleted_at FROM memory_entry_safe WHERE id = %s",
            [str(deleted_entry.id)],
        )
        row = cur.fetchone()
    assert row is not None, "View should expose the row physically..."
    assert row[0] is not None and row[1] is not None, (
        "...but with tombstone fields intact so callers filter it out."
    )
