"""DRF-1023 — web security settings wiring (admin login fix).

The pilot contour serves the Django admin over HTTPS behind a
TLS-terminating nginx, but the project declared no security directives at
all: ``CSRF_TRUSTED_ORIGINS`` was empty, so every admin login POST was
rejected with «Ошибка проверки CSRF». These tests pin the fix:

1. ``config.security.parse_trusted_origins`` — strict CSV parsing:
   valid / empty / malformed (a malformed value must refuse to boot, by
   the same fail-safe philosophy as the T-02 / DRF-1005 allowlists).
2. ``config.settings.base`` wiring — defaults keep behaviour unchanged
   (empty origins, no proxy header, insecure cookies); env opts in.
3. The original bug end-to-end: an admin login POST arriving over
   proxy-HTTPS is 403 with an empty origin list and passes the CSRF gate
   once the contour's origin is trusted.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator

import pytest
from django.conf import settings as dj_settings
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings

from config.security import OriginConfigurationError, parse_trusted_origins

PILOT_ORIGIN = "https://api-dev.gobeauty.site"


class TestParseTrustedOrigins:
    def test_empty_and_unset_trust_nothing(self) -> None:
        assert parse_trusted_origins(None) == []
        assert parse_trusted_origins("") == []
        assert parse_trusted_origins("   ") == []

    def test_valid_single_origin(self) -> None:
        assert parse_trusted_origins(PILOT_ORIGIN) == [PILOT_ORIGIN]

    def test_valid_csv_trims_and_dedups(self) -> None:
        raw = f" {PILOT_ORIGIN} , http://localhost:8000,{PILOT_ORIGIN}"
        assert parse_trusted_origins(raw) == [PILOT_ORIGIN, "http://localhost:8000"]

    def test_scheme_and_host_lowercased(self) -> None:
        assert parse_trusted_origins("HTTPS://API-DEV.GoBeauty.SITE") == [PILOT_ORIGIN]

    @pytest.mark.parametrize(
        "raw",
        [
            "api-dev.gobeauty.site",  # no scheme
            "https://api-dev.gobeauty.site/",  # trailing slash = path
            "https://api-dev.gobeauty.site/admin/",  # path
            "https://api-dev.gobeauty.site?q=1",  # query
            "https://user@api-dev.gobeauty.site",  # userinfo
            "https://*.gobeauty.site",  # subdomain wildcard
            "*",  # bare wildcard
            "all",
            "https://api-dev.gobeauty.site:0",  # port out of range
            "https://api-dev.gobeauty.site:65536",  # port out of range
            "https://api-dev.gobeauty.site:https",  # junk port — no match
            f"{PILOT_ORIGIN},",  # trailing comma = empty element
            f"{PILOT_ORIGIN},,https://x.example",  # stray comma
            "ftp://api-dev.gobeauty.site",  # non-http(s) scheme
        ],
    )
    def test_malformed_rejected(self, raw: str) -> None:
        """Partial acceptance is not offered: a typo must fail loudly
        instead of silently narrowing or widening the CSRF boundary."""
        with pytest.raises(OriginConfigurationError):
            parse_trusted_origins(raw)

    def test_non_string_rejected(self) -> None:
        with pytest.raises(OriginConfigurationError):
            parse_trusted_origins(["https://api-dev.gobeauty.site"])


@pytest.fixture
def _restore_base_module() -> Iterator[None]:
    """Re-import ``config.settings.base`` cleanly after each scenario.

    Same pattern as ``apps/skills/booking/tests/test_health_gate_settings.py``:
    the module's import-time side effect (``raise ImproperlyConfigured``)
    is exactly what we're testing — but we mustn't leave a half-imported
    module in ``sys.modules`` for the next test.
    """
    saved = sys.modules.pop("config.settings.base", None)
    yield
    if saved is not None:
        sys.modules["config.settings.base"] = saved
    else:
        sys.modules.pop("config.settings.base", None)


class TestAttributesDeclared:
    def test_csrf_trusted_origins_declared_default_empty(self) -> None:
        assert dj_settings.CSRF_TRUSTED_ORIGINS == []

    def test_secure_proxy_ssl_header_default_unset(self) -> None:
        assert getattr(dj_settings, "SECURE_PROXY_SSL_HEADER", None) is None

    def test_secure_cookies_default_off(self) -> None:
        assert dj_settings.SESSION_COOKIE_SECURE is False
        assert dj_settings.CSRF_COOKIE_SECURE is False


@pytest.mark.usefixtures("_restore_base_module")
class TestBaseSettingsWiring:
    def test_unset_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "DJANGO_BEHIND_TLS_PROXY",
            "DJANGO_SESSION_COOKIE_SECURE",
            "DJANGO_CSRF_COOKIE_SECURE",
        ):
            monkeypatch.delenv(var, raising=False)
        base = importlib.import_module("config.settings.base")
        assert base.CSRF_TRUSTED_ORIGINS == []
        assert not hasattr(base, "SECURE_PROXY_SSL_HEADER")
        assert base.SESSION_COOKIE_SECURE is False
        assert base.CSRF_COOKIE_SECURE is False

    def test_valid_csv_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            f"{PILOT_ORIGIN},http://localhost:8000",
        )
        base = importlib.import_module("config.settings.base")
        assert base.CSRF_TRUSTED_ORIGINS == [PILOT_ORIGIN, "http://localhost:8000"]

    def test_malformed_refuses_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo'd origin must fail LOUDLY at settings load — the failure
        mode DRF-1023 fixes is precisely a contour whose operator believes
        the origin is configured while the login 403s."""
        monkeypatch.setenv("DJANGO_CSRF_TRUSTED_ORIGINS", "api-dev.gobeauty.site")
        with pytest.raises(ImproperlyConfigured):
            importlib.import_module("config.settings.base")

    def test_tls_proxy_flag_sets_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DJANGO_BEHIND_TLS_PROXY", "true")
        base = importlib.import_module("config.settings.base")
        assert base.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")

    def test_secure_cookie_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DJANGO_SESSION_COOKIE_SECURE", "true")
        monkeypatch.setenv("DJANGO_CSRF_COOKIE_SECURE", "true")
        base = importlib.import_module("config.settings.base")
        assert base.SESSION_COOKIE_SECURE is True
        assert base.CSRF_COOKIE_SECURE is True


