"""Tests for apps.identity.services.solo_onboarding (Веха 1).

Veha 1 scope: basic correctness — atomicity, idempotency, default naming,
bootstrap-tenant gate, partial-state detection.

Race-condition tests + exhaustive idempotency live in Veha 2.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import (
    BOOTSTRAP_TENANT_SLUG,
    BootstrapTenantMissing,
    SoloOnboardingPartialStateError,
    SoloOnboardingResult,
    _default_tenant_name,
    _solo_external_id,
    _solo_tenant_slug,
    create_solo_provider,
    is_solo_provider,
)
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def bootstrap_tenant():
    """The ops-pre-seeded bootstrap tenant. Required by every test."""
    return Tenant.objects.create(
        slug=BOOTSTRAP_TENANT_SLUG,
        name="Ayla Solo — Registration",
    )


@pytest.fixture
def channel_identity():
    """A unique synthetic identity per test to avoid slug collisions."""
    return {
        "channel": "max",
        "channel_user_id": f"max-{uuid.uuid4().hex[:8]}",
        "display_name": "Ольга Иванова",
        "phone": "+79991234567",
        "chat_id": "chat-123",
    }


# ─── TestHelpers — pure functions ───────────────────────────────────────


class TestHelpers:
    def test_default_tenant_name_first_token(self):
        assert _default_tenant_name("Ольга Иванова") == "Студия Ольга"

    def test_default_tenant_name_single_word(self):
        assert _default_tenant_name("Ольга") == "Студия Ольга"

    def test_default_tenant_name_empty_fallback(self):
        assert _default_tenant_name("") == "Студия мастера"
        assert _default_tenant_name("   ") == "Студия мастера"

    def test_default_tenant_name_strips_whitespace(self):
        assert _default_tenant_name("  Ольга  ") == "Студия Ольга"

    def test_solo_tenant_slug_deterministic(self):
        s1 = _solo_tenant_slug("max", "12345")
        s2 = _solo_tenant_slug("max", "12345")
        assert s1 == s2
        assert s1.startswith("solo-max-")
        assert len(s1) <= 50  # SlugField max_length

    def test_solo_tenant_slug_differs_per_identity(self):
        assert _solo_tenant_slug("max", "12345") != _solo_tenant_slug("max", "12346")
        assert _solo_tenant_slug("max", "12345") != _solo_tenant_slug("telegram", "12345")

    def test_solo_external_id_negative(self):
        v = _solo_external_id(uuid.uuid4())
        assert v < 0
        # Bound: within signed-int 32-bit range
        assert v >= -(2**31 - 1)

    def test_solo_external_id_deterministic(self):
        bot_user_id = uuid.uuid4()
        assert _solo_external_id(bot_user_id) == _solo_external_id(bot_user_id)


# ─── TestBootstrapGate — pre-flight check ───────────────────────────────


class TestBootstrapGate:
    """Pre-flight: BootstrapTenantMissing raises when ops setup not done."""

    def test_missing_bootstrap_raises(self, channel_identity):
        # No bootstrap_tenant fixture loaded — Tenant table empty.
        with pytest.raises(BootstrapTenantMissing, match=BOOTSTRAP_TENANT_SLUG):
            create_solo_provider(**channel_identity)


# ─── TestFreshOnboarding — happy path: all 5 rows created ───────────────


class TestFreshOnboarding:
    def test_creates_all_5_rows(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity)

        assert isinstance(result, SoloOnboardingResult)
        assert result.created is True

        # All 5 records exist with correct cross-references
        assert result.tenant.pk is not None
        assert result.tenant.slug.startswith("solo-max-")
        assert result.tenant.id != bootstrap_tenant.id

        assert result.bot_user.tenant_id == result.tenant.id
        assert result.bot_user.channel == "max"
        assert result.bot_user.channel_user_id == channel_identity["channel_user_id"]
        assert result.bot_user.display_name == "Ольга Иванова"
        assert result.bot_user.phone == "+79991234567"

        assert result.owner_staff.tenant_id == result.tenant.id
        assert result.owner_staff.bot_user_id == result.bot_user.id
        assert result.owner_staff.role == TenantStaff.Role.OWNER
        assert result.owner_staff.deactivated_at is None

        assert result.admin_staff.tenant_id == result.tenant.id
        assert result.admin_staff.bot_user_id == result.bot_user.id
        assert result.admin_staff.role == TenantStaff.Role.ADMIN
        assert result.admin_staff.deactivated_at is None

        assert result.master.tenant_id == result.tenant.id
        assert result.master.linked_bot_user_id == result.bot_user.id
        assert result.master.invite_status == CatalogMaster.InviteStatus.ACCEPTED
        assert result.master.invite_token is None  # self-onboarded, no invite
        assert result.master.external_id < 0  # synthetic negative space

    def test_default_tenant_name_used(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity)
        # display_name='Ольга Иванова' → first token 'Ольга' → «Студия Ольга»
        assert result.tenant.name == "Студия Ольга"
        # Master name mirrors tenant name at seed time
        assert result.master.name == "Студия Ольга"

    def test_explicit_tenant_name_overrides_default(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity, tenant_name="Salon Maxima")
        assert result.tenant.name == "Salon Maxima"
        assert result.master.name == "Salon Maxima"

    def test_empty_display_name_fallback(self, bootstrap_tenant):
        result = create_solo_provider(
            channel="max",
            channel_user_id=f"max-{uuid.uuid4().hex[:8]}",
            display_name="",
        )
        assert result.tenant.name == "Студия мастера"


# ─── TestIdempotency — second call returns existing ────────────────────


class TestIdempotency:
    def test_second_call_returns_existing(self, bootstrap_tenant, channel_identity):
        first = create_solo_provider(**channel_identity)
        second = create_solo_provider(**channel_identity)

        assert first.created is True
        assert second.created is False
        assert first.tenant.pk == second.tenant.pk
        assert first.bot_user.pk == second.bot_user.pk
        assert first.owner_staff.pk == second.owner_staff.pk
        assert first.admin_staff.pk == second.admin_staff.pk
        assert first.master.pk == second.master.pk

    def test_no_duplicate_rows_on_second_call(self, bootstrap_tenant, channel_identity):
        create_solo_provider(**channel_identity)
        create_solo_provider(**channel_identity)

        solo_tenant_slug = _solo_tenant_slug(
            channel_identity["channel"], channel_identity["channel_user_id"]
        )
        tenant = Tenant.objects.get(slug=solo_tenant_slug)

        assert BotUser.all_tenants.filter(tenant=tenant).count() == 1
        assert TenantStaff.all_tenants.filter(tenant=tenant).count() == 2  # owner + admin
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

    def test_idempotent_tenant_name_ignored_on_second_call(
        self, bootstrap_tenant, channel_identity
    ):
        """Second call with different tenant_name returns the FIRST name —
        we don't rename on idempotent return (caller intent ambiguous)."""
        first = create_solo_provider(**channel_identity, tenant_name="Original Name")
        second = create_solo_provider(**channel_identity, tenant_name="New Name")

        assert first.tenant.name == "Original Name"
        assert second.tenant.name == "Original Name"  # not renamed


