"""Role resolver contract tests (PR 1.5 / ADR-0008).

Pins the 5-role detection model the platform uses for every downstream
master / admin / owner workflow. The resolver is the single source of
truth — these tests guard the matrix.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import (
    CAPABILITIES,
    has_capability,
    resolve_role,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def tenant_a(settings) -> Tenant:
    settings.STRICT_TENANT_SCOPE = "strict"
    return Tenant.objects.create(slug="rr-a", name="Salon A")


@pytest.fixture
def tenant_b(settings) -> Tenant:
    settings.STRICT_TENANT_SCOPE = "strict"
    return Tenant.objects.create(slug="rr-b", name="Salon B")


@pytest.fixture
def bot_user(tenant_a) -> BotUser:
    with tenant_scope(tenant_a):
        return BotUser.objects.create(
            tenant=tenant_a,
            channel="max",
            channel_user_id="bot-rr-1",
            display_name="Анна",
        )


@pytest.fixture
def bot_user_two(tenant_a) -> BotUser:
    with tenant_scope(tenant_a):
        return BotUser.objects.create(
            tenant=tenant_a,
            channel="max",
            channel_user_id="bot-rr-2",
            display_name="Карина",
        )


@pytest.fixture
def bot_user_b(tenant_b) -> BotUser:
    with tenant_scope(tenant_b):
        return BotUser.objects.create(
            tenant=tenant_b,
            channel="max",
            channel_user_id="bot-rr-1",  # same channel id, different tenant
            display_name="Анна (другой салон)",
        )


def _make_master(tenant: Tenant, bot_user: BotUser, **overrides) -> CatalogMaster:
    """Test helper: fabricate an ACCEPTED master linked to ``bot_user``."""

    fields = {
        "tenant": tenant,
        "external_id": overrides.pop("external_id", 1001),
        "external_updated_at": datetime(2026, 5, 19, tzinfo=timezone.utc),
        "name": overrides.pop("name", "Анна"),
        "linked_bot_user": bot_user,
        "invite_status": CatalogMaster.InviteStatus.ACCEPTED,
        "mode": CatalogMaster.Mode.INVITE,
    }
    fields.update(overrides)
    return CatalogMaster.all_tenants.create(**fields)


# --- customer-only --------------------------------------------------------


class TestResolveCustomer:
    def test_no_staff_no_master_is_customer(self, tenant_a, bot_user):
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "customer"
        assert ctx.is_customer is True
        assert ctx.is_master is False
        assert ctx.is_owner is False
        assert ctx.master_id is None
        assert ctx.landing_path == "/"

    def test_customer_capabilities_empty(self, tenant_a, bot_user):
        ctx = resolve_role(bot_user)
        # Customer baseline carries no admin capabilities. Booking /
        # browsing endpoints are gated by init-data, not by the matrix.
        assert ctx.capabilities == frozenset()


# --- single-role detection -----------------------------------------------


class TestResolveMaster:
    def test_master_link_makes_master(self, tenant_a, bot_user):
        master = _make_master(tenant_a, bot_user)
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "master"
        assert ctx.is_master is True
        assert ctx.master_id == master.id
        assert ctx.is_customer is True  # implicit baseline
        assert ctx.landing_path == "/master/dashboard"
        assert "view_conversation_list_own" in ctx.capabilities
        assert "send_reply_own" in ctx.capabilities
        assert "view_conversation_list_all" not in ctx.capabilities

    def test_archived_master_link_ignored(self, tenant_a, bot_user):
        """Soft-archived masters drop back to customer."""

        _make_master(
            tenant_a,
            bot_user,
            archived_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )
        ctx = resolve_role(bot_user)
        assert ctx.is_master is False
        assert ctx.primary_role == "customer"

    def test_pending_master_link_ignored(self, tenant_a, bot_user):
        """An invite that hasn't been accepted yet doesn't grant master role."""

        _make_master(
            tenant_a,
            bot_user,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        ctx = resolve_role(bot_user)
        assert ctx.is_master is False


class TestResolveAdmin:
    def test_admin_staff_row_only(self, tenant_a, bot_user):
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user, role=TenantStaff.Role.ADMIN
            )
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "admin"
        assert ctx.is_admin is True
        assert ctx.is_master is False
        assert ctx.master_id is None
        assert ctx.landing_path == "/admin/team"
        # Admin sees ALL conversations, can manage drafts, but no
        # owner-only privileges.
        assert "view_conversation_list_all" in ctx.capabilities
        assert "demote_human_locked" in ctx.capabilities
        assert "manage_tenant_roles" not in ctx.capabilities
        assert "export_data" not in ctx.capabilities


class TestResolveReceptionist:
    def test_receptionist_landing_and_caps(self, tenant_a, bot_user):
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a,
                bot_user=bot_user,
                role=TenantStaff.Role.RECEPTIONIST,
            )
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "receptionist"
        assert ctx.is_receptionist is True
        # DRF-1151 — was "/admin/conversations", a route App.tsx has never
        # mounted. Receptionists land on the roster like every other
        # admin-side role; the conversations surface is post-pilot.
        assert ctx.landing_path == "/admin/team"
        # Receptionist caps: list + audited phone + send + safety promote.
        assert "view_conversation_list_all" in ctx.capabilities
        assert "view_customer_phone_audited" in ctx.capabilities
        # Receptionist canNOT demote — only promote-to-locked is safety.
        assert "demote_human_locked" not in ctx.capabilities
        assert "block_customer" not in ctx.capabilities


class TestResolveOwner:
    def test_owner_holds_all_admin_caps(self, tenant_a, bot_user):
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user, role=TenantStaff.Role.OWNER
            )
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "owner"
        assert ctx.is_owner is True
        # Owner has the full owner-only set.
        for cap in (
            "manage_tenant_roles",
            "export_data",
            "tune_assistant",
            "change_assistant_persona",
            "override_sla",
            "view_customer_medical",
        ):
            assert cap in ctx.capabilities, f"owner missing {cap!r}"


# --- multi-role combos ---------------------------------------------------


class TestMultiRole:
    def test_master_plus_admin_primary_is_admin(self, tenant_a, bot_user):
        """ADR-0008 decision 3: highest privilege wins. Admin > Master."""

        _make_master(tenant_a, bot_user)
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user, role=TenantStaff.Role.ADMIN
            )
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "admin"
        assert ctx.is_admin is True
        assert ctx.is_master is True
        # master_id is still surfaced — Anna the master-admin needs her
        # master profile to render in /master/dashboard if she navigates.
        assert ctx.master_id is not None
        # Combined caps: admin all-scope PLUS master own-scope (union).
        assert "view_conversation_list_all" in ctx.capabilities
        assert "view_conversation_list_own" in ctx.capabilities

    def test_owner_plus_master_primary_is_owner(self, tenant_a, bot_user):
        _make_master(tenant_a, bot_user)
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user, role=TenantStaff.Role.OWNER
            )
        ctx = resolve_role(bot_user)
        assert ctx.primary_role == "owner"
        assert ctx.is_master is True
        assert ctx.is_owner is True
        assert ctx.master_id is not None
        assert ctx.landing_path == "/admin/team"
        assert "manage_tenant_roles" in ctx.capabilities

    def test_deactivated_staff_row_ignored(self, tenant_a, bot_user):
        """Deactivated TenantStaff drops the role from the effective set."""

        with tenant_scope(tenant_a):
            row = TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user, role=TenantStaff.Role.ADMIN
            )
            row.deactivated_at = datetime(2026, 5, 19, tzinfo=timezone.utc)
            row.save(update_fields=["deactivated_at"])
        ctx = resolve_role(bot_user)
        assert ctx.is_admin is False
        assert ctx.primary_role == "customer"


# --- cross-tenant ---------------------------------------------------------


class TestCrossTenant:
    def test_role_resolution_is_per_tenant(self, tenant_a, tenant_b, bot_user, bot_user_b):
        """Same person at two salons: master in A, plain customer in B."""

        _make_master(tenant_a, bot_user)  # master in A only
        ctx_a = resolve_role(bot_user)
        ctx_b = resolve_role(bot_user_b)

        assert ctx_a.primary_role == "master"
        assert ctx_a.is_master is True
        assert ctx_b.primary_role == "customer"
        assert ctx_b.is_master is False


# --- has_capability ------------------------------------------------------


class TestHasCapability:
    def test_owner_has_every_owner_capability(self, tenant_a, bot_user):
        with tenant_scope(tenant_a):
            TenantStaff.objects.create(
                tenant=tenant_a, bot_user=bot_user, role=TenantStaff.Role.OWNER
            )
        ctx = resolve_role(bot_user)
        for cap in CAPABILITIES["owner"]:
            assert has_capability(ctx, cap) is True, f"owner lacks {cap!r}"

    def test_master_has_own_scope_only(self, tenant_a, bot_user):
        _make_master(tenant_a, bot_user)
        ctx = resolve_role(bot_user)
        # has the "own" cap...
        assert has_capability(ctx, "view_conversation_list_own") is True
        # ...but NOT the all-scope cap that only staff get.
        assert has_capability(ctx, "view_conversation_list_all") is False
        # ...and definitely NOT owner-only.
        assert has_capability(ctx, "manage_tenant_roles") is False

    def test_unknown_capability_is_false(self, tenant_a, bot_user):
        ctx = resolve_role(bot_user)
        assert has_capability(ctx, "no_such_capability") is False


class TestLandingPathsAreRealRoutes:
    """DRF-1151 — every landing path must be a route the Mini App mounts.

    Two of them were not. ``/admin/dashboard`` and ``/admin/conversations``
    have never existed in ``App.tsx``; the admin catch-all redirected to
    ``/admin/team`` and quietly covered for them, which is why the dead
    values survived. A value that only works because something downstream
    cleans up after it is not a contract.

    The route list below is mirrored by hand from ``App.tsx`` — routes
    live in TypeScript and Python cannot read them. Mirrored deliberately:
    a short list that must be edited in two places when a landing
    destination changes is cheaper than the silent redirect that hid the
    defect. When this fails, check ``adminRouteElements`` /
    ``masterRouteElements`` before changing the expectation.
    """

    #: Routes ``App.tsx`` mounts that ``_LANDING_PATH`` may name.
    MOUNTED_ROUTES = frozenset(
        {
            "/",
            "/admin/team",
            "/admin/services",
            "/admin/availability-requests",
            "/admin/internal-chat",
            "/admin/settings",
            "/master/dashboard",
            "/master/schedule",
            "/master/profile",
        }
    )

    def test_every_landing_path_is_mounted(self):
        from apps.identity.services.role_resolver import _LANDING_PATH

        for role, path in _LANDING_PATH.items():
            assert path in self.MOUNTED_ROUTES, (
                f"landing_path for {role!r} is {path!r}, which App.tsx does not mount"
            )

    def test_every_role_has_a_landing_path(self):
        from apps.identity.services.role_resolver import _LANDING_PATH, _ROLE_PRIVILEGE

        assert set(_LANDING_PATH) == set(_ROLE_PRIVILEGE)
