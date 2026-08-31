"""GET /api/v1/admin/staff/ — the salon's people with their roles.

### Why this file leans on positive guards

Every negative assertion here («чужой тенант не виден», «телефона нет»)
is paired with a positive one on the *same* data: how many of our own
people came back, and which. A roster endpoint that returns an empty
list passes every negative check ever written about it and means
nothing. The pairing is the only thing that tells the two apart.

### The trap this endpoint exists to fall into

Roles are additive and live in two tables (ADR-0008): ``TenantStaff``
carries owner / admin / receptionist, ``CatalogMaster.linked_bot_user``
carries master. A salon owner who also does hair is one person with two
rows. Read one table and she is invisible; read both naively and she is
two people. ``TestOnePersonTwoRoles`` is the pilot's own shape and the
reason the merge is a service rather than a queryset.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.tenancy.models import Tenant, TenantStaff

from .conftest import init_data_header, link_master_to_bot_user, make_master

pytestmark = pytest.mark.django_db


def _get(client: Client, *, user_id: str = "5001"):
    return client.get(
        reverse("admin_api:staff_roster"),
        HTTP_AUTHORIZATION=init_data_header(user_id),
    )


def _by_name(payload: dict) -> dict[str, dict]:
    return {item["name"]: item for item in payload["items"]}


def _roles_of(item: dict) -> set[str]:
    return {r["role"] for r in item["roles"]}


def _active_roles_of(item: dict) -> set[str]:
    return {r["role"] for r in item["roles"] if r["active"]}


# --- who may look -----------------------------------------------------------


class TestWhoMayLook:
    """Owner only.

    The owner ruled that changing roles is owner-only. Viewing the map of
    who holds which administrative role is the reconnaissance half of the
    same act, and nobody asked for it to be wider. The negative cases
    below are worthless without ``test_the_owner_sees_the_roster``, which
    proves the endpoint answers at all on this data.
    """

    def test_the_owner_sees_the_roster(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _get(client)

        assert resp.status_code == 200, resp.content
        payload = resp.json()
        # Positive guard with a number: two staff rows exist, two people
        # come back. An empty list would satisfy every negative test here.
        assert payload["total_count"] == 2
        assert set(_by_name(payload)) == {"Карина", "Аня"}

    def test_an_admin_may_not(self, client, owner_bot_user, tenant, admin_bot_user):
        resp = _get(client, user_id="5002")

        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_a_receptionist_may_not(self, client, tenant, receptionist_bot_user):
        assert _get(client, user_id="5003").status_code == 403

    def test_a_customer_may_not(self, client, tenant, customer_bot_user):
        assert _get(client, user_id="5005").status_code == 403

    def test_a_master_may_not(self, client, tenant, master, master_only_bot_user):
        link_master_to_bot_user(master, master_only_bot_user)

        assert _get(client, user_id="5004").status_code == 403

    def test_post_is_not_a_verb_here(self, client, owner_bot_user, tenant):
        resp = client.post(
            reverse("admin_api:staff_roster"),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("5001"),
        )

        assert resp.status_code == 405


# --- both halves of the role model -----------------------------------------


class TestBothTablesAreRead:
    """Showing one table hides the other half of the salon."""

    def test_a_staff_only_person_appears(self, client, owner_bot_user, tenant, admin_bot_user):
        payload = _get(client).json()

        anya = _by_name(payload)["Аня"]
        assert _active_roles_of(anya) == {"admin"}
        assert anya["master_id"] is None
        assert anya["has_account"] is True

    def test_a_master_only_person_appears(self, client, owner_bot_user, tenant, master):
        payload = _get(client).json()

        anna = _by_name(payload)["Анна Петрова"]
        assert _active_roles_of(anna) == {"master"}
        assert anna["master_id"] == str(master.id)

    def test_a_master_with_no_max_account_still_appears(
        self, client, owner_bot_user, tenant, master
    ):
        """The pilot's dominant shape: catalog row, nobody linked to it.

        Dropping these would hide three of the pilot salon's four masters
        (counted on the pilot in DRF-1149).
        """

        payload = _get(client).json()

        anna = _by_name(payload)["Анна Петрова"]
        assert anna["has_account"] is False
        assert anna["bot_user_id"] is None
        assert _active_roles_of(anna) == {"master"}

    def test_the_two_halves_are_counted_together(
        self, client, owner_bot_user, tenant, admin_bot_user, master
    ):
        payload = _get(client).json()

        # Karina (owner) + Anya (admin) + Anna (master, unlinked) = 3.
        assert payload["total_count"] == 3
        assert set(_by_name(payload)) == {"Карина", "Аня", "Анна Петрова"}


class TestOnePersonTwoRoles:
    """The additive-role trap, on the pilot's own form.

    Karina is the salon owner AND one of its masters. She must appear
    once, carrying both roles — not twice, and not with the master half
    silently winning.
    """

    @pytest.fixture
    def owner_master(self, tenant: Tenant, owner_bot_user: BotUser) -> CatalogMaster:
        m = make_master(tenant, name="Карина", external_id=77)
        return link_master_to_bot_user(m, owner_bot_user)

    def test_she_appears_exactly_once(self, client, tenant, owner_master, owner_bot_user):
        payload = _get(client).json()

        assert payload["total_count"] == 1
        assert len(payload["items"]) == 1

    def test_she_carries_both_roles(self, client, tenant, owner_master, owner_bot_user):
        item = _get(client).json()["items"][0]

        assert _active_roles_of(item) == {"owner", "master"}
        assert item["bot_user_id"] == str(owner_bot_user.id)
        assert item["master_id"] == str(owner_master.id)

    def test_a_third_role_joins_the_same_row(self, client, tenant, owner_master, owner_bot_user):
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=owner_bot_user, role=TenantStaff.Role.RECEPTIONIST
        )

        payload = _get(client).json()

        assert payload["total_count"] == 1
        assert _active_roles_of(payload["items"][0]) == {"owner", "master", "receptionist"}

    def test_an_unbridged_master_is_a_separate_person(
        self, client, tenant, owner_master, owner_bot_user, master
    ):
        """Two rows nobody can prove are one human stay two.

        Same asymmetry ``is_solo_provider`` settled (DRF-1149): over-count
        rather than merge on a guess. Merging a staff row with an unlinked
        catalog row of the same name would be a name match, and names are
        not identity.
        """

        payload = _get(client).json()

        assert payload["total_count"] == 2


# --- tenant isolation -------------------------------------------------------


class TestOtherSalonsAreInvisible:
    def test_their_people_are_absent_and_ours_are_present(
        self, client, owner_bot_user, tenant, admin_bot_user, other_tenant
    ):
        """One test, both halves — an empty list would pass the first alone."""

        theirs_bu = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="9001",
            display_name="Чужой админ",
            chat_id="9001",
        )
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=theirs_bu, role=TenantStaff.Role.ADMIN
        )
        make_master(other_tenant, name="Чужой мастер")

        payload = _get(client).json()
        names = set(_by_name(payload))

        # Positive: our two are here, and there are exactly two.
        assert payload["total_count"] == 2
        assert names == {"Карина", "Аня"}
        # Negative: on the very same response.
        assert "Чужой админ" not in names
        assert "Чужой мастер" not in names


# --- privacy ----------------------------------------------------------------


class TestNoPhoneNumbers:
    """DRF-1039 rules that a client's phone never reaches an executor.

    No owner decision covers a *staff member's* phone, so this endpoint
    does not invent one: a roster is a list of who works here, and a
    number is not needed to read it. Asserted on the serialised body
    rather than on intent — the way ``test_salon_day`` does it — because
    a field added later by a well-meaning ``select_related`` is exactly
    what an intent-level assertion misses.
    """

    def test_no_phone_in_the_body(self, client, owner_bot_user, tenant, admin_bot_user, master):
        link_master_to_bot_user(master, admin_bot_user)

        resp = _get(client)
        raw = resp.content.decode()

        assert resp.status_code == 200
        # The fixtures give every BotUser this number.
        assert "79161234567" not in raw
        assert "phone" not in raw
        # Positive guard on the same body: it is not empty.
        assert resp.json()["total_count"] == 2


# --- how they got in, and when ---------------------------------------------


class TestHowTheyGotIn:
    def test_a_redeemed_access_code_is_named_as_one(
        self, client, owner_bot_user, tenant, customer_bot_user
    ):
        _invite, code = issue_staff_invite(
            tenant=tenant,
            role=TenantStaff.Role.ADMIN,
            catalog_master=None,
            created_by=owner_bot_user,
            note="",
        )
        redeem_staff_invite(code=code, bot_user=customer_bot_user, tenant=tenant)

        item = _by_name(_get(client).json())["Клиент"]
        admin_role = next(r for r in item["roles"] if r["role"] == "admin")

        assert admin_role["source"] == "access_code"
        assert admin_role["since"] is not None

    def test_a_staff_row_nobody_invited_says_so(self, client, owner_bot_user, tenant):
        """The pilot's owner was made by a management command."""

        item = _by_name(_get(client).json())["Карина"]
        owner_role = next(r for r in item["roles"] if r["role"] == "owner")

        assert owner_role["source"] == "direct"
        assert owner_role["since"] is not None

    def test_a_master_invited_through_the_master_flow_says_so(self, client, owner_bot_user, tenant):
        invited = make_master(tenant, name="Наталья Прохорова", external_id=98)
        invited.invited_at = timezone.now() - timedelta(days=3)
        invited.save(update_fields=["invited_at"])

        item = _by_name(_get(client).json())["Наталья Прохорова"]
        master_role = next(r for r in item["roles"] if r["role"] == "master")

        assert master_role["source"] == "master_invite"
        assert master_role["since"] is not None

    def test_a_catalog_synced_master_admits_it_does_not_know(self, client, owner_bot_user, tenant):
        synced = make_master(tenant, name="Ирина Смирнова", external_id=96)
        synced.invited_at = None
        synced.save(update_fields=["invited_at"])

        item = _by_name(_get(client).json())["Ирина Смирнова"]
        master_role = next(r for r in item["roles"] if r["role"] == "master")

        assert master_role["source"] == "direct"
        assert master_role["since"] is None

    def test_every_since_is_an_offset_from_now(self, client, owner_bot_user, tenant, master):
        """Guard for the fixtures, not the view.

        Every timestamp these tests assert on is an offset from ``now``. A
        fixture that hard-codes a date passes in the year it was written
        and rots after.
        """

        payload = _get(client).json()
        now = timezone.now()

        for item in payload["items"]:
            for role in item["roles"]:
                if role["since"] is None:
                    continue
                parsed = parse_datetime(role["since"])
                assert parsed is not None
                assert abs((now - parsed).days) < 365