# ─── TestAtomicity — failure mid-seed → full rollback ──────────────────


class TestAtomicity:
    def test_force_master_failure_rolls_back_staff_tenant_bot_user(
        self, bootstrap_tenant, channel_identity
    ):
        """Simulate CatalogMaster.create failure → all 4 earlier creates
        roll back. No orphan tenant / bot_user / staff rows."""
        with patch.object(
            CatalogMaster.all_tenants,
            "create",
            side_effect=IntegrityError("simulated master create failure"),
        ):
            with pytest.raises(IntegrityError):
                create_solo_provider(**channel_identity)

        # Verify NO partial state survived rollback
        solo_slug = _solo_tenant_slug(
            channel_identity["channel"], channel_identity["channel_user_id"]
        )
        assert not Tenant.objects.filter(slug=solo_slug).exists()
        assert not BotUser.all_tenants.filter(
            channel="max", channel_user_id=channel_identity["channel_user_id"]
        ).exists()
        assert TenantStaff.all_tenants.count() == 0
        assert CatalogMaster.all_tenants.count() == 0

    def test_force_admin_staff_failure_rolls_back_earlier_rows(
        self, bootstrap_tenant, channel_identity
    ):
        """Force second TenantStaff.create (admin) to fail → tenant + bot_user
        + owner_staff also roll back."""
        call_count = {"n": 0}
        real_create = TenantStaff.all_tenants.create

        def flaky_create(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:  # second call = admin
                raise IntegrityError("simulated admin staff failure")
            return real_create(*args, **kwargs)

        with patch.object(TenantStaff.all_tenants, "create", side_effect=flaky_create):
            with pytest.raises(IntegrityError):
                create_solo_provider(**channel_identity)

        solo_slug = _solo_tenant_slug(
            channel_identity["channel"], channel_identity["channel_user_id"]
        )
        assert not Tenant.objects.filter(slug=solo_slug).exists()
        assert TenantStaff.all_tenants.count() == 0


# ─── TestPartialState — pre-existing inconsistent rows → raise ─────────


class TestPartialState:
    def test_tenant_without_bot_user_raises(self, bootstrap_tenant, channel_identity):
        """Solo tenant exists but no matching BotUser → partial state."""
        slug = _solo_tenant_slug(channel_identity["channel"], channel_identity["channel_user_id"])
        Tenant.objects.create(slug=slug, name="Manual Tenant")
        # No BotUser, no staff, no master

        with pytest.raises(SoloOnboardingPartialStateError, match="no BotUser found"):
            create_solo_provider(**channel_identity)

    def test_tenant_bot_user_but_missing_master_raises(self, bootstrap_tenant, channel_identity):
        """Tenant + BotUser + 2 staff exist, but CatalogMaster missing."""
        slug = _solo_tenant_slug(channel_identity["channel"], channel_identity["channel_user_id"])
        tenant = Tenant.objects.create(slug=slug, name="Partial Salon")
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel=channel_identity["channel"],
            channel_user_id=channel_identity["channel_user_id"],
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.ADMIN)
        # CatalogMaster missing

        with pytest.raises(SoloOnboardingPartialStateError, match="master=False"):
            create_solo_provider(**channel_identity)

    def test_tenant_bot_user_but_missing_admin_raises(self, bootstrap_tenant, channel_identity):
        """Owner + master exist, admin missing → partial state."""
        slug = _solo_tenant_slug(channel_identity["channel"], channel_identity["channel_user_id"])
        tenant = Tenant.objects.create(slug=slug, name="Partial Salon")
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel=channel_identity["channel"],
            channel_user_id=channel_identity["channel_user_id"],
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_solo_external_id(bu.id),
            external_updated_at=timezone.now(),
            name="X",
            linked_bot_user=bu,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        # admin missing

        with pytest.raises(SoloOnboardingPartialStateError, match="admin=False"):
            create_solo_provider(**channel_identity)


