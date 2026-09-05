"""Boot-time guard: a deployment cannot be configured to file ownerless tasks.

DRF-1488. The pilot's ten unassigned tasks were not a bug in a branch —
there was no branch. Nothing in the system ever asked «who is this for?»,
so nothing could answer wrong.

Making :func:`apps.handoff.services.create_admin_task` raise on a missing
addressee would answer it at the worst possible moment: mid-escalation,
for the one client who just asked for a human. So the question is asked
at boot instead, where the only cost of a wrong answer is a deployment
that refuses to start.

``HANDOFF_DUTY_QUEUE`` ships non-empty precisely so this check passes out
of the box; a deployment has to go out of its way — blank BOTH the roster
and the queue — to trip it.
"""

from __future__ import annotations

from typing import Any

from django.core.checks import Error, register


@register()
def check_handoff_addressee_configured(app_configs: Any, **kwargs: Any) -> list[Error]:
    """handoff.E001 — no roster and no duty queue means no addressee."""

    from apps.handoff.assignment import duty_operator_usernames, duty_queue_name

    if duty_operator_usernames() or duty_queue_name():
        return []
    return [
        Error(
            "Handoff tasks would be filed with no addressee.",
            hint=(
                "Set HANDOFF_DUTY_OPERATORS to the usernames on duty, or "
                "HANDOFF_DUTY_QUEUE to the name of the duty queue that picks "
                "tasks up. Both empty reproduces DRF-1488: a task nobody owns, "
                "which nobody is late on, while the client's bot stays muted."
            ),
            id="handoff.E001",
        )
    ]
