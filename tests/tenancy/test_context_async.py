"""G2 (mandatory test gap) — ContextVar async-safety (DRF-418 / A3).

This is the **delta over Ayla** — the new code we add for async/Celery/
Redis-Streams worker context propagation. Must be ironclad. If
ContextVar leaks across `asyncio.gather` tasks or doesn't survive
``sync_to_async``, every multi-tenant async code path in Sprint 2+ has
a silent cross-tenant leak waiting to happen.

What we test:
1. ``asyncio.gather(t1, t2)`` with different tenants set inside each
   coroutine — each coroutine sees its own tenant, no bleed.
2. ``sync_to_async`` from an async coroutine into a sync function —
   the sync function sees the tenant set by the outer coroutine.
3. Setting a tenant inside an inner coroutine doesn't leak to the
   outer caller after the inner returns.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async

from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.mark.asyncio
async def test_gather_two_tenants_no_leak():
    """Two coroutines with different tenants — each sees its own."""

    t1 = await sync_to_async(Tenant.objects.create)(slug="leak-a", name="A")
    t2 = await sync_to_async(Tenant.objects.create)(slug="leak-b", name="B")

    async def work(tenant: Tenant, expected_slug: str) -> str:
        with tenant_scope(tenant):
            # Yield once to let the scheduler interleave.
            await asyncio.sleep(0)
            seen = current_tenant()
            assert seen is not None
            assert seen.slug == expected_slug
            return seen.slug

    results = await asyncio.gather(
        work(t1, "leak-a"),
        work(t2, "leak-b"),
    )

    assert sorted(results) == ["leak-a", "leak-b"]
    # Outer context is untouched.
    assert current_tenant() is None


@pytest.mark.asyncio
async def test_sync_to_async_preserves_context():
    """Tenant set in async scope is visible inside sync_to_async target."""

    t = await sync_to_async(Tenant.objects.create)(slug="bridge", name="Bridge")

    def sync_reader() -> str | None:
        tenant = current_tenant()
        return tenant.slug if tenant else None

    with tenant_scope(t):
        slug = await sync_to_async(sync_reader)()

    assert slug == "bridge"
    assert current_tenant() is None


@pytest.mark.asyncio
async def test_inner_scope_doesnt_leak_to_outer():
    """Tenant set inside an inner coroutine resets when the scope exits."""

    outer = await sync_to_async(Tenant.objects.create)(slug="outer", name="Outer")
    inner = await sync_to_async(Tenant.objects.create)(slug="inner", name="Inner")

    async def inner_work() -> None:
        with tenant_scope(inner):
            seen = current_tenant()
            assert seen is not None
            assert seen.slug == "inner"
        # Back to the caller's context after exit.

    with tenant_scope(outer):
        await inner_work()
        # Inner's scope_exit must restore us to outer, not None.
        outer_seen = current_tenant()
        assert outer_seen is not None
        assert outer_seen.slug == "outer"

    assert current_tenant() is None


@pytest.mark.asyncio
async def test_concurrent_set_doesnt_bleed():
    """asyncio.gather with overlapping tenant_scope calls — no bleed.

    Stress test: two coroutines that yield several times in the middle
    of their tenant_scope. With ContextVar this is safe because each
    coroutine has its own copy of the context. With threading.local
    this would race.
    """

    t1 = await sync_to_async(Tenant.objects.create)(slug="concur-a", name="A")
    t2 = await sync_to_async(Tenant.objects.create)(slug="concur-b", name="B")

    async def long_running(tenant: Tenant) -> list[str]:
        seen: list[str] = []
        with tenant_scope(tenant):
            for _ in range(5):
                await asyncio.sleep(0)
                current = current_tenant()
                assert current is not None
                seen.append(current.slug)
        return seen

    results = await asyncio.gather(long_running(t1), long_running(t2))

    # Every reading inside coroutine 1 saw t1; every reading inside coroutine 2 saw t2.
    assert results[0] == ["concur-a"] * 5
    assert results[1] == ["concur-b"] * 5
