"""Which BotUser a master request belongs to (DRF-1083).

Regression test for a live pilot failure on 2026-08-16: the cabinet opened,
``/api/v1/me`` answered 200, and every master endpoint answered 401
``not_a_master``.

Cause: the same MAX account legitimately has one ``BotUser`` per tenant,
and the staff link lives on exactly one of them. This surface picked
``order_by("-last_seen").first()`` across all tenants — whichever row the
person touched most recently. For the pilot owner that is reliably the
wrong one: he talks to the client bot daily (a ``global_bot`` row with no
master link) and opens the cabinet rarely (the ``formula-tela`` row that
holds the link).

Measured on the pilot before the fix:

    channel_user_id 83146139 -> 2 rows
      global_bot    last_seen 16:58  linked=False   <- was picked
      formula-tela  last_seen 09:28  linked=True    <- the real one
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.master_api.auth import _resolve_bot_user
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "83146139"


class _Verified:
    """Minimal stand-in for VerifiedInitData."""

    def __init__(self, user_id: str, bot_slug: str = "") -> None:
        self.user_id = user_id
        self.bot_slug = bot_slug


@pytest.fixture
def salon_tenant() -> Tenant:
    # get_or_create: the conftest and the global-bot seed migration may
    # already have put these rows in place.
    tenant, _ = Tenant.all_objects.get_or_create(
        slug="formula-tela", defaults={"name": "Формула тела"}
    )
    return tenant


@pytest.fixture
def global_tenant() -> Tenant:
    tenant, _ = Tenant.all_objects.get_or_create(slug="global_bot", defaults={"name": "Global bot"})
    return tenant


def _set_last_seen(row: BotUser, when) -> None:
    """Write `last_seen` for real. `create()`/`save()` cannot.

    DRF-1653. `last_seen` is `auto_now=True` (apps/identity/models.py:167), so
    every save overwrites whatever the caller passed — silently, with no error
    and no warning. Until this was measured, the fixture below asked for an
    eight-hour gap and got eight milliseconds:

        asked for:  28800.0 s
        stored:         0.008261 s

    `QuerySet.update()` is the one path that does not run `auto_now`, because
    it never loads or saves a model instance. Using it here is not a trick
    around the ORM — it is the only way to state the fact the test is about.
    """

    BotUser.all_tenants.filter(pk=row.pk).update(last_seen=when)
    row.refresh_from_db()


@pytest.fixture
def two_rows(salon_tenant, global_tenant) -> tuple[BotUser, BotUser]:
    """The pilot's exact shape: the stale row is the linked one.

    That sentence used to be false. See `_set_last_seen`: the gap the fixture
    appeared to create was discarded by `auto_now`, and the rows ended up
    milliseconds apart. The tests then passed for a reason nobody chose — the
    wall clock happened to tick between two inserts — and failed, once in two
    runs, whenever a warm interpreter made the second insert fast enough to
    land in the same tick. A tie in `ORDER BY last_seen DESC` lets Postgres
    return either row.
    """

    now = timezone.now()
    salon_row = BotUser.all_tenants.create(
        tenant=salon_tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
    )
    global_row = BotUser.all_tenants.create(
        tenant=global_tenant,
        channel="max",
        channel_user_id=CHANNEL_USER_ID,
    )
    _set_last_seen(salon_row, now - timedelta(hours=8))
    _set_last_seen(global_row, now)
    CatalogMaster.all_tenants.create(
        tenant=salon_tenant,
        name="Архипкин Денис",
        external_id=None,
        external_updated_at=now,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        linked_bot_user=salon_row,
    )
    return salon_row, global_row


class TestResolutionByBotTenant:
    def test_signature_bot_decides_not_recency(self, settings, two_rows):
        salon_row, _global_row = two_rows
        settings.MAX_BOT_REGISTRY = (
            BotEntry(
                slug="salon",
                webhook_secret="wh",  # pragma: allowlist secret
                api_token="tok",  # pragma: allowlist secret
                tenant_slug="formula-tela",
            ),
        )
        settings.MAX_BOT_TENANT_SLUG = ""

        resolved = _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon"))

        assert resolved == salon_row, "must pick the tenant of the bot that signed"

    def test_falls_back_to_bot_tenant_slug(self, settings, two_rows):
        # No registry: the pre-existing single-bot setting still scopes it,
        # the same way the customer and admin surfaces always have.
        salon_row, _ = two_rows
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"

        resolved = _resolve_bot_user(_Verified(CHANNEL_USER_ID))

        assert resolved == salon_row

    def test_without_any_tenant_hint_keeps_historical_behaviour(self, settings, two_rows):
        _, global_row = two_rows
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_BOT_TENANT_SLUG = ""

        resolved = _resolve_bot_user(_Verified(CHANNEL_USER_ID))

        assert resolved == global_row

    def test_unknown_bot_slug_falls_through_to_the_setting(self, settings, two_rows):
        salon_row, _ = two_rows
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"

        resolved = _resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="nope"))

        assert resolved == salon_row

    def test_no_row_in_the_bot_tenant_still_resolves_something(self, settings, global_tenant):
        # Someone linked under a different tenant must not be denied just
        # because this bot's tenant has no row for them.
        row = BotUser.all_tenants.create(
            tenant=global_tenant,
            channel="max",
            channel_user_id=CHANNEL_USER_ID,
        )
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"

        assert _resolve_bot_user(_Verified(CHANNEL_USER_ID)) == row

    def test_unknown_user_resolves_to_none(self, settings, two_rows):
        settings.MAX_BOT_TENANT_SLUG = "formula-tela"

        assert _resolve_bot_user(_Verified("does-not-exist")) is None


class TestEndToEnd:
    def test_master_endpoint_stops_answering_401(self, client, settings, two_rows):
        """The live symptom: 401 not_a_master with a correctly linked master."""

        from django.urls import reverse

        from apps.master_api.tests.conftest import init_data_header

        settings.MAX_BOT_TENANT_SLUG = "formula-tela"
        settings.MAX_BOT_REGISTRY = ()

        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header(CHANNEL_USER_ID),
        )

        assert resp.status_code == 200, resp.content