# ─── Веха 2 — race conditions + edge cases ──────────────────────────────


class TestConcurrentSerialization:
    """Веха 2 — concurrent first-time calls serialize via advisory lock.

    Requires `transaction=True` for real commits (default django_db wraps
    each test in a rolled-back transaction → savepoint, where advisory
    locks behave subtly differently and threads can't observe each
    other's «commits»).

    Postgres-only — advisory locks are PG-specific. SQLite skipped.
    """

    @pytest.mark.django_db(transaction=True)
    @pytest.mark.skipif(
        __import__("django.db", fromlist=["connection"]).connection.vendor != "postgresql",
        reason="pg_advisory_xact_lock is Postgres-only.",
    )
    def test_two_concurrent_first_time_calls_serialize(self):
        """Two threads, same identity, fresh DB — one wins (created=True),
        the other returns idempotent (created=False). No IntegrityError
        on Tenant.slug unique constraint."""
        import concurrent.futures
        import threading
        from django.db import connections

        # Seed bootstrap tenant (transaction=True doesn't auto-load fixtures).
        Tenant.objects.create(
            slug=BOOTSTRAP_TENANT_SLUG,
            name="Ayla Solo — Registration",
        )

        identity = {
            "channel": "max",
            "channel_user_id": f"max-{uuid.uuid4().hex[:8]}",
            "display_name": "Concurrent Ольга",
        }

        results: list[SoloOnboardingResult] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker():
            # Each thread MUST close inherited connection so it opens its
            # own — otherwise both threads share one PG session and lock
            # acquisition is trivially satisfied (not real contention).
            connections.close_all()
            barrier.wait()  # release both threads simultaneously
            try:
                return create_solo_provider(**identity)
            finally:
                connections.close_all()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(worker) for _ in range(2)]
            for f in concurrent.futures.as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as e:
                    errors.append(e)

        assert errors == [], f"Concurrent calls raised: {errors}"
        assert len(results) == 2

        # Both results point at the SAME tenant (serialized via lock).
        assert results[0].tenant.pk == results[1].tenant.pk
        assert results[0].bot_user.pk == results[1].bot_user.pk

        # Exactly one `created=True`, one `created=False`.
        created_flags = sorted(r.created for r in results)
        assert created_flags == [False, True], (
            f"Expected exactly one created+one idempotent, got: {created_flags}. "
            "Advisory lock did NOT serialize — race condition still live."
        )

        # Verify single set of rows in DB (no duplicates from race).
        target_slug = _solo_tenant_slug(identity["channel"], identity["channel_user_id"])
        tenant = Tenant.objects.get(slug=target_slug)
        assert BotUser.all_tenants.filter(tenant=tenant).count() == 1
        assert TenantStaff.all_tenants.filter(tenant=tenant).count() == 2
        assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == 1

        # Cleanup so subsequent transaction=True tests start clean.
        # (Django TRUNCATEs identity_botuser etc between tests; tenancy
        # also TRUNCATEd. No manual teardown needed.)


