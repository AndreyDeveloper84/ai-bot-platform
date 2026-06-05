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

        # Tenancy retro B1: audit-mode short-circuit now routes through
        # ``self.none()`` (not ``super().get_queryset().none()``) so the
        # empty queryset still carries the scope filter. Patch the
        # manager's ``none`` directly to assert the path.
        none_qs = MagicMock(name="empty_qs")
        with (
            patch.object(mgr, "none", return_value=none_qs),
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


# ---------------------------------------------------------------------------
# Tenancy retro B1 — Q-objects + lookup-alias enforcement
# ---------------------------------------------------------------------------


class TestRetroB1QObjectsAndAliases:
    """Pre-hotfix the cross-tenant detector inspected only the bare
    ``tenant`` / ``tenant_id`` kwargs. Lookups like ``tenant__id``,
    ``tenant_id__in``, ``tenant_id__exact`` AND positional ``Q`` nodes
    bypassed the check — ``super().filter()`` would AND them with the
    scope filter, return empty *for the wrong reason*, and no audit row
    landed.

    Post-hotfix the manager walks every kwarg key through
    ``_TENANT_LOOKUP_RE`` AND recurses ``Q`` nodes in ``args``.
    """

    def test_strict_raises_on_tenant_dunder_id_alias(self, settings):
        from django.db.models import Q  # noqa: F401  — referenced in companion tests

        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b1-a", name="A")
        t2 = Tenant.objects.create(slug="b1-b", name="B")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(tenant__id=t2.id)

    def test_strict_raises_on_tenant_id_in_lookup(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b1-c", name="C")
        t2 = Tenant.objects.create(slug="b1-d", name="D")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(tenant_id__in=[t2.id])

    def test_strict_raises_on_tenant_id_exact_lookup(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b1-e", name="E")
        t2 = Tenant.objects.create(slug="b1-f", name="F")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(tenant_id__exact=t2.id)

    def test_strict_raises_on_q_node_with_tenant_id(self, settings):
        from django.db.models import Q

        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b1-g", name="G")
        t2 = Tenant.objects.create(slug="b1-h", name="H")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(Q(tenant_id=t2.id))

    def test_strict_raises_on_nested_q_with_tenant_id(self, settings):
        from django.db.models import Q

        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b1-i", name="I")
        t2 = Tenant.objects.create(slug="b1-j", name="J")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1), pytest.raises(CrossTenantError):
            mgr.filter(Q(name="x") | Q(tenant_id=t2.id))

    def test_unrelated_kwarg_starting_with_tenant_does_not_trip(self, settings):
        # The regex anchors on ``^tenant(_id)?(__|$)``, so a kwarg like
        # ``tenant_label`` (hypothetical future field) doesn't trigger
        # the detector. Pin to prevent over-eager matching.
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="b1-k", name="K")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t):
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ) as base_filter:
                mgr.filter(tenant_label="unrelated-value")
            base_filter.assert_called_once_with(tenant_label="unrelated-value")

    def test_legitimate_same_tenant_id_passes(self, settings):
        # Negative regression: ``filter(tenant_id=<own id>)`` is a no-op
        # for the manager (the get_queryset filter already scopes), but
        # must NOT trip the detector. Tests both UUID and str forms.
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="b1-own", name="Own")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t):
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ):
                mgr.filter(tenant_id=t.id)
                mgr.filter(tenant_id=str(t.id))
                mgr.filter(tenant__id=t.id)

    def test_in_lookup_with_own_id_only_does_not_trip(self, settings):
        # Reviewer B1.1: pre-fix ``filter(tenant_id__in=[own.id])``
        # false-positived because the whole list got stringified and
        # compared against current.id. Post-fix walks the list and
        # each element compares individually.
        settings.STRICT_TENANT_SCOPE = "strict"
        t = Tenant.objects.create(slug="b1-in-own", name="In Own")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t):
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ):
                mgr.filter(tenant_id__in=[t.id])
                mgr.filter(tenant_id__in=[str(t.id)])

    def test_in_lookup_mixed_list_trips_with_full_breadcrumb(self, settings, caplog):
        # A mixed ``__in`` list (own + foreign) MUST trip the detector
        # AND the audit row must list the foreign id so triage isn't
        # blinded by the «list as string» shape.
        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="b1-mix-a", name="A")
        t2 = Tenant.objects.create(slug="b1-mix-b", name="B")
        mgr = _bind_manager_to_mock_model()

        with patch.object(mgr, "none", return_value=MagicMock(name="empty_qs")):
            with tenant_scope(t1):
                with caplog.at_level("WARNING", logger="apps.tenancy.managers"):
                    mgr.filter(tenant_id__in=[t1.id, t2.id])

        matching = [rec for rec in caplog.records if "explicit_cross_tenant_filter" in rec.message]
        assert matching, "expected an explicit_cross_tenant_filter audit log"
        # The audit payload carries the mismatched ids; own id should NOT
        # appear in the ``requested`` list (it WILL appear as ``current``,
        # so we substring-match the ``'requested': [...]`` segment only).
        msg = matching[0].message
        # Extract the requested-list segment via the audit log shape:
        # ``extra={... 'requested': ['<uuid>', ...], ...}``.
        requested_segment = msg.split("'requested':", 1)[1].split("], ", 1)[0]
        assert str(t2.id) in requested_segment
        assert str(t1.id) not in requested_segment

    def test_negated_q_tenant_id_does_not_trip(self, settings):
        # Reviewer Y2: ``~Q(tenant_id=X)`` semantically means «exclude
        # rows belonging to X», a defensive exclusion. It is NOT a
        # cross-tenant lookup and must not trip the detector.
        from django.db.models import Q

        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b1-neg-a", name="A")
        t2 = Tenant.objects.create(slug="b1-neg-b", name="B")
        mgr = _bind_manager_to_mock_model()

        with tenant_scope(t1):
            with patch.object(
                TenantScopedManager.__bases__[0],
                "filter",
                return_value=MagicMock(name="filtered_qs"),
            ):
                mgr.filter(~Q(tenant_id=t2.id))


