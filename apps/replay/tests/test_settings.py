"""REPLAY_* settings sanity tests (DRF-499 / Sprint 5 / A3)."""

from __future__ import annotations

from django.conf import settings


class TestReplaySettings:
    def test_sample_rate_defaults(self):
        # Phase 0: 100% on prod + staging.
        assert settings.REPLAY_SAMPLE_RATE_PROD == 1.0
        assert settings.REPLAY_SAMPLE_RATE_STAGING == 1.0
        # Test default = 0 — recorder tests opt-in via settings override.
        assert settings.REPLAY_SAMPLE_RATE_TEST == 0.0

    def test_retention_default(self):
        assert settings.REPLAY_RETENTION_DAYS == 30

    def test_allowlist_default_empty(self):
        assert settings.REPLAY_REDACTION_ALLOWLIST == []

    def test_settings_override_works(self, settings):
        settings.REPLAY_SAMPLE_RATE_TEST = 0.5
        from django.conf import settings as django_settings

        assert django_settings.REPLAY_SAMPLE_RATE_TEST == 0.5
