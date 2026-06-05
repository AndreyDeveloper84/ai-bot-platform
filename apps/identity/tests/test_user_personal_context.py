"""UserPersonalContext model contract tests (#229 / Sprint 1 Track A).

Per spec `docs/specs/memory-entry-schema.md` §1 + ADR-0011 §3.

Note: UserPersonalContext is CROSS-TENANT — no `tenant` FK, no
`TenantScopedManager`. Tests do NOT use `tenant_scope` context manager.
"""

from __future__ import annotations

import uuid

import pytest

from apps.identity.models import UserPersonalContext

pytestmark = pytest.mark.django_db


class TestUserPersonalContextBasics:
    def test_user_id_is_pk_and_required(self) -> None:
        """UPC uses canonical Ayla User UUID as PK — no separate id field."""
        user_id = uuid.uuid4()
        upc = UserPersonalContext.objects.create(user_id=user_id)
        assert upc.pk == user_id
        assert upc.user_id == user_id
        # PK == user_id ⇒ one row per user, forever.

    def test_default_minor_lock_false(self) -> None:
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        assert upc.minor_lock is False

    def test_default_soft_deleted_at_null(self) -> None:
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        assert upc.soft_deleted_at is None
        assert upc.forget_all_requested_at is None

    def test_timestamps_autoset(self) -> None:
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        assert upc.created_at is not None
        assert upc.updated_at is not None

    def test_str_includes_user_id(self) -> None:
        user_id = uuid.uuid4()
        upc = UserPersonalContext.objects.create(user_id=user_id)
        assert str(user_id) in str(upc)


class TestUserPersonalContextCrossTenancy:
    """UPC is cross-tenant by design — NO tenant scoping at the storage layer.

    Per ADR-0011 §9 + spec §1: UPC follows the user across all tenants.
    The reuse boundary (which yellow/red facts may surface to which provider)
    is enforced at the application layer via voice modulator + zone semantics,
    NOT at the manager / queryset layer.
    """

    def test_no_tenant_field_on_upc(self) -> None:
        """UPC explicitly has no tenant FK — cross-tenant by design."""
        field_names = {f.name for f in UserPersonalContext._meta.get_fields()}
        assert "tenant" not in field_names
        assert "tenant_id" not in field_names

    def test_default_manager_is_not_tenant_scoped(self) -> None:
        """UPC uses plain models.Manager (no TenantScopedManager)."""
        from django.db.models import Manager

        assert type(UserPersonalContext.objects) is Manager


class TestUserPersonalContextForgetAll:
    """`forget_all_requested_at` is the user-intent moment per ADR-0011 §3.3.

    The async sweep then soft-deletes all entries; tombstone retention 30d
    per spec §5. Here we test only the field semantics — sweep job is in
    a separate ticket (#597 reconciliation + forget-all sweep).
    """

    def test_forget_all_requested_at_settable(self) -> None:
        from django.utils import timezone

        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        assert upc.forget_all_requested_at is None

        upc.forget_all_requested_at = timezone.now()
        upc.save()

        upc.refresh_from_db()
        assert upc.forget_all_requested_at is not None
