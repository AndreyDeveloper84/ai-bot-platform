"""consent_required decorator tests (DRF-458 / Sprint 3 / A3)."""

from __future__ import annotations

import pytest

from apps.consent.decorators import consent_required
from apps.consent.exceptions import ConsentDenied
from apps.consent.services import grant
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="dec-a", name="A")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="dec-a-bot")


@consent_required("photo_biometric")
def _upload_photo(bot_user: BotUser, *, image_url: str) -> str:
    """Mock guarded tool — returns the URL on success."""

    return image_url


@consent_required("photo_biometric")
def _upload_kw_only(*, bot_user: BotUser, image_url: str) -> str:
    """Variant used to exercise the kwargs extraction path."""

    return image_url


class TestConsentRequired:
    def test_runs_when_consent_granted(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            grant(bot_user, consent_type="photo_biometric", source="opt-in")
            result = _upload_photo(bot_user, image_url="https://x/y.jpg")
        assert result == "https://x/y.jpg"

    def test_raises_when_consent_missing(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            with pytest.raises(ConsentDenied) as exc_info:
                _upload_photo(bot_user, image_url="https://x/y.jpg")
        assert exc_info.value.consent_type == "photo_biometric"
        assert exc_info.value.bot_user_id == str(bot_user.id)

    def test_emits_safety_triggered_on_denial(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            with pytest.raises(ConsentDenied):
                _upload_photo(bot_user, image_url="https://x/y.jpg")
        ev = Event.objects.filter(tenant=tenant, event_type="safety_triggered").first()
        assert ev is not None
        assert ev.payload["reason"] == "consent_denied"
        assert ev.payload["consent_type"] == "photo_biometric"
        assert ev.payload["bot_user_id"] == str(bot_user.id)
        assert ev.payload["tool"].endswith("_upload_photo")

    def test_does_not_emit_when_consent_granted(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            grant(bot_user, consent_type="photo_biometric", source="opt-in")
            _upload_photo(bot_user, image_url="https://x/y.jpg")
        assert not Event.objects.filter(tenant=tenant, event_type="safety_triggered").exists()

    def test_resolves_bot_user_from_kwargs(self, tenant, bot_user, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            grant(bot_user, consent_type="photo_biometric", source="opt-in")
            assert (
                _upload_kw_only(bot_user=bot_user, image_url="https://x/y.jpg") == "https://x/y.jpg"
            )

    def test_typeerror_when_wrong_signature(self, tenant, settings):
        """Decorator applied to a function without BotUser arg blows up loudly."""

        settings.STRICT_TENANT_SCOPE = "strict"

        @consent_required("photo_biometric")
        def _bad(value: int) -> int:
            return value

        with tenant_scope(tenant):
            with pytest.raises(TypeError, match="expected a BotUser"):
                _bad(42)