# ─── TestBotUserAnchorMigration — same identity in two tenants OK ──────


class TestBotUserAnchorMigration:
    """Веха 2 — bootstrap-tenant BotUser and solo-tenant BotUser coexist.

    Per-tenant `unique_together=(tenant, channel, channel_user_id)` allows
    the same (channel, channel_user_id) to anchor TWO BotUsers — one in
    bootstrap, one in solo. The bootstrap row is historical entry; the
    solo row is the canonical workspace identity.

    `create_solo_provider` creates the solo-tenant BotUser fresh; the
    bootstrap row is left untouched.
    """

    def test_bootstrap_bot_user_unchanged_when_solo_seeded(
        self, bootstrap_tenant, channel_identity
    ):
        # Pre-create the bootstrap-tenant BotUser as if the channel
        # adapter resolved this identity through the solo bot.
        boot_bu = BotUser.all_tenants.create(
            tenant=bootstrap_tenant,
            channel=channel_identity["channel"],
            channel_user_id=channel_identity["channel_user_id"],
            display_name="Bootstrap entry",
        )
        boot_bu_id = boot_bu.id
        boot_display = boot_bu.display_name

        # Trigger solo onboarding for the same identity.
        result = create_solo_provider(**channel_identity)

        # Solo bot_user is a SEPARATE row, NOT the bootstrap one.
        assert result.bot_user.id != boot_bu_id
        assert result.bot_user.tenant_id == result.tenant.id
        assert result.bot_user.tenant_id != bootstrap_tenant.id

        # Bootstrap row exists, unchanged.
        boot_bu.refresh_from_db()
        assert boot_bu.id == boot_bu_id
        assert boot_bu.display_name == boot_display
        assert boot_bu.tenant_id == bootstrap_tenant.id

        # Two BotUser rows coexist for this (channel, channel_user_id) —
        # legal per per-tenant unique_together.
        rows = BotUser.all_tenants.filter(
            channel=channel_identity["channel"],
            channel_user_id=channel_identity["channel_user_id"],
        )
        assert rows.count() == 2
        assert {r.tenant_id for r in rows} == {bootstrap_tenant.id, result.tenant.id}


# ─── TestSlugBoundary — slug always fits within max_length=50 ──────────


