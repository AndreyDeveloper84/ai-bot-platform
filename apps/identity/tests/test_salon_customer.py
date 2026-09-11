"""§2 (owner 11.09), slice S2-1 — LINKED / SHADOW on the salon shell (DRF-1700).

The rule is three criteria in the owner's order and a fourth outcome; the
tests take each criterion alone, then the forbidden one (the name) with
everything else absent, then the command's two modes. The name rule is
held on the function's tree, not on an example.
"""

from __future__ import annotations

import ast
import inspect
import io
import textwrap
import uuid

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.identity.services import salon_customer as sc
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="s21-salon", name="Салон")


@pytest.fixture
def other() -> Tenant:
    return Tenant.objects.create(slug="s21-other", name="Другой")


@pytest.fixture
def global_bot() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug=GLOBAL_BOT_TENANT_SLUG, defaults={"name": "Клиентский бот"}
    )
    return obj


def _shell(tenant: Tenant, cid: str, **kw) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id=cid, **kw)


# --- the rule, one criterion at a time ------------------------------------------


class TestTheRuleInTheOwnersOrder:
    def test_max_id_in_the_client_contour(self, salon, global_bot):
        _shell(salon, "1001")
        _shell(global_bot, "1001")
        c = sc.classify("max", "1001")
        assert c.match_by == sc.MATCH_MAX_ID
        assert c.status == BotUser.CustomerStatus.LINKED
        assert len(c.salon_shell_ids) == 1 and len(c.global_shell_ids) == 1

    def test_identity_link_without_a_global_shell(self, salon):
        _shell(salon, "1002", ayla_user_id=uuid.uuid4())
        assert sc.classify("max", "1002").match_by == sc.MATCH_IDENTITY

    def test_confirmed_phone_without_the_other_two(self, salon):
        _shell(salon, "1003", phone="+79990001003")
        assert sc.classify("max", "1003").match_by == sc.MATCH_PHONE

    def test_none_of_the_three_is_shadow(self, salon, other):
        _shell(salon, "1004")
        _shell(other, "1004")
        c = sc.classify("max", "1004")
        assert c.match_by == sc.MATCH_SHADOW
        assert c.status == BotUser.CustomerStatus.SHADOW
        assert len(c.salon_shell_ids) == 2

    def test_max_id_wins_over_identity_in_the_owners_order(self, salon, global_bot):
        _shell(salon, "1005", ayla_user_id=uuid.uuid4())
        _shell(global_bot, "1005")
        assert sc.classify("max", "1005").match_by == sc.MATCH_MAX_ID

    def test_an_unknown_person_is_shadow_with_no_shells(self):
        c = sc.classify("max", "404")
        assert c.match_by == sc.MATCH_SHADOW
        assert c.salon_shell_ids == () and c.global_shell_ids == ()


# --- §2.7: the name is not a key --------------------------------------------------


class TestTheNameIsNotAKey:
    def test_same_name_in_the_client_contour_does_not_link(self, salon, global_bot):
        """Two people called the same thing, different ids: the salon one is
        SHADOW, whatever the client contour has under that name."""
        _shell(salon, "2001", display_name="Анна Иванова")
        _shell(global_bot, "9001", display_name="Анна Иванова")
        # Presence first: the same person by ID IS linked …
        _shell(global_bot, "2002")
        _shell(salon, "2002", display_name="Анна Иванова")
        assert sc.classify("max", "2002").match_by == sc.MATCH_MAX_ID
        # … and by name alone is not.
        assert sc.classify("max", "2001").match_by == sc.MATCH_SHADOW

    def test_the_rule_never_reads_a_name(self):
        """On the tree: no attribute or string that names a person appears in
        the classifier. A probe that swapped `channel_user_id` for
        `display_name` in the global lookup went red here, not by luck."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(sc.classify)))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        strings = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        forbidden = {"display_name", "first_name", "last_name", "name", "client_name", "full_name"}
        assert names & {"channel_user_id", "ayla_user_id", "phone"}, "the rule reads identifiers"
        assert not (names & forbidden), names & forbidden
        assert not (strings & forbidden), strings & forbidden


# --- the schema default -----------------------------------------------------------


class TestTheDefaultIsUnresolved:
    def test_a_new_shell_is_unresolved_with_no_moment(self, salon):
        bu = _shell(salon, "3001")
        assert bu.customer_status == BotUser.CustomerStatus.UNRESOLVED
        assert bu.customer_source == BotUser.CustomerSource.UNKNOWN
        assert bu.customer_status_at is None


# --- the command: reads by default, writes on --apply ----------------------------


class TestTheCommand:
    def _people(self, salon, other, global_bot):
        _shell(salon, "4001")
        _shell(global_bot, "4001")  # MAX-ID
        _shell(salon, "4002", ayla_user_id=uuid.uuid4())  # identity
        _shell(salon, "4003")
        _shell(other, "4003")  # shadow, two salons

    def test_dry_run_prints_the_breakdown_and_writes_nothing(self, salon, other, global_bot):
        self._people(salon, other, global_bot)
        out = io.StringIO()
        with CaptureQueriesContext(connection) as ctx:
            call_command("resolve_salon_customers", stdout=out)
        text = out.getvalue()
        verbs = {q["sql"].lstrip().split(" ", 1)[0].upper() for q in ctx.captured_queries}
        assert verbs, "the command must have READ something"
        assert verbs <= {"SELECT", "SAVEPOINT", "RELEASE"}, sorted(verbs)
        assert "сухой прогон" in text
        assert "MAX-ID=1  identity=1  phone=0  shadow=1  людей=3" in text
        assert "max:4003" in text
        assert (
            BotUser.all_tenants.filter(customer_status=BotUser.CustomerStatus.UNRESOLVED).count()
            == 5
        )

    def test_apply_stamps_shells_and_only_unresolved_ones(self, salon, other, global_bot):
        self._people(salon, other, global_bot)
        out = io.StringIO()
        call_command("resolve_salon_customers", "--apply", stdout=out)
        assert "записано строк: 5" in out.getvalue()
        assert "UNRESOLVED после: 0" in out.getvalue()

        shadow = BotUser.all_tenants.get(tenant=salon, channel_user_id="4003")
        assert shadow.customer_status == BotUser.CustomerStatus.SHADOW
        assert shadow.customer_source == BotUser.CustomerSource.SALON_ASSISTANT
        assert shadow.customer_status_at is not None

        linked = BotUser.all_tenants.get(tenant=salon, channel_user_id="4001")
        assert linked.customer_status == BotUser.CustomerStatus.LINKED
        assert linked.customer_source == BotUser.CustomerSource.SALON_ASSISTANT

        client = BotUser.all_tenants.get(tenant=global_bot, channel_user_id="4001")
        assert client.customer_status == BotUser.CustomerStatus.LINKED
        assert client.customer_source == BotUser.CustomerSource.CLIENT_BOT

        # A re-run is a report, not a second decision.
        shadow.customer_status = BotUser.CustomerStatus.LINKED
        shadow.save(update_fields=["customer_status"])
        out2 = io.StringIO()
        call_command("resolve_salon_customers", "--apply", stdout=out2)
        assert "записано строк: 0" in out2.getvalue()
        shadow.refresh_from_db()
        assert shadow.customer_status == BotUser.CustomerStatus.LINKED
