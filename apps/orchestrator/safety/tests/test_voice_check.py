"""validate_voice tests (DRF-491 / Sprint 4 / C4)."""

from __future__ import annotations

import pytest

from apps.events.models import Event
from apps.orchestrator.safety.voice_check import (
    reset_cache,
    validate_voice,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="vc-c-a", name="VC-C-A")


class TestValidateVoice:
    def test_empty_forbidden_phrases_returns_empty(self):
        result = validate_voice("any text", {"forbidden_phrases": []})
        assert result == []

    def test_missing_forbidden_phrases_returns_empty(self):
        result = validate_voice("any text", {})
        assert result == []

    def test_single_match(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {"forbidden_phrases": [r"\bguarantee\b"], "version": 1}
        with tenant_scope(tenant):
            result = validate_voice("we guarantee 100% success", voice)
        assert result == [r"\bguarantee\b"]

    def test_multiple_matches_surfaced(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {
            "forbidden_phrases": [
                r"\bguarantee\b",
                r"100%\s+success",
                r"\bcure\b",
            ],
            "version": 1,
        }
        with tenant_scope(tenant):
            result = validate_voice("we guarantee 100% success cure", voice)
        # All three patterns matched.
        assert set(result) == {
            r"\bguarantee\b",
            r"100%\s+success",
            r"\bcure\b",
        }

    def test_case_insensitive(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {"forbidden_phrases": [r"\bGuarantee\b"], "version": 1}
        with tenant_scope(tenant):
            result = validate_voice("WE GUARANTEE results", voice)
        assert len(result) == 1

    def test_bad_regex_skipped_not_crashed(self, tenant, settings, caplog):
        import logging

        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {
            "forbidden_phrases": [r"[invalid(regex", r"\bok\b"],
            "version": 1,
        }
        with tenant_scope(tenant), caplog.at_level(logging.WARNING):
            result = validate_voice("ok text", voice)
        # Bad regex logged + skipped; good regex still ran.
        assert any("bad_regex" in r.message for r in caplog.records)
        assert result == [r"\bok\b"]

    def test_safety_triggered_event_on_match(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {"forbidden_phrases": [r"\bbad\b"], "version": 7}
        with tenant_scope(tenant):
            validate_voice("this is bad text", voice)
        ev = Event.objects.filter(tenant=tenant, event_name="safety_triggered").first()
        assert ev is not None
        assert ev.properties["reason"] == "forbidden_phrase"
        assert ev.properties["patterns"] == [r"\bbad\b"]
        assert ev.properties["voice_version"] == 7

    def test_no_event_when_clean(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {"forbidden_phrases": [r"\bbad\b"], "version": 1}
        with tenant_scope(tenant):
            validate_voice("totally fine text", voice)
        assert not Event.objects.filter(tenant=tenant, event_name="safety_triggered").exists()

    def test_non_string_pattern_skipped(self, tenant, settings, caplog):
        import logging

        settings.STRICT_TENANT_SCOPE = "strict"
        voice = {"forbidden_phrases": [123, r"\bok\b"], "version": 1}
        with tenant_scope(tenant), caplog.at_level(logging.WARNING):
            result = validate_voice("ok", voice)
        assert any("non_string_pattern" in r.message for r in caplog.records)
        assert result == [r"\bok\b"]
