"""Tests for TenantScopedManager + STRICT_TENANT_SCOPE (DRF-420 / A4).

Sprint 1 has no domain model that *uses* TenantScopedManager yet (the
first real consumer is `apps.audit.AuditLog` in B1). These tests
therefore exercise the manager logic in isolation by binding the
manager to a mock model class and asserting the queryset filter
contract.

When B1 ships AuditLog, ``apps/audit/tests/test_audit.py`` adds the
end-to-end ORM integration coverage. The mock-based tests here pin
the logic; the ORM-level test there pins the wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.tenancy.context import tenant_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.managers import TenantScopedManager
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _bind_manager_to_mock_model(name: str = "FakeModel") -> TenantScopedManager:
    """Return a TenantScopedManager whose ``.model`` is a mock class.

    The manager calls ``super().get_queryset()`` and ``super().filter()``;
    we stub those by patching the bound queryset to a MagicMock chain.
    The real `.tenant_scope` behaviour is what we exercise.
    """

    mgr: TenantScopedManager = TenantScopedManager()
    fake_model = MagicMock()
    fake_model.__name__ = name
    mgr.model = fake_model  # type: ignore[assignment]
    return mgr


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------


class TestStrictMode:
    """STRICT_TENANT_SCOPE=strict — raise on missing or mismatched tenant."""

    def test_missing_context_raises(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        mgr = _bind_manager_to_mock_model()

        with pytest.raises(CrossTenantError) as exc:
            mgr.get_queryset()
        assert "without a tenant context" in str(exc.value)

    def test_with_context_filters_by_tenant(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="t1", name="T1")
        mgr = _bind_manager_to_mock_model()

        base_qs = MagicMock(name="base_qs")
        with (
            patch.object(
                TenantScopedManager.__bases__[0],
                "get_queryset",
                return_value=base_qs,
            ) as base_get_qs,
            tenant_scope(t),
        ):
            qs = mgr.get_queryset()

        base_get_qs.assert_called_once()
        # base_qs.filter(tenant=t) is what the manager called; the returned
        # qs is the result of that filter call.
        base_qs.filter.assert_called_once_with(tenant=t)
        assert qs is base_qs.filter.return_value

    def test_explicit_cross_tenant_filter_raises(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="t1", name="T1")
        t2 = Tenant.objects.create(slug="t2", name="T2")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError) as exc:
            mgr.filter(tenant_id=t2.id)
        assert "current_tenant()" in str(exc.value)
        assert str(t2.id) in str(exc.value)

    def test_explicit_filter_with_tenant_object_raises(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="t1", name="T1")
        t2 = Tenant.objects.create(slug="t2", name="T2")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(tenant=t2)

    def test_filter_with_matching_tenant_passes(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="t1", name="T1")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t):
            # Should not raise.
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ) as base_filter:
                result = mgr.filter(tenant_id=t.id)
            base_filter.assert_called_once_with(tenant_id=t.id)
            assert result is base_filter.return_value


# ---------------------------------------------------------------------------
# Audit mode
# ---------------------------------------------------------------------------


class TestAuditMode:
    """STRICT_TENANT_SCOPE=audit — log violation, return empty queryset."""

    def test_missing_context_returns_empty_and_logs(self, settings, caplog):
        settings.STRICT_TENANT_SCOPE = "audit"
        mgr = _bind_manager_to_mock_model()

        none_qs = MagicMock(name="empty_qs")
        with patch.object(
            TenantScopedManager.__bases__[0],
            "get_queryset",
            return_value=MagicMock(none=MagicMock(return_value=none_qs)),
        ):
            with caplog.at_level("WARNING", logger="apps.tenancy.managers"):
                result = mgr.get_queryset()

        assert result is none_qs
        assert any("queryset_without_context" in rec.message for rec in caplog.records)

    def test_cross_tenant_filter_returns_empty_and_logs(self, settings, caplog):
        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="t1", name="T1")
        t2 = Tenant.objects.create(slug="t2", name="T2")
        mgr = _bind_manager_to_mock_model()

        none_qs = MagicMock(name="empty_qs")
        with (
            patch.object(
                TenantScopedManager.__bases__[0],
                "get_queryset",
                return_value=MagicMock(none=MagicMock(return_value=none_qs)),
            ),
            tenant_scope(t1),
        ):
            with caplog.at_level("WARNING", logger="apps.tenancy.managers"):
                result = mgr.filter(tenant_id=t2.id)

        assert result is none_qs
        assert any("explicit_cross_tenant_filter" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Off mode
# ---------------------------------------------------------------------------


class TestOffMode:
    """STRICT_TENANT_SCOPE=off — no filter applied."""

    def test_off_returns_base_queryset(self, settings):
        settings.STRICT_TENANT_SCOPE = "off"
        mgr = _bind_manager_to_mock_model()
        base_qs = MagicMock(name="base_qs")

        with patch.object(
            TenantScopedManager.__bases__[0],
            "get_queryset",
            return_value=base_qs,
        ):
            result = mgr.get_queryset()

        assert result is base_qs
        # No filter applied — base is returned directly.
        base_qs.filter.assert_not_called()

    def test_off_filter_passthrough(self, settings):
        settings.STRICT_TENANT_SCOPE = "off"
        t = Tenant.objects.create(slug="t1", name="T1")
        mgr = _bind_manager_to_mock_model()

        with (
            patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ) as base_filter,
            tenant_scope(t),
        ):
            mgr.filter(tenant_id=t.id)

        # Off mode doesn't raise even on intentional cross-tenant access.
        base_filter.assert_called_once_with(tenant_id=t.id)


# ---------------------------------------------------------------------------
# Invalid mode → default audit
# ---------------------------------------------------------------------------


class TestStrUUIDNormalisation:
    """Regression test for fix #3 (post-review): str(uuid) and UUID
    instances must compare equal in the cross-tenant check. Django
    accepts both shapes in ORM filters; the manager must too.
    """

    def test_filter_with_str_tenant_id_matching_current_passes(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="t-str", name="T")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t):
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ) as base_filter:
                # Caller passes str(uuid) — Django ORM normalises in SQL,
                # the manager must accept it too without false trip.
                mgr.filter(tenant_id=str(t.id))
            base_filter.assert_called_once_with(tenant_id=str(t.id))

    def test_filter_with_uuid_instance_still_passes(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="t-uuid", name="T")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t):
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ):
                mgr.filter(tenant_id=t.id)  # UUID instance

    def test_filter_with_str_other_tenant_still_raises(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="t-str-a", name="A")
        t2 = Tenant.objects.create(slug="t-str-b", name="B")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(tenant_id=str(t2.id))


class TestInvalidMode:
    def test_invalid_mode_falls_back_to_audit(self, settings, caplog):
        settings.STRICT_TENANT_SCOPE = "totally-bogus"
        mgr = _bind_manager_to_mock_model()

        none_qs = MagicMock(name="empty_qs")
        with patch.object(
            TenantScopedManager.__bases__[0],
            "get_queryset",
            return_value=MagicMock(none=MagicMock(return_value=none_qs)),
        ):
            with caplog.at_level("WARNING", logger="apps.tenancy.managers"):
                result = mgr.get_queryset()

        # Behaves as audit (no raise), and warns about the invalid setting.
        assert result is none_qs
        assert any("invalid_scope_mode" in rec.message for rec in caplog.records)
