"""Catalog sync settings smoke.

Verifies the wiring of the catalog-sync env vars and the production
fail-fast behaviour. Since S3B (#1044) the sync reads Ayla's internal
catalog (``AYLA_BASE_URL`` + ``AYLA_INTERNAL_API_TOKEN``) — the retired
``MYSITE_CATALOG_*`` vars are gone. Production is loaded via
``importlib.reload`` against a monkey-patched ``os.environ`` so the
side-effect-on-import doesn't pollute the test runner's own settings.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class TestBaseSettings:
    def test_ayla_catalog_settings_wired(self) -> None:
        # Catalog sync now depends on the shared Ayla internal-API config.
        assert hasattr(settings, "AYLA_BASE_URL")
        assert hasattr(settings, "AYLA_INTERNAL_API_TOKEN")

    def test_lock_ttl_default(self) -> None:
        # 25 minutes ≥ 1.5× the 15-min beat cadence.
        assert settings.CATALOG_SYNC_LOCK_TTL_SECONDS == 25 * 60

    def test_http_timeout_default(self) -> None:
        assert settings.CATALOG_SYNC_HTTP_TIMEOUT == 30

    def test_http_retries_default(self) -> None:
        assert settings.CATALOG_SYNC_HTTP_RETRIES == 3


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    """Reload `config.settings.production` cleanly after each scenario.

    The module's import-time side effect (`raise ImproperlyConfigured`)
    is exactly what we're testing — but we mustn't leave a half-imported
    module in `sys.modules` for the next test.
    """
    import sys

    saved = sys.modules.pop("config.settings.production", None)
    yield
    if saved is not None:
        sys.modules["config.settings.production"] = saved
    else:
        sys.modules.pop("config.settings.production", None)


class TestProductionFailFast:
    """The production settings module raises on boot when any required
    service-token is missing. Loading by importlib so the test runner's
    own settings (local) are unaffected. The AYLA_INTERNAL_API_TOKEN guard
    is first, so every non-AYLA scenario must set it to reach its target.
    """

    def test_missing_ayla_internal_token_raises_improperly_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        """S3B (#1044) — catalog sync + internal clients need the Ayla token."""
        monkeypatch.delenv("AYLA_INTERNAL_API_TOKEN", raising=False)
        monkeypatch.setenv("CHROMA_AUTH_TOKEN", "chroma-token-abc")  # noqa: S105
        monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
        monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")  # noqa: S105
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.import_module("config.settings.production")
        assert "AYLA_INTERNAL_API_TOKEN" in str(exc_info.value)

    def test_missing_chroma_token_raises_improperly_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("AYLA_INTERNAL_API_TOKEN", "ayla-token-abc")  # noqa: S105
        monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
        monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")  # noqa: S105
        monkeypatch.delenv("CHROMA_AUTH_TOKEN", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.import_module("config.settings.production")
        assert "CHROMA_AUTH_TOKEN" in str(exc_info.value)

    def test_missing_sentry_dsn_raises_improperly_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("AYLA_INTERNAL_API_TOKEN", "ayla-token-abc")  # noqa: S105
        monkeypatch.setenv("CHROMA_AUTH_TOKEN", "chroma-token-abc")  # noqa: S105
        monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")  # noqa: S105
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.import_module("config.settings.production")
        assert "SENTRY_DSN" in str(exc_info.value)

    def test_missing_webhook_hmac_secret_raises_improperly_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        """Sprint 10 / C3 (DRF-879) — webhook HMAC secret fail-fast."""
        monkeypatch.setenv("AYLA_INTERNAL_API_TOKEN", "ayla-token-abc")  # noqa: S105
        monkeypatch.setenv("CHROMA_AUTH_TOKEN", "chroma-token-abc")  # noqa: S105
        monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
        monkeypatch.delenv("MYSITE_WEBHOOK_HMAC_SECRET", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.import_module("config.settings.production")
        assert "MYSITE_WEBHOOK_HMAC_SECRET" in str(exc_info.value)

    def test_all_tokens_present_boots(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("AYLA_INTERNAL_API_TOKEN", "ayla-token-abc")  # noqa: S105
        monkeypatch.setenv("CHROMA_AUTH_TOKEN", "chroma-token-abc")  # noqa: S105
        monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
        monkeypatch.setenv("MYSITE_WEBHOOK_HMAC_SECRET", "hmac-secret-abc")  # noqa: S105
        module = importlib.import_module("config.settings.production")
        assert module.DEBUG is False
        assert module.AYLA_INTERNAL_API_TOKEN == "ayla-token-abc"  # noqa: S105
        assert module.CHROMA_AUTH_TOKEN == "chroma-token-abc"  # noqa: S105
        assert module.SENTRY_DSN == "https://public@sentry.example.com/1"
        expected_hmac = "hmac-secret-abc"  # noqa: S105  # pragma: allowlist secret
        assert module.MYSITE_WEBHOOK_HMAC_SECRET == expected_hmac
