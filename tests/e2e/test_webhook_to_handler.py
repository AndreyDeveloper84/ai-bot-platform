"""G1 (mandatory test gap) — end-to-end webhook → handler (DRF-432).

Exercises the full Sprint 1 ingress pipeline against real Redis:

    record_webhook()                 # apps.ingress.services
        ↓ (WebhookJournal row + ingress.webhook_received event)
    enqueue("max", payload)          # apps.ingress.streams
        ↓ (XADD on ingress:max + ingress.enqueued event)
    consume_once(["ingress:max"])    # apps.workers.consumer
        ↓ (XREADGROUP → TenantAwareTask → XACK)
    handler.handle(payload)          # registered handler ran with
                                     # tenant + trace context set

Run conditions:
- CI: postgres + redis services up in workflow → test runs.
- Local with ``docker compose up`` → test runs.
- Bare laptop without backing services → test SKIPPED (no Redis on 6379).

Marker: ``@pytest.mark.e2e`` — `pytest -m e2e` runs only e2e, `-m "not e2e"`
excludes them. CI runs everything; G1 stays in the matrix.
"""

from __future__ import annotations

import uuid

import pytest
import redis as redis_lib
from django.conf import settings

from apps.audit.models import AuditLog
from apps.events.models import Event
from apps.ingress import streams as ingress_streams
from apps.ingress.models import WebhookJournal
from apps.ingress.services import record_webhook
from apps.tenancy.context import current_tenant, current_trace_id, trace_id_scope
from apps.tenancy.models import Tenant
from apps.workers import consumer
from apps.workers.base import TenantAwareTask
from apps.workers.registry import clear_registry, register


def _redis_reachable() -> bool:
    """True if a Redis instance is reachable on settings.REDIS_URL."""

    try:
        client = redis_lib.Redis.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        client.close()
        return True
    except (redis_lib.ConnectionError, OSError, redis_lib.TimeoutError):
        return False


# Skip the whole module when Redis isn't reachable. CI provides it via
# the workflow's redis service; local dev needs `docker compose up` from
# the project root.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _redis_reachable(),
        reason="Redis not reachable on REDIS_URL — start `docker compose up` "
        "or rely on CI's redis service.",
    ),
    # Real Redis test, real Postgres test → outer transaction must be
    # disabled so the consumer thread (and downstream emits) see the
    # writes from the test thread.
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
def isolated_stream(monkeypatch):
    """Use a unique stream prefix per test so parallel runs don't collide.

    Without this, two test runs hitting the same Redis would interleave
    their entries on ``ingress:max`` and break the assertion counts.
    """

    prefix = f"e2etest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        ingress_streams,
        "_stream_prefix",
        lambda: prefix,
    )
    yield prefix
    # Cleanup: best-effort delete of the streams we created.
    client = ingress_streams._client()
    try:
        for ch in ("max", "telegram"):
            client.delete(f"{prefix}:{ch}")
    except Exception:  # noqa: BLE001 — cleanup is best-effort
        pass


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def _clear_client_cache():
    """Bust the lru_cache so a fresh Redis connection picks up settings."""

    ingress_streams._client.cache_clear()
    yield
    ingress_streams._client.cache_clear()


class _CaptureHandler(TenantAwareTask):
    """Test handler — records what it saw inside the tenant/trace scope."""

    received: list[dict] = []
    tenants_seen: list[object] = []
    traces_seen: list[str | None] = []

    def handle(self, payload):
        type(self).received.append(payload)
        type(self).tenants_seen.append(current_tenant())
        type(self).traces_seen.append(current_trace_id())


@pytest.fixture
def capture_handler():
    """Fresh handler with clean class-level capture lists per test."""

    _CaptureHandler.received = []
    _CaptureHandler.tenants_seen = []
    _CaptureHandler.traces_seen = []
    yield _CaptureHandler


# ---------------------------------------------------------------------------
# End-to-end happy path
# ---------------------------------------------------------------------------


