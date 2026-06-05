"""GIN index on ``audit_auditlog.payload`` for fast JSONB containment + key-presence queries.

Q12-α #679 follow-up to PR #678 (``Q12aChainTerminatorFilter`` Django Admin filter).

The filter compiles to ``payload ? 'q12a_chain_terminator'`` on Postgres
(via Django's ``JSONField.has_key`` lookup). Without a GIN index on
``payload``, every admin page load with the filter active does a
sequential scan over the audit table. At 90-day retention with
monotonically growing rows, this matters once the table crosses the
1M-row threshold (post-pilot scale).

### Index choice: ``jsonb_ops`` (default opclass)

Postgres has two GIN opclasses for JSONB:

* **Default ``jsonb_ops``** — supports ``?``, ``?|``, ``?&``, ``@>``,
  ``@?``, ``@@``. Larger index (≈2× of ``jsonb_path_ops``).
* ``jsonb_path_ops`` — supports ONLY ``@>``, ``@?``, ``@@``. Does NOT
  index the ``?`` / ``?|`` / ``?&`` operators. Smaller but narrower.

Our actual queries on this table:

* ``payload ? 'q12a_chain_terminator'`` — Django Admin filter
  (``Q12aChainTerminatorFilter`` from PR #678, the PRIMARY use case)
* ``payload @> '{"q12a_chain_terminator": true}'`` — raw-SQL fallback
  documented in the runbook (operator triage during admin-UI outage)

Initial draft picked ``jsonb_path_ops`` (matching the original #679
issue spec) but adversarial review caught the mismatch: ``?`` is the
PRIMARY operator (Django Admin filter), and ``jsonb_path_ops`` does
NOT index it — the planner falls back to a seq scan, defeating the
entire point of the migration. Switched to default ``jsonb_ops`` so
both operator forms are indexed. Storage cost ≈2× — acceptable trade
for the actual perf path being indexed.

### Migration shape: ``CREATE INDEX CONCURRENTLY``

Mirrors the project convention from
``apps/booking/migrations/0010_q12a_continuation_idx_concurrently.py``:

* Postgres → ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` (light
  ``ShareUpdateExclusiveLock``, blocks no concurrent traffic).
* SQLite + other vendors → no-op. SQLite does NOT support GIN indexes
  at all; the comparator falls back to ``JSON_TYPE(...) IS NOT NULL``
  which is already efficient enough for the test backend.

``atomic = False`` is required for the Postgres ``CONCURRENTLY`` path
(rejects inside transactions). Harmless on other vendors.
"""

from __future__ import annotations

from django.db import migrations

_INDEX_NAME = "auditlog_payload_gin"
_TABLE = "audit_auditlog"
_COLUMN = "payload"


def _create_gin_index_vendor_aware(apps, schema_editor) -> None:
    """Forward operation: Postgres-only GIN index; no-op on other vendors.

    Why not a plain B-tree fallback on SQLite: a B-tree on a JSONB
    column is useless for the ``?`` / ``@>`` operators (B-tree compares
    bytes, not JSON structure). The whole point of the index is the
    JSONB-aware GIN opclass. Other vendors (SQLite for tests) skip the
    index — their JSON queries already work at test scale.
    """

    vendor = schema_editor.connection.vendor
    if vendor != "postgresql":
        return
    # ``USING gin (payload)`` without an explicit opclass uses the
    # default ``jsonb_ops`` — covers both ``?`` (Admin filter primary
    # path) and ``@>`` (runbook raw-SQL fallback). See module docstring
    # for why ``jsonb_path_ops`` was rejected.
    sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_INDEX_NAME} ON {_TABLE} USING gin ({_COLUMN})"
    schema_editor.execute(sql)


def _drop_gin_index_vendor_aware(apps, schema_editor) -> None:
    """Reverse operation: Postgres-only DROP INDEX CONCURRENTLY."""

    vendor = schema_editor.connection.vendor
    if vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")


class Migration(migrations.Migration):
    # CRITICAL: atomic = False so CREATE INDEX CONCURRENTLY can run
    # on Postgres. Harmless on SQLite (which skips the operation
    # entirely via the vendor guard).
    atomic = False

    dependencies = [
        ("audit", "0002_auditlog_soft_delete"),
    ]

    operations = [
        # State is NOT mutated — the index lives on the DB side only.
        # ``AuditLog`` model has no ``Meta.indexes`` entry for this
        # GIN index (Django doesn't model GIN-with-opclass cleanly via
        # Meta), so makemigrations won't try to re-create it.
        migrations.RunPython(
            _create_gin_index_vendor_aware,
            reverse_code=_drop_gin_index_vendor_aware,
        ),
    ]
