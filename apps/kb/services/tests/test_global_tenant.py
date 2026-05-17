"""Tests for the ``get_global_kb_tenant`` helper (KB-RAG Sub-2 / GH #115).

Sub-2 extracts the lookup that Sub-3 (PR #122) inlined into
``apps.kb.services.retriever`` into a shared, public helper backed by
:data:`apps.kb.constants.GLOBAL_KB_TENANT_SLUG`. Sub-5 (seed command)
will reuse it to resolve the default target tenant.

Contracts pinned here:

    1. Returns the ``Tenant`` row when one exists with the configured slug.
    2. Returns ``None`` when no such row exists (graceful — caller decides
       what "no shared corpus available" means).
    3. Lookup uses ``Tenant.all_objects`` (not ``Tenant.objects``) so a
       deactivated service tenant is still discoverable for forensics /
       resurrection paths.
    4. Result is cached via ``functools.lru_cache`` — second call returns
       the same instance without hitting the DB. The test forces a cache
       clear before each scenario so prior-test state doesn't leak.
"""

from __future__ import annotations

import pytest

from apps.kb.constants import GLOBAL_KB_TENANT_SLUG
from apps.kb.services.global_tenant import get_global_kb_tenant
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the lru_cache between tests — otherwise the first test's
    None / Tenant result is held forever."""
    get_global_kb_tenant.cache_clear()
    yield
    get_global_kb_tenant.cache_clear()


class TestGetGlobalKbTenant:
    def test_returns_tenant_when_exists(self):
        provisioned = Tenant.objects.create(
            slug=GLOBAL_KB_TENANT_SLUG,
            name="Global Knowledge Base",
            is_system=True,
        )
        resolved = get_global_kb_tenant()
        assert resolved is not None
        assert resolved.id == provisioned.id
        assert resolved.slug == GLOBAL_KB_TENANT_SLUG

    def test_returns_none_when_missing(self):
        # No row provisioned — caller treats this as "no global fallback".
        resolved = get_global_kb_tenant()
        assert resolved is None

    def test_lookup_uses_all_objects_so_inactive_is_visible(self):
        # A deactivated system tenant should still be discoverable — the
        # admin guard only blocks delete, not deactivation; we want the
        # helper to surface the row regardless so operators can re-enable
        # it explicitly.
        Tenant.objects.create(
            slug=GLOBAL_KB_TENANT_SLUG,
            name="Global Knowledge Base",
            is_system=True,
            is_active=False,
        )
        resolved = get_global_kb_tenant()
        assert resolved is not None
        assert resolved.is_active is False

    def test_result_is_cached(self):
        Tenant.objects.create(
            slug=GLOBAL_KB_TENANT_SLUG,
            name="Global KB",
            is_system=True,
        )
        first = get_global_kb_tenant()
        second = get_global_kb_tenant()
        # Same Python instance — lru_cache returned the cached value, no
        # second DB hit. Useful invariant: callers can keep calling the
        # helper without worrying about query amplification.
        assert first is second

    def test_does_not_require_is_system_flag(self):
        # Sub-2 (Sub-1's is_system field exists in dev now) but the helper
        # must not filter on it — Sub-1 / Sub-2 / Sub-3 were independent
        # parallel PRs. The slug is the contract; ``is_system`` is admin
        # safety, not the lookup key.
        Tenant.objects.create(
            slug=GLOBAL_KB_TENANT_SLUG,
            name="Misconfigured",
            is_system=False,
        )
        resolved = get_global_kb_tenant()
        assert resolved is not None
        assert resolved.slug == GLOBAL_KB_TENANT_SLUG
