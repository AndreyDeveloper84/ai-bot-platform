"""Tests for the ``relabel_legacy_orders_targets`` management command — #730.

Pins the rewrite contract for AuditLog rows referencing the deleted
``apps.orders.tasks`` / ``apps.integrations.yookassa.webhooks``
modules (PR #739 / #427+#428). The command runs pre-T4 deploy per
``docs/runbooks/orders-yookassa-retirement-deploy.md``.

### Coverage goals

* Each rename in ``LEGACY_TARGET_RELABELS`` actually rewrites matching rows.
* Idempotent — second run is a no-op.
* ``--dry-run`` counts but does not modify.
* Cross-tenant — rows under different tenants AND system rows
  (``tenant=None``) all rewritten in one pass.
* Untouched targets remain — ``BookingSkill`` rows (live module) MUST NOT
  be relabeled.
* ``target_id`` UUID + ``payload`` JSON survive the rewrite unchanged
  (we only touch ``target`` string).
"""

from __future__ import annotations

import uuid
from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.management.commands.relabel_legacy_orders_targets import (
    LEGACY_TARGET_RELABELS,
)
from apps.audit.models import AuditLog
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants() -> tuple[Tenant, Tenant]:
    a = Tenant.objects.create(slug="t-a-relabel", name="Tenant A")
    b = Tenant.objects.create(slug="t-b-relabel", name="Tenant B")
    return a, b


