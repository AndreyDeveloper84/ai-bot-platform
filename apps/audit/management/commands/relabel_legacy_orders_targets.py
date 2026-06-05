"""Relabel legacy AuditLog target values for retired orders+yookassa (#730).

Pre-T4 forensic cleanup for #739 deploy. The packages
``apps.orders.tasks`` and ``apps.integrations.yookassa.webhooks`` were
deleted in PR #739 (#427+#428) — but ``AuditLog`` rows referencing
those modules via the ``target`` string column remain in the database
for ``AUDIT_LOG_RETENTION_DAYS`` (default 90 days, per
``config/settings/base.py:240``). Without relabel the forensic
narrative is misleading: an operator investigating an incident at
T+30 days sees ``target="orders.tasks"`` and either (a) tries to
import the module that no longer exists, or (b) wonders whether the
target was renamed vs deleted.

### Renames applied

| Original target       | Rewritten target                       | Source (now deleted)                                |
|-----------------------|----------------------------------------|-----------------------------------------------------|
| ``orders.tasks``      | ``legacy_orders.tasks_archived``       | ``apps/orders/tasks.py``                            |
| ``yookassa.webhook``  | ``legacy_yookassa.webhook_archived``   | ``apps/integrations/yookassa/webhooks.py``          |

``target="BookingSkill"`` rows (from ``apps.skills.booking.tools``,
which still exists) are intentionally untouched — they point to a
live module and remain forensically valid.

The ``orders.tasks`` rows carry a ``target_id=order.id`` UUID that
points at a dropped ``orders_order`` row. The UUID survives the
DROP (``AuditLog.target_id`` is a plain ``UUIDField``, NOT a FK)
but its forensic value is gone. That's accepted scope per Round-1
adversarial F-1 — we don't try to «fix» the orphan UUID, only the
narrative ``target`` label.

### Idempotency

Re-runs are no-op. The command queries by the ORIGINAL ``target``
value; once relabeled, rows no longer match and stay as-is. Safe
to run multiple times during T4 deploy choreography or recovery.

### Cross-tenant

Uses ``AuditLog.all_tenants`` so the rewrite applies to every
tenant's rows in one pass. Ops typically runs this with no
``tenant_scope`` active.

### When to run

Per ``docs/runbooks/orders-yookassa-retirement-deploy.md`` — BEFORE
``manage.py migrate orders 0002_drop_orders_tables``, AFTER the
prod DB snapshot. Order matters operationally:

1. Snapshot captures the ORIGINAL state (forensic baseline).
2. Relabel transforms forward.
3. Migrate then drops the tables.

If the relabel is run AFTER the migrate, that's also fine — the
rewrite is independent of whether the FK target rows still exist
(``target`` is a free-form string).

### Usage

::

    sudo -u ai-bot-platform uv run python manage.py \\
        relabel_legacy_orders_targets

    sudo -u ai-bot-platform uv run python manage.py \\
        relabel_legacy_orders_targets --dry-run

### Exit semantics

The command never raises on «no rows to relabel» — that's a
successful idempotent run. It only logs counts to stdout.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from django.core.management.base import BaseCommand

from apps.audit.models import AuditLog


logger = logging.getLogger(__name__)


# Closed mapping. The parametrized test in
# apps/audit/tests/test_relabel_legacy_orders_targets.py iterates
# this tuple — new entries are automatically covered there.
LEGACY_TARGET_RELABELS: Final[tuple[tuple[str, str], ...]] = (
    ("orders.tasks", "legacy_orders.tasks_archived"),
    ("yookassa.webhook", "legacy_yookassa.webhook_archived"),
)


def _row_word(n: int) -> str:
    """Singular/plural picker for operator-visible counts.

    Round-1 friendly review (#790) — «1 rows» reads sloppy in the
    deploy-evidence stdout; clean grammar avoids ambiguity in
    operator copy-paste.
    """
    return "row" if n == 1 else "rows"


class Command(BaseCommand):
    help = (
        "Relabel AuditLog.target values for retired apps/orders + "
        "apps/integrations/yookassa modules (#730). Idempotent + "
        "cross-tenant. See module docstring for the rename table."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Count rows that would be relabeled without modifying. "
                "Useful for pre-deploy verification."
            ),
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        dry_run = bool(opts.get("dry_run", False))

        total_relabeled = 0
        for original, replacement in LEGACY_TARGET_RELABELS:
            # ``all_tenants`` so the sweep covers every tenant's rows
            # in one pass. AuditLog rows with ``tenant=None`` (system
            # events) AND ``is_archived=True`` (soft-deleted but still
            # in the 90-day retention window) are also included via
            # this manager — both are forensically relevant.
            qs = AuditLog.all_tenants.filter(target=original)
            # Race window between count() and update() is structurally
            # closed by the deploy choreography: the producer modules
            # (apps.orders.tasks, apps.integrations.yookassa.webhooks)
            # are DELETED in #739, and T2 mandates a 24h zero-traffic
            # soak before T4 — no new rows with these targets can be
            # created.
            count = qs.count()

            if count == 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  no rows with target={original!r} (already relabeled or never existed)"
                    )
                )
                # Forensic trail: log even the no-op so a SIEM has
                # evidence the operator ran the command + saw zero
                # matches. Round-1 friendly S2.
                logger.info(
                    "audit.relabel_legacy_orders_targets dry_run=%s count=0 from=%r",
                    dry_run,
                    original,
                )
                continue

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] would relabel {count} {_row_word(count)}: "
                    f"target={original!r} → {replacement!r}"
                )
            else:
                # Bulk UPDATE — atomic per-row, and a single SQL
                # statement on PostgreSQL is atomic against power
                # loss / network drop mid-execution (commits all or
                # nothing). The sweep across the 2 legacy targets is
                # NOT wrapped in a transaction — partial failure
                # (target #1 commits, target #2 errors) leaves the
                # run idempotent: re-running picks up #2.
                qs.update(target=replacement)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  relabeled {count} {_row_word(count)}: "
                        f"target={original!r} → {replacement!r}"
                    )
                )

            # Log on BOTH paths (dry-run + real) so SIEM has a
            # forensic record of every invocation, including the
            # pre-flight check the runbook prescribes.
            logger.info(
                "audit.relabel_legacy_orders_targets dry_run=%s count=%d from=%r to=%r",
                dry_run,
                count,
                original,
                replacement,
            )
            total_relabeled += count

        action = "would relabel" if dry_run else "relabeled"
        self.stdout.write(
            self.style.SUCCESS(
                f"#730 complete: {action} {total_relabeled} {_row_word(total_relabeled)} total."
            )
        )