# ---------------------------------------------------------------------------
# Tenancy retro B3 — slug immutability
# ---------------------------------------------------------------------------


class TestRetroB3SlugImmutability:
    """The ``Tenant.slug`` field docstring promised «Cannot be changed
    after creation», but nothing enforced it at save() time. A renamed
    slug would let the middleware resolver rebind a known slug to a
    different tenant — cross-tenant attack vector when stale links /
    cached webhook URLs / bookmarked customer pages carry the old slug.
    """

    def test_slug_rename_raises_value_error(self):
        t = Tenant.objects.create(slug="orig-slug", name="Original")
        t.slug = "renamed-slug"
        with pytest.raises(ValueError) as exc:
            t.save()
        assert "immutable" in str(exc.value).lower()
        assert "orig-slug" in str(exc.value)
        assert "renamed-slug" in str(exc.value)

    def test_resave_with_same_slug_passes(self):
        # Round-trip without renaming must not raise — pin the negative.
        t = Tenant.objects.create(slug="stable-slug", name="Stable")
        t.name = "Renamed (name is mutable)"
        t.save()  # No raise
        t.refresh_from_db()
        assert t.name == "Renamed (name is mutable)"
        assert t.slug == "stable-slug"

    def test_create_with_fresh_slug_works(self):
        # First-creation path must work even after fixture pollution.
        Tenant.objects.create(slug="brand-new", name="Brand New")
        assert Tenant.objects.filter(slug="brand-new").exists()

    def test_rename_blocked_even_when_soft_deleted(self):
        # Soft-deleted tenants must also not be renamable — otherwise
        # a re-activation could rebind a stale slug to a different
        # tenant id. Default manager (_ActiveTenantManager) hides
        # is_active=False rows, so the save() check must use
        # ``_base_manager`` to see the prior slug.
        t = Tenant.objects.create(slug="soft-del", name="To Delete")
        t.is_active = False
        t.save()  # Same slug, just deactivating — OK
        t.refresh_from_db()
        assert t.is_active is False

        t.slug = "rebound"
        with pytest.raises(ValueError, match="immutable"):
            t.save()


# ---------------------------------------------------------------------------
# Tenancy retro B2 — write-path enforcement
# ---------------------------------------------------------------------------


