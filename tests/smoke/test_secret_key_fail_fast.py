"""Deployed contours refuse to boot on a placeholder SECRET_KEY.

``config/settings/base.py`` reads ``DJANGO_SECRET_KEY`` with a committed
scaffold default (``django-insecure-sprint0-scaffold-only-...``). That is
right for local dev and CI and wrong for anything deployed: ``SECRET_KEY``
signs sessions, CSRF tokens, the master session signer and the HMAC of every
encrypted field (ADR-0006). DRF-1447 measured a staging service booting
without its env_file and therefore on exactly that key — a key that is public
in this repository.

Form taken from the service-token guards in ``production.py`` (DRF-2340 /
DRF-2346 tests): ``ImproperlyConfigured`` at import, not ``assert``.

* p1 — production: unset / empty / the scaffold default / the `.env.example`
  line / any ``django-insecure-`` value — import fails naming the variable;
* p2 — production: a real value is accepted and becomes ``SECRET_KEY``;
* s1 / s2 — the same two halves for staging;
* b1 — base keeps its default: local dev and CI are not asked for a key.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import secret_key_or_refuse

REQUIRED_PRODUCTION_ENV = {
    "AYLA_INTERNAL_API_TOKEN": "ayla-token-abc",  # pragma: allowlist secret
    "CHROMA_AUTH_TOKEN": "chroma-token-abc",  # pragma: allowlist secret
    "SENTRY_DSN": "https://public@sentry.example.com/1",
    "MYSITE_WEBHOOK_HMAC_SECRET": "hmac-secret-abc",  # pragma: allowlist secret
    "AYLA_PAYMENTS_TEST_MODE": "false",
    "BOOKING_VIA_AYLA_REST": "true",
}

REAL_KEY = "a-real-looking-key-of-reasonable-length-0123456789abcdef"  # pragma: allowlist secret

REFUSED = [
    pytest.param(None, id="unset"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace"),
    pytest.param("django-insecure-sprint0-scaffold-only-replace-before-staging", id="scaffold"),
    pytest.param("replace-me-in-staging-and-prod", id="env-example"),
    pytest.param("django-insecure-abc123xyz", id="startproject-prefix"),
]


def _restore(name: str) -> Iterator[None]:
    saved = sys.modules.pop(name, None)
    yield
    if saved is not None:
        sys.modules[name] = saved
    else:
        sys.modules.pop(name, None)


@pytest.fixture
def _restore_production_module() -> Iterator[None]:
    """Import with a side effect is the subject; the module is cleaned up after."""
    yield from _restore("config.settings.production")


@pytest.fixture
def _restore_staging_module() -> Iterator[None]:
    yield from _restore("config.settings.staging")


def _set_key(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("DJANGO_SECRET_KEY", raw)


class TestTheHelper:
    @pytest.mark.parametrize("raw", REFUSED)
    def test_placeholders_are_refused_by_name(self, raw: str | None) -> None:
        with pytest.raises(ImproperlyConfigured) as exc:
            secret_key_or_refuse(raw, contour="production")
        assert "DJANGO_SECRET_KEY" in str(exc.value)
        assert "production" in str(exc.value)

    def test_a_real_value_is_returned_stripped(self) -> None:
        assert secret_key_or_refuse(f"  {REAL_KEY}  ", contour="staging") == REAL_KEY


class TestProductionCannotBootOnThePlaceholder:
    @pytest.mark.parametrize("raw", REFUSED)
    def test_placeholder_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None, raw: str | None
    ) -> None:
        for name, value in REQUIRED_PRODUCTION_ENV.items():
            monkeypatch.setenv(name, value)
        _set_key(monkeypatch, raw)
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.production")
        assert "DJANGO_SECRET_KEY" in str(exc.value)

    def test_a_real_key_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, _restore_production_module: None
    ) -> None:
        for name, value in REQUIRED_PRODUCTION_ENV.items():
            monkeypatch.setenv(name, value)
        _set_key(monkeypatch, REAL_KEY)
        module = importlib.import_module("config.settings.production")
        assert module.SECRET_KEY == REAL_KEY


class TestStagingCannotBootOnThePlaceholder:
    @pytest.mark.parametrize("raw", REFUSED)
    def test_placeholder_refuses_to_boot(
        self, monkeypatch: pytest.MonkeyPatch, _restore_staging_module: None, raw: str | None
    ) -> None:
        _set_key(monkeypatch, raw)
        with pytest.raises(ImproperlyConfigured) as exc:
            importlib.import_module("config.settings.staging")
        assert "DJANGO_SECRET_KEY" in str(exc.value)
        assert "staging" in str(exc.value)

    def test_a_real_key_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, _restore_staging_module: None
    ) -> None:
        _set_key(monkeypatch, REAL_KEY)
        module = importlib.import_module("config.settings.staging")
        assert module.SECRET_KEY == REAL_KEY


class TestBaseKeepsItsDefault:
    def test_local_dev_and_ci_are_not_asked_for_a_key(self) -> None:
        """The guard lives in the deployed contours only: ``base`` still has a
        default, so ``manage.py`` on a laptop and the CI runner boot as before."""
        from config.settings import base

        assert isinstance(base.SECRET_KEY, str)
        assert base.SECRET_KEY != ""
