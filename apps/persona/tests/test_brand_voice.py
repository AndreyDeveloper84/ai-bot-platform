"""BrandVoiceConfig structured field tests (DRF-488 / Sprint 4 / C1)."""

from __future__ import annotations

import pytest

from apps.persona.models import BrandVoiceConfig
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="bv-a", name="BV-A")


class TestStructuredFields:
    def test_defaults(self, tenant):
        bv = BrandVoiceConfig.objects.create(tenant=tenant)
        bv.refresh_from_db()
        assert bv.persona == {}
        assert bv.tone_vector == {}
        assert bv.forbidden_phrases == []
        assert bv.tone_modulations == {}
        assert bv.version == 1

    def test_round_trip_persona(self, tenant):
        bv = BrandVoiceConfig.objects.create(
            tenant=tenant,
            persona={
                "name": "Alina",
                "role": "consultant",
                "age": 32,
                "traits": ["warm", "professional"],
            },
        )
        bv.refresh_from_db()
        assert bv.persona["name"] == "Alina"
        assert bv.persona["traits"] == ["warm", "professional"]

    def test_round_trip_tone_vector(self, tenant):
        bv = BrandVoiceConfig.objects.create(
            tenant=tenant,
            tone_vector={"warmth": 0.8, "formality": 0.5, "energy": 0.6},
        )
        bv.refresh_from_db()
        assert bv.tone_vector["warmth"] == 0.8
        assert bv.tone_vector["formality"] == 0.5

    def test_round_trip_forbidden_phrases(self, tenant):
        bv = BrandVoiceConfig.objects.create(
            tenant=tenant,
            forbidden_phrases=[r"\bguarantee\b", r"100%\s+success"],
        )
        bv.refresh_from_db()
        assert len(bv.forbidden_phrases) == 2
        assert r"\bguarantee\b" in bv.forbidden_phrases

    def test_round_trip_tone_modulations(self, tenant):
        bv = BrandVoiceConfig.objects.create(
            tenant=tenant,
            tone_modulations={
                "risk_level=high": {"formality": 0.9, "warmth": 0.4},
                "sentiment=frustrated": {"warmth": 0.95},
            },
        )
        bv.refresh_from_db()
        assert bv.tone_modulations["risk_level=high"]["formality"] == 0.9

    def test_version_bump_on_save(self, tenant):
        bv = BrandVoiceConfig.objects.create(tenant=tenant, version=1)
        bv.version = 2
        bv.save(update_fields=["version", "updated_at"])
        bv.refresh_from_db()
        assert bv.version == 2

    def test_one_to_one_tenant(self, tenant):
        BrandVoiceConfig.objects.create(tenant=tenant)
        # OneToOne — second create raises IntegrityError.
        from django.db.utils import IntegrityError

        with pytest.raises(IntegrityError):
            BrandVoiceConfig.objects.create(tenant=tenant)
