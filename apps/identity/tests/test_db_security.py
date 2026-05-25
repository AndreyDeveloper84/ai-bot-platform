"""DB security layer tests (spec §13 rows 14.6, 14.17, 14.18).

Per spec `docs/specs/memory-entry-schema.md` §8 (DB roles) + §9 (RLS policy).

# Postgres-only

The migration 0008_red_zone_db_security is postgres-only (CREATE ROLE +
RLS policy + view are not supported on SQLite). All tests in this file
are pytest.mark.skipif'd for SQLite.

Local dev (SQLite): tests skip. Application-side defence (RedZoneReader
accessor + AST lint, veha 3 + veha 4) covers SQLite developer flow.

CI + production (Postgres): tests run and verify roles, view, RLS.

# Tests in this file (spec §13)

- 14.6 — DB roles + view + RLS policy exist after migration
- 14.17 — Red SELECT without GUC returns zero rows (RLS filters)
- 14.18 — GUC behaviour: empty string, non-UUID, valid UUID
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason=(
            "DB roles + view + RLS policy are Postgres-only. SQLite "
            "migration no-ops the security layer. Production + CI run "
            "Postgres."
        ),
    ),
]


def _set_red_zone_guc(cur, request_id: str) -> None:
    """Set the RLS-gating GUC for the current transaction.

    Using is_local=true means the GUC is auto-reset at the transaction
    end (matches the RedZoneReader pattern in veha 3).
    """
    cur.execute(
        "SELECT set_config('ayla.red_zone_access_context', %s, true)",
        [request_id],
    )


@pytest.fixture
def upc():
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


@pytest.fixture
def red_entry(upc):
    """A red-zone MemoryEntry with consent — passes CHECK 2."""
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone="red",
        source="explicit",
        consent_at=timezone.now(),
        content={"marker": "PII_CANARY"},
    )


@pytest.fixture
def green_entry(upc):
    """A green-zone MemoryEntry — always visible, no GUC required."""
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone="green",
        source="explicit",
        content={"diet": "vegetarian"},
    )


class TestDbRolesAndViewExist:
    """Test 14.6 — migration creates 5 roles + memory_entry_safe view + RLS policy."""

    def test_all_five_roles_exist(self):
        """All 5 roles from spec §8 are created."""
        expected_roles = {
            "ayla_app",
            "ayla_ops",
            "ayla_migrator",
            "ayla_backup",
            "ayla_ops_redzone_break_glass",
        }
        with connection.cursor() as cur:
            cur.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                [list(expected_roles)],
            )
            found = {row[0] for row in cur.fetchall()}
        assert found == expected_roles, f"Missing roles: {expected_roles - found}"

    def test_memory_entry_safe_view_exists(self):
        """memory_entry_safe view exists and filters red rows."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.views "
                "WHERE table_name = 'memory_entry_safe')"
            )
            exists = cur.fetchone()[0]
        assert exists, "memory_entry_safe view not found"

    def test_memory_entry_safe_view_omits_content_column(self):
        """View intentionally excludes the `content` column (spec §8)."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'memory_entry_safe'"
            )
            columns = {row[0] for row in cur.fetchall()}
        assert "content" not in columns, (
            "memory_entry_safe view should NOT expose `content` column — "
            "even non-red content shouldn't be readable via ops view "
            "(per spec §8 view definition comment)."
        )

    def test_rls_enabled_on_memory_entry(self):
        """ROW LEVEL SECURITY is enabled (and FORCEd)."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'identity_memoryentry'"
            )
            row = cur.fetchone()
        assert row is not None
        enabled, forced = row
        assert enabled, "RLS not enabled on identity_memoryentry"
        assert forced, "RLS not FORCEd (table owner can still bypass — wrong)"

    def test_rls_policy_named_correctly(self):
        """Policy `memory_entry_non_red_visible` exists per spec §9."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT policyname FROM pg_policies WHERE tablename = 'identity_memoryentry'"
            )
            policies = {row[0] for row in cur.fetchall()}
        assert "memory_entry_non_red_visible" in policies, (
            f"Expected policy not found. Got: {policies}"
        )

    def test_select_filter_is_restrictive(self):
        """Round-5 Cat 4 regression — SELECT filter MUST be RESTRICTIVE.

        Postgres combines permissive policies via OR. Without RESTRICTIVE
        on the SELECT-filter, a companion `FOR ALL USING (true)` permissive
        policy would OR-collapse the SELECT filter to `true` → red zone
        leaks. RESTRICTIVE forces AND-intersection, so the filter applies
        independently of how many permissive write-authorisation policies
        exist.

        If this test fails, the RLS layer has regressed and red rows
        leak via direct ORM access. Do NOT merge.
        """
        with connection.cursor() as cur:
            cur.execute(
                "SELECT policyname, permissive FROM pg_policies "
                "WHERE tablename = 'identity_memoryentry' "
                "AND policyname = 'memory_entry_non_red_visible'"
            )
            row = cur.fetchone()
        assert row is not None, "SELECT filter policy missing"
        # pg_policies.permissive is the literal text 'PERMISSIVE' or 'RESTRICTIVE'.
        assert row[1] == "RESTRICTIVE", (
            f"memory_entry_non_red_visible MUST be RESTRICTIVE — got {row[1]!r}. "
            "PERMISSIVE would OR-collapse with the write_all policy and leak red."
        )


class TestRlsBlocksRedWithoutGuc:
    """Test 14.17 — RLS filters red rows when GUC unset.

    Note on `objects.all()` returning the row in Django tests:
      Django's test DB is owned by the test-runner role (typically the
      default postgres superuser in CI). FORCE ROW LEVEL SECURITY means
      RLS DOES apply even to the table owner. But the role running
      tests may have BYPASSRLS (superuser default).

      To exercise RLS faithfully we must either:
      (a) Switch role inside the test via `SET LOCAL ROLE ayla_app`, OR
      (b) Run tests as a non-superuser role.

      Approach (a) is used here — `_as_ayla_app` context manager.
    """

    def test_red_invisible_without_guc(self, red_entry, green_entry):
        """RLS hides red rows when GUC unset."""
        from contextlib import contextmanager

        @contextmanager
        def _as_ayla_app():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_app")
                # GUC is NOT set — red rows should be invisible.
                yield cur
                # SET LOCAL auto-resets at transaction end.

        with _as_ayla_app() as cur:
            cur.execute(
                "SELECT id, sensitivity_zone FROM identity_memoryentry ORDER BY sensitivity_zone"
            )
            rows = cur.fetchall()

        zones = {row[1] for row in rows}
        assert "red" not in zones, (
            f"Red row leaked through RLS without GUC. Zones returned: {zones}"
        )
        assert "green" in zones, "Green row should be visible always"

    def test_red_visible_with_valid_uuid_guc(self, red_entry):
        """RLS allows red rows when GUC is a valid UUID."""
        request_id = str(uuid.uuid4())
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ROLE ayla_app")
            _set_red_zone_guc(cur, request_id)
            cur.execute("SELECT id FROM identity_memoryentry WHERE sensitivity_zone = 'red'")
            rows = cur.fetchall()

        assert len(rows) == 1, (
            f"Red row should be visible with valid UUID GUC. Got: {len(rows)} rows"
        )


class TestGucValidation:
    """Test 14.18 — GUC behaviour for empty string, non-UUID, valid UUID."""

    def test_empty_guc_blocks_red(self, red_entry):
        """Empty-string GUC is treated as «no context» — red invisible."""
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ROLE ayla_app")
            _set_red_zone_guc(cur, "")  # empty string
            cur.execute("SELECT COUNT(*) FROM identity_memoryentry WHERE sensitivity_zone = 'red'")
            count = cur.fetchone()[0]
        assert count == 0, "Empty GUC should NOT grant red-zone visibility"

    def test_non_uuid_garbage_blocks_red(self, red_entry):
        """Non-UUID GUC value blocks red rows (regex mismatch)."""
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ROLE ayla_app")
            _set_red_zone_guc(cur, "not-a-uuid-just-garbage")
            cur.execute("SELECT COUNT(*) FROM identity_memoryentry WHERE sensitivity_zone = 'red'")
            count = cur.fetchone()[0]
        assert count == 0, "Non-UUID GUC should NOT grant red-zone visibility"

    def test_uuid_without_dashes_blocks_red(self, red_entry):
        """UUID without dashes (raw hex 32 chars) doesn't match the regex."""
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ROLE ayla_app")
            # Valid UUID but no dashes — regex requires the canonical form.
            _set_red_zone_guc(cur, uuid.uuid4().hex)
            cur.execute("SELECT COUNT(*) FROM identity_memoryentry WHERE sensitivity_zone = 'red'")
            count = cur.fetchone()[0]
        assert count == 0, (
            "Hex-only UUID (no dashes) should NOT match the canonical UUID "
            "regex in the RLS policy. Use `str(uuid.uuid4())` not `.hex`."
        )

    def test_uppercase_uuid_blocks_red(self, red_entry):
        """Uppercase UUID doesn't match the regex (regex is lowercase-only)."""
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ROLE ayla_app")
            _set_red_zone_guc(cur, str(uuid.uuid4()).upper())
            cur.execute("SELECT COUNT(*) FROM identity_memoryentry WHERE sensitivity_zone = 'red'")
            count = cur.fetchone()[0]
        assert count == 0, (
            "Uppercase UUID should NOT match lowercase-only regex. "
            "RedZoneReader produces lowercase UUIDs via str(uuid.uuid4())."
        )

    def test_canonical_lowercase_uuid_allows_red(self, red_entry):
        """Canonical lowercase UUID with dashes allows red visibility."""
        with connection.cursor() as cur:
            cur.execute("SET LOCAL ROLE ayla_app")
            _set_red_zone_guc(cur, str(uuid.uuid4()))
            cur.execute("SELECT COUNT(*) FROM identity_memoryentry WHERE sensitivity_zone = 'red'")
            count = cur.fetchone()[0]
        assert count == 1, "Valid UUID GUC should grant red-zone visibility"
