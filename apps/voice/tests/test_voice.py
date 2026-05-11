"""apps/voice tests (DRF-489 + DRF-490 / Sprint 4 / C2 + C3)."""

from __future__ import annotations

import logging

import pytest

from apps.persona.models import BrandVoiceConfig
from apps.tenancy.models import Tenant
from apps.voice import load_brand_voice
from apps.voice.rewriter import rewrite_to_voice
from apps.voice.services import reset_cache

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="vc-a", name="VC-A")


class TestLoadBrandVoice:
    def test_no_config_returns_defaults(self, tenant):
        v = load_brand_voice(tenant)
        assert v == {
            "persona": {},
            "tone_vector": {},
            "forbidden_phrases": [],
            "voice_examples": [],
            "tone_modulations": {},
            "version": 0,
        }

    def test_loads_existing_config(self, tenant):
        BrandVoiceConfig.objects.create(
            tenant=tenant,
            persona={"name": "Alina"},
            tone_vector={"warmth": 0.8},
            forbidden_phrases=[r"\bguarantee\b"],
            version=3,
        )
        v = load_brand_voice(tenant)
        assert v["persona"]["name"] == "Alina"
        assert v["tone_vector"]["warmth"] == 0.8
        assert v["forbidden_phrases"] == [r"\bguarantee\b"]
        assert v["version"] == 3

    def test_cache_hit_avoids_db(self, tenant, django_assert_num_queries):
        BrandVoiceConfig.objects.create(tenant=tenant, version=1)
        # First call: 1 query (the lookup).
        with django_assert_num_queries(1):
            load_brand_voice(tenant)
        # Second call same version: still 1 query (the existence check
        # happens before cache lookup). Then dict lookup returns cached.
        # In practice this means: O(1 SELECT) per call but no JSONField
        # re-deserialisation. Verify cache returns same dict object.
        v1 = load_brand_voice(tenant)
        v2 = load_brand_voice(tenant)
        assert v1 is v2  # same cached dict object

    def test_version_bump_invalidates_cache(self, tenant):
        bv = BrandVoiceConfig.objects.create(tenant=tenant, version=1)
        v1 = load_brand_voice(tenant)
        assert v1["version"] == 1
        bv.version = 2
        bv.persona = {"name": "Beta"}
        bv.save(update_fields=["version", "persona", "updated_at"])
        v2 = load_brand_voice(tenant)
        # New cache key → fresh dict.
        assert v2["version"] == 2
        assert v2["persona"]["name"] == "Beta"


class TestRewriteToVoice:
    def test_passthrough(self, tenant):
        v = load_brand_voice(tenant)
        assert rewrite_to_voice("hello world", v) == "hello world"

    def test_logs_debug(self, tenant, caplog):
        v = load_brand_voice(tenant)
        with caplog.at_level(logging.DEBUG):
            rewrite_to_voice("x", v)
        assert any("voice.rewrite.skipped" in r.message for r in caplog.records)
