"""The Mini App dev-bypass needs an explicit flag, and deployed contours pin it off.

``apps/miniapp_api/dev_bypass.py`` used to be gated on ``settings.DEBUG``
alone. DEBUG exists for tracebacks and SQL logging, and DRF-1447 measured a
staging service running with it on — which also made Mini App init-data
verification skippable by three request headers. The gate is now
``DEBUG AND MINIAPP_DEV_BYPASS_ENABLED`` (the request-level matrix lives in
``apps/miniapp_api/tests/test_dev_bypass.py``); this file pins the settings
half:

* d1 — the flag is declared in base, defaults to False, parses plainly;
* p1 / s1 — production and staging REFUSE TO BOOT if the environment tries to
  enable it (``ImproperlyConfigured`` at import, naming the variable);
* p2 / s2 — with the variable unset or false both contours boot with the flag
  pinned to False;
* e1 — the key is in ``.env.example`` (local dev is where it is legitimately
  turned on).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import dev_bypass_enabled_from

REPO = Path(__file__).resolve().parents[2]
REQUIRED_PRODUCTION_ENV = {
    "DJANGO_SECRET_KEY": "a-real-looking-key-of-reasonable-length-0123456789abcdef",  # pragma: allowlist secret
    "AYLA_INTERNAL_API_TOKEN": "ayla-token-abc",  # pragma: allowlist secret
    "CHROMA_AUTH_TOKEN": "chroma-token-abc",  # pragma: allowlist secret
    "SENTRY_DSN": "https://public@sentry.example.com/1",
    "MYSITE_WEBHOOK_HMAC_SECRET": "hmac-secret-abc",  # pragma: allowlist secret
    "AYLA_PAYMENTS_TEST_MODE": "false",
    "BOOKING_VIA_AYLA_REST": "true",
}
# Staging has no required service tokens, but since the SECRET_KEY guard it
# needs a real key to import at all.
REQUIRED_STAGING_ENV = {"DJANGO_SECRET_KEY": REQUIRED_PRODUCTION_ENV["DJANGO_SECRET_KEY"]}


def _restore(name: str) -> Iterator[None]:
    saved = sys.modules.pop(name, None)
    yield
    if saved is not None:
        sys.modules[name] = saved
    else:
        sys.modules.pop(name, None)


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    yield from _restore("config.settings.production")


@pytest.fixture
def _restore_staging_module() -> Iterator[None]:
    yield from _restore("config.settings.staging")


def _with(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)


class TestDeclared:
    def test_the_setting_is_a_setting_and_defaults_off(self) -> None:
        assert hasattr(settings, "MINIAPP_DEV_BYPASS_ENABLED")
        assert settings.MINIAPP_DEV_BYPASS_ENABLED is False

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True),
            ("True", True),
            (" 1 ", True),
            ("false", False),
            ("0", False),
            ("", False),
            (None, False),
            ("yes", False),
        ],
    )
    def test_plain_values_read_and_garbage_is_off(self, raw: str | None, expected: bool) -> None:
        """Anything that is not an explicit yes is OFF — the flag fails closed."""
        assert dev_bypass_enabled_from(raw) is expected


class TestProductionRefusesTheFlag:
    @pytest.mark.parametrize("raw", ["true", "1", "True"])
    def test_enabled_in_env_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None, raw: str
    ) -> None:
        _with(monkeypatch, REQUIRED_PRODUCTION_ENV)
        monkeypatch.setenv("MINIAPP_DEV_BYPASS_ENABLED", raw)
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.production")
        assert "MINIAPP_DEV_BYPASS_ENABLED" in str(exc.value)

    @pytest.mark.parametrize("raw", [None, "false", "0"])
    def test_unset_or_false_boots_with_the_flag_pinned_off(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None, raw: str | None
    ) -> None:
        _with(monkeypatch, REQUIRED_PRODUCTION_ENV)
        if raw is None:
            monkeypatch.delenv("MINIAPP_DEV_BYPASS_ENABLED", raising=False)
        else:
            monkeypatch.setenv("MINIAPP_DEV_BYPASS_ENABLED", raw)
        module = importlib.import_module("config.settings.production")
        assert module.MINIAPP_DEV_BYPASS_ENABLED is False
        assert module.DEBUG is False


class TestStagingRefusesTheFlag:
    @pytest.mark.parametrize("raw", ["true", "1"])
    def test_enabled_in_env_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_staging_module: None, raw: str
    ) -> None:
        _with(monkeypatch, REQUIRED_STAGING_ENV)
        monkeypatch.setenv("MINIAPP_DEV_BYPASS_ENABLED", raw)
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.staging")
        assert "MINIAPP_DEV_BYPASS_ENABLED" in str(exc.value)

    def test_unset_boots_with_the_flag_pinned_off(
        self, monkeypatch: pytest.MonkeyPatch, _restore_staging_module: None
    ) -> None:
        _with(monkeypatch, REQUIRED_STAGING_ENV)
        monkeypatch.delenv("MINIAPP_DEV_BYPASS_ENABLED", raising=False)
        module = importlib.import_module("config.settings.staging")
        assert module.MINIAPP_DEV_BYPASS_ENABLED is False


class TestTheDeclaredSurfaceMatchesTheLiveOne:
    def test_the_key_is_in_the_local_template(self) -> None:
        text = (REPO / ".env.example").read_text(encoding="utf-8")
        assert "DJANGO_DEBUG" in text  # the file is the one we think it is
        assert "MINIAPP_DEV_BYPASS_ENABLED" in text
