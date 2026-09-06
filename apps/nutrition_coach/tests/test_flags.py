"""The two safety switches in front of the coach (DRF-1464, T1).

Mirrors the DRF-1285 contract: both switches ship closed, and opening
them is two deliberate operator acts in a fixed order — ENABLED=True
with DRY_RUN=True first, DRY_RUN=False only after the dry-run logs have
been read.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.conf import settings
from django.test import override_settings

from apps.nutrition_coach import flags


class TestDefaults:
    """An environment that never heard of the flags is a closed environment."""

    def test_enabled_defaults_false(self, monkeypatch) -> None:
        monkeypatch.setattr(flags, "settings", SimpleNamespace())
        assert flags.enabled() is False

    def test_dry_run_defaults_true(self, monkeypatch) -> None:
        monkeypatch.setattr(flags, "settings", SimpleNamespace())
        assert flags.dry_run() is True

    def test_base_settings_ship_both_closed(self) -> None:
        """The committed defaults: nothing reaches a person from a deploy."""
        assert settings.NUTRITION_COACH_ENABLED is False
        assert settings.NUTRITION_COACH_DRY_RUN is True


class TestReadersFollowSettings:
    def test_enabled_reads_settings(self) -> None:
        with override_settings(NUTRITION_COACH_ENABLED=True):
            assert flags.enabled() is True
        with override_settings(NUTRITION_COACH_ENABLED=False):
            assert flags.enabled() is False

    def test_dry_run_reads_settings(self) -> None:
        with override_settings(NUTRITION_COACH_DRY_RUN=False):
            assert flags.dry_run() is False
        with override_settings(NUTRITION_COACH_DRY_RUN=True):
            assert flags.dry_run() is True
