"""Event-ingest settings wiring — FAIL_OPEN + TRUSTED_PROXY_DEPTH (finding №2).

Both settings were getattr-only (undeclared in any settings file), so
env never reached them and staging could not enable the documented
Round-2 AS8 pre-#246 bridge. Pins: attributes declared with correct
types/defaults, staging module values, and the fail-open branch of
``assert_envelope_tenant_authorized`` actually working under
``override_settings(FAIL_OPEN=True)`` (no TenantUserRelationship model).
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator

import pytest
from django.conf import settings as dj_settings
from django.test import override_settings

from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized


class TestAttributesDeclared:
    def test_fail_open_declared_bool_default_false(self) -> None:
        assert dj_settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN is False

    def test_proxy_depth_declared_int_default_zero(self) -> None:
        assert dj_settings.EVENT_INGEST_TRUSTED_PROXY_DEPTH == 0


@pytest.fixture
def _restore_staging_module() -> Iterator[None]:
    """Reload ``config.settings.staging`` cleanly after the scenario."""
    saved = sys.modules.pop("config.settings.staging", None)
    yield
    if saved is not None:
        sys.modules["config.settings.staging"] = saved
    else:
        sys.modules.pop("config.settings.staging", None)


@pytest.mark.usefixtures("_restore_staging_module")
class TestStagingValues:
    def test_staging_fail_open_true(self) -> None:
        """Staging = pre-#246 bridge per AS8: FAIL_OPEN must be True."""
        import config.settings.staging as staging

        importlib.reload(staging)
        assert staging.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN is True

    def test_staging_proxy_depth_one(self) -> None:
        """Staging nginx adds exactly one trusted hop → depth 1."""
        import config.settings.staging as staging

        importlib.reload(staging)
        assert staging.EVENT_INGEST_TRUSTED_PROXY_DEPTH == 1


class TestFailOpenBranch:
    """The AS8 bridge itself: with FAIL_OPEN=True and the canonical
    TenantUserRelationship model unavailable, a tenant-set envelope
    passes (log + pass) instead of raising TenantAuthorizationError."""

    def _envelope(self, *, tenant_id: str | None) -> object:
        class _Env:
            event_name = "booking.created"
            user_id = "f1a2b3c4-d5e6-4789-9abc-def012345678"

        env = _Env()
        env.tenant_id = tenant_id
        return env

    def test_fail_closed_by_default(self) -> None:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        with override_settings(EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=False):
            with pytest.raises(TenantAuthorizationError):
                assert_envelope_tenant_authorized(
                    self._envelope(tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c")
                )

    def test_fail_open_bridge_passes(self) -> None:
        with override_settings(EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN=True):
            # Must NOT raise — the pre-#246 bridge logs and passes.
            assert_envelope_tenant_authorized(
                self._envelope(tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c")
            )