@pytest.mark.django_db
class TestAdminLoginCsrf:
    """The DRF-1023 bug itself, pinned end-to-end through the CSRF
    middleware with ``enforce_csrf_checks=True``."""

    def _client(self) -> Client:
        return Client(enforce_csrf_checks=True)

    def _get_login(self, client: Client):
        return client.get("/admin/login/", HTTP_X_FORWARDED_PROTO="https")

    def _post_login(self, client: Client, *, origin: str):
        token = client.cookies["csrftoken"].value
        return client.post(
            "/admin/login/",
            {
                "username": "nobody",
                "password": "wrong-on-purpose",  # pragma: allowlist secret
                "csrfmiddlewaretoken": token,
                "next": "/admin/",
            },
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_ORIGIN=origin,
        )

    def test_login_form_get_200(self) -> None:
        with override_settings(
            CSRF_TRUSTED_ORIGINS=[PILOT_ORIGIN],
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            response = self._get_login(self._client())
        assert response.status_code == 200

    def test_login_post_with_trusted_origin_passes_csrf_gate(self) -> None:
        """With the contour's origin configured, a proxy-HTTPS login POST
        reaches the view (200 = form re-rendered on bad credentials),
        instead of dying at the CSRF gate with 403."""
        with override_settings(
            CSRF_TRUSTED_ORIGINS=[PILOT_ORIGIN],
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            client = self._client()
            self._get_login(client)
            response = self._post_login(client, origin=PILOT_ORIGIN)
        assert response.status_code == 200

    def test_login_post_with_empty_origins_is_403(self) -> None:
        """The original pilot failure: empty CSRF_TRUSTED_ORIGINS → every
        proxy-HTTPS POST rejected before it reaches the view."""
        with override_settings(
            CSRF_TRUSTED_ORIGINS=[],
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            client = self._client()
            self._get_login(client)
            response = self._post_login(client, origin=PILOT_ORIGIN)
        assert response.status_code == 403

    def test_login_post_with_untrusted_origin_is_403(self) -> None:
        with override_settings(
            CSRF_TRUSTED_ORIGINS=[PILOT_ORIGIN],
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        ):
            client = self._client()
            self._get_login(client)
            response = self._post_login(client, origin="https://evil.example")
        assert response.status_code == 403