class TestRetroB2WriteEnforcement:
    """Pre-fix TenantScopedManager.get_queryset() filtered reads, but
    write methods (bulk_create, get_or_create, update_or_create,
    create) bypassed scoping entirely. A caller passing tenant=other
    to any of these wrote cross-tenant rows with no check.

    Post-fix: tenant kwarg ABSENT → stamp current_tenant(); PRESENT
    and matches → pass through; PRESENT and mismatches → raise
    CrossTenantError (even in audit mode, since writes are
    irreversible state).
    """

    def test_create_stamps_tenant_when_omitted(self, settings):
        # A model_a-style write WITHOUT tenant kwarg gets the tenant
        # stamped from current_tenant(). Use a real model since the
        # default manager is the production manager.
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t = Tenant.objects.create(slug="b2-stamp", name="Stamp")
        with tenant_scope(t):
            row = AuditLog.objects.create(action="test.write_stamps", target="x")
        assert row.tenant_id == t.id

    def test_create_matching_tenant_passes(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t = Tenant.objects.create(slug="b2-match", name="Match")
        with tenant_scope(t):
            row = AuditLog.objects.create(action="test.matching", target="x", tenant=t)
        assert row.tenant_id == t.id

    def test_create_mismatched_tenant_raises_in_audit_mode(self, settings):
        # Audit mode raises on writes — silent cross-tenant writes are
        # irreversible and not acceptable degradation, unlike reads.
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="b2-mm-a", name="A")
        t2 = Tenant.objects.create(slug="b2-mm-b", name="B")
        with tenant_scope(t1), pytest.raises(CrossTenantError):
            AuditLog.objects.create(action="test.cross_tenant", target="x", tenant=t2)

    def test_create_mismatched_tenant_raises_in_strict_mode(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "strict"
        t1 = Tenant.objects.create(slug="b2-mm-s-a", name="A")
        t2 = Tenant.objects.create(slug="b2-mm-s-b", name="B")
        with tenant_scope(t1), pytest.raises(CrossTenantError):
            AuditLog.objects.create(action="test.cross_tenant_strict", target="x", tenant=t2)

    def test_create_without_context_raises_in_strict(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "strict"
        # No tenant_scope entered → strict mode refuses the write.
        with pytest.raises(CrossTenantError, match="without a tenant context"):
            AuditLog.objects.create(action="test.no_scope", target="x")

    def test_get_or_create_stamps_tenant(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t = Tenant.objects.create(slug="b2-goc", name="GOC")
        with tenant_scope(t):
            row, created = AuditLog.objects.get_or_create(
                action="b2.get_or_create",
                target="anchor",
                defaults={"payload": {"x": 1}},
            )
        assert created
        assert row.tenant_id == t.id

    def test_get_or_create_mismatched_raises(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="b2-goc-a", name="A")
        t2 = Tenant.objects.create(slug="b2-goc-b", name="B")
        with tenant_scope(t1), pytest.raises(CrossTenantError):
            AuditLog.objects.get_or_create(
                action="b2.goc_cross",
                tenant=t2,
            )

    def test_bulk_create_stamps_tenant_on_each_instance(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t = Tenant.objects.create(slug="b2-bulk", name="Bulk")
        with tenant_scope(t):
            instances = [AuditLog(action=f"b2.bulk.{i}", target="x") for i in range(3)]
            AuditLog.objects.bulk_create(instances)
        rows = AuditLog.all_tenants.filter(action__startswith="b2.bulk.")
        assert rows.count() == 3
        for row in rows:
            assert row.tenant_id == t.id

    def test_bulk_create_mismatched_instance_raises(self, settings):
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="b2-bulk-a", name="A")
        t2 = Tenant.objects.create(slug="b2-bulk-b", name="B")
        with tenant_scope(t1):
            instances = [
                AuditLog(action="b2.bulk_cross.0", target="x"),
                AuditLog(action="b2.bulk_cross.1", target="x", tenant=t2),
            ]
            with pytest.raises(CrossTenantError):
                AuditLog.objects.bulk_create(instances)

    def test_update_or_create_mismatched_defaults_raises(self, settings):
        # Reviewer B-2: pre-fix had no test for update_or_create mismatch
        # via the ``defaults`` dict — the assertion that update_or_create
        # rejects cross-tenant writes was unverified. This test pins it.
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="b2-uoc-a", name="A")
        t2 = Tenant.objects.create(slug="b2-uoc-b", name="B")
        with tenant_scope(t1), pytest.raises(CrossTenantError):
            AuditLog.objects.update_or_create(
                action="b2.uoc_cross",
                target="x",
                defaults={"payload": {"x": 1}, "tenant": t2},
            )

    def test_queryset_update_mismatched_tenant_raises(self, settings):
        # Reviewer B-1: pre-fix ``.objects.filter(...).update(tenant=other)``
        # could silently reassign rows cross-tenant. Post-fix the
        # TenantScopedQuerySet.update() override catches it.
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="b2-upd-a", name="A")
        t2 = Tenant.objects.create(slug="b2-upd-b", name="B")
        with tenant_scope(t1):
            AuditLog.objects.create(action="b2.update_target", target="x")
            with pytest.raises(CrossTenantError):
                AuditLog.objects.filter(action="b2.update_target").update(tenant=t2)

    def test_queryset_update_intra_tenant_passes(self, settings):
        # Negative regression: an intra-tenant update (no tenant kwarg)
        # must continue to work — this is the legitimate use case.
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "audit"
        t = Tenant.objects.create(slug="b2-upd-intra", name="Intra")
        with tenant_scope(t):
            AuditLog.objects.create(action="b2.intra_update", target="before")
            updated = AuditLog.objects.filter(action="b2.intra_update").update(target="after")
        assert updated == 1
        row = AuditLog.all_tenants.get(action="b2.intra_update")
        assert row.target == "after"

    def test_bulk_create_without_scope_in_strict_raises(self, settings):
        # Reviewer Y-2: closes the previous silent-skip when bulk_create
        # ran without a tenant_scope under strict mode.
        from apps.audit.models import AuditLog

        settings.STRICT_TENANT_SCOPE = "strict"
        with pytest.raises(CrossTenantError, match="without a tenant context"):
            AuditLog.objects.bulk_create([AuditLog(action="b2.bulk_no_scope", target="x")])


# ---------------------------------------------------------------------------
# Abstract-base check — system check for tenant FK + manager pair
# ---------------------------------------------------------------------------


class TestSystemCheckTenantManagers:
    """The Django check walks installed models with a tenant FK and
    warns when the canonical manager pair is missing.
    """

    def test_check_returns_warnings_for_known_missing_models(self):
        from apps.tenancy.system_checks import check_tenant_scoped_managers

        messages = check_tenant_scoped_managers()
        # Updated after PR #466 closed all 5 known unscoped models with
        # documented opt-outs. Now the dev surface SHOULD be silent —
        # any warning here means either:
        #   (a) a new tenant-FK-bearing model landed without the
        #       canonical manager pair, OR
        #   (b) an existing opt-out was accidentally removed.
        # Either way the operator must triage. The ID-restriction
        # (closes reviewer Y-5 from PR #454) catches future check
        # codes (W902+) that need explicit test coverage.
        ids = {m.id for m in messages}
        assert ids <= {"tenancy.W900", "tenancy.W901"}, (
            f"unexpected check ids: {ids - {'tenancy.W900', 'tenancy.W901'}} — "
            "either a new W-code landed (add to the test) or the assertion "
            "needs widening"
        )

    def test_check_fires_on_synthetic_violator(self):
        # The check itself MUST still fire for a model that lacks the
        # canonical manager pair AND the opt-out sentinel. Build a
        # synthetic class without going through the migration pipeline
        # and ensure the walker flags it. Catches the failure shape
        # «check function is silently broken because ``apps.get_models``
        # returns an unexpected iterable» — pre-fix that was a vacuous
        # pass; post-fix the synthetic violator forces the walker to
        # exercise the failure path.
        from unittest.mock import MagicMock

        from django.db.models import Manager

        from apps.tenancy.system_checks import check_tenant_scoped_managers

        # A model that LOOKS like it has a ``tenant`` FK pointing at
        # tenancy.Tenant but uses a plain Manager.
        fake_field = MagicMock()
        fake_field.name = "tenant"
        fake_field.related_model = Tenant
        fake_model = MagicMock()
        fake_model.__name__ = "FakeUnscopedModel"
        fake_model._meta.abstract = False
        fake_model._meta.proxy = False
        fake_model._meta.label = "synthetic.FakeUnscopedModel"
        fake_model._meta.get_fields.return_value = [fake_field]
        fake_model._default_manager = Manager()  # NOT a TenantScopedManager
        # No ``all_tenants`` attribute either.
        if hasattr(fake_model, "all_tenants"):
            del fake_model.all_tenants
        # No opt-out sentinel.
        if hasattr(fake_model, "_IGNORE_TENANT_MANAGER_CHECK"):
            del fake_model._IGNORE_TENANT_MANAGER_CHECK

        with patch(
            "apps.tenancy.system_checks.django_apps.get_models",
            return_value=[fake_model],
        ):
            messages = check_tenant_scoped_managers()
        ids = {m.id for m in messages}
        # Both W900 (manager) and W901 (escape hatch) should fire.
        assert "tenancy.W900" in ids
        assert "tenancy.W901" in ids

    def test_well_scoped_model_does_not_trip_check(self):
        # AuditLog has both objects = TenantScopedManager() AND
        # all_tenants = models.Manager() — the canonical shape. The
        # check must NOT flag it.
        from apps.audit.models import AuditLog
        from apps.tenancy.system_checks import check_tenant_scoped_managers

        messages = check_tenant_scoped_managers()
        offenders = [m for m in messages if getattr(m, "obj", None) is AuditLog]
        assert offenders == []
