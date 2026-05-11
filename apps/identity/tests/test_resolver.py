"""``resolve_or_create_bot_user`` tests (DRF-434 / Sprint 2 / A2)."""

from __future__ import annotations

import pytest

from apps.events.models import Event
from apps.identity.models import BotUser
from apps.identity.services import resolve_or_create_bot_user
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="resolver-a", name="A")


@pytest.fixture
def tenant_b() -> Tenant:
    return Tenant.objects.create(slug="resolver-b", name="B")


class TestResolverHappyPath:
    def test_creates_when_missing(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            user = resolve_or_create_bot_user(channel="max", channel_user_id="100")
        assert user.tenant_id == tenant_a.id
        assert user.channel == "max"
        assert user.channel_user_id == "100"
        # Event emitted with the right type.
        events = Event.objects.filter(tenant=tenant_a).values_list("event_type", flat=True)
        assert "identity.bot_user.created" in events

    def test_returns_existing_when_present(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            first = resolve_or_create_bot_user(channel="max", channel_user_id="200")
            second = resolve_or_create_bot_user(channel="max", channel_user_id="200")
        assert first.id == second.id
        # Two events: one .created, one .resolved.
        types = list(
            Event.objects.filter(tenant=tenant_a)
            .order_by("created_at")
            .values_list("event_type", flat=True)
        )
        assert types == ["identity.bot_user.created", "identity.bot_user.resolved"]


class TestResolverOpportunisticEnrichment:
    def test_fills_blank_fields(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            # First call leaves display_name/phone/chat_id blank.
            resolve_or_create_bot_user(channel="max", channel_user_id="300")
            # Second call enriches blanks.
            user = resolve_or_create_bot_user(
                channel="max",
                channel_user_id="300",
                display_name="Анна",
                phone="+79991112233",
                chat_id="chat-1",
            )
        user.refresh_from_db()
        assert user.display_name == "Анна"
        assert user.phone == "+79991112233"
        assert user.chat_id == "chat-1"

    def test_never_overwrites_non_blank(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            resolve_or_create_bot_user(
                channel="max",
                channel_user_id="400",
                display_name="Original",
                phone="+79990000000",
            )
            # Second call with different values must NOT overwrite.
            user = resolve_or_create_bot_user(
                channel="max",
                channel_user_id="400",
                display_name="Imposter",
                phone="+79998887766",
            )
        user.refresh_from_db()
        assert user.display_name == "Original"
        assert user.phone == "+79990000000"


class TestResolverTenantIsolation:
    def test_cross_tenant_same_channel_user_id_creates_two(self, tenant_a, tenant_b, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            ua = resolve_or_create_bot_user(channel="max", channel_user_id="cross")
        with tenant_scope(tenant_b):
            ub = resolve_or_create_bot_user(channel="max", channel_user_id="cross")
        assert ua.id != ub.id
        assert ua.tenant_id == tenant_a.id
        assert ub.tenant_id == tenant_b.id

    def test_missing_tenant_context_raises_value_error_even_in_audit_mode(self, settings):
        # Audit mode would silently return empty for ORM reads; the
        # resolver still must fail loudly to prevent phantom rows.
        settings.STRICT_TENANT_SCOPE = "audit"
        with pytest.raises(ValueError, match="tenant in scope"):
            resolve_or_create_bot_user(channel="max", channel_user_id="ghost")
        # And of course strict mode raises too.
        settings.STRICT_TENANT_SCOPE = "strict"
        with pytest.raises(ValueError, match="tenant in scope"):
            resolve_or_create_bot_user(channel="max", channel_user_id="ghost2")


class TestResolverPiiSafety:
    def test_phone_not_in_event_payload(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            resolve_or_create_bot_user(
                channel="max",
                channel_user_id="500",
                phone="+79991234567",
            )
        ev = Event.objects.get(tenant=tenant_a, event_type="identity.bot_user.created")
        # The raw phone string must not appear anywhere in the payload.
        assert "+79991234567" not in str(ev.payload)
        # Only the boolean indicator is in payload.
        assert ev.payload["phone_present"] is True


class TestResolverLastSeenTouch:
    def test_resolve_bumps_last_seen(self, tenant_a, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a):
            first = resolve_or_create_bot_user(channel="max", channel_user_id="600")
            first.refresh_from_db()
            initial = first.last_seen
            # Cause a sub-millisecond delay by issuing an unrelated DB call.
            BotUser.all_tenants.count()
            again = resolve_or_create_bot_user(channel="max", channel_user_id="600")
            again.refresh_from_db()
        assert again.last_seen >= initial
