"""Tests for the ``global_bot`` sentinel tenant helper (#1019 / EPIC #1014)."""

from __future__ import annotations

import pytest

from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.services.global_tenant import get_global_bot_tenant

pytestmark = pytest.mark.django_db


def test_get_global_bot_tenant_returns_seeded_system_tenant() -> None:
    """Seed migration 0014 provisions it; the helper resolves that row."""
    tenant = get_global_bot_tenant()
    assert tenant.slug == GLOBAL_BOT_TENANT_SLUG
    assert tenant.is_system is True
    assert tenant.name == "Global Bot Identity"


def test_get_global_bot_tenant_idempotent() -> None:
    from apps.tenancy.models import Tenant

    a = get_global_bot_tenant()
    b = get_global_bot_tenant()
    assert a.pk == b.pk
    assert Tenant.all_objects.filter(slug=GLOBAL_BOT_TENANT_SLUG).count() == 1


def test_global_bot_tenant_distinct_from_global_kb() -> None:
    """Identity sentinel must not be conflated with the KB corpus tenant."""
    from apps.kb.constants import GLOBAL_KB_TENANT_SLUG

    assert GLOBAL_BOT_TENANT_SLUG != GLOBAL_KB_TENANT_SLUG
