"""Catalog sync settings smoke (DRF-578 / Sprint 7 / C8).

Verifies the wiring of `MYSITE_CATALOG_*` + `CATALOG_SYNC_*` env vars
and the production fail-fast behaviour. Production is loaded via
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
    def test_base_url_default(self) -> None:
        assert settings.MYSITE_CATALOG_BASE_URL == "https://formulatela58.ru"

    def test_service_token_default_empty(self) -> None:
        # Local config doesn't enforce fail-fast — the token may be blank.
        assert hasattr(settings, "MYSITE_CATALOG_SERVICE_TOKEN")

    def test_lock_ttl_default(self) -> None:
        # 25 minutes ≥ 1.5× the 15-min beat cadence (C5 / DRF-579).
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
    """The production settings module raises on boot when the service-token
    is missing. Loading by importlib so the test runner's own settings
    (local) are unaffected.
    """

    def test_missing_token_raises_improperly_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.delenv("MYSITE_CATALOG_SERVICE_TOKEN", raising=False)
        with pytest.raises(ImproperlyConfigured) as exc_info:
            importlib.import_module("config.settings.production")
        assert "MYSITE_CATALOG_SERVICE_TOKEN" in str(exc_info.value)

    def test_present_token_boots(
        self,
        monkeypatch: pytest.MonkeyPatch,
        _restore_production_module: None,
    ) -> None:
        monkeypatch.setenv("MYSITE_CATALOG_SERVICE_TOKEN", "prod-token-abc")  # noqa: S105
        module = importlib.import_module("config.settings.production")
        assert module.DEBUG is False