class TestSlugBoundary:
    """Веха 2 — `_solo_tenant_slug` never overflows `SlugField(max_length=50)`."""

    def test_short_channel_well_within_50(self):
        slug = _solo_tenant_slug("max", "12345")
        assert len(slug) <= 50
        # Expected exact shape: solo-{3}-{8} = 5+3+1+8 = 17
        assert len(slug) == 17

    def test_longest_realistic_channel_fits(self):
        # Longest production channel — `whatsapp` = 8 chars. Slug = 5+8+1+8=22.
        slug = _solo_tenant_slug("whatsapp", "12345678901234567890")
        assert len(slug) <= 50
        assert slug.startswith("solo-whatsapp-")

    def test_overlong_channel_is_trimmed_to_fit(self):
        """Defensive: even a 60-char channel produces a slug ≤ 50 chars."""
        long_channel = "a" * 60
        slug = _solo_tenant_slug(long_channel, "12345")
        assert len(slug) <= 50, f"Slug overflowed max_length=50: {len(slug)} chars"
        assert slug.startswith("solo-")
        # Hash suffix preserved (last 8 chars)
        from apps.identity.services.solo_onboarding import _slug_hash

        assert slug.endswith(_slug_hash(long_channel, "12345"))


# ─── TestNonAsciiChannelUserId — Unicode safety ─────────────────────────


class TestNonAsciiChannelUserId:
    """Веха 2 — channel_user_id with Cyrillic / emoji / etc works end-to-end.

    Russian market: Telegram allows Unicode usernames; MAX may forward
    Unicode display names through channel_user_id in custom auth flows.
    """

    def test_cyrillic_channel_user_id_succeeds(self, bootstrap_tenant):
        result = create_solo_provider(
            channel="max",
            channel_user_id="иванов_2024",
            display_name="Иван",
        )
        assert result.created is True
        assert result.bot_user.channel_user_id == "иванов_2024"
        # Slug is ASCII (hash is hex) — safe for URLs / headers
        assert result.tenant.slug.isascii()
        assert result.tenant.slug.startswith("solo-max-")

    def test_emoji_channel_user_id_succeeds(self, bootstrap_tenant):
        result = create_solo_provider(
            channel="max",
            channel_user_id="user🦄123",
            display_name="Unicorn Master",
        )
        assert result.created is True
        assert result.bot_user.channel_user_id == "user🦄123"
        assert result.tenant.slug.isascii()

    def test_cyrillic_idempotency_works(self, bootstrap_tenant):
        """Second call for same Unicode identity returns existing rows."""
        first = create_solo_provider(
            channel="max", channel_user_id="мария_2024", display_name="Мария"
        )
        second = create_solo_provider(
            channel="max", channel_user_id="мария_2024", display_name="Мария"
        )
        assert first.created is True
        assert second.created is False
        assert first.tenant.pk == second.tenant.pk


# ─── TestPartialStateExtras — Веха 1's partial-state covers все 3 кейса ─
#
# Веха 1 уже покрыл 3 partial-state scenarios. Веха 2 verdict Fork 2
# подтвердил: NO auto-recovery, raise PartialStateError. The existing 3
# tests are the regression set; no new tests needed here, but
# PartialStateError docstring updated per tech-lead sample 2026-05-26
# (see service file).


# ─── Веха 3 — is_solo_provider() helper (Tau §3.1 distinct-count) ──────


