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

from apps.admin_api.services import staff_roster as staff_roster_service
from apps.admin_api.services.staff_roster import build_staff_roster
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.tenancy.context import current_tenant, tenant_scope
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


def _state_of(item: dict, role: str) -> str:
    return next(r["state"] for r in item["roles"] if r["role"] == role)


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
        # LINKED on purpose. An unlinked foreign master never touches the
        # `linked_bot_user__display_name` join, which is the only path a
        # foreign person's name could travel into this body — so the
        # unlinked shape tests the boring half.
        link_master_to_bot_user(make_master(other_tenant, name="Чужой мастер"), theirs_bu)

        payload = _get(client).json()
        names = set(_by_name(payload))

        # Positive: our two are here, and there are exactly two.
        assert payload["total_count"] == 2
        assert names == {"Карина", "Аня"}
        # Negative: on the very same response.
        assert "Чужой админ" not in names
        assert "Чужой мастер" not in names


class TestTheCatalogReadIsTenantScoped:
    """MKT1 (#1018) regression, asserted on behaviour.

    The first cut read the catalog through ``CatalogMaster.all_tenants``
    — the manager that spans every salon — taken by inertia from
    neighbouring admin code. The explicit ``tenant=`` filter below it meant
    the answer was right anyway, which is precisely why no existing test
    objected: with two guards, removing one changes nothing observable.

    Measured, not assumed. Deleting the ``tenant=`` filter leaves all 39
    tests green; deleting the ``tenant_scope`` this service enters turns
    four of them red. So the scope is the load-bearing guard and the
    filter is redundant by construction — kept as cheap defence for the
    day somebody removes the scope again «because the decorator already
    does it», but no behavioural test can distinguish it and none here
    pretends to. The manager itself is the linter's job (MKT1, repo-wide),
    not re-implemented as a unit test.
    """

    def test_a_foreign_master_is_absent_under_strict_scope(
        self, client, owner_bot_user, tenant, admin_bot_user, other_tenant, settings
    ):
        """Strict mode turns a missing tenant context into an exception.

        Under ``all_tenants`` this passes for the wrong reason (that manager
        ignores scope entirely). Its job is to prove the scoped read has a
        tenant to scope TO — that ``build_staff_roster`` really enters
        ``tenant_scope`` and does not lean on the admin decorator's.
        """

        settings.STRICT_TENANT_SCOPE = "strict"
        foreign_bu = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="9200",
            display_name="Чужой мастер-аккаунт",
            chat_id="9200",
        )
        link_master_to_bot_user(make_master(other_tenant, name="Чужой мастер"), foreign_bu)

        assert current_tenant() is None
        people, total, _ = build_staff_roster(tenant)
        names = {p.name for p in people}

        # Positive guard first: it answered, and answered fully.
        assert total == 2
        assert names == {"Карина", "Аня"}
        # Negative, on the same result.
        assert "Чужой мастер" not in names


