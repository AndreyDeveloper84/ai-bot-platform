"""DRF-1325 — the preference has to survive the tenant boundary.

The person says «завтра вечером» to the GLOBAL bot, whose conversation is
parked under the ``global_bot`` sentinel. The flow that has to honour it runs
inside ``tenant_scope(T)`` against a different ``Conversation`` row entirely.
Between those two lies the handoff — and a preference that dies there is a
smaller copy of the very defect the ticket is about: heard, then lost, in
silence.

This file exists because that seam broke once during the work and broke
quietly: reading the source conversation through ``resolve_active_conversation``
looks right, compiles, type-checks, and returns ``None`` every time, because
that resolver searches inside the tenant scope the caller has just entered.
Every test above it stayed green and the whole feature degraded to the
no-preference path. A unit test on the parser would never have caught it.
"""

from __future__ import annotations

import pytest

from apps.conversations.models import Conversation
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.handoff import carry_time_preference
from apps.orchestrator.time_preference import (
    PART_EVENING,
    TimePreference,
    load_time_preference,
    save_time_preference,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def global_bot_user():  # noqa: ANN201
    get_global_bot_tenant()
    return resolve_or_create_global_bot_user(
        channel="max", channel_user_id="u-1325", chat_id="u-1325"
    )


@pytest.fixture
def tenant_conversation() -> Conversation:
    tenant = Tenant.objects.create(slug="handoff-tz", name="Handoff TZ")
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="u-1325", chat_id="u-1325"
    )
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


def test_the_preference_crosses_into_tenant_scope(
    global_bot_user, tenant_conversation: Conversation
) -> None:
    source = resolve_active_global_conversation(global_bot_user)
    save_time_preference(
        source, TimePreference(day_offset=1, part=PART_EVENING, said="завтра вечером")
    )

    carry_time_preference(global_bot_user, tenant_conversation)

    landed = load_time_preference(tenant_conversation)
    assert landed is not None, "the preference did not survive the handoff"
    assert landed.day_offset == 1
    assert landed.part == PART_EVENING


def test_nothing_said_leaves_the_tenant_conversation_alone(
    global_bot_user, tenant_conversation: Conversation
) -> None:
    resolve_active_global_conversation(global_bot_user)
    carry_time_preference(global_bot_user, tenant_conversation)
    assert load_time_preference(tenant_conversation) is None


def test_reading_a_hint_never_creates_a_conversation(
    global_bot_user, tenant_conversation: Conversation
) -> None:
    """A best-effort read on the booking path must have no side effects.

    ``resolve_active_global_conversation`` creates by default; this call has
    to pass ``create_if_missing=False`` or every ``cb:book:*`` tap would
    silently open a global conversation for people who never had one.
    """
    before = Conversation.all_tenants.count()
    carry_time_preference(global_bot_user, tenant_conversation)
    assert Conversation.all_tenants.count() == before


def test_a_missing_conversation_is_not_an_error(global_bot_user) -> None:
    carry_time_preference(global_bot_user, None)
