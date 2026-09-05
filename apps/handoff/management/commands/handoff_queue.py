"""``manage.py handoff_queue`` — the open handoff queue, without opening a DB shell.

DRF-1488, third leg. «How many people are currently waiting for a human,
and for how long?» was a question that required a psql session and a
by-hand join against `AdminTask`. A question that expensive does not get
asked, which is how ten tasks went unassigned for a month and one of them
kept a client's bot muted for 20 hours.

Read-only. Cross-tenant, like the admin and for the same reason: one
internal team works the queue across every salon. It prints no client
message text and no phone — the transcript stays in the admin behind a
login, exactly as ``notify.py`` keeps it out of a messenger.

    $ manage.py handoff_queue
    $ manage.py handoff_queue --all      # include closed tasks from today
    $ manage.py handoff_queue --json     # for a monitor, not a person
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.handoff.escalation import pickup_sla
from apps.handoff.models import AdminTask


class Command(BaseCommand):
    help = "Show open handoff tasks with their age, addressee and overdue state."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--all",
            action="store_true",
            help="Also list tasks closed in the last 24 hours.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit machine-readable JSON instead of the table.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        sla_minutes = int(pickup_sla().total_seconds() // 60)
        open_states = (AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS)
        qs = AdminTask.all_tenants.select_related("tenant", "assigned_to").order_by("created_at")
        if options["all"]:
            qs = qs.filter(Q(status__in=open_states) | Q(created_at__gte=now - timedelta(days=1)))
        else:
            qs = qs.filter(status__in=open_states)

        rows = [self._row(task, now=now, sla_minutes=sla_minutes) for task in qs]

        if options["as_json"]:
            self.stdout.write(json.dumps({"sla_minutes": sla_minutes, "tasks": rows}, indent=2))
            return

        if not rows:
            self.stdout.write("Открытых задач нет.")
            return

        self.stdout.write(f"SLA на взятие: {sla_minutes} мин. Задач: {len(rows)}")
        self.stdout.write(
            f"{'ID':<10}{'СТАТУС':<14}{'ЖДЁТ':>8}  {'АДРЕСАТ':<22}{'ПРОСРОЧКА':<11}САЛОН"
        )
        for row in rows:
            self.stdout.write(
                f"{row['id'][:8]:<10}"
                f"{row['status']:<14}"
                f"{row['waiting_minutes']:>6} м  "
                f"{row['addressee'][:20]:<22}"
                f"{('ДА' if row['overdue'] else '—'):<11}"
                f"{row['tenant']}"
            )

    def _row(self, task: AdminTask, *, now: Any, sla_minutes: int) -> dict[str, Any]:
        end = task.resolved_at or now
        waiting = max(0, int((end - task.created_at).total_seconds() // 60))
        # «Overdue» is the stamp when the sweep has already run, and the raw
        # comparison when it has not yet — so a queue inspected between two
        # ticks tells the truth rather than the sweep's last opinion.
        overdue = task.pickup_escalated_at is not None or (
            task.status == AdminTask.Status.OPEN
            and task.claimed_at is None
            and sla_minutes > 0
            and waiting >= sla_minutes
        )
        return {
            "id": str(task.id),
            "tenant": task.tenant.slug,
            "status": task.status,
            "task_type": task.task_type,
            "priority": task.priority,
            "addressee": task.addressee or "НЕ НАЗНАЧЕН",
            "waiting_minutes": waiting,
            "overdue": overdue,
            "claimed": task.claimed_at is not None,
            "created_at": task.created_at.isoformat(),
        }