def _make_row(
    *,
    target: str,
    action: str = "test.action",
    tenant: Tenant | None = None,
    target_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> AuditLog:
    return AuditLog.all_tenants.create(
        tenant=tenant,
        action=action,
        target=target,
        target_id=target_id,
        payload=payload or {},
    )


class TestRenameTable:
    """Each entry in ``LEGACY_TARGET_RELABELS`` actually rewrites rows."""

    @pytest.mark.parametrize(("original", "replacement"), LEGACY_TARGET_RELABELS)
    def test_each_legacy_target_relabeled(
        self, tenants: tuple[Tenant, Tenant], original: str, replacement: str
    ) -> None:
        tenant_a, _ = tenants
        _make_row(tenant=tenant_a, target=original, action="legacy.action")

        out = StringIO()
        call_command("relabel_legacy_orders_targets", stdout=out)

        # Original target gone, replacement present.
        assert AuditLog.all_tenants.filter(target=original).count() == 0
        assert AuditLog.all_tenants.filter(target=replacement).count() == 1
        # stdout reports the rewrite.
        assert original in out.getvalue()
        assert replacement in out.getvalue()


class TestIdempotency:
    def test_second_run_is_noop(self, tenants: tuple[Tenant, Tenant]) -> None:
        tenant_a, _ = tenants
        _make_row(tenant=tenant_a, target="orders.tasks")

        # First run — rewrites.
        call_command("relabel_legacy_orders_targets", stdout=StringIO())
        first_run_id_set = set(
            AuditLog.all_tenants.filter(target="legacy_orders.tasks_archived").values_list(
                "id", flat=True
            )
        )

        # Second run — touches nothing.
        out = StringIO()
        call_command("relabel_legacy_orders_targets", stdout=out)
        second_run_id_set = set(
            AuditLog.all_tenants.filter(target="legacy_orders.tasks_archived").values_list(
                "id", flat=True
            )
        )

        assert first_run_id_set == second_run_id_set
        # stdout confirms «no rows».
        assert "no rows with target='orders.tasks'" in out.getvalue()


class TestDryRun:
    def test_dry_run_does_not_modify(self, tenants: tuple[Tenant, Tenant]) -> None:
        tenant_a, _ = tenants
        _make_row(tenant=tenant_a, target="orders.tasks")
        _make_row(tenant=tenant_a, target="yookassa.webhook")

        out = StringIO()
        call_command("relabel_legacy_orders_targets", "--dry-run", stdout=out)

        # Originals still there, no replacement.
        assert AuditLog.all_tenants.filter(target="orders.tasks").count() == 1
        assert AuditLog.all_tenants.filter(target="yookassa.webhook").count() == 1
        assert AuditLog.all_tenants.filter(target="legacy_orders.tasks_archived").count() == 0
        # Round-1 friendly S1 — singular grammar for n=1.
        assert "would relabel 1 row:" in out.getvalue()
        assert "would relabel 1 rows" not in out.getvalue()


class TestCrossTenant:
    def test_relabels_across_tenants_and_system_rows(self, tenants: tuple[Tenant, Tenant]) -> None:
        """Cross-tenant sweep: rows under tenant A, tenant B, and
        system rows (``tenant=None``) all rewritten in one pass."""
        tenant_a, tenant_b = tenants

        _make_row(tenant=tenant_a, target="orders.tasks", action="a.action")
        _make_row(tenant=tenant_b, target="orders.tasks", action="b.action")
        _make_row(tenant=None, target="orders.tasks", action="system.action")

        call_command("relabel_legacy_orders_targets", stdout=StringIO())

        # All three relabeled — across both tenants AND system rows.
        relabeled = AuditLog.all_tenants.filter(target="legacy_orders.tasks_archived")
        assert relabeled.count() == 3
        tenant_ids = {row.tenant_id for row in relabeled}
        assert tenant_ids == {tenant_a.id, tenant_b.id, None}


class TestArchivedRows:
    """Round-1 friendly N3 — `is_archived=True` rows are still in the
    90-day retention window and their target label still matters
    forensically. The cross-tenant `all_tenants` manager (raw
    `models.Manager()`) includes archived rows; pin this so a future
    refactor to `objects` (which would skip them) gets caught."""

    def test_archived_rows_also_relabeled(self, tenants: tuple[Tenant, Tenant]) -> None:
        tenant_a, _ = tenants
        row = _make_row(tenant=tenant_a, target="orders.tasks", action="cert.fulfilled")
        # Flip to archived via raw manager so the manager-default filter
        # doesn't mask it.
        AuditLog.all_tenants.filter(pk=row.pk).update(is_archived=True)

        call_command("relabel_legacy_orders_targets", stdout=StringIO())

        row.refresh_from_db()
        assert row.target == "legacy_orders.tasks_archived"
        # Still archived — the relabel does not flip is_archived.
        assert row.is_archived is True


class TestUntouchedTargets:
    def test_booking_skill_rows_untouched(self, tenants: tuple[Tenant, Tenant]) -> None:
        """``target="BookingSkill"`` rows point to a LIVE module
        (apps.skills.booking.tools). They MUST NOT be relabeled —
        forensic narrative is still accurate."""
        tenant_a, _ = tenants
        _make_row(tenant=tenant_a, target="BookingSkill", action="booking.something")
        _make_row(tenant=tenant_a, target="orders.tasks", action="cert.fulfilled")

        call_command("relabel_legacy_orders_targets", stdout=StringIO())

        # BookingSkill stays. orders.tasks rewritten.
        assert AuditLog.all_tenants.filter(target="BookingSkill").count() == 1
        assert AuditLog.all_tenants.filter(target="legacy_orders.tasks_archived").count() == 1

    def test_unknown_targets_untouched(self, tenants: tuple[Tenant, Tenant]) -> None:
        """Anything not in ``LEGACY_TARGET_RELABELS`` is left alone."""
        tenant_a, _ = tenants
        for unknown_target in (
            "tenancy.tenant",
            "Conversation",
            "eventbus.ingest",
            "",  # default empty target
        ):
            _make_row(tenant=tenant_a, target=unknown_target, action="x.action")

        call_command("relabel_legacy_orders_targets", stdout=StringIO())

        for unknown_target in (
            "tenancy.tenant",
            "Conversation",
            "eventbus.ingest",
            "",
        ):
            assert AuditLog.all_tenants.filter(target=unknown_target).count() == 1, (
                f"target={unknown_target!r} unexpectedly relabeled"
            )


class TestPreservesOtherFields:
    def test_target_id_and_payload_survive_rewrite(self, tenants: tuple[Tenant, Tenant]) -> None:
        """The rewrite only touches the ``target`` string. ``target_id``
        (forensic UUID; orphan but preserved per Round-1 F-1 acceptance)
        + ``payload`` JSON must survive unchanged."""
        tenant_a, _ = tenants
        order_uuid = uuid.uuid4()
        payload = {
            "order_id": str(order_uuid),
            "tenant_id": str(tenant_a.id),
            "amount_rub": "1500.00",
        }
        row = _make_row(
            tenant=tenant_a,
            target="orders.tasks",
            action="cert.fulfilled",
            target_id=order_uuid,
            payload=payload,
        )

        call_command("relabel_legacy_orders_targets", stdout=StringIO())

        row.refresh_from_db()
        assert row.target == "legacy_orders.tasks_archived"
        assert row.target_id == order_uuid  # orphan UUID preserved
        assert row.payload == payload  # JSON untouched
        assert row.action == "cert.fulfilled"  # action untouched


class TestEmptyDatabase:
    def test_no_rows_to_relabel_is_successful_noop(self) -> None:
        """A fresh deploy / pre-pilot replica with no historical rows
        completes successfully reporting zero work — the command is
        safe to run as a deploy step even when there's nothing to do."""
        out = StringIO()
        call_command("relabel_legacy_orders_targets", stdout=out)

        output = out.getvalue()
        assert "no rows with target='orders.tasks'" in output
        assert "no rows with target='yookassa.webhook'" in output
        assert "#730 complete: relabeled 0 rows total" in output
