"""CREATE INDEX CONCURRENTLY for the Q12-α continuation chain FK.

Tech-lead double-pass S4 (PR #526): the standard ``AddField(db_index=True)``
on a >100k-row prod tenant table takes a multi-second
``AccessExclusiveLock`` because Django wraps the ``ALTER TABLE`` +
``CREATE INDEX`` in a single transaction. ``CREATE INDEX CONCURRENTLY``
takes a much lighter ``ShareUpdateExclusiveLock`` (allows concurrent
reads + writes) but cannot run inside a transaction.

Migration shape:

* Migration 0009 added the FK column without an index (``db_index=False``).
* This migration adds the index — Postgres path uses ``CREATE INDEX
  CONCURRENTLY``; SQLite + other vendors (tests) use plain
  ``CREATE INDEX``. Both paths use ``IF NOT EXISTS`` so a partial
  failure / retry is safe.

``atomic = False`` is required for the Postgres path (``CONCURRENTLY``
rejects inside transactions). It's harmless on other vendors.
"""

from __future__ import annotations

from django.db import migrations

_INDEX_NAME = "booking_bookingrequest_original_booking_event_id_idx"
_TABLE = "booking_bookingrequest"
_COLUMN = "original_booking_event_id"


def _create_index_vendor_aware(apps, schema_editor) -> None:
    """Forward operation: vendor-aware CREATE INDEX.

    Postgres → ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` (light
    lock, blocks no concurrent traffic).
    SQLite + others → plain ``CREATE INDEX IF NOT EXISTS`` (no
    CONCURRENTLY support and no concurrency concern at test scale).
    """

    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME} ON {_TABLE} ({_COLUMN})"
    else:
        sql = f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} ON {_TABLE} ({_COLUMN})"
    schema_editor.execute(sql)


def _drop_index_vendor_aware(apps, schema_editor) -> None:
    """Reverse operation: vendor-aware DROP INDEX (mirrors forward)."""

    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        sql = f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}"
    else:
        sql = f"DROP INDEX IF EXISTS {_INDEX_NAME}"
    schema_editor.execute(sql)


class Migration(migrations.Migration):
    # CRITICAL: ``atomic = False`` so CREATE INDEX CONCURRENTLY can run
    # on Postgres. Harmless on SQLite.
    atomic = False

    dependencies = [
        ("booking", "0009_q12a_continuation_chain_root_fk"),
    ]

    operations = [
        # Django's state isn't mutated by this migration — model
        # already declares the FK in 0009 (with db_index=False). The
        # actual DB index is created via raw SQL so it isn't
        # represented in the model state; `makemigrations` won't try
        # to re-create it because db_index=False on the field.
        migrations.RunPython(
            _create_index_vendor_aware,
            reverse_code=_drop_index_vendor_aware,
        ),
    ]
