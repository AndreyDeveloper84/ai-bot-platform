"""Tests for ``resolve_or_create_global_bot_user`` (#1019 / EPIC #1014).

The global resolver must work at ``current_tenant()=None`` (discovery) without
entering a tenant scope, parking the BotUser under the ``global_bot`` sentinel
to satisfy ``unique_together`` — all while STRICT_TENANT_SCOPE is "strict"
(conftest default), proving it never trips the scoped manager.
"""

from __future__ import annotations

import uuid

import pytest

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_global_bot_user
from apps.tenancy.context import current_tenant

pytestmark = pytest.mark.django_db


def test_resolves_under_sentinel_without_entering_scope() -> None:
    assert current_tenant() is None  # no scope entered (strict mode)
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="900900", chat_id="900900"
    )
    assert bot_user.tenant.slug == GLOBAL_BOT_TENANT_SLUG
    assert bot_user.tenant.is_system is True
    # The resolver must NOT have leaked a tenant scope.
    assert current_tenant() is None


def test_idempotent_no_duplicate() -> None:
    a = resolve_or_create_global_bot_user(channel="max", channel_user_id="901")
    b = resolve_or_create_global_bot_user(channel="max", channel_user_id="901")
    assert a.id == b.id
    assert BotUser.all_tenants.filter(channel="max", channel_user_id="901").count() == 1


def test_ayla_user_id_set_on_create() -> None:
    uid = uuid.uuid4()
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="902", ayla_user_id=uid
    )
    assert bot_user.ayla_user_id == uid


def test_ayla_user_id_blank_filled_then_never_overwritten() -> None:
    # First resolve has no ayla_user_id.
    first = resolve_or_create_global_bot_user(channel="max", channel_user_id="903")
    assert first.ayla_user_id is None

    # Second resolve carries one → blank-filled.
    uid = uuid.uuid4()
    filled = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="903", ayla_user_id=uid
    )
    filled.refresh_from_db()
    assert filled.ayla_user_id == uid

    # Third resolve with a DIFFERENT value must NOT overwrite the known one.
    other = uuid.uuid4()
    again = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="903", ayla_user_id=other
    )
    again.refresh_from_db()
    assert again.ayla_user_id == uid


def test_legacy_resolver_untouched_still_requires_tenant() -> None:
    """The per-tenant resolver must still fail loud at current_tenant()=None."""
    from apps.identity.services import resolve_or_create_bot_user

    with pytest.raises(ValueError):
        resolve_or_create_bot_user(channel="max", channel_user_id="904")
