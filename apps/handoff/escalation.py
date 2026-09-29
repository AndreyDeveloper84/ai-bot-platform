"""The wait on a handoff task has a limit (DRF-1488).

# The measurement

Ten tasks over the pilot. Time from creation to close: 0 seconds on one
(`35a76650`, created and closed in the same second) and 20 hours on
another (`6a5a881e`). A spread like that is not a process running badly;
it is what a queue looks like when nothing measures it. Nobody was ever
told a task had been waiting, because nobody was ever told a task
existed past the one notification at creation (DRF-1029).

Meanwhile the client's bot is muted for the whole wait (DRF-1015 releases
on close, and only on close).

# What this does, and what it deliberately does not

Every 5 minutes the sweep looks for OPEN tasks that nobody has claimed
within ``HANDOFF_PICKUP_SLA_MINUTES`` and escalates each **once**:
re-notifies the operator chats and writes an audit row. ``exactly once``
is not a hope — it is the conditional UPDATE on ``pickup_escalated_at``,
so a re-run, an overlapping tick or a retried Celery message all no-op.

What happens *beyond* nudging the operator is an owner decision and is
NOT taken here. «Return the bot to the dialog» and «tell the client they
are still waiting» are different products with different failure modes —
one risks the bot talking over an operator who is about to reply, the
other risks promising twice. The mechanism is built so either can be
attached later; the default does the one thing that is unambiguously
right, which is to make the wait visible to the people who can end it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.audit.services import write_audit
from apps.handoff.models import AdminTask
from apps.handoff.notify import notify_admin_task_unclaimed

logger = logging.getLogger(__name__)


def pickup_sla() -> timedelta:
    """How long a task may sit unclaimed. Zero or negative disables the sweep."""

    return timedelta(minutes=int(getattr(settings, "HANDOFF_PICKUP_SLA_MINUTES", 0) or 0))


def overdue_tasks(*, now=None):
    """OPEN, unclaimed, not-yet-escalated tasks past the SLA.

    Cross-tenant (``all_tenants``) on purpose: the queue is worked by one
    internal team across every salon, which is the same reason the admin
    is cross-tenant. IN_PROGRESS is excluded — somebody is on it, and the
    thing being measured here is pickup, not resolution.
    """

    sla = pickup_sla()
    if sla <= timedelta(0):
        return AdminTask.all_tenants.none()
    cutoff = (now or timezone.now()) - sla
    return AdminTask.all_tenants.filter(
        status=AdminTask.Status.OPEN,
        claimed_at__isnull=True,
        pickup_escalated_at__isnull=True,
        created_at__lte=cutoff,
    ).select_related("tenant", "assigned_to")


def sweep_unclaimed_tasks(*, now=None) -> int:
    """Escalate every overdue task once. Returns how many were escalated.

    At-most-once, stated plainly: the stamp lands BEFORE the notification,
    so a process killed between the two leaves a task marked escalated
    whose nudge never went out. That ordering is deliberate. The opposite
    one is at-least-once, and a repeated nudge on every sweep tick for a
    task the operator has already seen is exactly the kind of noise that
    teaches people to mute the operator chat — after which nothing is
    escalated at all. A lost nudge still leaves the task visible in
    ``manage.py handoff_queue`` and in the admin's «Просрочка» column.
    """

    moment = now or timezone.now()
    escalated = 0
    for task in list(overdue_tasks(now=moment)):
        # The stamp IS the lock: whoever's UPDATE matches the still-NULL
        # row owns the escalation, and every other runner gets 0 rows.
        claimed = AdminTask.all_tenants.filter(pk=task.pk, pickup_escalated_at__isnull=True).update(
            pickup_escalated_at=moment
        )
        if not claimed:
            continue
        task.pickup_escalated_at = moment
        waited_minutes = int((moment - task.created_at).total_seconds() // 60)
        escalated += 1
        logger.warning(
            "handoff.pickup_overdue task=%s tenant=%s addressee=%s waited_minutes=%d",
            task.id,
            task.tenant_id,
            task.addressee or "NOBODY",
            waited_minutes,
        )
        _audit_overdue(task, waited_minutes)
        notify_admin_task_unclaimed(task, waited_minutes=waited_minutes)
    if escalated:
        logger.info("handoff.pickup_sweep.done escalated=%d", escalated)
    return escalated


def _audit_overdue(task: AdminTask, waited_minutes: int) -> None:
    """Audit the breach so it survives log rotation. Best-effort."""

    try:
        write_audit(
            "handoff.pickup_overdue",
            target="AdminTask",
            target_id=task.id,
            payload={
                "conversation_id": str(task.conversation_id),
                "addressee": task.addressee,
                "waited_minutes": waited_minutes,
                "sla_minutes": int(pickup_sla().total_seconds() // 60),
            },
        )
    except Exception:  # noqa: BLE001 — the audit row must not cost the nudge
        logger.exception("handoff.pickup_overdue.audit_failed task=%s", task.id)