class TestTheManagerMustNotStartHidingPeople:
    """Pins the manager choice itself, not just its tenant scoping.

    ``CatalogMaster.objects`` is ``_MasterManager(TenantScopedManager)``:
    tenant-bound, but hiding nothing — the «доступен для записи» filter
    lives in the opt-in ``bookable()`` method. That is exactly why the
    roster can use it.

    The failure this guards is one step away and silent: swap ``.objects``
    for ``.objects.bookable()`` (``is_active=True`` AND
    ``invite_status=ACCEPTED``) and the salon loses every invited and every
    archived master from the roster — the two groups an owner most needs to
    see, because they are the ones something must be done about. The list
    would still render, still be tenant-correct, and still pass every other
    test in this file.

    One test, both halves, on the same response: the people who would
    vanish are here, AND the ordinary master who would survive is here too.
    Without the second half this passes on a roster that returns nobody.
    """

    def test_invited_and_archived_masters_stay_in_the_roster(
        self, client, owner_bot_user, tenant, master
    ):
        make_master(
            tenant,
            name="Наталья Прохорова",
            external_id=91,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        make_master(
            tenant,
            name="Ирина Смирнова",
            external_id=92,
            is_active=False,
            archived_at=timezone.now() - timedelta(days=6),
        )

        payload = _get(client).json()
        names = set(_by_name(payload))

        # The two `bookable()` would drop.
        assert "Наталья Прохорова" in names
        assert "Ирина Смирнова" in names
        # The one it would keep — the guard that makes the two above mean
        # something rather than passing on an empty list.
        assert "Анна Петрова" in names
        assert payload["total_count"] == 4


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

        # Positive guard FIRST, and on `raw` — the very string the absence
        # assertions below read. The earlier version guarded with
        # `resp.json()["total_count"]` afterwards, which is a different
        # object read after the fact; `assert resp.status_code == 200` is
        # not a guard at all (DRF-1406) — a 200 carrying `{"items": []}`
        # satisfies it and every absence assertion under it.
        # Ids, not names: ``JsonResponse`` escapes non-ASCII, so «Анна
        # Петрова» is `Анна...` in this string and a
        # name match would fail for a reason that has nothing to do with
        # phones. Ids are ASCII and name the exact rows expected — a
        # stronger guard than «non-empty» either way.
        assert str(master.id) in raw
        assert str(owner_bot_user.id) in raw
        assert str(admin_bot_user.id) in raw

        # The fixtures give every BotUser this number.
        assert "79161234567" not in raw
        assert "phone" not in raw


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

    def test_a_later_direct_grant_is_not_credited_to_an_old_code(
        self, client, owner_bot_user, tenant, customer_bot_user
    ):
        """«Эта роль пришла по коду» ≠ «человек когда-то вводил код».

        Аня redeems an admin code, is revoked, and is later re-added by a
        management command. Stamping the new row «по коду доступа» tells
        the owner a live code let her back in — and sends her hunting for
        a code nobody holds.
        """

        _invite, code = issue_staff_invite(
            tenant=tenant,
            role=TenantStaff.Role.ADMIN,
            catalog_master=None,
            created_by=owner_bot_user,
            note="",
        )
        redeem_staff_invite(code=code, bot_user=customer_bot_user, tenant=tenant)
        redeemed = TenantStaff.all_tenants.get(
            bot_user=customer_bot_user, role=TenantStaff.Role.ADMIN
        )
        redeemed.deactivated_at = timezone.now()
        redeemed.save(update_fields=["deactivated_at"])
        # Re-added by hand, well after the code was burned.
        fresh = TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=customer_bot_user, role=TenantStaff.Role.ADMIN
        )
        fresh.created_at = timezone.now() + timedelta(days=1)
        fresh.save(update_fields=["created_at"])

        item = _by_name(_get(client).json())["Клиент"]
        admin_role = next(r for r in item["roles"] if r["role"] == "admin")

        assert admin_role["state"] == "active"
        assert admin_role["source"] == "direct"

    def test_every_since_is_close_to_the_offset_the_fixture_asked_for(
        self, client, owner_bot_user, tenant
    ):
        """Guard for the fixtures, not the view.

        A tolerance of a year would pass for any literal date written this
        year — precisely the class of value the no-literal-dates rule
        forbids. Rows these tests create moments ago must read as moments
        ago, and the one deliberately backdated fixture must land on its
        own offset.
        """

        backdated = make_master(tenant, name="Наталья Прохорова", external_id=98)
        backdated.invited_at = timezone.now() - timedelta(days=3)
        backdated.save(update_fields=["invited_at"])

        payload = _get(client).json()
        now = timezone.now()

        owner_since = parse_datetime(_by_name(payload)["Карина"]["roles"][0]["since"])
        assert owner_since is not None
        assert abs(now - owner_since) < timedelta(minutes=5)

        master_since = parse_datetime(_by_name(payload)["Наталья Прохорова"]["roles"][0]["since"])
        assert master_since is not None
        assert abs((now - timedelta(days=3)) - master_since) < timedelta(minutes=5)


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

    def test_a_re_granted_role_shows_once_and_shows_as_live(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        """Revoke and grant again and TWO admin rows exist.

        ``deactivated_at`` is a soft delete kept for audit, and only
        ``owner`` has a partial unique index — admin and receptionist can
        stack. Reporting both would put «Администратор» next to
        «Администратор, доступ отозван» on one person, which reads as a
        contradiction, not as history.
        """

        old = TenantStaff.all_tenants.get(bot_user=admin_bot_user)
        old.deactivated_at = timezone.now() - timedelta(days=2)
        old.save(update_fields=["deactivated_at"])
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=admin_bot_user, role=TenantStaff.Role.ADMIN
        )

        item = _by_name(_get(client).json())["Аня"]

        assert [r["role"] for r in item["roles"]] == ["admin"]
        assert _active_roles_of(item) == {"admin"}
        assert item["is_active"] is True

    def test_a_twice_revoked_role_shows_once_as_revoked(
        self, client, owner_bot_user, tenant, admin_bot_user
    ):
        """The same collapse when neither row is live — the later wins."""

        old = TenantStaff.all_tenants.get(bot_user=admin_bot_user)
        old.deactivated_at = timezone.now() - timedelta(days=9)
        old.save(update_fields=["deactivated_at"])
        newer = TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=admin_bot_user, role=TenantStaff.Role.ADMIN
        )
        newer.deactivated_at = timezone.now() - timedelta(days=1)
        newer.save(update_fields=["deactivated_at"])

        item = _by_name(_get(client).json())["Аня"]

        assert [r["role"] for r in item["roles"]] == ["admin"]
        assert item["is_active"] is False
        # The surviving grant is the later row, not the first one found.
        since = parse_datetime(item["roles"][0]["since"])
        assert since is not None
        assert since >= newer.created_at

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


