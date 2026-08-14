"""Inbound event routing after identity resolution (DRF-1035, §15 row 13 / AC-10).

All five inbound consumers find their addressee by
``BotUser.…filter(ayla_user_id=<envelope user_id>)``. While that field had no
writer, every one of them dead-ended on the «Ayla-mobile-only user, no channel
projection to sync» branch: reminders, payment and review events could not be
routed back to the person who caused them.

This locks the round trip that DRF-1035 restores:

    resolved subject → BotUser.ayla_user_id persisted
        → inbound event keyed by that id → correct BotUser found
"""

from __future__ import annotations

import uuid

import pytest

from apps.eventbus.consumers.booking import _resolve_bot_user
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.ayla_link import ensure_ayla_link
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def resolved_uuid(monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    uid = uuid.uuid4()
    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity",
        lambda external_user_id: ResolvedIdentity(ayla_user_id=uid, is_proxy=True),
        raising=True,
    )
    return uid


def test_unlinked_user_is_unroutable_before_resolution(settings) -> None:
    """The pre-DRF-1035 state, kept as the contrast case."""
    settings.STRICT_TENANT_SCOPE = "strict"
    tenant = Tenant.objects.create(slug="drf1035-eb-0", name="EB 0")
    BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="7001")

    assert _resolve_bot_user(user_id=uuid.uuid4(), tenant=tenant) is None


def test_event_finds_bot_user_after_resolution(settings, resolved_uuid: uuid.UUID) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    tenant = Tenant.objects.create(slug="drf1035-eb-1", name="EB 1")
    shell = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="7002", chat_id="7002"
    )

    ensure_ayla_link(shell, trigger="booking")

    found = _resolve_bot_user(user_id=resolved_uuid, tenant=tenant)
    assert found is not None
    assert found.id == shell.id


def test_fan_out_makes_the_person_routable_in_every_tenant(
    settings, resolved_uuid: uuid.UUID
) -> None:
    """The reason the write fans out across shells.

    A person resolved on the global (discovery) path must still be findable
    when an event arrives scoped to the pilot tenant — otherwise reminders for
    a booking made through discovery would never reach them.
    """
    settings.STRICT_TENANT_SCOPE = "strict"
    pilot = Tenant.objects.create(slug="drf1035-eb-2", name="EB 2")
    global_shell = resolve_or_create_global_bot_user(channel="max", channel_user_id="7003")
    pilot_shell = BotUser.all_tenants.create(
        tenant=pilot, channel="max", channel_user_id="7003", chat_id="7003"
    )

    # Resolution happens on the GLOBAL shell (the discovery path)...
    ensure_ayla_link(global_shell, trigger="booking")

    # ...and the tenant-scoped event still finds the person.
    found = _resolve_bot_user(user_id=resolved_uuid, tenant=pilot)
    assert found is not None
    assert found.id == pilot_shell.id