class TestIsSoloProviderHelper:
    """Веха 3 — `is_solo_provider(tenant)` per Tau policy §3.1.

    Formula: `len(active_staff_user_ids ∪ active_master_user_ids) == 1`.
    Pragmatic edge cases accepted (1 staff with 0 masters still counts
    as solo per Tau).
    """

    def test_after_create_solo_provider_returns_true(self, bootstrap_tenant, channel_identity):
        result = create_solo_provider(**channel_identity)
        assert is_solo_provider(result.tenant) is True

    def test_bootstrap_tenant_returns_false(self, bootstrap_tenant):
        """Bootstrap tenant has no staff + no masters → 0 distinct → False.

        Important — bootstrap is NOT itself a solo workspace; it's a
        landing pad for new registrations.
        """
        assert is_solo_provider(bootstrap_tenant) is False

    def test_team_tenant_returns_false(self, bootstrap_tenant):
        """2 distinct people (owner + admin = different users) → False."""
        team_tenant = Tenant.objects.create(slug="team-tn", name="Team Salon")
        owner_bu = BotUser.all_tenants.create(
            tenant=team_tenant, channel="max", channel_user_id="owner-1"
        )
        admin_bu = BotUser.all_tenants.create(
            tenant=team_tenant, channel="max", channel_user_id="admin-1"
        )
        TenantStaff.all_tenants.create(
            tenant=team_tenant, bot_user=owner_bu, role=TenantStaff.Role.OWNER
        )
        TenantStaff.all_tenants.create(
            tenant=team_tenant, bot_user=admin_bu, role=TenantStaff.Role.ADMIN
        )
        assert is_solo_provider(team_tenant) is False

    def test_single_staff_no_masters_returns_true(self, bootstrap_tenant):
        """Edge: 1 staff row, 0 master rows → 1 distinct person → True.

        Per Tau §3.1 pragmatic — even without an explicit CatalogMaster,
        a tenant with one staff person is functionally solo.
        """
        tenant = Tenant.objects.create(slug="staff-only", name="Staff Only")
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="staff-only-1"
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        assert is_solo_provider(tenant) is True

    def test_single_master_no_staff_returns_true(self, bootstrap_tenant):
        """Edge: 0 staff, 1 master with linked_bot_user → 1 distinct → True."""
        tenant = Tenant.objects.create(slug="master-only", name="Master Only")
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="master-only-1"
        )
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_solo_external_id(bu.id),
            external_updated_at=timezone.now(),
            name="Solo",
            linked_bot_user=bu,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        assert is_solo_provider(tenant) is True

    def test_same_person_in_both_staff_and_master_returns_true(self, bootstrap_tenant):
        """Set union dedupes — same bot_user as staff AND master → True."""
        tenant = Tenant.objects.create(slug="solo-dup", name="Solo Dup")
        bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="dup-1")
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.ADMIN)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_solo_external_id(bu.id),
            external_updated_at=timezone.now(),
            name="X",
            linked_bot_user=bu,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        assert is_solo_provider(tenant) is True

    def test_deactivated_staff_excluded(self, bootstrap_tenant):
        """`deactivated_at IS NOT NULL` staff rows are NOT counted.

        Scenario: tenant had a team, masters left, only owner-admin active
        + 0 active masters → 1 distinct → solo.
        """
        tenant = Tenant.objects.create(slug="post-team", name="Post Team")
        active_bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="active-1"
        )
        ex_bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="ex-1")
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=active_bu, role=TenantStaff.Role.OWNER
        )
        # Deactivated row — should NOT count
        TenantStaff.all_tenants.create(
            tenant=tenant,
            bot_user=ex_bu,
            role=TenantStaff.Role.ADMIN,
            deactivated_at=timezone.now(),
        )
        assert is_solo_provider(tenant) is True

    def test_archived_master_excluded(self, bootstrap_tenant):
        """`archived_at IS NOT NULL` masters are NOT counted."""
        tenant = Tenant.objects.create(slug="archived-mtn", name="Archived")
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="archived-mtn-1"
        )
        ex_bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="ex-master-1"
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_solo_external_id(bu.id),
            external_updated_at=timezone.now(),
            name="Active master",
            linked_bot_user=bu,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        # Archived — NOT counted toward distinct
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=_solo_external_id(ex_bu.id),
            external_updated_at=timezone.now(),
            name="Ex-master",
            linked_bot_user=ex_bu,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            archived_at=timezone.now(),
        )
        assert is_solo_provider(tenant) is True

    def test_master_without_linked_bot_user_excluded(self, bootstrap_tenant):
        """Mysite-synced legacy masters (`linked_bot_user IS NULL`) excluded.

        Otherwise an empty tenant with one legacy mirror row would
        report solo=True incorrectly (NULL ID would dedupe with itself
        and count as 1 — semantically wrong).
        """
        tenant = Tenant.objects.create(slug="legacy-master", name="Legacy")
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=42,  # positive — mysite-synced
            external_updated_at=timezone.now(),
            name="Legacy Master",
            linked_bot_user=None,  # NOT bridged yet
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        assert is_solo_provider(tenant) is False