class TestPendingIsNotRevokedAndNotActive:
    """A master invited yesterday is neither, and both words are lies.

    ``resolve_role`` grants the master role only on ACCEPTED + not
    archived. A PENDING catalog row has ``is_active=True,
    archived_at=None``, so judging by those two columns alone would print
    «Мастер · активна» for somebody the platform treats as a plain
    customer. Calling her «доступ отозван» instead is the opposite lie —
    nobody took anything from her, and the owner's next move is to resend
    the invite, not to wonder who revoked it.
    """

    def test_a_pending_master_is_pending(self, client, owner_bot_user, tenant, pending_master):
        item = _by_name(_get(client).json())["Наталья Прохорова"]

        assert _state_of(item, "master") == "pending"
        assert item["is_active"] is False
        assert _active_roles_of(item) == set()

    def test_an_accepted_master_is_active(self, client, owner_bot_user, tenant, master):
        """The positive guard: the same field on the same shape says active."""

        item = _by_name(_get(client).json())["Анна Петрова"]

        assert _state_of(item, "master") == "active"
        assert item["is_active"] is True

    def test_an_archived_pending_master_is_revoked_not_pending(
        self, client, owner_bot_user, tenant
    ):
        """Archived wins: she is gone, not waiting."""

        make_master(
            tenant,
            name="Ирина Смирнова",
            external_id=94,
            invite_status=CatalogMaster.InviteStatus.PENDING,
            is_active=False,
            archived_at=timezone.now() - timedelta(days=4),
        )

        item = _by_name(_get(client).json())["Ирина Смирнова"]

        assert _state_of(item, "master") == "revoked"

    def test_the_roster_and_the_resolver_agree_on_who_is_a_master(
        self, client, owner_bot_user, tenant, master_only_bot_user
    ):
        """The property, not the field: same answer as the auth layer.

        A screen that says «Мастер · активна» about a person
        ``resolve_role`` calls a customer has failed at the one thing it
        exists for.
        """

        pending = make_master(
            tenant,
            name="Наталья Прохорова",
            external_id=93,
            invite_status=CatalogMaster.InviteStatus.PENDING,
        )
        link_master_to_bot_user(pending, master_only_bot_user)

        assert resolve_role(master_only_bot_user).is_master is False

        item = _by_name(_get(client).json())["Наталья Прохорова"]
        assert _state_of(item, "master") == "pending"