def test_webhook_to_handler_e2e(
    settings,
    isolated_stream,
    _clear_client_cache,
    capture_handler,
):
    """One webhook → journal + XADD + consumer + handler with full context."""

    t = Tenant.objects.create(slug="e2e-formula", name="E2E Formula")
    settings.CHANNEL_TOKEN_TO_TENANT_SLUG = "e2e-token=e2e-formula"

    stream = f"{isolated_stream}:max"
    register(stream)(capture_handler)

    # 1. Webhook arrives → journal + ingress event + maybe audit log.
    with trace_id_scope("trace-e2e-1"):
        journal_row, created = record_webhook(
            channel="max",
            external_event_id="evt-e2e-1",
            raw_payload={"text": "hello from e2e"},
            channel_token="e2e-token",
        )
    assert created is True
    assert journal_row.resolved_tenant_id == t.id
    assert journal_row.trace_id == "trace-e2e-1"

    # 2. Enqueue the payload onto the platform's Redis stream.
    # tenant_id is promoted to a top-level entry field so the consumer's
    # TenantAwareTask can restore tenant context without parsing JSON.
    with trace_id_scope("trace-e2e-1"):
        entry_id = ingress_streams.enqueue(
            "max",
            {
                "journal_id": str(journal_row.id),
                "data": journal_row.raw_payload,
            },
            tenant_id=str(t.id),
        )
    assert entry_id  # Redis XADD returned a real entry id

    # 3. Consumer pops the entry and dispatches the handler.
    processed = consumer.consume_once(streams=[stream], block_ms=1000, count=10)
    assert processed == 1

    # 4. Handler ran with correct tenant + trace context.
    assert len(capture_handler.received) == 1
    payload = capture_handler.received[0]
    assert payload["data"] == {"text": "hello from e2e"}
    assert payload["journal_id"] == str(journal_row.id)

    # tenant + trace were set during the handler's execution.
    seen_tenant = capture_handler.tenants_seen[0]
    assert seen_tenant is not None
    assert seen_tenant.id == t.id
    assert capture_handler.traces_seen[0] == "trace-e2e-1"

    # 5. Telemetry contract: events emitted at each stage, audit clean.
    event_types = list(
        Event.objects.filter(trace_id="trace-e2e-1").values_list("event_type", flat=True)
    )
    # Every stage must have emitted at least one event with this trace_id.
    assert "ingress.webhook_received" in event_types
    assert "ingress.enqueued" in event_types
    assert "worker.consumed" in event_types
    assert "worker.handler_started" in event_types
    assert "worker.handler_completed" in event_types
    # No failure event.
    assert "worker.handler_failed" not in event_types

    # No audit log for unknown tenant (we resolved one).
    assert not AuditLog.all_tenants.filter(action="ingress.webhook_unknown_tenant").exists()


# ---------------------------------------------------------------------------
# Dedup at the journal layer
# ---------------------------------------------------------------------------


def test_duplicate_webhook_is_skipped_at_journal(
    settings,
    isolated_stream,
    _clear_client_cache,
):
    """A second webhook with the same (channel, event_id) does NOT create
    a second journal row. The caller still gets the journal row back.
    """

    Tenant.objects.create(slug="e2e-dup", name="Dup")
    settings.CHANNEL_TOKEN_TO_TENANT_SLUG = "tok-dup=e2e-dup"

    record_webhook(
        channel="max",
        external_event_id="evt-dup",
        raw_payload={"first": True},
        channel_token="tok-dup",
    )
    row2, created2 = record_webhook(
        channel="max",
        external_event_id="evt-dup",
        raw_payload={"second": True},  # different payload — channel says "retry"
        channel_token="tok-dup",
    )

    assert created2 is False
    assert WebhookJournal.objects.filter(external_event_id="evt-dup").count() == 1
    # First payload wins — the journal preserves the original.
    assert row2.raw_payload == {"first": True}

    # ingress.webhook_duplicate event was emitted for the retry.
    assert Event.objects.filter(event_type="ingress.webhook_duplicate").count() == 1


# ---------------------------------------------------------------------------
# Failure path: handler exception → no XACK, stays in PEL
# ---------------------------------------------------------------------------


class _FailingHandler(TenantAwareTask):
    def handle(self, payload):
        raise RuntimeError("intentional e2e fail")


def test_handler_failure_no_xack(
    settings,
    isolated_stream,
    _clear_client_cache,
):
    """Handler raises → consumer does NOT XACK; entry stays in PEL."""

    t = Tenant.objects.create(slug="e2e-fail", name="Fail")
    settings.CHANNEL_TOKEN_TO_TENANT_SLUG = "tok-fail=e2e-fail"

    stream = f"{isolated_stream}:max"
    register(stream)(_FailingHandler)

    ingress_streams.enqueue(
        "max",
        {"x": 1},
        tenant_id=str(t.id),
    )
    processed = consumer.consume_once(streams=[stream], block_ms=1000, count=10)
    assert processed == 0  # nothing was XACK'd

    # PEL retains the entry. We can read it back via xpending.
    client = ingress_streams._client()
    summary = client.xpending(stream, "consumers")
    # xpending returns dict-like with pending count. For redis-py this
    # is a dict {pending: int, min: ..., max: ..., consumers: [...]}.
    if isinstance(summary, dict):
        pending_count = summary.get("pending", 0)
    else:
        pending_count = summary[0] if summary else 0
    assert pending_count >= 1

    # worker.handler_failed event emitted.
    assert Event.objects.filter(event_type="worker.handler_failed").exists()
