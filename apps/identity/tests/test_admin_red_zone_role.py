"""Admin red-zone DB-role assertion tests (#572 / Sprint 1 Track A AS6).

Per spec `docs/specs/memory-entry-schema.md` §11 + ADR-0011 §10.

The AST lint (`tools/lint/red_zone_guard.py`) allowlists `apps/identity/
admin.py`, so it cannot enforce that a red-zone-capable admin runs as an
RLS-subject role. `apps.identity.admin.assert_admin_db_role()` is the runtime
guard that closes that gap, and `RedZoneGuardedAdminMixin` is the mandatory
base that wires it onto the serving path. These tests lock:

1. the mixin actually invokes the guard before reading (engine-agnostic), and
2. the guard's capability check (rolsuper / rolbypassrls, NOT role name) plus
   the underlying role separation from migration 0008 (Postgres-only).

# Capability, not name (S5 finding)

The guard must reject any SUPERUSER / BYPASSRLS role — a role merely *named*
`ayla_app` but granted BYPASSRLS would still bypass red-zone RLS. So the
Postgres-only tests exercise the privileged default test role (raises) and the
RLS-subject `ayla_app` role (passes).
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.db import ProgrammingError, connection, transaction
from django.test import RequestFactory

import apps.identity.admin as admin_mod
from apps.identity.admin import (
    RedZoneAdminRoleError,
    RedZoneGuardedAdminMixin,
    assert_admin_db_role,
)
from apps.identity.models import MemoryEntry

requires_pg = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="DB role separation + RLS are Postgres-only.",
)


# ───────────────────────────────────────────────────────────────────────
# Mixin wiring — engine-agnostic (runs on SQLite + CI)
# ───────────────────────────────────────────────────────────────────────


def test_mixin_runs_guard_before_queryset(monkeypatch) -> None:
    """`RedZoneGuardedAdminMixin.get_queryset` calls the guard before reading.

    This is the wiring that makes the control real: any red-zone admin
    inheriting the mixin runs `assert_admin_db_role()` ahead of the queryset.
    Engine-agnostic — we stub the guard and assert it was invoked.
    """
    calls: list[int] = []
    monkeypatch.setattr(admin_mod, "assert_admin_db_role", lambda: calls.append(1))

    class _DummyRedZoneAdmin(RedZoneGuardedAdminMixin, admin.ModelAdmin):
        pass

    model_admin = _DummyRedZoneAdmin(MemoryEntry, AdminSite())
    qs = model_admin.get_queryset(RequestFactory().get("/admin/"))

    assert calls == [1], "mixin.get_queryset must call assert_admin_db_role() exactly once"
    assert qs.model is MemoryEntry


def test_guard_noop_on_non_postgres() -> None:
    """On SQLite (no roles/RLS) the guard is a no-op and never raises."""
    if connection.vendor == "postgresql":
        pytest.skip("covered by the Postgres-only capability tests below")
    assert_admin_db_role()


# ───────────────────────────────────────────────────────────────────────
# Capability check + role separation — Postgres-only
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@requires_pg
def test_guard_passes_for_rls_subject_role() -> None:
    """Connected as `ayla_app` (neither SUPERUSER nor BYPASSRLS) → no error."""
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE ayla_app")
    # Must not raise — ayla_app is RLS-subject. The guard returns None; the
    # assertion is simply that it does not raise.
    assert_admin_db_role()


@pytest.mark.django_db
@requires_pg
def test_guard_raises_for_superuser_or_bypassrls() -> None:
    """A SUPERUSER / BYPASSRLS connection role → fail loud.

    The default test connection role is the DB owner/superuser, which bypasses
    RLS — exactly the misconfiguration the guard must catch. Skipped if the
    test happens to run as an already-RLS-subject role (can't exercise raise).
    """
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        privileged = cur.fetchone()[0]
    if not privileged:
        pytest.skip("default test role is already RLS-subject; cannot exercise the raise")
    with pytest.raises(RedZoneAdminRoleError):
        assert_admin_db_role()


@pytest.mark.django_db
@requires_pg
def test_ayla_ops_denied_on_red_zone_base_table() -> None:
    """RLS-style denial: `ayla_ops` has no grant on the base table.

    Role separation (migration 0008) gives `ayla_ops` SELECT only on the
    `memory_entry_safe` view, never on `identity_memoryentry` directly — a
    direct base-table read raises «permission denied». (Note: `ayla_ops` is
    itself RLS-subject, so it *passes* the role-capability guard above; this
    denial is the complementary DB-grant half of the defence.)
    """
    with pytest.raises(ProgrammingError):
        # Nested atomic so the aborted-transaction state from the denied query
        # is confined to this savepoint and doesn't break test teardown.
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_ops")
                cur.execute("SELECT 1 FROM identity_memoryentry LIMIT 1")