# --- revoked access ---------------------------------------------------------


class TestRevokedAccessIsVisibleAsRevoked:
    def test_a_revoked_role_stays_on_the_row_marked_inactive(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        staff = TenantStaff.all_tenants.get(bot_user=admin_bot_user)
        staff.deactivated_at = timezone.now() - timedelta(hours=2)
        staff.save(update_fields=["deactivated_at"])

        item = _by_name(_get(client).json())["Аня"]

        assert item["is_active"] is False
        assert _roles_of(item) == {"admin"}
        assert _active_roles_of(item) == set()

    def test_one_live_role_keeps_the_person_active(
        self, client, owner_bot_user, tenant, admin_bot_user, master
    ):
        link_master_to_bot_user(master, admin_bot_user)
        staff = TenantStaff.all_tenants.get(bot_user=admin_bot_user)
        staff.deactivated_at = timezone.now() - timedelta(hours=2)
        staff.save(update_fields=["deactivated_at"])

        item = _by_name(_get(client).json())["Анна Петрова"]

        assert item["is_active"] is True
        assert _active_roles_of(item) == {"master"}
        assert _roles_of(item) == {"master", "admin"}

    def test_an_archived_master_is_inactive(self, client, owner_bot_user, tenant):
        make_master(
            tenant,
            name="Ирина Смирнова",
            external_id=95,
            is_active=False,
            archived_at=timezone.now() - timedelta(days=10),
        )

        item = _by_name(_get(client).json())["Ирина Смирнова"]

        assert item["is_active"] is False
        assert _active_roles_of(item) == set()


# --- ordering ---------------------------------------------------------------


class TestOrdering:
    def test_active_people_come_first_then_by_privilege(
        self, client, owner_bot_user, tenant, admin_bot_user, receptionist_bot_user
    ):
        revoked = TenantStaff.all_tenants.get(bot_user=receptionist_bot_user)
        revoked.deactivated_at = timezone.now() - timedelta(days=1)
        revoked.save(update_fields=["deactivated_at"])

        names = [i["name"] for i in _get(client).json()["items"]]

        assert names == ["Карина", "Аня", "Стажёр"]