# --- the cap ----------------------------------------------------------------


class TestTheCap:
    def test_it_truncates_the_list_and_still_counts_the_dropped(
        self, client, owner_bot_user, tenant, monkeypatch
    ):
        """``total_count`` must not shrink with the list.

        A truncated answer that also under-reports its own size hides the
        anomaly the cap exists to surface, and the screen's «показаны
        первые N из M» banner has nothing to say.
        """

        monkeypatch.setattr(staff_roster_service, "MAX_ROSTER_PEOPLE", 2)
        for i in range(3):
            make_master(tenant, name=f"Мастер {i}", external_id=200 + i)

        payload = _get(client).json()

        # Karina (owner) + three masters = 4 people, 2 returned.
        assert payload["total_count"] == 4
        assert len(payload["items"]) == 2
        assert payload["truncated"] is True

    def test_a_list_under_the_cap_is_not_truncated(self, client, owner_bot_user, tenant):
        """Positive guard — the flag is not simply always True."""

        payload = _get(client).json()

        assert payload["truncated"] is False
        assert payload["total_count"] == len(payload["items"]) == 1


# --- the service's own tenant scope -----------------------------------------


class TestItCarriesItsOwnTenantScope:
    """Called with no ambient scope, it must answer fully — not emptily.

    The reader uses the tenant-scoped default manager (MKT1: the catalog
    mirror's ``all_tenants`` belongs to ``apps.marketplace.discovery``).
    ``STRICT_TENANT_SCOPE`` defaults to ``audit``, where a scoped manager
    with no tenant in context returns ``.none()`` **silently** — so a
    caller outside a request would get «staff yes, masters no» and read it
    as a salon with no masters.

    The assertion is positive on purpose. «It did not crash» and «it
    returned everyone» are different claims, and only the second one
    distinguishes a working reader from a silently empty one.
    """

    def test_no_ambient_scope_still_returns_every_person(
        self, tenant, owner_bot_user, admin_bot_user, master
    ):
        assert current_tenant() is None

        people, total, truncated = build_staff_roster(tenant)

        assert total == 3
        assert {p.name for p in people} == {"Карина", "Аня", "Анна Петрова"}
        assert truncated is False

    def test_it_restores_whatever_scope_it_found(self, tenant, owner_bot_user, other_tenant):
        """A push/pop, not an assignment — the caller's scope survives."""

        with tenant_scope(other_tenant):
            build_staff_roster(tenant)
            assert current_tenant() == other_tenant

        assert current_tenant() is None

    def test_it_reads_the_tenant_it_was_given_not_the_one_in_context(
        self, tenant, owner_bot_user, admin_bot_user, other_tenant
    ):
        """The argument wins over an ambient scope naming another salon.

        Belt and braces: the explicit ``tenant=`` filter and the scope this
        function enters must agree, and if they ever disagree the argument
        is the one the caller meant.
        """

        theirs = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="9100",
            display_name="Чужой админ",
            chat_id="9100",
        )
        TenantStaff.all_tenants.create(
            tenant=other_tenant, bot_user=theirs, role=TenantStaff.Role.ADMIN
        )

        with tenant_scope(other_tenant):
            people, total, _ = build_staff_roster(tenant)

        assert total == 2
        assert {p.name for p in people} == {"Карина", "Аня"}


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
