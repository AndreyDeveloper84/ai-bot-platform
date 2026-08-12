"""DRF-1005 — ``BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS`` settings wiring.

Mirrors ``apps/eventbus/tests/test_ingest_settings.py``: the setting is
declared in ``config/settings/base.py``, defaults to deny-all (empty
frozenset → gate closed for every tenant), parses a CSV of canonical
tenant UUIDs from the environment, and a malformed value refuses to boot
(``ImproperlyConfigured``) instead of silently becoming an empty
allowlist.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator

import pytest
from django.conf import settings as dj_settings
from django.core.exceptions import ImproperlyConfigured


class TestAttributeDeclared:
    def test_declared_default_deny_all(self) -> None:
        assert dj_settings.BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS == frozenset()


@pytest.fixture
def _restore_base_module() -> Iterator[None]:
    """Re-import ``config.settings.base`` cleanly after each scenario.

    The module's import-time side effect (``raise ImproperlyConfigured``)
    is exactly what we're testing — but we mustn't leave a half-imported
    module in ``sys.modules`` for the next test.
    """
    saved = sys.modules.pop("config.settings.base", None)
    yield
    if saved is not None:
        sys.modules["config.settings.base"] = saved
    else:
        sys.modules.pop("config.settings.base", None)


@pytest.mark.usefixtures("_restore_base_module")
class TestBaseSettingsParsing:
    def test_unset_env_defaults_to_deny_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS", raising=False)
        base = importlib.import_module("config.settings.base")
        assert base.BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS == frozenset()

    def test_valid_csv_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS",
            "b32a057a-56c7-4bf0-ae50-e11e76ab44be",
        )
        base = importlib.import_module("config.settings.base")
        assert base.BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS == frozenset(
            {"b32a057a-56c7-4bf0-ae50-e11e76ab44be"}
        )

    def test_malformed_refuses_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo'd allowlist must fail LOUDLY at settings load — an
        operator who believes the pilot tenant is listed while the process
        silently parsed nothing is the exact failure mode T-02 rejected."""
        monkeypatch.setenv("BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS", "not-a-uuid")
        with pytest.raises(ImproperlyConfigured):
            importlib.import_module("config.settings.base")
