"""Sprint 4 / G1 — Live-reload exit gate (DRF-494).

Exercises the F0.5 design exit gate:

    Edit prompt in admin → bot uses new prompt within 5 sec without restart.

Concretely: publish a PromptVersion → Redis pub/sub broadcast →
subscriber in another "process" (simulated via direct cache calls)
evicts → next `get_prompt` returns the new body.

The test uses a poll-with-timeout (max 5s) to align with the design
SLA. Locally with the in-process pub/sub it completes in ~10ms; the
generous bound protects CI from flaky Redis latency.

Marker: `@pytest.mark.e2e` — auto-skip when Redis unreachable.
"""

from __future__ import annotations

import time
import uuid

import pytest
import redis as redis_lib
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model

from apps.promptreg import cache as cache_mod
from apps.promptreg.models import PromptVersion
from apps.promptreg.registry import get_prompt
from apps.promptreg.services import publish_prompt
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


def _redis_reachable() -> bool:
    try:
        client = redis_lib.Redis.from_url(
            getattr(django_settings, "REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        client.close()
        return True
    except (redis_lib.ConnectionError, OSError, redis_lib.TimeoutError):
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _redis_reachable(),
        reason="Redis not reachable on REDIS_URL — start `docker compose up` or rely on CI's redis service.",
    ),
    pytest.mark.django_db(transaction=True),
]


User = get_user_model()


@pytest.fixture(autouse=True)
def _clean_cache():
    cache_mod.invalidate_all()
    yield
    cache_mod.invalidate_all()
    cache_mod.stop_subscriber()


def test_publish_invalidates_cache_within_sla(settings):
    """Live-reload SLA: ≤5s from publish to cache eviction."""

    settings.STRICT_TENANT_SCOPE = "strict"
    # Subscriber thread MUST be live for this test — it's the production
    # invalidation path.
    settings.PROMPTREG_DISABLE_SUBSCRIBER = False

    tenant = Tenant.objects.create(slug=f"lr-{uuid.uuid4().hex[:8]}", name="LR")
    author = User.objects.create_user(username=f"lr-author-{uuid.uuid4().hex[:8]}")

    # Set up: create v1 and v2, publish v1.
    v1 = PromptVersion.all_tenants.create(
        tenant=tenant,
        skill_name="faq",
        body="v1-body",
        version=1,
        created_by=author,
    )
    v2 = PromptVersion.all_tenants.create(
        tenant=tenant,
        skill_name="faq",
        body="v2-body",
        version=2,
        created_by=author,
    )
    with tenant_scope(tenant):
        publish_prompt(v1, user=author)
        # Prime the cache.
        assert get_prompt(tenant, "faq").body == "v1-body"

    # Give the subscriber thread a moment to attach (lazy start fired
    # on first get_prompt above).
    time.sleep(0.3)

    # Publish v2 — this fires Redis publish on commit.
    with tenant_scope(tenant):
        publish_prompt(v2, user=author)

    # Poll up to 5s for the cache to reflect v2.
    deadline = time.time() + 5.0
    last_body = "v1-body"
    while time.time() < deadline:
        with tenant_scope(tenant):
            last_body = get_prompt(tenant, "faq").body
        if last_body == "v2-body":
            break
        time.sleep(0.1)

    assert last_body == "v2-body", f"cache still serving stale body after 5s: {last_body!r}"

    # And the DB state is consistent: v1 inactive, v2 active.
    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v1.is_active is False
    assert v2.is_active is True
