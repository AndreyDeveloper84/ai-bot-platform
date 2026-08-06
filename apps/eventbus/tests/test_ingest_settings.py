"""Event-ingest settings wiring — FAIL_OPEN + TRUSTED_PROXY_DEPTH (finding №2).

Both settings were getattr-only (undeclared in any settings file), so env
never reached them. Pins: attributes declared with correct types/defaults,
staging module values, and the emergency global fail-open escape hatch.

T-02 / OD-T02-1 update: staging no longer hard-codes
``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = True``. The staging assertions
below are inverted accordingly, and the pilot allowlists are pinned to
deny-all defaults. The fail-open branch tests remain — the flag survives as
an emergency escape hatch, so its behaviour must stay covered — but it is
NOT the happy path any more; that lives in ``test_ingest_pilot_allowlist``.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path

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
    def test_staging_has_no_global_fail_open(self) -> None:
        """T-02 / OD-T02-1 — staging must NOT globally disable tenant verification.

        Staging used to hard-code ``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN =
        True``, which turned off tenant verification for every tenant and
        every event. It now inherits the fail-closed base default and gets
        its scope from the pilot allowlists instead.
        """
        import config.settings.staging as staging

        importlib.reload(staging)
        assert staging.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN is False

    def test_staging_module_source_has_no_fail_open_assignment(self) -> None:
        """Regression guard: the flag must not be re-added to staging.py.

        The value assertion above passes for the wrong reason if someone
        writes ``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = os.environ...`` —
        it would read False in CI while being flippable by an env var in
        the real staging deploy. Pin the absence of the assignment itself.
        """
        import config.settings.staging as staging

        source = Path(staging.__file__).read_text(encoding="utf-8")
        assignments = [
            line
            for line in source.splitlines()
            if line.strip().startswith("EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN")
        ]
        assert assignments == [], (
            f"staging.py must not assign EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN; found: {assignments}"
        )

    def test_staging_allowlists_default_to_deny_all(self) -> None:
        """With no env configuration, staging ingests nothing (fail-closed)."""
        import config.settings.staging as staging

        importlib.reload(staging)
        assert staging.EVENT_INGEST_ALLOWED_TENANTS == frozenset()
        assert staging.EVENT_INGEST_ALLOWED_EVENTS == frozenset()

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
            tenant_id: str | None = None

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
