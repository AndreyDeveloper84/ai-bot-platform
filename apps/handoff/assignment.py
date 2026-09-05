"""Who a handoff task is addressed to (DRF-1488).

# The defect

`AdminTask.all_tenants` over the whole pilot — 11.08 to 04.09.2026, ten
tasks — holds `assigned_to = None` ten times out of ten. Not one was ever
addressed to anybody. Time-to-close ranged from 0 seconds to 20 hours,
which is not a slow process: it is the absence of one. And because
DRF-1015 lifts the bot's mute only when the task closes, an unowned task
is a client waiting in silence for as long as nobody happens to look.

# The rule

    A task is filed when it has an addressee. Before that it is a message
    on the floor.

Two axes, checked in this order:

* **A named operator.** ``HANDOFF_DUTY_OPERATORS`` lists the usernames on
  duty. The least-loaded ACTIVE one takes the task — «least loaded» being
  the count of tasks already OPEN or IN_PROGRESS on them, ties broken by
  username so the choice is deterministic and testable. That is enough
  fairness for a pilot with one operator and does not pretend to be a
  scheduler.
* **An explicit duty queue.** With no roster configured, the task is
  addressed to ``HANDOFF_DUTY_QUEUE`` and assignment happens on pickup:
  the operator sets ``assigned_to`` in the admin, which stamps
  ``claimed_at`` (see :func:`claim`). The queue name is a label for
  humans, not a routing key — it exists so «unassigned» stops being a
  legitimate resting state.

Both empty is a configuration error caught at boot by
:mod:`apps.handoff.checks`, not a runtime branch: the alternative —
raising inside ``create_admin_task`` — would drop the escalation of the
one client who most needs it, which is a worse failure than the one being
fixed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

if TYPE_CHECKING:  # pragma: no cover — typing only
    from django.contrib.auth.models import User

    from apps.handoff.models import AdminTask

logger = logging.getLogger(__name__)


def duty_operator_usernames() -> list[str]:
    """Configured duty roster; empty list means «queue addressing»."""

    return [u for u in getattr(settings, "HANDOFF_DUTY_OPERATORS", []) if u]


def duty_queue_name() -> str:
    """Configured duty queue label; "" only in a misconfigured deployment."""

    return (getattr(settings, "HANDOFF_DUTY_QUEUE", "") or "").strip()


def resolve_addressee() -> tuple["User | None", str]:
    """Return ``(operator, queue)`` for a task being created.

    Exactly one of the two is meaningful: a named operator wins when the
    roster resolves to an active user, otherwise the queue label carries
    the address. Both empty is possible only when the deployment ignored
    the boot check — the caller logs that loudly rather than silently
    filing an ownerless task.
    """

    from apps.handoff.models import AdminTask

    usernames = duty_operator_usernames()
    if usernames:
        open_load = Count(
            "assigned_admin_tasks",
            filter=Q(
                assigned_admin_tasks__status__in=(
                    AdminTask.Status.OPEN,
                    AdminTask.Status.IN_PROGRESS,
                )
            ),
        )
        operator = (
            get_user_model()
            .objects.filter(username__in=usernames, is_active=True)
            .annotate(open_load=open_load)
            .order_by("open_load", "username")
            .first()
        )
        if operator is not None:
            return operator, ""
        # Roster configured but nobody in it exists / is active. Falling
        # back to the queue keeps the task addressed; the log says why the
        # named assignment the operator expected did not happen.
        logger.warning(
            "handoff.assignment.roster_unresolved usernames=%s reason=no_active_user",
            ",".join(usernames),
        )
    return None, duty_queue_name()


def claim(task: "AdminTask", operator: "User") -> bool:
    """Record that ``operator`` actually took ``task``. Idempotent.

    Returns True when this call is what claimed it. ``claimed_at`` is the
    clock the pickup sweep reads: stamping it is what stops the escalation
    from firing on a task somebody is already working.
    """

    from apps.handoff.models import AdminTask

    if task.claimed_at is not None:
        return False
    now = timezone.now()
    updated = AdminTask.all_tenants.filter(pk=task.pk, claimed_at__isnull=True).update(
        claimed_at=now, assigned_to=operator
    )
    if not updated:
        return False
    task.claimed_at = now
    task.assigned_to = operator
    logger.info(
        "handoff.assignment.claimed task=%s operator=%s waited_seconds=%d",
        task.id,
        getattr(operator, "username", operator.pk),
        int((now - task.created_at).total_seconds()),
    )
    return True
