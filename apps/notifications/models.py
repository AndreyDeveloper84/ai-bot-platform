"""Master notification preferences (master-mobile §M7).

Per-master toggles + quiet-hours window powering the M7 settings
screen. Created on-demand by the GET endpoint — there is intentionally
NO data migration backfill. Defaults are baked into the column DDL so
a master who has never opened the M7 screen still gets sensible
behaviour from any future consumer-side dispatcher.

### Spec mapping (verbatim §M7 quotes)

* «Settings persisted server-side» — backed by this OneToOne row.
* «Срочно (HUMAN_LOCKED) … нельзя выключить» — DB CheckConstraint
  ``master_notif_urgent_forced_on`` blocks ``urgent=False`` writes;
  the service layer surfaces this as HTTP 400 ``urgent_forced_on``.
* «Тихие часы … с 21:00 до 09:00» — the default quiet-hours window;
  ``quiet_start > quiet_end`` is legal and represents an overnight
  window (start-time previous day → end-time current day).
* «Утренний бриф (08:30) … Switch: ON» — ``morning_brief`` defaults
  True per §823.
* «Вечерний итог (после смены) … Switch: OFF» — ``evening_summary``
  defaults False per §827.

### TZ semantics

Quiet hours are stored as naive ``time``-of-day values and interpreted
in the *tenant's* local timezone (``Tenant.timezone``). Cross-DST
correctness is deferred — for MVP a master in MSK has consistent
local-clock quiet hours; the dispatcher layer (out of scope here)
will be responsible for converting to UTC at delivery time.

### Out of scope (this PR)

* Per-customer overrides.
* Channel split (DM vs email vs SMS) — single boolean per category.
* MAX bot subscription DB write (§835) — deferred; out-of-band push
  beyond chat is anyway unsupported by MAX today.
* Consumer-side gating — dispatchers that READ these prefs ship in a
  follow-up PR.
"""

from __future__ import annotations

import uuid
from datetime import time

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class MasterNotificationPrefs(models.Model):
    """Per-master notification toggles + quiet-hours window.

    OneToOne to :class:`apps.catalog.CatalogMaster`. Row is created
    on-demand by the GET endpoint (lazy materialisation) so newly-
    onboarded masters don't need a backfill migration.

    The ``urgent`` field carries a CHECK constraint forcing it ON —
    spec §805 «нельзя выключить». PATCH-layer validation raises 400
    ``urgent_forced_on`` before the DB sees the offending row; the
    constraint is belt-and-braces against future writers (admin
    shell, data migrations, etc.).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="master_notification_prefs",
        help_text="Owning tenant. CASCADE — prefs are derived state.",
    )
    master = models.OneToOneField(
        "catalog.CatalogMaster",
        on_delete=models.CASCADE,
        related_name="notification_prefs",
        help_text="The master this row belongs to. OneToOne by design — "
        "M7 is a single screen of per-master toggles, not per-customer.",
    )

    # --- «В РАБОЧЕЕ ВРЕМЯ» section (§M7 lines 789-805) ----------------
    new_booking = models.BooleanField(
        default=True,
        help_text="«Новая запись» — bot wrote a customer into the master's diary.",
    )
    booking_change = models.BooleanField(
        default=True,
        help_text="«Изменение записи» — customer rescheduled or cancelled.",
    )
    personal_message = models.BooleanField(
        default=True,
        help_text="«Личное сообщение клиента» — customer sent free-form text.",
    )
    urgent = models.BooleanField(
        default=True,
        help_text=(
            "«Срочно (HUMAN_LOCKED)» — safety-critical handoff. "
            "Forced ON per spec §805; the CheckConstraint below "
            "and service-layer validation both block False writes."
        ),
    )

    # --- «ТИХИЙ РЕЖИМ» section (§M7 lines 807-817) --------------------
    quiet_hours_enabled = models.BooleanField(
        default=False,
        help_text="Master switch for the quiet-hours window below.",
    )
    quiet_start = models.TimeField(
        default=time(21, 0),
        help_text="«с 21:00» — quiet window START in tenant-local TZ.",
    )
    quiet_end = models.TimeField(
        default=time(9, 0),
        help_text=(
            "«до 09:00» — quiet window END in tenant-local TZ. "
            "``quiet_start > quiet_end`` is legal: overnight window."
        ),
    )

    # --- «ЕЖЕДНЕВНЫЕ СВОДКИ» section (§M7 lines 819-827) --------------
    morning_brief = models.BooleanField(
        default=True,
        help_text="«Утренний бриф (08:30)» — daily schedule preview. ON per §823.",
    )
    evening_summary = models.BooleanField(
        default=False,
        help_text="«Вечерний итог» — end-of-shift recap. OFF per §827.",
    )

    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Master notification preferences"
        verbose_name_plural = "Master notification preferences"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(urgent=True),
                name="master_notif_urgent_forced_on",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "master"]),
        ]

    def __str__(self) -> str:
        return f"MasterNotificationPrefs[master={self.master_id}]"
